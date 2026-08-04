"""Dashboard de Saúde Populacional — módulo de orquestração.

Pipeline:
    production DF (SIA/SIH CNES/PA) → compute_dashboard()
        ├─ KPI globals (n_hospitais, valor_total, LOS, ICSAP%, readmissao%est)
        ├─ Estratificação de Risco (LACE + CMI + estratos)
        ├─ Eficiência Clínica (LOS O/E, custo O/E, margem, CMI)
        ├─ Integração Territorial (ICSAP bruta + porcentagem + Modelo HUFF gravitacional)
        ├─ Transição de Cuidado (10 linhas de cuidado: altas, contato48h, readmissão30d)
        ├─ Série temporal (ano×mes: internações, taxa ICSAP, LOS médio)
        └─ Top10 procedimentos e especialidades + alertas
"""
from __future__ import annotations

from papahr.dashboard.cnes_loader import CNESData, get_cnes_data
from papahr.dashboard.huff_model import (
    BETA_ALTA_COMPLEX,
    BETA_AMBULATORIAL,
    BETA_MEDIA,
    HuffResult,
    HospitalNode,
    MunicipioNode,
    haversine_km,
    modelo_huff,
)
from papahr.dashboard.metrics import (
    DashboardMetrics,
    ICSAP_PATTERNS,
    LINHAS_CUIDADO,
    compute_dashboard,
)

__all__ = [
    "BETA_ALTA_COMPLEX",
    "BETA_AMBULATORIAL",
    "BETA_MEDIA",
    "CNESData",
    "DashboardMetrics",
    "HuffResult",
    "HospitalNode",
    "ICSAP_PATTERNS",
    "LINHAS_CUIDADO",
    "MunicipioNode",
    "compute_dashboard",
    "get_cnes_data",
    "haversine_km",
    "modelo_huff",
]
