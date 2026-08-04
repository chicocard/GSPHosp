"""Cliente FTP assíncrono com retry e caching."""
from __future__ import annotations

import asyncio
import ftplib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from papahr.config import get_settings
from papahr.logging_config import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class RemoteFile:
    name: str
    host: str
    path: str
    url: str
    size: int = 0

    @classmethod
    def from_ftp(cls, host: str, base_path: str, name: str) -> "RemoteFile":
        path = base_path.rstrip("/") + "/" + name
        return cls(name=name, host=host, path=path, url=f"ftp://{host}{path}")


class FTPClient:
    """Cliente FTP com retry, caching e listagem de diretórios."""

    def __init__(self) -> None:
        self.settings = get_settings()

    # ------------------------------------------------------------------ sync
    def list_dir_ftp(self, host: str, remote_dir: str) -> list[str]:
        """Lista arquivos via FTP (síncrono, fallback robusto)."""
        remote_dir = remote_dir.rstrip("/") + "/"
        last_err: Exception | None = None
        for attempt in range(self.settings.ftp_max_retries):
            try:
                with ftplib.FTP(host, timeout=self.settings.ftp_timeout) as ftp:
                    ftp.login()
                    try:
                        ftp.cwd(remote_dir)
                    except ftplib.error_perm:
                        # Tenta criação de diretório inválido; lista via nlst direto
                        pass
                    files: list[str] = []
                    try:
                        ftp.retrlines(f"NLST {remote_dir}", files.append)
                    except ftplib.error_perm:
                        ftp.retrlines("NLST", files.append)
                    return [f.split()[-1] for f in files if f.strip()]
            except Exception as e:  # noqa: PERF203
                last_err = e
                log.warn("list_dir_ftp falhou tentativa", attempt=attempt + 1, err=str(e))
                asyncio.run(asyncio.sleep(self.settings.ftp_retry_delay))
        raise RuntimeError(f"list_dir_ftp falhou para ftp://{host}{remote_dir}") from last_err

    def download_ftp(self, url: str, dest: Path, *, overwrite: bool = False) -> Path:
        """Baixa arquivo via FTP síncrono."""
        dest = Path(dest)
        if dest.exists() and not overwrite and dest.stat().st_size > 0:
            log.debug("Arquivo ja existe, pulando", dest=dest.name)
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        parsed = urlparse(url)
        host = parsed.hostname or self.settings.datasus_ftp_host
        path = parsed.path
        last_err: Exception | None = None
        for attempt in range(self.settings.ftp_max_retries):
            try:
                with ftplib.FTP(host, timeout=self.settings.ftp_timeout) as ftp:
                    ftp.login()
                    with open(dest, "wb") as f:
                        ftp.retrbinary(f"RETR {path}", f.write, blocksize=65536)
                return dest
            except Exception as e:  # noqa: PERF203
                last_err = e
                log.warn("download_ftp falhou", attempt=attempt + 1, url=url, err=str(e))
                import time
                time.sleep(self.settings.ftp_retry_delay)
        raise RuntimeError(f"download_ftp falhou: {url}") from last_err

    def download_https(self, url: str, dest: Path, *, overwrite: bool = False) -> Path:
        """Baixa arquivo via HTTPS (fallback para FTP)."""
        dest = Path(dest)
        if dest.exists() and not overwrite and dest.stat().st_size > 0:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        last_err: Exception | None = None
        for attempt in range(self.settings.http_max_retries):
            try:
                with httpx.stream(
                    "GET", url, timeout=self.settings.http_timeout, follow_redirects=True
                ) as r:
                    r.raise_for_status()
                    with open(dest, "wb") as f:
                        for chunk in r.iter_bytes(chunk_size=65536):
                            f.write(chunk)
                return dest
            except Exception as e:  # noqa: PERF203
                last_err = e
                log.warn("download_https falhou", attempt=attempt + 1, url=url, err=str(e))
                import time
                time.sleep(self.settings.ftp_retry_delay)
        raise RuntimeError(f"download_https falhou: {url}") from last_err

    # ----------------------------------------------------------------- async
    async def list_dir_async(self, host: str, remote_dir: str) -> list[str]:
        """Lista arquivos via aioftp."""
        import aioftp
        remote_dir = remote_dir.rstrip("/") + "/"
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.settings.ftp_max_retries),
            wait=wait_exponential(multiplier=1, min=self.settings.ftp_retry_delay, max=10),
            reraise=True,
        ):
            async with attempt:
                client = aioftp.Client(timeout=self.settings.ftp_timeout, socket_timeout=self.settings.ftp_timeout)
                async with client.context(host):
                    try:
                        await client.change_directory(remote_dir)
                    except Exception:
                        pass
                    try:
                        entries = await client.list(remote_dir)
                    except Exception:
                        entries = await client.list()
                    return [str(p.name) for p, _ in entries]
        return []  # unreachable

    async def download_async(
        self,
        url: str,
        dest: Path,
        *,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        overwrite: bool = False,
    ) -> Path:
        """Download assíncrono via HTTP/FTP com barra de progresso."""
        dest = Path(dest)
        if dest.exists() and not overwrite and dest.stat().st_size > 0:
            if progress_cb:
                progress_cb(1, 1)
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        if url.startswith("http"):
            async with httpx.AsyncClient(timeout=self.settings.http_timeout) as client:
                async with client.stream("GET", url, follow_redirects=True) as r:
                    r.raise_for_status()
                    total = int(r.headers.get("Content-Length", 0))
                    downloaded = 0
                    with open(dest, "wb") as f:
                        async for chunk in r.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_cb:
                                progress_cb(downloaded, total or downloaded)
            return dest
        # FTP
        import aioftp
        parsed = urlparse(url)
        host = parsed.hostname or self.settings.datasus_ftp_host
        path = parsed.path
        async with aioftp.Client.context(host, timeout=self.settings.ftp_timeout) as client:
            try:
                size = await client.stat(path)
                total = int(size.get("size", 0)) if isinstance(size, dict) else 0
            except Exception:
                total = 0
            downloaded = 0
            async with client.download_stream(path) as stream:
                with open(dest, "wb") as f:
                    async for block in stream.iter_by_block(65536):
                        f.write(block)
                        downloaded += len(block)
                        if progress_cb:
                            progress_cb(downloaded, total or downloaded)
        return dest

    async def download_many(
        self,
        urls: Iterable[tuple[str, Path]],
        *,
        concurrency: int = 4,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> list[Path]:
        """Baixa múltiplos arquivos com concorrência limitada."""
        urls = list(urls)
        sem = asyncio.Semaphore(concurrency)
        done = 0
        total = len(urls)

        async def _one(url: str, dest: Path) -> Path:
            nonlocal done
            async with sem:
                try:
                    return await self.download_async(url, dest)
                finally:
                    done += 1
                    if progress_cb:
                        progress_cb(done, total)

        tasks = [asyncio.create_task(_one(u, d)) for u, d in urls]
        return await asyncio.gather(*tasks)


# URLs base DATASUS
BASE_SIASIH = "ftp://ftp.datasus.gov.br/dissemin/publicos/{src}SUS/200801_/Dados/"
BASE_CNES = "ftp://ftp.datasus.gov.br/dissemin/publicos/CNES/200508_/Dados/{tipo}/"
BASE_SIGTAP = "ftp://ftp2.datasus.gov.br/pub/sistemas/tup/downloads/"
