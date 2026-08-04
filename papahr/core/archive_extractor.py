"""Extrator de arquivos compactados (zip, tar, rar, 7z) para uso no Wizard Passo 1."""
from __future__ import annotations

import shutil
import tarfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from papahr.logging_config import get_logger

log = get_logger(__name__)

ARCHIVE_SUFFIXES = {
    ".zip",
    ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
    ".7z", ".rar",
}


@dataclass(slots=True)
class ExtractionResult:
    """Resultado de extração de arquivos compactados."""
    root_dir: Path
    extracted_files: list[Path]
    n_archives: int
    total_bytes: int
    warnings: list[str]


def _is_archive(p: Path) -> bool:
    """Verifica se um arquivo é um arquivo compactado conhecido."""
    if not p.is_file():
        return False
    name = p.name.lower()
    for suf in sorted(ARCHIVE_SUFFIXES, key=len, reverse=True):
        if name.endswith(suf):
            return True
    return False


def _find_archives(folder: Path) -> list[Path]:
    """Retorna todos arquivos compactados encontrados na pasta (recursivo)."""
    folder = Path(folder)
    if not folder.exists():
        return []
    return sorted([p for p in folder.rglob("*") if _is_archive(p)])


def _find_files_by_suffix(root: Path, suffixes: set[str]) -> list[Path]:
    """Encontra arquivos de interesse em uma árvore de diretórios."""
    return sorted([
        p for p in root.rglob("*")
        if p.is_file() and any(p.name.lower().endswith(s) for s in suffixes)
    ])


def _extract_zip(archive: Path, dest: Path) -> list[str]:
    """Extrai ZIP; retorna lista de avisos."""
    warns: list[str] = []
    try:
        with zipfile.ZipFile(archive, "r") as zf:
            # Segurança: impede zip-slip (não extrai para fora do dest)
            for member in zf.namelist():
                abs_target = (dest / member).resolve()
                if not str(abs_target).startswith(str(dest.resolve()) + "\\") and \
                   not str(abs_target) == str(dest.resolve()):
                    warns.append(f"[zip-slip evitado] {member}")
                    continue
            zf.extractall(dest)
    except zipfile.BadZipFile as e:
        warns.append(f"ZIP corrompido ignorado: {archive.name} ({e})")
    return warns


def _extract_tar(archive: Path, dest: Path) -> list[str]:
    """Extrai TAR / TAR.GZ / TAR.BZ2 / TAR.XZ; retorna avisos."""
    warns: list[str] = []
    try:
        with tarfile.open(archive, "r:*") as tf:
            safe_members = []
            for m in tf.getmembers():
                target = (dest / m.name).resolve()
                if str(target).startswith(str(dest.resolve()) + "\\") or \
                   str(target) == str(dest.resolve()):
                    safe_members.append(m)
                else:
                    warns.append(f"[tar-slip evitado] {m.name}")
            tf.extractall(dest, members=safe_members)
    except tarfile.TarError as e:
        warns.append(f"TAR corrompido ignorado: {archive.name} ({e})")
    return warns


def _extract_7z(archive: Path, dest: Path) -> list[str]:
    """Extrai 7z (tenta py7zr; avisa se biblioteca ausente)."""
    warns: list[str] = []
    try:
        import py7zr  # type: ignore
    except ModuleNotFoundError:
        warns.append("py7zr não instalado. Instale: pip install py7zr (suporte .7z).")
        return warns
    try:
        with py7zr.SevenZipFile(archive, mode="r") as sz:
            sz.extractall(path=dest)
    except Exception as e:  # noqa: BLE001
        warns.append(f"7z corrompido ignorado: {archive.name} ({e})")
    return warns


def _extract_rar(archive: Path, dest: Path) -> list[str]:
    """Extrai RAR (tenta rarfile / pyunpack; avisa se bibliotecas ausentes)."""
    warns: list[str] = []
    # 1. rarfile (requer unrar.dll/unrar no PATH)
    try:
        import rarfile  # type: ignore
    except ModuleNotFoundError:
        pass
    else:
        try:
            with rarfile.RarFile(archive) as rf:
                rf.extractall(dest)
            return warns
        except Exception as e:  # noqa: BLE001
            warns.append(f"rarfile falhou: {e}")
    # 2. pyunpack (funciona como wrapper genérico)
    try:
        from pyunpack import Archive  # type: ignore
    except ModuleNotFoundError:
        warns.append(
            "rarfile/pyunpack não instalados. Instale: pip install rarfile pyunpack patool "
            "e tenha o UnRAR.exe no PATH do Windows (suporte .rar)."
        )
        return warns
    try:
        Archive(str(archive)).extractall(str(dest), auto_create_dir=True)
    except Exception as e:  # noqa: BLE001
        warns.append(f"RAR corrompido / binário unrar ausente: {archive.name} ({e})")
    return warns


def extract_all_archives(
    source_dir: Path,
    dest_dir: Optional[Path] = None,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> ExtractionResult:
    """Extrai TODOS os arquivos compactados de `source_dir` para `dest_dir`.

    Args:
        source_dir: Pasta Windows do usuário onde ele colocou .zip/.rar/.7z etc
        dest_dir: Onde extrair; se None, cria subpasta timestamp em cache.
        progress_cb: Callback (arquivo_atual, total, mensagem) opcional.

    Returns:
        ExtractionResult com (root_dir, arquivos extraídos, n_arqs, total bytes, avisos).
    """
    from papahr.config import get_settings
    source_dir = Path(source_dir)
    if dest_dir is None:
        s = get_settings()
        s.ensure_dirs()
        dest_dir = s.cache_dir / "extracted" / f"archive_{int(time.time())}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    archives = _find_archives(source_dir)
    if not archives:
        return ExtractionResult(
            root_dir=dest_dir, extracted_files=[], n_archives=0, total_bytes=0,
            warnings=[
                "Nenhum arquivo compactado encontrado na pasta. "
                "Tipos aceitos: .zip, .tar, .tar.gz, .tgz, .tar.bz2, .tar.xz, .7z, .rar."
            ],
        )

    total = len(archives)
    warns: list[str] = []
    all_extracted: list[Path] = []
    total_bytes = 0

    for i, arc in enumerate(archives, 1):
        if progress_cb:
            progress_cb(i, total, f"Extraindo {arc.name} ...")
        log.info("extraindo arquivo compactado", n=i, total=total, name=arc.name)
        nm = arc.name.lower()
        if nm.endswith(".zip"):
            w = _extract_zip(arc, dest_dir)
        elif any(nm.endswith(s) for s in (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
            w = _extract_tar(arc, dest_dir)
        elif nm.endswith(".7z"):
            w = _extract_7z(arc, dest_dir)
        elif nm.endswith(".rar"):
            w = _extract_rar(arc, dest_dir)
        else:
            w = [f"Tipo desconhecido, ignorado: {arc.name}"]
        warns.extend(w)
        try:
            total_bytes += arc.stat().st_size
        except OSError:
            pass

    # Depois de extrair tudo, acha arquivos de interesse
    dbc_csv_xlsx = (
        _find_files_by_suffix(dest_dir, {".dbc"})
        + _find_files_by_suffix(dest_dir, {".csv"})
        + _find_files_by_suffix(dest_dir, {".xlsx"})
    )
    all_extracted = dbc_csv_xlsx

    if progress_cb:
        progress_cb(total, total, "Extração concluída")

    # Se houver um diretório-filho único dentro de dest_dir com tudo, sobe um nível
    # (muitos zips baixados compactam uma pasta com nome "Dados_2025/" e tudo dentro)
    while True:
        filhos = [p for p in dest_dir.iterdir()]
        dirs = [p for p in filhos if p.is_dir()]
        arquivos = [p for p in filhos if p.is_file()]
        if len(dirs) == 1 and len(arquivos) == 0:
            filho_unico = dirs[0]
            # Move tudo para cima
            for item in filho_unico.iterdir():
                try:
                    shutil.move(str(item), str(dest_dir / item.name))
                except shutil.Error:
                    # Destino já existe, deixa quieto
                    pass
            try:
                filho_unico.rmdir()
            except OSError:
                break
        else:
            break

    log.info("extracao finalizada", n_archives=total, bytes=total_bytes, files=len(all_extracted))
    return ExtractionResult(
        root_dir=dest_dir,
        extracted_files=all_extracted,
        n_archives=total,
        total_bytes=total_bytes,
        warnings=warns,
    )


def find_data_folders(root: Path) -> dict[str, Optional[Path]]:
    """Analisa o diretório extraído e retorna pastas dos dados:
    - dbc: pasta com *.dbc OU *.dbf para LocalPipeline (dbf = já descompactado)
    - cnes: pasta com tbEstabelecimento*.csv etc (CNES)
    """
    root = Path(root)
    result: dict[str, Optional[Path]] = {"dbc": None, "cnes": None}

    # DBC/DBF: pasta com maior quantidade de arquivos *.dbc + *.dbf
    data_candidates: dict[Path, int] = {}
    for p in list(root.rglob("*.dbc")) + list(root.rglob("*.dbf")):
        data_candidates[p.parent] = data_candidates.get(p.parent, 0) + 1
    if data_candidates:
        # Pega pasta com mais dados (normalmente a raiz ou uma subpasta Dados/)
        result["dbc"] = max(data_candidates.items(), key=lambda kv: kv[1])[0]

    # CNES: detecta tbEstabelecimento*, tbMunicipio* etc
    for pattern in ("tbEstabelecimento*.csv", "tbMunicipio*.csv"):
        for p in root.rglob(pattern):
            result["cnes"] = p.parent
            break
        if result["cnes"] is not None:
            break
    return result
