"""Dados geográficos SUS (hierarquia UF → Macrorregião → DRS / Região de Saúde)."""
from __future__ import annotations

from papahr.geo.reference import (
    TODAS_UFS,
    UF_CODIBGE_TO_SIGLA,
    UF_POR_SIGLA,
    get_macrorregioes,
    get_ufs,
    lista_drs,
    lista_macrorregioes,
    match_macrorregiao_por_texto,
)

__all__ = [
    "TODAS_UFS",
    "UF_CODIBGE_TO_SIGLA",
    "UF_POR_SIGLA",
    "get_macrorregioes",
    "get_ufs",
    "lista_drs",
    "lista_macrorregioes",
    "match_macrorregiao_por_texto",
]
