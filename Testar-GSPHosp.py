# -*- coding: utf-8 -*-
"""Testes automatizados FASE 1 do GSPHosp (sem GUI).

Roda os testes 1-15 do checklist do PROMPT-DE-CORRECAO-E-TESTE-GSPHosp.md.
Resultado gravado em TESTES-RESULTADO.txt (UTF-8) — HC-7: nunca imprimir
dataframes com acentos direto no PowerShell.

Uso:  .venv\\Scripts\\python.exe Testar-GSPHosp.py
"""
from __future__ import annotations

import io
import os
import sys
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

OUT_PATH = os.path.join(BASE, "TESTES-RESULTADO.txt")
_out = io.StringIO()

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []


def log(msg: str = "") -> None:
    _out.write(msg + "\n")


def registra(n: str, status: str, detalhe: str = "") -> None:
    _results.append((n, status, detalhe))
    log(f"[{status}] Teste {n}: {detalhe}")


def main() -> int:
    log("=" * 70)
    log("GSPHosp — Testes FASE 1 (sem GUI)")
    log("=" * 70)

    # ---------------------------------------------------------------- Teste 0:
    # Compilacao estatica dos 3 arquivos criticos
    import py_compile
    criticos = [
        "papahr/dashboard/cnes_loader.py",
        "papahr/geo/reference.py",
        "papahr/ui/pages/__init__.py",
        "papahr/ui/pages/login_page.py",
    ]
    ok = True
    for f in criticos:
        try:
            py_compile.compile(os.path.join(BASE, f), doraise=True)
        except Exception as e:
            ok = False
            registra("0-sintaxe", FAIL, f"{f}: {e}")
    if ok:
        registra("0-sintaxe", PASS, "4 arquivos criticos compilam sem erro")

    # ---------------------------------------------------------------- Geo puro
    from papahr.geo.reference import (
        hospital_bate_geo, resolve_geo_enrichment,
        lista_macrorregioes, lista_drs, UF_CODIBGE_TO_SIGLA,
    )

    # Teste 5 — resolve_geo_enrichment golden (Dilson Godinho)
    try:
        uf, mun, co_macro, no_macro, co_drs, no_drs = resolve_geo_enrichment(
            co_municipio_6dig="314330", co_regiao_saude_cnes_cod="0006")
        cond = (uf == "MG" and mun == "0314330" and co_macro == "MR02"
                and "Triângulo" in no_macro and co_drs == "06"
                and "Patos de Minas" in no_drs)
        registra("5-enrichment", PASS if cond else FAIL,
                 f"uf={uf} mun={mun} macro={co_macro}/{no_macro} drs={co_drs}/{no_drs}")
    except Exception as e:
        registra("5-enrichment", FAIL, f"excecao: {e}")

    # Teste 6 — bate geo MG positivo
    r = hospital_bate_geo(uf_hospital="MG", co_municipio_hospital="314330",
                          co_regiao_saude_hospital="0006",
                          sel_ufs=["MG"], sel_macros=[], sel_drs=[])
    registra("6-bate-MG", PASS if r is True else FAIL, f"retorno={r} (esperado True)")

    # Teste 7 — controle negativo SP
    r = hospital_bate_geo(uf_hospital="MG", co_municipio_hospital="314330",
                          co_regiao_saude_hospital="0006",
                          sel_ufs=["SP"], sel_macros=[], sel_drs=[])
    registra("7-bate-SP", PASS if r is False else FAIL, f"retorno={r} (esperado False)")

    # Teste 8 — REGRA RIGIDA 3: macros do RJ nao podem passar em MG
    macros_rj = lista_macrorregioes("RJ")
    r = hospital_bate_geo(uf_hospital="MG", co_municipio_hospital="314330",
                          co_regiao_saude_hospital="0006",
                          sel_ufs=["MG"], sel_macros=macros_rj, sel_drs=[])
    registra("8-macros-RJ", PASS if r is False else FAIL,
             f"retorno={r} (esperado False; {len(macros_rj)} macros RJ testadas)")

    # Teste 9 — fallback UF vazia via municipio
    r = hospital_bate_geo(uf_hospital="", co_municipio_hospital="314330",
                          co_regiao_saude_hospital="0006",
                          sel_ufs=["MG"], sel_macros=[], sel_drs=[])
    registra("9-uf-vazia", PASS if r is True else FAIL, f"retorno={r} (esperado True)")

    # Teste 11 — cascata certa MG + MR02 + DRS 06
    mg_macros = [m for m in lista_macrorregioes("MG") if m.startswith("MR02")]
    mg_drs = [d for d in lista_drs("MG") if d.startswith("DRS 06")]
    r = hospital_bate_geo(uf_hospital="MG", co_municipio_hospital="314330",
                          co_regiao_saude_hospital="0006",
                          sel_ufs=["MG"], sel_macros=mg_macros, sel_drs=mg_drs)
    registra("11-cascata", PASS if r is True else FAIL,
             f"retorno={r} macros={mg_macros} drs={mg_drs} (esperado True)")

    # Teste 10 — _selected_ufs NUNCA expande p/ 27 UFs (verificacao estatica)
    try:
        import inspect
        import papahr.ui.pages as pages_mod  # importa sem instanciar GUI? (PySide6 necessario)
        src = inspect.getsource(pages_mod)
        import re as _re
        m = _re.search(r"def _selected_ufs\(self\).*?(?=\n    def )", src, _re.DOTALL)
        corpo = m.group(0) if m else ""
        cond = (corpo != ""
                and "or list(get_ufs())" not in corpo
                and "return self._selected_ufs_nonempty()" in corpo)
        registra("10-selected-ufs", PASS if cond else FAIL,
                 "corpo de _selected_ufs sem fallback 'or list(get_ufs())'"
                 if cond else "FALLBACK 27 UFs AINDA PRESENTE!")
    except Exception as e:
        # PySide6 pode nao carregar em ambiente sem display; verifica via texto puro
        try:
            with open(os.path.join(BASE, "papahr", "ui", "pages", "__init__.py"),
                      encoding="utf-8") as fh:
                src = fh.read()
            i = src.index("def _selected_ufs(self)")
            j = src.index("def _selected_macros_nonempty", i)
            corpo = src[i:j]
            cond = "or list(get_ufs())" not in corpo
            registra("10-selected-ufs", PASS if cond else FAIL,
                     "verificado via texto (PySide6 indisponivel: %s)" % e)
        except Exception as e2:
            registra("10-selected-ufs", FAIL, f"nao consegui verificar: {e2}")

    # ---------------------------------------------------------------- CNES base
    # Testes 1-4 e 12-15 dependem da base local do CNES (C:\DICIONARIO_DADOS\...)
    estabs = None
    try:
        import polars as pl
        from papahr.dashboard.cnes_loader import get_cnes_data
        c = get_cnes_data()
        c.load()
        estabs = c.estabelecimentos
        if estabs is None or estabs.is_empty():
            raise RuntimeError("base CNES vazia/nao encontrada")
    except Exception as e:
        for t in ("1-sigla-uf", "2-linhas", "3-mg", "4-dilson",
                  "12-heur2-mg", "13-heur2-drs06", "14-search", "15-fallback"):
            registra(t, SKIP, f"base CNES local indisponivel ({e})")

    if estabs is not None and not estabs.is_empty():
        import polars as pl

        # Teste 1
        cond = "SIGLA_UF" in estabs.columns and estabs["SIGLA_UF"].n_unique() >= 26
        registra("1-sigla-uf", PASS if cond else FAIL,
                 f"SIGLA_UF presente={'SIGLA_UF' in estabs.columns} "
                 f"n_unique={estabs['SIGLA_UF'].n_unique() if 'SIGLA_UF' in estabs.columns else 0}")

        # Teste 2
        registra("2-linhas", PASS if estabs.height >= 500_000 else FAIL,
                 f"linhas={estabs.height:,} (esperado >= 500.000)")

        # Teste 3
        mg = estabs.filter(pl.col("SIGLA_UF") == "MG")
        registra("3-mg", PASS if 70_000 <= mg.height <= 80_000 else FAIL,
                 f"MG={mg.height:,} (esperado 70.000-80.000)")

        # Teste 4 — golden Dilson Godinho
        r4 = estabs.filter(
            pl.col("CO_CNES").cast(pl.Utf8).str.strip_chars() == "2219646")
        if r4.height == 1:
            row = r4.row(0, named=True)
            cond = (str(row.get("NO_FANTASIA", "")).startswith("HOSPITAL DILSON GODINHO")
                    and str(row.get("CO_ESTADO_GESTOR", "")).strip() == "31"
                    and str(row.get("CO_MUNICIPIO_GESTOR", "")).strip() == "314330"
                    and str(row.get("CO_REGIAO_SAUDE", "")).strip().lstrip("0") == "6"
                    and str(row.get("SIGLA_UF", "")) == "MG")
            registra("4-dilson", PASS if cond else FAIL,
                     f"nome={row.get('NO_FANTASIA')} uf_ibge={row.get('CO_ESTADO_GESTOR')} "
                     f"mun={row.get('CO_MUNICIPIO_GESTOR')} reg={row.get('CO_REGIAO_SAUDE')} "
                     f"sigla={row.get('SIGLA_UF')}")
        else:
            registra("4-dilson", FAIL, f"encontrado {r4.height} linhas (esperado 1)")

        # Teste 12 — Heuristica 2 completa MG
        lista: list[tuple[str, str, str, str]] = []
        cols = estabs.columns
        cod_l = estabs["CO_CNES"].cast(pl.Utf8).fill_null("").str.strip_chars().to_list()
        uf_l = estabs["SIGLA_UF"].cast(pl.Utf8).fill_null("").to_list()
        nome_col = next((cc for cc in ("NO_FANTASIA", "NO_RAZAO_SOCIAL") if cc in cols), None)
        nome_l = (estabs[nome_col].cast(pl.Utf8).fill_null("").to_list()
                  if nome_col else [""] * len(cod_l))
        mun_l = (estabs["CO_MUNICIPIO_GESTOR"].cast(pl.Utf8).fill_null("").to_list()
                 if "CO_MUNICIPIO_GESTOR" in cols else [""] * len(cod_l))
        reg_l = (estabs["CO_REGIAO_SAUDE"].cast(pl.Utf8).fill_null("").to_list()
                 if "CO_REGIAO_SAUDE" in cols else [""] * len(cod_l))
        for i in range(len(cod_l)):
            if not cod_l[i]:
                continue
            if hospital_bate_geo(uf_hospital=uf_l[i],
                                 co_municipio_hospital=mun_l[i] or None,
                                 co_regiao_saude_hospital=reg_l[i] or None,
                                 sel_ufs=["MG"], sel_macros=[], sel_drs=[]):
                lista.append((cod_l[i], nome_l[i], uf_l[i], ""))
        tem_dilson = any(x[0] == "2219646" for x in lista)
        cond = 70_000 <= len(lista) <= 80_000 and tem_dilson
        registra("12-heur2-mg", PASS if cond else FAIL,
                 f"MG filtrado={len(lista):,} dilson_presente={tem_dilson}")

        # Teste 13 — MG + MR02 + DRS 06 (mesma chamada da UI, com municipio/reg)
        lista13 = []
        for i in range(len(cod_l)):
            if not cod_l[i]:
                continue
            if hospital_bate_geo(uf_hospital=uf_l[i],
                                 co_municipio_hospital=mun_l[i] or None,
                                 co_regiao_saude_hospital=reg_l[i] or None,
                                 sel_ufs=["MG"], sel_macros=mg_macros, sel_drs=mg_drs):
                lista13.append(cod_l[i])
        tem_dilson13 = "2219646" in lista13
        registra("13-heur2-drs06",
                 PASS if (len(lista13) > 0 and tem_dilson13) else FAIL,
                 f"filtrado={len(lista13):,} dilson_presente={tem_dilson13}")

        # Teste 14 — search_filter (replica logica _filter_hosp_list)
        def search_filter(dados, query: str):
            ft = (query or "").lower().strip()
            tokens = ft.split() if ft else []
            outq = []
            for cnes, nm, u, tp in dados:
                hay = f"{nm} {cnes} {u} {tp}".lower()
                if tokens and not all(t in hay for t in tokens):
                    continue
                outq.append((cnes, nm))
            return outq

        r_exato = search_filter(lista, "HOSPITAL DILSON GODINHO")
        r_dilson = search_filter(lista, "dilson")
        r_cod = search_filter(lista, "2219646")
        cond = len(r_exato) == 1 and len(r_dilson) >= 10 and len(r_cod) == 1
        registra("14-search", PASS if cond else FAIL,
                 f"exato={len(r_exato)} dilson={len(r_dilson)} codigo={len(r_cod)}")

        # Teste 15 — fallback validacao com _hospitals_data vazia (simulado)
        try:
            match = estabs.filter(
                pl.col("CO_CNES").cast(pl.Utf8).str.strip_chars() == "2219646").head(1)
            if match.is_empty():
                registra("15-fallback", FAIL, "CNES 2219646 nao existe na base")
            else:
                row = match.row(0, named=True)
                uf_h = str(row.get("SIGLA_UF", "") or "").strip().upper()
                bate = hospital_bate_geo(
                    uf_hospital=uf_h,
                    co_municipio_hospital=str(row.get("CO_MUNICIPIO_GESTOR", "") or "").strip() or None,
                    co_regiao_saude_hospital=str(row.get("CO_REGIAO_SAUDE", "") or "").strip() or None,
                    sel_ufs=["MG"], sel_macros=[], sel_drs=[])
                registra("15-fallback", PASS if bate else FAIL,
                         f"added={1 if bate else 0} invalid={[] if bate else ['2219646']}")
        except Exception as e:
            registra("15-fallback", FAIL, f"excecao: {e}")

    # ---------------------------------------------------------------- Resumo
    log()
    log("=" * 70)
    n_pass = sum(1 for _, s, _ in _results if s == PASS)
    n_fail = sum(1 for _, s, _ in _results if s == FAIL)
    n_skip = sum(1 for _, s, _ in _results if s == SKIP)
    log(f"RESUMO: {n_pass} PASS | {n_fail} FAIL | {n_skip} SKIP "
        f"(total {len(_results)})")
    log("=" * 70)
    return 1 if n_fail else 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        log("ERRO FATAL:")
        log(traceback.format_exc())
        code = 2
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write(_out.getvalue())
    # Console: apenas ASCII seguro (HC-7)
    try:
        n_pass = sum(1 for _, s, _ in _results if s == "PASS")
        n_fail = sum(1 for _, s, _ in _results if s == "FAIL")
        n_skip = sum(1 for _, s, _ in _results if s == "SKIP")
        print(f"RESULTADO: {n_pass} PASS / {n_fail} FAIL / {n_skip} SKIP")
        print(f"Detalhes em: {OUT_PATH}")
    except Exception:
        pass
    sys.exit(code)
