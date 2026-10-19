"""Dados de municípios brasileiros (counties) via API IBGE ou arquivo embutido."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
import polars as pl
from tenacity import retry, stop_after_attempt, wait_exponential

from papahr.config import get_settings
from papahr.logging_config import get_logger

log = get_logger(__name__)

CACHE_FILENAME = "counties.parquet"

_COUNTY_SCHEMA = {
    "id_municipio": pl.Utf8,
    "nome_municipio": pl.Utf8,
    "nome_microrregiao": pl.Utf8,
    "nome_mesorregiao": pl.Utf8,
    "id_estado": pl.Utf8,
    "nome_estado": pl.Utf8,
    "sigla_estado": pl.Utf8,
    "nome_regiao": pl.Utf8,
}


@dataclass(slots=True)
class CountyData:
    df: pl.DataFrame

    def filter_by_state(self, ufs: list[str]) -> pl.DataFrame:
        return self.df.filter(pl.col("sigla_estado").is_in(ufs))

    def rename_for_estabelecimento(self) -> pl.DataFrame:
        return self.df.select(
            pl.col("id_municipio"),
            pl.col("nome_municipio").alias("municipio_estabelecimento"),
            pl.col("nome_regiao").alias("nome_regiao_estabelecimento"),
        )

    def get_ids(self) -> list[str]:
        return self.df["id_municipio"].to_list()


class CountiesRepository:
    """Gerencia dados de municípios com cache e fallback."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.cache_path: Path = self.settings.cache_dir / CACHE_FILENAME

    @property
    def _data_file(self) -> Path:
        return Path(__file__).resolve().parent.parent / "data" / "counties.parquet"

    @property
    def _user_data_file(self) -> Path:
        """Cópia persistente no perfil do usuário (sobrevive à limpeza do cache)."""
        return self.settings.data_dir / CACHE_FILENAME

    def _save_everywhere(self, df: pl.DataFrame) -> None:
        for dest in (self.cache_path, self._user_data_file):
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                df.write_parquet(dest)
            except Exception as e:  # noqa: BLE001
                log.warn("Nao foi possivel gravar cache de municipios", path=str(dest), err=str(e))

    def load(self, *, download: bool = False) -> CountyData:
        """Carrega municípios, tentando várias fontes.

        Um parquet com 0 linhas (stub de empacotamento) é tratado como AUSENTE:
        antes ele era aceito e toda a geografia do output saía vazia.
        """
        if download:
            try:
                df = self._download_ibge()
                self._save_everywhere(df)
                return CountyData(df=df)
            except Exception as e:
                log.warn("Falhou download IBGE, tentando caches", err=str(e))
        for candidate in (self.cache_path, self._user_data_file, self._data_file):
            if not candidate.exists():
                continue
            try:
                df = pl.read_parquet(candidate)
            except Exception as e:  # noqa: PERF203
                log.warn("Cache corrompido", path=str(candidate), err=str(e))
                continue
            if df.is_empty():
                log.warn("Cache de municipios vazio (stub); ignorando", path=str(candidate))
                continue
            return CountyData(df=df)
        if download:
            # já tentou baixar acima e falhou: devolve vazio em vez de recursão infinita
            log.error("Municipios IBGE indisponiveis (sem rede e sem cache)")
            return CountyData(df=pl.DataFrame(schema=_COUNTY_SCHEMA))
        log.warn("Nenhum cache de municipios disponivel, baixando do IBGE")
        return self.load(download=True)

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=15))
    def _download_ibge(self) -> pl.DataFrame:
        url = f"{self.settings.ibge_api_base}/localidades/municipios"
        log.info("Baixando municipios IBGE", url=url)
        with httpx.Client(timeout=self.settings.http_timeout) as c:
            r = c.get(url, follow_redirects=True)
            r.raise_for_status()
            raw = r.json()
        rows = []
        for m in raw:
            microrregiao = m.get("microrregiao") or {}
            meso = microrregiao.get("mesorregiao") or {}
            uf = meso.get("UF") or {}
            regiao = uf.get("regiao") or {}
            rows.append(
                {
                    "id_municipio": str(m.get("id", ""))[:6],
                    "nome_municipio": m.get("nome", ""),
                    "nome_microrregiao": microrregiao.get("nome", ""),
                    "nome_mesorregiao": meso.get("nome", ""),
                    "id_estado": str(uf.get("id", "")),
                    "nome_estado": uf.get("nome", ""),
                    "sigla_estado": uf.get("sigla", ""),
                    "nome_regiao": regiao.get("nome", ""),
                }
            )
        df = pl.DataFrame(rows, schema=_COUNTY_SCHEMA)
        log.info("Municipios carregados", n=len(df))
        return df
