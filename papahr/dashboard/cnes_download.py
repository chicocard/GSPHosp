"""
cnes_download.py — baixa e extrai a "Base de Dados do CNES" do FTP do DATASUS.

O DATASUS publica todo mês ftp://ftp.datasus.gov.br/cnes/BASE_DE_DADOS_CNES_AAAAMM.ZIP
(centenas de MB). Este módulo:

  1. lista as competências disponíveis;
  2. baixa o ZIP escolhido com callback de progresso (para a barra do diálogo);
  3. extrai só as tabelas que o GSPHosp usa (ou todas) para
     <destino>/BASE_DE_DADOS_CNES_AAAAMM/, no mesmo formato que o loader já lê.

Só biblioteca padrão (ftplib, zipfile). Funções puras (parse, seleção de membros)
são testáveis sem rede; a rede fica em `listar_competencias` e `baixar`.

Uso direto:
    from papahr.dashboard.cnes_download import listar_competencias, baixar_e_extrair, pasta_cnes_padrao
    comps = listar_competencias()                     # ['202512', '202511', ...]
    pasta = baixar_e_extrair(comps[0], pasta_cnes_padrao(), progresso=print)
"""
from __future__ import annotations

import ftplib
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Iterable

HOST = "ftp.datasus.gov.br"
PASTA_FTP = "/cnes"
PADRAO_ZIP = re.compile(r"BASE_DE_DADOS_CNES_(\d{6})\.ZIP$", re.IGNORECASE)

# tabelas que o GSPHosp lê (nomes sem a competência e sem extensão)
TABELAS_GSPHOSP = (
    "tbEstabelecimento", "tbMunicipio", "tbLeito", "tbTipoEstabelecimento",
    "tbEstado", "tbNaturezaJuridica",
)

Progresso = Callable[[int, int, str], None]   # (bytes_feitos, bytes_total, mensagem)


def pasta_cnes_padrao() -> Path:
    """Pasta onde o app instalado guarda o CNES: %LOCALAPPDATA%\\GSPHosp\\cnes (ou ~/.gsphosp/cnes)."""
    home = os.environ.get("GSPHOSP_HOME")
    if home:
        return Path(home) / "cnes"
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "GSPHosp" / "cnes"
    return Path.home() / ".gsphosp" / "cnes"


def pastas_cnes_candidatas() -> list[Path]:
    """Onde procurar bases já existentes, na ordem: pasta do app, C:\\DICIONARIO_DADOS."""
    return [pasta_cnes_padrao(), Path("C:/DICIONARIO_DADOS")]


# ---------------------------------------------------------------------------
# funções puras
# ---------------------------------------------------------------------------

def competencias_de(nomes: Iterable[str]) -> list[str]:
    """Extrai 'AAAAMM' dos nomes de arquivo do FTP, do mais recente ao mais antigo."""
    comps = set()
    for n in nomes:
        m = PADRAO_ZIP.search(n.rsplit("/", 1)[-1])
        if m:
            comps.add(m.group(1))
    return sorted(comps, reverse=True)


def nome_zip(competencia: str) -> str:
    if not re.fullmatch(r"\d{6}", competencia):
        raise ValueError(f"competência inválida: {competencia!r} (esperado AAAAMM)")
    return f"BASE_DE_DADOS_CNES_{competencia}.ZIP"


def membros_a_extrair(nomes_no_zip: Iterable[str], competencia: str, tabelas: Iterable[str] | None) -> list[str]:
    """
    Seleciona, entre os arquivos do ZIP, os CSV das tabelas pedidas.
    tabelas=None → todos os .csv. Compara pelo nome-base, sem diferenciar maiúsculas,
    aceitando tanto 'tbEstabelecimento202512.csv' quanto 'tbEstabelecimento.csv'.
    """
    quer = None if tabelas is None else {t.lower() for t in tabelas}
    out = []
    for n in nomes_no_zip:
        base = n.rsplit("/", 1)[-1]
        if not base.lower().endswith(".csv"):
            continue
        if quer is None:
            out.append(n); continue
        stem = base[:-4]
        if stem.lower().endswith(competencia):
            stem = stem[: -len(competencia)]
        if stem.lower() in quer:
            out.append(n)
    return out


def pasta_destino(destino: Path, competencia: str) -> Path:
    return Path(destino) / f"BASE_DE_DADOS_CNES_{competencia}"


def base_ja_existe(destino: Path, competencia: str, tabelas: Iterable[str] = TABELAS_GSPHOSP) -> bool:
    p = pasta_destino(destino, competencia)
    if not p.is_dir():
        return False
    existentes = {f.name.lower() for f in p.glob("*.csv")}
    return all(f"{t}{competencia}.csv".lower() in existentes for t in tabelas)


# ---------------------------------------------------------------------------
# rede
# ---------------------------------------------------------------------------

def _conectar(timeout: float = 60) -> ftplib.FTP:
    ftp = ftplib.FTP(HOST, timeout=timeout)
    ftp.login()                      # anônimo
    ftp.set_pasv(True)
    ftp.cwd(PASTA_FTP)
    return ftp


def listar_competencias(timeout: float = 60) -> list[str]:
    """Competências publicadas no FTP, da mais recente para a mais antiga."""
    ftp = _conectar(timeout)
    try:
        nomes = ftp.nlst()
    finally:
        try:
            ftp.quit()
        except Exception:
            pass
    comps = competencias_de(nomes)
    if not comps:
        raise RuntimeError(f"nenhum BASE_DE_DADOS_CNES_AAAAMM.ZIP encontrado em ftp://{HOST}{PASTA_FTP}/")
    return comps


def baixar(competencia: str, arquivo_destino: Path, progresso: Progresso | None = None,
           cancelado: Callable[[], bool] | None = None, timeout: float = 120) -> Path:
    """Baixa o ZIP da competência para `arquivo_destino` (gravando em .part e renomeando ao fim)."""
    nome = nome_zip(competencia)
    arquivo_destino = Path(arquivo_destino)
    arquivo_destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = arquivo_destino.with_suffix(arquivo_destino.suffix + ".part")
    ftp = _conectar(timeout)
    try:
        ftp.voidcmd("TYPE I")
        try:
            total = ftp.size(nome) or 0
        except ftplib.error_perm:
            total = 0
        feito = 0
        with parcial.open("wb") as f:
            def _bloco(dados: bytes):
                nonlocal feito
                if cancelado and cancelado():
                    raise InterruptedError("download cancelado")
                f.write(dados); feito += len(dados)
                if progresso:
                    progresso(feito, total, f"Baixando {nome}")
            ftp.retrbinary(f"RETR {nome}", _bloco, blocksize=256 * 1024)
    except Exception:
        parcial.unlink(missing_ok=True)
        raise
    finally:
        try:
            ftp.quit()
        except Exception:
            pass
    if total and feito != total:
        parcial.unlink(missing_ok=True)
        raise RuntimeError(f"download incompleto: {feito} de {total} bytes")
    parcial.replace(arquivo_destino)
    return arquivo_destino


def extrair(zip_path: Path, destino: Path, competencia: str, tabelas: Iterable[str] | None = TABELAS_GSPHOSP,
            progresso: Progresso | None = None) -> Path:
    """Extrai as tabelas pedidas do ZIP para <destino>/BASE_DE_DADOS_CNES_<competencia>/ (sem subpastas)."""
    pasta = pasta_destino(destino, competencia)
    pasta.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        membros = membros_a_extrair(z.namelist(), competencia, tabelas)
        if not membros:
            raise RuntimeError("o ZIP não contém as tabelas esperadas (tb*.csv)")
        for i, m in enumerate(membros, 1):
            base = m.rsplit("/", 1)[-1]
            if progresso:
                progresso(i, len(membros), f"Extraindo {base}")
            with z.open(m) as src, (pasta / base).open("wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
    return pasta


def baixar_e_extrair(competencia: str, destino: Path | None = None, tabelas: Iterable[str] | None = TABELAS_GSPHOSP,
                     progresso: Progresso | None = None, cancelado: Callable[[], bool] | None = None,
                     manter_zip: bool = False) -> Path:
    """Fluxo completo. Devolve a pasta BASE_DE_DADOS_CNES_<competencia> pronta para o loader."""
    destino = Path(destino) if destino else pasta_cnes_padrao()
    if base_ja_existe(destino, competencia, tabelas or TABELAS_GSPHOSP):
        if progresso:
            progresso(1, 1, "Base já existente; nada a baixar")
        return pasta_destino(destino, competencia)
    tmpdir = Path(tempfile.mkdtemp(prefix="gsphosp_cnes_"))
    zip_path = tmpdir / nome_zip(competencia)
    try:
        baixar(competencia, zip_path, progresso, cancelado)
        pasta = extrair(zip_path, destino, competencia, tabelas, progresso)
    finally:
        if not manter_zip:
            zip_path.unlink(missing_ok=True)
            try:
                tmpdir.rmdir()
            except OSError:
                pass
    if progresso:
        progresso(1, 1, f"Pronto: {pasta}")
    return pasta
