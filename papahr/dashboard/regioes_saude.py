"""
regioes_saude.py — município → região de saúde (CIR) e macrorregião, a partir de
papahr/data/regioes_saude.parquet (gerado por scripts/build_regioes_saude.py: DATASUS base
territorial ou, na falta, a consolidação covid19br/UFV com os mesmos códigos CO_REGSAUD).

    from papahr.dashboard.regioes_saude import carregar_regioes_saude, juntar_regiao
    reg = carregar_regioes_saude()                 # COD_MUN | NOME_MUN | UF | CO_REGSAUD | NO_REGSAUD | CO_MACSAUD | NO_MACSAUD
    mun = juntar_regiao(municipios)                # acrescenta CO_REGSAUD / NO_REGSAUD à tabela de municípios

Sem o parquet, a reserva é a microrregião IBGE de papahr/data/counties.parquet (rotulada como
tal), para a interface continuar funcionando.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import polars as pl

ARQUIVO = Path(__file__).resolve().parents[1] / "data" / "regioes_saude.parquet"
COUNTIES = Path(__file__).resolve().parents[1] / "data" / "counties.parquet"
COLS = ["COD_MUN", "NOME_MUN", "UF", "CO_REGSAUD", "NO_REGSAUD", "CO_MACSAUD", "NO_MACSAUD"]


@lru_cache(maxsize=1)
def carregar_regioes_saude() -> pl.DataFrame:
    """Tabela completa (5.570 municípios, 450 regiões). Cache em memória."""
    if ARQUIVO.exists():
        df = pl.read_parquet(ARQUIVO)
        return df.select([c for c in COLS if c in df.columns]).with_columns(pl.col("COD_MUN").cast(pl.Utf8).str.slice(0, 6))
    if COUNTIES.exists():   # reserva: microrregião IBGE
        c = pl.read_parquet(COUNTIES)
        return c.select(
            pl.col("id_municipio").cast(pl.Utf8).str.slice(0, 6).alias("COD_MUN"),
            pl.col("nome_municipio").alias("NOME_MUN"), pl.col("sigla_estado").alias("UF"),
            pl.concat_str([pl.col("sigla_estado"), pl.lit("-"), pl.col("nome_microrregiao")]).alias("CO_REGSAUD"),
            pl.concat_str([pl.col("nome_microrregiao"), pl.lit(" (microrregião IBGE)")]).alias("NO_REGSAUD"),
            pl.lit(None, dtype=pl.Utf8).alias("CO_MACSAUD"), pl.col("nome_mesorregiao").alias("NO_MACSAUD"),
        )
    return pl.DataFrame(schema={c: pl.Utf8 for c in COLS})


def fonte_regioes() -> str:
    if ARQUIVO.exists():
        try:
            f = pl.read_parquet(ARQUIVO, columns=["FONTE"])
            return str(f["FONTE"][0]) if f.height else "regiões de saúde (CIR)"
        except Exception:  # noqa: BLE001
            return "regiões de saúde (CIR)"
    return "microrregiões IBGE (reserva)"


def juntar_regiao(municipios: pl.DataFrame) -> pl.DataFrame:
    """Acrescenta CO_REGSAUD, NO_REGSAUD, CO_MACSAUD, NO_MACSAUD (chave COD_MUN de 6 dígitos)."""
    reg = carregar_regioes_saude().select(["COD_MUN", "CO_REGSAUD", "NO_REGSAUD", "CO_MACSAUD", "NO_MACSAUD"])
    base = municipios.with_columns(pl.col("COD_MUN").cast(pl.Utf8).str.slice(0, 6))
    base = base.drop([c for c in ("CO_REGSAUD", "NO_REGSAUD", "CO_MACSAUD", "NO_MACSAUD") if c in base.columns])
    return base.join(reg, on="COD_MUN", how="left")


def regioes_da_uf(uf: str) -> pl.DataFrame:
    """CO_REGSAUD | NO_REGSAUD | n_mun da UF (sigla ou código de 2 dígitos), em ordem alfabética."""
    reg = carregar_regioes_saude()
    u = str(uf).strip().upper()
    sub = reg.filter(pl.col("COD_MUN").str.slice(0, 2) == u) if u.isdigit() else reg.filter(pl.col("UF") == u)
    return sub.group_by(["CO_REGSAUD", "NO_REGSAUD"]).len().rename({"len": "n_mun"}).sort("NO_REGSAUD")


def municipios_da_regiao(co_regsaud: str) -> pl.DataFrame:
    """COD_MUN | NOME_MUN dos municípios da região."""
    return carregar_regioes_saude().filter(pl.col("CO_REGSAUD") == str(co_regsaud)).select(["COD_MUN", "NOME_MUN"]).sort("NOME_MUN")
