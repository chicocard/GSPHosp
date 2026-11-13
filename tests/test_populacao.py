"""Testes de papahr.dashboard.populacao (SIDRA simulado; sem internet)."""
from __future__ import annotations

import polars as pl

from papahr.dashboard import populacao as P

SIDRA_FAKE = [
    {"NC": "Nível Territorial (Código)", "NN": "Nível Territorial", "MC": "Unidade de Medida (Código)",
     "MN": "Unidade de Medida", "V": "Valor", "D1C": "Município (Código)", "D1N": "Município",
     "D2C": "Variável (Código)", "D2N": "Variável", "D3C": "Ano (Código)", "D3N": "Ano"},
    {"NC": "6", "NN": "Município", "MC": "45", "MN": "Pessoas", "V": "2416339", "D1C": "3106200",
     "D1N": "Belo Horizonte (MG)", "D2C": "9324", "D2N": "População residente estimada", "D3C": "2024", "D3N": "2024"},
    {"NC": "6", "NN": "Município", "MC": "45", "MN": "Pessoas", "V": "621000", "D1C": "3118601",
     "D1N": "Contagem (MG)", "D2C": "9324", "D2N": "População residente estimada", "D3C": "2024", "D3N": "2024"},
    {"NC": "6", "NN": "Município", "MC": "45", "MN": "Pessoas", "V": "-", "D1C": "3100000",
     "D1N": "Inválido (MG)", "D2C": "9324", "D2N": "População residente estimada", "D3C": "2024", "D3N": "2024"},
]


def test_sidra_para_df_normaliza_codigo_nome_uf():
    df = P._sidra_para_df(SIDRA_FAKE, "IBGE estimativa")
    assert df.height == 2                     # linha com "-" descartada
    bh = df.filter(pl.col("COD_MUN") == "310620").row(0, named=True)
    assert bh["COD_MUN7"] == "3106200" and bh["NOME"] == "Belo Horizonte" and bh["UF"] == "MG"
    assert bh["POP"] == 2416339.0 and bh["ANO"] == 2024


def test_carregar_usa_cache_depois_do_download(tmp_path, monkeypatch):
    monkeypatch.setenv("GSPHOSP_HOME", str(tmp_path))
    chamadas = []

    def fake_baixar(url, timeout=60):
        chamadas.append(url)
        return SIDRA_FAKE

    monkeypatch.setattr(P, "_baixar_json", fake_baixar)
    df1 = P.carregar_populacao()
    assert df1.height == 2 and df1["FONTE"][0] == "IBGE estimativa 2024"
    assert (tmp_path / "cache" / "populacao_ibge_ultima.parquet").exists()
    df2 = P.carregar_populacao()                 # segunda vez: cache, sem rede
    assert len(chamadas) == 1 and df2.height == 2
    assert "last%201" in chamadas[0]


def test_sem_rede_cai_para_cnes(tmp_path, monkeypatch):
    monkeypatch.setenv("GSPHOSP_HOME", str(tmp_path))

    def falha(url, timeout=60):
        raise OSError("sem internet")

    monkeypatch.setattr(P, "_baixar_json", falha)
    cnes = pl.DataFrame({"COD_MUN": ["310620"], "COD_MUN7": [None], "NOME": ["Belo Horizonte"], "UF": ["MG"],
                         "POP": [2400000.0], "ANO": [None], "FONTE": ["CNES"]},
                        schema=list(P.SCHEMA.items()))
    monkeypatch.setattr(P, "populacao_cnes", lambda: cnes)
    df = P.carregar_populacao()
    assert df["FONTE"][0] == "CNES" and df.height == 1


def test_censo_2022_usa_outra_tabela(tmp_path, monkeypatch):
    monkeypatch.setenv("GSPHOSP_HOME", str(tmp_path))
    urls = []

    def fake(url, timeout=60):
        urls.append(url)
        return [SIDRA_FAKE[0]] + [dict(SIDRA_FAKE[1], D3C="2022", D3N="2022")]

    monkeypatch.setattr(P, "_baixar_json", fake)
    df = P.carregar_populacao(ano=2022)
    assert "/t/4714/" in urls[0] and df["FONTE"][0] == "IBGE Censo 2022" and df["ANO"][0] == 2022


def test_juntar_populacao_preenche_e_descreve():
    mun = pl.DataFrame({"COD_MUN": ["310620", "311860", "314330"], "NOME": ["BH", "Contagem", "Montes Claros"],
                        "POP": [None, None, 410000.0]})
    pop = P._sidra_para_df(SIDRA_FAKE, "IBGE estimativa 2024")
    out = P.juntar_populacao(mun, pop)
    assert out.filter(pl.col("COD_MUN") == "310620")["POP"][0] == 2416339.0
    assert out.filter(pl.col("COD_MUN") == "314330")["POP"][0] == 410000.0   # mantém o que já tinha
    assert P.descrever_fonte(pop).startswith("IBGE estimativa 2024")
