"""Orquestração de pipelines: remoto (FTP) e local (DBC arquivos)."""
from __future__ import annotations

import gc
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Optional

import polars as pl

from papahr.config import get_settings
from papahr.core.archive_extractor import extract_all_archives, find_data_folders
from papahr.core.dbc_reader import read_dbc, read_many_dbc
from papahr.core.ftp_client import BASE_SIASIH, FTPClient
from papahr.core.utils import chunkify, ensure_dir, ymd
from papahr.datasources.cnes import CNESRepository
from papahr.datasources.counties import CountiesRepository
from papahr.datasources.sigtap import SIGTAPRepository
from papahr.geo.reference import resolve_geo_enrichment
from papahr.logging_config import get_logger
from papahr.processors import ProcessorContext, get_processor
from papahr.security import PipelineRequest

log = get_logger(__name__)


def _aplicar_filtro_geo_final(df: pl.DataFrame, req: PipelineRequest) -> pl.DataFrame:
    """Aplica filtro cascata Macro/DRS no resultado final do pipeline.

    USO: Correção Antigravity #3 — 100% VETORIZADO (Polars nativo) sobre ~5570
    combinações únicas de (UF, Município), 0 loops Python linha-a-linha.
    Para 500 mil linhas executa em MILISSEGUNDOS (antes: 5-15s travando UI).
    """
    macros = [m.strip() for m in (req.macro_regions or []) if m and m.strip()]
    drss = [d.strip() for d in (req.health_regions or []) if d and d.strip()]
    if not macros and not drss:
        return df
    if df.is_empty():
        return df

    cols = df.columns
    col_cnes = None
    for c in ("CNES", "cnes", "CO_CNES", "cod_cnes"):
        if c in cols:
            col_cnes = c
            break
    col_uf = None
    for c in ("UF", "uf", "SG_UF", "SIGLA_UF", "CO_UF_GESTOR", "CO_ESTADO_GESTOR", "uf_gestora"):
        if c in cols:
            col_uf = c
            break
    col_mun = None
    for c in (
        "CO_MUNICIPIO_GESTOR", "MUNICIPIO_GESTOR", "CO_MUNICIPIO",
        "municipio_cod", "cod_municipio_ibge",
    ):
        if c in cols:
            col_mun = c
            break
    col_uf_ibge = None
    if col_uf and col_uf.startswith("CO_"):
        col_uf_ibge = col_uf
        col_uf = None

    # (1) Extrai combinações únicas (máx ~5570 no Brasil inteiro)
    # NORMALIZA colunas de códigos para largura OFICIAL ANTES de extrair únicos:
    # CNES=7, Mun=7, UF=2 — evita "0123456" vs "123456" criar 2 combinações separadas
    norm_exprs: list[pl.Expr] = []
    if col_cnes:
        norm_exprs.append(
            pl.col(col_cnes).cast(pl.Utf8, strict=False).str.strip_chars().str.zfill(7).alias(col_cnes)
        )
    if col_mun:
        norm_exprs.append(
            pl.col(col_mun).cast(pl.Utf8, strict=False).str.strip_chars().str.zfill(7).alias(col_mun)
        )
    if col_uf:
        norm_exprs.append(
            pl.col(col_uf).cast(pl.Utf8, strict=False).str.strip_chars().str.to_uppercase().alias(col_uf)
        )
    if col_uf_ibge:
        norm_exprs.append(
            pl.col(col_uf_ibge).cast(pl.Utf8, strict=False).str.strip_chars().str.zfill(2).alias(col_uf_ibge)
        )
    if norm_exprs:
        df_n = df.with_columns(norm_exprs)
    else:
        df_n = df
    uniq_cols = []
    if col_uf:      uniq_cols.append(col_uf)
    if col_uf_ibge: uniq_cols.append(col_uf_ibge)
    if col_mun:     uniq_cols.append(col_mun)
    if col_cnes:    uniq_cols.append(col_cnes)
    if not uniq_cols:
        return df

    uniq = df_n.select(uniq_cols).unique()
    rows_keep_uniq: list[bool] = []
    # (2) Resolve geo enrichment PARA CADA COMBINAÇÃO ÚNICA (pequeno loop)
    for i in range(uniq.height):
        uf_s = None
        uf_ibge_2 = None
        mun = None
        if col_uf:
            v = uniq.item(i, col_uf)
            if v is not None:
                uf_s = str(v).strip().upper()
        if col_uf_ibge:
            v = uniq.item(i, col_uf_ibge)
            if v is not None:
                uf_ibge_2 = str(v).strip().zfill(2)
        if col_mun:
            v = uniq.item(i, col_mun)
            if v is not None:
                mun = str(v).strip().zfill(7)
        _uf_f, _mun_f, co_macro, no_macro, co_drs, no_drs = resolve_geo_enrichment(
            uf_sigla=uf_s,
            co_uf_ibge_2dig=uf_ibge_2,
            co_municipio_6dig=mun,
        )
        keep = True
        if macros:
            bate = False
            for sm in macros:
                if sm == no_macro or (co_macro and co_macro in sm) or (no_macro and sm.startswith(no_macro[:8])):
                    bate = True
                    break
            if not bate:
                keep = False
        if keep and drss:
            bate = False
            for sd in drss:
                if sd == no_drs or (co_drs and co_drs in sd) or (no_drs and sd.startswith(no_drs[:8])):
                    bate = True
                    break
            if not bate:
                keep = False
        rows_keep_uniq.append(keep)

    keep_df = uniq.with_columns(pl.Series("_keep_uniq", rows_keep_uniq))
    # (3) JOIN vetorial de volta com o df original (1 passada Polars nativa, milissegundos)
    out = df_n.join(keep_df, on=uniq_cols, how="left", coalesce=True)
    return out.filter(pl.col("_keep_uniq").fill_null(False)).drop("_keep_uniq")


_DBC_FILE_RE_ORDEM1 = re.compile(
    r"^(?P<tipo>[A-Z]{2})(?P<uf>[A-Z]{2})(?P<ym>\d{4})(?P<part>[A-Z]{0,4})\.(?P<ext>dbc|dbf|csv|zip|gz|rar|7z)$",
    re.I,
)
_DBC_FILE_RE_ORDEM2 = re.compile(
    r"^(?P<tipo>[A-Z]{2})(?P<ym>\d{4})(?P<uf>[A-Z]{2})(?P<part>[A-Z]{0,4})\.(?P<ext>dbc|dbf|csv|zip|gz|rar|7z)$",
    re.I,
)

def _match_dbc_file(filename: str) -> re.Match | None:
    """Match nome arquivo DATASUS (correção Antigravity #2): ORDEM1 (tipo)(uf)(ym) OU ORDEM2 (tipo)(ym)(uf).
    Sempre opera sobre Path(filename).name — ignora caminhos FTP completos acidentalmente passados.
    """
    from pathlib import Path
    nm = Path(filename).name
    return (_DBC_FILE_RE_ORDEM1.match(nm)
            or _DBC_FILE_RE_ORDEM2.match(nm))

_DBC_FILE_RE = _DBC_FILE_RE_ORDEM1  # backward compat (alguém pode importar o nome)
_SYS_DATA_TYPE = {
    "SIA-PA": ("SIA", "PA"),
    "SIH-RD": ("SIH", "RD"),
    "SIH-RJ": ("SIH", "RJ"),
    "SIH-SP": ("SIH", "SP"),
}


@dataclass(slots=True)
class PipelineProgress:
    stage: str = ""
    current: int = 0
    total: int = 0
    message: str = ""
    elapsed: float = 0.0


ProgressCb = Callable[[PipelineProgress], None]


@dataclass(slots=True)
class PipelineResult:
    system: str
    df: pl.DataFrame
    rows: int
    output_csv: Optional[Path]
    output_parquet: Optional[Path]
    revoked_procedures_main: list[str] = field(default_factory=list)
    revoked_procedures_sec: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0


class BasePipeline:
    """Base com setup comum (carrega counties, CNES, SIGTAP)."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.settings.ensure_dirs()
        self.ftp = FTPClient()
        self.counties_repo = CountiesRepository()
        self.cnes_repo = CNESRepository()
        self.sigtap_repo = SIGTAPRepository()

    def _build_context(
        self,
        *,
        sigtap_dates: Optional[list[str]] = None,
        sigtap_years: Optional[tuple[int, int, int, int]] = None,
        newer_sigtap: bool = False,
    ) -> ProcessorContext:
        log.info("Carregando auxiliares (counties/CNES/SIGTAP)")
        counties = self.counties_repo.load()
        establishments = self.cnes_repo.load_health_establishments(newer=True)
        if newer_sigtap:
            self.sigtap_repo.download(newer=True)
        elif sigtap_dates:
            self.sigtap_repo.download(specific_dates=sigtap_dates)
        elif sigtap_years:
            self.sigtap_repo.download(
                year_start=sigtap_years[0], month_start=sigtap_years[1],
                year_end=sigtap_years[2], month_end=sigtap_years[3],
            )
        else:
            self.sigtap_repo.download(newer=True)
        proc = self.sigtap_repo.get_procedure_details()
        cbo = self.sigtap_repo.get_detail("CBO")
        cid = self.sigtap_repo.get_detail("CID")
        return ProcessorContext(
            counties=counties,
            establishments=establishments,
            procedure_details=proc,
            cbo=cbo,
            cid=cid,
        )

    def _refresh_sigtap_for_versions(self, version_ids: list[str]) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
        """Faz download incremental se novas versões aparecerem no PA_CMP etc."""
        existing = {p.name for p in self.sigtap_repo.cache_dir.iterdir() if p.is_dir()}
        needed = [v for v in version_ids if v not in existing]
        if needed:
            log.info("Baixando versoes SIGTAP ausentes", n=len(needed), vers=needed[:5])
            self.sigtap_repo.download(specific_dates=needed)
        proc = self.sigtap_repo.get_procedure_details().df
        cbo = self.sigtap_repo.get_detail("CBO")
        cid = self.sigtap_repo.get_detail("CID")
        return proc, cbo, cid

    def _save_outputs(self, df: pl.DataFrame, req: PipelineRequest, name: str) -> tuple[Optional[Path], Optional[Path]]:
        if df.is_empty():
            return None, None
        csv_path, parquet_path = None, None
        out_dir = ensure_dir(self.settings.output_dir)
        if req.save_csv:
            csv_path = out_dir / f"{name}.csv"
            df.write_csv(csv_path, separator=";", null_value="")
            log.info("CSV salvo", path=str(csv_path))
        if req.save_parquet:
            parquet_path = out_dir / f"{name}.parquet"
            df.write_parquet(parquet_path, compression="zstd")
            log.info("Parquet salvo", path=str(parquet_path))
        return csv_path, parquet_path

    # ---------------------------------------------------------------- helper
    @staticmethod
    def _check_revoked(df: pl.DataFrame, col_proc: str) -> list[str]:
        if df.is_empty() or col_proc not in df.columns:
            return []
        mask = df[col_proc].is_null()
        code_col = col_proc.replace("Procedimentos realizados", "CO Procedimentos realizados").replace(
            "Procedimentos Secundarios", "CO Procedimentos Secundarios"
        )
        if code_col not in df.columns:
            return []
        codes = df.filter(mask)[code_col].unique(maintain_order=True).to_list()
        return [str(c) for c in codes if c is not None]

    def _warn_revoked(self, result: PipelineResult) -> None:
        if result.revoked_procedures_main:
            msg = (
                "Procedimentos com valores nulos (possivelmente revogados): "
                + ", ".join(result.revoked_procedures_main[:10])
                + ("..." if len(result.revoked_procedures_main) > 10 else "")
            )
            result.warnings.append(msg)
            log.warn(msg)
        if result.revoked_procedures_sec:
            msg = (
                "Procedimentos secundários com valores nulos (possivelmente revogados): "
                + ", ".join(result.revoked_procedures_sec[:10])
            )
            result.warnings.append(msg)
            log.warn(msg)


# ---------------------------------------------------------------------------
# Pipeline Remoto: baixa arquivos via FTP e processa em chunks
# ---------------------------------------------------------------------------

class RemotePipeline(BasePipeline):
    """Pipeline com download direto de FTP do DATASUS."""

    @staticmethod
    def _ftp_candidates(data_source: str) -> list[tuple[str, str]]:
        """Candidatos FTP limpos (sem duplicatas, sem paths que sempre retornam 550 NOT FOUND).

        Hosts: ftp.datasus.gov.br (principal, 100% ativo) PRIMEIRO; ftp2.datasus.gov.br (backup/espelho) ÚLTIMO.
        Paths válidos (prova ao vivo FTP 05/08/2026):
          1. /dissemin/publicos/{SRC}SUS/200801_/Dados/       (padrão OFICIAL — 53.920 SIA / 22.696 SIH arquivos)
          2. /dissemin/publicos/{SRC}SUS/200801_/             (fallback raro, alguns dados ficam aqui)
          3. /dissemin/publicos/{SRC}SUS/Dados/               (versão sem ano corte — legado)
          4. /dissemin/publicos/{SRC}SUS/                     (raiz sistema, fallback raro)
        """
        hosts: list[str] = [
            "ftp.datasus.gov.br",
            "ftp2.datasus.gov.br",
        ]
        src_upper = data_source.upper()
        sys_name = {"SIA": "SIASUS", "SIH": "SIHSUS"}.get(src_upper, f"{src_upper}SUS")
        paths_base: list[str] = [
            f"/dissemin/publicos/{sys_name}/200801_/Dados/",
            f"/dissemin/publicos/{sys_name}/200801_/",
            f"/dissemin/publicos/{sys_name}/Dados/",
            f"/dissemin/publicos/{sys_name}/",
        ]
        seen: set[tuple[str, str]] = set()
        candidates: list[tuple[str, str]] = []
        for host in hosts:
            for p in paths_base:
                key = (host, p)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(key)
        return candidates

    def _try_filter_on_listing(
        self,
        listing: list[str],
        data_type: str,
        state_abbr: list[str],
        date_start: date,
        date_end: date,
    ) -> list[tuple[str, date, str]]:
        out: list[tuple[str, date, str]] = []
        for f in listing:
            m = _match_dbc_file(f)
            if not m:
                continue
            if m.group("tipo").upper() != data_type.upper():
                continue
            if m.group("uf").upper() not in state_abbr:
                continue
            try:
                d = ymd(m.group("ym"))
            except Exception:  # noqa: PERF203
                continue
            if date_start <= d <= date_end:
                out.append((f, d, m.group("uf").upper()))
        return out

    def _list_remote_files(
        self,
        data_source: str,
        data_type: str,
        state_abbr: list[str],
        date_start: date,
        date_end: date,
    ) -> list[tuple[str, date, str]]:
        """Lista arquivos DBC do DATASUS com FALLBACK robusto (12 candidatos)."""
        candidates = self._ftp_candidates(data_source)
        errors_tried: list[str] = []
        best_partial: list[tuple[str, date, str]] = []
        chosen_host = self.settings.datasus_ftp_host
        # ✅ BUG FINAL FIX: last_match contém o PRIMEIRO filtered com MATCHES que fez break.
        #    Anteriormente chosen = best_partial SEMPRE (best_partial iniciava [] → 0 arquivos
        #    mesmo que filtered = 24 hits! — 100% reproduz erro do usuário "Nenhum arquivo encontrado").
        last_match: list[tuple[str, date, str]] = []

        for host, remote_dir in candidates:
            try:
                files = self.ftp.list_dir_ftp(host, remote_dir)
            except Exception as e:  # noqa: PERF203
                errors_tried.append(f"  - ftp://{host}{remote_dir}  [FALHA list_dir: {type(e).__name__}]")
                continue

            filtered = self._try_filter_on_listing(files, data_type, state_abbr, date_start, date_end)
            if filtered:
                chosen_host = host
                last_match = filtered
                log.info("[FTP] Encontrei %d arquivo(s) candidatos em ftp://%s%s",
                         len(filtered), host, remote_dir)
                if self.settings.datasus_ftp_host != host:
                    try:
                        from papahr.config import patch_ftp_host
                        patch_ftp_host(host)
                    except Exception:  # noqa: BLE001
                        self.settings.datasus_ftp_host = host
                break
            if files and len(files) > len(best_partial):
                best_partial = filtered

        all_months = {ymd(f"{y:04d}{m:02d}") for y in range(date_start.year, date_end.year + 1)
                      for m in range(1, 13)
                      if date_start <= date(y, m, 1) <= date_end}

        # Preferência: primeiro candidato que FEZ MATCH (last_match) → só se NENHUM
        # candidato deu match → usa best_partial (maior pasta varrida, ainda que 0 matches,
        # para a mensagem de erro conter a maior informação de diagnóstico).
        chosen = last_match if last_match else best_partial
        have = {f[1] for f in chosen}
        if not chosen:
            msg_lines = [
                "Nenhum arquivo encontrado no FTP para os parametros informados.",
                f" - Sistema: {data_source} tipo={data_type} UFs={state_abbr}",
                f" - Janela: {date_start.isoformat()}..{date_end.isoformat()}",
                "Candidatos tentados (2 hosts x 6 paths):",
                *errors_tried,
                "Sugestoes:",
                "  1) Verifica a data (ex: 2025/01 pode ainda nao ter sido publicado no DATASUS).",
                "  2) Tenta 'ano INTEIRO anterior' (2024 completo - sempre tem arquivos).",
                "  3) Se na Web https://datasus.saude.gov.br/transferencia-de-arquivos/ existir arquivos",
                "     em outra pasta, usa fonte 'Pasta compactada' e aponte o arquivo .zip/.rar baixado.",
            ]
            if errors_tried:
                msg_lines.append("Ultimos erros acima.")
            raise RuntimeError("\n".join(msg_lines))

        missing = sorted(all_months - have)
        if missing:
            log.warn("Meses ausentes no FTP", n=len(missing), sample=missing[:5], host=chosen_host)
        return chosen

    def run(self, req: PipelineRequest, progress_cb: Optional[ProgressCb] = None) -> PipelineResult:
        start = time.perf_counter()
        if not req.date_range:
            raise ValueError("RemotePipeline requer date_range")
        progress = PipelineProgress(stage="Inicializando")
        if progress_cb:
            progress_cb(progress)

        data_source, data_type = _SYS_DATA_TYPE[req.information_system]
        dr = req.date_range
        ds = date(dr.year_start, dr.month_start, 1)
        de = date(dr.year_end, dr.month_end, 1)

        progress.stage = "Listando arquivos FTP"
        progress.message = f"{req.information_system} em {ds}..{de} (fallback 12 caminhos)"
        if progress_cb:
            progress_cb(progress)

        files = self._list_remote_files(data_source, data_type, req.state_abbr, ds, de)
        if not files:
            raise RuntimeError(
                "Nenhum arquivo encontrado no FTP (apos fallback 12 caminhos). "
                "Tenta datas mais antigas ou usa fonte 'Pasta compactada'."
            )

        # Constrói o contexto com SIGTAP das datas selecionadas
        progress.stage = "Carregando dados auxiliares"
        if progress_cb:
            progress_cb(progress)
        ctx = self._build_context(sigtap_years=(dr.year_start, dr.month_start, dr.year_end, dr.month_end))
        processor = get_processor(req.information_system)

        files_names = [f[0] for f in files]
        chunks = chunkify(files_names, data_type if data_type in {"PA", "SP"} else None)
        progress.total = max(1, len(chunks))

        tmp = ensure_dir(self.settings.cache_dir / req.information_system)
        # limpa diretório de chunks antigos
        for old in tmp.iterdir():
            if old.is_dir():
                shutil.rmtree(old, ignore_errors=True)
        chunk_outputs: list[pl.DataFrame] = []

        for i, chunk in enumerate(chunks, 1):
            progress.stage = f"Chunk {i}/{len(chunks)} - Download"
            progress.current = i
            progress.message = f"{len(chunk)} arquivo(s)"
            if progress_cb:
                progress_cb(progress)

            chunk_dir = ensure_dir(tmp / f"chunk_{i}")
            downloaded: list[Path] = []
            for fname in chunk:
                url = BASE_SIASIH.format(src=data_source) + fname
                dest = chunk_dir / fname
                try:
                    downloaded.append(self.ftp.download_ftp(url, dest))
                except Exception as e:  # noqa: PERF203
                    log.warn("Falha download FTP", fname=fname, err=str(e))

            progress.stage = f"Chunk {i}/{len(chunks)} - Leitura DBC"
            if progress_cb:
                progress_cb(progress)
            raw = read_many_dbc([str(p) for p in downloaded])

            # Atualiza SIGTAP com versões encontradas nos dados
            ym_col = {"SIA": "PA_CMP", "SIH": "ANOMES_CMPT"}[data_source]
            if data_source == "SIH":
                # SIH-RD/RJ tem ANO_CMPT/MES_CMPT separados
                if ym_col not in raw.columns and "ANO_CMPT" in raw.columns:
                    raw = raw.with_columns(
                        pl.concat_str([
                            pl.col("ANO_CMPT").cast(pl.Utf8),
                            pl.col("MES_CMPT").cast(pl.Utf8).str.zfill(2),
                        ]).alias("ANOMES_CMPT")
                    )
            if ym_col in raw.columns:
                vers = [str(v) for v in raw[ym_col].unique(maintain_order=True).to_list() if v]
                if vers:
                    proc, cbo, cid = self._refresh_sigtap_for_versions(vers)
                    if proc.height > 0:
                        from papahr.datasources.sigtap import ProcedureDetails
                        ctx.procedure_details = ProcedureDetails(df=proc)
                    if cbo.height > 0:
                        ctx.cbo = cbo
                    if cid.height > 0:
                        ctx.cid = cid

            # Aplica filtro de município/estabelecimento: decide qual filtro usar
            cols_set = set(raw.columns)
            county_col = next(iter(cols_set.intersection({"PA_UFMUN", "MUNIC_MOV", "SP_M_HOSP", "CO_MUNICIPIO", "MUNICIP"})), None)
            county_match = bool(req.county_id and county_col)
            county_ok = bool(county_match and county_col and raw[county_col].cast(pl.Utf8).is_in(req.county_id).any())

            estab_col = next(iter(cols_set.intersection({"CNES", "PA_CODUNI", "SP_CNES", "CO_CNES"})), None)
            estab_ok = (
                bool(req.health_establishment_id and estab_col)
                and raw.with_columns(pl.col(estab_col).cast(pl.Utf8))[estab_col].is_in(req.health_establishment_id).any()
            )

            progress.stage = f"Chunk {i}/{len(chunks)} - Processamento"
            if progress_cb:
                progress_cb(progress)
            try:
                # Correção Antigravity #4: PASSAR AMBOS os filtros SEMPRE
                # (não anula county_id se tem health_establishment_id e vice-versa).
                # O processor.process() trata a interseção (AND) internamente.
                out_chunk = processor.process(
                    raw, ctx,
                    county_id=req.county_id,
                    health_establishment_id=req.health_establishment_id,
                )
            except Exception as e:
                log.warn("Falhou processamento chunk", i=i, err=str(e))
                out_chunk = pl.DataFrame()

            del raw
            gc.collect()
            if out_chunk.height > 0:
                chunk_outputs.append(out_chunk)
            # Limpa chunk dir imediatamente para economizar disco
            shutil.rmtree(chunk_dir, ignore_errors=True)

        # Concat final
        progress.stage = "Finalizando"
        if progress_cb:
            progress_cb(progress)

        final_df = pl.concat(chunk_outputs, how="diagonal_relaxed") if chunk_outputs else pl.DataFrame()
        del chunk_outputs
        gc.collect()
        final_df = _aplicar_filtro_geo_final(final_df, req)

        csv_p, parquet_p = self._save_outputs(final_df, req, f"output{req.information_system.replace('-', '_')}")

        revoked_main = self._check_revoked(final_df, "Procedimentos realizados")
        revoked_sec = self._check_revoked(final_df, "Procedimentos Secundarios")

        elapsed = time.perf_counter() - start
        result = PipelineResult(
            system=req.information_system,
            df=final_df,
            rows=final_df.height,
            output_csv=csv_p,
            output_parquet=parquet_p,
            revoked_procedures_main=revoked_main,
            revoked_procedures_sec=revoked_sec,
            elapsed_seconds=elapsed,
        )
        self._warn_revoked(result)
        if progress_cb:
            progress.current = progress.total
            progress.elapsed = elapsed
            progress.stage = "Concluido"
            progress.message = f"{result.rows} linhas em {elapsed:.1f}s"
            progress_cb(progress)
        return result


# ---------------------------------------------------------------------------
# Pipeline Local: processa arquivos DBC já baixados
# ---------------------------------------------------------------------------

class LocalPipeline(BasePipeline):
    """Pipeline usando arquivos DBC (ou DBF já descompactados) locais."""

    def _find_files(self, req: PipelineRequest) -> list[Path]:
        if not req.dbc_dir_path:
            raise ValueError("LocalPipeline requer dbc_dir_path")
        data_source, data_type = _SYS_DATA_TYPE[req.information_system]
        # Correção Antigravity #6: rglob case-insensitive + formatos compactados (csv/zip)
        exts = ("*.dbc", "*.DBF", "*.dbf", "*.DBC", "*.csv", "*.CSV", "*.zip", "*.ZIP", "*.rar", "*.RAR", "*.7z")
        all_files_raw: list[Path] = []
        base = Path(req.dbc_dir_path)
        for pat in exts:
            all_files_raw.extend(list(base.rglob(pat)))
        all_files = list(dict.fromkeys(all_files_raw))  # dedupe mantendo ordem
        seen_keys: set[tuple[str, str, str]] = set()
        out: list[Path] = []
        for f in sorted(all_files, key=lambda p: (p.stem, p.suffix.lower() == ".dbf"), reverse=True):
            m = _match_dbc_file(f.name)
            if not m:
                continue
            if m.group("tipo").upper() != data_type.upper():
                continue
            if m.group("uf").upper() not in req.state_abbr:
                continue
            key = (m.group("tipo").upper(), m.group("uf").upper(), m.group("ym"))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            out.append(f)
        return sorted(out)

    def run(self, req: PipelineRequest, progress_cb: Optional[ProgressCb] = None) -> PipelineResult:
        start = time.perf_counter()
        progress = PipelineProgress(stage="Inicializando")
        if progress_cb:
            progress_cb(progress)

        progress.stage = "Carregando dados auxiliares"
        if progress_cb:
            progress_cb(progress)
        ctx = self._build_context(newer_sigtap=True)
        processor = get_processor(req.information_system)

        progress.stage = "Buscando arquivos locais"
        files = self._find_files(req)
        if not files:
            raise RuntimeError(
                "Nenhum arquivo DBC/DBF local corresponde aos parâmetros "
                f"(sistema={req.information_system}, UF(s)={req.state_abbr}). "
                "Certifique-se de que a pasta contenha arquivos no padrão DATASUS "
                "(ex: RDCE2501.dbc, RDCE2501.dbf, PAAC2412.dbf, etc)."
            )
        chunks = chunkify(
            files,
            _SYS_DATA_TYPE[req.information_system][1]
            if _SYS_DATA_TYPE[req.information_system][1] in {"PA", "SP"} else None,
        )
        progress.total = max(1, len(chunks))

        tmp = ensure_dir(self.settings.cache_dir / req.information_system)
        chunk_outputs: list[pl.DataFrame] = []

        for i, chunk in enumerate(chunks, 1):
            progress.stage = f"Chunk {i}/{len(chunks)} - Leitura DBC"
            progress.current = i
            progress.message = f"{len(chunk)} arquivo(s)"
            if progress_cb:
                progress_cb(progress)
            raw = read_many_dbc([str(p) for p in chunk])

            # Atualiza SIGTAP com versões encontradas
            data_source = _SYS_DATA_TYPE[req.information_system][0]
            ym_col = {"SIA": "PA_CMP", "SIH": "ANOMES_CMPT"}[data_source]
            if data_source == "SIH" and ym_col not in raw.columns and "ANO_CMPT" in raw.columns:
                raw = raw.with_columns(
                    pl.concat_str([
                        pl.col("ANO_CMPT").cast(pl.Utf8),
                        pl.col("MES_CMPT").cast(pl.Utf8).str.zfill(2),
                    ]).alias("ANOMES_CMPT")
                )
            if ym_col in raw.columns:
                vers = [str(v) for v in raw[ym_col].unique(maintain_order=True).to_list() if v]
                if vers:
                    proc, cbo, cid = self._refresh_sigtap_for_versions(vers)
                    if proc.height > 0:
                        from papahr.datasources.sigtap import ProcedureDetails
                        ctx.procedure_details = ProcedureDetails(df=proc)
                    if cbo.height > 0:
                        ctx.cbo = cbo
                    if cid.height > 0:
                        ctx.cid = cid

            progress.stage = f"Chunk {i}/{len(chunks)} - Processamento"
            if progress_cb:
                progress_cb(progress)
            try:
                out_chunk = processor.process(
                    raw, ctx, county_id=req.county_id, health_establishment_id=req.health_establishment_id
                )
            except Exception as e:
                log.warn("Falhou processamento chunk local", i=i, err=str(e))
                out_chunk = pl.DataFrame()
            del raw
            gc.collect()
            if out_chunk.height > 0:
                chunk_outputs.append(out_chunk)

        progress.stage = "Finalizando"
        if progress_cb:
            progress_cb(progress)
        final_df = pl.concat(chunk_outputs, how="diagonal_relaxed") if chunk_outputs else pl.DataFrame()
        del chunk_outputs
        gc.collect()
        final_df = _aplicar_filtro_geo_final(final_df, req)

        csv_p, parquet_p = self._save_outputs(final_df, req, f"output{req.information_system.replace('-', '_')}")

        revoked_main = self._check_revoked(final_df, "Procedimentos realizados")
        revoked_sec = self._check_revoked(final_df, "Procedimentos Secundarios")
        elapsed = time.perf_counter() - start
        result = PipelineResult(
            system=req.information_system,
            df=final_df,
            rows=final_df.height,
            output_csv=csv_p,
            output_parquet=parquet_p,
            revoked_procedures_main=revoked_main,
            revoked_procedures_sec=revoked_sec,
            elapsed_seconds=elapsed,
        )
        self._warn_revoked(result)
        if progress_cb:
            progress.current = progress.total
            progress.elapsed = elapsed
            progress.stage = "Concluido"
            progress.message = f"{result.rows} linhas em {elapsed:.1f}s"
            progress_cb(progress)
        return result


def run_pipeline(req: PipelineRequest, progress_cb: Optional[ProgressCb] = None) -> PipelineResult:
    """Fábrica: escolhe Local ou Remote baseado em dbc_dir_path ou archive_dir_path.

    Quando `req.archive_dir_path` é fornecido:
      (a) Extrai todos .zip/.tar/.rar/.7z para cache_dir/extracted
      (b) Procura a pasta com maior quantidade de *.dbc (atribui a req.dbc_dir_path)
      (c) Procura pasta CNES e injeta em Settings.custom_cnes_dir se detectado
      (d) Chama LocalPipeline normalmente, com os mesmos avisos acumulados.
    """
    # ===== Caso 1: Fonte de arquivos compactados (.zip/.rar/.7z) =====
    if req.archive_dir_path is not None:
        extra = extract_all_archives(
            Path(req.archive_dir_path),
            progress_cb=(
                (lambda c, t, m: progress_cb and progress_cb(
                    PipelineProgress(
                        stage="Extraindo arquivos compactados",
                        current=c, total=t, message=m,
                    ))) if progress_cb else None
            ),
        )

        # Acha as subpastas: DBC (+ DBF descompactado) + opcionalmente CNES
        folders = find_data_folders(extra.root_dir)
        if folders.get("dbc") is None and folders.get("cnes") is None:
            raise RuntimeError(
                "Nenhum arquivo .dbc/.dbf nem planilhas de CNES encontradas após extração. "
                "Coloque na pasta os arquivos compactados (do CNES, SIA ou AIH/SIH) e tente novamente. "
                + (("Observações: " + " ; ".join(extra.warnings)) if extra.warnings else "")
            )

        # Atribui diretório CNES detectado (sobrescreve o padrão C:\DICIONARIO_DADOS)
        if folders.get("cnes"):
            s = get_settings()
            cnes_path = Path(folders["cnes"])
            log.info("CNES detectado em arquivo compactado, carregando dali", path=str(cnes_path))
            # Detecta a data_versao CNES por nome da pasta (YYYYMM)
            try:
                vers = re.search(r"(20\d{4})", cnes_path.name) or re.search(r"(20\d{4})", cnes_path.parent.name)
                s.custom_cnes_dir = cnes_path
                if vers:
                    s.custom_cnes_version = int(vers.group(1))
            except Exception:  # noqa: BLE001
                s.custom_cnes_dir = cnes_path

        # Precisamos ter DBCs para LocalPipeline; se detectou só CNES, ainda faz Remote
        if folders.get("dbc"):
            req.dbc_dir_path = folders["dbc"]
            log.info("LocalPipeline apos extracao, dbc_dir=", dbc=str(req.dbc_dir_path))
            pipeline: BasePipeline = LocalPipeline()
        else:
            # Só CNES extraído → deixa seguir para RemotePipeline,
            # mas avisa o usuário que os DBCs vão vir por FTP e o CNES já tá carregado.
            pipeline = RemotePipeline()
            if progress_cb:
                progress_cb(PipelineProgress(
                    stage="Extração finalizada (apenas CNES)",
                    current=1, total=1,
                    message="CNES extraído; DBCs serão baixados via FTP DATASUS.",
                ))

        result = pipeline.run(req, progress_cb=progress_cb)
        # Anexa avisos da extração
        if extra.warnings:
            result.warnings = list(result.warnings) + extra.warnings
            if progress_cb:
                progress_cb(PipelineProgress(
                    stage="Concluído com avisos de extração",
                    current=1, total=1,
                    message=f"{len(extra.warnings)} aviso(s) de extração",
                ))
        return result

    # ===== Casos 2 e 3: Local (já tem DBCs) ou Remoto (FTP) =====
    pipeline = LocalPipeline() if req.dbc_dir_path else RemotePipeline()
    return pipeline.run(req, progress_cb=progress_cb)
