"""Importação de pasta/arquivo RD (dbf/dbc/parquet) para as telas de atratividade."""
from __future__ import annotations

import struct
import sys
import types
from pathlib import Path

import polars as pl
import pytest

from papahr.dashboard import atratividade as A
from papahr.dashboard import importar_rd as I

# --------------------------------------------------------------------------- um DBF mínimo (dBase III)
CAMPOS = [("N_AIH", "C", 13, 0), ("CNES", "C", 7, 0), ("MUNIC_RES", "C", 6, 0), ("PROC_REA", "C", 10, 0),
          ("DIAG_PRINC", "C", 4, 0), ("ESPEC", "C", 2, 0), ("CAR_INT", "C", 2, 0), ("COMPLEX", "C", 2, 0),
          ("IDADE", "N", 3, 0), ("COD_IDADE", "C", 1, 0), ("SEXO", "C", 1, 0), ("ANO_CMPT", "C", 4, 0),
          ("MES_CMPT", "C", 2, 0), ("MUNIC_MOV", "C", 6, 0), ("VAL_TOT", "N", 13, 2), ("DIAS_PERM", "N", 5, 0),
          ("MORTE", "N", 1, 0)]


def escrever_dbf(p: Path, linhas: list[dict]) -> None:
    tam_reg = 1 + sum(c[2] for c in CAMPOS)
    tam_cab = 32 + 32 * len(CAMPOS) + 1
    cab = struct.pack("<BBBBIHH20x", 0x03, 25, 1, 1, len(linhas), tam_cab, tam_reg)
    desc = b"".join(struct.pack("<11sc4xBB14x", n.encode().ljust(11, b"\0"), t.encode(), tam, dec) for n, t, tam, dec in CAMPOS)
    regs = bytearray()
    for r in linhas:
        regs += b" "
        for n, t, tam, dec in CAMPOS:
            v = r.get(n, "")
            s = (f"{float(v):.{dec}f}" if dec else str(int(v))).rjust(tam) if t == "N" else str(v).ljust(tam)
            regs += s.encode("latin-1")[:tam]
    p.write_bytes(cab + desc + b"\r" + bytes(regs) + b"\x1a")


def _aih(cnes, mun, proc, n, ano="2025", mes="01", cid="I219"):
    return [{"N_AIH": f"31{ano}{mes}{i:07d}", "CNES": cnes, "MUNIC_RES": mun, "PROC_REA": proc, "DIAG_PRINC": cid,
             "ESPEC": "01", "CAR_INT": "02", "COMPLEX": "02", "IDADE": 40 + i % 30, "COD_IDADE": "4", "SEXO": "1",
             "ANO_CMPT": ano, "MES_CMPT": mes, "MUNIC_MOV": "310620", "VAL_TOT": 1234.5, "DIAS_PERM": 3, "MORTE": 0}
            for i in range(n)]


@pytest.fixture
def pasta_rd(tmp_path, monkeypatch):
    monkeypatch.setenv("GSPHOSP_HOME", str(tmp_path / "home"))   # cache isolado
    d = tmp_path / "AIH_RD_2025"; d.mkdir()
    escrever_dbf(d / "RDMG2501.dbf", _aih("0026921", "310620", "0408050012", 30) + _aih("7654321", "311860", "0303010037", 12))
    escrever_dbf(d / "RDMG2502.DBF", _aih("0026921", "310620", "0408050012", 24, mes="02") + _aih("0026921", "310002", "0303010037", 6, mes="02"))
    (d / "SPMG2501.dbc").write_bytes(b"\x03" * 20)        # outro sistema: ignorado
    (d / "notas.txt").write_text("x")
    return d


# --------------------------------------------------------------------------- testes
def test_listar_e_descrever(pasta_rd, tmp_path):
    a = I.listar_arquivos(pasta_rd)
    assert [f.name for f in a["rd"]] == ["RDMG2501.dbf", "RDMG2502.DBF"] and [f.name for f in a["outros"]] == ["SPMG2501.dbc"]
    assert "2 arquivo(s) RD do SIH (MG; 2025-01 a 2025-02)" in I.descrever_pasta(pasta_rd)
    assert I.descrever_pasta(tmp_path / "vazia").startswith("nenhum")


def test_ler_pasta_rd_concatena_normaliza_e_usa_cache(pasta_rd):
    msgs: list[str] = []
    aih = I.ler_pasta_rd(pasta_rd, progresso=msgs.append)
    assert aih.height == 72 and "N_AIH" not in aih.columns and "ARQUIVO" in aih.columns
    assert aih.schema["IDADE"] == pl.Float64 and aih.schema["CNES"] == pl.Utf8 and aih["CNES"][0] == "0026921"
    assert aih["ANOMES_CMPT"].unique().sort().to_list() == ["202501", "202502"] and aih["FONTE"].unique().to_list() == ["SIH"]
    assert A.meses_competencia(aih) == 2
    assert any("consolidado guardado" in m for m in msgs)
    msgs.clear()
    aih2 = I.ler_pasta_rd(pasta_rd, progresso=msgs.append)
    assert aih2.height == 72 and any("já consolidados em cache" in m for m in msgs)
    (pasta_rd / "RDMG2503.dbf").write_bytes((pasta_rd / "RDMG2501.dbf").read_bytes())   # pasta mudou → relê
    msgs.clear()
    assert I.ler_pasta_rd(pasta_rd, progresso=msgs.append).height == 114 and not any("cache" in m and "já" in m for m in msgs)


def test_leitura_rapida_dbf_ignora_apagados_e_bate_com_dbfread(pasta_rd):
    p = pasta_rd / "RDMG2501.dbf"
    raw = bytearray(p.read_bytes())
    n_reg, tam_cab, tam_reg = struct.unpack_from("<IHH", raw, 4)
    raw[tam_cab + 3 * tam_reg] = ord("*")                     # 4º registro marcado como apagado
    p.write_bytes(bytes(raw))
    rapido, n_campos = I._ler_dbf_rapido(p.read_bytes())
    assert rapido.height == 41 and n_campos == len(CAMPOS) and "N_AIH" not in rapido.columns
    assert rapido.schema["CNES"] == pl.Utf8 and rapido.schema["VAL_TOT"] == pl.Float64 and rapido["VAL_TOT"][0] == 1234.5
    try:
        from papahr.core.dbc_reader import read_dbc
    except Exception:  # noqa: BLE001
        pytest.skip("leitor do app indisponível neste ambiente")
    lento = I._normalizar(I._projetar(read_dbc(p)))
    a, b = I._normalizar(rapido), lento
    assert a.select(sorted(a.columns)).equals(b.select(sorted(a.columns)))


def test_ler_fonte_arquivo_unico_e_parquet(pasta_rd, tmp_path):
    df, origem = I.ler_fonte(pasta_rd / "RDMG2501.dbf")
    assert df.height == 42 and origem == "RDMG2501.dbf"
    p = tmp_path / "saida"; p.mkdir()
    df.with_columns(pl.lit("lixo").alias("COLUNA_INUTIL")).write_parquet(p / "outputSIH_RD.parquet")
    df2, origem2 = I.ler_fonte(p)                                   # pasta sem RD → parquet mais recente
    assert df2.height == 42 and "COLUNA_INUTIL" not in df2.columns and origem2 == "outputSIH_RD.parquet"
    with pytest.raises(FileNotFoundError):
        I.ler_fonte(tmp_path / "nada")


def test_enriquecer_sigtap_e_rotulo_sem_codigo_repetido(pasta_rd):
    aih = I.ler_pasta_rd(pasta_rd, usar_cache=False)
    tab = pl.DataFrame({"CO_PROCEDIMENTO": ["0408050012", "0303010037"],
                        "NO_GRUPO": ["04-Procedimentos cirúrgicos", "03-Procedimentos clínicos"],
                        "NO_SUB_GRUPO": ["08-Cirurgia do sistema osteomuscular", "03-Tratamentos clínicos (outras especialidades)"],
                        "NO_PROCEDIMENTO": ["0408050012-ARTROPLASTIA", "0303010037-TRATAMENTO DE IAM"]})
    out, aviso = I.enriquecer_sigtap(aih, tab)
    assert "NO_GRUPO" in out.columns and "NO_PROCEDIMENTO" in out.columns and aviso.startswith("nomes SIGTAP")
    cls = A.classificar(out, "grupo")
    assert set(cls["categoria"].unique().to_list()) == {"04 Procedimentos cirúrgicos", "03 Procedimentos clínicos"}
    assert set(A.classificar(out, "subgrupo")["categoria"].unique().to_list()) == {"0408 Cirurgia do sistema osteomuscular", "0303 Tratamentos clínicos (outras especialidades)"}
    assert A.classificar(out, "procedimento")["categoria"][0] == "0408050012 ARTROPLASTIA"
    # sem SIGTAP: dicionários embutidos para grupo/subgrupo
    sem, aviso2 = I.enriquecer_sigtap(aih, None) if I.tabela_sigtap() is None else (aih, "SIGTAP não está em cache")
    assert "SIGTAP não está em cache" in aviso2
    assert A.classificar(sem, "grupo")["categoria"][0].startswith("04 ")


def test_carregar_fonte_monta_hospitais_e_municipios(pasta_rd, monkeypatch):
    est = pl.DataFrame({
        "CO_CNES": ["26921", "7654321", "2222222", "5555555"],
        "NO_FANTASIA": ["HOSP A", "HOSPITAL B", "UBS CENTRO", "HOSPITAL DE SP"],
        "NU_LATITUDE": [-19.9, -19.95, -19.9, -23.5], "NU_LONGITUDE": [-43.9, -44.05, -43.9, -46.6],
        "CO_MUNICIPIO_GESTOR": ["0310620", "0311860", "0310620", "0355030"],
        "TP_UNIDADE": ["05", "05", "01", "05"], "ATENDHOS": ["1", "1", "0", "1"],
    })
    mun = pl.DataFrame({"CO_MUNICIPIO": ["310620", "311860", "310002", "355030"], "NO_MUNICIPIO": ["Belo Horizonte", "Contagem", "M3", "São Paulo"],
                        "NU_LATITUDE": [-19.92, -19.93, -20.3, -23.5], "NU_LONGITUDE": [-43.94, -44.05, -43.94, -46.6],
                        "NU_POPULACAO": [2_000_000, 600_000, 50_000, 11_000_000]})
    fake = types.ModuleType("papahr.dashboard.cnes_loader")
    fake.get_cnes_data = lambda: types.SimpleNamespace(load=lambda: types.SimpleNamespace(estabelecimentos=est, municipios=mun))
    monkeypatch.setitem(sys.modules, "papahr.dashboard.cnes_loader", fake)
    msgs: list[str] = []
    c = I.carregar_fonte(pasta_rd, sem_ibge=True, progresso=msgs.append)
    assert c.uf == "MG" and c.hospitais["CNES"].to_list() == ["0026921", "7654321"] and c.hospitais["n"].to_list() == [60, 12]
    assert c.municipios.filter(pl.col("COD_MUN") == "310620")["POP"][0] == 2_000_000
    assert "2 CNES com AIH" in c.descricao and any("competência" in a for a in c.avisos)   # 2 meses → aviso de anualização
    assert c.aih.height == 72


# --------------------------------------------------------------------------- SIGTAP (cache do app)
def _fw(rows, widths):
    return "\n".join("".join(str(c).ljust(w)[:w] for c, w in zip(r, widths)) for r in rows) + "\n"


def _layout(cols):
    out, pos = ["Coluna,Tamanho,Inicio,Fim,Tipo"], 1
    for n, w in cols:
        out.append(f"{n},{w},{pos},{pos + w - 1},VARCHAR2"); pos += w
    return "\n".join(out) + "\n"


@pytest.fixture
def sigtap_cache(tmp_path, monkeypatch):
    """Versão 202501 do SIGTAP em cache, com o layout oficial (tb_procedimento SEM CO_GRUPO)."""
    pytest.importorskip("papahr.datasources.sigtap", reason="módulo SIGTAP do app ausente neste ambiente")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    from papahr.config import get_settings
    get_settings.cache_clear()
    v = tmp_path / "cache" / "SIGTAP" / "202501"; v.mkdir(parents=True)
    pc = [("CO_PROCEDIMENTO", 10), ("NO_PROCEDIMENTO", 250), ("TP_COMPLEXIDADE", 1), ("TP_SEXO", 1), ("QT_MAXIMA_EXECUCAO", 4),
          ("QT_DIAS_PERMANENCIA", 4), ("QT_PONTOS", 4), ("VL_IDADE_MINIMA", 4), ("VL_IDADE_MAXIMA", 4), ("VL_SH", 10), ("VL_SA", 10),
          ("VL_SP", 10), ("CO_FINANCIAMENTO", 2), ("CO_RUBRICA", 6), ("QT_TEMPO_PERMANENCIA", 4), ("DT_COMPETENCIA", 6)]
    procs = [("0303010037", "TRATAMENTO DE IAM", "2", "N", "1", "5", "0", "0", "9999", "0", "0", "0", "06", "", "0", "202501"),
             ("0408050012", "ARTROPLASTIA", "3", "N", "1", "5", "0", "0", "9999", "0", "0", "0", "06", "", "0", "202501"),
             ("0310010039", "PARTO NORMAL", "2", "F", "1", "2", "0", "0", "9999", "0", "0", "0", "06", "", "0", "202501")]
    (v / "tb_procedimento.txt").write_text(_fw(procs, [w for _, w in pc]), encoding="latin-1"); (v / "tb_procedimento_layout.txt").write_text(_layout(pc))
    gc = [("CO_GRUPO", 2), ("NO_GRUPO", 100), ("DT_COMPETENCIA", 6)]
    (v / "tb_grupo.txt").write_text(_fw([("03", "Procedimentos clínicos", "202501"), ("04", "Procedimentos cirúrgicos", "202501"),
                                         ("08", "Ações complementares", "202501")], [w for _, w in gc]), encoding="latin-1")
    (v / "tb_grupo_layout.txt").write_text(_layout(gc))
    sc = [("CO_GRUPO", 2), ("CO_SUB_GRUPO", 2), ("NO_SUB_GRUPO", 100), ("DT_COMPETENCIA", 6)]
    (v / "tb_sub_grupo.txt").write_text(_fw([("03", "03", "Tratamentos clínicos (outras especialidades)", "202501"), ("03", "10", "Parto e nascimento", "202501"),
                                             ("04", "08", "Cirurgia do sistema osteomuscular", "202501"), ("08", "01", "Atenção em Nefrologia", "202501")],
                                            [w for _, w in sc]), encoding="latin-1")
    (v / "tb_sub_grupo_layout.txt").write_text(_layout(sc))
    fc = [("CO_GRUPO", 2), ("CO_SUB_GRUPO", 2), ("CO_FORMA_ORGANIZACAO", 2), ("NO_FORMA_ORGANIZACAO", 100), ("DT_COMPETENCIA", 6)]
    (v / "tb_forma_organizacao.txt").write_text(_fw([("03", "03", "01", "Tratamento de doenças infecciosas", "202501"), ("03", "10", "01", "Parto", "202501"),
                                                     ("04", "08", "05", "Cirurgia de quadril", "202501"), ("08", "01", "01", "Diálise", "202501")],
                                                    [w for _, w in fc]), encoding="latin-1")
    (v / "tb_forma_organizacao_layout.txt").write_text(_layout(fc))
    yield v
    get_settings.cache_clear()


def test_tabela_sigtap_nomes_certos_por_procedimento(sigtap_cache):
    t = I.tabela_sigtap()
    assert t is not None and t.height == 3
    d = {r["CO_PROCEDIMENTO"]: r for r in t.to_dicts()}
    assert d["0408050012"]["NO_GRUPO"] == "Procedimentos cirúrgicos" and d["0408050012"]["NO_SUB_GRUPO"] == "Cirurgia do sistema osteomuscular"
    assert d["0310010039"]["NO_SUB_GRUPO"] == "Parto e nascimento" and d["0310010039"]["NO_FORMA_ORGANIZACAO"] == "Parto"
    assert d["0303010037"]["COMPLEXIDADE"] == "Média complexidade" and d["0408050012"]["COMPLEXIDADE"] == "Alta complexidade"


def test_sanear_nomes_corrompidos_do_pipeline(sigtap_cache):
    aih = pl.DataFrame({"CNES": ["1"] * 3, "MUNIC_RES": ["310620"] * 3, "PROC_REA": ["0303010037", "0408050012", "0310010039"],
                        "NO_GRUPO": ["08-Ações complementares"] * 3, "NO_SUB_GRUPO": ["01-Atenção em Nefrologia"] * 3,
                        "Forma de organizacao": ["01-Diálise"] * 3, "NO_PROCEDIMENTO": ["0303010037-A", "0408050012-B", "0310010039-C"]})
    san, avisos = I.sanear_nomes_sigtap(aih)
    assert set(san.columns) == {"CNES", "MUNIC_RES", "PROC_REA", "NO_PROCEDIMENTO"} and avisos and "NO_SUB_GRUPO" in avisos[0]
    out, _ = I.enriquecer_sigtap(san)
    assert sorted(A.classificar(out, "subgrupo")["categoria"].unique().to_list()) == [
        "0303 Tratamentos clínicos (outras especialidades)", "0310 Parto e nascimento", "0408 Cirurgia do sistema osteomuscular"]
    assert A.classificar(out, "procedimento")["categoria"][1] == "0408050012 B"       # nome íntegro do arquivo é mantido
    ok, av2 = I.sanear_nomes_sigtap(out)
    assert not av2                                                                    # nada a sanear depois de refeito


def test_sigtap_do_app_da_um_nome_por_procedimento(sigtap_cache):
    """Falha enquanto papahr/datasources/sigtap.py tiver o defeito; aplique scripts/corrigir_sigtap_app.py."""
    from papahr.datasources.sigtap import SIGTAPRepository
    det = SIGTAPRepository().get_procedure_details().df
    d = {r["CO_PROCEDIMENTO"]: r for r in det.to_dicts()}
    assert d["0408050012"]["NO_SUB_GRUPO"].endswith("Cirurgia do sistema osteomuscular") and d["0310010039"]["NO_SUB_GRUPO"].endswith("Parto e nascimento"), (
        "get_procedure_details() dá o mesmo grupo/subgrupo/forma a todos os procedimentos — rode: python scripts\\corrigir_sigtap_app.py")


# --------------------------------------------------------------------------- CIHA (não SUS) lado a lado com o SIH
CAMPOS_CIHA = [("CNES", "C", 7, 0), ("MUNIC_MOV", "C", 6, 0), ("ANO_CMPT", "C", 4, 0), ("MES_CMPT", "C", 2, 0), ("NASC", "C", 8, 0),
               ("SEXO", "C", 1, 0), ("MUNIC_RES", "C", 6, 0), ("DT_INTER", "C", 8, 0), ("DT_SAIDA", "C", 8, 0), ("DIAG_PRINC", "C", 4, 0),
               ("PROC_REA", "C", 10, 0), ("MODALIDADE", "C", 2, 0), ("CAR_INT", "C", 2, 0), ("FONTE", "C", 2, 0)]


def escrever_dbf_campos(p: Path, campos, linhas: list[dict]) -> None:
    tam_reg = 1 + sum(c[2] for c in campos); tam_cab = 32 + 32 * len(campos) + 1
    cab = struct.pack("<BBBBIHH20x", 0x03, 25, 1, 1, len(linhas), tam_cab, tam_reg)
    desc = b"".join(struct.pack("<11sc4xBB14x", n.encode().ljust(11, b"\0"), t.encode(), tam, dec) for n, t, tam, dec in campos)
    regs = bytearray()
    for r in linhas:
        regs += b" "
        for n, t, tam, dec in campos:
            v = r.get(n, "")
            s = (f"{float(v):.{dec}f}" if dec else str(int(v))).rjust(tam) if t == "N" else str(v).ljust(tam)
            regs += s.encode("latin-1")[:tam]
    p.write_bytes(cab + desc + b"\r" + bytes(regs) + b"\x1a")


def _ciha(cnes, mun, proc, n, mes="01", pagador="01", modalidade="01"):
    return [{"CNES": cnes, "MUNIC_MOV": "310620", "ANO_CMPT": "2025", "MES_CMPT": mes, "NASC": f"{1985 - i % 40:04d}0315", "SEXO": "3",
             "MUNIC_RES": mun, "DT_INTER": f"2025{mes}10", "DT_SAIDA": f"2025{mes}14", "DIAG_PRINC": "I219", "PROC_REA": proc,
             "MODALIDADE": modalidade, "CAR_INT": "02", "FONTE": pagador} for i in range(n)]


def test_ciha_lado_a_lado_com_rd(pasta_rd):
    escrever_dbf_campos(pasta_rd / "CIHAMG2501.dbf", CAMPOS_CIHA,
                        _ciha("9999999", "310620", "0408050012", 20) + _ciha("0026921", "311860", "0303010037", 10, pagador="02", modalidade="02"))
    assert "1 arquivo(s) CIHA, não SUS (MG; 2025-01 a 2025-01)" in I.descrever_pasta(pasta_rd)
    aih = I.ler_pasta_rd(pasta_rd, usar_cache=False)
    assert aih.height == 72 + 30
    por = {r["FONTE"]: r["n"] for r in aih.group_by("FONTE").agg(pl.len().alias("n")).to_dicts()}
    assert por == {"SIH": 72, "CIHA": 30}
    c = aih.filter(pl.col("FONTE") == "CIHA")
    assert "FONTE_ARQ" not in c.columns and set(c["PAGADOR"].unique().to_list()) == {"01", "02"}      # FONTE do arquivo → PAGADOR
    assert set(c["MODALIDADE"].unique().to_list()) == {"01", "02"} and c["IDADE"].min() >= 0 and c["IDADE"].max() <= 80   # idade de NASC/DT_INTER
    assert c["ANOMES_CMPT"].unique().to_list() == ["202501"] and A.meses_competencia(aih) == 2
    # níveis novos só aparecem com as duas fontes; filtro por fonte
    niveis = dict(A.niveis_disponiveis(aih))
    assert "fonte" in niveis and "pagador" in niveis and "modalidade" in niveis
    assert "fonte" not in dict(A.niveis_disponiveis(aih.filter(pl.col("FONTE") == "SIH")))
    assert A.filtrar_fonte(aih, "CIHA").height == 30 and A.filtrar_fonte(aih, None).height == 102 and A.fontes_presentes(aih) == ["SIH", "CIHA"]
    cls = A.classificar(aih, "fonte")
    assert set(cls["categoria"].unique().to_list()) == {"SIH", "CIHA"}
    # market share com % SUS: hospital 9999999 só tem CIHA; 0026921 tem SIH e CIHA
    mun = pl.DataFrame({"COD_MUN": ["310620", "311860", "310002"], "NOME": ["BH", "Contagem", "M3"], "LAT": [-19.92, -19.93, -20.3], "LON": [-43.94, -44.05, -43.94], "POP": [2e6, 6e5, 5e4]})
    h = {"cnes": "0026921", "nome": "A", "lat": -19.92, "lon": -43.94, "cod_mun": "310620"}
    d = A.distancias(h, mun, A.LIMITES_PADRAO)
    ms = A.market_share(aih, h, d, 0)                       # faixa 0 = BH
    r = {x["CNES"]: x for x in ms.to_dicts()}
    assert r["9999999"]["pct_sih"] == 0.0 and r["0026921"]["pct_sih"] == pytest.approx(54 / 54) and "7654321" not in r
    ms_c = A.market_share(A.filtrar_fonte(aih, "CIHA"), h, d, 0)
    assert ms_c["CNES"].to_list() == ["9999999"]


def test_ciha_competencia_pelo_nome_do_arquivo_e_sinonimos(pasta_rd):
    campos = [("CNES_EXEC", "C", 7, 0), ("MUNRES", "C", 6, 0), ("PROC_PRINC", "C", 10, 0), ("CID_PRINC", "C", 4, 0), ("SEXO", "C", 1, 0),
              ("IDADE", "N", 3, 0), ("TP_FINANC", "C", 2, 0), ("MODAL", "C", 2, 0)]
    linhas = [{"CNES_EXEC": "1234567", "MUNRES": "310620", "PROC_PRINC": "0303010037", "CID_PRINC": "J189", "SEXO": "1", "IDADE": 60 + i,
               "TP_FINANC": "03", "MODAL": "01"} for i in range(5)]
    escrever_dbf_campos(pasta_rd / "CIHAMG2502.dbf", campos, linhas)
    df, _ = I.ler_arquivo(pasta_rd / "CIHAMG2502.dbf")
    assert df["CNES"].to_list() == ["1234567"] * 5 and df["MUNIC_RES"][0] == "310620" and df["PROC_REA"][0] == "0303010037"
    assert df["DIAG_PRINC"][0] == "J189" and df["PAGADOR"][0] == "03" and df["MODALIDADE"][0] == "01" and df["FONTE"][0] == "CIHA"
    assert df["ANOMES_CMPT"][0] == "202502" and df["IDADE"][0] == 60.0
    rel = I.inspecionar_arquivo(pasta_rd / "CIHAMG2502.dbf")
    assert "5 registros, 8 campos" in rel and "TP_FINANC: '03'=5" in rel


# --------------------------------------------------------------------------- seleção (UF × período × fonte) e download
def test_competencias_inventario_e_selecao(pasta_rd):
    assert I.competencias("202411", "202502") == ["202411", "202412", "202501", "202502"] and I.competencias("202503", "202501") == []
    assert I.nome_arquivo("SIH", "mg", "202501") == "RDMG2501.dbc" and I.nome_arquivo("CIHA", "MG", "202512") == "CIHAMG2512.dbc"
    escrever_dbf_campos(pasta_rd / "CIHAMG2501.dbf", CAMPOS_CIHA, _ciha("9999999", "310620", "0408050012", 3))
    (pasta_rd / "RDSP2501.dbf").write_bytes((pasta_rd / "RDMG2501.dbf").read_bytes())
    inv = I.inventario(pasta_rd)
    assert inv.height == 4 and set(inv["uf"].to_list()) == {"MG", "SP"} and inv.filter(pl.col("fonte") == "CIHA").height == 1
    sel = I.Selecao(ufs=["MG"], inicio="202501", fim="202503", fontes=["SIH", "CIHA"])
    achados, faltam = I.arquivos_da_selecao(pasta_rd, sel)
    assert [a.name for a in achados] == ["RDMG2501.dbf", "RDMG2502.DBF", "CIHAMG2501.dbf"]
    assert faltam == [("SIH", "MG", "202503"), ("CIHA", "MG", "202502"), ("CIHA", "MG", "202503")]
    assert sel.rotulo() == "MG · 2025-01 a 2025-03 · SIH+CIHA"
    sel2 = I.Selecao(ufs=["MG"], inicio="202501", fim="202501", fontes=["SIH"], outras_ufs=True)
    achados2, _ = I.arquivos_da_selecao(pasta_rd, sel2)
    assert sorted(a.name for a in achados2) == ["RDMG2501.dbf", "RDSP2501.dbf"]
    # leitura só do que a seleção pede; partes por arquivo reaproveitadas entre seleções
    msgs: list[str] = []
    df = I.ler_fonte(pasta_rd, msgs.append, selecao=I.Selecao(ufs=["MG"], inicio="202501", fim="202501", fontes=["SIH"]))[0]
    assert df.height == 42 and any("FALTAM" not in m for m in msgs)
    msgs.clear()
    df2 = I.ler_fonte(pasta_rd, msgs.append, selecao=sel)[0]
    assert df2.height == 42 + 30 + 3 and any("já lidos numa carga anterior" in m for m in msgs) and any("FALTAM 3" in m for m in msgs)


def test_baixar_datasus_com_ftp_falso(tmp_path):
    class FTPFalso:
        arquivos = {"/dissemin/publicos/SIHSUS/200801_/Dados": {"RDMG2501.dbc": b"\x03" * 5000},
                    "/dissemin/publicos/CIHA/201101_/Dados": {"CIHAMG2501.dbc": b"\x03" * 4000}}
        def __init__(self): self.dir = None
        def login(self): pass
        def cwd(self, d): self.dir = d
        def retrbinary(self, cmd, cb, blocksize=8192):
            import ftplib
            nome = cmd.split(" ", 1)[1]
            dados = self.arquivos[self.dir].get(nome)
            if dados is None:
                raise ftplib.error_perm("550 Failed to open file.")
            cb(dados)
        def quit(self): pass
    msgs: list[str] = []
    baixados, erros = I.baixar_datasus([("SIH", "MG", "202501"), ("CIHA", "MG", "202501"), ("SIH", "MG", "202502")],
                                       tmp_path / "dl", progresso=msgs.append, ftp_factory=FTPFalso)
    assert [b.name for b in baixados] == ["RDMG2501.dbc", "CIHAMG2501.dbc"] and len(erros) == 1 and "RDMG2502.dbc" in erros[0]
    assert (tmp_path / "dl" / "RDMG2501.dbc").stat().st_size == 5000 and not list((tmp_path / "dl").glob("*.part"))
    b2, e2 = I.baixar_datasus([("SIH", "MG", "202501")], tmp_path / "dl", ftp_factory=FTPFalso)   # já existe: não baixa de novo
    assert b2 and not e2


# --------------------------------------------------------------------------- página Dados (Qt offscreen)
def test_pagina_dados_selecao_inventario_e_faltantes(pasta_rd, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(QSettings, "value", lambda self, k, d=None, *a, **kw: d)      # sem estado de sessões anteriores
    monkeypatch.setattr(QSettings, "setValue", lambda self, k, v: None)
    from papahr.ui.atratividade.dados import PaginaDados
    escrever_dbf_campos(pasta_rd / "CIHAMG2501.dbf", CAMPOS_CIHA, _ciha("9999999", "310620", "0408050012", 3))
    pg = PaginaDados()
    assert pg.selecao().inicio.endswith("01") and pg.selecao().fim.endswith("12")        # "ano fechado" por padrão
    pg.definir(pasta=str(pasta_rd), ufs=["MG"], inicio="202501", fim="202503", fontes=["SIH", "CIHA"])
    sel = pg.selecao()
    assert sel.ufs == ["MG"] and sel.fontes == ["SIH", "CIHA"] and sel.competencias() == ["202501", "202502", "202503"]
    assert pg._faltam == [("SIH", "MG", "202503"), ("CIHA", "MG", "202502"), ("CIHA", "MG", "202503")]
    assert "FALTAM 3" in pg.lbl_sel.texto() and pg.btn_baixar.isEnabled() and pg.btn_carregar.isEnabled()
    assert pg.chips["MG"].property("presente") is True and pg.chips["SP"].property("presente") is False
    pedidos = []
    pg.carregarPedido.connect(lambda pasta, s, sem: pedidos.append((pasta, s.rotulo(), sem)))
    pg.btn_carregar.click()
    assert pedidos == [(str(pasta_rd), "MG · 2025-01 a 2025-03 · SIH+CIHA", False)]
    pg.chips["MG"].setChecked(False)
    assert "marque ao menos uma UF" in pg.lbl_sel.texto() and not pg.btn_carregar.isEnabled()
    pg.btn_tudo.click()                                                                   # tudo na pasta → MG, 2025-01..02, SIH+CIHA
    s2 = pg.selecao()
    assert s2.ufs == ["MG"] and (s2.inicio, s2.fim) == ("202501", "202502") and s2.fontes == ["SIH", "CIHA"]
    assert pg._faltam == [("CIHA", "MG", "202502")]                                       # a CIHA só tem janeiro na pasta
