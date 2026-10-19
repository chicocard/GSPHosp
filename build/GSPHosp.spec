# -*- mode: python ; coding: utf-8 -*-
"""
GSPHosp.spec — PyInstaller (modo onedir) para o GSPHosp.

Uso (na raiz do projeto):
    pyinstaller build\\GSPHosp.spec --noconfirm
    pyinstaller build\\GSPHosp.spec --noconfirm -- --console   # variante com console (diagnóstico)

Resultado: dist\\GSPHosp\\GSPHosp.exe + pasta _internal\\ (Qt, QtWebEngine, Polars, DuckDB, dados do pacote).
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

RAIZ = Path(SPECPATH).resolve().parent          # pasta do projeto (build\ está um nível abaixo)
CONSOLE = "--console" in sys.argv                # pyinstaller ... -- --console

# Dados do pacote papahr: counties.parquet, theme.qss, assets do mapa (html, js, css, malha), etc.
datas = collect_data_files("papahr")

# pyqtgraph entra pelos imports normais do app; collect_submodules("pyqtgraph") importa
# pyqtgraph.examples e derruba o processo de análise, por isso não é usado.
hiddenimports = (
    collect_submodules("papahr")
    + [
        "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore", "PySide6.QtWebChannel",
        "PySide6.QtSvg", "PySide6.QtOpenGL", "PySide6.QtNetwork",
        "pyqtgraph", "polars", "duckdb", "pyarrow", "structlog",
    ]
)

# Módulos que não entram no instalador (opcionais ou só de desenvolvimento)
excludes = [
    "llama_cpp", "torch", "tensorflow", "matplotlib", "IPython", "jupyter", "notebook",
    "pytest", "PyInstaller", "tkinter", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtBluetooth", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtDataVisualization", "PySide6.QtCharts", "PySide6.QtPdf", "PySide6.QtRemoteObjects",
]

a = Analysis(
    [str(RAIZ / "build" / "launcher.py")],
    pathex=[str(RAIZ)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GSPHosp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # UPX quebra DLLs do Qt; manter desligado
    console=CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(RAIZ / "build" / "icone.ico"),
    version=str(RAIZ / "build" / "versao.txt") if (RAIZ / "build" / "versao.txt").exists() else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="GSPHosp",
)
