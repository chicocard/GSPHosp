"""Testes de papahr.dashboard.atratividade com dados sintéticos de valores conhecidos."""
from __future__ import annotations

import math

import polars as pl
import pytest

from papahr.dashboard import atratividade as A

LAT0, LON0 = -19.92, -43.94          # hospital (BH)
KM_GRAU = 111.195                    # 1 grau de latitude


def _mun(cod, nome, dlat_km, pop):
    return {"COD_MUN": cod, "NOME": nome, "LAT": LAT0 - dlat_km / KM_GRAU, "LON": LON0, "POP": float(pop)}


@pytest.fixture
def municipios():
    return pl.DataFrame([
        _mun("310620", "Belo Horizonte", 3.0, 2_000_000),   # município do hospital: distância forçada a 0
        _mun("311860", "Contagem", 20.0, 600_000),          # faixa 10–25
        _mun("310001", "Vizinha sem AIH", 22.0, 100_000),   # faixa 10–25, zero internações (entra na população!)
        _mun("310002", "M3", 40.0, 50_000),                 # 25–50
        _mun("310003", "M4", 80.0, 40_000),                 # 50–100
        _mun("310004", "M5", 150.0, 30_000),                # 100–200
        _mun("310005", "M6", 300.0, 20_000),                # 200–400
        _mun("310006", "M7", 500.0, 10_000),                # > 400
        _mun("350001", "Fora da UF", 600.0, 10_000),        # > 400, outra UF
    ])


@pytest.fixture
def hospital():
    return {"cnes": "1234567", "nome": "Hospital Teste", "lat": LAT0, "lon": LON0, "cod_mun": "310620"}


def _linhas(cod, proc, n, cid="I219", idade=50):
    return [{"CNES": "1234567", "MUNIC_RES": cod, "PROC_REA": proc, "DIAG_PRINC": cid, "ESPEC": "01",
             "CAR_INT": "02", "IDADE": idade, "COD_IDADE": "4", "SEXO": "1", "ANOMES_CMPT": f"2024{m:02d}"}
            for m in range(1, 7) for _ in range(n // 6)]


@pytest.fixture
def aih():
    rows = []
    rows += _linhas("310620", "0408050012", 198)   # A = cirurgia ortopédica (grupo 04, subgrupo 0408)
    rows += _linhas("310620", "0303010037", 102, cid="J189", idade=0)   # B = clínico
    rows += _linhas("311860", "0408050012", 42)
    rows += _linhas("310002", "0408050012", 18)
    rows += _linhas("310003", "0408050012", 12)
    rows += _linhas("310004", "0408050012", 6)
    rows += _linhas("310005", "0408050012", 6)
    rows += _linhas("310006", "0408050012", 6)
    rows += _linhas("999999", "0408050012", 6)     # residência ignorada → "sem localização"
    # outro hospital, para a TIH municipal
    outro = [dict(r, CNES="7654321") for r in _linhas("310002", "0303010037", 30)]
    return pl.DataFrame(rows + outro)


def test_meses_e_fator(aih):
    assert A.meses_competencia(aih) == 6
    assert A.fator_anual(6) == 2.0


def test_classificacao_grupo_subgrupo_sem_nomes_sigtap(aih):
    g = A.classificar(aih, "grupo")["categoria"].unique().sort().to_list()
    assert g == ["03 Procedimentos clínicos", "04 Procedimentos cirúrgicos"]
    s = A.classificar(aih, "subgrupo")["categoria"].unique().sort().to_list()
    assert s[1].startswith("0408 ") and "osteomuscular" in s[1].lower()
    assert A.classificar(aih, "cid_capitulo")["categoria"].unique().sort().to_list()[0].startswith("Cap. IX")
    fe = A.classificar(aih, "faixa_etaria")["categoria"].unique().sort().to_list()
    assert fe == ["1 · 0 anos", "6 · 45–64 anos"]
    ids = [k for k, _ in A.niveis_disponiveis(aih)]
    assert "grupo" in ids and "faixa_etaria" in ids and "financiamento" not in ids


def test_distancias_e_faixas(hospital, municipios):
    lim = A.validar_limites([10, 25, 50, 100, 200, 400])
    d = A.distancias(hospital, municipios, lim)
    bh = d.filter(pl.col("COD_MUN") == "310620").row(0, named=True)
    assert bh["dist_km"] == 0.0 and bh["faixa"] == 0
    assert d.filter(pl.col("COD_MUN") == "311860")["faixa"][0] == 1
    assert d.filter(pl.col("COD_MUN") == "310006")["faixa"][0] == 6
    t = A.tabela_faixas(d, lim)
    assert t.height == 7 and t["rotulo"].to_list()[0] == "0–10 km" and t["rotulo"].to_list()[-1] == "> 400 km"
    f1 = t.filter(pl.col("faixa") == 1).row(0, named=True)
    assert f1["n_mun"] == 2 and f1["pop"] == 700_000          # inclui o município sem internações
    assert 20.0 < f1["d_ref"] < 22.0                           # média ponderada pela população
    assert t.filter(pl.col("faixa") == 6)["n_mun"][0] == 2     # M7 + fora da UF


def test_perfil_taxas_anualizadas_por_10mil(aih, hospital, municipios):
    p = A.perfil_completo(aih, hospital, municipios, nivel="grupo")
    todos = p.filter(pl.col("categoria") == A.TODOS).sort("faixa")
    assert todos["n"].to_list() == [300, 42, 18, 12, 6, 6, 6]
    assert todos["n_sem_localizacao"][0] == 6
    # faixa 0: 300 internações em 6 meses → 600/ano / 2.000.000 × 10.000 = 3,0
    assert todos["taxa"][0] == pytest.approx(3.0)
    # faixa 1: 42 × 2 / 700.000 × 10.000 = 1,2
    assert todos["taxa"][1] == pytest.approx(1.2)
    cir = p.filter(pl.col("categoria") == "04 Procedimentos cirúrgicos").sort("faixa")
    assert cir["n"].to_list() == [198, 42, 18, 12, 6, 6, 6]
    assert cir["pct"][0] == pytest.approx(198 / 288) and cir["cum_pct"][-1] == pytest.approx(1.0)
    cli = p.filter(pl.col("categoria") == "03 Procedimentos clínicos").sort("faixa")
    assert cli["n"].to_list() == [102, 0, 0, 0, 0, 0, 0] and cli["taxa"][1] == 0.0


def test_ajuste_potencia_exata():
    # T(d) = 8 · (d/10)^-1.5 nas distâncias de referência
    ds = [5.0, 17.0, 37.0, 72.0, 140.0, 290.0]
    linhas = [{"faixa": i, "d_min": 0, "d_max": 10, "d_ref": d, "pop": 1e5, "n": 100, "taxa": 8 * (d / 10) ** -1.5} for i, d in enumerate(ds)]
    r = A.ajustar_categoria(linhas, d_max=400)
    assert r["modelo"] == "potencia" and r["beta"] == pytest.approx(1.5, abs=1e-6) and r["T0"] == pytest.approx(8.0, rel=1e-6)
    assert r["R2"] == pytest.approx(1.0) and r["d_meia"] == pytest.approx(10 * 2 ** (1 / 1.5))
    assert 0 < r["ia"] < 0.2 and "^−1.50" in r["formula"]


def test_ajuste_exponencial_exata_e_indices():
    ds = [5.0, 17.0, 37.0, 72.0]
    linhas = [{"faixa": i, "d_min": lo, "d_max": hi, "d_ref": d, "pop": 1e5, "n": n, "taxa": 6 * math.exp(-0.02 * (d - 10))}
              for i, (d, lo, hi, n) in enumerate(zip(ds, [0, 10, 25, 50], [10, 25, 50, 100], [50, 50, 60, 40]))]
    r = A.ajustar_categoria(linhas, d_max=100)
    assert r["modelo"] == "exponencial" and r["lambda"] == pytest.approx(0.02, abs=1e-6)
    assert r["d_meia"] == pytest.approx(10 + math.log(2) / 0.02)
    assert r["d50"] == pytest.approx(25.0)            # 100 de 200 acumuladas exatamente no fim da 2ª faixa
    assert r["d80"] == pytest.approx(50 + (160 - 160) / 40 * 50)
    assert r["pct_local"] == pytest.approx(0.25)
    assert A.descrever_ajuste(r).startswith("Modelo exponencial")


def test_ajustar_decaimento_tabela(aih, hospital, municipios):
    p = A.perfil_completo(aih, hospital, municipios, nivel="grupo")
    r = A.ajustar_decaimento(p, limites=A.LIMITES_PADRAO)
    assert r["categoria"][0] == A.TODOS                       # hospital como um todo vem primeiro
    cir = r.filter(pl.col("categoria") == "04 Procedimentos cirúrgicos").row(0, named=True)
    assert cir["modelo"] in ("potencia", "exponencial") and cir["n"] == 288 and cir["n_pontos"] == 7
    assert cir["d50"] is not None and 0 < cir["d50"] < 10
    cli = r.filter(pl.col("categoria") == "03 Procedimentos clínicos").row(0, named=True)
    assert cli["modelo"] is None and cli["n_pontos"] == 1   # só uma faixa com internações


def test_taxas_municipais_e_histograma(aih, municipios):
    t = A.taxas_municipais(aih, municipios, uf="MG", cnes_destaque="1234567")
    assert t.height == 8                                     # só MG
    m3 = t.filter(pl.col("COD_MUN") == "310002").row(0, named=True)
    assert m3["n"] == 48 and m3["n_hosp"] == 18              # 18 no hospital + 30 no outro
    assert m3["taxa"] == pytest.approx(48 * 2 / 50_000 * 1000)
    assert t.filter(pl.col("COD_MUN") == "310001")["taxa"][0] == 0.0
    e = A.estatisticas_taxas(t)
    assert e["n_mun"] == 8 and e["n_zero"] == 1 and e["taxa_uf"] > 0 and e["mediana"] is not None
    h = A.histograma(t)
    assert sum(h["contagens"]) == 8 and len(h["bordas"]) == len(h["contagens"]) + 1
    assert all(c <= d for c, d in zip(h["bordas"], h["bordas"][1:]))


def test_payloads_serializaveis(aih, hospital, municipios):
    import json
    lim = A.LIMITES_PADRAO
    d = A.distancias(hospital, municipios, lim)
    p = A.perfil_faixas(aih, hospital, municipios, nivel="todos", limites=lim, dist_df=d)
    nm = A.internacoes_por_municipio(aih, "1234567", "todos", None)
    pay = A.payload_faixas(p, d, hospital, lim, nm)
    json.dumps(pay)
    assert pay["municipios"]["311860"]["faixa"] == 1 and pay["municipios"]["311860"]["n"] == 42
    t = A.taxas_municipais(aih, municipios, uf="31")
    pc = A.payload_coropleto(t, uf="31", titulo="TIH", destaque="310620")
    json.dumps(pc)
    assert len(pc["breaks"]) == 4 and pc["detalhe"]["310002"]["n"] == 48
    hd = A.hospitais_do_dataset(aih, pl.DataFrame({"CNES": ["1234567", "7654321"], "NOME": ["H1", "H2"],
                                                   "LAT": [LAT0, None], "LON": [LON0, None], "COD_MUN": ["310620", "310002"]}))
    assert hd.height == 1 and A.hospital_dict(hd.row(0, named=True))["cod_mun"] == "310620"


def test_insumos_do_app_normaliza_tabelas_do_cnes():
    geo = pl.DataFrame({"CO_CNES": ["26921", "0027014"], "NO_FANTASIA": ["HOSP A", None], "NU_LATITUDE": [-19.9, 0.0],
                        "NU_LONGITUDE": [-43.9, 0.0], "CO_MUNICIPIO_GESTOR": ["310620", "3106200"]})
    mun = pl.DataFrame({"CO_MUNICIPIO": ["310620", "311860", "999999"], "NO_MUNICIPIO": ["BELO HORIZONTE", "CONTAGEM", "X"],
                        "NU_LATITUDE": [-19.9, None, None], "NU_LONGITUDE": [-43.9, None, None], "NU_POPULACAO": [2400000.0, 600000.0, 1.0]})
    hosp, m = A.insumos_do_app(geo, mun, completar_malha=True)
    assert hosp["CNES"].to_list() == ["0026921", "0027014"] and hosp["COD_MUN"].to_list() == ["310620", "310620"]
    assert hosp.filter(pl.col("CNES") == "0027014")["LAT"][0] is None          # (0,0) vira nulo
    bh = m.filter(pl.col("COD_MUN") == "310620").row(0, named=True)
    assert bh["POP"] == 2400000.0 and bh["UF"] == "MG"
    ct = m.filter(pl.col("COD_MUN") == "311860").row(0, named=True)
    assert ct["LAT"] is not None and ct["POP"] == 600000.0                        # centroide veio da malha
    assert m.height >= 5000                                                       # malha completa, não só os 3 do CNES


def test_hospitais_do_dataset_mantem_hospitais_sem_aih(aih):
    hosp = pl.DataFrame({"CNES": ["1234567", "7654321", "0000001"], "NOME": ["H1", "H2", "Sem AIH"],
                         "LAT": [LAT0, LAT0, LAT0], "LON": [LON0, LON0, LON0], "COD_MUN": ["310620", "310002", "310003"]})
    hd = A.hospitais_do_dataset(aih, hosp)
    assert hd["CNES"].to_list() == ["1234567", "7654321", "0000001"] and hd["n"].to_list()[-1] == 0
    assert A.hospitais_do_dataset(aih, hosp, somente_com_aih=True).height == 2
    # sem coordenada: some sem municípios; com municípios, usa o centroide
    sem = hosp.with_columns(pl.when(pl.col("CNES") == "0000001").then(None).otherwise(pl.col("LAT")).alias("LAT"))
    assert A.hospitais_do_dataset(aih, sem).height == 2
    com = A.hospitais_do_dataset(aih, sem, municipios=pl.DataFrame({"COD_MUN": ["310003"], "NOME": ["M4"], "LAT": [-20.5], "LON": [-43.9]}))
    assert com.height == 3 and com.filter(pl.col("CNES") == "0000001")["COORD"][0] == "centroide do município"


def test_hospitais_da_uf_tipo_nome_flags_ou_aih(aih, municipios):
    est = pl.DataFrame({
        "CO_CNES": ["26921", "2222222", "3333333", "4444444", "5555555", "6666666"],
        "NO_FANTASIA": ["HOSP A", "UBS CENTRO", "CLINICA X", "SANTA CASA DE Y", "HOSPITAL DE SP", "CLINICA COM INTERNACAO"],
        "NU_LATITUDE": [-19.9, -19.9, -19.9, 0.0, -23.5, None], "NU_LONGITUDE": [-43.9, -43.9, -43.9, 0.0, -46.6, None],
        "CO_MUNICIPIO_GESTOR": ["0310620", "0310620", "0310002", "0310004", "0355030", "0310003"],
        "TP_UNIDADE": ["05", "01", "22", "05", "05", "36"], "ATENDHOS": ["1", "0", "0", "1", "1", "1"],
    })
    com = A.hospitais_da_uf(est, "MG", aih.with_columns(pl.lit("3333333").alias("CNES")), municipios=municipios)
    assert sorted(com["CNES"].to_list()) == ["0026921", "3333333", "4444444", "6666666"]   # clínica com AIH e clínica com ATENDHOS=1 entram; UBS não; SP fora
    assert com["CNES"][0] == "3333333"                                                     # quem tem AIH vem primeiro
    assert com.filter(pl.col("CNES") == "4444444")["COORD"][0] == "centroide do município"   # (0,0) → centroide
    assert com.filter(pl.col("CNES") == "6666666")["LAT"][0] is not None                  # sem coordenada → centroide
    assert com.filter(pl.col("CNES") == "0026921")["TIPO"][0] == "Hospital geral" and com["n"].to_list() == [426, 0, 0, 0]
    assert A.hospitais_da_uf(est, "31").height == 3                                       # sem AIH: tipo, nome ou flag
    # layout de disseminação ST*.dbf (IT_CNES 2017-06): sem nome, com flags e leitos
    st = pl.DataFrame({"CNES": ["1234567", "7777777", "8888888"], "CODUFMUN": ["310620", "310620", "310002"],
                       "TP_UNID": ["05", "22", "22"], "ATENDHOS": ["1", "0", "0"], "LEITHOSP": ["1", "0", "0"],
                       "QTLEITP1": [10, 0, 0], "QTLEITP2": [20, 0, 3], "QTLEITP3": [0, 0, 0]})
    h = A.hospitais_da_uf(st, "MG", aih, municipios=municipios)
    assert h["CNES"].to_list() == ["1234567", "8888888"] and h["NOME"].to_list()[1] == "CNES 8888888"
    assert h["COORD"].to_list() == ["centroide do município"] * 2 and h["n"].to_list() == [396, 0]
    # leitos por estabelecimento (LT*.dbf) também classificam
    lt = pl.DataFrame({"CNES": ["7777777"], "QT_EXIST": [5]})
    assert "7777777" in A.hospitais_da_uf(st, "MG", leitos=lt)["CNES"].to_list()


def test_market_share_e_share_por_faixa(aih, hospital, municipios):
    lim = A.LIMITES_PADRAO
    d = A.distancias(hospital, municipios, lim)
    hosp = pl.DataFrame({"CNES": ["1234567", "7654321"], "NOME": ["Hospital Teste", "Outro"], "LAT": [LAT0, LAT0 - 0.36], "LON": [LON0, LON0], "COD_MUN": ["310620", "310002"]})
    ms = A.market_share(aih, hospital, d, 2, hospitais=hosp)               # faixa 25–50 km = M3
    assert ms["CNES"].to_list() == ["7654321", "1234567"] and ms["n"].to_list() == [30, 18]
    assert ms["pct"][0] == pytest.approx(30 / 48) and ms["cum_pct"][-1] == pytest.approx(1.0)
    assert ms.filter(pl.col("foco"))["dist_km"][0] == 0.0 and 38 < ms["dist_km"][0] < 42
    # só cirurgias: o outro hospital (clínico) some
    ms_c = A.market_share(aih, hospital, d, 2, nivel="grupo", categoria="04 Procedimentos cirúrgicos", hospitais=hosp)
    assert ms_c.height == 1 and ms_c["pct"][0] == pytest.approx(1.0)
    acum = A.market_share(aih, hospital, d, 2, hospitais=hosp, acumulado=True)   # faixas 0..2
    assert int(acum["n"].sum()) == 300 + 42 + 48
    spf = A.share_por_faixa(aih, hospital, d, lim)
    assert spf.height == 7 and spf.filter(pl.col("faixa") == 2).row(0, named=True)["share_foco"] == pytest.approx(18 / 48)
    r0 = spf.filter(pl.col("faixa") == 0).row(0, named=True)
    assert r0["n_hospitais"] == 1 and r0["hhi"] == pytest.approx(1.0) and r0["cnes_lider"] == "1234567"
