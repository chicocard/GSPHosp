"""Ponto de entrada de `python -m papahr`.

- Sem argumentos      → abre a GUI desktop (comportamento histórico).
- Com argumentos      → CLI (`python -m papahr run --system SIH-RD ...`, `--help`, ...).
"""
from __future__ import annotations

import sys


def _main() -> int:
    if len(sys.argv) > 1:
        from papahr.cli import main as cli_main
        return int(cli_main() or 0)
    from papahr.ui.app import main as gui_main
    return int(gui_main() or 0)


if __name__ == "__main__":
    raise SystemExit(_main())
