"""SMOKE HEADLESS: DashboardPage com outputSIH_RD.parquet real.
Executa fora da GUI (QT_QPA_PLATFORM=offscreen) para capturar stack trace das exceções
que antes causavam "abas congeladas".
"""
from __future__ import annotations
import os
import sys
import io
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import polars as pl

def main() -> int:
    print("=" * 80, flush=True)
    print("[SMOKE] Iniciando headless dashboard smoke test...", flush=True)
    erros: list[str] = []

    # 1. Carregar dataset real (se existir)
    parquet_path = ROOT / "output" / "outputSIH_RD.parquet"
    if parquet_path.exists():
        print(f"[SMOKE] Carregando dataset: {parquet_path} (size={parquet_path.stat().st_size/1024/1024:.1f}MB)", flush=True)
        df = pl.read_parquet(parquet_path)
        print(f"[SMOKE] Dataset carregado: shape={df.shape}, cols={df.columns[:10]}...", flush=True)
    else:
        print(f"[SMOKE] Dataset NAO encontrado. Usando DataFrame VAZIO.", flush=True)
        df = pl.DataFrame()

    # 2. Calcular métricas (fora da UI)
    try:
        from papahr.dashboard.metrics import compute_dashboard
        print("[SMOKE] Rodando compute_dashboard (system_kind=SIH_RD)...", flush=True)
        m = compute_dashboard(df, system_kind="SIH_RD", compute_huff=False)
        print(f"[SMOKE] compute_dashboard OK. alerts={m.alerts}", flush=True)
        print(f"[SMOKE]  estratificacao_risco shape={m.estratificacao_risco.shape}, cols={m.estratificacao_risco.columns}", flush=True)
        print(f"[SMOKE]  eficiencia_clinica shape={m.eficiencia_clinica.shape}, cols={m.eficiencia_clinica.columns}", flush=True)
        print(f"[SMOKE]  integracao_territorial shape={m.integracao_territorial.shape}", flush=True)
        print(f"[SMOKE]  icsap_por_condicao shape={m.icsap_por_condicao.shape}", flush=True)
        print(f"[SMOKE]  benchmarking shape={m.benchmarking.shape}", flush=True)
        print(f"[SMOKE]  distance_decay shape={m.distance_decay.shape}", flush=True)
        print(f"[SMOKE]  glosas_causas shape={m.glosas_causas.shape}", flush=True)
        print(f"[SMOKE]  serie_temporal shape={m.serie_temporal.shape}", flush=True)
        print(f"[SMOKE]  transicao_cuidado shape={m.transicao_cuidado.shape}", flush=True)
    except Exception as e:
        print(f"[FATAL] compute_dashboard crashou: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return 2

    # 3. Inicializar Qt e Widget
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        print("[SMOKE] QApplication OK (offscreen).", flush=True)
    except Exception as e:
        print(f"[FATAL] QApplication crashou: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return 3

    try:
        from papahr.ui.pages.dashboard_page import DashboardPage
        page = DashboardPage()
        print("[SMOKE] DashboardPage instanciado OK.", flush=True)
    except Exception as e:
        print(f"[FATAL] DashboardPage __init__ crashou: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return 4

    # 4. Rodar _on_ready - o slot que preenche todas as abas
    #    Redirecionamos stdout/stderr para capturar as mensagens [!!!]
    captured = io.StringIO()
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    try:
        import sys as _sys
        class _Tee:
            def __init__(self, *files):
                self.files = files
            def write(self, data):
                for f in self.files:
                    try:
                        f.write(data)
                    except Exception:
                        pass
            def flush(self):
                for f in self.files:
                    try:
                        f.flush()
                    except Exception:
                        pass
        sys.stdout = _Tee(old_stdout, captured)
        sys.stderr = _Tee(old_stderr, captured)

        print("\n" + "="*80, flush=True)
        print("[SMOKE] INICIANDO _on_ready(m) - O SLOT QUE PREENCHE TODAS AS ABAS", flush=True)
        print("="*80, flush=True)
        page._on_ready(m)
        print("\n" + "="*80, flush=True)
        print("[SMOKE] _on_ready(m) TERMINOU SEM CRASH GLOBAL.", flush=True)
        print("="*80 + "\n", flush=True)
    except Exception as e:
        print(f"\n[FATAL] _on_ready CRASH GLOBAL (PySide6 NÃO deveria engolir isto): {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        erros.append(f"_on_ready GLOBAL CRASH: {type(e).__name__}: {e}")
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        captured_text = captured.getvalue()
        # Salvar evidências em arquivo
        evid_path = ROOT / "debug-EVIDENCIAS-headless.txt"
        with open(evid_path, "w", encoding="utf-8") as f:
            f.write(captured_text)
        print(f"[SMOKE] Evidências capturadas salvas em: {evid_path}", flush=True)
        # Contar [!!!]
        count = captured_text.count("[!!!] EXCECAO")
        print(f"[SMOKE] Contagem de exceções isoladas (try/except): {count}", flush=True)
        if count > 0:
            for linha in captured_text.splitlines():
                if "[!!!] EXCECAO" in linha:
                    print(f"   >> {linha}", flush=True)

    print("[SMOKE] Smoke headless finalizado: 0 erros globais, exceções isoladas acima.", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
