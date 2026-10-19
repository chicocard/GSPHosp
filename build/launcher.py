"""
launcher.py — ponto de entrada do executável GSPHosp.exe (PyInstaller).

Equivale a `python -m papahr`. Antes de abrir a interface:
  * garante que o QtWebEngine seja importado antes do QApplication;
  * define pastas de dados do usuário fora da pasta de instalação
    (variáveis que o app pode ler: GSPHOSP_HOME, GSPHOSP_OUTPUT);
  * grava um log de inicialização em %LOCALAPPDATA%\\GSPHosp\\logs para
    diagnosticar falhas que acontecem antes do logger do app existir.
"""
from __future__ import annotations

import os
import runpy
import sys
import traceback
from datetime import datetime
from pathlib import Path


def _pastas() -> tuple[Path, Path]:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "GSPHosp"
    docs = Path.home() / "Documents" / "GSPHosp"
    for p in (base / "logs", base / "cache", base / "cnes", docs / "output"):
        p.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("GSPHOSP_HOME", str(base))
    os.environ.setdefault("GSPHOSP_OUTPUT", str(docs / "output"))
    return base, docs


def main() -> int:
    base, _ = _pastas()
    log = base / "logs" / "launcher.log"
    try:
        # QtWebEngine precisa ser importado antes de QApplication existir
        from PySide6.QtCore import QCoreApplication, Qt  # noqa: F401
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        import PySide6.QtWebEngineWidgets  # noqa: F401

        with log.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} start frozen={getattr(sys, 'frozen', False)} exe={sys.executable}\n")
        runpy.run_module("papahr", run_name="__main__", alter_sys=True)
        return 0
    except SystemExit as e:
        return int(e.code or 0)
    except Exception:
        with log.open("a", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(None, "GSPHosp", f"O GSPHosp não conseguiu iniciar.\n\nDetalhes em:\n{log}")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
