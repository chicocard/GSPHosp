"""python -m papahr.ui.atratividade — abre a janela na aba "Dados" (pasta · UF · período · fontes).

    python -m papahr.ui.atratividade                                   # aba Dados com a última seleção usada
    python -m papahr.ui.atratividade --dados D:\\AIH_RD_2025 --uf MG --periodo 2025-01:2025-12
                                                                       # preenche a seleção e já carrega
    python -m papahr.ui.atratividade --dados D:\\AIH_RD_2025 --uf MG --periodo 2024-01:2024-12 --fontes SIH,CIHA --baixar
                                                                       # baixa do DATASUS o que faltar e carrega
    python -m papahr.ui.atratividade --dados output\\outputSIH_RD.parquet   # parquet/csv do pipeline (sem seleção)
    python -m papahr.ui.atratividade --demo                            # hospitais fictícios
Opções:
    --uf MG[,SP]          UF(s) dos hospitais analisados
    --periodo AAAA-MM:AAAA-MM   competências, inclusive (padrão: último ano fechado)
    --fontes SIH,CIHA     fontes (padrão: SIH)
    --baixar              baixa do FTP do DATASUS os arquivos da seleção que faltarem na pasta, antes de carregar
    --sem-ibge            população do CNES em vez do IBGE
    --cnes CNES           hospital inicial em foco (7 dígitos)
    --listar              lista os arquivos de dados das pastas usuais e sai
    --inspecionar ARQ     colunas, tipos e distribuições de um .dbc/.dbf (conferir o layout da CIHA) e sai
    --sem-cache           relê os arquivos mesmo que já estejam consolidados em cache
    --nao-recarregar      não recarregar sozinho a última seleção ao abrir
Variáveis: GSPHOSP_PROCESSOS=1 desliga a leitura paralela (padrão: até 3 processos).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from papahr.dashboard.importar_rd import COLUNAS_UTEIS, Selecao, carregar_fonte  # noqa: F401  (reexportado)


def _procurar_dados() -> list[Path]:
    """parquet/csv do pipeline e pastas com RD*/CIHA*.dbc nos lugares usuais (mais recentes primeiro)."""
    import os
    from papahr.dashboard.importar_rd import PADRAO_CIHA, PADRAO_RD
    raizes = [Path.cwd() / "output", Path.cwd() / "dist" / "output", Path.home() / "Documents" / "GSPHosp" / "output",
              Path.home() / "Documentos" / "GSPHosp" / "output", Path(os.environ.get("LOCALAPPDATA", "")) / "GSPHosp",
              Path(os.environ.get("GSPHOSP_OUTPUT", "")), Path.home() / ".gsphosp", Path.home() / "Downloads"]
    for d in ("D:/", "C:/", "E:/"):
        if Path(d).is_dir():
            raizes += [p for p in Path(d).iterdir() if p.is_dir() and any(k in p.name.upper() for k in ("AIH", "RD", "SIH", "CIHA", "DATASUS"))]
    achados: list[Path] = []
    for r in raizes:
        if r and r.is_dir():
            try:
                achados += [f.resolve() for f in r.rglob("*")
                            if (f.suffix.lower() in (".parquet", ".csv") and f.stat().st_size > 100_000) or PADRAO_RD.match(f.name) or PADRAO_CIHA.match(f.name)]
            except OSError:
                pass
    return sorted(set(achados), key=lambda f: f.stat().st_mtime, reverse=True)[:60]


def _periodo(txt: str | None) -> tuple[str | None, str | None]:
    if not txt:
        return None, None
    m = re.fullmatch(r"\s*(\d{4})-?(\d{2})\s*[:a]\s*(\d{4})-?(\d{2})\s*", txt)
    if not m:
        sys.exit(f"--periodo deve ser AAAA-MM:AAAA-MM (recebi {txt!r})")
    return f"{m.group(1)}{m.group(2)}", f"{m.group(3)}{m.group(4)}"


def demo() -> None:
    ap = argparse.ArgumentParser(prog="python -m papahr.ui.atratividade", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dados", help="PASTA com RD*/CIHA*.dbc do DATASUS, ou um ARQUIVO (.dbc/.dbf/parquet/csv)")
    ap.add_argument("--uf", help="UF(s) dos hospitais, ex.: MG ou MG,SP")
    ap.add_argument("--periodo", help="AAAA-MM:AAAA-MM (competências, inclusive)")
    ap.add_argument("--fontes", default=None, help="SIH, CIHA ou SIH,CIHA")
    ap.add_argument("--baixar", action="store_true", help="baixa do DATASUS o que faltar antes de carregar")
    ap.add_argument("--demo", action="store_true", help="abre a demonstração sintética")
    ap.add_argument("--listar", action="store_true", help="só lista os arquivos de dados encontrados nas pastas usuais e sai")
    ap.add_argument("--inspecionar", metavar="ARQUIVO", help="mostra colunas, tipos e distribuições de um .dbc/.dbf e sai")
    ap.add_argument("--cnes", help="CNES inicial em foco")
    ap.add_argument("--sem-ibge", action="store_true", help="não baixar população do IBGE")
    ap.add_argument("--sem-cache", action="store_true", help="relê os arquivos mesmo com consolidado em cache")
    ap.add_argument("--nao-recarregar", action="store_true", help="não recarregar sozinho a última seleção ao abrir")
    args = ap.parse_args()
    if args.inspecionar:
        from papahr.dashboard.importar_rd import inspecionar_arquivo
        print(inspecionar_arquivo(args.inspecionar))
        return
    if args.listar:
        achados = _procurar_dados()
        print("\n".join(str(f) for f in achados) if achados else "nenhum parquet/csv/RD*/CIHA*.dbc encontrado nas pastas usuais")
        return
    if args.dados and not Path(args.dados).exists():
        print(f"caminho não encontrado: {args.dados}\n-- arquivos de dados encontrados nos lugares usuais:")
        for f in _procurar_dados():
            print(f"   {f}")
        sys.exit(2)
    if args.sem_cache:
        import papahr.dashboard.importar_rd as imp
        _orig = imp.ler_arquivos
        imp.ler_arquivos = lambda arquivos, *, usar_cache=True, **kw: _orig(arquivos, usar_cache=False, **kw)
    inicio, fim = _periodo(args.periodo)
    ufs = [u.strip().upper() for u in (args.uf or "").split(",") if u.strip()]
    fontes = [f.strip().upper() for f in (args.fontes or "").split(",") if f.strip()] or None

    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    # importar as telas ANTES do QApplication (QtWebEngine)
    from papahr.ui.atratividade.janela import JanelaAtratividade

    app = QApplication(sys.argv)
    app.setStyle("Fusion"); app.setFont(QFont("Segoe UI", 9))

    auto = False if (args.nao_recarregar or args.dados or args.demo) else None     # com --dados/--demo, a carga pedida manda
    jan = JanelaAtratividade(cnes_inicial=args.cnes, carregar_ultima=auto)
    tela = app.primaryScreen().availableGeometry()
    jan.resize(min(1440, tela.width() - 40), min(860, tela.height() - 60))
    jan.move(tela.center() - jan.rect().center())
    jan.show()
    pg = jan.dados
    if args.sem_ibge:
        pg.rb_cnes.setChecked(True)
    if args.dados and Path(args.dados).is_file():
        jan.carregar_caminho(args.dados, uf=ufs[0] if ufs else None, sem_ibge=args.sem_ibge)
    elif args.dados:
        pg.definir(pasta=args.dados, ufs=ufs or None, inicio=inicio, fim=fim, fontes=fontes)
        if ufs and inicio:
            sel = pg.selecao()
            if args.baixar and pg._faltam:
                # baixa e, ao terminar, carrega
                def _depois(r, _sel=sel, _pasta=args.dados):
                    jan.carregar_selecao(_pasta, _sel, args.sem_ibge)
                jan.baixar(args.dados, list(pg._faltam)); jan._dl.pronto.connect(_depois)
            else:
                jan.carregar_selecao(args.dados, sel, args.sem_ibge)
    elif args.demo:
        jan.carregar_demo()
    sys.exit(app.exec())


if __name__ == "__main__":
    demo()
