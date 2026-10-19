"""Contrato de colunas do Dashboard.

Declara, por aba, quais colunas do dataset analítico são necessárias e quais
nomes alternativos são aceitos. É usado logo após o pipeline/compute para
avisar o usuário, em texto claro, POR QUE uma aba está vazia — em vez de uma
aba em branco sem explicação.

Regra: o dataset consumido pelo dashboard é o analítico (colunas brutas dos
microdados + enriquecimento), nunca a visão de exportação com rótulos legíveis.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

# Cada requisito é uma tupla de nomes alternativos (qualquer um satisfaz).
# A comparação ignora maiúsculas/minúsculas e troca espaço/hífen por "_".
_Req = tuple[str, ...]


@dataclass(slots=True)
class TabContract:
    tab: str
    required: dict[str, _Req]           # descrição humana -> alternativas aceitas
    optional: dict[str, _Req] = field(default_factory=dict)


CNES: _Req = ("CNES", "CO_CNES", "PA_CODUNI", "SP_CNES", "cnes")
CID: _Req = ("DIAG_PRINC", "PA_CIDPRI", "SP_CIDPRI", "CID_PRINCIPAL", "DIAGNOSTICO_PRINCIPAL")
VALOR: _Req = ("VAL_TOT", "PA_VALAPR", "SP_VALATO", "VAL_TOTAL", "Valor Total", "Valor Aprovado")
MUN_RES: _Req = ("MUNIC_RES", "PA_MUNPCN", "SP_M_PAC", "municipio_paciente", "CO_MUNICIPIO")
LOS: _Req = ("QT_DIARIAS", "DIAS_PERM", "QT_PERMANENCIA")
DT_INTER: _Req = ("DT_INTER", "SP_DTINTER", "DT_INTERNACAO")
DT_SAIDA: _Req = ("DT_SAIDA", "SP_DTSAIDA", "DT_ALTA")
COMPETENCIA: _Req = ("ANO_CMPT", "PA_CMP", "SP_AA", "ANOMES_CMPT", "ANO")
PROC: _Req = ("PROC_REA", "PA_PROC_ID", "SP_PROCREA", "CO Procedimentos realizados")
ACUIDADE: _Req = ("CAR_INT", "PA_CATEND", "carater_atendimento")
IDADE: _Req = ("IDADE", "PA_IDADE")


CONTRACTS: dict[str, list[TabContract]] = {
    # SIH-RD / SIH-RJ
    "SIH": [
        TabContract("KPIs", {"estabelecimento (CNES)": CNES, "valor": VALOR}),
        TabContract("Risco e Demanda", {"estabelecimento (CNES)": CNES, "CID principal": CID,
                                        "permanência": LOS},
                    optional={"caráter da internação": ACUIDADE, "idade": IDADE}),
        TabContract("Eficiência Clínica", {"estabelecimento (CNES)": CNES, "permanência": LOS,
                                           "valor": VALOR}),
        TabContract("Território", {"estabelecimento (CNES)": CNES, "município de residência": MUN_RES,
                                   "CID principal": CID}),
        TabContract("Transição de Cuidado", {"estabelecimento (CNES)": CNES, "data internação": DT_INTER,
                                             "data saída": DT_SAIDA, "CID principal": CID}),
        TabContract("Visão Geral / Série", {"estabelecimento (CNES)": CNES, "competência": COMPETENCIA,
                                            "procedimento": PROC}),
        TabContract("Benchmarking", {"estabelecimento (CNES)": CNES, "valor": VALOR, "permanência": LOS}),
    ],
    # SIA-PA
    "SIA": [
        TabContract("KPIs", {"estabelecimento (CNES)": CNES, "valor": VALOR}),
        TabContract("Território", {"estabelecimento (CNES)": CNES, "município de residência": MUN_RES}),
        TabContract("Visão Geral / Série", {"estabelecimento (CNES)": CNES, "competência": COMPETENCIA,
                                            "procedimento": PROC}),
        TabContract("Benchmarking", {"estabelecimento (CNES)": CNES, "valor": VALOR}),
    ],
}


def _norm(c: str) -> str:
    return str(c).strip().lower().replace(" ", "_").replace("-", "_")


def _has(cols_norm: set[str], alternatives: _Req) -> bool:
    return any(_norm(a) in cols_norm for a in alternatives)


def family_of(system_kind: str) -> str:
    return "SIA" if (system_kind or "").upper().replace("-", "_").startswith("SIA") else "SIH"


def validate_dashboard_columns(df: pl.DataFrame | list[str], system_kind: str) -> dict[str, list[str]]:
    """Retorna {aba: [requisitos ausentes]} apenas para abas com algo faltando."""
    cols = df.columns if hasattr(df, "columns") else list(df)
    cols_norm = {_norm(c) for c in cols}
    missing: dict[str, list[str]] = {}
    for tc in CONTRACTS.get(family_of(system_kind), []):
        falta = [desc for desc, alts in tc.required.items() if not _has(cols_norm, alts)]
        if falta:
            missing[tc.tab] = falta
    return missing


def contract_alerts(df: pl.DataFrame | list[str], system_kind: str) -> list[str]:
    """Mensagens prontas para a área de alertas do dashboard."""
    missing = validate_dashboard_columns(df, system_kind)
    out: list[str] = []
    for tab, falta in missing.items():
        out.append(
            f"Aba '{tab}' incompleta: faltam coluna(s) de {', '.join(falta)} no dataset. "
            "Use o Parquet/DuckDB gerado pelo pipeline (dataset analítico), não o CSV de exportação."
        )
    return out


__all__ = ["CONTRACTS", "TabContract", "validate_dashboard_columns", "contract_alerts", "family_of"]
