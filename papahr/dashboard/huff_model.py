"""Modelo Gravitacional Espacial de Huff para Saúde Populacional.

Mede a força de atração de cada hospital sobre os municípios ao redor usando a
fórmula padrão ouro (ver relatorio_saude_populacional_dinamica.md):

        P_ij = (M_j / d_ij^beta) / SUM(M_k / d_ik^beta for k in hospitais)

Onde:
    M_j     = massa do hospital j (leitos, produção histórica, CMI ponderado)
    d_ij    = distância haversine (km) entre município i e hospital j
    beta    = decay exponencial. Alto = paciente viaja pouco (procedimentos
              ambulatoriais/primária); Baixo = paciente viaja para alta
              complexidade (cirurgias, oncologia).

Referência:
    Huff, D. L. (1963). Probability and parameter estimation in a dynamic model
    of consumer spatial behavior. Papers of the Regional Science Association.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import polars as pl

from papahr.logging_config import get_logger

log = get_logger(__name__)

# Constants ---------------------------------------------------------------
_EARTH_RADIUS_KM = 6371.0088
_INTRA_MUNICIPAL_MIN_KM = 1.0  # Evita divisão por zero

# Betas empíricos por complexidade (ver p. 3 do relatório)
BETA_AMBULATORIAL = 2.2    # PSF, UBS: pacientes não se deslocam muito
BETA_MEDIA          = 1.5   # Internação clínica, urgências geral
BETA_ALTA_COMPLEX   = 0.9   # Oncologia, cirurgia de grande porte, transplante


# ---------------------------------------------------------------------------
# Geometria
# ---------------------------------------------------------------------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância de Haversine (km) entre dois pontos WGS84. Em Python puro para
    permitir map_elements em Polars sem depender de pyproj/numpy."""
    if not (math.isfinite(lat1) and math.isfinite(lon1)
            and math.isfinite(lat2) and math.isfinite(lon2)):
        return float("nan")
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2.0) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2.0) ** 2)
    c = 2.0 * math.asin(math.sqrt(a))
    return _EARTH_RADIUS_KM * c


def haversine_km_polars(lat1: pl.Expr, lon1: pl.Expr,
                        lat2: pl.Expr, lon2: pl.Expr) -> pl.Expr:
    """Versão vetorizada Polars (mais rápida em colunas grandes)."""
    phi1 = lat1.cast(pl.Float64) * math.pi / 180.0
    phi2 = lat2.cast(pl.Float64) * math.pi / 180.0
    dphi = (lat2.cast(pl.Float64) - lat1.cast(pl.Float64)) * math.pi / 180.0
    dlmb = (lon2.cast(pl.Float64) - lon1.cast(pl.Float64)) * math.pi / 180.0
    a = (dphi / 2.0).sin().pow(2) + phi1.cos() * phi2.cos() * (dlmb / 2.0).sin().pow(2)
    return 2.0 * _EARTH_RADIUS_KM * a.sqrt().arcsin()


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class HospitalNode:
    cnes: str
    nome: str
    latitude: float
    longitude: float
    massa: float = 0.0
    tipo: str = ""
    municipio_cod: str = ""
    municipio_nome: str = ""
    uf: str = ""


@dataclass(slots=True)
class MunicipioNode:
    cod_ibge: str
    nome: str
    latitude: float
    longitude: float
    populacao: float = 0.0
    uf: str = ""


@dataclass(slots=True)
class HuffResult:
    od_matrix: pl.DataFrame = field(default_factory=pl.DataFrame)
    hospital_attraction: pl.DataFrame = field(default_factory=pl.DataFrame)
    catchment_profile: pl.DataFrame = field(default_factory=pl.DataFrame)

    def summary(self) -> dict[str, float]:
        if self.hospital_attraction.is_empty():
            return {}
        return {
            "hospitais_modelados": self.hospital_attraction.height,
            "municipios_modelados": self.od_matrix.select("MUN_ORIGEM").unique().height,
            "raio_medio_50pct_km":
                float(self.catchment_profile["raio_50pct_km"].mean() or 0),
            "raio_medio_80pct_km":
                float(self.catchment_profile["raio_80pct_km"].mean() or 0),
        }


# ---------------------------------------------------------------------------
def modelo_huff(hospitais: Sequence[HospitalNode],
                municipios: Sequence[MunicipioNode],
                beta: float = BETA_MEDIA,
                chunk_size: int = 5000) -> HuffResult:
    """Matriz Origem-Destino probabilística completa.

    Performance: (len(municipios) * len(hospitais)) pares. Para ~5.600
    municípios e ~100 hospitais = 560k pares: Polars lida em <1s.
    Para ~5.600 e ~600 hospitais = 3,3M: use chunk_size.
    """
    if not hospitais or not municipios:
        return HuffResult(pl.DataFrame(), pl.DataFrame(), pl.DataFrame())

    log.info(f"[HUFF] beta={beta:.2f} | {len(hospitais)} hospitais x {len(municipios)} municípios")

    # 1. Cross-join Polars (municípios × hospitais)
    df_mun = pl.DataFrame([
        pl.Series("MUN_ORIGEM", [m.cod_ibge for m in municipios]),
        pl.Series("LAT_MUN", [m.latitude for m in municipios], dtype=pl.Float64),
        pl.Series("LON_MUN", [m.longitude for m in municipios], dtype=pl.Float64),
        pl.Series("POP_MUN", [m.populacao for m in municipios], dtype=pl.Float64),
    ])
    df_hos = pl.DataFrame([
        pl.Series("CNES", [h.cnes for h in hospitais]),
        pl.Series("NM_HOSPITAL", [h.nome for h in hospitais]),
        pl.Series("LAT_HOSP", [h.latitude for h in hospitais], dtype=pl.Float64),
        pl.Series("LON_HOSP", [h.longitude for h in hospitais], dtype=pl.Float64),
        pl.Series("MASSA_HOSP", [h.massa for h in hospitais], dtype=pl.Float64),
    ]).filter(pl.col("MASSA_HOSP") > 0).with_columns(
        pl.col("MASSA_HOSP").fill_null(0.0)
    )

    # Cross-join
    rows_target = df_mun.height * df_hos.height
    if rows_target <= chunk_size * 4:
        cross = df_mun.join(df_hos, how="cross")
        cross = _compute_chunk(cross, beta)
        od_matrix = cross
    else:
        chunks: list[pl.DataFrame] = []
        mun_parts = [df_mun[i:i + chunk_size] for i in range(0, df_mun.height, chunk_size)]
        for p in mun_parts:
            c = p.join(df_hos, how="cross")
            chunks.append(_compute_chunk(c, beta))
        od_matrix = pl.concat(chunks, rechunk=True)

    # 2. Soma das forças por município → probabilidade
    od_matrix = od_matrix.with_columns(
        pl.col("forca_atracao")
        .sum()
        .over("MUN_ORIGEM")
        .alias("soma_atracao_residencia")
    ).with_columns(
        pl.when(pl.col("soma_atracao_residencia") > 0)
        .then(pl.col("forca_atracao") / pl.col("soma_atracao_residencia"))
        .otherwise(0.0)
        .alias("probabilidade_escolha")
    )

    # 3. Atração agregada por hospital
    hospital_attraction = od_matrix.group_by(["CNES", "NM_HOSPITAL"]).agg(
        pl.col("forca_atracao").mean().alias("forca_media"),
        pl.col("distancia_km").weighted_mean("probabilidade_escolha").alias("distancia_ponderada_km"),
        (pl.col("probabilidade_escolha") > 0.01).sum().alias("mun_atendidos"),
        pl.col("probabilidade_escolha").max().alias("probabilidade_max"),
    ).sort("forca_media", descending=True)

    # 4. Perfil de área de cobertura (raio que acumula X% da probabilidade)
    catchment_profile = _catchment_radii(od_matrix, [50.0, 80.0])

    log.info(f"[HUFF] Finalizado | O-D={od_matrix.height} linhas")
    return HuffResult(od_matrix, hospital_attraction, catchment_profile)


def _compute_chunk(cross: pl.DataFrame, beta: float) -> pl.DataFrame:
    # Distância haversine (evita d=0 em intra-municipais)
    d = haversine_km_polars(
        cross["LAT_MUN"], cross["LON_MUN"], cross["LAT_HOSP"], cross["LON_HOSP"])
    d_safe = pl.when(d < _INTRA_MUNICIPAL_MIN_KM)\
        .then(_INTRA_MUNICIPAL_MIN_KM)\
        .otherwise(d).alias("distancia_km")

    forca = (pl.col("MASSA_HOSP") / d_safe.pow(beta)).alias("forca_atracao")
    return cross.with_columns([d_safe, forca]).drop([
        "LAT_MUN", "LON_MUN", "LAT_HOSP", "LON_HOSP", "MASSA_HOSP"])


def _catchment_radii(od: pl.DataFrame, percentis: Iterable[float]) -> pl.DataFrame:
    """Para cada hospital, ordena municípios por distância crescente e encontra
    o raio mínimo em km que acumula p% da massa probabilística total."""
    frames: list[pl.DataFrame] = []
    od_sorted = od.sort(["CNES", "distancia_km"])
    for cnes, grp in od_sorted.group_by("CNES", maintain_order=True):
        cumprob = grp["probabilidade_escolha"].cum_sum()
        total = cumprob[-1] if len(cumprob) else 0.0
        if total <= 0:
            continue
        linha = {"CNES": cnes[0] if isinstance(cnes, tuple) else cnes}
        dists = grp["distancia_km"].to_list()
        for pct in percentis:
            target = total * (pct / 100.0)
            idx = (cumprob >= target).arg_max()
            raio = float(dists[idx]) if idx is not None and idx < len(dists) else float(dists[-1])
            linha[f"raio_{int(pct)}pct_km"] = round(raio, 2)
            linha[f"mun_{int(pct)}pct"] = int(idx) + 1
        frames.append(pl.DataFrame(linha))
    return pl.concat(frames, rechunk=True, how="diagonal") if frames else pl.DataFrame()
