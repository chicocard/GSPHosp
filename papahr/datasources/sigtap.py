"""Download e processamento do SIGTAP (Tabela Unificada de Procedimentos)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Literal

import polars as pl

from papahr.config import get_settings
from papahr.core.ftp_client import FTPClient
from papahr.core.utils import ensure_dir, ymd
from papahr.logging_config import get_logger

log = get_logger(__name__)

SIGTAP_DETAIL_NAMES = Literal[
    "Procedimento", "Grupo", "Subgrupo", "Forma de organização",
    "Financiamento", "Rubrica", "CBO", "CID",
]

_FILE_MAP: dict[SIGTAP_DETAIL_NAMES, tuple[str, str]] = {
    "Procedimento": ("tb_procedimento.txt", "tb_procedimento_layout.txt"),
    "Grupo": ("tb_grupo.txt", "tb_grupo_layout.txt"),
    "Subgrupo": ("tb_sub_grupo.txt", "tb_sub_grupo_layout.txt"),
    "Forma de organização": ("tb_forma_organizacao.txt", "tb_forma_organizacao_layout.txt"),
    "Financiamento": ("tb_financiamento.txt", "tb_financiamento_layout.txt"),
    "Rubrica": ("tb_rubrica.txt", "tb_rubrica_layout.txt"),
    "CBO": ("tb_ocupacao.txt", "tb_ocupacao_layout.txt"),
    "CID": ("tb_cid.txt", "tb_cid_layout.txt"),
}

_ZIP_RE = re.compile(r"^TabelaUnificada_(?P<versao>\d+)_(?P<year>\d{4})(?P<month>\d{2})\.zip$", re.I)


@dataclass(slots=True)
class ProcedureDetails:
    df: pl.DataFrame

    def join_keys(self, left: pl.DataFrame, proc_col: str, ym_col: str) -> pl.DataFrame:
        """Join conveniente da tabela de procedimentos."""
        return left.join(
            self.df,
            left_on=[proc_col, ym_col],
            right_on=["CO_PROCEDIMENTO", "file_version_id"],
            how="left",
        )


class SIGTAPRepository:
    """Baixa, descompacta e processa arquivos do SIGTAP."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.ftp = FTPClient()
        self.cache_dir: Path = ensure_dir(self.settings.cache_dir / "SIGTAP")

    # --------------------------------------------------------------------
    def _list_remote(self) -> list[tuple[str, str, date]]:
        files = self.ftp.list_dir_ftp(
            self.settings.datasus_ftp_host2, "/pub/sistemas/tup/downloads/"
        )
        out: list[tuple[str, str, date]] = []
        for f in files:
            m = _ZIP_RE.match(f)
            if not m:
                continue
            try:
                d = ymd(f"{m.group('year')}{m.group('month')}")
                version_id = f"{m.group('year')}{m.group('month')}"
                out.append((f, version_id, d))
            except Exception:  # noqa: PERF203
                continue
        return out

    def download(
        self,
        *,
        year_start: int | None = None,
        month_start: int | None = None,
        year_end: int | None = None,
        month_end: int | None = None,
        specific_dates: list[str] | None = None,
        newer: bool = False,
        prefer_github_mirror: bool = True,
    ) -> list[str]:
        """Baixa arquivos do SIGTAP e retorna os version_ids baixados.

        Strategy:
          1. Se prefer_github_mirror=True, tenta o mirror GitHub RenatoKR/SIGTAP
             (faster e resiliente a indisponibilidades do FTP DATASUS).
          2. Fallback para o FTP oficial do DATASUS.
        """
        version_ids_wanted: list[str] = []
        if specific_dates:
            version_ids_wanted = list(dict.fromkeys(specific_dates))
        elif newer:
            # Pega o mais recente do FTP (lista remoto primeiro)
            ftp_versions = [v for (_n, v, _d) in self._list_remote()]
            if ftp_versions:
                version_ids_wanted = [ftp_versions[-1]]
        elif year_start and month_start and year_end and month_end:
            from papahr.core import year_month_iter
            version_ids_wanted = [f"{y:04d}{m:02d}" for y, m in
                                  year_month_iter(year_start, month_start, year_end, month_end)]

        downloaded: list[str] = []
        # 1. Tenta GitHub mirror
        if prefer_github_mirror and version_ids_wanted:
            for v in version_ids_wanted:
                if self._download_github_mirror(v):
                    downloaded.append(v)

        # 2. Pega as restantes pelo FTP oficial
        remaining = [v for v in version_ids_wanted if v not in downloaded]
        if remaining:
            files = self._list_remote()
            files = [f for f in files if f[1] in remaining]
            for fname, version_id, _d in files:
                if self._download_ftp(fname, version_id):
                    downloaded.append(version_id)
        # Limpeza
        if year_start and month_start and year_end and month_end:
            self._cleanup_old(date(year_start, month_start, 1), date(year_end, month_end, 1))
        return downloaded

    # --------------------------------------------------------------------
    GITHUB_RAW = (
        "https://raw.githubusercontent.com/RenatoKR/SIGTAP/main/"
        "TabelaUnificada_{version_id}.zip"
    )

    def _download_github_mirror(self, version_id: str) -> bool:
        """Faz download do ZIP SIGTAP via mirror público GitHub RenatoKR."""
        import httpx
        dest_dir = ensure_dir(self.cache_dir / version_id)
        if any(dest_dir.glob("tb_*.txt")):
            return True  # já foi
        url = self.GITHUB_RAW.format(version_id=version_id)
        zip_path = dest_dir / f"TabelaUnificada_{version_id}.zip"
        try:
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    log.debug("[SIGTAP] GitHub mirror não tem", version=version_id,
                              status=resp.status_code)
                    return False
                zip_path.write_bytes(resp.content)
            self._unzip(zip_path, dest_dir)
            try:
                zip_path.unlink()
            except Exception:
                pass
            log.info("[SIGTAP] Mirror GitHub OK", version=version_id)
            return True
        except Exception as e:  # noqa: BLE001
            log.debug("[SIGTAP] Mirror GitHub falhou", version=version_id, err=str(e))
            return False

    def _download_ftp(self, fname: str, version_id: str) -> bool:
        dest_dir = ensure_dir(self.cache_dir / version_id)
        if any(dest_dir.glob("tb_*.txt")):
            return True
        url = f"ftp://{self.settings.datasus_ftp_host2}/pub/sistemas/tup/downloads/{fname}"
        zip_path = dest_dir / fname
        try:
            self.ftp.download_ftp(url, zip_path)
            self._unzip(zip_path, dest_dir)
            try:
                zip_path.unlink()
            except Exception:
                pass
            return True
        except Exception as e:  # noqa: BLE001
            log.warn("Falhou SIGTAP download", url=url, err=str(e))
            return False

    def _unzip(self, zip_path: Path, dest: Path) -> None:
        import zipfile
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(dest)

    def _cleanup_old(self, start: date, end: date) -> None:
        if not self.cache_dir.exists():
            return
        for d in self.cache_dir.iterdir():
            if not d.is_dir():
                continue
            try:
                dt = ymd(d.name)
            except Exception:
                continue
            if not (start <= dt <= end):
                import shutil
                shutil.rmtree(d, ignore_errors=True)

    # --------------------------------------------------------------------
    def _parse_fixed_width(self, version_id: str, detail: SIGTAP_DETAIL_NAMES) -> pl.DataFrame:
        """Lê um arquivo TXT de layout fixo do SIGTAP usando seu layout."""
        raw_name, layout_name = _FILE_MAP[detail]
        # Encontra os arquivos em qualquer subdiretório (versão)
        raw_files = sorted(self.cache_dir.glob(f"**/{raw_name}"))
        layout_files = sorted(self.cache_dir.glob(f"**/{layout_name}"))
        if not raw_files or not layout_files:
            log.warn("Arquivos SIGTAP ausentes para", detail=detail, version=version_id)
            return pl.DataFrame()
        # Monta layout a partir do primeiro layout file encontrado
        layouts: list[dict[str, int | str]] = []
        # usa o layout correspondente ao version_id se houver
        layout_candidates = [
            lf for lf in layout_files if lf.parent.name == version_id
        ] or layout_files
        with open(layout_candidates[0], encoding="latin-1", errors="replace") as f:
            lines = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("Coluna")]
        for ln in lines:
            parts = [p.strip() for p in ln.split(",")]
            if len(parts) < 5:
                continue
            try:
                layouts.append(
                    {
                        "column_name": parts[0],
                        "size": int(parts[1]),
                        "start": int(parts[2]),
                        "end": int(parts[3]),
                        "type": parts[4],
                    }
                )
            except Exception:  # noqa: PERF203
                continue
        if not layouts:
            return pl.DataFrame()
        # Lê os arquivos raw correspondentes ao version_id
        raw_candidates = [rf for rf in raw_files if rf.parent.name == version_id] or raw_files
        all_rows: list[dict[str, str]] = []
        for rf in raw_candidates:
            with open(rf, encoding="latin-1", errors="replace") as f:
                for line in f:
                    if not line.strip():
                        continue
                    row = {"file_version_id": rf.parent.name}
                    for lyt in layouts:
                        s = int(lyt["start"]) - 1
                        e = int(lyt["end"])
                        row[str(lyt["column_name"])] = line[s:e].strip()
                    all_rows.append(row)
        return pl.DataFrame(all_rows) if all_rows else pl.DataFrame()

    def get_detail(self, detail: SIGTAP_DETAIL_NAMES) -> pl.DataFrame:
        """Retorna o detalhe consolidado de todas as versões baixadas."""
        versions = sorted([p.name for p in self.cache_dir.iterdir() if p.is_dir()])
        frames = []
        for v in versions:
            try:
                df = self._parse_fixed_width(v, detail)
                if df.height > 0:
                    frames.append(df)
            except Exception as e:  # noqa: PERF203
                log.warn("Falhou parse SIGTAP detail", detail=detail, version=v, err=str(e))
        if not frames:
            log.warn("Nenhum dado SIGTAP carregado para", detail=detail)
            return pl.DataFrame()
        return pl.concat(frames, how="diagonal_relaxed")

    def get_procedure_details(self) -> ProcedureDetails:
        """Consolida todos os detalhes SIGTAP em uma tabela única."""
        proc = self.get_detail("Procedimento")
        grupo = self.get_detail("Grupo")
        sub = self.get_detail("Subgrupo")
        forma = self.get_detail("Forma de organização")
        fin = self.get_detail("Financiamento")
        rub = self.get_detail("Rubrica")
        if proc.is_empty():
            return ProcedureDetails(df=pl.DataFrame())

        def _sel(df: pl.DataFrame, *cols: str) -> pl.DataFrame:
            return df.select([c for c in cols if c in df.columns])

        if not grupo.is_empty():
            proc = proc.join(
                _sel(grupo, "file_version_id", "CO_GRUPO", "NO_GRUPO"),
                on=["file_version_id", "CO_GRUPO"] if "CO_GRUPO" in proc.columns else ["file_version_id"],
                how="left",
            )
        if not sub.is_empty():
            proc = proc.join(
                _sel(sub, "file_version_id", "CO_GRUPO", "CO_SUB_GRUPO", "NO_SUB_GRUPO"),
                on=[c for c in ("file_version_id", "CO_GRUPO", "CO_SUB_GRUPO") if c in proc.columns],
                how="left",
            )
        if not forma.is_empty():
            proc = proc.join(
                _sel(forma, "file_version_id", "CO_GRUPO", "CO_SUB_GRUPO", "CO_FORMA_ORGANIZACAO", "NO_FORMA_ORGANIZACAO"),
                on=[c for c in ("file_version_id", "CO_GRUPO", "CO_SUB_GRUPO", "CO_FORMA_ORGANIZACAO") if c in proc.columns],
                how="left",
            )
        if not fin.is_empty():
            proc = proc.join(
                _sel(fin, "file_version_id", "CO_FINANCIAMENTO", "NO_FINANCIAMENTO"),
                on=[c for c in ("file_version_id", "CO_FINANCIAMENTO") if c in proc.columns],
                how="left",
            )
        if not rub.is_empty():
            proc = proc.join(
                _sel(rub, "file_version_id", "CO_RUBRICA", "NO_RUBRICA"),
                on=[c for c in ("file_version_id", "CO_RUBRICA") if c in proc.columns],
                how="left",
            )

        # Derivações
        proc = proc.with_columns(
            # colunas complementares (substrings de CO_PROCEDIMENTO se não existirem)
            pl.col("CO_PROCEDIMENTO").cast(pl.Utf8),
        )
        if "CO_GRUPO" not in proc.columns:
            proc = proc.with_columns(pl.col("CO_PROCEDIMENTO").str.slice(0, 2).alias("CO_GRUPO"))
        if "CO_SUB_GRUPO" not in proc.columns:
            proc = proc.with_columns(pl.col("CO_PROCEDIMENTO").str.slice(2, 2).alias("CO_SUB_GRUPO"))
        if "CO_FORMA_ORGANIZACAO" not in proc.columns:
            proc = proc.with_columns(pl.col("CO_PROCEDIMENTO").str.slice(4, 2).alias("CO_FORMA_ORGANIZACAO"))

        def _concat_id_nome(co: str, nome: str) -> pl.Expr:
            return pl.concat_str(
                [pl.col(co).cast(pl.Utf8), pl.lit("-"), pl.col(nome).fill_null("")],
            )

        proc = proc.with_columns(
            _concat_id_nome("CO_PROCEDIMENTO", "NO_PROCEDIMENTO").alias("NO_PROCEDIMENTO"),
        )
        for co_col, no_col in [
            ("CO_GRUPO", "NO_GRUPO"),
            ("CO_SUB_GRUPO", "NO_SUB_GRUPO"),
            ("CO_FORMA_ORGANIZACAO", "NO_FORMA_ORGANIZACAO"),
            ("CO_FINANCIAMENTO", "NO_FINANCIAMENTO"),
            ("CO_RUBRICA", "NO_RUBRICA"),
        ]:
            if co_col in proc.columns and no_col in proc.columns:
                proc = proc.with_columns(_concat_id_nome(co_col, no_col).alias(no_col))

        # COMPLEXIDADE + NO_SUB_FINANCIAMENTO (com defaults)
        if "TP_COMPLEXIDADE" in proc.columns:
            proc = proc.with_columns(
                pl.when(pl.col("TP_COMPLEXIDADE").cast(pl.Int64, strict=False) == 0)
                .then(pl.lit("0-Não se Aplica"))
                .when(pl.col("TP_COMPLEXIDADE").cast(pl.Int64, strict=False) == 1)
                .then(pl.lit("1-Atenção Básica"))
                .when(pl.col("TP_COMPLEXIDADE").cast(pl.Int64, strict=False) == 2)
                .then(pl.lit("2-Média Complexidade"))
                .when(pl.col("TP_COMPLEXIDADE").cast(pl.Int64, strict=False) == 3)
                .then(pl.lit("3-Alta Complexidade"))
                .otherwise(pl.lit(""))
                .alias("COMPLEXIDADE")
            )
        if "NO_RUBRICA" in proc.columns and "CO_FINANCIAMENTO" in proc.columns:
            proc = proc.with_columns(
                pl.when(pl.col("NO_RUBRICA").is_null() & (pl.col("CO_FINANCIAMENTO") == "01"))
                .then(pl.lit("Atenção Básica (PAB)"))
                .when(pl.col("NO_RUBRICA").is_null() & (pl.col("CO_FINANCIAMENTO") == "06"))
                .then(pl.lit("Média e Alta Complexidade (MAC)"))
                .when(pl.col("NO_RUBRICA").is_null() & (pl.col("CO_FINANCIAMENTO") == "07"))
                .then(pl.lit("Vigilância em Saúde"))
                .otherwise(pl.col("NO_RUBRICA").fill_null(""))
                .alias("NO_SUB_FINANCIAMENTO")
            )
        proc = proc.drop([c for c in proc.columns if c.startswith("DT_COMPETENCIA")])
        return ProcedureDetails(df=proc.unique(subset=["CO_PROCEDIMENTO", "file_version_id"], keep="last"))
