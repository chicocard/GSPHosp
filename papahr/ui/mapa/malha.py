"""
malha.py — acesso à malha municipal embarcada (assets/malha_municipios.js).

Uso típico:
    from papahr.ui.mapa.malha import centroides_municipios
    cent = centroides_municipios(ufs=["31"])   # Polars: COD_MUN, NOME, LAT, LON

Serve para preencher centroides que o CNES não cobre (municípios sem
estabelecimento georreferenciado) e para a demonstração do widget.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import polars as pl

ASSETS = Path(__file__).resolve().parent / "assets"
ARQUIVO_MALHA = ASSETS / "malha_municipios.js"
_PREFIXO = "window.GSP_MALHA="


@lru_cache(maxsize=1)
def carregar_malha() -> dict:
    """Devolve o FeatureCollection embutido no JS (cache em memória)."""
    txt = ARQUIVO_MALHA.read_text(encoding="utf-8").strip()
    if not txt.startswith(_PREFIXO):
        raise ValueError(f"{ARQUIVO_MALHA} não começa com {_PREFIXO!r}")
    txt = txt[len(_PREFIXO):].rstrip(";").rstrip()
    return json.loads(txt)


def _centroide_vertices(geom: dict) -> tuple[float, float]:
    """Média dos vértices do maior anel (aproximação suficiente para distâncias)."""
    if geom["type"] == "Polygon":
        anel = geom["coordinates"][0]
    else:  # MultiPolygon: maior polígono
        anel = max(geom["coordinates"], key=lambda p: len(p[0]))[0]
    n = len(anel)
    lon = sum(p[0] for p in anel) / n
    lat = sum(p[1] for p in anel) / n
    return lat, lon


def centroides_municipios(ufs: Iterable[str] | None = None) -> pl.DataFrame:
    """COD_MUN (6 dígitos), NOME, LAT, LON de cada município da malha."""
    ufs = set(str(u) for u in ufs) if ufs else None
    rows = []
    for f in carregar_malha()["features"]:
        cod = f["properties"]["cod6"]
        if ufs and cod[:2] not in ufs:
            continue
        lat, lon = _centroide_vertices(f["geometry"])
        rows.append((cod, f["properties"]["nome"], lat, lon))
    return pl.DataFrame(rows, schema=["COD_MUN", "NOME", "LAT", "LON"], orient="row")


def completar_centroides(municipios: pl.DataFrame) -> pl.DataFrame:
    """
    Recebe a tabela de municípios do app (COD_MUN, NOME, LAT, LON, ...) e
    preenche LAT/LON nulos com o centroide da malha. Mantém as demais colunas.
    """
    base = centroides_municipios().rename({"LAT": "_lat_m", "LON": "_lon_m", "NOME": "_nome_m"})
    out = (
        municipios.with_columns(pl.col("COD_MUN").cast(pl.Utf8).str.slice(0, 6))
        .join(base, on="COD_MUN", how="left")
        .with_columns(
            pl.coalesce([pl.col("LAT"), pl.col("_lat_m")]).alias("LAT"),
            pl.coalesce([pl.col("LON"), pl.col("_lon_m")]).alias("LON"),
            pl.coalesce([pl.col("NOME"), pl.col("_nome_m")]).alias("NOME") if "NOME" in municipios.columns else pl.col("_nome_m").alias("NOME"),
        )
        .drop(["_lat_m", "_lon_m", "_nome_m"])
    )
    return out
