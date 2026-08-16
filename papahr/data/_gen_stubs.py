import polars as pl

# Stub counties vazio com schema correto — será populado pelo IBGE no primeiro uso
schema = {
    "id_municipio": pl.Utf8,
    "nome_municipio": pl.Utf8,
    "nome_microrregiao": pl.Utf8,
    "nome_mesorregiao": pl.Utf8,
    "id_estado": pl.Utf8,
    "nome_estado": pl.Utf8,
    "sigla_estado": pl.Utf8,
    "nome_regiao": pl.Utf8,
}
pl.DataFrame(schema=schema).write_parquet(__import__("pathlib").Path(__file__).with_name("counties.parquet"))

# Stub CADGER-BR vazio
cadger_schema = {"CNES": pl.Utf8, "FANTASIA": pl.Utf8, "COMPETEN": pl.Utf8}
pl.DataFrame(schema=cadger_schema).write_parquet(__import__("pathlib").Path(__file__).with_name("CADGER-BR.parquet"))
