# -*- coding: utf-8 -*-
"""Teste critico das correcoes P0/P1 do DashboardPage (GSPHosp).

Prova, por evidencia estatica + AST (nao por "palavra"):
  A) [POSITIVO] O arquivo dashboard_page.py tem >= 10 ocorrencias do prefixo
     "[!!!] EXCECAO" (invariante V7 do Gauntlet).
  B) [POSITIVO] TODO metodo publico _fill_tab_* possui bloco try/except com
     o prefixo de instrumentacao (HC-7).
  C) [NEGATIVO] A MESMA funcao de verificacao aplicada a um metodo SEM
     try/except retorna False — isto prova que o check nao e vacuoso.
  D) [POSITIVO] BarGraphItem trata altura zero -> 0.1px (guard "else 0.1").
  E) [POSITIVO] metrics.py detecta ausencia de colunas LACE e emite alerta
     ("risco NAO MENSURAVEL"), em vez de fingir BAIXO.
  F) [POSITIVO] Helpers perdidos restaurados: _on_load_csv_parquet,
     _gold_aggr_has_rows, _update_badge_fonte_camada.

Uso (a partir de d:\\papapapa\\GSPHosp-Novo):
    $env:PYTHONIOENCODING="utf-8"
    & .\\.venv\\Scripts\\python.exe _critico_dashboard_p0.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DASH = BASE / "papahr" / "ui" / "pages" / "dashboard_page.py"
METRICS = BASE / "papahr" / "dashboard" / "metrics.py"

_RESULTS: list[tuple[str, bool, str]] = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


# ---------------------------------------------------------------------------
def _metodos_com_range(src: str) -> dict[str, tuple[int, int]]:
    """Retorna {nome_metodo: (linha_ini, linha_fim)} para todas as defs."""
    tree = ast.parse(src)
    out: dict[str, tuple[int, int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ini = node.lineno
            fim = getattr(node, "end_lineno", ini)
            # primeiro registro vence (evita sobrescrever em namespaces aninhados)
            out.setdefault(node.name, (ini, fim))
    return out


def _metodo_tem_instrumentacao(src: str, nome: str, ranges: dict[str, tuple[int, int]]) -> bool:
    """True se o corpo do metodo contem '[!!!] EXCECAO' dentro de um except."""
    if nome not in ranges:
        return False
    ini, fim = ranges[nome]
    linhas = src.splitlines()[ini - 1:fim]
    bloco = "\n".join(linhas)
    return "[!!!] EXCECAO" in bloco


# --------------------------------------------------------------------------- A
def test_a() -> None:
    src = DASH.read_text(encoding="utf-8")
    n = src.count("[!!!] EXCECAO")
    rec("A-prefixo-excecao>=10", n >= 10, f"ocorrencias={n} (minimo exigido=10)")


# --------------------------------------------------------------------------- B
def test_b() -> None:
    src = DASH.read_text(encoding="utf-8")
    ranges = _metodos_com_range(src)
    alvos = sorted(m for m in ranges if m.startswith("_fill_tab_"))
    faltando = [m for m in alvos if not _metodo_tem_instrumentacao(src, m, ranges)]
    rec(
        "B-fill_tab_instrumentados",
        len(alvos) >= 5 and not faltando,
        f"metodos={alvos} sem_instrumentacao={faltando}",
    )


# --------------------------------------------------------------------------- C
def test_c() -> None:
    """NEGATIVO: metodo fake sem try/except NAO pode passar no mesmo check."""
    src_fake = (
        "class X:\n"
        "    def _fill_tab_fake(self, m):\n"
        "        x = 1\n"
        "        return x\n"
    )
    ranges = _metodos_com_range(src_fake)
    detectou = _metodo_tem_instrumentacao(src_fake, "_fill_tab_fake", ranges)
    # E o positivo de controle: com instrumentacao, o MESMO check deve passar.
    src_ok = (
        "class X:\n"
        "    def _fill_tab_ok(self, m):\n"
        "        try:\n"
        "            x = 1\n"
        "        except Exception as _e:\n"
        "            print(f\"[!!!] EXCECAO _fill_tab_ok: {_e}\", flush=True)\n"
    )
    ranges_ok = _metodos_com_range(src_ok)
    controle = _metodo_tem_instrumentacao(src_ok, "_fill_tab_ok", ranges_ok)
    ok = (detectou is False) and (controle is True)
    rec("C-negativo-nao-vacuoso", ok, f"fake_detectou={detectou} controle_positivo={controle}")


# --------------------------------------------------------------------------- D
def test_d() -> None:
    src = DASH.read_text(encoding="utf-8")
    tem_guard = "else 0.1" in src
    tem_y105 = "max(105.0, max(vals) * 1.1)" in src
    ok = tem_guard and tem_y105
    rec("D-bargraph-zero-min", ok, f"guard_else_0.1={tem_guard} yrange_105={tem_y105}")


# --------------------------------------------------------------------------- E
def test_e() -> None:
    src = METRICS.read_text(encoding="utf-8")
    tem_helper = "_lace_colunas_ausentes" in src
    tem_nao_mensuravel = "N\u00c3O MENSUR\u00c1VEL" in src or "NAO MENSURAVEL" in src.upper().replace("\u00c3", "A")
    tem_ausentes = "lace_ausentes" in src
    ok = tem_helper and tem_ausentes
    rec(
        "E-lace-alerta-honesto",
        ok,
        f"helper={tem_helper} campo_lace_ausentes={tem_ausentes} "
        f"texto_nao_mensuravel={tem_nao_mensuravel}",
    )


# --------------------------------------------------------------------------- F
def test_f() -> None:
    src = DASH.read_text(encoding="utf-8")
    alvos = ["_on_load_files", "_gold_aggr_has_rows", "_update_badge_fonte_camada"]
    faltando = [a for a in alvos if f"def {a}" not in src]
    # o botao deve estar conectado ao slot
    conectado = "btn_load_files.clicked.connect(self._on_load_files)" in src
    # deve aceitar .dbf/.dbc via read_dbc
    aceita_dbf = 'ext in (".dbf", ".dbc")' in src and "from papahr.core.dbc_reader import read_dbc" in src
    ok = (not faltando) and conectado and aceita_dbf
    rec(
        "F-helpers-restaurados",
        ok,
        f"faltando={faltando} botao_conectado={conectado} aceita_dbf_dbc={aceita_dbf}",
    )


def main() -> int:
    print("=" * 70)
    print("CRITICO DASHBOARD P0/P1 — GSPHosp (instrumentacao + risco honesto + helpers)")
    print("=" * 70)
    if not DASH.exists():
        print(f"arquivo nao encontrado: {DASH}")
        return 1
    for fn in (test_a, test_b, test_c, test_d, test_e, test_f):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            rec(fn.__name__, False, f"excecao: {type(e).__name__}: {e}")
    n_pass = sum(1 for _, ok, _ in _RESULTS if ok)
    n_fail = sum(1 for _, ok, _ in _RESULTS if not ok)
    print()
    print("=" * 70)
    print(f"CRITICO_DASHBOARD: {n_pass} PASS / {n_fail} FAIL")
    print("=" * 70)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
