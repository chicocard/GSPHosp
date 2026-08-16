"""Extrai amostra de dados CNES para entender o schema do dashboard."""
from __future__ import annotations
import sys
import io
import polars as pl
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(r"C:\DICIONARIO_DADOS")
OUT = Path(r"D:\papapapa\PaPAHR-Python\cnes_summary.txt")

lines: list[str] = []

def show(name: str, df: pl.DataFrame, cols: list[str] | None = None, n: int = 5) -> None:
    lines.append(f"\n=== {name} (shape={df.shape}) ===")
    view = df if cols is None else df.select(cols)
    lines.append(view.head(n).write_csv(separator=";", quote_style="always"))
    lines.append("---")

estab = pl.read_csv(ROOT / "tbEstabelecimento202512.csv", separator=";",
                    encoding="latin-1", infer_schema_length=5000,
                    ignore_errors=True, truncate_ragged_lines=True,
                    try_parse_dates=False)

lines.append(f"Todos colunas Estab: {list(estab.columns)}")

show("tbEstabelecimento", estab,
     [c for c in ["CO_CNES", "NO_FANTASIA", "NO_RAZAO_SOCIAL", "CO_ESTADO_GESTOR",
                  "CO_MUNICIPIO_GESTOR", "NU_LATITUDE", "NU_LONGITUDE",
                  "CO_TIPO_ESTABELECIMENTO", "CO_TIPO_UNIDADE", "TP_UNIDADE",
                  "TP_ESTAB_SEMPRE_ABERTO", "ST_CONEXAO_INTERNET", "CO_NATUREZA_JUR"]
      if c in estab.columns])

leitos = pl.read_csv(ROOT / "tbLeito202512.csv", separator=";",
                     encoding="latin-1", infer_schema_length=5000,
                     ignore_errors=True, truncate_ragged_lines=True)
lines.append(f"\nColunas tbLeito: {list(leitos.columns)}")
show("tbLeito head", leitos)

# agrega leitos
q_cols = [c for c in leitos.columns if c.startswith("QT_")]
agg_exprs = []
for c in q_cols:
    agg_exprs.append(pl.col(c).cast(pl.Float64, strict=False).fill_null(0).sum().alias(f"soma_{c}"))
agg_exprs.append(pl.count().alias("tipos_leito_distintos"))
agg_exprs.insert(0, pl.col("TP_LEITO").cast(pl.Utf8).unique().count().alias("n_tipos_leito") if "TP_LEITO" in leitos.columns else pl.lit(0))
if "CO_CNES" in leitos.columns:
    la = leitos.group_by("CO_CNES").agg(agg_exprs if agg_exprs else pl.count())
    show("leitos_agregados_por_cnes_amostra", la, n=8)

muni = pl.read_csv(ROOT / "tbMunicipio202512.csv", separator=";",
                   encoding="latin-1", infer_schema_length=5000,
                   ignore_errors=True, truncate_ragged_lines=True)
lines.append(f"\nColunas tbMunicipio: {list(muni.columns)}")
show("tbMunicipio head", muni, n=10)

# Tipo Estabelecimento
if ROOT.joinpath("tbTipoEstabelecimento202512.csv").exists():
    te = pl.read_csv(ROOT / "tbTipoEstabelecimento202512.csv", separator=";",
                     encoding="latin-1", infer_schema_length=1000, ignore_errors=True)
    show("tbTipoEstabelecimento (dicionário)", te)

OUT.write_text("\n".join(lines), encoding="utf-8", errors="replace")
print("Escrito:", OUT, "tamanho:", OUT.stat().st_size)

