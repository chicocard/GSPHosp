"""
atratividade.py — atratividade hospitalar por faixas de distância e taxas municipais.

Conceito (definido pelo usuário do GSPHosp):
  * Em torno do hospital h traçam-se círculos concêntricos; a faixa f é a coroa entre
    dois raios consecutivos (a última é aberta: "> R km").
  * A população da faixa é a soma da população de TODOS os municípios cujo centroide
    cai na faixa (tenham ou não mandado pacientes ao hospital).
  * Para cada tipo de internação k (grupo/subgrupo/forma de organização SIGTAP, CID,
    especialidade, caráter, complexidade, faixa etária…):
        T_hkf = internações de residentes da faixa f internadas em h por k
                × (12 / meses carregados) / população_f × 10.000
    → internações por 10 mil hab./ano.
  * O perfil T × distância é a atratividade de h para k. Ajustam-se dois modelos de
    decaimento (mínimos quadrados ponderados pelo nº de internações, em escala log):
        potência:     T(d) = T0 · (d / d0)^(−β)
        exponencial:  T(d) = T0 · exp(−λ · (d − d0))          d0 = 10 km
    e fica o de maior R². Índices observados: d50/d80 (distância que acumula 50 %/80 %
    das internações), DMT (distância média ponderada pela taxa), % local (1ª faixa)
    e IA (alcance relativo: área sob a curva ajustada entre d0 e D, dividida pela área
    de uma atração sem decaimento; 0 = puramente local, 1 = atrai igual de perto e de longe).

Entradas (Polars):
  aih         CNES (7), MUNIC_RES (6/7), PROC_REA (10) e, se houver: NO_GRUPO, NO_SUB_GRUPO,
              NO_FORMA_ORGANIZACAO, NO_PROCEDIMENTO, DIAG_PRINC, NO_CID, ESPEC, CAR_INT,
              COMPLEXIDADE/COMPLEX, NO_FINANCIAMENTO, IDADE, COD_IDADE, SEXO, ANOMES_CMPT
              (ou ANO_CMPT + MES_CMPT).
  municipios  COD_MUN (6), NOME, LAT, LON, POP  (+ UF opcional)
  hospital    dict(cnes, nome, lat, lon, cod_mun)

Tudo vetorizado em Polars; só o ajuste de curvas usa Python puro (poucas faixas).
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import polars as pl

from papahr.dashboard.huff_area import (
    _CID_CAPITULOS, _expr_cid_capitulo, _haversine_expr, ESPEC_SIH, SUBGRUPOS_SIGTAP_FALLBACK,
)

# ---------------------------------------------------------------------------
# 1. Constantes e dicionários
# ---------------------------------------------------------------------------

LIMITES_PADRAO: tuple[float, ...] = (10.0, 25.0, 50.0, 100.0, 200.0, 400.0)
D0_KM = 10.0                 # distância de referência dos modelos
POR_FAIXAS = 10_000          # internações por 10 mil hab./ano (faixas)
POR_MUNICIPIOS = 1_000       # TIH municipal por 1.000 hab./ano (padrão DATASUS)
TODOS = "Hospital como um todo"

GRUPOS_SIGTAP: dict[str, str] = {
    "01": "Ações de promoção e prevenção em saúde",
    "02": "Procedimentos com finalidade diagnóstica",
    "03": "Procedimentos clínicos",
    "04": "Procedimentos cirúrgicos",
    "05": "Transplantes de órgãos, tecidos e células",
    "06": "Medicamentos",
    "07": "Órteses, próteses e materiais especiais",
    "08": "Ações complementares da atenção à saúde",
}
CARATER_SIH: dict[str, str] = {
    "01": "Eletivo",
    "02": "Urgência",
    "03": "Acidente no local de trabalho ou a serviço da empresa",
    "04": "Acidente no trajeto para o trabalho",
    "05": "Outros tipos de acidente de trânsito",
    "06": "Outros tipos de lesões e envenenamentos",
}
COMPLEXIDADE_SIH: dict[str, str] = {"01": "Atenção básica", "02": "Média complexidade", "03": "Alta complexidade"}
SEXO_SIH: dict[str, str] = {"1": "Masculino", "3": "Feminino", "M": "Masculino", "F": "Feminino"}
_CAP_NOME = {cap: nome for _, _, _, cap, nome in _CID_CAPITULOS}
_FAIXAS_ETARIAS = [(0, 0, "0 anos"), (1, 4, "1–4 anos"), (5, 14, "5–14 anos"), (15, 24, "15–24 anos"),
                   (25, 44, "25–44 anos"), (45, 64, "45–64 anos"), (65, 79, "65–79 anos"), (80, 200, "80+ anos")]

UF_SIGLA: dict[str, str] = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO", "21": "MA", "22": "PI",
    "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE", "29": "BA", "31": "MG", "32": "ES",
    "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF",
}
UF_NOME: dict[str, str] = {
    "RO": "Rondônia", "AC": "Acre", "AM": "Amazonas", "RR": "Roraima", "PA": "Pará", "AP": "Amapá", "TO": "Tocantins",
    "MA": "Maranhão", "PI": "Piauí", "CE": "Ceará", "RN": "Rio Grande do Norte", "PB": "Paraíba", "PE": "Pernambuco",
    "AL": "Alagoas", "SE": "Sergipe", "BA": "Bahia", "MG": "Minas Gerais", "ES": "Espírito Santo", "RJ": "Rio de Janeiro",
    "SP": "São Paulo", "PR": "Paraná", "SC": "Santa Catarina", "RS": "Rio Grande do Sul", "MS": "Mato Grosso do Sul",
    "MT": "Mato Grosso", "GO": "Goiás", "DF": "Distrito Federal",
}

# id → (rótulo, colunas aceitas — a 1ª encontrada é usada; "PROC_REA" serve de reserva p/ SIGTAP)
NIVEIS: dict[str, tuple[str, tuple[str, ...]]] = {
    "todos": (TODOS, ()),
    "grupo": ("Grupo SIGTAP", ("NO_GRUPO", "Grupo de Procedimentos", "PROC_REA", "CO Procedimentos realizados")),
    "subgrupo": ("Subgrupo SIGTAP", ("NO_SUB_GRUPO", "SubGrupo de Procedimentos", "PROC_REA", "CO Procedimentos realizados")),
    "forma": ("Forma de organização SIGTAP", ("NO_FORMA_ORGANIZACAO", "Forma de organizacao", "PROC_REA", "CO Procedimentos realizados")),
    "procedimento": ("Procedimento SIGTAP", ("NO_PROCEDIMENTO", "Procedimentos realizados", "PROC_REA", "CO Procedimentos realizados")),
    "cid_capitulo": ("Capítulo CID-10", ("DIAG_PRINC",)),
    "cid": ("Diagnóstico principal (CID-10)", ("DIAG_PRINC",)),
    "especialidade": ("Especialidade do leito", ("ESPEC",)),
    "carater": ("Caráter da internação", ("CAR_INT",)),
    "complexidade": ("Complexidade", ("COMPLEXIDADE", "Complexidade do Procedimento", "COMPLEX")),
    "financiamento": ("Financiamento", ("NO_FINANCIAMENTO", "Financiamento")),
    "faixa_etaria": ("Faixa etária", ("IDADE",)),
    "sexo": ("Sexo", ("SEXO",)),
    "fonte": ("Fonte (SIH = SUS · CIHA = não SUS)", ("FONTE",)),
    "pagador": ("Pagador (CIHA)", ("PAGADOR",)),
    "modalidade": ("Modalidade (CIHA)", ("MODALIDADE",)),
}
FONTES = ("SIH", "CIHA")


# ---------------------------------------------------------------------------
# 2. Utilidades
# ---------------------------------------------------------------------------

def _col(df: pl.DataFrame | Iterable[str], candidatos: Sequence[str]) -> str | None:
    cols = list(df.columns) if isinstance(df, pl.DataFrame) else list(df)
    for c in candidatos:
        if c in cols:
            return c
    return None


def _mun6(col: str) -> pl.Expr:
    """Código IBGE em 6 dígitos: '3106200' (7 c/ DV) → '310620'; '0310620' → '310620'; 310620 → '310620'."""
    c = pl.col(col).cast(pl.Utf8, strict=False).str.strip_chars()
    return (
        pl.when(c.str.len_chars() == 7)
        .then(pl.when(c.str.starts_with("0")).then(c.str.slice(1, 6)).otherwise(c.str.slice(0, 6)))
        .otherwise(c.str.zfill(6).str.slice(0, 6))
    )


def _cnes7(col: str) -> pl.Expr:
    return pl.col(col).cast(pl.Utf8, strict=False).str.strip_chars().str.zfill(7)


def _proc_col(df: pl.DataFrame) -> str | None:
    return _col(df, ("PROC_REA", "CO Procedimentos realizados", "SP_PROCREA", "PA_PROC_ID"))


def _lookup(expr: pl.Expr, dic: dict[str, str], default: str = "(sem nome)") -> pl.Expr:
    return expr.replace_strict(dic, default=default, return_dtype=pl.Utf8)


def meses_competencia(aih: pl.DataFrame) -> int:
    """Nº de competências (meses) distintas no dataset; usado para anualizar as taxas."""
    if aih.is_empty():
        return 12
    c = _col(aih, ("ANOMES_CMPT", "Ano/Mes processamento", "ANOMES_MVM", "Ano/Mes Processamento", "PA_CMP"))
    if c:
        n = aih.select(pl.col(c).cast(pl.Utf8, strict=False).str.slice(0, 6).n_unique()).item()
        return max(1, int(n or 0))
    a, m = _col(aih, ("ANO_CMPT", "SP_AA")), _col(aih, ("MES_CMPT", "SP_MM"))
    if a and m:
        n = aih.select(pl.concat_str([pl.col(a).cast(pl.Utf8, strict=False), pl.col(m).cast(pl.Utf8, strict=False).str.zfill(2)]).n_unique()).item()
        return max(1, int(n or 0))
    d = _col(aih, ("DT_INTER", "Data Internacao", "DT_CMPT"))
    if d and aih.schema[d] in (pl.Date, pl.Datetime):
        n = aih.select(pl.col(d).dt.strftime("%Y%m").n_unique()).item()
        return max(1, int(n or 0))
    return 12


def fator_anual(meses: int) -> float:
    return 12.0 / max(1, int(meses))


# ---------------------------------------------------------------------------
# 3. Classificação das internações por nível
# ---------------------------------------------------------------------------

def fontes_presentes(aih: pl.DataFrame) -> list[str]:
    """Fontes no dataset (coluna FONTE: 'SIH' = SUS, 'CIHA' = não SUS). Sem a coluna, assume só SIH."""
    if aih.is_empty() or "FONTE" not in aih.columns:
        return ["SIH"]
    return [f for f in FONTES if f in set(aih["FONTE"].drop_nulls().unique().to_list())] or ["SIH"]


def filtrar_fonte(aih: pl.DataFrame, fonte: str | None) -> pl.DataFrame:
    """Recorta o dataset por fonte ('SIH', 'CIHA' ou None/'ambas' = tudo)."""
    if not fonte or fonte.lower() in ("ambas", "todas", "*") or "FONTE" not in aih.columns:
        return aih
    return aih.filter(pl.col("FONTE") == fonte)


def niveis_disponiveis(aih: pl.DataFrame) -> list[tuple[str, str]]:
    """[(id, rótulo)] dos níveis que o dataset permite."""
    out = [("todos", TODOS)]
    for k, (rot, cols) in NIVEIS.items():
        if k == "todos" or not any(c in aih.columns for c in cols):
            continue
        if k in ("fonte", "pagador", "modalidade"):     # só quando a coluna tem mais de um valor (dataset com SIH e CIHA)
            c = cols[0]
            if aih.is_empty() or aih[c].n_unique() < 2:
                continue
        out.append((k, rot))
    return out


def expr_categoria(aih: pl.DataFrame, nivel: str) -> pl.Expr | None:
    """Expressão que rotula cada AIH no nível pedido (None se faltar coluna)."""
    cols = aih.columns
    proc = _proc_col(aih)
    pc = pl.col(proc).cast(pl.Utf8, strict=False).str.zfill(10) if proc else None

    def nome_ou_codigo(nome_cols: Sequence[str], ncod: int, fallback: dict[str, str] | None) -> pl.Expr | None:
        nome = _col(cols, nome_cols)
        if pc is not None:
            cod = pc.str.slice(0, ncod)
            if nome:
                # o pipeline e o SIGTAP gravam "02-Nome" (código-nome): tira o prefixo para não repetir o código
                nm = pl.col(nome).cast(pl.Utf8, strict=False).str.replace(r"^\d{2,10}\s*-\s*", "")
                return pl.concat_str([cod, pl.lit(" "), nm.fill_null("(sem nome)")])
            if fallback:
                return pl.concat_str([cod, pl.lit(" "), _lookup(cod, fallback, "(sem nome)")])
            return cod
        return pl.col(nome).cast(pl.Utf8, strict=False) if nome else None

    if nivel == "todos":
        return pl.lit(TODOS)
    if nivel == "grupo":
        return nome_ou_codigo(("NO_GRUPO", "Grupo de Procedimentos"), 2, GRUPOS_SIGTAP)
    if nivel == "subgrupo":
        return nome_ou_codigo(("NO_SUB_GRUPO", "SubGrupo de Procedimentos"), 4, SUBGRUPOS_SIGTAP_FALLBACK)
    if nivel == "forma":
        return nome_ou_codigo(("NO_FORMA_ORGANIZACAO", "Forma de organizacao"), 6, None)
    if nivel == "procedimento":
        return nome_ou_codigo(("NO_PROCEDIMENTO", "Procedimentos realizados"), 10, None)
    if nivel == "cid_capitulo":
        if "DIAG_PRINC" not in cols:
            return None
        cap = _expr_cid_capitulo("DIAG_PRINC")
        return pl.concat_str([pl.lit("Cap. "), cap.fill_null("?"), pl.lit(" – "), _lookup(cap.fill_null("?"), _CAP_NOME, "Não classificado")])
    if nivel == "cid":
        if "DIAG_PRINC" not in cols:
            return None
        cod = pl.col("DIAG_PRINC").cast(pl.Utf8, strict=False).str.to_uppercase().str.slice(0, 4)
        n = _col(cols, ("NO_CID", "Nome da Mobirdade (CID)"))
        return pl.concat_str([cod, pl.lit(" "), pl.col(n).cast(pl.Utf8, strict=False).fill_null("")]) if n else cod
    if nivel == "especialidade":
        if "ESPEC" not in cols:
            return None
        c = pl.col("ESPEC").cast(pl.Utf8, strict=False).str.zfill(2)
        return pl.concat_str([c, pl.lit(" "), _lookup(c, ESPEC_SIH, "Outra")])
    if nivel == "carater":
        if "CAR_INT" not in cols:
            return None
        c = pl.col("CAR_INT").cast(pl.Utf8, strict=False).str.zfill(2)
        return pl.concat_str([c, pl.lit(" "), _lookup(c, CARATER_SIH, "Outro")])
    if nivel == "complexidade":
        n = _col(cols, ("COMPLEXIDADE", "Complexidade do Procedimento"))
        if n:
            return pl.col(n).cast(pl.Utf8, strict=False).fill_null("(sem informação)")
        if "COMPLEX" in cols:
            c = pl.col("COMPLEX").cast(pl.Utf8, strict=False).str.zfill(2)
            return _lookup(c, COMPLEXIDADE_SIH, "Outra")
        return None
    if nivel == "financiamento":
        n = _col(cols, ("NO_FINANCIAMENTO", "Financiamento"))
        return pl.col(n).cast(pl.Utf8, strict=False).fill_null("(sem informação)") if n else None
    if nivel == "faixa_etaria":
        if "IDADE" not in cols:
            return None
        idade = pl.col("IDADE").cast(pl.Float64, strict=False)
        if "COD_IDADE" in cols:   # 2 = dias, 3 = meses → menor de 1 ano
            idade = pl.when(pl.col("COD_IDADE").cast(pl.Utf8, strict=False).str.zfill(1).is_in(["2", "3"])).then(0.0).otherwise(idade)
        expr = pl.lit("9 · (sem idade)")
        for i, (a, b, rot) in reversed(list(enumerate(_FAIXAS_ETARIAS, start=1))):
            expr = pl.when((idade >= a) & (idade <= b)).then(pl.lit(f"{i} · {rot}")).otherwise(expr)
        return expr
    if nivel in ("fonte", "pagador", "modalidade"):
        n = {"fonte": "FONTE", "pagador": "PAGADOR", "modalidade": "MODALIDADE"}[nivel]
        if n not in cols:
            return None
        v = pl.col(n).cast(pl.Utf8, strict=False).str.strip_chars()
        return pl.when(v.is_null() | (v == "")).then(pl.lit("(sem informação)")).otherwise(v)
    if nivel == "sexo":
        if "SEXO" not in cols:
            return None
        return _lookup(pl.col("SEXO").cast(pl.Utf8, strict=False).str.strip_chars(), SEXO_SIH, "Ignorado")
    raise ValueError(f"nível desconhecido: {nivel}")


def classificar(aih: pl.DataFrame, nivel: str) -> pl.DataFrame:
    """Devolve o dataset com a coluna `categoria` (string) do nível pedido."""
    e = expr_categoria(aih, nivel)
    if e is None:
        raise ValueError(f"o dataset não tem as colunas do nível '{nivel}' ({NIVEIS[nivel][1]})")
    return aih.with_columns(e.alias("categoria"))


def categorias(aih_cls: pl.DataFrame, cnes: str | None = None) -> pl.DataFrame:
    """categoria | n (ordem decrescente), opcionalmente só de um hospital."""
    df = aih_cls
    if cnes:
        df = df.filter(_cnes7("CNES") == str(cnes).strip().zfill(7))
    return df.group_by("categoria").len().rename({"len": "n"}).sort(["n", "categoria"], descending=[True, False])


# ---------------------------------------------------------------------------
# 4. Distâncias e faixas
# ---------------------------------------------------------------------------

def rotulos_faixas(limites: Sequence[float]) -> list[str]:
    lim = [float(x) for x in limites]
    rot, prev = [], 0.0
    for x in lim:
        rot.append(f"{prev:g}–{x:g} km")
        prev = x
    rot.append(f"> {prev:g} km")
    return rot


def validar_limites(limites: Iterable[float]) -> tuple[float, ...]:
    lim = sorted({float(x) for x in limites if x is not None and float(x) > 0})
    if len(lim) < 2:
        raise ValueError("informe pelo menos dois raios (ex.: 10, 25, 50, 100, 200, 400)")
    return tuple(lim)


def _expr_faixa(dist_col: str, limites: Sequence[float]) -> pl.Expr:
    faixa = pl.lit(len(limites))
    for i in reversed(range(len(limites))):
        faixa = pl.when(pl.col(dist_col) <= limites[i]).then(pl.lit(i)).otherwise(faixa)
    return faixa.cast(pl.Int32)


def distancias(hospital: dict, municipios: pl.DataFrame, limites: Sequence[float]) -> pl.DataFrame:
    """COD_MUN | NOME | LAT | LON | POP | UF | dist_km | faixa, para todos os municípios com coordenadas.

    O município do próprio hospital recebe distância 0 (está sempre na 1ª faixa, mesmo
    quando o hospital fica longe do centroide de uma cidade grande).
    """
    base = municipios.with_columns(pl.col("COD_MUN").cast(pl.Utf8).str.slice(0, 6))
    if "POP" not in base.columns:
        base = base.with_columns(pl.lit(None, dtype=pl.Float64).alias("POP"))
    if "UF" not in base.columns:
        base = base.with_columns(_lookup(pl.col("COD_MUN").str.slice(0, 2), UF_SIGLA, "?").alias("UF"))
    base = base.with_columns(
        pl.lit(float(hospital["lat"])).alias("_hlat"), pl.lit(float(hospital["lon"])).alias("_hlon"),
        pl.col("LAT").cast(pl.Float64, strict=False), pl.col("LON").cast(pl.Float64, strict=False),
        pl.col("POP").cast(pl.Float64, strict=False).fill_null(0.0),
    ).filter(pl.col("LAT").is_not_null() & pl.col("LON").is_not_null())
    cod_h = str(hospital.get("cod_mun") or "")[:6]
    dist = _haversine_expr("LAT", "LON", "_hlat", "_hlon")
    if cod_h:
        dist = pl.when(pl.col("COD_MUN") == cod_h).then(0.0).otherwise(dist)
    return (
        base.with_columns(dist.alias("dist_km"))
        .with_columns(_expr_faixa("dist_km", limites).alias("faixa"))
        .drop(["_hlat", "_hlon"])
        .select(["COD_MUN", "NOME", "LAT", "LON", "POP", "UF", "dist_km", "faixa"])
    )


def tabela_faixas(dist_df: pl.DataFrame, limites: Sequence[float]) -> pl.DataFrame:
    """faixa | rotulo | d_min | d_max | n_mun | pop | d_ref (distância média ponderada pela população)."""
    rot = rotulos_faixas(limites)
    k = len(limites)
    bordas = pl.DataFrame({
        "faixa": list(range(k + 1)), "rotulo": rot,
        "d_min": [0.0] + [float(x) for x in limites],
        "d_max": [float(x) for x in limites] + [None],
    }).with_columns(pl.col("faixa").cast(pl.Int32), pl.col("d_max").cast(pl.Float64))
    agg = dist_df.group_by("faixa").agg(
        pl.len().alias("n_mun"), pl.col("POP").sum().alias("pop"),
        (pl.col("POP") * pl.col("dist_km")).sum().alias("_pd"), pl.col("dist_km").mean().alias("_dm"),
        pl.col("dist_km").max().alias("_dmax"),
    )
    out = bordas.join(agg, on="faixa", how="left").with_columns(
        pl.col("n_mun").fill_null(0), pl.col("pop").fill_null(0.0),
    ).with_columns(
        pl.when(pl.col("pop") > 0).then(pl.col("_pd") / pl.col("pop")).otherwise(pl.col("_dm")).alias("d_ref"),
        pl.coalesce([pl.col("d_max"), pl.col("_dmax"), pl.col("d_min")]).alias("d_max"),
    )
    # faixa sem município: d_ref = ponto médio da faixa. Na 1ª faixa o município do hospital tem
    # distância 0 (centroide), o que puxaria a média para ~0; o piso é o ponto médio da faixa
    # (os residentes da cidade-sede moram, em média, a alguns km do hospital).
    meio = (pl.col("d_min") + pl.col("d_max")) / 2
    out = out.with_columns(
        pl.when(pl.col("d_ref").is_null()).then(meio)
        .when(pl.col("faixa") == 0).then(pl.max_horizontal(pl.col("d_ref"), meio))
        .otherwise(pl.col("d_ref")).alias("d_ref")
    )
    return out.drop(["_pd", "_dm", "_dmax"]).sort("faixa")


# ---------------------------------------------------------------------------
# 5. Perfil taxa × faixa por tipo de internação
# ---------------------------------------------------------------------------

def perfil_faixas(
    aih: pl.DataFrame,
    hospital: dict,
    municipios: pl.DataFrame,
    *,
    nivel: str = "todos",
    categorias_sel: Sequence[str] | None = None,
    limites: Sequence[float] = LIMITES_PADRAO,
    meses: int | None = None,
    por: int = POR_FAIXAS,
    dist_df: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Uma linha por (categoria, faixa): n, pop, taxa (por `por` hab./ano), pct, cum_pct, d_ref, d_obs."""
    limites = validar_limites(limites)
    cnes = str(hospital["cnes"]).strip().zfill(7)
    if dist_df is None:
        dist_df = distancias(hospital, municipios, limites)
    faixas = tabela_faixas(dist_df, limites)
    meses = meses or meses_competencia(aih)
    fator = fator_anual(meses)

    col_cnes = _col(aih, ("CNES", "CO_CNES", "_cnes"))
    col_mun = _col(aih, ("MUNIC_RES", "PA_MUNPCN", "SP_M_PAC", "municipio_residencia"))
    if col_cnes is None or col_mun is None:
        raise ValueError("o dataset precisa de CNES e MUNIC_RES")
    h = aih.filter(_cnes7(col_cnes) == cnes)
    h = classificar(h, nivel)
    if categorias_sel:
        h = h.filter(pl.col("categoria").is_in(list(categorias_sel)))
    cont = (
        h.with_columns(_mun6(col_mun).alias("COD_MUN"))
        .group_by(["categoria", "COD_MUN"]).len().rename({"len": "n"})
        .join(dist_df.select(["COD_MUN", "dist_km", "faixa"]), on="COD_MUN", how="left")
    )
    sem_loc = cont.filter(pl.col("faixa").is_null())
    n_sem_loc = int(sem_loc["n"].sum() or 0) if not sem_loc.is_empty() else 0
    por_faixa = (
        cont.filter(pl.col("faixa").is_not_null())
        .group_by(["categoria", "faixa"])
        .agg(pl.col("n").sum().alias("n"), ((pl.col("n") * pl.col("dist_km")).sum() / pl.col("n").sum()).alias("d_obs"),
             pl.col("dist_km").max().alias("d_obs_max"))
    )
    if nivel == "todos":
        cats = pl.DataFrame({"categoria": [TODOS]})
    else:
        cats = h.select(pl.col("categoria").cast(pl.Utf8)).unique()
    grade = cats.join(faixas, how="cross")
    out = (
        grade.join(por_faixa, on=["categoria", "faixa"], how="left")
        .with_columns(pl.col("n").fill_null(0).cast(pl.Int64))
        .with_columns(
            pl.when(pl.col("pop") > 0).then(pl.col("n") * fator / pl.col("pop") * por).otherwise(None).alias("taxa"),
            pl.col("n").sum().over("categoria").alias("n_total"),
        )
        .with_columns(
            pl.when(pl.col("n_total") > 0).then(pl.col("n") / pl.col("n_total")).otherwise(None).alias("pct"),
        )
        .sort(["categoria", "faixa"])
        .with_columns(pl.col("pct").cum_sum().over("categoria").alias("cum_pct"))
        .with_columns(pl.lit(nivel).alias("nivel"), pl.lit(meses).alias("meses"), pl.lit(n_sem_loc).alias("n_sem_localizacao"))
    )
    return out.select(["nivel", "categoria", "faixa", "rotulo", "d_min", "d_max", "d_ref", "n_mun", "pop", "n", "taxa",
                       "pct", "cum_pct", "d_obs", "d_obs_max", "n_total", "meses", "n_sem_localizacao"])


def perfil_completo(aih, hospital, municipios, *, nivel, categorias_sel=None, limites=LIMITES_PADRAO,
                    meses=None, por=POR_FAIXAS) -> pl.DataFrame:
    """'Hospital como um todo' + categorias do nível, na mesma tabela."""
    limites = validar_limites(limites)
    dist_df = distancias(hospital, municipios, limites)
    meses = meses or meses_competencia(aih)
    todos = perfil_faixas(aih, hospital, municipios, nivel="todos", limites=limites, meses=meses, por=por, dist_df=dist_df)
    if nivel == "todos":
        return todos
    cats = perfil_faixas(aih, hospital, municipios, nivel=nivel, categorias_sel=categorias_sel, limites=limites,
                         meses=meses, por=por, dist_df=dist_df)
    return pl.concat([todos, cats], how="vertical")


# ---------------------------------------------------------------------------
# 6. Ajuste de decaimento e índices
# ---------------------------------------------------------------------------

def _wls(x: list[float], y: list[float], w: list[float]) -> tuple[float, float, float] | None:
    """Mínimos quadrados ponderados y = a + b x. Devolve (a, b, R²_ponderado)."""
    sw = sum(w)
    if len(x) < 2 or sw <= 0:
        return None
    mx = sum(wi * xi for wi, xi in zip(w, x)) / sw
    my = sum(wi * yi for wi, yi in zip(w, y)) / sw
    sxx = sum(wi * (xi - mx) ** 2 for wi, xi in zip(w, x))
    sxy = sum(wi * (xi - mx) * (yi - my) for wi, xi, yi in zip(w, x, y))
    if sxx <= 1e-12:
        return None
    b = sxy / sxx
    a = my - b * mx
    sst = sum(wi * (yi - my) ** 2 for wi, yi in zip(w, y))
    sse = sum(wi * (yi - a - b * xi) ** 2 for wi, xi, yi in zip(w, x, y))
    r2 = 1.0 - sse / sst if sst > 1e-12 else 1.0
    return a, b, max(0.0, min(1.0, r2))


def _interp_quantil(faixas: list[dict], q: float) -> float | None:
    """Distância em que a soma acumulada de internações atinge a fração q (interpolação linear na faixa)."""
    total = sum(f["n"] for f in faixas)
    if total <= 0:
        return None
    alvo = q * total
    acc = 0.0
    for f in faixas:
        n = f["n"]
        if acc + n >= alvo and n > 0:
            d0 = float(f["d_min"])
            d1 = float(f["d_max"] if f["d_max"] is not None else (f.get("d_obs_max") or d0))
            d1 = max(d1, d0)
            frac = (alvo - acc) / n
            return d0 + frac * (d1 - d0)
        acc += n
    return None


def ajustar_categoria(linhas: list[dict], *, d0: float = D0_KM, d_max: float | None = None) -> dict:
    """Ajusta os modelos a uma categoria (lista de dicts das faixas, ordenada por faixa)."""
    n_total = int(sum(l["n"] for l in linhas))
    pts = [l for l in linhas if (l.get("taxa") or 0) > 0 and (l.get("pop") or 0) > 0 and l["n"] > 0]
    res: dict = {"n": n_total, "n_pontos": len(pts), "modelo": None, "T0": None, "beta": None, "lambda": None,
                 "R2": None, "d_meia": None, "formula": "", "ia": None}
    if len(pts) >= 2:
        x = [float(l["d_ref"]) for l in pts]
        y = [math.log(float(l["taxa"])) for l in pts]
        w = [float(l["n"]) for l in pts]
        xp = [math.log(max(xi, 0.5) / d0) for xi in x]
        xe = [xi - d0 for xi in x]
        fp = _wls(xp, y, w)
        fe = _wls(xe, y, w)
        cand = []
        if fp:
            a, b, r2 = fp
            cand.append(("potencia", r2, math.exp(a), -b))
        if fe:
            a, b, r2 = fe
            cand.append(("exponencial", r2, math.exp(a), -b))
        if cand:
            cand.sort(key=lambda c: (c[1], c[0] == "potencia"), reverse=True)
            modelo, r2, t0, decl = cand[0]
            res.update(modelo=modelo, R2=r2, T0=t0)
            D = float(d_max) if d_max else max(x) * 1.0
            if modelo == "potencia":
                beta = decl
                res["beta"] = beta
                res["d_meia"] = d0 * 2 ** (1.0 / beta) if beta > 0 else None
                res["formula"] = f"T(d) = {t0:.3g} · (d / {d0:g})^−{beta:.2f}" if beta >= 0 else f"T(d) = {t0:.3g} · (d / {d0:g})^{-beta:.2f}"
                if D > d0 and beta > 0:
                    if abs(beta - 1.0) < 1e-6:
                        area = t0 * d0 * math.log(D / d0)
                    else:
                        area = t0 * d0 / (1.0 - beta) * ((D / d0) ** (1.0 - beta) - 1.0)
                    res["ia"] = max(0.0, min(1.0, area / (t0 * (D - d0))))
                elif beta <= 0:
                    res["ia"] = 1.0
            else:
                lam = decl
                res["lambda"] = lam
                res["d_meia"] = d0 + math.log(2) / lam if lam > 0 else None
                res["formula"] = f"T(d) = {t0:.3g} · e^(−{lam:.4f}·(d − {d0:g}))" if lam >= 0 else f"T(d) = {t0:.3g} · e^({-lam:.4f}·(d − {d0:g}))"
                if D > d0 and lam > 0:
                    area = t0 / lam * (1.0 - math.exp(-lam * (D - d0)))
                    res["ia"] = max(0.0, min(1.0, area / (t0 * (D - d0))))
                elif lam <= 0:
                    res["ia"] = 1.0
    # índices observados
    res["d50"] = _interp_quantil(linhas, 0.5)
    res["d80"] = _interp_quantil(linhas, 0.8)
    tx = [(float(l["taxa"]), float(l["d_ref"])) for l in linhas if (l.get("taxa") or 0) > 0]
    res["dmt"] = sum(t * d for t, d in tx) / sum(t for t, _ in tx) if tx else None
    res["pct_local"] = (linhas[0]["n"] / n_total) if n_total > 0 and linhas else None
    res["taxa_local"] = linhas[0].get("taxa") if linhas else None
    return res


def ajustar_decaimento(perfil: pl.DataFrame, *, d0: float = D0_KM, limites: Sequence[float] | None = None) -> pl.DataFrame:
    """Uma linha por (nivel, categoria) com modelo, parâmetros, fórmula e índices."""
    if perfil.is_empty():
        return pl.DataFrame()
    d_max = float(max(limites)) if limites else float(perfil.filter(pl.col("d_max").is_not_null())["d_max"].max() or 0) or None
    rows = []
    for (nivel, cat), g in perfil.sort(["categoria", "faixa"]).group_by(["nivel", "categoria"], maintain_order=True):
        linhas = g.to_dicts()
        r = ajustar_categoria(linhas, d0=d0, d_max=d_max)
        r.update(nivel=nivel, categoria=cat)
        rows.append(r)
    cols = ["nivel", "categoria", "n", "n_pontos", "modelo", "T0", "beta", "lambda", "R2", "d_meia", "ia",
            "d50", "d80", "dmt", "pct_local", "taxa_local", "formula"]
    df = pl.DataFrame(rows).select(cols)
    # "Hospital como um todo" primeiro, depois por volume
    return df.with_columns((pl.col("categoria") == TODOS).alias("_t")).sort(["_t", "n"], descending=[True, True]).drop("_t")


def descrever_ajuste(r: dict, por: int = POR_FAIXAS) -> str:
    """Frase para a interface (uma categoria)."""
    if not r or not r.get("modelo"):
        n = r.get("n", 0) if r else 0
        return f"{n} internações — faixas insuficientes para ajustar um modelo (precisa de ≥ 2 faixas com internações)."
    un = f"por {por:,} hab./ano".replace(",", ".")
    nome = "potência" if r["modelo"] == "potencia" else "exponencial"
    s = f"Modelo {nome}: {r['formula']}  (R² = {r['R2']:.2f}; T em internações {un}). "
    if r.get("d_meia"):
        s += f"A taxa cai à metade da taxa a {D0_KM:g} km em {r['d_meia']:.0f} km. "
    if r.get("d50") is not None and r.get("d80") is not None:
        s += f"Metade das internações vem de até {r['d50']:.0f} km e 80 % de até {r['d80']:.0f} km. "
    if r.get("ia") is not None:
        s += f"IA (alcance relativo) = {r['ia']:.2f}."
    return s


# ---------------------------------------------------------------------------
# 7. Taxas municipais (TIH) e histograma
# ---------------------------------------------------------------------------

def taxas_municipais(
    aih: pl.DataFrame,
    municipios: pl.DataFrame,
    *,
    uf: str | None = None,
    nivel: str = "todos",
    categorias_sel: Sequence[str] | None = None,
    meses: int | None = None,
    por: int = POR_MUNICIPIOS,
    cnes_destaque: str | None = None,
) -> pl.DataFrame:
    """TIH por município de residência (internações em qualquer hospital do dataset ÷ população).

    uf: código IBGE de 2 dígitos ("31") ou sigla ("MG"); None = todos os municípios da tabela.
    """
    col_cnes = _col(aih, ("CNES", "CO_CNES", "_cnes"))
    col_mun = _col(aih, ("MUNIC_RES", "PA_MUNPCN", "SP_M_PAC", "municipio_residencia"))
    if col_mun is None:
        raise ValueError("o dataset precisa de MUNIC_RES")
    meses = meses or meses_competencia(aih)
    fator = fator_anual(meses)
    df = aih
    if nivel != "todos":
        df = classificar(df, nivel)
        if categorias_sel:
            df = df.filter(pl.col("categoria").is_in(list(categorias_sel)))
    df = df.with_columns(_mun6(col_mun).alias("COD_MUN"))
    cont = df.group_by("COD_MUN").len().rename({"len": "n"})
    if cnes_destaque and col_cnes:
        ch = df.filter(_cnes7(col_cnes) == str(cnes_destaque).strip().zfill(7)).group_by("COD_MUN").len().rename({"len": "n_hosp"})
        cont = cont.join(ch, on="COD_MUN", how="left")
    else:
        cont = cont.with_columns(pl.lit(0).alias("n_hosp"))
    mun = municipios.with_columns(pl.col("COD_MUN").cast(pl.Utf8).str.slice(0, 6))
    if "POP" not in mun.columns:
        mun = mun.with_columns(pl.lit(None, dtype=pl.Float64).alias("POP"))
    if "UF" not in mun.columns:
        mun = mun.with_columns(_lookup(pl.col("COD_MUN").str.slice(0, 2), UF_SIGLA, "?").alias("UF"))
    if uf:
        u = str(uf).strip().upper()
        cod_uf = u if u.isdigit() else next((k for k, v in UF_SIGLA.items() if v == u), None)
        if cod_uf:
            mun = mun.filter(pl.col("COD_MUN").str.slice(0, 2) == cod_uf)
    cols = [c for c in ("COD_MUN", "NOME", "UF", "POP", "LAT", "LON") if c in mun.columns]
    out = (
        mun.select(cols).join(cont, on="COD_MUN", how="left")
        .with_columns(pl.col("n").fill_null(0).cast(pl.Int64), pl.col("n_hosp").fill_null(0).cast(pl.Int64),
                      pl.col("POP").cast(pl.Float64, strict=False))
        .with_columns(
            pl.when(pl.col("POP") > 0).then(pl.col("n") * fator / pl.col("POP") * por).otherwise(None).alias("taxa"),
            pl.when(pl.col("n") > 0).then(pl.col("n_hosp") / pl.col("n")).otherwise(None).alias("pct_hosp"),
        )
    )
    tot_n = float(out["n"].sum() or 0)
    tot_pop = float(out.filter(pl.col("POP") > 0)["POP"].sum() or 0)
    taxa_ref = tot_n * fator / tot_pop * por if tot_pop > 0 else None
    out = out.with_columns(
        (pl.col("taxa") / taxa_ref if taxa_ref else pl.lit(None, dtype=pl.Float64)).alias("razao_uf"),
        (pl.col("taxa").rank(method="average") / pl.col("taxa").is_not_null().sum() * 100).alias("percentil"),
        pl.lit(meses).alias("meses"), pl.lit(taxa_ref).alias("taxa_uf"),
    )
    return out.sort("taxa", descending=True, nulls_last=True)


def _quantil(v: list[float], q: float) -> float | None:
    if not v:
        return None
    s = sorted(v)
    pos = q * (len(s) - 1)
    lo = int(math.floor(pos)); hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def estatisticas_taxas(df: pl.DataFrame) -> dict:
    """Resumo das TIH municipais para os cartões da aba."""
    if df.is_empty():
        return {}
    v = [float(x) for x in df["taxa"].drop_nulls().to_list()]
    n_mun = df.height
    media = sum(v) / len(v) if v else None
    dp = math.sqrt(sum((x - media) ** 2 for x in v) / (len(v) - 1)) if v and len(v) > 1 else None
    p10, p90 = _quantil(v, 0.1), _quantil(v, 0.9)
    return {
        "n_mun": n_mun, "n_com_taxa": len(v), "n_zero": int(df.filter(pl.col("n") == 0).height),
        "taxa_uf": float(df["taxa_uf"][0]) if df["taxa_uf"][0] is not None else None,
        "media": media, "dp": dp, "cv": (dp / media) if (dp is not None and media) else None,
        "mediana": _quantil(v, 0.5), "p10": p10, "p25": _quantil(v, 0.25), "p75": _quantil(v, 0.75), "p90": p90,
        "min": min(v) if v else None, "max": max(v) if v else None,
        "razao_p90_p10": (p90 / p10) if (p10 and p90) else None,
        "internacoes": int(df["n"].sum() or 0), "populacao": float(df["POP"].fill_null(0).sum() or 0),
        "meses": int(df["meses"][0]) if "meses" in df.columns else None,
    }


def histograma(df: pl.DataFrame, bins: int | None = None, limite_sup: float | None = None) -> dict:
    """Bordas e contagens do histograma das taxas (Freedman–Diaconis, 8 a 40 classes).

    limite_sup: taxas acima disso vão para a última classe (evita que 2 outliers achatem o resto).
    Devolve {"bordas": [...], "contagens": [...], "largura": w, "n": n, "cod_por_classe": [[cod,...], ...]}.
    """
    sub = df.filter(pl.col("taxa").is_not_null())
    v = [float(x) for x in sub["taxa"].to_list()]
    cods = sub["COD_MUN"].to_list()
    if not v:
        return {"bordas": [], "contagens": [], "largura": 0.0, "n": 0, "cod_por_classe": []}
    vmax = max(v)
    if limite_sup is None:
        p99 = _quantil(v, 0.99) or vmax
        limite_sup = p99 if p99 < vmax else vmax
    vmin = 0.0
    if bins is None:
        iqr = (_quantil(v, 0.75) or 0) - (_quantil(v, 0.25) or 0)
        w = 2 * iqr / (len(v) ** (1 / 3)) if iqr > 0 else 0
        bins = int(math.ceil((limite_sup - vmin) / w)) if w > 0 else 12
        bins = max(8, min(40, bins))
    largura = (limite_sup - vmin) / bins if limite_sup > vmin else 1.0
    # arredonda a largura para um número "limpo"
    mag = 10 ** math.floor(math.log10(largura)) if largura > 0 else 1
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= largura:
            largura = m * mag
            break
    bins = int(math.ceil((limite_sup - vmin) / largura)) or 1
    bordas = [round(vmin + i * largura, 10) for i in range(bins + 1)]
    cont = [0] * bins
    por_classe: list[list[str]] = [[] for _ in range(bins)]
    for x, c in zip(v, cods):
        i = int((x - vmin) / largura)
        i = max(0, min(bins - 1, i))
        cont[i] += 1
        por_classe[i].append(str(c))
    return {"bordas": bordas, "contagens": cont, "largura": largura, "n": len(v), "cod_por_classe": por_classe,
            "acima_limite": int(sum(1 for x in v if x > bordas[-1]))}


# ---------------------------------------------------------------------------
# 8. Payloads para o mapa (JSON-serializáveis)
# ---------------------------------------------------------------------------

def _limpo(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def quantis_breaks(valores: Iterable[float], k: int = 5) -> list[float]:
    v = sorted(float(x) for x in valores if x is not None and not (isinstance(x, float) and math.isnan(x)) and x > 0)
    if len(v) < 2:
        return [x for x in v]
    return [_quantil(v, i / k) for i in range(1, k)]


def payload_faixas(perfil_cat: pl.DataFrame, dist_df: pl.DataFrame, hospital: dict, limites: Sequence[float],
                   aih_mun: pl.DataFrame | None = None, *, por: int = POR_FAIXAS, titulo: str = "") -> dict:
    """Dados que o mapa (mapa_faixas.html → gsp.setFaixas) precisa para uma categoria.

    aih_mun: COD_MUN | n (internações da categoria por município) — opcional, para o tooltip.
    """
    faixas = [
        {k: _limpo(v) for k, v in r.items() if k in ("faixa", "rotulo", "d_min", "d_max", "n_mun", "pop", "n", "taxa", "pct", "cum_pct")}
        for r in perfil_cat.sort("faixa").to_dicts()
    ]
    nmun: dict[str, int] = {}
    if aih_mun is not None and not aih_mun.is_empty():
        nmun = {str(r["COD_MUN"]): int(r["n"]) for r in aih_mun.to_dicts()}
    fator = 1.0
    if not perfil_cat.is_empty() and "meses" in perfil_cat.columns:
        fator = fator_anual(int(perfil_cat["meses"][0]))
    muns = {}
    for r in dist_df.to_dicts():
        cod = str(r["COD_MUN"])
        n = nmun.get(cod, 0)
        pop = float(r["POP"] or 0)
        muns[cod] = {"faixa": int(r["faixa"]), "dist": _limpo(round(float(r["dist_km"]), 1)), "pop": pop, "n": n,
                     "taxa": _limpo(n * fator / pop * por) if pop > 0 else None, "nome": r.get("NOME")}
    return {
        "hospital": {"cnes": hospital.get("cnes"), "nome": hospital.get("nome"), "lat": hospital.get("lat"), "lon": hospital.get("lon")},
        "limites": [float(x) for x in limites], "faixas": faixas, "municipios": muns,
        "unidade": f"internações por {por:,} hab./ano".replace(",", "."), "titulo": titulo,
    }


def payload_coropleto(taxas: pl.DataFrame, *, uf: str | None, titulo: str, por: int = POR_MUNICIPIOS,
                      destaque: str | None = None, hospital: dict | None = None, breaks: list[float] | None = None) -> dict:
    """Dados para gsp.setCoropleto (TIH por município)."""
    valores, detalhe = {}, {}
    for r in taxas.to_dicts():
        cod = str(r["COD_MUN"])
        valores[cod] = _limpo(r.get("taxa"))
        detalhe[cod] = {"nome": r.get("NOME"), "pop": _limpo(r.get("POP")), "n": int(r.get("n") or 0),
                        "n_hosp": int(r.get("n_hosp") or 0), "razao": _limpo(r.get("razao_uf")), "percentil": _limpo(r.get("percentil"))}
    if breaks is None:
        breaks = quantis_breaks(valores.values(), 5)
    return {
        "uf": uf, "titulo": titulo, "unidade": f"internações por {por:,} hab./ano".replace(",", "."),
        "valores": valores, "detalhe": detalhe, "breaks": [float(b) for b in breaks], "destaque": destaque,
        "hospital": ({"lat": hospital.get("lat"), "lon": hospital.get("lon"), "nome": hospital.get("nome")} if hospital else None),
    }


# ---------------------------------------------------------------------------
# 9. Conveniências para a interface
# ---------------------------------------------------------------------------

def completar_coordenadas(hospitais: pl.DataFrame, municipios: pl.DataFrame | None) -> pl.DataFrame:
    """Hospital sem NU_LATITUDE/NU_LONGITUDE no CNES recebe o centroide do seu município (coluna COORD
    = "CNES" ou "centroide do município"). Sem isso, hospitais não georreferenciados sumiam da lista."""
    h = hospitais
    if "COORD" not in h.columns:
        h = h.with_columns(pl.when(pl.col("LAT").is_not_null() & pl.col("LON").is_not_null()).then(pl.lit("CNES")).otherwise(None).alias("COORD"))
    if municipios is None or municipios.is_empty() or "COD_MUN" not in h.columns:
        return h
    m = municipios.select(pl.col("COD_MUN").cast(pl.Utf8).str.slice(0, 6), pl.col("LAT").alias("_mlat"), pl.col("LON").alias("_mlon")).unique(subset=["COD_MUN"])
    return (
        h.join(m, on="COD_MUN", how="left")
        .with_columns(
            pl.when(pl.col("LAT").is_null() & pl.col("_mlat").is_not_null()).then(pl.lit("centroide do município")).otherwise(pl.col("COORD")).alias("COORD"),
            pl.coalesce([pl.col("LAT"), pl.col("_mlat")]).alias("LAT"), pl.coalesce([pl.col("LON"), pl.col("_mlon")]).alias("LON"),
        ).drop(["_mlat", "_mlon"])
    )


def hospitais_do_dataset(aih: pl.DataFrame, hospitais: pl.DataFrame, *, somente_com_aih: bool = False,
                         municipios: pl.DataFrame | None = None) -> pl.DataFrame:
    """CNES | NOME | LAT | LON | COD_MUN | COORD | n — TODOS os hospitais da tabela `hospitais`, com o nº de AIH no
    dataset (0 quando não aparece). Sem coordenada no CNES, usa o centroide do município (se `municipios` vier);
    só fica de fora quem não tem nem isso. `somente_com_aih=True` descarta os de n = 0."""
    col_cnes = _col(aih, ("CNES", "CO_CNES", "_cnes"))
    h = hospitais.with_columns(_cnes7("CNES").alias("CNES"))
    if "COD_MUN" in h.columns:
        h = h.with_columns(_mun6("COD_MUN").alias("COD_MUN"))
    for c, t in (("LAT", pl.Float64), ("LON", pl.Float64)):
        if c not in h.columns:
            h = h.with_columns(pl.lit(None, dtype=t).alias(c))
    h = h.with_columns(
        pl.when((pl.col("LAT") == 0) | (pl.col("LON") == 0)).then(None).otherwise(pl.col("LAT")).alias("LAT"),
        pl.when((pl.col("LAT") == 0) | (pl.col("LON") == 0)).then(None).otherwise(pl.col("LON")).alias("LON"),
    )
    if col_cnes is not None and not aih.is_empty():
        n = aih.group_by(_cnes7(col_cnes).alias("CNES")).len().rename({"len": "n"})
        h = h.drop([c for c in ("n",) if c in h.columns]).join(n, on="CNES", how="left")
    elif "n" not in h.columns:
        h = h.with_columns(pl.lit(0).alias("n"))
    h = completar_coordenadas(h.with_columns(pl.col("n").fill_null(0).cast(pl.Int64)), municipios)
    if somente_com_aih:
        h = h.filter(pl.col("n") > 0)
    return (
        h.filter(pl.col("LAT").is_not_null() & pl.col("LON").is_not_null())
        .unique(subset=["CNES"], keep="first", maintain_order=True)
        .sort(["n", "NOME"], descending=[True, False])
    )


# Tipos de unidade do CNES (TP_UNID / TP_UNIDADE / CO_TIPO_UNIDADE — tabela tbTipoUnidade)
TIPOS_UNIDADE: dict[str, str] = {
    "01": "Posto de saúde", "02": "Centro de saúde/UBS", "04": "Policlínica", "05": "Hospital geral",
    "07": "Hospital especializado", "15": "Unidade mista", "20": "Pronto-socorro geral", "21": "Pronto-socorro especializado",
    "22": "Consultório isolado", "36": "Clínica/centro de especialidade", "39": "Unidade de apoio diagnose e terapia",
    "40": "Unidade móvel terrestre", "42": "Unidade móvel pré-hospitalar", "43": "Farmácia", "61": "Centro de parto normal",
    "62": "Hospital/dia isolado", "70": "CAPS", "73": "Pronto atendimento", "76": "Central de regulação de urgências",
}
TIPOS_UNIDADE_HOSPITALARES = {k: TIPOS_UNIDADE[k] for k in ("05", "07", "15", "20", "21", "61", "62")}
_PALAVRAS_HOSPITAL = ("HOSPITAL", "HOSP ", " HOSP", "MATERNIDADE", "PRONTO SOCORRO", "PRONTO-SOCORRO", "SANTA CASA",
                      "UNIDADE MISTA", "CASA DE SAUDE", "CASA DE SAÚDE")
# Flags do layout de disseminação ST*.dbf (IT_CNES 2017-06): 1 = sim
_FLAGS_INTERNACAO = ("ATENDHOS", "LEITHOSP", "NIVATE_H", "AP01CV01", "AP01CV02", "AP01CV03", "AP01CV04", "AP01CV05", "AP01CV06")
_LEITOS_ST = ("QTLEITP1", "QTLEITP2", "QTLEITP3")


def hospitais_da_uf(estabelecimentos: pl.DataFrame, uf: str, aih: pl.DataFrame | None = None, *,
                    leitos: pl.DataFrame | None = None, municipios: pl.DataFrame | None = None) -> pl.DataFrame:
    """TODOS os hospitais de uma UF, a partir da tabela de estabelecimentos do CNES.

    Aceita os dois layouts: a base CSV do app (tbEstabelecimento: CO_CNES, NO_FANTASIA, CO_MUNICIPIO_GESTOR,
    TP_UNIDADE/CO_TIPO_UNIDADE, NU_LATITUDE/NU_LONGITUDE) e o de disseminação ST*.dbf (IT_CNES 2017-06: CNES,
    CODUFMUN, TP_UNID, ATENDHOS, LEITHOSP, AP01CV01…06, QTLEITP1…3, NIVATE_H; sem nome nem coordenadas).

    É hospital quem satisfaz QUALQUER critério:
      * tipo de unidade hospitalar (05, 07, 15, 20, 21, 61, 62);
      * flag de internação = 1 (ATENDHOS, LEITHOSP, NIVATE_H ou AP01CV01…06) ou QTLEITP1+2+3 > 0;
      * leitos existentes > 0 em `leitos` (LT*.dbf ou rlEstabComplementar: CNES/CO_UNIDADE, QT_EXIST);
      * nome com termo hospitalar; ou
      * CNES presente no dataset de AIH (quem faturou internação é hospital para esta análise).
    Devolve CNES | NOME | LAT | LON | COD_MUN | TIPO | COORD | n (AIH no dataset; 0 se nenhuma). Sem coordenadas
    no CNES, usa o centroide do município (COORD = "centroide do município") quando `municipios` é dado.
    `uf` aceita sigla ("MG") ou código ("31").
    """
    u = str(uf).strip().upper()
    cod_uf = u if u.isdigit() else next((k for k, v in UF_SIGLA.items() if v == u), None)
    e = _renomear(estabelecimentos, {
        "CNES": ("CO_CNES", "CNES", "cnes"), "NOME": ("NO_FANTASIA", "NO_RAZAO_SOCIAL", "NOME", "nome"),
        "LAT": ("NU_LATITUDE", "LAT", "latitude"), "LON": ("NU_LONGITUDE", "LON", "longitude"),
        "COD_MUN": ("CO_MUNICIPIO_GESTOR", "CODUFMUN", "CO_MUNICIPIO", "COD_MUN"),
        "TIPO": ("TP_UNID", "TP_UNIDADE", "CO_TIPO_UNIDADE", "DS_TIPO_UNIDADE"),
    })
    if e.is_empty():
        return pl.DataFrame()
    for c, t in (("NOME", pl.Utf8), ("LAT", pl.Float64), ("LON", pl.Float64), ("COD_MUN", pl.Utf8), ("TIPO", pl.Utf8)):
        if c not in e.columns:
            e = e.with_columns(pl.lit(None, dtype=t).alias(c))
    # flags e leitos do layout ST (quando existirem)
    flags = [c for c in _FLAGS_INTERNACAO if c in estabelecimentos.columns]
    qtl = [c for c in _LEITOS_ST if c in estabelecimentos.columns]
    extra = estabelecimentos.select(
        [_cnes7(_col(estabelecimentos, ("CO_CNES", "CNES", "cnes"))).alias("CNES")]
        + [pl.col(c).cast(pl.Utf8, strict=False).str.strip_chars().alias(c) for c in flags]
        + [pl.col(c).cast(pl.Float64, strict=False).fill_null(0.0).alias(c) for c in qtl]
    ) if (flags or qtl) else None
    e = e.with_columns(
        _cnes7("CNES").alias("CNES"), _mun6("COD_MUN").alias("COD_MUN"),
        pl.col("NOME").fill_null("").str.to_uppercase().alias("_nome_u"),
        pl.col("TIPO").cast(pl.Utf8, strict=False).str.strip_chars().str.zfill(2).alias("_tipo"),
    )
    if extra is not None:
        e = e.join(extra.unique(subset=["CNES"], keep="first"), on="CNES", how="left")
    if cod_uf:
        e = e.filter(pl.col("COD_MUN").str.slice(0, 2) == cod_uf)
    # AIH: contagem e nomes de reserva (o layout ST não traz nome)
    com_aih: set[str] = set(); n = None; nomes_aih = None
    if aih is not None and not aih.is_empty():
        col_cnes = _col(aih, ("CNES", "CO_CNES", "_cnes"))
        if col_cnes:
            n = aih.group_by(_cnes7(col_cnes).alias("CNES")).len().rename({"len": "n"})
            com_aih = set(n["CNES"].to_list())
            cn = _col(aih, ("NO_ESTABELECIMENTO", "Hospital (CNES)", "NO_FANTASIA", "NOME_HOSPITAL"))
            if cn:
                nomes_aih = aih.group_by(_cnes7(col_cnes).alias("CNES")).agg(pl.col(cn).cast(pl.Utf8).drop_nulls().first().alias("_nome_aih"))
    # leitos por estabelecimento (LT*.dbf / rlEstabComplementar)
    com_leito: set[str] = set()
    if leitos is not None and not leitos.is_empty():
        cl = _col(leitos, ("CO_CNES", "CNES", "CO_UNIDADE")); cq = _col(leitos, ("QT_EXIST", "QT_LEITOS", "QT_SUS"))
        if cl and cq:
            key = pl.col(cl).cast(pl.Utf8, strict=False).str.strip_chars()
            key = pl.when(key.str.len_chars() > 7).then(key.str.slice(-7)).otherwise(key.str.zfill(7))   # CO_UNIDADE = UF+MUN+CNES
            lt = leitos.group_by(key.alias("CNES")).agg(pl.col(cq).cast(pl.Float64, strict=False).sum().alias("q"))
            com_leito = set(lt.filter(pl.col("q") > 0)["CNES"].to_list())
    crit = pl.col("_tipo").is_in(list(TIPOS_UNIDADE_HOSPITALARES))
    crit = crit | pl.any_horizontal([pl.col("_nome_u").str.contains(p, literal=True) for p in _PALAVRAS_HOSPITAL])
    for f in flags:
        crit = crit | (pl.col(f) == "1")
    if qtl:
        crit = crit | (pl.sum_horizontal([pl.col(c) for c in qtl]) > 0)
    if com_leito:
        crit = crit | pl.col("CNES").is_in(list(com_leito))
    if com_aih:
        crit = crit | pl.col("CNES").is_in(list(com_aih))
    e = e.filter(crit)
    if nomes_aih is not None:
        e = e.join(nomes_aih, on="CNES", how="left").with_columns(
            pl.when(pl.col("NOME").fill_null("") == "").then(pl.col("_nome_aih")).otherwise(pl.col("NOME")).alias("NOME")).drop("_nome_aih")
    e = e.with_columns(
        pl.when((pl.col("LAT") == 0) | (pl.col("LON") == 0)).then(None).otherwise(pl.col("LAT")).alias("LAT"),
        pl.when((pl.col("LAT") == 0) | (pl.col("LON") == 0)).then(None).otherwise(pl.col("LON")).alias("LON"),
        pl.when(pl.col("NOME").fill_null("") == "").then(pl.concat_str([pl.lit("CNES "), pl.col("CNES")])).otherwise(pl.col("NOME")).alias("NOME"),
        _lookup(pl.col("_tipo"), TIPOS_UNIDADE, "").alias("TIPO"),
    )
    e = e.join(n, on="CNES", how="left") if n is not None else e.with_columns(pl.lit(0).alias("n"))
    e = completar_coordenadas(e.with_columns(pl.col("n").fill_null(0).cast(pl.Int64)), municipios)
    return (
        e.unique(subset=["CNES"], keep="first", maintain_order=True)
        .select(["CNES", "NOME", "LAT", "LON", "COD_MUN", "TIPO", "COORD", "n"])
        .sort(["n", "NOME"], descending=[True, False])
    )


def hospital_dict(linha: dict) -> dict:
    return {"cnes": str(linha["CNES"]).zfill(7), "nome": linha.get("NOME") or str(linha["CNES"]),
            "lat": float(linha["LAT"]), "lon": float(linha["LON"]), "cod_mun": str(linha.get("COD_MUN") or "")[:6]}


def internacoes_por_municipio(aih: pl.DataFrame, cnes: str, nivel: str, categoria: str | None) -> pl.DataFrame:
    """COD_MUN | n das internações de um hospital numa categoria (para o tooltip do mapa)."""
    col_cnes = _col(aih, ("CNES", "CO_CNES", "_cnes"))
    col_mun = _col(aih, ("MUNIC_RES", "PA_MUNPCN", "SP_M_PAC", "municipio_residencia"))
    h = aih.filter(_cnes7(col_cnes) == str(cnes).zfill(7))
    if nivel != "todos" and categoria and categoria != TODOS:
        h = classificar(h, nivel).filter(pl.col("categoria") == categoria)
    return h.group_by(_mun6(col_mun).alias("COD_MUN")).len().rename({"len": "n"})


# ---------------------------------------------------------------------------
# 10. Adaptação dos dados do app (CNES loader / métricas) para estas funções
# ---------------------------------------------------------------------------

def _renomear(df: pl.DataFrame, mapa: dict[str, Sequence[str]]) -> pl.DataFrame:
    """Para cada nome-alvo, usa a 1ª coluna candidata existente (cast para Utf8/Float64 conforme o alvo)."""
    exprs = []
    for alvo, cands in mapa.items():
        c = _col(df, cands)
        if c is None:
            continue
        e = pl.col(c)
        if alvo in ("LAT", "LON", "POP"):
            e = e.cast(pl.Float64, strict=False)
        else:
            e = e.cast(pl.Utf8, strict=False).str.strip_chars()
        exprs.append(e.alias(alvo))
    return df.select(exprs) if exprs else pl.DataFrame()


def insumos_do_app(hospitais_geo: pl.DataFrame, municipios_cnes: pl.DataFrame,
                   populacao: pl.DataFrame | None = None, completar_malha: bool = True) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Converte as tabelas do app nas tabelas que as telas esperam.

    hospitais_geo   : DashboardMetrics.hospitais_geo (CO_CNES, NO_FANTASIA, NU_LATITUDE, NU_LONGITUDE, CO_MUNICIPIO_GESTOR)
    municipios_cnes : get_cnes_data().load().municipios (CO_MUNICIPIO, NO_MUNICIPIO, NU_LATITUDE, NU_LONGITUDE, NU_POPULACAO)
    populacao       : papahr.dashboard.populacao.carregar_populacao() (opcional; substitui NU_POPULACAO)
    Devolve (hospitais[CNES, NOME, LAT, LON, COD_MUN], municipios[COD_MUN, NOME, LAT, LON, POP, UF]).
    Municípios sem coordenadas no CNES recebem o centroide da malha IBGE embarcada.
    """
    hosp = _renomear(hospitais_geo, {
        "CNES": ("CO_CNES", "CNES", "cnes"), "NOME": ("NO_FANTASIA", "NO_RAZAO_SOCIAL", "NOME", "nome"),
        "LAT": ("NU_LATITUDE", "LAT", "latitude"), "LON": ("NU_LONGITUDE", "LON", "longitude"),
        "COD_MUN": ("CO_MUNICIPIO_GESTOR", "CO_MUNICIPIO", "COD_MUN", "MUNIC_MOV"),
    })
    if not hosp.is_empty():
        if "NOME" not in hosp.columns:
            hosp = hosp.with_columns(pl.col("CNES").alias("NOME"))
        if "COD_MUN" not in hosp.columns:
            hosp = hosp.with_columns(pl.lit(None, dtype=pl.Utf8).alias("COD_MUN"))
        hosp = hosp.with_columns(
            _cnes7("CNES").alias("CNES"),
            pl.when(pl.col("COD_MUN").is_not_null()).then(_mun6("COD_MUN")).otherwise(None).alias("COD_MUN"),
            pl.when((pl.col("LAT") == 0) | (pl.col("LON") == 0)).then(None).otherwise(pl.col("LAT")).alias("LAT"),
            pl.when((pl.col("LAT") == 0) | (pl.col("LON") == 0)).then(None).otherwise(pl.col("LON")).alias("LON"),
        ).unique(subset=["CNES"], keep="first", maintain_order=True)
    mun = _renomear(municipios_cnes, {
        "COD_MUN": ("CO_MUNICIPIO", "COD_MUN", "codigo_ibge", "id_municipio"), "NOME": ("NO_MUNICIPIO", "NOME", "nome_municipio"),
        "LAT": ("NU_LATITUDE", "LAT", "latitude"), "LON": ("NU_LONGITUDE", "LON", "longitude"),
        "POP": ("NU_POPULACAO", "POP", "POPULACAO", "populacao"),
    })
    if mun.is_empty():
        mun = pl.DataFrame(schema={"COD_MUN": pl.Utf8, "NOME": pl.Utf8, "LAT": pl.Float64, "LON": pl.Float64, "POP": pl.Float64})
    for c, t in (("NOME", pl.Utf8), ("LAT", pl.Float64), ("LON", pl.Float64), ("POP", pl.Float64)):
        if c not in mun.columns:
            mun = mun.with_columns(pl.lit(None, dtype=t).alias(c))
    mun = mun.with_columns(_mun6("COD_MUN").alias("COD_MUN")).filter(pl.col("COD_MUN").str.len_chars() == 6).unique(subset=["COD_MUN"], keep="first", maintain_order=True)
    if completar_malha:
        try:
            from papahr.ui.mapa.malha import centroides_municipios
            base = centroides_municipios().rename({"LAT": "_lat", "LON": "_lon", "NOME": "_nome"})
            mun = base.join(mun, on="COD_MUN", how="left").with_columns(
                pl.coalesce([pl.col("LAT"), pl.col("_lat")]).alias("LAT"),
                pl.coalesce([pl.col("LON"), pl.col("_lon")]).alias("LON"),
                pl.coalesce([pl.col("NOME"), pl.col("_nome")]).alias("NOME"),
            ).drop(["_lat", "_lon", "_nome"])
        except Exception:  # noqa: BLE001  (malha ausente: segue só com o CNES)
            pass
    if populacao is not None and not populacao.is_empty():
        from papahr.dashboard.populacao import juntar_populacao
        mun = juntar_populacao(mun, populacao)
    mun = mun.with_columns(_lookup(pl.col("COD_MUN").str.slice(0, 2), UF_SIGLA, "?").alias("UF"))
    return hosp, mun.select(["COD_MUN", "NOME", "LAT", "LON", "POP", "UF"])


# ---------------------------------------------------------------------------
# 11. Market share por faixa de distância
# ---------------------------------------------------------------------------

def _aih_categoria(aih: pl.DataFrame, nivel: str, categoria: str | None) -> pl.DataFrame:
    if nivel != "todos" and categoria and categoria != TODOS:
        return classificar(aih, nivel).filter(pl.col("categoria") == categoria)
    return aih


def market_share(aih: pl.DataFrame, hospital: dict, dist_df: pl.DataFrame, faixa: int, *, nivel: str = "todos",
                 categoria: str | None = None, hospitais: pl.DataFrame | None = None, acumulado: bool = False) -> pl.DataFrame:
    """Participação de cada hospital nas internações (do tipo) dos residentes da faixa `faixa` em torno de `hospital`.

    acumulado=True: faixas 0..faixa (do hospital até o limite externo da faixa).
    Devolve CNES | NOME | COD_MUN | dist_km (do hospital em foco) | n | pct | cum_pct | foco, por n decrescente.
    """
    col_cnes = _col(aih, ("CNES", "CO_CNES", "_cnes"))
    col_mun = _col(aih, ("MUNIC_RES", "PA_MUNPCN", "SP_M_PAC", "municipio_residencia"))
    if col_cnes is None or col_mun is None:
        raise ValueError("o dataset precisa de CNES e MUNIC_RES")
    muns = dist_df.filter(pl.col("faixa") <= faixa if acumulado else pl.col("faixa") == faixa)["COD_MUN"].to_list()
    sub = _aih_categoria(aih, nivel, categoria).with_columns(_mun6(col_mun).alias("_m6")).filter(pl.col("_m6").is_in(muns))
    if "FONTE" in sub.columns:
        cont = sub.group_by(_cnes7(col_cnes).alias("CNES")).agg(
            pl.len().alias("n"), (pl.col("FONTE") == "SIH").sum().alias("n_sih"))
        cont = cont.with_columns((pl.col("n_sih") / pl.col("n")).alias("pct_sih"))
    else:
        cont = sub.group_by(_cnes7(col_cnes).alias("CNES")).len().rename({"len": "n"}).with_columns(
            pl.col("n").alias("n_sih"), pl.lit(1.0).alias("pct_sih"))
    total = int(cont["n"].sum() or 0)
    if hospitais is not None and not hospitais.is_empty():
        h = hospitais.with_columns(_cnes7("CNES").alias("CNES"))
        cols = ["CNES"] + [c for c in ("NOME", "COD_MUN", "LAT", "LON") if c in h.columns]
        cont = cont.join(h.select(cols).unique(subset=["CNES"], keep="first"), on="CNES", how="left")
    for c, t in (("NOME", pl.Utf8), ("COD_MUN", pl.Utf8), ("LAT", pl.Float64), ("LON", pl.Float64)):
        if c not in cont.columns:
            cont = cont.with_columns(pl.lit(None, dtype=t).alias(c))
    cont = cont.with_columns(
        pl.coalesce([pl.col("NOME"), pl.concat_str([pl.lit("CNES "), pl.col("CNES")])]).alias("NOME"),
        pl.lit(float(hospital["lat"])).alias("_hlat"), pl.lit(float(hospital["lon"])).alias("_hlon"),
    ).with_columns(
        pl.when(pl.col("LAT").is_not_null()).then(_haversine_expr("LAT", "LON", "_hlat", "_hlon")).otherwise(None).alias("dist_km"),
        (pl.col("CNES") == str(hospital["cnes"]).zfill(7)).alias("foco"),
        (pl.col("n") / total if total else pl.lit(None, dtype=pl.Float64)).alias("pct"),
    ).sort(["n", "NOME"], descending=[True, False]).with_columns(pl.col("pct").cum_sum().alias("cum_pct"))
    return cont.select(["CNES", "NOME", "COD_MUN", "dist_km", "n", "pct", "cum_pct", "foco", "n_sih", "pct_sih"])


def share_por_faixa(aih: pl.DataFrame, hospital: dict, dist_df: pl.DataFrame, limites: Sequence[float], *,
                    nivel: str = "todos", categoria: str | None = None) -> pl.DataFrame:
    """Por faixa: internações totais dos residentes, as do hospital em foco, participação, nº de hospitais
    concorrentes, participação do líder e HHI (índice de Herfindahl, 0–1)."""
    col_cnes = _col(aih, ("CNES", "CO_CNES", "_cnes"))
    col_mun = _col(aih, ("MUNIC_RES", "PA_MUNPCN", "SP_M_PAC", "municipio_residencia"))
    sub = _aih_categoria(aih, nivel, categoria).with_columns(_mun6(col_mun).alias("COD_MUN"), _cnes7(col_cnes).alias("_cnes"))
    sub = sub.join(dist_df.select(["COD_MUN", "faixa"]), on="COD_MUN", how="inner")
    foco = str(hospital["cnes"]).zfill(7)
    por = sub.group_by(["faixa", "_cnes"]).len().rename({"len": "n"})
    tot = por.group_by("faixa").agg(
        pl.col("n").sum().alias("n_total"), pl.col("n").filter(pl.col("_cnes") == foco).sum().alias("n_foco"),
        pl.len().alias("n_hospitais"), pl.col("n").max().alias("n_lider"),
        ((pl.col("n") / pl.col("n").sum()) ** 2).sum().alias("hhi"),
        pl.col("_cnes").sort_by("n", descending=True).first().alias("cnes_lider"),
    )
    rot = pl.DataFrame({"faixa": list(range(len(limites) + 1)), "rotulo": rotulos_faixas(limites)}).with_columns(pl.col("faixa").cast(pl.Int32))
    out = rot.join(tot.with_columns(pl.col("faixa").cast(pl.Int32)), on="faixa", how="left").with_columns(
        pl.col("n_total").fill_null(0), pl.col("n_foco").fill_null(0), pl.col("n_hospitais").fill_null(0),
    ).with_columns(
        pl.when(pl.col("n_total") > 0).then(pl.col("n_foco") / pl.col("n_total")).otherwise(None).alias("share_foco"),
        pl.when(pl.col("n_total") > 0).then(pl.col("n_lider") / pl.col("n_total")).otherwise(None).alias("share_lider"),
    )
    return out.select(["faixa", "rotulo", "n_total", "n_foco", "share_foco", "n_hospitais", "cnes_lider", "share_lider", "hhi"]).sort("faixa")
