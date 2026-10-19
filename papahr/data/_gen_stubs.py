"""Gera APENAS o stub vazio do CADGER-BR (fallback de estabelecimentos).

ATENÇÃO: `counties.parquet` NÃO é mais um stub — contém os 5.571 municípios do
IBGE embarcados no pacote (baixados via CountiesRepository._download_ibge()).
Para atualizá-lo use:  papahr-cli prepare-aux --download-counties
"""
import pathlib

import polars as pl

cadger_schema = {"CNES": pl.Utf8, "FANTASIA": pl.Utf8, "COMPETEN": pl.Utf8}
pl.DataFrame(schema=cadger_schema).write_parquet(pathlib.Path(__file__).with_name("CADGER-BR.parquet"))
