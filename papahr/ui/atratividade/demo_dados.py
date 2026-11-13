"""
demo_dados.py — dados sintéticos para abrir as telas sem o pipeline (demonstração e testes de layout).

    from papahr.ui.atratividade.demo_dados import gerar
    aih, hospitais, municipios = gerar()

4 hospitais nomeados + 40 hospitais municipais em MG, 12 competências, procedimentos de vários grupos/subgrupos SIGTAP,
CID, especialidade, caráter, idade e sexo. A população é sintética (BH, Contagem e
Montes Claros com valores reais aproximados; os demais log-normais), só para a demo —
o app usa IBGE/CNES.
"""
from __future__ import annotations

import math
import random

import polars as pl

from papahr.ui.mapa.malha import centroides_municipios

# (PROC_REA, NO_PROCEDIMENTO, NO_GRUPO, NO_SUB_GRUPO, NO_FORMA_ORGANIZACAO, CID, ESPEC, COMPLEXIDADE, peso)
_PROCS = [
    ("0408050012", "Artroplastia total de quadril", "Procedimentos cirúrgicos", "Cirurgia do sistema osteomuscular", "Cirurgia de quadril", "M161", "01", "Alta complexidade", 3),
    ("0408040130", "Tratamento cirúrgico de fratura de fêmur", "Procedimentos cirúrgicos", "Cirurgia do sistema osteomuscular", "Cirurgia de membro inferior", "S720", "01", "Média complexidade", 6),
    ("0406010013", "Revascularização miocárdica", "Procedimentos cirúrgicos", "Cirurgia do aparelho circulatório", "Cirurgia cardíaca", "I251", "01", "Alta complexidade", 2),
    ("0406040036", "Angioplastia coronariana", "Procedimentos cirúrgicos", "Cirurgia do aparelho circulatório", "Cirurgia intervencionista", "I219", "01", "Alta complexidade", 3),
    ("0407040102", "Colecistectomia", "Procedimentos cirúrgicos", "Cirurgia do aparelho digestivo", "Cirurgia das vias biliares", "K801", "01", "Média complexidade", 7),
    ("0303010037", "Tratamento de pneumonia", "Procedimentos clínicos", "Tratamentos clínicos (outras especialidades)", "Tratamento de doenças do aparelho respiratório", "J189", "03", "Média complexidade", 12),
    ("0303040149", "Tratamento de insuficiência cardíaca", "Procedimentos clínicos", "Tratamentos clínicos (outras especialidades)", "Tratamento de doenças do aparelho circulatório", "I500", "03", "Média complexidade", 8),
    ("0310010039", "Parto normal", "Procedimentos clínicos", "Parto e nascimento", "Parto", "O800", "02", "Média complexidade", 10),
    ("0411010034", "Parto cesariano", "Procedimentos cirúrgicos", "Cirurgia obstétrica", "Parto cirúrgico", "O829", "02", "Média complexidade", 8),
    ("0303140151", "Tratamento de acidente vascular cerebral", "Procedimentos clínicos", "Tratamentos clínicos (outras especialidades)", "Tratamento de doenças do sistema nervoso", "I64", "03", "Alta complexidade", 4),
]

# 4 hospitais nomeados + ~40 "hospitais municipais" nos municípios mais populosos (ver gerar())
_HOSP = [
    ("0026921", "Hospital A (Belo Horizonte) — referência", "310620", 2.4, True),
    ("0027014", "Hospital B (Belo Horizonte)", "310620", 1.2, True),
    ("2126303", "Hospital C (Contagem)", "311860", 0.9, True),
    ("2144228", "Hospital D (Montes Claros)", "314330", 1.1, True),
]
N_HOSP_EXTRA = 40
_POP_REAL = {"310620": 2_416_000, "311860": 621_000, "314330": 414_000, "313670": 299_000, "310670": 265_000,
             "311000": 238_000, "315460": 235_000, "313130": 227_000, "317020": 221_000, "312230": 205_000}


def _dist(a, b):
    r = 6371.0088; p1, p2 = math.radians(a[0]), math.radians(b[0])
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(x))


def gerar(seed: int = 7, escala: float = 1.0, n_extra: int = N_HOSP_EXTRA) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    rng = random.Random(seed)
    mun = centroides_municipios(ufs=["31"])
    pops = [float(_POP_REAL.get(c, int(math.exp(rng.gauss(9.3, 0.9))))) for c in mun["COD_MUN"].to_list()]
    mun = mun.with_columns(pl.Series("POP", pops), pl.lit("MG").alias("UF"))
    cent = {r["COD_MUN"]: (r["LAT"], r["LON"]) for r in mun.to_dicts()}
    # hospitais extras: um por município entre os mais populosos (fora dos 3 que já têm hospital nomeado)
    lista = list(_HOSP)
    ja = {h[2] for h in _HOSP}
    for i, r in enumerate(mun.sort("POP", descending=True).filter(~pl.col("COD_MUN").is_in(list(ja))).head(n_extra).to_dicts()):
        lista.append((f"{2900001 + i:07d}", f"Hospital Municipal de {r['NOME']}", r["COD_MUN"], round(rng.uniform(0.25, 1.0), 2), i < 4))
    hosp = pl.DataFrame({
        "CNES": [h[0] for h in lista], "NOME": [h[1] for h in lista], "COD_MUN": [h[2] for h in lista],
        "LAT": [cent[h[2]][0] for h in lista], "LON": [cent[h[2]][1] for h in lista],
    })
    H = hosp.to_dicts(); massa = {h[0]: h[3] for h in lista}; alta = {h[0]: h[4] for h in lista}
    # alcance por procedimento: alta complexidade decai menos (β menor) e atrai de mais longe
    beta = {p[0]: (1.2 if p[7] == "Alta complexidade" else 1.9) for p in _PROCS}
    rows = []
    for m in mun.to_dicts():
        pm = (m["LAT"], m["LON"]); pop = m["POP"]
        d = [max(2.0, _dist(pm, (h["LAT"], h["LON"]))) for h in H]
        for p in _PROCS:
            # demanda anual do município ∝ população × peso (≈ 30 internações/10 mil hab. por procedimento-peso);
            # probabilidade de ir a cada hospital ∝ massa·d^-β, competindo com um "hospital local"
            # genérico a 8 km (o resto da rede), cuja massa é maior para média complexidade
            esperado = pop / 10_000 * p[8] * 1.5 * escala
            # hospitais sem alta complexidade quase não fazem esses procedimentos (massa × 0,05)
            w = [massa[h["CNES"]] * (1.0 if (alta[h["CNES"]] or p[7] != "Alta complexidade") else 0.05) * dd ** -beta[p[0]]
                 for h, dd in zip(H, d)]
            local = (0.35 if p[7] == "Alta complexidade" else 1.0) * 8.0 ** -beta[p[0]]
            tot = sum(w) + local
            for h, ww in zip(H, w):
                lam = esperado * ww / tot
                n = _poisson(rng, lam)
                for _ in range(n):
                    idade = max(0, int(rng.gauss(58, 20))) if p[5][0] != "O" else rng.randint(16, 42)
                    rows.append({
                        "CNES": h["CNES"], "MUNIC_RES": m["COD_MUN"], "PROC_REA": p[0], "NO_PROCEDIMENTO": p[1],
                        "NO_GRUPO": p[2], "NO_SUB_GRUPO": p[3], "NO_FORMA_ORGANIZACAO": p[4], "DIAG_PRINC": p[5],
                        "ESPEC": p[6], "COMPLEXIDADE": p[7], "CAR_INT": "01" if rng.random() < 0.4 else "02",
                        "IDADE": idade, "COD_IDADE": "4", "SEXO": "3" if p[5][0] == "O" or rng.random() < 0.5 else "1",
                        "ANO_CMPT": 2024, "MES_CMPT": rng.randint(1, 12), "VAL_TOT": round(rng.uniform(600, 25000), 2),
                    })
    aih = pl.DataFrame(rows).with_columns(
        pl.concat_str([pl.col("ANO_CMPT").cast(pl.Utf8), pl.col("MES_CMPT").cast(pl.Utf8).str.zfill(2)]).alias("ANOMES_CMPT"))
    return aih, hosp, mun


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 50:
        return max(0, int(rng.gauss(lam, math.sqrt(lam)) + 0.5))
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        k += 1; p *= rng.random()
        if p <= L:
            return k - 1
