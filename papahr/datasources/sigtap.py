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

# Nomes publicados no FTP (ftp2.datasus.gov.br/pub/sistemas/tup/downloads/):
#   formato ATUAL : TabelaUnificada_202610_v2610151110.zip   (competência + versão)
#   formato antigo: TabelaUnificada_<versao>_202301.zip
_ZIP_RE_ATUAL = re.compile(r"^TabelaUnificada_(?P<year>\d{4})(?P<month>\d{2})(?:_v\d+)?\.zip$", re.I)
_ZIP_RE_ANTIGO = re.compile(r"^TabelaUnificada_(?P<versao>\d+)_(?P<year>\d{4})(?P<month>\d{2})\.zip$", re.I)
_ALIAS_FILE = "ALIAS_OF.txt"  # versão sem publicação própria aponta para a versão vizinha


def parse_sigtap_zip_name(fname: str) -> str | None:
    """Extrai a competência AAAAMM de um nome de ZIP do SIGTAP (ou None)."""
    for rx in (_ZIP_RE_ATUAL, _ZIP_RE_ANTIGO):
        m = rx.match(fname.strip())
        if m:
            return f"{m.group('year')}{m.group('month')}"
    return None


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
            fname = f.rsplit("/", 1)[-1]
            version_id = parse_sigtap_zip_name(fname)
            if not version_id:
                continue
            try:
                out.append((fname, version_id, ymd(version_id)))
            except Exception:  # noqa: PERF203
                continue
        # Se houver mais de um ZIP para a mesma competência, fica o último (mais novo)
        by_version: dict[str, tuple[str, str, date]] = {}
        for item in sorted(out, key=lambda t: t[0]):
            by_version[item[1]] = item
        return sorted(by_version.values(), key=lambda t: t[1])

    # --------------------------------------------------------------------
    def version_dir(self, version_id: str) -> Path:
        return self.cache_dir / version_id

    def _resolve_dir(self, version_id: str) -> Path | None:
        """Diretório com os tb_*.txt da versão (segue ALIAS_OF se existir)."""
        d = self.version_dir(version_id)
        if not d.is_dir():
            return None
        alias = d / _ALIAS_FILE
        if alias.is_file():
            target = self.cache_dir / alias.read_text(encoding="utf-8").strip()
            return target if any(target.glob("tb_*.txt")) else None
        return d if any(d.glob("tb_*.txt")) else None

    def has_version(self, version_id: str) -> bool:
        """True só se a versão tem dados reais (pasta vazia NÃO conta)."""
        return self._resolve_dir(version_id) is not None

    def available_versions(self) -> list[str]:
        if not self.cache_dir.exists():
            return []
        return sorted(p.name for p in self.cache_dir.iterdir() if p.is_dir() and self.has_version(p.name))

    def _limpar_pasta_vazia(self, version_id: str) -> None:
        d = self.version_dir(version_id)
        if d.is_dir() and not any(d.iterdir()):
            try:
                d.rmdir()
            except OSError:
                pass

    def download(
        self,
        *,
        year_start: int | None = None,
        month_start: int | None = None,
        year_end: int | None = None,
        month_end: int | None = None,
        specific_dates: list[str] | None = None,
        newer: bool = False,
        prefer_github_mirror: bool = False,  # mantido por compatibilidade; espelho desativado
    ) -> list[str]:
        """Garante as versões SIGTAP pedidas no cache e retorna os version_ids disponíveis.

        Fonte única: FTP oficial do DATASUS (ftp2). O antigo espelho GitHub foi
        removido porque responde 404 para todas as competências desde 2025.

        Competência sem ZIP próprio no FTP (ex.: mês ainda não publicado) recebe um
        ALIAS para a competência publicada mais próxima ANTERIOR, de modo que o join
        por `file_version_id` continue funcionando com a tabela vigente.
        """
        version_ids_wanted: list[str] = []
        remote: list[tuple[str, str, date]] | None = None
        if specific_dates:
            version_ids_wanted = list(dict.fromkeys(specific_dates))
        elif newer:
            remote = self._list_remote()
            if remote:
                version_ids_wanted = [remote[-1][1]]
        elif year_start and month_start and year_end and month_end:
            from papahr.core import year_month_iter
            version_ids_wanted = [f"{y:04d}{m:02d}" for y, m in
                                  year_month_iter(year_start, month_start, year_end, month_end)]

        ok: list[str] = [v for v in version_ids_wanted if self.has_version(v)]
        remaining = [v for v in version_ids_wanted if v not in ok]
        if remaining:
            for v in remaining:
                self._limpar_pasta_vazia(v)
            try:
                remote = remote if remote is not None else self._list_remote()
            except Exception as e:  # noqa: BLE001
                log.warn("Falhou listar SIGTAP no FTP", err=str(e))
                remote = []
            by_version = {v: fname for (fname, v, _d) in remote}
            for v in remaining:
                if v in by_version:
                    if self._download_ftp(by_version[v], v):
                        ok.append(v)
                    continue
                # Sem publicação própria: alias para a mais recente anterior
                anteriores = sorted(x for x in by_version if x <= v)
                if not anteriores:
                    log.warn("SIGTAP: nenhuma versao publicada para", version=v)
                    continue
                alvo = anteriores[-1]
                if self.has_version(alvo) or self._download_ftp(by_version[alvo], alvo):
                    d = ensure_dir(self.version_dir(v))
                    (d / _ALIAS_FILE).write_text(alvo, encoding="utf-8")
                    log.info("SIGTAP: competencia sem ZIP proprio, usando alias", version=v, alvo=alvo)
                    ok.append(v)
        if year_start and month_start and year_end and month_end:
            self._cleanup_old(date(year_start, month_start, 1), date(year_end, month_end, 1))
        return ok

    # --------------------------------------------------------------------
    def _download_ftp(self, fname: str, version_id: str) -> bool:
        dest_dir = ensure_dir(self.cache_dir / version_id)
        if any(dest_dir.glob("tb_*.txt")):
            return True
        log.info("SIGTAP: baixando do FTP", arquivo=fname, version=version_id)
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
        src_dir = self._resolve_dir(version_id)
        if src_dir is None:
            log.warn("Arquivos SIGTAP ausentes para", detail=detail, version=version_id)
            return pl.DataFrame()
        raw_files = sorted(src_dir.glob(raw_name)) or sorted(src_dir.glob(raw_name.upper()))
        layout_files = sorted(src_dir.glob(layout_name)) or sorted(src_dir.glob(layout_name.upper()))
        if not raw_files or not layout_files:
            log.warn("Arquivos SIGTAP ausentes para", detail=detail, version=version_id)
            return pl.DataFrame()
        layouts: list[dict[str, int | str]] = []
        layout_candidates = layout_files
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
        # file_version_id é SEMPRE a competência pedida (mesmo quando é alias)
        all_rows: list[dict[str, str]] = []
        for rf in raw_files:
            with open(rf, encoding="latin-1", errors="replace") as f:
                for line in f:
                    if not line.strip():
                        continue
                    row = {"file_version_id": version_id}
                    for lyt in layouts:
                        s = int(lyt["start"]) - 1
                        e = int(lyt["end"])
                        row[str(lyt["column_name"])] = line[s:e].strip()
                    all_rows.append(row)
        return pl.DataFrame(all_rows) if all_rows else pl.DataFrame()

    def get_detail(self, detail: SIGTAP_DETAIL_NAMES) -> pl.DataFrame:
        """Retorna o detalhe consolidado de todas as versões baixadas."""
        versions = self.available_versions()
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
