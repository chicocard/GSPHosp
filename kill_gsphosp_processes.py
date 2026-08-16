r"""Matar processos python que são GSPHosp (SELETIVO! Não toca em python.exe GERAL)."""
from __future__ import annotations

import os
import sys

def main() -> int:
    killed = 0
    mypid = os.getpid()
    try:
        import psutil
        for proc in psutil.process_iter():
            try:
                if proc.pid == mypid:
                    continue
                cmd_list = proc.cmdline()
                if not cmd_list:
                    continue
                cmd = " ".join(cmd_list).lower()
                if any(k in cmd for k in ("papahr", "gsphosp", "testar-gsphosp")):
                    proc.kill()
                    killed += 1
            except Exception:
                continue
    except Exception:
        pass
    print(killed)
    return 0

if __name__ == "__main__":
    main()
