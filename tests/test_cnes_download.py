"""Testes do download do CNES — partes puras e extração, sem rede."""
import io
import zipfile
from pathlib import Path

import pytest

from papahr.dashboard import cnes_download as cd


def test_competencias_de_nomes_do_ftp():
    nomes = ["BASE_DE_DADOS_CNES_202511.ZIP", "/cnes/BASE_DE_DADOS_CNES_202512.ZIP", "base_de_dados_cnes_202510.zip",
             "LEIAME.TXT", "BASE_DE_DADOS_CNES_2025.ZIP"]
    assert cd.competencias_de(nomes) == ["202512", "202511", "202510"]


def test_nome_zip_valida():
    assert cd.nome_zip("202512") == "BASE_DE_DADOS_CNES_202512.ZIP"
    with pytest.raises(ValueError):
        cd.nome_zip("2025-12")


def test_membros_a_extrair_seleciona_tabelas():
    nomes = ["tbEstabelecimento202512.csv", "tbMunicipio202512.csv", "rlEstabEquipe202512.csv", "LEIAME.txt",
             "sub/tbLeito202512.csv", "tbEstado202512.CSV"]
    sel = cd.membros_a_extrair(nomes, "202512", cd.TABELAS_GSPHOSP)
    assert set(sel) == {"tbEstabelecimento202512.csv", "tbMunicipio202512.csv", "sub/tbLeito202512.csv", "tbEstado202512.CSV"}
    assert len(cd.membros_a_extrair(nomes, "202512", None)) == 5


def _zip_sintetico(tmp_path: Path, comp: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / f"BASE_DE_DADOS_CNES_{comp}.ZIP"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        for t in cd.TABELAS_GSPHOSP:
            z.writestr(f"{t}{comp}.csv", "CO_X;NO_Y\n1;a\n")
        z.writestr(f"rlEstabEquipe{comp}.csv", "x\n")
        z.writestr("LEIAME.txt", "doc")
    return p


def test_extrair_e_base_ja_existe(tmp_path):
    comp = "202512"
    zp = _zip_sintetico(tmp_path, comp)
    destino = tmp_path / "cnes"
    msgs = []
    pasta = cd.extrair(zp, destino, comp, progresso=lambda a, b, m: msgs.append(m))
    assert pasta == destino / f"BASE_DE_DADOS_CNES_{comp}"
    assert {f.name for f in pasta.glob("*.csv")} == {f"{t}{comp}.csv" for t in cd.TABELAS_GSPHOSP}
    assert not (pasta / f"rlEstabEquipe{comp}.csv").exists()
    assert msgs and msgs[0].startswith("Extraindo")
    assert cd.base_ja_existe(destino, comp)
    assert not cd.base_ja_existe(destino, "202401")


def test_baixar_e_extrair_curto_circuito_quando_existe(tmp_path, monkeypatch):
    comp = "202512"
    destino = tmp_path / "cnes"
    cd.extrair(_zip_sintetico(tmp_path, comp), destino, comp)
    monkeypatch.setattr(cd, "baixar", lambda *a, **k: pytest.fail("não deveria baixar"))
    assert cd.baixar_e_extrair(comp, destino) == destino / f"BASE_DE_DADOS_CNES_{comp}"


def test_baixar_e_extrair_com_download_falso(tmp_path, monkeypatch):
    comp = "202506"
    destino = tmp_path / "cnes"
    origem = _zip_sintetico(tmp_path / "src", comp)

    def baixar_falso(competencia, arquivo_destino, progresso=None, cancelado=None, timeout=120):
        Path(arquivo_destino).write_bytes(origem.read_bytes())
        if progresso:
            progresso(10, 10, "Baixando")
        return Path(arquivo_destino)

    monkeypatch.setattr(cd, "baixar", baixar_falso)
    pasta = cd.baixar_e_extrair(comp, destino, progresso=lambda *a: None)
    assert cd.base_ja_existe(destino, comp)
    assert (pasta / f"tbEstabelecimento{comp}.csv").read_text().startswith("CO_X")


def test_pasta_cnes_padrao_respeita_gsphosp_home(monkeypatch, tmp_path):
    monkeypatch.setenv("GSPHOSP_HOME", str(tmp_path))
    assert cd.pasta_cnes_padrao() == tmp_path / "cnes"
