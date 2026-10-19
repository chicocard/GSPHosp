"""Download e processamento do CNES - Cadastro Nacional de Estabelecimentos de Saúde."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zipfile import ZipFile

import polars as pl

from papahr.config import get_settings
from papahr.core.dbc_reader import read_many_dbc
from papahr.core.ftp_client import BASE_CNES, FTPClient
from papahr.core.utils import ensure_dir, ymd, year_month_iter
from papahr.logging_config import get_logger

log = get_logger(__name__)

# ✅ Corrigido: regex mais permissivo.
# - Aceita partição A/B/C no final (ex: STMG2401a.dbc / STMG2401.DBC / STMG2401.zip
# - Aceita extensões .dbc/.dbf/.csv/.zip/.gz/.rar/.7z (r
_FILE_RE = re.compile(
    r"^(?P<prefix>[A-Z]{2})(?P<uf>[A-Z]{2})(?P<ym>\d{4})(?P<part>[A-Za-z0-9]{0,4})\.(?P<ext>dbc|dbf|csv|zip|gz|rar|7z)$",
    re.I,
)
_CNES_TYPES = {"ST", "LT", "HB", "EQ"}


@dataclass(slots=True)
class CNESHealthEstablishment:
    df: pl.DataFrame

    def filter_by_ids(self, ids: list[str]) -> pl.DataFrame:
        if not ids:
            return self.df
        return self.df.filter(pl.col("CNES").is_in(ids))

    def get_ids(self) -> list[str]:
        return self.df["CNES"].to_list()


class CNESRepository:
    """Baixa e lê arquivos CNES/ST (estabelecimentos) + CADGER-BR embutido."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.ftp = FTPClient()
        self.base_cache: Path = self.settings.cache_dir / "CNES"
        self._embedded_cadger: Path = (
            Path(__file__).resolve().parent.parent / "data" / "CADGER-BR.parquet"
        )

    # --------------------------------------------------------------------
    def _list_type(self, type_data: str = "ST") -> list[tuple[str, str, date]]:
        """Lista arquivos remotos do CNES."""
        type_data = type_data.upper().strip()
        if type_data not in _CNES_TYPES:
            raise ValueError(f"type_data invalido: {type_data}")
        url_dir = f"ftp://{self.settings.datasus_ftp_host}/dissemin/publicos/CNES/200508_/Dados/{type_data}/"
        files = self.ftp.list_dir_ftp(self.settings.datasus_ftp_host, f"/dissemin/publicos/CNES/200508_/Dados/{type_data}/")
        out: list[tuple[str, str, date]] = []
        for f in files:
            m = _FILE_RE.match(f)
            if not m:
                continue
            try:
                d = ymd(m.group("ym"))
                out.append((f, m.group("uf").upper(), d))
            except Exception:  # noqa: PERF203
                continue
        return out

    def download_type(
        self,
        type_data: str = "ST",
        *,
        year_start: int | None = None,
        month_start: int | None = None,
        year_end: int | None = None,
        month_end: int | None = None,
        state_abbr: list[str] | None = None,
        newer: bool = False,
    ) -> list[Path]:
        """Baixa arquivos DBC do CNES."""
        files = self._list_type(type_data)
        if not files:
            raise RuntimeError("Nenhum arquivo CNES listado")
        if newer and year_start is None:
            max_date = max(f[2] for f in files)
            files = [f for f in files if f[2] == max_date]
        elif year_start and month_start and year_end and month_end:
            start = date(year_start, month_start, 1)
            end = date(year_end, month_end, 1)
            files = [f for f in files if start <= f[2] <= end]
        if state_abbr and "ALL" not in state_abbr:
            ufs = {s.upper() for s in state_abbr}
            files = [f for f in files if f[1] in ufs]
        type_dir = ensure_dir(self.base_cache / type_data)
        downloaded: list[Path] = []
        for fname, _uf, _d in files:
            url = BASE_CNES.format(tipo=type_data) + fname
            dest = type_dir / fname
            try:
                downloaded.append(self.ftp.download_ftp(url, dest))
            except Exception as e:  # noqa: PERF203
                log.warn("Falhou download CNES", fname=fname, err=str(e))
        return downloaded

    # --------------------------------------------------------------------
    def _load_cadger(self) -> pl.DataFrame:
        """Carrega CADGER-BR (detalhes de estabelecimento) embutido."""
        if self._embedded_cadger.exists():
            return pl.read_parquet(self._embedded_cadger)
        # Fallback: cria DF vazio com as colunas esperadas
        return pl.DataFrame(
            schema={"CNES": pl.Utf8, "FANTASIA": pl.Utf8, "COMPETEN": pl.Utf8}
        )

    # --------------------------------------------------------------------
    def _try_cnes_loader_local(self, state_abbr: list[str] | None) -> pl.DataFrame | None:
        r"""Camada 1 PRIORITARIA: usa CNESLoader CSV local (C:\DICIONARIO_DADOS) —
        a mesma fonte do Step4 Wizard (que já carregou Hospital Dilson Godinho!)."""
        try:
            from papahr.dashboard.cnes_loader import get_cnes_data
            cnes = get_cnes_data()
            cnes.load()
            edf = cnes.estabelecimentos
            if edf is None or edf.is_empty():
                return None
            cnes_col = next(
                (c for c in ("CO_CNES", "CNES", "COD_CNES") if c in edf.columns),
                None,
            )
            if cnes_col is None:
                return None
            fantasia_col = next(
                (c for c in ("NO_FANTASIA", "NO_RAZAO_SOCIAL", "NOME_FANTASIA",
                             "RAZAO_SOCIAL", "NOME") if c in edf.columns),
                None,
            )
            comp_col = next(
                (c for c in ("COMPETEN", "NU_COMPETENCIA") if c in edf.columns),
                None,
            )
            cols_keep: list[pl.Expr] = [
                pl.col(cnes_col).cast(pl.Utf8, strict=False).str.strip_chars().str.zfill(7).alias("CNES")
            ]
            if fantasia_col:
                cols_keep.append(
                    pl.col(fantasia_col).cast(pl.Utf8).fill_null(pl.lit("")).alias("FANTASIA")
                )
            else:
                cols_keep.append(pl.lit("").alias("FANTASIA"))
            if comp_col:
                cols_keep.append(
                    pl.col(comp_col).cast(pl.Utf8).fill_null(pl.lit("")).alias("COMPETEN")
                )
            else:
                cols_keep.append(pl.lit("").alias("COMPETEN"))
            # UF para filtro (simplificado: aplica via coluna SIGLA_UF se houver)
            uf_col = None
            if state_abbr and "ALL" not in state_abbr:
                for c in ("SIGLA_UF", "SIGLA_ESTADO_GESTOR", "SIGLA_UF_GESTOR",
                          "SG_UF", "UF", "SG_UF_GESTOR", "SG_ESTADO"):
                    if c in edf.columns:
                        uf_col = c
                        break
            if uf_col:
                cols_keep.append(pl.col(uf_col).cast(pl.Utf8).fill_null("").alias("__UF"))
            df = edf.select(cols_keep)
            if "__UF" in df.columns and state_abbr:
                uf_set = {str(s).strip().upper() for s in state_abbr if s and str(s).strip()}
                filt = [str(v).strip().upper() in uf_set for v in df["__UF"].to_list()]
                df = df.filter(pl.Series(filt)).drop("__UF")
            df = df.unique(subset=["CNES"], keep="last")
            log.info("[CNES] Camada 1 (CNESLoader local CSV) ok: %d registros", len(df))
            return df
        except Exception as exc:
            log.warning("[CNES] Falhou camada 1 CNESLoader local: %s", exc)
            return None

    def load_health_establishments(
        self,
        *,
        newer: bool = True,
        year_start: int | None = None,
        month_start: int | None = None,
        year_end: int | None = None,
        month_end: int | None = None,
        state_abbr: list[str] | None = None,
    ) -> CNESHealthEstablishment:
        """4 camadas: (1) CNESLoader local CSV → (2) cache DBC → (3) FTP → (4) VAZIO.

        NUNCA MAIS gera RuntimeError "Nenhum arquivo CNES listado" que trava o pipeline do usuário.
        """
        # ===============================================
        # 🥇 CAMADA 1: CNESLoader CSV local (C:\DICIONARIO_DADOS) — PRINCIPAL
        df_local = self._try_cnes_loader_local(state_abbr)
        df_st: pl.DataFrame | None = None
        if df_local is not None and not df_local.is_empty():
            df_st = df_local

        # ===============================================
        # 🥈 CAMADA 2: Cache DBC CNES/ST local (pré-baixados)
        if df_st is None:
            st_dir = self.base_cache / "ST"
            dbcs = sorted(st_dir.glob("*.dbc")) if st_dir.exists() else []
            if state_abbr and "ALL" not in state_abbr:
                uf_set = {s.upper() for s in state_abbr}
                dbcs = [p for p in dbcs if len(p.name) >= 8 and p.name[2:4].upper() in uf_set]
            if not dbcs:
                # ===============================================
                # 🥉 CAMADA 3: FTP DATASUS (só se não temos cache)
                log.info("[CNES] Camada 2 cache vazio, tentando FTP", state_abbr=state_abbr)
                try:
                    if newer:
                        self.download_type("ST", newer=True, state_abbr=state_abbr)
                    elif year_start and month_start and year_end and month_end:
                        self.download_type("ST",
                            year_start=year_start, month_start=month_start,
                            year_end=year_end, month_end=month_end,
                            state_abbr=state_abbr)
                    else:
                        self.download_type("ST", newer=True, state_abbr=state_abbr)
                except Exception as exc_ftp:  # noqa: BLE001
                    log.warning("[CNES] Camada 3 FTP falhou: %s", exc_ftp)
                st_dir2 = self.base_cache / "ST"
                dbcs = sorted(st_dir2.glob("*.dbc")) if st_dir2.exists() else []
                if state_abbr and "ALL" not in state_abbr:
                    uf_set = {s.upper() for s in state_abbr}
                    dbcs = [p for p in dbcs if len(p.name) >= 8 and p.name[2:4].upper() in uf_set]
            if dbcs:
                log.info("[CNES] Camada 2/3 lendo %d arquivos DBC", len(dbcs))
                try:
                    df_raw = read_many_dbc([str(p) for p in dbcs])
                except Exception as exc_dbc:  # noqa: BLE001
                    # read_many_dbc agora FALHA explicitamente em vez de retornar vazio;
                    # preserva o contrato desta camada ("NUNCA QUEBRA PIPELINE") caindo
                    # para a proxima camada com df vazio (sem dado sintetico).
                    log.warning("[CNES] Camada 2/3 falha ao ler DBC: %s", exc_dbc)
                    df_raw = pl.DataFrame()
                if not df_raw.is_empty():
                    cols_want = [c for c in ("CNES", "FANTASIA", "COMPETEN") if c in df_raw.columns]
                    df_st = df_raw.select(cols_want) if cols_want else df_raw

        # ===============================================
        # 🏅 CAMADA 4 FALLBACK VAZIO (NUNCA QUEBRA PIPELINE!)
        if df_st is None:
            log.warning("[CNES] 4 camadas sem fonte CNES → retornando VAZIO (não quebra pipeline).")
            df_st = pl.DataFrame(schema={"CNES": pl.Utf8, "FANTASIA": pl.Utf8, "COMPETEN": pl.Utf8})

        # Enriquecimento CADGER (opcional)
        df_cadger = self._load_cadger()
        if not df_cadger.is_empty():
            if "CNES" in df_st.columns:
                df_st = df_st.with_columns(pl.col("CNES").cast(pl.Utf8, strict=False))
            df_cadger = df_cadger.with_columns(pl.col("CNES").cast(pl.Utf8, strict=False))
            df_st = df_st.join(df_cadger, on="CNES", how="left", coalesce=True)
            if "FANTASIA_right" in df_st.columns:
                df_st = df_st.with_columns(
                    pl.coalesce(pl.col("FANTASIA"), pl.col("FANTASIA_right")).alias("FANTASIA")
                ).drop("FANTASIA_right")

        # Normaliza colunas finais
        if "CNES" not in df_st.columns:
            df_st = df_st.with_columns(pl.lit("").cast(pl.Utf8).alias("CNES"))
        df_st = df_st.with_columns(
            pl.col("CNES").cast(pl.Utf8, strict=False).str.strip_chars().str.zfill(7),
        )
        if "FANTASIA" not in df_st.columns:
            df_st = df_st.with_columns(pl.lit("").cast(pl.Utf8).alias("FANTASIA"))
        if "COMPETEN" not in df_st.columns:
            df_st = df_st.with_columns(pl.lit("").cast(pl.Utf8).alias("COMPETEN"))
        df_st = df_st.with_columns(
            pl.concat_str(
                pl.col("CNES").cast(pl.Utf8),
                pl.lit("-"),
                pl.col("FANTASIA").cast(pl.Utf8),
            ).alias("NO_ESTABELECIMENTO"),
        ).unique(subset=["CNES"], keep="last")
        return CNESHealthEstablishment(df=df_st)
