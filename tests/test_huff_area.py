"""Testes do modelo de Huff por área (dados sintéticos, sem acesso a disco)."""
import polars as pl
import pytest

from papahr.dashboard.huff_area import (METRICAS, classificar_areas, huff_por_area, listar_areas, payload_mapa)


@pytest.fixture
def dados():
    hosp = pl.DataFrame({
        "CNES": ["0000001", "0000002"], "NOME": ["H1", "H2"],
        "LAT": [-19.92, -20.14], "LON": [-43.94, -44.88], "COD_MUN": ["310620", "312230"],
    })
    mun = pl.DataFrame({
        "COD_MUN": ["310620", "312230", "311860", "310100"], "NOME": ["BH", "Divinópolis", "Contagem", "Abaeté"],
        "LAT": [-19.92, -20.14, -19.93, -19.16], "LON": [-43.94, -44.88, -44.05, -45.45],
        "REGIAO": ["Centro", "Oeste", "Centro", "Oeste"],
    })
    rows = []
    def add(cnes, mun_res, proc, n, cid="M171", espec="01"):
        rows.extend([{"CNES": cnes, "MUNIC_RES": mun_res, "PROC_REA": proc, "DIAG_PRINC": cid, "ESPEC": espec, "VAL_TOT": 1000.0}] * n)
    add("0000001", "310620", "0408050012", 80); add("0000001", "311860", "0408050012", 40); add("0000001", "312230", "0408050012", 5)
    add("0000002", "312230", "0408050012", 60); add("0000002", "310100", "0408050012", 30); add("0000002", "310620", "0408050012", 5)
    add("0000001", "310620", "0406010013", 50, cid="I219"); add("0000002", "312230", "0406010013", 10, cid="I219")
    add("0000001", "310620", "0303010037", 35, cid="J180", espec="03")
    return pl.DataFrame(rows), hosp, mun


def test_classificacao(dados):
    aih, _, _ = dados
    c = classificar_areas(aih)
    assert set(c["area_cod"].unique()) == {"0408", "0406", "0303"}
    assert c.filter(pl.col("area_cod") == "0408")["area_nome"][0].startswith("04.08 Cirurgia do sistema osteomuscular")
    assert c.filter(pl.col("DIAG_PRINC") == "I219")["cid_cap"][0] == "IX"
    assert c.filter(pl.col("DIAG_PRINC") == "M171")["cid_cap"][0] == "XIII"
    assert c.filter(pl.col("ESPEC") == "03")["espec_nome"][0] == "Clínico"


def test_huff_probabilidades_somam_um(dados):
    aih, hosp, mun = dados
    od, resumo = huff_por_area(classificar_areas(aih), hosp, mun, beta=1.5, min_aih_area=1)
    soma = od.group_by(["area_cod", "COD_MUN"]).agg(pl.col("p_huff").sum().alias("s"))
    assert soma["s"].min() == pytest.approx(1.0) and soma["s"].max() == pytest.approx(1.0)
    # esperado total = demanda total quando todos os hospitais estão no conjunto
    tot = od.group_by("area_cod").agg(pl.col("n_obs").sum().alias("o"), pl.col("n_esp").sum().alias("e"))
    for o, e in zip(tot["o"], tot["e"]):
        assert o == pytest.approx(e)


def test_resumo_indicadores(dados):
    aih, hosp, mun = dados
    od, resumo = huff_por_area(classificar_areas(aih), hosp, mun, min_aih_area=1)
    r = resumo.filter((pl.col("CNES") == "0000001") & (pl.col("area_cod") == "0408")).to_dicts()[0]
    assert r["capt_obs"] == 125
    assert r["pct_fora_mun"] == pytest.approx(45 / 125)
    assert r["pct_fora_regiao"] == pytest.approx(5 / 125)
    assert r["n_mun_80"] == 2           # BH (80) + Contagem (40) cobrem 96% → 2 municípios
    assert 0 < r["razao_obs_esp"] < 5


def test_min_aih_area_descarta(dados):
    aih, hosp, mun = dados
    od, resumo = huff_por_area(classificar_areas(aih), hosp, mun, min_aih_area=30)
    assert resumo.filter((pl.col("CNES") == "0000002") & (pl.col("area_cod") == "0406")).is_empty()


def test_filtros(dados):
    aih, hosp, mun = dados
    od, resumo = huff_por_area(classificar_areas(aih), hosp, mun, min_aih_area=1, filtro_cid=["IX"])
    assert set(resumo["area_cod"].unique()) == {"0406"}
    od, resumo = huff_por_area(classificar_areas(aih), hosp, mun, min_aih_area=1, filtro_espec=["03"])
    assert set(resumo["area_cod"].unique()) == {"0303"}


def test_payload(dados):
    aih, hosp, mun = dados
    od, resumo = huff_por_area(classificar_areas(aih), hosp, mun, min_aih_area=1)
    for m in METRICAS:
        p = payload_mapa(od, resumo, "0408", metrica=m, cnes=None, hospitais=hosp)
        assert p["area"]["cod"] == "0408" and len(p["hospitais"]) == 2
        assert p["hospitais"][0]["lat"] is not None
        assert "310620" in p["valores"] and "310620" in p["dominante"]
    p = payload_mapa(od, resumo, "0408", metrica="share_obs", cnes="0000002", hospitais=hosp)
    assert p["selecionado"] == "0000002" and p["dominante"] == {}
    assert p["valores"]["312230"] == pytest.approx(60 / 65)
    assert listar_areas(resumo)[0][0] == "0408"


def test_area_inexistente_payload_vazio(dados):
    aih, hosp, mun = dados
    od, resumo = huff_por_area(classificar_areas(aih), hosp, mun, min_aih_area=1)
    p = payload_mapa(od, resumo, "9999")
    assert p["hospitais"] == [] and p["valores"] == {}
