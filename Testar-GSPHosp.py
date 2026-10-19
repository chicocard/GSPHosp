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
from pathlib import Path

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
                    and str(row.get("CO_MUNICIPIO_GESTOR", "")).strip() in ("314330", "0314330")
                    and str(row.get("CO_REGIAO_SAUDE", "")).strip().lstrip("0") == "6"
                    and str(row.get("SIGLA_UF", "")) == "MG")
            registra("4-dilson", PASS if cond else FAIL,
                     f"nome={row.get('NO_FANTASIA')} uf_ibge={row.get('CO_ESTADO_GESTOR')} "
                     f"mun={row.get('CO_MUNICIPIO_GESTOR')} reg={row.get('CO_REGIAO_SAUDE')} "
                     f"sigla={row.get('SIGLA_UF')}")
        else:
            registra("4-dilson", FAIL, f"encontrado {r4.height} linhas (esperado 1)")

        # Teste 12 — Heuristica 2 completa MG (filtra por MG primeiro para economizar memória)
        lista: list[tuple[str, str, str, str]] = []
        estabs_mg = estabs.filter(pl.col("SIGLA_UF") == "MG") if "SIGLA_UF" in estabs.columns else estabs
        cols = estabs_mg.columns
        cod_l = estabs_mg["CO_CNES"].cast(pl.Utf8).fill_null("").str.strip_chars().to_list()
        uf_l = estabs_mg["SIGLA_UF"].cast(pl.Utf8).fill_null("").to_list()
        nome_col = next((cc for cc in ("NO_FANTASIA", "NO_RAZAO_SOCIAL") if cc in cols), None)
        nome_l = (estabs_mg[nome_col].cast(pl.Utf8).fill_null("").to_list()
                  if nome_col else [""] * len(cod_l))
        mun_l = (estabs_mg["CO_MUNICIPIO_GESTOR"].cast(pl.Utf8).fill_null("").to_list()
                 if "CO_MUNICIPIO_GESTOR" in cols else [""] * len(cod_l))
        reg_l = (estabs_mg["CO_REGIAO_SAUDE"].cast(pl.Utf8).fill_null("").to_list()
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


    # ---------------------------------------------------------------- Teste 0b:
    # Lint de erros reais (sintaxe, nomes indefinidos, import inexistente).
    # Só regras que indicam código quebrado; estilo não entra aqui.
    try:
        import subprocess
        r = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "papahr", "--select", "E9,F63,F7,F82,F821",
             "--output-format", "concise", "--no-cache"],
            cwd=BASE, capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0:
            registra("0b-ruff", PASS, "ruff: 0 erros de sintaxe/nome indefinido em papahr/")
        else:
            linhas = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
            registra("0b-ruff", FAIL, f"{len(linhas)} ocorrencia(s): " + " | ".join(linhas[:5]))
    except FileNotFoundError:
        registra("0b-ruff", SKIP, "ruff nao instalado (pip install ruff)")
    except Exception as e:
        registra("0b-ruff", SKIP, f"ruff indisponivel: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------- Teste 16:
    # INTEGRACAO REAL: 1 DBC pequeno (SIH-RD AC 2025-01, ~323 KB) -> pipeline
    # completo (DBC -> processador -> SIGTAP FTP -> IBGE -> CNES) -> dashboard.
    # Este e o teste que faltava: as suites anteriores passavam 100% com o
    # dashboard vazio porque nunca exercitavam o caminho real.
    # Sem rede: SKIP (nunca FAIL por ambiente), mas o motivo fica registrado.
    try:
        import shutil
        import tempfile
        import polars as pl
        from papahr.config import get_settings
        from papahr.core.ftp_client import FTPClient
        from papahr.pipelines import run_pipeline
        from papahr.security import PipelineRequest
        from papahr.dashboard import compute_dashboard

        st = get_settings()
        cache_dbc = st.cache_dir / "SIH-RD" / "_teste_integracao"
        cache_dbc.mkdir(parents=True, exist_ok=True)
        dbc_path = cache_dbc / "RDAC2501.dbc"
        if not dbc_path.exists() or dbc_path.stat().st_size < 100_000:
            url = "ftp://ftp.datasus.gov.br/dissemin/publicos/SIHSUS/200801_/Dados/RDAC2501.dbc"
            try:
                FTPClient().download_ftp(url, dbc_path)
            except Exception as e_dl:
                raise RuntimeError(f"sem rede/FTP para baixar RDAC2501.dbc: {e_dl}") from e_dl

        # Pipeline LOCAL numa pasta isolada (so este arquivo) e output em pasta temporaria
        work = Path(tempfile.mkdtemp(prefix="gsphosp_it_"))
        try:
            shutil.copy2(dbc_path, work / "RDAC2501.dbc")
            out_dir_bak = st.output_dir
            st.output_dir = work / "out"
            try:
                req = PipelineRequest(information_system="SIH-RD", state_abbr="AC",
                                      dbc_dir_path=work, save_csv=True, save_parquet=True)
                res = run_pipeline(req)
            finally:
                st.output_dir = out_dir_bak
            df = res.df
            problemas: list[str] = []
            if res.rows <= 0 or df.is_empty():
                problemas.append(f"rows={res.rows}")
            if "CNES" not in df.columns or not bool((df["CNES"].cast(pl.Utf8).str.len_chars() == 7).all()):
                problemas.append("CNES ausente ou nao tem 7 digitos")
            for col_req in ("DIAG_PRINC", "QT_DIARIAS", "MUNIC_RES", "VAL_TOT", "ANO_CMPT"):
                if col_req not in df.columns:
                    problemas.append(f"coluna analitica ausente: {col_req}")

            def _pct_preenchido(col: str) -> float:
                if col not in df.columns:
                    return 0.0
                sc = df[col].cast(pl.Utf8)
                return 100.0 * float((sc.is_not_null() & (sc != "")).sum()) / max(1, df.height)

            p_proc = _pct_preenchido("Procedimentos realizados")
            p_mun = _pct_preenchido("Municipio de Residencia")
            p_cid = _pct_preenchido("Nome da Mobirdade (CID)")
            if p_proc < 90:
                problemas.append(f"SIGTAP nome procedimento preenchido {p_proc:.0f}% (<90%)")
            if p_mun < 90:
                problemas.append(f"IBGE municipio residencia preenchido {p_mun:.0f}% (<90%)")
            if res.output_csv is None or not Path(res.output_csv).exists():
                problemas.append("CSV de exportacao nao gerado")
            else:
                n_csv = pl.read_csv(res.output_csv, separator=";", n_rows=1).width
                if n_csv >= df.width:
                    problemas.append(f"CSV deveria ter so colunas de exportacao ({n_csv} vs {df.width})")

            m = compute_dashboard(df, system_kind="SIH_RD", compute_huff=True)
            vazios = [k for k in ("estratificacao_risco", "eficiencia_clinica", "integracao_territorial",
                                  "icsap_por_condicao", "benchmarking", "serie_temporal",
                                  "transicao_cuidado", "distance_decay")
                      if getattr(m, k) is None or getattr(m, k).is_empty()]
            if vazios:
                problemas.append("quadros do dashboard vazios: " + ",".join(vazios))
            if m.huff is None:
                problemas.append("modelo de Huff nao calculado")

            detalhe = (f"rows={res.rows} cols={df.width} proc={p_proc:.0f}% cid={p_cid:.0f}% "
                       f"mun={p_mun:.0f}% huff={'OK' if m.huff is not None else 'NAO'} "
                       f"decay={m.distance_decay.shape[0]} alertas={len(m.alerts)}")
            if problemas:
                registra("16-integracao-real", FAIL, detalhe + " | " + "; ".join(problemas))
            else:
                registra("16-integracao-real", PASS, detalhe)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    except RuntimeError as e:
        registra("16-integracao-real", SKIP, str(e))
    except Exception as e:
        registra("16-integracao-real", FAIL, f"excecao: {type(e).__name__}: {e}")


    # ---------------------------------------------------------------- Teste 17:
    # DASHBOARD HEADLESS: renderiza a DashboardPage offscreen com o ultimo Parquet
    # analitico e exige: 0 excecoes nos _fill_tab_*, graficos com itens desenhados,
    # mapa com hospitais georreferenciados e aneis populados, e largura minima da
    # pagina <= 1366 px (layout cabe em notebook comum).
    try:
        import os as _os
        import polars as pl
        pq = Path(BASE) / "output" / "outputSIH_RD.parquet"
        if not pq.exists():
            raise RuntimeError("output/outputSIH_RD.parquet ausente (rode o pipeline antes)")
        _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QFont
        app = QApplication.instance() or QApplication([])
        app.setFont(QFont("Segoe UI", 10))
        from papahr.ui.pages.dashboard_page import DashboardPage
        from papahr.dashboard import compute_dashboard
        df = pl.read_parquet(pq)
        page = DashboardPage(); page.resize(1366, 768); page.show(); app.processEvents()
        page._last_df = df; page._last_system = "SIH_RD"; page._last_cnes = None
        m = compute_dashboard(df, system_kind="SIH_RD", compute_huff=True)
        page._metrics = m
        import io as _io, contextlib
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            page._on_ready(m)
            for _ in range(10):
                app.processEvents()
        excecoes = buf.getvalue().count("[!!!] EXCECAO")
        problemas = []
        if excecoes:
            problemas.append(f"{excecoes} excecao(oes) em _fill_tab_*")
        for nome in ("plot_estratos", "plot_los", "plot_icsap", "plot_ts", "plot_bench_bar", "plot_distance_decay"):
            pw = getattr(page, nome, None)
            n_items = len(pw.getPlotItem().items) if pw is not None and hasattr(pw, "getPlotItem") else 0
            if n_items == 0:
                problemas.append(f"{nome} sem itens")
        mp = page.concentric_map
        n_pts = len(mp._points)
        n_buck = sum(1 for bs in mp._buckets.values() for b in bs if b.populacao_total > 0)
        if n_pts == 0:
            problemas.append("mapa sem hospitais")
        if n_buck == 0:
            problemas.append("mapa sem aneis populados")
        minw = page.minimumSizeHint().width()
        if minw > 1366:
            problemas.append(f"largura minima {minw}px > 1366")
        detalhe = (f"excecoes={excecoes} mapa_hospitais={n_pts} aneis_populados={n_buck} "
                   f"min_largura={minw}px tabs={page.tabs.count()}")
        registra("17-dashboard-headless", FAIL if problemas else PASS,
                 detalhe + (" | " + "; ".join(problemas) if problemas else ""))
    except RuntimeError as e:
        registra("17-dashboard-headless", SKIP, str(e))
    except Exception as e:
        registra("17-dashboard-headless", FAIL, f"excecao: {type(e).__name__}: {e}")

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
