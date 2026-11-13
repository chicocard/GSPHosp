"""Testes da tabela de regiões de saúde e do seletor em cascata (Qt offscreen)."""
from __future__ import annotations

import os

import polars as pl

from papahr.dashboard import regioes_saude as R


def test_tabela_embarcada_completa():
    reg = R.carregar_regioes_saude()
    assert reg.height >= 5500 and reg["CO_REGSAUD"].n_unique() == 450
    assert R.regioes_da_uf("MG").height == 89 and R.regioes_da_uf("31").height == 89
    bh = reg.filter(pl.col("COD_MUN") == "310620").row(0, named=True)
    assert bh["CO_REGSAUD"] == "31016" and bh["NO_REGSAUD"].startswith("Belo Horizonte")
    assert "310620" in R.municipios_da_regiao("31016")["COD_MUN"].to_list()


def test_juntar_regiao_preserva_colunas():
    mun = pl.DataFrame({"COD_MUN": ["3106200", "311860", "999999"], "NOME": ["BH", "Contagem", "X"], "POP": [1.0, 2.0, 3.0]})
    out = R.juntar_regiao(mun)
    assert out.columns[:3] == ["COD_MUN", "NOME", "POP"] and out.height == 3
    assert out.filter(pl.col("COD_MUN") == "310620")["NO_REGSAUD"][0].startswith("Belo Horizonte")
    assert out.filter(pl.col("COD_MUN") == "999999")["CO_REGSAUD"][0] is None


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_seletor_cascata_e_busca():
    _app()
    from papahr.ui.atratividade.seletor import SeletorHospital, TODAS_REG, TODOS_MUN

    hosp = pl.DataFrame({
        "CNES": ["0026921", "0027014", "2126303", "2144228", "9999999"],
        "NOME": ["Hospital A", "Hospital B", "Hospital C", "Hospital D", "Hospital SP"],
        "COD_MUN": ["310620", "310620", "311860", "314330", "355030"],
        "n": [1000, 500, 300, 200, 50],
        "LAT": [-19.9, -19.9, -19.9, -16.6, -23.5], "LON": [-43.9, -43.9, -44.0, -43.9, -46.6],
    })
    mun = pl.DataFrame({"COD_MUN": ["310620", "311860", "314330", "355030"], "NOME": ["Belo Horizonte", "Contagem", "Montes Claros", "São Paulo"]})
    sel = SeletorHospital()
    recebidos: list[str] = []
    sel.hospitalMudou.connect(recebidos.append)
    sel.carregar(hosp, mun)
    # padrão: hospital de maior volume (A, em BH), cascata posicionada
    assert sel.cnes() == "0026921" and sel.uf() == "MG"
    assert sel.regiao()[0] == "31016" and sel.cmb_mun.currentData() == "310620"
    assert sel.cmb_hosp.count() == 2                       # A e B em BH
    assert [sel.cmb_uf.itemData(i) for i in range(sel.cmb_uf.count())] == ["MG", "SP"]
    assert sel.cmb_reg.count() == 4                        # (todas) + BH, Contagem, Montes Claros
    assert sel.cmb_mun.itemData(0) == TODOS_MUN
    # "(todas as regiões)" → todos os hospitais da UF na caixa Hospital, maior volume primeiro
    sel.cmb_reg.setCurrentIndex(sel.cmb_reg.findData(TODAS_REG))
    assert sel.cmb_hosp.count() == 4 and sel.cnes() == "0026921"
    assert sel.regiao()[0] == "31016"                      # região do hospital escolhido
    sel.cmb_hosp.setCurrentIndex(sel.cmb_hosp.findData("2144228"))
    assert sel.regiao()[0] == "31084" and "314330" in sel.municipios_da_regiao()
    # região específica → só os municípios dela
    sel.cmb_reg.setCurrentIndex(sel.cmb_reg.findData("31016"))
    assert sel.cmb_mun.count() == 2 and sel.cmb_hosp.count() == 2
    # mudar de UF → SP
    sel.cmb_uf.setCurrentIndex(sel.cmb_uf.findData("SP"))
    assert sel.cnes() == "9999999" and recebidos[-1] == "9999999"
    # selecionar direto um CNES reposiciona tudo
    sel.selecionar("2144228")
    assert sel.cnes() == "2144228" and sel.uf() == "MG" and sel.regiao()[0] == "31084"
    assert "314330" in sel.municipios_da_regiao()
    # busca livre
    sel.ed_busca.setText("hospital c")
    assert sel.cmb_hosp.count() == 1 and sel.cnes() == "2126303"
    sel.ed_busca.clear()
    assert sel.cnes() == "2144228"                         # volta à cascata (Montes Claros)


def test_seletor_permite_nenhum():
    _app()
    from papahr.ui.atratividade.seletor import SeletorHospital

    hosp = pl.DataFrame({"CNES": ["0026921"], "NOME": ["Hospital A"], "COD_MUN": ["310620"], "n": [10], "LAT": [-19.9], "LON": [-43.9]})
    sel = SeletorHospital(permitir_nenhum=True)
    sel.carregar(hosp, pl.DataFrame({"COD_MUN": ["310620"], "NOME": ["Belo Horizonte"]}))
    assert sel.cnes() == "0026921" and sel.cmb_hosp.itemText(0) == "(nenhum)"
    sel.cmb_hosp.setCurrentIndex(0)
    assert sel.cnes() is None and sel.hospital() is None
