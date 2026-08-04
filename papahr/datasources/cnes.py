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

_FILE_RE = re.compile(r"^(?P<prefix>[A-Z]{2})(?P<uf>[A-Z]{2})(?P<ym>\d{4})\.dbc$", re.I)
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
        """Carrega estabelecimentos de saúde (CNES/ST + CADGER).

        Quando `state_abbr` é fornecido (ex: ["MG", "SP"]), o cache é filtrado
        e os downloads são feitos apenas para as UFs selecionadas (evita baixar
        27 UFs quando só 1 é necessária).
        """
        st_dir = self.base_cache / "ST"
        dbcs = sorted(st_dir.glob("*.dbc")) if st_dir.exists() else []
        if state_abbr and "ALL" not in state_abbr:
            uf_set = {s.upper() for s in state_abbr}
            dbcs = [
                p for p in dbcs
                if len(p.name) >= 8 and p.name[2:4].upper() in uf_set
            ]
        if not dbcs:
            log.info("CNES/ST nao encontrado em cache, baixando", state_abbr=state_abbr)
            if newer:
                self.download_type("ST", newer=True, state_abbr=state_abbr)
            elif year_start and month_start and year_end and month_end:
                self.download_type(
                    "ST",
                    year_start=year_start, month_start=month_start,
                    year_end=year_end, month_end=month_end,
                    state_abbr=state_abbr,
                )
            else:
                self.download_type("ST", newer=True, state_abbr=state_abbr)
            dbcs = sorted(st_dir.glob("*.dbc"))
            if state_abbr and "ALL" not in state_abbr:
                uf_set = {s.upper() for s in state_abbr}
                dbcs = [
                    p for p in dbcs
                    if len(p.name) >= 8 and p.name[2:4].upper() in uf_set
                ]
        log.info("Lendo CNES/ST", n_files=len(dbcs))
        df_st = read_many_dbc([str(p) for p in dbcs])
        if df_st.is_empty():
            raise RuntimeError("Nao foi possivel ler arquivos CNES/ST")
        df_st = df_st.select([c for c in ("CNES", "FANTASIA", "COMPETEN") if c in df_st.columns])
        df_cadger = self._load_cadger()
        if not df_cadger.is_empty():
            df_st = df_st.join(df_cadger, on="CNES", how="left", coalesce=True)
            if "FANTASIA_right" in df_st.columns:
                df_st = df_st.with_columns(
                    pl.coalesce(pl.col("FANTASIA"), pl.col("FANTASIA_right")).alias("FANTASIA")
                ).drop("FANTASIA_right")
        df_st = df_st.with_columns(
            pl.col("CNES").cast(pl.Utf8).str.strip_chars(),
            pl.concat_str(
                [pl.col("CNES").cast(pl.Utf8), pl.lit("-"), pl.col("FANTASIA").cast(pl.Utf8)],
            ).alias("NO_ESTABELECIMENTO"),
        ).unique(subset=["CNES"], keep="last")
        return CNESHealthEstablishment(df=df_st)
