"""Script entrypoint para PyInstaller: main.pyw abre a GUI sem console."""
from papahr.ui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
