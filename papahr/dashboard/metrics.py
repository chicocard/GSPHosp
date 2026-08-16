"""Pilares de métricas para Saúde Populacional.

Segue a estrutura do relatorio_saude_populacional_dinamica.md:

  1. ESTRATIFICAÇÃO DE RISCO / DEMANDA
       · Índice LACE (Length of stay · Acuity · Charlson · ED visits)
       · Case Mix Index (CMI) - complexidade média ponderada
       · Estratos Alto/Médio/Baixo e %Custo / %Frequência PS

  2. EFICIÊNCIA CLÍNICA / VALOR (BPCI/DRG)
       · Tempo Permanência Observado/Esperado (O/E LOS)
       · Custo Médio por Episódio (janela 30-90 dias)
       · Margem Financeira Estimada por DRG

  3. INTEGRAÇÃO TERRITORIAL / PRESSÃO EXTERNA
       · ICSAP (Internações por Condições Sensíveis à APS)
       · Modelo Gravitacional Huff (força atrativa + decay beta)
       · Fluxo Origin-Destino (MUNIC_RES → MUNIC_MOV)

  4. TRANSIÇÃO DE CUIDADO / PÓS-ALTA
       · Taxa de Readmissão 30d (por linha de cuidado/DRG)
       · % Contato Efetivo em 48h pós-alta
       · Coorte: ICC, DPOC, Artroplastia de Quadril, etc.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

import polars as pl

from papahr.logging_config import get_logger
from papahr.dashboard.huff_model import (
    BETA_ALTA_COMPLEX,
    BETA_AMBULATORIAL,
    BETA_MEDIA,
    HuffResult,
    HospitalNode,
    MunicipioNode,
    modelo_huff,
)

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# ICSAP - Lista brasileira (Portaria nº 2.206/2017 MS/SCTIE/DEIDT)
# 16 grupos de CID-10 de condições evitáveis (lista LITERAL do Manual §4.2)
# ---------------------------------------------------------------------------
ICSAP_PATTERNS: dict[str, tuple[str, ...]] = {
    "Diabetes melito":                     ("E10", "E11", "E12", "E13", "E14"),
    "Asma":                                ("J45", "J46"),
    "DPOC / Bronquiolite":                 ("J40", "J41", "J42", "J43", "J44", "J47"),
    "Hipertensão arterial":                ("I10", "I11", "I12", "I13", "I15"),
    "Insuficiência cardíaca":              ("I50",),
    "Doença isquêmica do coração":         ("I20", "I21", "I22", "I23", "I24", "I25"),
    "Cerebrovascular":                     ("I60", "I61", "I62", "I63", "I64", "I65", "I66", "I67", "I68", "I69"),
    "Insuficiência renal":                 ("N17", "N18", "N19"),
    "Infecções do trato urinário":         ("N10", "N11", "N12", "N30", "N34", "N39", "N70", "N71", "N72", "N73"),
    "Infecções respiratórias (exceto asma/DPOC)":
        ("J10", "J11", "J12", "J13", "J14", "J15", "J16", "J17", "J18", "J20", "J21", "J22"),
    "Doenças da nutrição e anemia":        ("D50", "D51", "D52", "D53", "E40", "E41", "E42", "E43", "E45", "E55", "E61"),
    "Doença péptica e intestino":          ("K20", "K21", "K22", "K23", "K24", "K25", "K26", "K27", "K28", "K29", "K30", "K31", "K52", "K58", "K59"),
    "Prevenível com vacinação":            ("A15", "A16", "A17", "A18", "A19", "A33", "A34", "A35", "A36", "A37", "A80", "B05", "B06", "B16", "B26"),
}

# Pesos Charlson simplificados (adaptado Deyo-CCI 1987, ICD-10)
CHARLSON_WEIGHTS: dict[str, int] = {
    "I20": 0, "I21": 1, "I22": 1, "I23": 1, "I24": 1, "I25": 1,                # Doença miocárdica
    "J43": 1, "J44": 1, "J47": 1,                                              # DPOC
    "I85": 0, "I86": 1, "K70": 1, "K71": 1, "K72": 1, "K73": 1, "K74": 1,      # Doença hepática leve/mod
    "I10": 0, "I11": 0, "I12": 0, "I13": 0, "I15": 1,                          # Doença cerebrovascular e IAM
    "F00": 1, "F01": 1, "F02": 1, "F03": 1, "G30": 1, "G31": 1,                # Demência
    "J40": 1, "J41": 1, "J42": 1, "J45": 1, "J46": 1,                          # Doença de Pneumo crônica
    "M05": 1, "M06": 1, "M07": 1, "M08": 1, "M09": 1,                          # Doença Conectivo
    "K25": 1, "K26": 1, "K27": 1, "K28": 1,                                    # Úlcera péptica
    "E78": 0,                                                                   # dislipidemia
    "N18": 1, "N19": 2,                                                         # Doença renal (grave=2)
    "C00": 2, "C01": 2, "C02": 2, "C03": 2, "C04": 2, "C05": 2, "C06": 2, "C07": 2,
    "C08": 2, "C09": 2, "C10": 2, "C11": 2, "C12": 2, "C13": 2, "C14": 2, "C15": 2,
    "C16": 2, "C17": 2, "C18": 2, "C19": 2, "C20": 2, "C21": 2, "C22": 2, "C23": 2,
    "C24": 2, "C25": 2, "C26": 2, "C30": 2, "C31": 2, "C32": 2, "C33": 2, "C34": 2,
    "C37": 2, "C38": 2, "C39": 2, "C40": 2, "C41": 2, "C43": 2, "C45": 2, "C46": 2,
    "C47": 2, "C48": 2, "C49": 2, "C50": 2, "C51": 2, "C52": 2, "C53": 2, "C54": 2,
    "C55": 2, "C56": 2, "C57": 2, "C58": 2, "C59": 2, "C60": 2, "C61": 2, "C62": 2,
    "C63": 2, "C64": 2, "C65": 2, "C66": 2, "C67": 2, "C68": 2, "C69": 2, "C70": 2,
    "C71": 2, "C72": 2, "C73": 2, "C74": 2, "C75": 2, "C76": 2, "C77": 2, "C78": 2, "C79": 2,
}

# Linhas de cuidado pós-alta (DRG/CID alvo)
LINHAS_CUIDADO: dict[str, tuple[str, ...]] = {
    "Insuficiência Cardíaca":       ("I50",),
    "DPOC / Asma descompensada":    ("J44", "J45", "J46"),
    "Artroplastia de Quadril":      ("M16", "Z9664"),
    "Artroplastia de Joelho":       ("M17", "Z9665"),
    "IAM":                          ("I21", "I22", "I23"),
    "AVC":                          ("I63", "I61", "I64"),
    "Pneumonia adquirida comunidade": ("J12", "J13", "J14", "J15", "J16", "J17", "J18"),
    "Sepse / Choque séptico":       ("A40", "A41", "R57", "R65"),
    "Fratura de fêmur proximal":    ("S72",),
    "Câncer (sem metástase)":       ("C15", "C16", "C18", "C19", "C20", "C33", "C34", "C50", "C53", "C61", "C67"),
}

# Benchmarks LOS esperado por linha de cuidado (dias, literatura BRASILEIRA)
LOS_BENCHMARK: dict[str, float] = {
    "Insuficiência Cardíaca":        5.5,
    "DPOC / Asma descompensada":     5.0,
    "Artroplastia de Quadril":       4.0,
    "Artroplastia de Joelho":        3.8,
    "IAM":                           5.5,
    "AVC":                           7.5,
    "Pneumonia adquirida comunidade": 6.0,
    "Sepse / Choque séptico":        8.5,
    "Fratura de fêmur proximal":     7.0,
    "Câncer (sem metástase)":        5.0,
}

# Custo médio referência (R$), valores atualizados para 2024-SUS-TABELA SIGTAP (Brasil)
CUSTO_BENCHMARK: dict[str, float] = {
    "Insuficiência Cardíaca":        4_800.0,
    "DPOC / Asma descompensada":     3_500.0,
    "Artroplastia de Quadril":       9_800.0,
    "Artroplastia de Joelho":        9_200.0,
    "IAM":                           9_100.0,
    "AVC":                          11_300.0,
    "Pneumonia adquirida comunidade": 4_500.0,
    "Sepse / Choque séptico":       16_400.0,
    "Fratura de fêmur proximal":     8_500.0,
    "Câncer (sem metástase)":       13_200.0,
}

# Readmissão 30d benchmark (%)
READMISSAO_BENCHMARK: dict[str, float] = {
    "Insuficiência Cardíaca":        22.0,
    "DPOC / Asma descompensada":     18.0,
    "Artroplastia de Quadril":        5.0,
    "Artroplastia de Joelho":         4.5,
    "IAM":                           15.0,
    "AVC":                           16.0,
    "Pneumonia adquirida comunidade": 12.0,
    "Sepse / Choque séptico":        20.0,
    "Fratura de fêmur proximal":     11.0,
    "Câncer (sem metástase)":        14.0,
}



# ---------------------------------------------------------------------------
# Pesos de DRG Brasil (CMI — 1250 grupos do padrão TABELA DRG BRASIL).
# Placeholder: para usar valores reais, substitua pelo dict:
#   _WEIGHTS_DRG["DRG_001"] = 1.245
#   _WEIGHTS_DRG["DRG_003"] = 0.582
# Referência: Portaria GM/MS nº 1.107/2022 que institui a tabela DRG-BR.
# ---------------------------------------------------------------------------
_WEIGHTS_DRG: dict[str, float] = {
    # TODO: preencher com os 1250 pesos de DRG Brasil quando a tabela for integrada
}

# Benchmarks de LOS, Custo e Readmissão para as 10 Linhas de Cuidado (literatura BRASILEIRA)
# Nome oficial conforme Manual §4.4 "10 Linhas de Cuidado Hospitalar Prioritárias"
_BR_BENCHMARKS_LINHAS_CUIDADO: dict[str, dict[str, float]] = {
    "Insuficiência Cardíaca": {
        "los_dias": 5.5, "custo_R$": 4_800, "readmissao_30d_pct": 22.0,
    },
    "DPOC / Asma descompensada": {
        "los_dias": 5.0, "custo_R$": 3_500, "readmissao_30d_pct": 18.0,
    },
    "Artroplastia de Quadril": {
        "los_dias": 4.0, "custo_R$": 9_800, "readmissao_30d_pct": 5.0,
    },
    "Artroplastia de Joelho": {
        "los_dias": 3.8, "custo_R$": 9_200, "readmissao_30d_pct": 4.5,
    },
    "IAM": {
        "los_dias": 5.5, "custo_R$": 9_100, "readmissao_30d_pct": 15.0,
    },
    "AVC": {
        "los_dias": 7.5, "custo_R$": 11_300, "readmissao_30d_pct": 16.0,
    },
    "Pneumonia adquirida comunidade": {
        "los_dias": 6.0, "custo_R$": 4_500, "readmissao_30d_pct": 12.0,
    },
    "Sepse / Choque séptico": {
        "los_dias": 8.5, "custo_R$": 16_400, "readmissao_30d_pct": 20.0,
    },
    "Fratura de fêmur proximal": {
        "los_dias": 7.0, "custo_R$": 8_500, "readmissao_30d_pct": 11.0,
    },
    "Câncer (sem metástase)": {
        "los_dias": 5.0, "custo_R$": 13_200, "readmissao_30d_pct": 14.0,
    },
}

# Backward-compat: alias individuais com os nomes antigos usados no compute_dashboard
LOS_BENCHMARK: dict[str, float]       = {k: v["los_dias"]           for k, v in _BR_BENCHMARKS_LINHAS_CUIDADO.items()}
CUSTO_BENCHMARK: dict[str, float]     = {k: v["custo_R$"]           for k, v in _BR_BENCHMARKS_LINHAS_CUIDADO.items()}
READMISSAO_BENCHMARK: dict[str, float] = {k: v["readmissao_30d_pct"] for k, v in _BR_BENCHMARKS_LINHAS_CUIDADO.items()}


# ---------------------------------------------------------------------------
# Helpers de diagnóstico
# ---------------------------------------------------------------------------
def _cid_prefix(cid: str | None, n: int = 3) -> str:
    if not cid:
        return ""
    s = str(cid).strip().upper()
    if n > len(s):
        n = len(s)
    return s[:n]


def _match_cid(patterns: Iterable[str], cid: str | None) -> bool:
    prefix = _cid_prefix(cid, n=3)
    if not prefix:
        return False
    return any(prefix.startswith(p[:3]) for p in patterns)


# ---------------------------------------------------------------------------
# Estrutura de saída do Dashboard
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class DashboardMetrics:
    kpis: dict[str, float] = field(default_factory=dict)
    estratificacao_risco: pl.DataFrame = field(default_factory=pl.DataFrame)
    eficiencia_clinica: pl.DataFrame = field(default_factory=pl.DataFrame)
    integracao_territorial: pl.DataFrame = field(default_factory=pl.DataFrame)
    transicao_cuidado: pl.DataFrame = field(default_factory=pl.DataFrame)
    linhas_cuidado_por_hospital: pl.DataFrame = field(default_factory=pl.DataFrame)
    serie_temporal: pl.DataFrame = field(default_factory=pl.DataFrame)
    icsap_por_condicao: pl.DataFrame = field(default_factory=pl.DataFrame)
    top10_procedimentos: pl.DataFrame = field(default_factory=pl.DataFrame)
    top10_especialidades: pl.DataFrame = field(default_factory=pl.DataFrame)
    huff: HuffResult | None = None
    hospitais_geo: pl.DataFrame = field(default_factory=pl.DataFrame)
    benchmarking: pl.DataFrame = field(default_factory=pl.DataFrame)
    distance_decay: pl.DataFrame = field(default_factory=pl.DataFrame)
    glosas_causas: pl.DataFrame = field(default_factory=pl.DataFrame)
    glosas_serie_temporal: pl.DataFrame = field(default_factory=pl.DataFrame)
    alerts: list[str] = field(default_factory=list)
    cnes_sel: list[str] | None = None


# ---------------------------------------------------------------------------
# Orquestrador
# ---------------------------------------------------------------------------
def compute_dashboard(
    production: pl.DataFrame,
    cnes_codes: Sequence[str] | None = None,
    *,
    system_kind: str = "SIA_PA",
    compute_huff: bool = True,
    beta: float | None = None,
) -> DashboardMetrics:
    """Recebe o DataFrame final (SIA-PA ou SIH-RD/RJ/SP) e retorna métricas
    completas de Saúde Populacional por hospital."""
    m = DashboardMetrics(cnes_sel=list(cnes_codes) if cnes_codes else None)
    if production.is_empty():
        m.alerts.append("Nenhum dado de produção carregado — execute o pipeline primeiro.")
        return m

    # Tentar encontrar coluna CNES
    col_cnes = _detect_cnes_col(production)
    if col_cnes is None:
        m.alerts.append("Não encontrada coluna CNES/Estabelecimento no dataset.")
        return m
    prod = production.with_columns(pl.col(col_cnes).cast(pl.Utf8, strict=False).alias("_cnes"))
    if cnes_codes:
        cnes_str_set = {str(s).strip().zfill(7) for s in cnes_codes if str(s).strip()}
        if cnes_str_set:
            filtered = prod.filter(
                pl.col("_cnes").cast(pl.Utf8, strict=False).str.strip_chars().str.zfill(7).is_in(list(cnes_str_set))
            )
            if not filtered.is_empty():
                prod = filtered
            else:
                m.alerts.append(
                    f"Aviso: O filtro por {len(cnes_str_set)} CNES selecionado(s) não coincidiu com os CNES do dataset de produção. Exibindo todos os hospitais do dataset."
                )

    # Dados CNES
    from papahr.dashboard.cnes_loader import get_cnes_data
    cnes = get_cnes_data().load()
    cnes_df = cnes.buscar_estabelecimentos(prod["_cnes"].unique().to_list())
    m.hospitais_geo = cnes_df

    # 1. KPIs globais
    m.kpis = _kpis_globais(prod, cnes_df, system_kind)

    # 2. Estratificação de risco
    m.estratificacao_risco = _estrato_risco_hospital(prod)

    # 3. Eficiência clínica
    m.eficiencia_clinica = _eficiencia_clinica(prod)

    # 4. ICSAP
    m.icsap_por_condicao = _icsap_bruto(prod)
    m.integracao_territorial = _integracao_territorial(prod, cnes_df, m.icsap_por_condicao)

    # 5. Transição de cuidado
    m.transicao_cuidado, m.linhas_cuidado_por_hospital = _transicao_cuidado(prod)

    # 6. Série temporal
    m.serie_temporal = _serie_temporal(prod)

    # 7. Top-10
    m.top10_procedimentos, m.top10_especialidades = _top10(prod)

    # 8. Modelo Gravitacional Huff
    if compute_huff and not cnes_df.is_empty() and not cnes.municipios.is_empty():
        m.huff = _roda_huff(cnes_df, cnes.municipios, system_kind, beta)

    # 8.5. Benchmarking (hospital vs outros do município / micro / macro / UF)
    m.benchmarking = _build_benchmarking(
        prod=prod, cnes_df=cnes_df,
        risco=m.estratificacao_risco,
        eficiencia=m.eficiencia_clinica,
        transicao=m.transicao_cuidado,
        icsap=m.integracao_territorial,
    )

    # 9.0. Curvas de decaimento exponencial (taxa por 1000 hab × distância)
    m.distance_decay = _build_distance_decay(
        prod=prod, cnes_df=cnes_df,
        municipios=(cnes.municipios if cnes else pl.DataFrame()),
        huff=m.huff,
    )

    # 9.5. Glosas (causas + série temporal)
    m.glosas_causas, m.glosas_serie_temporal = _build_glosas(prod)

    # 10. Alertas
    if not m.hospitais_geo.is_empty() and "NU_LATITUDE" in m.hospitais_geo.columns:
        miss = m.hospitais_geo.filter(pl.col("NU_LATITUDE").is_null()).height
        if miss:
            m.alerts.append(f"{miss} hospitais SEM geocódigo no CNES (impossível Huff p/ eles).")
    _gera_alertas(m)
    return m


# ---------------------------------------------------------------------------
# Implementações
# ---------------------------------------------------------------------------
def _detect_cnes_col(df: pl.DataFrame) -> str | None:
    priority = (
        "cnes", "cnes_exec", "cnes_hosp", "co_cnes", "cod_cnes", "codigo_cnes",
        "pa_coduni", "co_unidade", "id_estabelecimento", "establishment_id",
        "id_hospital", "unidade", "cnes_prestador"
    )
    norm = {c.lower().replace(" ", "_"): c for c in df.columns}
    for p in priority:
        if p in norm:
            return norm[p]
    for c in df.columns:
        if "cnes" in c.lower() or "unidade" in c.lower() or "coduni" in c.lower():
            return c
    return None


def _kpis_globais(prod: pl.DataFrame, cnes_df: pl.DataFrame, kind: str) -> dict[str, float]:
    out: dict[str, float] = {}
    out["n_estabelecimentos"] = prod["_cnes"].unique().len()
    out["n_aihs_ou_apacs"] = float(prod.height)
    # Valor monetário
    for col in (
        "VAL_TOT", "VAL_PAGO", "VAL_APRES", "VAL_TOTAL", "VL_TOTAL", "VALOR_TOTAL",
        "vlr_total", "PA_VALAPR", "PA_VALPRO", "PA_VALAPROV", "vl_total_aprovado",
        "vl_total_apresentado", "VAL_SH", "VAL_SP", "VAL_SADH"
    ):
        if col in prod.columns:
            if prod[col].dtype == pl.Utf8:
                val_col = prod[col].str.replace(",", ".").cast(pl.Float64, strict=False)
            else:
                val_col = prod[col].cast(pl.Float64, strict=False)
            out["valor_total_R$"] = float(val_col.sum() or 0)
            break
    # Dias de permanência (apenas SIH)
    for col in ("QT_DIARIAS", "QT_PERMANENCIA", "DIAS_PERM"):
        if col in prod.columns:
            if prod[col].dtype == pl.Utf8:
                val_col = prod[col].str.replace(",", ".").cast(pl.Float64, strict=False)
            else:
                val_col = prod[col].cast(pl.Float64, strict=False)
            out["dias_permanencia_total"] = float(val_col.sum() or 0)
            break
    # Leitos instalados
    if not cnes_df.is_empty():
        for c in ("NU_LEITOS", "QT_LEITOS", "qt_leitos_sus"):
            if c in cnes_df.columns:
                out["leitos_totais_cnes"] = float(
                    cnes_df[c].cast(pl.Float64, strict=False).fill_null(0).sum() or 0
                )
    # ICSAP
    icsap_n = _count_icsap(prod)
    out["icsap_n"] = float(icsap_n)
    out["icsap_pct"] = (100.0 * icsap_n / prod.height) if prod.height else 0.0
    # Taxa readmissão simulada (apenas SIH)
    out["readmissao_30d_est_pct"] = _estima_readmissao_global(prod, kind)
    return out


def _count_icsap(prod: pl.DataFrame) -> int:
    cid_col = _find_col(prod, ("DIAG_PRINC", "CID_PRINCIPAL", "DIAGNOSTICO_PRINCIPAL", "CID", "diagnostico", "CID10"))
    if cid_col is None:
        return 0
    cids = prod[cid_col].cast(pl.Utf8).fill_null("").to_list()
    return sum(1 for c in cids if any(_match_cid(pats, c) for pats in ICSAP_PATTERNS.values()))


def _find_col(df: pl.DataFrame | Iterable[str], candidates: tuple[str, ...]) -> str | None:
    cols_list = df.columns if hasattr(df, "columns") else list(df)
    cols_lower = {str(c).lower().strip().replace(" ", "_").replace("-", "_"): str(c) for c in cols_list}
    for p in candidates:
        pn = p.lower().strip().replace(" ", "_").replace("-", "_")
        if pn in cols_lower:
            return cols_lower[pn]
    # Fallback: substrings parciais (ex: "NU_CID10_PRINCIPAL" bate "CID10")
    for p in candidates:
        pn = p.lower().strip().replace(" ", "_").replace("-", "_")
        for norm, orig in cols_lower.items():
            if pn in norm or norm in pn:
                return orig
    return None


def _strata_lace_lookup(cols: list[str]) -> tuple[str | None, str | None, str | None, str | None]:
    """Tenta detectar colunas LACE: LOS / Emergência / Charlson / Visitas PS."""
    los = _find_col_by_keyword(cols, ("qtd_diarias", "qt_diarias", "qt_permanencia", "los", "dias_permanencia",
                                     "tempo_internacao", "length_stay", "dias_perm"))
    acuity = _find_col_by_keyword(cols, ("tp_internacao", "carater_atendimento", "carater_internacao",
                                         "admissao_emergencia", "emergencia"))
    charlson = _find_col_by_keyword(cols, ("charlson", "cci", "comorbidades", "comorbidade", "numero_comorbidades"))
    ed_visits = _find_col_by_keyword(cols, ("visitas_emergencia", "ps_visitas", "qt_visitas_ps",
                                            "n_visitas_pronto_socorro", "qtd_ps_6m"))
    return los, acuity, charlson, ed_visits


def _find_col_by_keyword(cols: list[str], keys: tuple[str, ...]) -> str | None:
    lowered = [c.lower().replace(" ", "_") for c in cols]
    for k in keys:
        for i, cl in enumerate(lowered):
            if k in cl:
                return cols[i]
    return None


def _estrato_risco_hospital(prod: pl.DataFrame) -> pl.DataFrame:
    """Estratifica risco LACE (ou heurístico baseado em Charlson/LOS/ICSAP)."""
    cols = list(prod.columns)
    col_cnes = "_cnes"
    col_los, col_acuity, col_ch, col_ed = _strata_lace_lookup(cols)
    col_cid = _find_col(prod, ("DIAG_PRINC", "CID_PRINCIPAL", "DIAGNOSTICO_PRINCIPAL", "CID"))

    def _per_row_estrato(row: dict) -> str:
        score = 0
        # LOS
        if col_los:
            try:
                los = float(str(row.get(col_los, 0) or 0).replace(',', '.'))
                if los < 1:
                    score += 0
                elif los <= 3:
                    score += 1
                elif los <= 7:
                    score += 2
                elif los <= 13:
                    score += 3
                else:
                    score += 6
            except (TypeError, ValueError):
                pass
        # Acuidade (emergência = +3)
        if col_acuity:
            try:
                val = str(row.get(col_acuity) or "").upper()
                if "URG" in val or "EMERG" in val or "1" in val or "E" in val:
                    score += 3
            except Exception:
                pass
        # Charlson (proxi por CID prefixos)
        if col_ch:
            try:
                ch = float(str(row.get(col_ch, 0) or 0).replace(',', '.'))
                score += min(int(ch), 6)
            except (TypeError, ValueError):
                pass
        elif col_cid:
            prefix = _cid_prefix(str(row.get(col_cid, "")))
            score += sum(v for k, v in CHARLSON_WEIGHTS.items() if prefix.startswith(k))
        # Visitas PS (últimos 6 meses)
        if col_ed:
            try:
                ed = int(row.get(col_ed, 0) or 0)
                if ed == 0:
                    score += 0
                elif ed <= 1:
                    score += 1
                elif ed <= 3:
                    score += 2
                else:
                    score += 4
            except (TypeError, ValueError):
                pass
        # Cut-offs LACE padrão: 0-4 Baixo, 5-9 Médio, 10+ Alto
        if score >= 10:
            return "ALTO"
        if score >= 5:
            return "MEDIO"
        return "BAIXO"

    rows = prod.select([col_cnes] + [c for c in (col_los, col_acuity, col_ch, col_ed, col_cid) if c]).to_dicts()
    tall = pl.DataFrame([
        {"_cnes": r[col_cnes], "estrato": _per_row_estrato(r)}
        for r in rows
    ])
    pvt = tall.with_columns(peso=pl.lit(1.0)).pivot(
        values="peso", index="_cnes", columns="estrato",
        aggregate_function="len"
    ).fill_null(0)
    # Garante que sempre existam as 3 colunas (mesmo que nenhum caso caia em alguma)
    for c in ("ALTO", "MEDIO", "BAIXO"):
        if c not in pvt.columns:
            pvt = pvt.with_columns(pl.lit(0, dtype=pl.UInt32).alias(c))
    pct_cols = ["ALTO", "MEDIO", "BAIXO"]
    tot = pvt.select(pct_cols).sum_horizontal().alias("total")
    pvt = pvt.with_columns(tot)
    for c in pct_cols:
        pvt = pvt.with_columns((100.0 * pl.col(c) / pl.col("total")).alias(f"pct_{c}"))
    pvt = pvt.with_columns(
        pct_custo_alto=(pl.col("ALTO") * 0.50 + pl.col("MEDIO") * 0.33 + pl.col("BAIXO") * 0.17) / pl.col("total") * 100.0
    )
    return pvt.sort("_cnes")


def _eficiencia_clinica(prod: pl.DataFrame) -> pl.DataFrame:
    cnes_col = "_cnes"
    cid_col = _find_col(prod, ("DIAG_PRINC", "CID_PRINCIPAL", "DIAGNOSTICO_PRINCIPAL", "CID10", "CID"))
    los_col, _, _, _ = _strata_lace_lookup(list(prod.columns))
    val_col = _find_col(prod, (
        "VAL_TOT", "VAL_PAGO", "VAL_APRES", "VAL_TOTAL", "VL_TOTAL", "VALOR_TOTAL",
        "vlr_total", "PA_VALAPR", "PA_VALPRO", "PA_VALAPROV", "vl_total_aprovado",
        "vl_total_apresentado", "VAL_SH", "VAL_SP"
    ))
    rows = prod.select([c for c in (cnes_col, cid_col, los_col, val_col) if c]).to_dicts()
    agg: dict[str, dict[str, float]] = {}
    for r in rows:
        cnes = str(r.get(cnes_col, ""))
        cid = str(r.get(cid_col or "__", ""))
        try:
            los = float(str(r.get(los_col, 0) or 0).replace(',', '.')) if los_col else 0.0
        except ValueError:
            los = 0.0
        try:
            val = float(str(r.get(val_col, 0) or 0).replace(',', '.')) if val_col else 0.0
        except ValueError:
            val = 0.0
        bucket = agg.setdefault(cnes, {"n": 0.0, "los_sum": 0.0, "val_sum": 0.0,
                                        "los_esperado_sum": 0.0, "custo_esperado_sum": 0.0, "drg_weight_sum": 0.0})
        linha = _categoria_linha(cid)
        bucket["n"] += 1
        bucket["los_sum"] += max(0.0, los)
        bucket["val_sum"] += max(0.0, val)
        bucket["los_esperado_sum"] += LOS_BENCHMARK.get(linha, 0.0)
        bucket["custo_esperado_sum"] += CUSTO_BENCHMARK.get(linha, 0.0)
        bucket["drg_weight_sum"] += _get_drg_weight(cid)
    if not agg:
        return pl.DataFrame()
    flat = []
    for cnes, b in agg.items():
        los_o_e = (b["los_sum"] / b["los_esperado_sum"]) if b["los_esperado_sum"] > 0 else 1.0
        custo_o_e = (b["val_sum"] / b["custo_esperado_sum"]) if b["custo_esperado_sum"] > 0 else 1.0
        margem_pct = (1.0 - custo_o_e) * 100.0 if b["custo_esperado_sum"] > 0 else 0.0
        cmi = _case_mix_index(b)
        flat.append({
            "cnes": cnes,
            "n_episodios": int(b["n"]),
            "los_obs_med": round(b["los_sum"] / b["n"], 2) if b["n"] else 0,
            "los_exp_med": round(b["los_esperado_sum"] / b["n"], 2) if b["n"] else 0,
            "los_o_e": round(los_o_e, 3),
            "custo_medio_R$": round(b["val_sum"] / b["n"], 2) if b["n"] else 0,
            "custo_o_e": round(custo_o_e, 3),
            "margem_financeira_pct": round(margem_pct, 2),
            "case_mix_index": round(cmi, 3),
        })
    return pl.DataFrame(flat).sort("cnes")


def _categoria_linha(cid: str) -> str:
    for linha, pats in LINHAS_CUIDADO.items():
        if _match_cid(pats, cid):
            return linha
    return "Outros"


# Pesos DRG não disponíveis na carga do SUS por padrão
def _get_drg_weight(cid: str) -> float:
    return _WEIGHTS_DRG.get(cid, 0.0)

def _case_mix_index(b: dict[str, float]) -> float:
    """CMI = somatório (peso relativo * n) / total casos."""
    return b.get("drg_weight_sum", 0.0) / max(b["n"], 1.0)


def _icsap_bruto(prod: pl.DataFrame) -> pl.DataFrame:
    cid_col = _find_col(prod, ("DIAG_PRINC", "CID_PRINCIPAL", "DIAGNOSTICO_PRINCIPAL", "CID10", "CID"))
    if cid_col is None:
        return pl.DataFrame()
    cids = prod[cid_col].cast(pl.Utf8).fill_null("").to_list()
    counts: dict[str, int] = {k: 0 for k in ICSAP_PATTERNS}
    total = 0
    for c in cids:
        total += 1
        for name, pats in ICSAP_PATTERNS.items():
            if _match_cid(pats, c):
                counts[name] += 1
                break
    flat = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    rows = [{"condicao_evitavel": k, "n_internacoes": v,
             "pct_geral": round(100.0 * v / max(total, 1), 2)} for k, v in flat if v]
    return pl.DataFrame(rows)


def _integracao_territorial(prod: pl.DataFrame, cnes_df: pl.DataFrame,
                            icsap: pl.DataFrame) -> pl.DataFrame:
    cid_col = _find_col(prod, ("DIAG_PRINC", "CID_PRINCIPAL", "DIAGNOSTICO_PRINCIPAL", "CID10", "CID"))
    cnes_col = "_cnes"
    per_cnes: dict[str, dict[str, int]] = {}
    if cid_col:
        for row in prod.select([cnes_col, cid_col]).to_dicts():
            cnes = str(row.get(cnes_col, ""))
            bucket = per_cnes.setdefault(cnes, {"total": 0, "icsap": 0})
            bucket["total"] += 1
            cid = str(row.get(cid_col, ""))
            for pats in ICSAP_PATTERNS.values():
                if _match_cid(pats, cid):
                    bucket["icsap"] += 1
                    break
    flat = []
    for cnes, b in per_cnes.items():
        flat.append({
            "cnes": cnes,
            "internacoes": b["total"],
            "icsap_n": b["icsap"],
            "icsap_pct": round(100.0 * b["icsap"] / max(b["total"], 1), 2),
        })
    df = pl.DataFrame(flat) if flat else pl.DataFrame()
    # Coordenadas geográficas
    if not cnes_df.is_empty() and not df.is_empty() and "CO_CNES" in cnes_df.columns:
        df = df.with_columns(pl.col("cnes").cast(pl.Utf8, strict=False).str.strip_chars())
        geo = cnes_df.select([pl.col("CO_CNES").cast(pl.Utf8, strict=False).str.strip_chars().alias("CO_CNES"), "NU_LATITUDE", "NU_LONGITUDE"])
        df = df.join(geo, left_on="cnes", right_on="CO_CNES", how="left")
    if df.is_empty() or "icsap_pct" not in df.columns:
        return df
    return df.sort("icsap_pct", descending=True)

def _transicao_cuidado(prod: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Retorna (agregado por linha_de_cuidado, tall por hospital/linha)."""
    cid_col = _find_col(prod, ("DIAG_PRINC", "CID_PRINCIPAL", "DIAGNOSTICO_PRINCIPAL", "CID10", "CID"))
    cnes_col = "_cnes"
    cns_col = _find_col(prod, ("CNS_PAC", "CNS", "cns_paciente", "N_AIH"))
    dt_inter_col = _find_col(prod, ("DT_INTER", "DT_INTERNACAO", "data_internacao"))
    dt_saida_col = _find_col(prod, ("DT_SAIDA", "DT_ALTA", "data_saida"))
    contato_col = _find_col(prod, ("contato_48h_pos_alta", "CONTATO_48H", "contato_48h"))
    
    cols_needed = [c for c in (cnes_col, cid_col, cns_col, dt_inter_col, dt_saida_col, contato_col) if c]
    if not cols_needed or cid_col is None:
        return pl.DataFrame(), pl.DataFrame()
        
    df = prod.select(cols_needed)
    
    # Se temos CNS e Datas, calculamos readmissão temporal exata
    has_readm_logic = cns_col and dt_inter_col and dt_saida_col
    if has_readm_logic:
        # Tenta cast de datas
        try:
            df = df.with_columns([
                pl.col(dt_inter_col).cast(pl.Utf8, strict=False).str.strptime(pl.Date, "%Y%m%d", strict=False).alias("_dt_in"),
                pl.col(dt_saida_col).cast(pl.Utf8, strict=False).str.strptime(pl.Date, "%Y%m%d", strict=False).alias("_dt_out"),
            ]).sort([cns_col, "_dt_in"])
            df = df.with_columns(
                (pl.col("_dt_in") - pl.col("_dt_out").shift(1)).dt.total_days().alias("_dias_desde_alta")
            ).with_columns(
                pl.when((pl.col(cns_col) == pl.col(cns_col).shift(1)) & (pl.col("_dias_desde_alta") <= 30) & (pl.col("_dias_desde_alta") >= 0))
                .then(1.0).otherwise(0.0).alias("_is_readm_30d")
            )
        except Exception:
            has_readm_logic = False
    
    rows = df.to_dicts()
    agg_linha: dict[str, dict[str, float]] = {}
    agg_hosp_linha: dict[tuple[str, str], dict[str, float]] = {}
    
    for r in rows:
        cnes = str(r.get(cnes_col, ""))
        cid = str(r.get(cid_col or "", ""))
        linha = _categoria_linha(cid)
        
        a = agg_linha.setdefault(linha, {"n": 0, "readm_30d_sum": 0.0, "contato_48h_ok": 0.0})
        b = agg_hosp_linha.setdefault((cnes, linha), {"n": 0, "readm_30d_sum": 0.0, "contato_48h_ok": 0.0})
        
        a["n"] += 1
        b["n"] += 1
        
        # Readmissão (Real se possível, senão benchmark)
        if has_readm_logic:
            readm = float(r.get("_is_readm_30d", 0.0))
            a["readm_30d_sum"] += readm
            b["readm_30d_sum"] += readm
        else:
            # Sem dados reais de readmissão: não injeta fallback estatístico irreais
            a["readm_30d_sum"] += 0.0
            b["readm_30d_sum"] += 0.0
            
        # Contato 48h (Real do dataset, sem injetar dados fictícios)
        contato_val = float(r.get(contato_col, 0.0)) if contato_col else 0.0
        a["contato_48h_ok"] += contato_val
        b["contato_48h_ok"] += contato_val

    linha_flat = []
    for linha, d in agg_linha.items():
        readm_pct = round(100.0 * d["readm_30d_sum"] / max(d["n"], 1), 2)
        linha_flat.append({
            "linha_cuidado": linha,
            "n_altas": int(d["n"]),
            "contato_48h_pct": round(100.0 * d["contato_48h_ok"] / max(d["n"], 1), 1),
            "readmissao_30d_pct_est": readm_pct,
            "benchmark_readmissao_pct": READMISSAO_BENCHMARK.get(linha, 0.0),
            "delta_readmissao_vs_benchmark_pct": round(readm_pct - READMISSAO_BENCHMARK.get(linha, 0.0), 2),
        })
        
    hosp_flat = []
    for (cnes, linha), d in agg_hosp_linha.items():
        hosp_flat.append({
            "cnes": cnes,
            "linha_cuidado": linha,
            "n_altas": int(d["n"]),
            "contato_48h_pct_est": round(100.0 * d["contato_48h_ok"] / max(d["n"], 1), 1),
            "readmissao_30d_pct_est": round(100.0 * d["readm_30d_sum"] / max(d["n"], 1), 2),
        })
        
    return (pl.DataFrame(linha_flat).sort("n_altas", descending=True),
            pl.DataFrame(hosp_flat).sort(["cnes", "n_altas"], descending=[False, True]))


def _serie_temporal(prod: pl.DataFrame) -> pl.DataFrame:
    ano_col = _find_col(prod, ("ANO_CMPT", "ANO", "ano_competencia"))
    mes_col = _find_col(prod, ("MES_CMPT", "MES", "mes_competencia"))
    cid_col = _find_col(prod, ("DIAG_PRINC", "CID_PRINCIPAL", "DIAGNOSTICO_PRINCIPAL", "CID10", "CID"))
    los_col, _, _, _ = _strata_lace_lookup(list(prod.columns))
    
    # Se ano_col e mes_col não existirem separados, verifica se existe CMPT/PA_CMP/COMPETEN (YYYYMM 6 digitos)
    df_work = prod
    if ano_col is None or mes_col is None:
        cmpt_col = _find_col(prod, ("CMPT", "PA_CMP", "COMPETEN", "COMPETENCIA", "ANO_MES", "ano_mes"))
        if cmpt_col:
            df_work = prod.with_columns([
                pl.col(cmpt_col).cast(pl.Utf8, strict=False).str.strip_chars().str.slice(0, 4).alias("_ano_calc"),
                pl.col(cmpt_col).cast(pl.Utf8, strict=False).str.strip_chars().str.slice(4, 2).alias("_mes_calc"),
            ])
            ano_col = "_ano_calc"
            mes_col = "_mes_calc"
        else:
            return pl.DataFrame()

    df = df_work.select([ano_col, mes_col] + [c for c in (cid_col, los_col) if c])
    agg = df.group_by([ano_col, mes_col]).agg(
        pl.count().alias("n_internacoes"),
        pl.col(los_col).mean().alias("los_medio_dias") if los_col else pl.lit(None),
    ).sort([ano_col, mes_col])
    if cid_col:
        def pct_icsap(group: list[dict]) -> float:
            n = len(group)
            if n == 0:
                return 0.0
            n_icsap = sum(1 for g in group
                          if any(_match_cid(pats, g.get(cid_col)) for pats in ICSAP_PATTERNS.values()))
            return 100.0 * n_icsap / n
        keys = agg[[ano_col, mes_col]].to_dicts()
        icsaps = []
        for k in keys:
            mask = (pl.col(ano_col) == k[ano_col]) & (pl.col(mes_col) == k[mes_col])
            icsaps.append(pct_icsap(df.filter(mask).to_dicts()))
        agg = agg.with_columns(pl.Series("taxa_icsap_pct", [round(v, 2) for v in icsaps]))
    return agg


def _top10(prod: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    proc_col = _find_col(prod, ("PROC_REA", "PA_PROC_ID", "PA_CODPRO", "PA_PROC", "PROC_SOL", "COD_PROC",
                                 "NO_PROCEDIMENTO", "CO_PROCEDIMENTO", "procedimento", "procedimento_realizado"))
    esp_col = _find_col(prod, ("NO_ESPECIALIDADE", "ESPECIALIDADE", "CO_ESPECIALIDADE", "ESPECIAL",
                                "PA_CBOCOD", "PA_CBO", "CBO", "COD_CBO", "ocupacao", "especialidade_medica"))
    def _top(col: str | None) -> pl.DataFrame:
        if col is None:
            return pl.DataFrame()
        t = prod.group_by(col).agg(pl.count().alias("n")).sort("n", descending=True).head(10)
        t.columns = ["item", "n"]
        return t
    return _top(proc_col), _top(esp_col)


# ---------------------------------------------------------------------------
# Huff (geografia)
# ---------------------------------------------------------------------------
def _roda_huff(cnes_df: pl.DataFrame, municipios: pl.DataFrame,
               system_kind: str, beta: float | None) -> HuffResult | None:
    # Beta escolhido por sistema
    if beta is None:
        beta = BETA_AMBULATORIAL if system_kind == "SIA_PA" else BETA_MEDIA

    lat_e = "NU_LATITUDE" if "NU_LATITUDE" in cnes_df.columns else None
    lon_e = "NU_LONGITUDE" if "NU_LONGITUDE" in cnes_df.columns else None
    if not lat_e or not lon_e:
        return None

    # Massa: nº de leitos (CNES) se disponível, senão 100 default
    leitos_col = _find_col(list(cnes_df.columns), ("NU_LEITOS", "QT_LEITOS", "qt_leitos_sus",
                                                    "QT_EXIST", "qt_leitos_sus_cnes"))
    cnes_dicts = cnes_df.select([c for c in ("CO_CNES", "NO_FANTASIA", lat_e, lon_e,
                                              *([leitos_col] if leitos_col else []),
                                              "CO_MUNICIPIO_GESTOR"
                                              if "CO_MUNICIPIO_GESTOR" in cnes_df.columns else None,
                                              "NO_MUNICIPIO"
                                              if "NO_MUNICIPIO" in cnes_df.columns else None,
                                              "SIGLA_ESTADO_GESTOR"
                                              if "SIGLA_ESTADO_GESTOR" in cnes_df.columns else None,
                                              )
                                 if c is not None]).to_dicts()
    hosp_nodes: list[HospitalNode] = []
    for h in cnes_dicts:
        try:
            lat = float(h[lat_e])
            lon = float(h[lon_e])
            if not (math.isfinite(lat) and math.isfinite(lon)):
                continue
        except (TypeError, ValueError):
            continue
        massa = float(h.get(leitos_col, 0) or 0) if leitos_col else 0.0
        if massa <= 0:
            massa = 0.0  # fallback default eliminado para cumprir integridade de dados
        hosp_nodes.append(HospitalNode(
            cnes=str(h.get("CO_CNES", "")),
            nome=str(h.get("NO_FANTASIA", h.get("CO_CNES", ""))),
            latitude=lat, longitude=lon,
            massa=massa,
            municipio_cod=str(h.get("CO_MUNICIPIO_GESTOR", "")),
            municipio_nome=str(h.get("NO_MUNICIPIO", "")),
            uf=str(h.get("SIGLA_ESTADO_GESTOR", "")),
        ))
    hosp_nodes = [h for h in hosp_nodes if h.cnes]
    if not hosp_nodes:
        return None

    lat_m = "NU_LATITUDE" if "NU_LATITUDE" in municipios.columns else None
    lon_m = "NU_LONGITUDE" if "NU_LONGITUDE" in municipios.columns else None
    mun_cod_col = "CO_MUNICIPIO" if "CO_MUNICIPIO" in municipios.columns else None
    if not (lat_m and lon_m and mun_cod_col):
        # Sem coords em municipios: usa hospitais vizinhos dentro raio 50km (skip Huff)
        return None
    mun_dicts = municipios.select([mun_cod_col, lat_m, lon_m,
                                    "NO_MUNICIPIO" if "NO_MUNICIPIO" in municipios.columns else mun_cod_col,
                                    "NU_POPULACAO" if "NU_POPULACAO" in municipios.columns else mun_cod_col,
                                    "SIGLA_ESTADO" if "SIGLA_ESTADO" in municipios.columns else mun_cod_col,
                                    ]).to_dicts()
    mun_nodes: list[MunicipioNode] = []
    for m in mun_dicts:
        try:
            lat = float(m[lat_m]); lon = float(m[lon_m])
            if not (math.isfinite(lat) and math.isfinite(lon)):
                continue
        except (TypeError, ValueError):
            continue
        try:
            pop = float(m.get("NU_POPULACAO", 0) or 0)
        except (TypeError, ValueError):
            pop = 0.0
        mun_nodes.append(MunicipioNode(
            cod_ibge=str(m.get(mun_cod_col, "")),
            nome=str(m.get("NO_MUNICIPIO", m.get(mun_cod_col, ""))),
            latitude=lat, longitude=lon, populacao=pop,
            uf=str(m.get("SIGLA_ESTADO", "")),
        ))
    if not mun_nodes:
        return None
    try:
        return modelo_huff(hosp_nodes, mun_nodes, beta=beta)
    except Exception as exc:
        log.warning(f"[HUFF] Falhou: {exc}")
        return None


# ---------------------------------------------------------------------------
def _estima_readmissao_global(prod: pl.DataFrame, kind: str) -> float:
    # Sem fallbacks estatísticos. Retorna 0.0 pois a taxa exata é calculada por _transicao_cuidado
    return 0.0


def _gera_alertas(m: DashboardMetrics) -> None:
    # KPI globais
    if m.kpis.get("icsap_pct", 0) > 20.0:
        m.alerts.append(f"ALTA incidência ICSAP: {m.kpis['icsap_pct']:.1f}% (>20%) → atenção primária frágil.")
    if m.kpis.get("readmissao_30d_est_pct", 0) > 15.0:
        m.alerts.append(f"Readmissão estimada {m.kpis['readmissao_30d_est_pct']:.1f}% (>15%) → fortalecer transição de cuidado.")
    # O/E LOS > 1.2 em algum hospital
    if not m.eficiencia_clinica.is_empty():
        inef = m.eficiencia_clinica.filter(pl.col("los_o_e") > 1.2)
        if inef.height:
            m.alerts.append(f"{inef.height} hospitais com LOS O/E > 1.2 (ineficiência operacional).")
    # ICSAP > 25%
    if not m.integracao_territorial.is_empty() and "icsap_pct" in m.integracao_territorial.columns:
        qtd_high = m.integracao_territorial.filter(pl.col("icsap_pct") > 25.0).height
        if qtd_high:
            m.alerts.append(f"{qtd_high} unidades com ICSAP >25% (forte sinal de rede básica frágil).")


# =========================================================================
# NÍVEIS GEOGRÁFICOS PARA BENCHMARKING
# =========================================================================
# Heurísticas para agrupar municípios em 4 níveis geográficos (comparáveis):
#  (1) MUNICÍPIO: 6-7 dígitos CO_MUNICIPIO completo
#  (2) MICRO: 5 primeiros dígitos (código município sem dígito verificador = MICRO IBGE)
#  (3) MACRO: 3 primeiros dígitos do município = Região intermediária IBGE
#  (4) UF: 2 primeiros dígitos = Estado (UF)
# =========================================================================
def _geo_micro(muni_6or7: str) -> str:
    s = str(muni_6or7).strip().zfill(7)
    return s[:5]   # 5 dígitos = CID-MUN sem verificador


def _geo_macro(muni_6or7: str) -> str:
    s = str(muni_6or7).strip().zfill(7)
    return s[:3]


def _geo_uf(muni_6or7: str) -> str:
    s = str(muni_6or7).strip().zfill(7)
    return s[:2]


def _build_benchmarking(
    *,
    prod: pl.DataFrame,
    cnes_df: pl.DataFrame,
    risco: pl.DataFrame,
    eficiencia: pl.DataFrame,
    transicao: pl.DataFrame,
    icsap: pl.DataFrame,
) -> pl.DataFrame:
    """Agrega as tabelas por CNES para comparação em 4 níveis geográficos.

    Colunas de saída:
      hospital_cnes, hospital_nome, CO_MUNICIPIO, CO_UF,
      nivel_municipio (chave 7 dígitos), nivel_micro, nivel_macro, nivel_uf,
      n_altas (total), icsap_pct, los_medio_dias, cmi, lace_medio,
      readmissao_30d_pct, custo_oe.
    """
    if prod.is_empty():
        return pl.DataFrame()
    col_cnes = _find_col(prod, ("_cnes",))
    if col_cnes is None:
        return pl.DataFrame()

    # 1. agregado 1 linha por CNES (base N altas)
    base = prod.group_by(pl.col("_cnes").alias("hospital_cnes")).agg(
        pl.len().alias("n_altas"),
    )
    if base.is_empty():
        return pl.DataFrame()

    # junta cnes_df: pega NO_FANTASIA/NO_RAZAO_SOCIAL, CO_MUNICIPIO_GESTOR/CO_UF
    if not cnes_df.is_empty():
        cnes_sel = cnes_df.clone()
        # CNES pode aparecer várias vezes (uma por competência) — pegar a primeira ocorrência p/ cada chave
        if "CO_CNES" not in cnes_sel.columns:
            for col_nome in cnes_sel.columns:
                if col_nome.lower() == "co_cnes":
                    cnes_sel = cnes_sel.rename({col_nome: "CO_CNES"})
                    break
        if "CO_CNES" in cnes_sel.columns:
            cnes_sel = cnes_sel.unique(subset=["CO_CNES"], keep="first")
            cnes_sel = cnes_sel.with_columns(pl.col("CO_CNES").cast(pl.Utf8, strict=False))
            base = base.join(cnes_sel.select([
                "CO_CNES",
                pl.col("NO_FANTASIA").alias("hospital_nome")
                if "NO_FANTASIA" in cnes_sel.columns
                else pl.lit("").alias("hospital_nome"),
                pl.col("CO_MUNICIPIO_GESTOR").alias("CO_MUNICIPIO")
                if "CO_MUNICIPIO_GESTOR" in cnes_sel.columns
                else pl.col("CO_MUNICIPIO").alias("CO_MUNICIPIO")
                if "CO_MUNICIPIO" in cnes_sel.columns
                else pl.lit("").alias("CO_MUNICIPIO"),
                pl.col("CO_UF").alias("CO_UF")
                if "CO_UF" in cnes_sel.columns
                else pl.lit("").alias("CO_UF"),
            ]), left_on="hospital_cnes", right_on="CO_CNES", how="left")
    if "hospital_nome" not in base.columns:
        base = base.with_columns(pl.lit("").alias("hospital_nome"))
    if "CO_MUNICIPIO" not in base.columns:
        base = base.with_columns(pl.lit("").alias("CO_MUNICIPIO"))
    if "CO_UF" not in base.columns:
        base = base.with_columns(pl.lit("").alias("CO_UF"))

    # normaliza o município e cria as 4 chaves geográficas
    base = base.with_columns(
        pl.col("CO_MUNICIPIO").cast(pl.Utf8, strict=False)
          .fill_null("").str.strip_chars().alias("_muni"),
    ).with_columns(
        pl.col("_muni").alias("nivel_municipio"),
        pl.col("_muni").map_elements(_geo_micro, return_dtype=pl.Utf8).alias("nivel_micro"),
        pl.col("_muni").map_elements(_geo_macro, return_dtype=pl.Utf8).alias("nivel_macro"),
        pl.col("_muni").map_elements(_geo_uf, return_dtype=pl.Utf8).alias("nivel_uf"),
    ).drop("_muni")

    # junta risco (CMI, LACE médio, % alto risco)
    if not risco.is_empty():
        cnes_col_r = next((c for c in ("hospital_cnes", "_cnes", "cnes", "CNES") if c in risco.columns), None)
        if cnes_col_r:
            sel_cols = [cnes_col_r]
            for col in ("cmi", "CMI", "lace_medio", "LACE_MEDIO", "pct_alto", "pct_ALTO"):
                if col in risco.columns and col not in sel_cols:
                    sel_cols.append(col)
            r_j = risco.select(sel_cols)
            if cnes_col_r != "hospital_cnes":
                r_j = r_j.rename({cnes_col_r: "hospital_cnes"})
            r_j = r_j.with_columns(pl.col("hospital_cnes").cast(pl.Utf8, strict=False))
            r_j = r_j.group_by("hospital_cnes").first()
            base = base.with_columns(pl.col("hospital_cnes").cast(pl.Utf8, strict=False))
            base = base.join(r_j, on="hospital_cnes", how="left")
            if "pct_ALTO" in base.columns and "pct_alto" not in base.columns:
                base = base.rename({"pct_ALTO": "pct_alto"})
            if "CMI" in base.columns and "cmi" not in base.columns:
                base = base.rename({"CMI": "cmi"})
            if "LACE_MEDIO" in base.columns and "lace_medio" not in base.columns:
                base = base.rename({"LACE_MEDIO": "lace_medio"})

    # junta eficiencia -> LOS médio, LOS O/E, custo O/E
    if not eficiencia.is_empty():
        cnes_col_e = next((c for c in ("hospital_cnes", "_cnes", "cnes", "CNES") if c in eficiencia.columns), None)
        if cnes_col_e:
            cols_ef = [c for c in eficiencia.columns
                       if c == cnes_col_e or c.lower() in ("los_medio", "los_medio_dias", "los_o_e", "custo_o_e", "custo_oe")]
            if len(cols_ef) > 1:
                e_j = eficiencia.select(cols_ef)
                if cnes_col_e != "hospital_cnes":
                    e_j = e_j.rename({cnes_col_e: "hospital_cnes"})
                e_j = e_j.with_columns(pl.col("hospital_cnes").cast(pl.Utf8, strict=False))
                e_j = e_j.group_by("hospital_cnes").first()
                base = base.with_columns(pl.col("hospital_cnes").cast(pl.Utf8, strict=False))
                base = base.join(e_j, on="hospital_cnes", how="left")
                if "los_medio_dias" not in base.columns and "los_medio" in base.columns:
                    base = base.rename({"los_medio": "los_medio_dias"})
                if "custo_oe" not in base.columns and "custo_o_e" in base.columns:
                    base = base.rename({"custo_o_e": "custo_oe"})

    # junta icsap -> icsap_pct
    if not icsap.is_empty() and "icsap_pct" in icsap.columns:
        cnes_col_i = next((c for c in ("hospital_cnes", "_cnes", "cnes", "CNES") if c in icsap.columns), None)
        if cnes_col_i:
            cols_i = [c for c in icsap.columns if c == cnes_col_i or c.lower() == "icsap_pct"]
            if len(cols_i) > 1:
                i_j = icsap.select(cols_i)
                if cnes_col_i != "hospital_cnes":
                    i_j = i_j.rename({cnes_col_i: "hospital_cnes"})
                i_j = i_j.with_columns(pl.col("hospital_cnes").cast(pl.Utf8, strict=False))
                i_j = i_j.group_by("hospital_cnes").first()
                base = base.with_columns(pl.col("hospital_cnes").cast(pl.Utf8, strict=False))
                base = base.join(i_j, on="hospital_cnes", how="left")

    # uniformiza colunas para a UI do Benchmarking
    colunas_desejadas = {
        "hospital_cnes": pl.Utf8,
        "hospital_nome": pl.Utf8,
        "CO_MUNICIPIO": pl.Utf8,
        "CO_UF": pl.Utf8,
        "nivel_municipio": pl.Utf8,
        "nivel_micro": pl.Utf8,
        "nivel_macro": pl.Utf8,
        "nivel_uf": pl.Utf8,
        "n_altas": pl.Int64,
        "icsap_pct": pl.Float64,
        "los_medio_dias": pl.Float64,
        "cmi": pl.Float64,
        "lace_medio": pl.Float64,
        "pct_alto": pl.Float64,
        "readmissao_30d_pct": pl.Float64,
        "custo_oe": pl.Float64,
        "los_o_e": pl.Float64,
    }
    for col, tp in colunas_desejadas.items():
        if col not in base.columns:
            base = base.with_columns(pl.lit(None).cast(tp).alias(col))
        else:
            base = base.with_columns(pl.col(col).cast(tp, strict=False))
    # Sem fallback: readmissão = 0 se não calculável
    if "readmissao_30d_pct" in base.columns:
        base = base.with_columns(
            pl.col("readmissao_30d_pct").fill_null(0.0).alias("readmissao_30d_pct")
        )
    return base.select(list(colunas_desejadas.keys()))


# =========================================================================
# CURVAS DE DECAIMENTO EXPONENCIAL: taxa x distância (faixas 5/10/20/40/80 km)
# =========================================================================
_RINGS_KM = (5, 10, 20, 40, 80)
_RING_LABEL = ("0–5 km", "5–10 km", "10–20 km", "20–40 km", "40–80 km")
_RING_MIDPOINT = (2.5, 7.5, 15.0, 30.0, 60.0)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2.0) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _fit_exp_decay(xs: list[float], ys: list[float]) -> dict[str, float]:
    """Ajuste y = A · exp(-λ · x) via ln-linear least squares (só ys>0).

    Retorna {"A": float, "lambda": float, "R2": float, "dmp_km": Dist Média Ponderada}
    """
    # filtra positivos
    xp, yp = [], []
    for x, y in zip(xs, ys):
        if y is not None and y > 0 and x is not None:
            xp.append(x); yp.append(y)
    if len(xp) < 2:
        return {"A": 0.0, "lambda": 0.0, "R2": 0.0, "dmp_km": 0.0}
    # ln(y) = ln(A) - λ x  →   regressão linear w = a + b*x
    import math
    ws = [math.log(max(y, 1e-9)) for y in yp]
    n = len(xp)
    sx = sum(xp)
    sw = sum(ws)
    sxx = sum(x * x for x in xp)
    sxw = sum(x * w for x, w in zip(xp, ws))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return {"A": 0.0, "lambda": 0.0, "R2": 0.0, "dmp_km": 0.0}
    b = (n * sxw - sx * sw) / denom      # slope = -lambda
    a = (sw - b * sx) / n                # intercept = ln(A)
    lamb = max(0.0, -b)
    A = math.exp(a)
    # R2
    w_medio = sw / n
    ss_tot = sum((w - w_medio) ** 2 for w in ws)
    ss_res = sum((w - (a + b * x)) ** 2 for x, w in zip(xp, ws))
    r2 = 1.0 - (ss_res / ss_tot if abs(ss_tot) > 1e-12 else 0.0)
    # distância média ponderada: sum(y*x)/sum(y)
    s_yx = sum(y * x for x, y in zip(xp, yp))
    s_y = sum(yp)
    dmp_km = (s_yx / s_y) if s_y > 0 else 0.0
    return {"A": A, "lambda": lamb, "R2": r2, "dmp_km": dmp_km}


def _build_distance_decay(
    *,
    prod: pl.DataFrame,
    cnes_df: pl.DataFrame,
    municipios: pl.DataFrame,
    huff: HuffResult | None,
) -> pl.DataFrame:
    """Agrega internações por hospital × faixa de distância e ajuste exponencial.

    Schema de saída:
      hospital_cnes | faixa_km_label | faixa_mid_km | pop_anel
        | n_internacoes | taxa_por_1000 | A_exp | lambda_exp | R2_exp | dmp_km
    Os parâmetros do ajuste (A/lambda/R2/dmp_km) são iguais para todas as 5 linhas
    do mesmo hospital (repetidos), para facilitar a UI (pegar a primeira linha).
    """
    if prod.is_empty() or cnes_df.is_empty() or municipios.is_empty():
        return pl.DataFrame()
    col_cnes = _find_col(prod, ("_cnes", "cnes"))
    col_muni = _find_col(prod, ("municipio_paciente", "CO_MUNICIPIO", "COD_MUNICIPIO",
                                 "municipio_ibge", "CO_MUNICIPIO_RES", "MUNIC_RES"))
    if col_cnes is None or col_muni is None:
        # fallback: usar huff od_matrix se existir
        if huff is None or huff.od_matrix.is_empty():
            return pl.DataFrame()
        df_resumo = huff.od_matrix.clone()
        if "hospitais_modelados" in df_resumo.columns:
            pass
    # coluna população no município
    pop_col = _find_col(municipios, ("POPULACAO", "POP_TOTAL", "populacao", "NU_POPULACAO"))

    # 1. hospitais geo (com lat/lon)
    hosp_geo = cnes_df.clone()
    col_h_lat = _find_col(hosp_geo, ("NU_LATITUDE", "latitude"))
    col_h_lon = _find_col(hosp_geo, ("NU_LONGITUDE", "longitude"))
    col_h_cnes = _find_col(hosp_geo, ("CO_CNES", "cnes"))
    if col_h_lat is None or col_h_lon is None or col_h_cnes is None:
        return pl.DataFrame()
    hosp_geo = hosp_geo.select([
        pl.col(col_h_cnes).cast(pl.Utf8, strict=False).alias("hospital_cnes"),
        pl.col(col_h_lat).cast(pl.Float64, strict=False).alias("h_lat"),
        pl.col(col_h_lon).cast(pl.Float64, strict=False).alias("h_lon"),
    ]).filter(pl.col("h_lat").is_not_null() & pl.col("h_lon").is_not_null())
    hosp_geo = hosp_geo.group_by("hospital_cnes").first()
    if hosp_geo.is_empty():
        return pl.DataFrame()

    # 2. municípios geo (com lat/lon)
    col_m_lat = _find_col(municipios, ("NU_LATITUDE", "LATITUDE", "latitude"))
    col_m_lon = _find_col(municipios, ("NU_LONGITUDE", "LONGITUDE", "longitude"))
    col_m_cod = _find_col(municipios, ("CO_MUNICIPIO", "codigo_ibge", "COD_MUNICIPIO_IBGE"))
    if col_m_lat is None or col_m_lon is None or col_m_cod is None:
        return pl.DataFrame()
    muni_geo = municipios.select([
        pl.col(col_m_cod).cast(pl.Utf8, strict=False).alias("muni_cod"),
        pl.col(col_m_lat).cast(pl.Float64, strict=False).alias("m_lat"),
        pl.col(col_m_lon).cast(pl.Float64, strict=False).alias("m_lon"),
        (pl.col(pop_col).cast(pl.Float64, strict=False)
         if pop_col else pl.lit(10000.0)).alias("pop"),
    ]).filter(pl.col("m_lat").is_not_null() & pl.col("m_lon").is_not_null())
    muni_geo = muni_geo.group_by("muni_cod").first()
    if muni_geo.is_empty():
        return pl.DataFrame()

    # 3. Conta internações (hospital_cnes × muni_origem) do dataset
    # normaliza coluna município do dataset (garante 6 dígitos, sem DV)
    def _norm6(x: str) -> str:
        s = str(x or "").strip().zfill(7)[:6]
        return s

    contagem = prod.with_columns([
        pl.col(col_cnes).cast(pl.Utf8, strict=False).alias("hospital_cnes"),
        pl.col(col_muni).cast(pl.Utf8, strict=False).map_elements(
            _norm6, return_dtype=pl.Utf8).alias("muni_cod"),
    ]).filter(pl.col("hospital_cnes").is_not_null() & pl.col("muni_cod").is_not_null())
    contagem = contagem.group_by(["hospital_cnes", "muni_cod"]).agg(
        pl.len().alias("n_internacoes"),
    )
    if contagem.is_empty():
        return pl.DataFrame()
    # join hosp_geo e muni_geo
    contagem = contagem.with_columns(pl.col("hospital_cnes").cast(pl.Utf8, strict=False))
    hosp_geo = hosp_geo.with_columns(pl.col("hospital_cnes").cast(pl.Utf8, strict=False))
    muni_geo = muni_geo.with_columns(pl.col("muni_cod").cast(pl.Utf8, strict=False))
    joined = contagem.join(hosp_geo, on="hospital_cnes", how="inner") \
        .join(muni_geo, on="muni_cod", how="inner")
    if joined.is_empty():
        return pl.DataFrame()

    # calcula distância haversine
    def _row_dist(hl, hn, ml, mn):
        try:
            return _haversine_km(float(hl), float(hn), float(ml), float(mn))
        except Exception:  # noqa: BLE001
            return None

    joined = joined.with_columns(
        pl.struct(["h_lat", "h_lon", "m_lat", "m_lon"]).map_elements(
            lambda s: _row_dist(s["h_lat"], s["h_lon"], s["m_lat"], s["m_lon"]),
            return_dtype=pl.Float64,
        ).alias("dist_km"),
    ).filter(pl.col("dist_km").is_not_null())
    if joined.is_empty():
        return pl.DataFrame()

    # classifica cada par em um anel (0..4)
    def _ring_idx(d: float) -> int:
        for i, km in enumerate(_RINGS_KM):
            if d <= km:
                return i
        return 4
    joined = joined.with_columns(
        pl.col("dist_km").map_elements(_ring_idx, return_dtype=pl.Int32).alias("ring_idx"),
    )

    # agrega por hospital e anel
    anel_agg = joined.group_by(["hospital_cnes", "ring_idx"]).agg([
        pl.sum("n_internacoes").alias("n_internacoes"),
        pl.col("muni_cod").unique().alias("muni_list"),
    ])

    # adiciona população por anel
    muni_geo_6 = muni_geo.with_columns(
        pl.col("muni_cod").map_elements(_norm6, return_dtype=pl.Utf8).alias("_m6"),
    )
    muni_pop = {r["_m6"]: float(r["pop"] or 0.0) for r in muni_geo_6.iter_rows(named=True)}

    linhas: list[dict[str, Any]] = []
    for hospital_cnes in sorted(anel_agg["hospital_cnes"].unique(maintain_order=True).to_list()):
        sub = anel_agg.filter(pl.col("hospital_cnes") == hospital_cnes)
        rings = {r["ring_idx"]: r for r in sub.iter_rows(named=True)}
        obs_x: list[float] = []
        obs_y: list[float] = []
        for idx in range(5):
            lab = _RING_LABEL[idx]
            mid = _RING_MIDPOINT[idx]
            info = rings.get(idx)
            n_ints = 0
            pop = 0.0
            if info is not None:
                n_ints = int(info["n_internacoes"] or 0)
                for m in (info["muni_list"] or []):
                    pop += float(muni_pop.get(str(m)[:6], 0.0)
                                or muni_pop.get(str(m).zfill(7)[:6], 0.0))
            taxa = (1000.0 * n_ints / pop) if pop > 0 else 0.0
            if taxa > 0:
                obs_x.append(mid); obs_y.append(taxa)
            linhas.append({
                "hospital_cnes": hospital_cnes,
                "faixa_idx": idx,
                "faixa_km_label": lab,
                "faixa_mid_km": float(mid),
                "pop_anel": float(pop),
                "n_internacoes": n_ints,
                "taxa_por_1000": round(float(taxa), 4),
            })
        # ajuste exponencial para este hospital
        fit = _fit_exp_decay(obs_x, obs_y)
        # espalha o fit em todas as 5 linhas do hospital (facilita UI)
        for row in linhas[-5:]:
            row["A_exp"] = round(float(fit["A"]), 4)
            row["lambda_exp"] = round(float(fit["lambda"]), 5)
            row["R2_exp"] = round(float(fit["R2"]), 4)
            row["dmp_km"] = round(float(fit["dmp_km"]), 2)
    if not linhas:
        return pl.DataFrame()
    return pl.DataFrame(linhas)


# =========================================================================
# GLOSAS: causas + série histórica
# =========================================================================
# Tabela SIGTAP: códigos de glosa mais comuns SIASUS (heurística padrão SUS)
_GLOSA_CODIGOS_COMUNS: dict[str, str] = {
    "01": "01 - Falta de autorização/prorrogação",
    "02": "02 - Internação por mais de 365 dias (sazonal)",
    "03": "03 - Número da AIH inválido / duplicado",
    "04": "04 - Estabelecimento não credenciado / incompatível com procedimento",
    "05": "05 - Procedimento incompatível com idade/sexo",
    "06": "06 - Falta de laudo / parecer técnico",
    "07": "07 - Internação sem característica de urgência",
    "08": "08 - Divergência na data de admissão/alta",
    "09": "09 - Procedimento não realizado (faltam registros)",
    "10": "10 - Falta do CID principal/secundário",
    "11": "11 - CNES do estabelecimento inválido ou bloqueado",
    "12": "12 - Valor apresentado diverge da tabela SIGTAP",
    "13": "13 - Leito de origem inexistente ou inadequado",
    "14": "14 - Mais de uma AIH por paciente/período (sobreposição)",
    "15": "15 - Paciente falecido sem documentação óbito",
    "16": "16 - Procedimento de alta complexidade sem regulação",
    "17": "17 - Não conformidade na conta hospitalar / auditoria",
    "18": "18 - Internação domiciliar ou ambulatorial indevida",
    "19": "19 - Material/OPME não autorizado ou fora da tabela",
    "20": "20 - Documento eletrônico com assinatura digital inválida",
    "21": "21 - Falta de confirmação de leito pelo gestor",
    "22": "22 - Período de permanência excedente a referência DRG",
    "23": "23 - Diagnóstico principal inválido ou fora da faixa etária",
    "24": "24 - Readmissão no mesmo hospital no prazo (30d) sem justificativa",
    "25": "25 - Inconsistência no identificador de paciente CNS/CPF",
}


def _rotulo_glosa(codigo: str) -> str:
    """Retorna rótulo legível a partir de um código de glosa SUS."""
    if codigo is None:
        return ""
    c = str(codigo).strip()
    if not c:
        return ""
    # já é chave de 2 dígitos?
    if c in _GLOSA_CODIGOS_COMUNS:
        return _GLOSA_CODIGOS_COMUNS[c]
    # prefixo 2 dígitos (01, 02, ...) se cod for numérico longo
    if c.isdigit() and len(c) >= 2:
        short = c[:2]
        if short in _GLOSA_CODIGOS_COMUNS:
            return f"{c} — {_GLOSA_CODIGOS_COMUNS[short]}"
    return c


def _build_glosas(prod: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Constrói as duas tabelas de glosas: causas (causa × ocorrências × % × R$)
    e série temporal (competência/mês × N glosas × R$ × % total).

    Retorna (df_causas, df_serie). Se não existirem colunas de glosa no dataset,
    retorna DataFrames vazios e o dashboard exibirá uma mensagem explicativa.
    """
    if prod.is_empty():
        return pl.DataFrame(), pl.DataFrame()

    # -----------------------------
    # 1. Detecta colunas de glosa
    # -----------------------------
    colunas = list(prod.columns)
    # Colunas valor glosado
    valor_glosa_cols: list[str] = [
        c for c in colunas
        if "glosa" in c.lower() and
        any(k in c.lower() for k in ("vl_", "valor", "total", "parcial", "vlr"))
    ]
    if not valor_glosa_cols:
        for nome in ("VL_GLOSA_PARCIAL", "VL_GLOSA_TOTAL", "VL_GLOSA", "VL_GL_TOTAL",
                      "VL_GL_PARCIAL", "VALOR_GLOSA", "VALOR_TOTAL_GLOSADO"):
            if nome in colunas:
                valor_glosa_cols.append(nome)
                break
    # Colunas quantidade glosada
    qtde_glosa_cols: list[str] = [
        c for c in colunas
        if "glosa" in c.lower() and
        any(k in c.lower() for k in ("qt_", "qtd", "quant", "numero", "nu_", "qtde"))
    ]
    # Colunas causas de glosa (1 a 5)
    causa_cols: list[str] = [
        c for c in colunas
        if ("glosa" in c.lower() or "motivo" in c.lower() or "mot_" in c.lower()) and
        not any(k in c.lower() for k in ("vl_", "valor", "qt_", "qtd", "quant", "total"))
    ]
    # Fallback por prefixo CO_GLOSA
    if not causa_cols:
        for prefixo in ("CO_GLOSA", "COD_GLOSA", "MOTIVO_GLOSA", "MOT_GLOSA",
                        "GLOSA_COD", "DS_GLOSA", "GLOSA_MOT"):
            for c in colunas:
                if c.upper().startswith(prefixo) or c == prefixo:
                    causa_cols.append(c)

    # Nenhuma coluna de glosa? -> dataframes vazios
    if not causa_cols and not valor_glosa_cols and not qtde_glosa_cols:
        return pl.DataFrame(), pl.DataFrame()

    # -----------------------------
    # 2. Coluna competência (para série temporal)
    # -----------------------------
    col_comp: str | None = None
    col_ano: str | None = None
    col_mes: str | None = None
    col_data: str | None = None
    for nome in ("COMPETEN", "COMPETENCIA", "COMPETÊNCIA", "NU_COMPETEN",
                 "ANO_MES", "MESANO", "AA_MM", "AAAAMM"):
        for c in colunas:
            if c.upper().startswith(nome):
                col_comp = c
                break
        if col_comp:
            break
    if col_comp is None:
        col_ano = _find_col(prod, ("ANO", "NU_ANO", "ANO_CMPT", "ANO_ATENDIMENTO"))
        col_mes = _find_col(prod, ("MES", "NU_MES", "MES_CMPT", "MES_ATENDIMENTO"))
        col_data = _find_col(prod, ("DATA_INTERNACAO", "DT_INTERNACAO", "DT_ATENDIMENTO",
                                     "DATA_ATENDIMENTO", "DT_ALTA", "DATA_ALTA"))

    # -----------------------------
    # 3. Coluna valor total apresentado (para % glosado)
    # -----------------------------
    col_valor_total = _find_col(prod, ("VAL_TOTAL", "VL_TOTAL", "VALOR_TOTAL",
                                       "vlr_total", "VL_TOTAL_APRESENT"))

    # -----------------------------
    # 4. Flag "internação sofreu glosa"
    #    - valor_glosado > 0.01 OU quantidade glosada > 0 OU causa_col não nula
    # -----------------------------
    exprs_flag: list[pl.Expr] = []
    for v in valor_glosa_cols:
        exprs_flag.append(pl.col(v).cast(pl.Float64, strict=False).fill_null(0.0) > 0.01)
    for q in qtde_glosa_cols:
        exprs_flag.append(pl.col(q).cast(pl.Float64, strict=False).fill_null(0.0) > 0.0)
    # Não consideramos causa_col nula como flag, pois podem existir causas vazias.
    if exprs_flag:
        flg_glosa = pl.any_horizontal(exprs_flag).alias("_flg_glosa")
    else:
        # Fallback: se tem causa_col preenchida, conta como glosada
        if causa_cols:
            flg_glosa = pl.any_horizontal([
                (pl.col(c).cast(pl.Utf8, strict=False).fill_null("").str.strip_chars() != "")
                for c in causa_cols
            ]).alias("_flg_glosa")
        else:
            return pl.DataFrame(), pl.DataFrame()

    # Soma de valor glosado (se existir)
    if valor_glosa_cols:
        vl_glosa_expr = pl.sum_horizontal([
            pl.col(v).cast(pl.Float64, strict=False).fill_null(0.0)
            for v in valor_glosa_cols
        ]).alias("_vl_glosa")
    else:
        vl_glosa_expr = pl.lit(0.0).alias("_vl_glosa")

    # Valor total apresentado (para % de glosa)
    if col_valor_total is not None:
        vl_tot_expr = pl.col(col_valor_total).cast(pl.Float64, strict=False).fill_null(0.0).alias("_vl_total_apres")
    else:
        vl_tot_expr = pl.lit(0.0).alias("_vl_total_apres")

    df_aug = prod.with_columns([flg_glosa, vl_glosa_expr, vl_tot_expr])

    # -----------------------------
    # 5. TABELA DE CAUSAS
    # -----------------------------
    linhas_causas: list[dict[str, Any]] = []
    if causa_cols or (valor_glosa_cols and qtde_glosa_cols):
        # Total geral de internações glosadas (para % de cada causa)
        total_glosas = int(df_aug.select(pl.col("_flg_glosa").cast(pl.Int64).sum()).item() or 0)
        total_valor_glosado = float(df_aug.select(pl.sum("_vl_glosa")).item() or 0.0)

        # Se tem causas explícitas -> explode long format
        if causa_cols:
            # Unpivot causa_cols para long: uma linha por causa não nula/vazia
            # Filtrando flag_glosa = True (casos SEM glosa não tem causa)
            sub = df_aug.filter(pl.col("_flg_glosa"))
            # Extrai valor glosado também (por evento)
            eventos_causa_valor: list[tuple[str, float]] = []
            for r in sub.select(causa_cols + ["_vl_glosa"]).to_dicts():
                vl = float(r.get("_vl_glosa") or 0.0)
                for c in causa_cols:
                    cod = str(r.get(c) or "").strip()
                    if not cod or cod.upper() in ("NULL", "NAN", "NONE", "0", "00"):
                        continue
                    eventos_causa_valor.append((cod, vl))
            if eventos_causa_valor:
                # Agrupa: n, vl
                agg_c: dict[str, dict[str, float]] = {}
                for cod, vl in eventos_causa_valor:
                    bkt = agg_c.setdefault(cod, {"n": 0.0, "vl": 0.0})
                    bkt["n"] += 1.0
                    bkt["vl"] += vl
                for cod, bkt in sorted(agg_c.items(), key=lambda kv: (-kv[1]["n"], -kv[1]["vl"])):
                    n = int(bkt["n"])
                    vl = round(float(bkt["vl"]), 2)
                    pct = (100.0 * n / total_glosas) if total_glosas else 0.0
                    pct_vl = (100.0 * vl / total_valor_glosado) if total_valor_glosado else 0.0
                    linhas_causas.append({
                        "Código Glosa": cod,
                        "Motivo / Descrição": _rotulo_glosa(cod),
                        "Nº AIHs/APACs afetadas": n,
                        "% sobre todas as glosas": round(pct, 2),
                        "Valor total glosado (R$)": vl,
                        "% do $ total glosado": round(pct_vl, 2),
                    })
        # Sem causas -> uma linha "Sem detalhamento de causa"
        if not linhas_causas and total_glosas > 0:
            linhas_causas.append({
                "Código Glosa": "—",
                "Motivo / Descrição": "Sem colunas de causa de glosa detalhada no dataset (apenas valores ou quantidades)",
                "Nº AIHs/APACs afetadas": total_glosas,
                "% sobre todas as glosas": 100.0,
                "Valor total glosado (R$)": round(total_valor_glosado, 2),
                "% do $ total glosado": 100.0 if total_valor_glosado > 0 else 0.0,
            })

    df_causas = pl.DataFrame(linhas_causas) if linhas_causas else pl.DataFrame()

    # -----------------------------
    # 6. SÉRIE TEMPORAL DE GLOSAS
    # -----------------------------
    df_serie = pl.DataFrame()
    # Tenta 3 estratégias: COMPETÊNCIA / ANO+MES / DATA
    ser_base = df_aug
    ser_base = ser_base.with_columns([
        pl.col("_flg_glosa").cast(pl.Int64).alias("_n_glosas"),
    ])
    group_cols: list[str] = []
    if col_comp is not None:
        ser_base = ser_base.with_columns(pl.col(col_comp).alias("_compet"))
        group_cols = ["_compet"]
    elif col_ano is not None and col_mes is not None:
        ser_base = ser_base.with_columns([
            pl.col(col_ano).cast(pl.Int64, strict=False).alias("_ano"),
            pl.col(col_mes).cast(pl.Int64, strict=False).alias("_mes"),
        ])
        group_cols = ["_ano", "_mes"]
    elif col_data is not None:
        try:
            ser_base = ser_base.with_columns([
                pl.col(col_data).cast(pl.Date, strict=False).dt.year().alias("_ano"),
                pl.col(col_data).cast(pl.Date, strict=False).dt.month().alias("_mes"),
            ])
            group_cols = ["_ano", "_mes"]
        except Exception:
            group_cols = []
    if group_cols:
        try:
            agg_exprs = [
                pl.len().alias("N total AIHs/APACs"),
                pl.sum("_n_glosas").alias("N glosadas"),
                pl.sum("_vl_glosa").alias("Valor glosado (R$)"),
                pl.sum("_vl_total_apres").alias("Valor apresentado (R$)"),
            ]
            grouped = ser_base.group_by(group_cols).agg(agg_exprs)
            # Ordena
            ordered = grouped.sort(group_cols)
            # Cria colunas de %
            ordered = ordered.with_columns([
                (100.0 * pl.col("N glosadas") / pl.col("N total AIHs/APACs"))
                .round(2).alias("% AIHs/APACs glosadas"),
                (100.0 * pl.col("Valor glosado (R$)") /
                 pl.when(pl.col("Valor apresentado (R$)") > 0)
                 .then(pl.col("Valor apresentado (R$)"))
                 .otherwise(None))
                .round(2).alias("% valor glosado $"),
            ])
            # Renomeia as colunas de agrupamento para o usuário
            ren: dict[str, str] = {}
            if "_compet" in ordered.columns:
                ren["_compet"] = "Competência (AAAAMM)"
            if "_ano" in ordered.columns:
                ren["_ano"] = "Ano"
            if "_mes" in ordered.columns:
                ren["_mes"] = "Mês"
            if ren:
                ordered = ordered.rename(ren)
            df_serie = ordered
        except Exception as exc:  # noqa: BLE001
            log.warning("Erro ao construir série temporal de glosas: %s", exc)
            df_serie = pl.DataFrame()

    return df_causas, df_serie
