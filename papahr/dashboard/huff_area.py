"""
huff_area.py — Modelo de Huff por área da assistência (GSPHosp).

Calcula, para cada hospital e cada área da assistência (subgrupo SIGTAP do
PROC_REA), a probabilidade de Huff de cada município de residência escolher o
hospital, o fluxo esperado (probabilidade × demanda observada do município na
área), e compara com o fluxo observado no SIH-RD.

Entradas (Polars DataFrames; nomes de coluna exatamente como abaixo):

  aih         uma linha por AIH
              CNES        str  7 dígitos
              MUNIC_RES   str  6 dígitos (código IBGE sem dígito verificador)
              PROC_REA    str  10 dígitos
              DIAG_PRINC  str  opcional (CID-10, ex.: "I219")
              ESPEC       str  opcional (2 dígitos)
              VAL_TOT     float opcional

  hospitais   CNES str, NOME str, LAT float, LON float, COD_MUN str (6 dígitos)

  municipios  COD_MUN str (6 dígitos), NOME str, LAT float, LON float,
              REGIAO str opcional (macro/DRS/região de saúde — qualquer recorte),
              POP int opcional

Saídas principais:

  od      hospital × área × município
  resumo  hospital × área (indicadores de atratividade)

Dependências: apenas polars (>= 1.0).
"""
from __future__ import annotations

import math
from typing import Iterable

import polars as pl

# ---------------------------------------------------------------------------
# 1. Dicionários de classificação
# ---------------------------------------------------------------------------

# Subgrupos SIGTAP (grupo + subgrupo = 4 primeiros dígitos de PROC_REA).
# FONTE AUTORITATIVA: tb_sub_grupo.txt dentro do zip da Tabela Unificada que o
# app já baixa do FTP do DATASUS. Passe o dicionário lido de lá em
# `nomes_sigtap` e este fallback é ignorado. O fallback cobre os grupos que
# geram AIH (02, 03, 04, 05); procedimentos fora dele recebem "Outros".
SUBGRUPOS_SIGTAP_FALLBACK: dict[str, str] = {
    "0201": "Coleta de material",
    "0202": "Diagnóstico em laboratório clínico",
    "0203": "Diagnóstico por anatomia patológica e citopatologia",
    "0204": "Diagnóstico por radiologia",
    "0205": "Diagnóstico por ultrassonografia",
    "0206": "Diagnóstico por tomografia",
    "0207": "Diagnóstico por ressonância magnética",
    "0208": "Diagnóstico por medicina nuclear in vivo",
    "0209": "Diagnóstico por endoscopia",
    "0210": "Diagnóstico por radiologia intervencionista",
    "0211": "Métodos diagnósticos em especialidades",
    "0301": "Consultas / atendimentos / acompanhamentos",
    "0302": "Fisioterapia",
    "0303": "Tratamentos clínicos (outras especialidades)",
    "0304": "Tratamento em oncologia",
    "0305": "Tratamento em nefrologia",
    "0306": "Hemoterapia",
    "0307": "Tratamentos odontológicos",
    "0308": "Tratamento de lesões, envenenamentos e outros decorrentes de causas externas",
    "0309": "Terapias especializadas",
    "0310": "Parto e nascimento",
    "0401": "Pequenas cirurgias e cirurgias de pele, tecido subcutâneo e mucosa",
    "0402": "Cirurgia de glândulas endócrinas",
    "0403": "Cirurgia do sistema nervoso central e periférico",
    "0404": "Cirurgia das vias aéreas superiores, da face, da cabeça e do pescoço",
    "0405": "Cirurgia do aparelho da visão",
    "0406": "Cirurgia do aparelho circulatório",
    "0407": "Cirurgia do aparelho digestivo, órgãos anexos e parede abdominal",
    "0408": "Cirurgia do sistema osteomuscular",
    "0409": "Cirurgia do aparelho geniturinário",
    "0410": "Cirurgia de mama",
    "0411": "Cirurgia obstétrica",
    "0412": "Cirurgia torácica",
    "0413": "Cirurgia reparadora",
    "0414": "Bucomaxilofacial",
    "0415": "Outras cirurgias",
    "0416": "Cirurgia em oncologia",
    "0417": "Anestesiologia",
    "0418": "Cirurgia em nefrologia",
    "0501": "Coleta e exames para fins de doação de órgãos, tecidos e células",
    "0502": "Avaliação de morte encefálica",
    "0503": "Ações relacionadas à doação de órgãos e tecidos para transplante",
    "0504": "Processamento de tecidos para transplante",
    "0505": "Transplante de órgãos, tecidos e células",
    "0506": "Acompanhamento e intercorrências no pré e pós-transplante",
}

# ESPEC do SIH-RD (especialidade do leito). Filtro auxiliar.
ESPEC_SIH: dict[str, str] = {
    "01": "Cirúrgico",
    "02": "Obstétrico",
    "03": "Clínico",
    "04": "Crônicos",
    "05": "Psiquiatria",
    "06": "Pneumologia sanitária",
    "07": "Pediátrico",
    "08": "Reabilitação",
    "09": "Leito dia — cirúrgico",
    "10": "Leito dia — AIDS",
    "11": "Leito dia — fibrose cística",
    "12": "Leito dia — intercorrência pós-transplante",
    "13": "Leito dia — geriatria",
    "14": "Leito dia — saúde mental",
}

# Capítulos CID-10: (letra, número inicial, número final, capítulo, nome)
_CID_CAPITULOS = [
    ("A", 0, 99, "I", "Doenças infecciosas e parasitárias"),
    ("B", 0, 99, "I", "Doenças infecciosas e parasitárias"),
    ("C", 0, 99, "II", "Neoplasias"),
    ("D", 0, 48, "II", "Neoplasias"),
    ("D", 50, 89, "III", "Doenças do sangue e transtornos imunitários"),
    ("E", 0, 90, "IV", "Doenças endócrinas, nutricionais e metabólicas"),
    ("F", 0, 99, "V", "Transtornos mentais e comportamentais"),
    ("G", 0, 99, "VI", "Doenças do sistema nervoso"),
    ("H", 0, 59, "VII", "Doenças do olho e anexos"),
    ("H", 60, 95, "VIII", "Doenças do ouvido e apófise mastoide"),
    ("I", 0, 99, "IX", "Doenças do aparelho circulatório"),
    ("J", 0, 99, "X", "Doenças do aparelho respiratório"),
    ("K", 0, 93, "XI", "Doenças do aparelho digestivo"),
    ("L", 0, 99, "XII", "Doenças da pele e do tecido subcutâneo"),
    ("M", 0, 99, "XIII", "Doenças do sistema osteomuscular e do tecido conjuntivo"),
    ("N", 0, 99, "XIV", "Doenças do aparelho geniturinário"),
    ("O", 0, 99, "XV", "Gravidez, parto e puerpério"),
    ("P", 0, 96, "XVI", "Afecções originadas no período perinatal"),
    ("Q", 0, 99, "XVII", "Malformações congênitas e anomalias cromossômicas"),
    ("R", 0, 99, "XVIII", "Sintomas, sinais e achados anormais"),
    ("S", 0, 99, "XIX", "Lesões, envenenamentos e outras consequências de causas externas"),
    ("T", 0, 98, "XIX", "Lesões, envenenamentos e outras consequências de causas externas"),
    ("V", 1, 99, "XX", "Causas externas de morbidade e mortalidade"),
    ("W", 0, 99, "XX", "Causas externas de morbidade e mortalidade"),
    ("X", 0, 99, "XX", "Causas externas de morbidade e mortalidade"),
    ("Y", 0, 98, "XX", "Causas externas de morbidade e mortalidade"),
    ("Z", 0, 99, "XXI", "Fatores que influenciam o estado de saúde"),
    ("U", 0, 99, "XXII", "Códigos para propósitos especiais"),
]


# ---------------------------------------------------------------------------
# 2. Classificação das AIH
# ---------------------------------------------------------------------------

def _expr_cid_capitulo(col: str = "DIAG_PRINC") -> pl.Expr:
    """Expressão Polars que devolve o capítulo CID-10 ("I".."XXII") a partir do código."""
    letra = pl.col(col).str.to_uppercase().str.slice(0, 1)
    num = pl.col(col).str.slice(1, 2).cast(pl.Int32, strict=False)
    expr = pl.lit(None, dtype=pl.Utf8)
    # constrói a cadeia de when/then de trás para frente
    for l, a, b, cap, _ in reversed(_CID_CAPITULOS):
        expr = pl.when((letra == l) & (num >= a) & (num <= b)).then(pl.lit(cap)).otherwise(expr)
    return expr


def classificar_areas(
    aih: pl.DataFrame,
    nivel: str = "subgrupo",
    nomes_sigtap: dict[str, str] | None = None,
) -> pl.DataFrame:
    """
    Acrescenta a `aih` as colunas:
      area_cod   "0408" (subgrupo, 4 dígitos) ou "040801" (forma de organização, 6 dígitos)
      area_nome  "04.08 Cirurgia do sistema osteomuscular"
      cid_cap    capítulo CID-10 do DIAG_PRINC ("IX"), se a coluna existir
      espec_nome especialidade do leito, se ESPEC existir

    nivel: "subgrupo" (padrão) ou "forma".
    nomes_sigtap: dicionário código→nome lido de tb_sub_grupo.txt (chaves "0408")
                  ou tb_forma_organizacao.txt (chaves "040801"). Se None, usa o
                  fallback embutido (só para subgrupo).
    """
    if nivel not in ("subgrupo", "forma"):
        raise ValueError("nivel deve ser 'subgrupo' ou 'forma'")
    ncar = 4 if nivel == "subgrupo" else 6
    nomes = nomes_sigtap if nomes_sigtap else (SUBGRUPOS_SIGTAP_FALLBACK if nivel == "subgrupo" else {})

    df = aih.with_columns(
        pl.col("PROC_REA").cast(pl.Utf8).str.zfill(10).str.slice(0, ncar).alias("area_cod")
    )
    # nome legível: "04.08 Nome"
    cod_fmt = (
        pl.col("area_cod").str.slice(0, 2) + pl.lit(".") + pl.col("area_cod").str.slice(2, 2)
        if ncar == 4
        else pl.col("area_cod").str.slice(0, 2) + pl.lit(".") + pl.col("area_cod").str.slice(2, 2)
        + pl.lit(".") + pl.col("area_cod").str.slice(4, 2)
    )
    nome_expr = pl.col("area_cod").replace_strict(nomes, default=pl.lit("Outros"), return_dtype=pl.Utf8)
    df = df.with_columns((cod_fmt + pl.lit(" ") + nome_expr).alias("area_nome"))

    if "DIAG_PRINC" in df.columns:
        df = df.with_columns(_expr_cid_capitulo("DIAG_PRINC").alias("cid_cap"))
    if "ESPEC" in df.columns:
        df = df.with_columns(
            pl.col("ESPEC").cast(pl.Utf8).str.zfill(2)
            .replace_strict(ESPEC_SIH, default=pl.lit("Outros"), return_dtype=pl.Utf8)
            .alias("espec_nome")
        )
    return df


# ---------------------------------------------------------------------------
# 3. Distâncias
# ---------------------------------------------------------------------------

def _haversine_expr(lat1: str, lon1: str, lat2: str, lon2: str) -> pl.Expr:
    """Distância em km entre dois pares (lat, lon) em graus."""
    r = 6371.0088
    p1 = pl.col(lat1).radians()
    p2 = pl.col(lat2).radians()
    dphi = (pl.col(lat2) - pl.col(lat1)).radians()
    dlmb = (pl.col(lon2) - pl.col(lon1)).radians()
    a = (dphi / 2).sin().pow(2) + p1.cos() * p2.cos() * (dlmb / 2).sin().pow(2)
    return 2 * r * a.sqrt().arcsin()


# ---------------------------------------------------------------------------
# 4. Huff por área
# ---------------------------------------------------------------------------

def huff_por_area(
    aih_cls: pl.DataFrame,
    hospitais: pl.DataFrame,
    municipios: pl.DataFrame,
    beta: float = 1.5,
    d_min_km: float = 3.0,
    peso: str = "n",
    areas: Iterable[str] | None = None,
    min_aih_area: int = 30,
    filtro_cid: Iterable[str] | None = None,
    filtro_espec: Iterable[str] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """
    Modelo de Huff por área da assistência.

    P(i→j | k) = M_jk · d_ij^-β / Σ_j' M_j'k · d_ij'^-β

    aih_cls       saída de classificar_areas()
    hospitais     conjunto de hospitais analisados (CNES, NOME, LAT, LON, COD_MUN)
    municipios    centroides (COD_MUN, NOME, LAT, LON[, REGIAO][, POP])
    beta          expoente de decaimento pela distância
    d_min_km      piso de distância (intra-municipal) para evitar d=0
    peso          "n" (nº de AIH) ou "valor" (VAL_TOT) como massa do hospital
    areas         subconjunto de area_cod a calcular (None = todas)
    min_aih_area  descarta hospital×área com massa menor que isso
    filtro_cid    capítulos CID-10 a manter (ex.: ["IX"]) — aplica-se antes de tudo
    filtro_espec  códigos ESPEC a manter (ex.: ["01"])

    Observação metodológica: a demanda de cada município na área k é a soma das
    AIH de residentes de i em k em TODOS os hospitais presentes em `aih_cls`,
    não só no conjunto `hospitais`. Se `hospitais` for um subconjunto, o
    fluxo esperado total do conjunto fica superestimado (toda a demanda é
    distribuída entre os hospitais do conjunto). Para a leitura mais limpa,
    passe em `hospitais` todos os hospitais com produção na área.

    Retorna (od, resumo).
    """
    if peso not in ("n", "valor"):
        raise ValueError("peso deve ser 'n' ou 'valor'")
    if peso == "valor" and "VAL_TOT" not in aih_cls.columns:
        raise ValueError("peso='valor' exige a coluna VAL_TOT")

    df = aih_cls
    if filtro_cid is not None and "cid_cap" in df.columns:
        df = df.filter(pl.col("cid_cap").is_in(list(filtro_cid)))
    if filtro_espec is not None and "ESPEC" in df.columns:
        df = df.filter(pl.col("ESPEC").cast(pl.Utf8).str.zfill(2).is_in(list(filtro_espec)))
    if areas is not None:
        df = df.filter(pl.col("area_cod").is_in(list(areas)))

    df = df.with_columns(
        pl.col("CNES").cast(pl.Utf8).str.zfill(7),
        pl.col("MUNIC_RES").cast(pl.Utf8).str.slice(0, 6),
        pl.lit(1.0).alias("_n"),
        (pl.col("VAL_TOT").cast(pl.Float64) if "VAL_TOT" in df.columns else pl.lit(0.0)).alias("_val"),
    )

    hosp = hospitais.with_columns(
        pl.col("CNES").cast(pl.Utf8).str.zfill(7),
        pl.col("COD_MUN").cast(pl.Utf8).str.slice(0, 6),
        pl.col("LAT").cast(pl.Float64), pl.col("LON").cast(pl.Float64),
    ).select(["CNES", "NOME", "LAT", "LON", "COD_MUN"]).unique(subset=["CNES"])

    mun_cols = ["COD_MUN", "NOME", "LAT", "LON"] + [c for c in ("REGIAO", "POP") if c in municipios.columns]
    mun = municipios.with_columns(
        pl.col("COD_MUN").cast(pl.Utf8).str.slice(0, 6),
        pl.col("LAT").cast(pl.Float64), pl.col("LON").cast(pl.Float64),
    ).select(mun_cols).unique(subset=["COD_MUN"])
    if "REGIAO" not in mun.columns:
        mun = mun.with_columns(pl.lit(None, dtype=pl.Utf8).alias("REGIAO"))
    if "POP" not in mun.columns:
        mun = mun.with_columns(pl.lit(None, dtype=pl.Int64).alias("POP"))

    # --- demanda por município × área (todos os hospitais do dado) ------------
    demanda = (
        df.group_by(["area_cod", "MUNIC_RES"])
        .agg(pl.col("_n").sum().alias("demanda_n"), pl.col("_val").sum().alias("demanda_val"))
    )

    # --- fluxo observado hospital × área × município (só hospitais do conjunto)
    obs = (
        df.filter(pl.col("CNES").is_in(hosp["CNES"].implode()))
        .group_by(["CNES", "area_cod", "area_nome", "MUNIC_RES"])
        .agg(pl.col("_n").sum().alias("n_obs"), pl.col("_val").sum().alias("val_obs"))
    )

    # --- massa do hospital na área ------------------------------------------
    massa = (
        obs.group_by(["CNES", "area_cod", "area_nome"])
        .agg(pl.col("n_obs").sum().alias("massa_n"), pl.col("val_obs").sum().alias("massa_val"))
        .with_columns((pl.col("massa_n") if peso == "n" else pl.col("massa_val")).alias("massa"))
        .filter(pl.col("massa_n") >= min_aih_area)
    )
    if massa.is_empty():
        vazio = pl.DataFrame()
        return vazio, vazio

    # --- universo de municípios: com demanda, ou na(s) UF(s) dos hospitais ----
    ufs = hosp["COD_MUN"].str.slice(0, 2).unique()
    com_demanda = demanda["MUNIC_RES"].unique()
    universo = mun.filter(
        pl.col("COD_MUN").str.slice(0, 2).is_in(ufs.implode()) | pl.col("COD_MUN").is_in(com_demanda.implode())
    )

    # --- distâncias hospital × município -------------------------------------
    pares = (
        hosp.select(["CNES", pl.col("LAT").alias("h_lat"), pl.col("LON").alias("h_lon")])
        .join(universo.select(["COD_MUN", pl.col("LAT").alias("m_lat"), pl.col("LON").alias("m_lon")]), how="cross")
        .with_columns(_haversine_expr("h_lat", "h_lon", "m_lat", "m_lon").alias("dist_km"))
        .with_columns(pl.max_horizontal(pl.col("dist_km"), pl.lit(d_min_km)).alias("dist_km"))
        .select(["CNES", "COD_MUN", "dist_km"])
    )

    # --- Huff ------------------------------------------------------------------
    od = (
        massa.select(["CNES", "area_cod", "area_nome", "massa"])
        .join(pares, on="CNES", how="inner")
        .with_columns((pl.col("massa") * pl.col("dist_km").pow(-beta)).alias("_w"))
        .with_columns((pl.col("_w") / pl.col("_w").sum().over(["area_cod", "COD_MUN"])).alias("p_huff"))
        .drop("_w")
        .join(demanda.rename({"MUNIC_RES": "COD_MUN"}), on=["area_cod", "COD_MUN"], how="left")
        .with_columns(pl.col("demanda_n").fill_null(0.0), pl.col("demanda_val").fill_null(0.0))
        .with_columns((pl.col("p_huff") * pl.col("demanda_n")).alias("n_esp"))
        .join(obs.rename({"MUNIC_RES": "COD_MUN"}).drop("area_nome"), on=["CNES", "area_cod", "COD_MUN"], how="left")
        .with_columns(pl.col("n_obs").fill_null(0.0), pl.col("val_obs").fill_null(0.0))
        .with_columns(
            pl.when(pl.col("demanda_n") > 0).then(pl.col("n_obs") / pl.col("demanda_n")).otherwise(None).alias("share_obs"),
            (pl.col("n_obs") - pl.col("n_esp")).alias("dif_obs_esp"),
        )
        .join(mun.select(["COD_MUN", pl.col("NOME").alias("mun_nome"), "REGIAO", "POP"]), on="COD_MUN", how="left")
        .join(hosp.select(["CNES", pl.col("NOME").alias("hosp_nome"), pl.col("COD_MUN").alias("hosp_mun")]), on="CNES", how="left")
    )

    # região do hospital = região do seu município
    reg_hosp = mun.select([pl.col("COD_MUN").alias("hosp_mun"), pl.col("REGIAO").alias("hosp_regiao")])
    od = od.join(reg_hosp, on="hosp_mun", how="left").with_columns(
        (pl.col("COD_MUN") == pl.col("hosp_mun")).alias("mesmo_mun"),
        (pl.col("REGIAO") == pl.col("hosp_regiao")).fill_null(False).alias("mesma_regiao"),
    )

    # --- resumo hospital × área ----------------------------------------------
    # nº de municípios que respondem por 80% das internações observadas
    n80 = (
        od.filter(pl.col("n_obs") > 0)
        .sort(["CNES", "area_cod", "n_obs"], descending=[False, False, True])
        .with_columns(
            (pl.col("n_obs").cum_sum().over(["CNES", "area_cod"]) / pl.col("n_obs").sum().over(["CNES", "area_cod"])).alias("_acum")
        )
        .with_columns(pl.col("_acum").shift(1, fill_value=0.0).over(["CNES", "area_cod"]).alias("_acum_ant"))
        .filter(pl.col("_acum_ant") < 0.8)
        .group_by(["CNES", "area_cod"]).agg(pl.len().alias("n_mun_80"))
    )

    resumo = (
        od.group_by(["CNES", "hosp_nome", "area_cod", "area_nome"])
        .agg(
            pl.col("massa").first(),
            pl.col("n_obs").sum().alias("capt_obs"),
            pl.col("n_esp").sum().alias("capt_esp"),
            pl.col("val_obs").sum().alias("val_obs"),
            (pl.col("n_obs") * pl.col("dist_km")).sum().alias("_od"),
            (pl.col("n_esp") * pl.col("dist_km")).sum().alias("_ed"),
            pl.col("n_obs").filter(pl.col("mesmo_mun")).sum().alias("_obs_mesmo_mun"),
            pl.col("n_obs").filter(pl.col("mesma_regiao")).sum().alias("_obs_mesma_reg"),
            pl.col("hosp_regiao").first().alias("hosp_regiao"),
            (pl.col("n_obs") > 0).sum().alias("n_mun_atendidos"),
        )
        .with_columns(
            (pl.col("capt_obs") / pl.col("capt_esp")).alias("razao_obs_esp"),
            (pl.col("_od") / pl.col("capt_obs")).alias("dist_media_obs_km"),
            (pl.col("_ed") / pl.col("capt_esp")).alias("dist_media_esp_km"),
            (1 - pl.col("_obs_mesmo_mun") / pl.col("capt_obs")).alias("pct_fora_mun"),
            pl.when(pl.col("hosp_regiao").is_not_null())
              .then(1 - pl.col("_obs_mesma_reg") / pl.col("capt_obs")).otherwise(None).alias("pct_fora_regiao"),
            (pl.col("massa") / pl.col("massa").sum().over("area_cod")).alias("share_massa_area"),
        )
        .join(n80, on=["CNES", "area_cod"], how="left")
        .drop(["_od", "_ed", "_obs_mesmo_mun", "_obs_mesma_reg"])
        .sort(["area_cod", "capt_obs"], descending=[False, True])
    )

    od = od.select([
        "CNES", "hosp_nome", "area_cod", "area_nome", "COD_MUN", "mun_nome", "REGIAO", "POP",
        "dist_km", "massa", "p_huff", "demanda_n", "demanda_val", "n_esp", "n_obs", "val_obs",
        "share_obs", "dif_obs_esp", "mesmo_mun", "mesma_regiao",
    ])
    return od, resumo


# ---------------------------------------------------------------------------
# 5. Payload para o mapa (JS)
# ---------------------------------------------------------------------------

METRICAS = {
    "p_huff":      {"label": "Participação esperada (Huff)",         "formato": "pct", "tipo": "seq"},
    "share_obs":   {"label": "Participação observada",               "formato": "pct", "tipo": "seq"},
    "dif_obs_esp": {"label": "Observado − esperado (internações)",   "formato": "num", "tipo": "div"},
    "n_obs":       {"label": "Internações observadas",               "formato": "num", "tipo": "seq"},
    "n_esp":       {"label": "Internações esperadas",                "formato": "num", "tipo": "seq"},
}


def _quantis(valores: list[float], k: int = 5) -> list[float]:
    v = sorted(x for x in valores if x is not None and not math.isnan(x))
    if len(v) < k + 1:
        return v[1:] if len(v) > 1 else v
    out = []
    for q in range(1, k):
        idx = int(round(q / k * (len(v) - 1)))
        out.append(v[idx])
    # remove repetidos preservando ordem
    uniq = []
    for x in out:
        if not uniq or x > uniq[-1]:
            uniq.append(x)
    return uniq


def payload_mapa(
    od: pl.DataFrame,
    resumo: pl.DataFrame,
    area_cod: str,
    metrica: str = "p_huff",
    cnes: str | None = None,
    hospitais: pl.DataFrame | None = None,
) -> dict:
    """
    Monta o dicionário que a página mapa_atratividade.html consome via
    window.gsp.setData(payload).

    cnes=None → mapa mostra, em cada município, o hospital dominante
    (maior p_huff) e o valor da métrica para esse hospital.
    hospitais → se informado, acrescenta lat/lon a cada hospital do payload
    (necessário para os marcadores).
    """
    if metrica not in METRICAS:
        raise ValueError(f"metrica deve ser uma de {list(METRICAS)}")
    sub = od.filter(pl.col("area_cod") == area_cod)
    if sub.is_empty():
        return {"area": {"cod": area_cod, "nome": area_cod}, "hospitais": [], "valores": {}, "breaks": [],
                "detalhe": {}, "dominante": {},
                "metrica": {"id": metrica, **METRICAS[metrica]}, "selecionado": cnes}

    area_nome = sub["area_nome"][0]
    res = resumo.filter(pl.col("area_cod") == area_cod)
    coords = coordenadas_hospitais(hospitais) if hospitais is not None else {}

    hosp_rows = []
    for r in res.iter_rows(named=True):
        xy = coords.get(r["CNES"], {})
        hosp_rows.append({
            "cnes": r["CNES"], "nome": r["hosp_nome"], "massa": r["massa"],
            "lat": xy.get("lat"), "lon": xy.get("lon"),
            "capt_obs": r["capt_obs"], "capt_esp": r["capt_esp"],
            "razao": r["razao_obs_esp"], "n_mun_80": r["n_mun_80"],
            "dist_obs": r["dist_media_obs_km"], "dist_esp": r["dist_media_esp_km"],
            "pct_fora_mun": r["pct_fora_mun"], "pct_fora_regiao": r["pct_fora_regiao"],
            "share_massa": r["share_massa_area"],
        })

    if cnes is not None:
        s = sub.filter(pl.col("CNES") == cnes)
        valores = {c: v for c, v in zip(s["COD_MUN"], s[metrica]) if v is not None}
        detalhe = {c: {"n_obs": a, "n_esp": b, "demanda": d, "p": p, "dist": k}
                   for c, a, b, d, p, k in zip(s["COD_MUN"], s["n_obs"], s["n_esp"], s["demanda_n"], s["p_huff"], s["dist_km"])}
        dominante = {}
    else:
        dom = (
            sub.sort(["COD_MUN", "p_huff"], descending=[False, True])
            .group_by("COD_MUN", maintain_order=True).first()
        )
        valores = {c: v for c, v in zip(dom["COD_MUN"], dom[metrica]) if v is not None}
        detalhe = {c: {"n_obs": a, "n_esp": b, "demanda": d, "p": p, "dist": k}
                   for c, a, b, d, p, k in zip(dom["COD_MUN"], dom["n_obs"], dom["n_esp"], dom["demanda_n"], dom["p_huff"], dom["dist_km"])}
        dominante = {c: {"cnes": h, "nome": n} for c, h, n in zip(dom["COD_MUN"], dom["CNES"], dom["hosp_nome"])}

    tipo = METRICAS[metrica]["tipo"]
    if tipo == "div":
        mx = max((abs(v) for v in valores.values()), default=1.0) or 1.0
        breaks = [-mx * 0.6, -mx * 0.2, mx * 0.2, mx * 0.6]
    else:
        breaks = _quantis([v for v in valores.values() if v and v > 0], 5)

    return {
        "area": {"cod": area_cod, "nome": area_nome},
        "metrica": {"id": metrica, **METRICAS[metrica]},
        "selecionado": cnes,
        "hospitais": hosp_rows,
        "valores": valores,
        "detalhe": detalhe,
        "dominante": dominante,
        "breaks": breaks,
    }


def coordenadas_hospitais(hospitais: pl.DataFrame) -> dict[str, dict]:
    """{cnes: {lat, lon, nome}} para o mapa posicionar os marcadores."""
    h = hospitais.with_columns(pl.col("CNES").cast(pl.Utf8).str.zfill(7))
    return {c: {"lat": a, "lon": b, "nome": n} for c, a, b, n in zip(h["CNES"], h["LAT"], h["LON"], h["NOME"])}


def listar_areas(resumo: pl.DataFrame) -> list[tuple[str, str, float]]:
    """[(area_cod, area_nome, total de AIH observadas)] ordenado por volume."""
    if resumo.is_empty():
        return []
    t = resumo.group_by(["area_cod", "area_nome"]).agg(pl.col("capt_obs").sum().alias("tot")).sort("tot", descending=True)
    return list(zip(t["area_cod"], t["area_nome"], t["tot"]))
