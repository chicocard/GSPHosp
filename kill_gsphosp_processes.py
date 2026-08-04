r"""Matar processos python que sao GSPHosp (SELETIVO! Nao toca em python.exe GERAL).

Retorno via print(qtd): quantidade de processos mortos (le via batch >> tempfile).
Uso (batch):
    set "_MATAQTY=0"
    "%PYEXE%" kill_gsphosp_processes.py 1>"%TEMP%\gsp_kill.txt" 2>nul
    for /f %%N in ("%TEMP%\gsp_kill.txt") do set "_MATAQTY=%%~N"
"""
from __future__ import annotations

import ctypes
import os
import re
import signal
import subprocess
import sys

KILL_PATTERNS = (
    r"papahr\.ui\.app",
    r"GSPHosp",
    r"papahr-python",
)


def main() -> int:
    killed = 0
    try:
        Kernel32 = ctypes.windll.kernel32
    except Exception:  # noqa: BLE001
        Kernel32 = None  # type: ignore[assignment]

    try:
        cmd = [
            "wmic", "PROCESS",
            "where", "name='python.exe' or name='pythonw.exe'",
            "get", "CommandLine,ProcessId",
            "/FORMAT:list",
        ]
        raw = subprocess.check_output(cmd, text=True, errors="ignore")
    except Exception:  # noqa: BLE001
        print(0)
        return 0

    pats = [re.compile(p, re.IGNORECASE) for p in KILL_PATTERNS]
    cur: dict[str, str] = {}
    for ln in raw.splitlines():
        ln2 = ln.strip()
        if not ln2:
            continue
        if "=" not in ln2:
            continue
        k, v = ln2.split("=", 1)
        k = k.strip(); v = v.strip()
        if k == "CommandLine":
            cur["cl"] = v
        elif k == "ProcessId":
            cur["pid"] = v
        if "cl" in cur and "pid" in cur:
            try:
                pid = int(cur["pid"])
            except Exception:  # noqa: BLE001
                cur.clear(); continue
            try:
                mypid = os.getpid()
                myppid = os.getppid()
            except Exception:  # noqa: BLE001
                mypid = -1; myppid = -1
            if pid == mypid or pid == myppid:
                cur.clear(); continue
            if not any(p.search(cur.get("cl", "")) for p in pats):
                cur.clear(); continue
            try:
                os.kill(pid, signal.SIGTERM)  # type: ignore[attr-defined]
                killed += 1
            except Exception:
                try:
                    if Kernel32 is not None:
                        handle = Kernel32.OpenProcess(1, 0, pid)
                        if handle:
                            Kernel32.TerminateProcess(handle, 0)
                            Kernel32.CloseHandle(handle)
                            killed += 1
                except Exception:  # noqa: BLE001
                    pass
            cur.clear()
    print(killed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
