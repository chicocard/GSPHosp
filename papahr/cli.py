"""Linha de comando (CLI) para PaPAHR.

Uso:
  papahr-cli run --system SIA-PA --state CE --establishments 2561492,2481286 \\
      --year-start 2023 --month-start 1 --year-end 2023 --month-end 3
  papahr-cli run-local --system SIA-PA --state CE --dbc-dir ./dbc
  papahr-cli download --system SIA-PA --dest ./dbc --year-start 2023 --month-start 1
  papahr-cli summarize outputSIA_PA.parquet --question "Quais os top 5 procedimentos?"
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import polars as pl
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from papahr.config import get_settings
from papahr.core.ftp_client import FTPClient
from papahr.core.utils import ensure_dir
from papahr.datasources.cnes import CNESRepository
from papahr.datasources.counties import CountiesRepository
from papahr.datasources.sigtap import SIGTAPRepository
from papahr.llm import LLMAnalyst
from papahr.logging_config import configure_logging
from papahr.pipelines import PipelineProgress, PipelineRequest, run_pipeline
from papahr.security import DateRange

console = Console()


def _progress_from_rich(progress: Progress, task_id: int) -> callable:
    def cb(p: PipelineProgress) -> None:
        progress.update(
            task_id,
            total=max(1, p.total),
            completed=p.current,
            description=f"[cyan]{p.stage}",
        )
        if p.message:
            progress.console.print(f"  └ {p.message}")
    return cb


def cmd_run(args: argparse.Namespace) -> int:
    dr = DateRange(
        year_start=args.year_start, month_start=args.month_start,
        year_end=args.year_end, month_end=args.month_end,
    )
    ufs = [s.strip().upper() for s in args.state.split(",") if s.strip()]
    estab = [s.strip() for s in args.establishments.split(",")] if args.establishments else None
    counties = [s.strip() for s in args.counties.split(",")] if args.counties else None

    req = PipelineRequest(
        information_system=args.system,
        state_abbr=ufs,
        county_id=counties,
        health_establishment_id=estab,
        save_csv=not args.no_csv,
        save_parquet=not args.no_parquet,
        date_range=dr,
    )
    with Progress(
        SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
        TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(),
    ) as progress:
        task_id = progress.add_task("Iniciando")
        result = run_pipeline(req, progress_cb=_progress_from_rich(progress, task_id))

    table = Table(title=f"Resultado - {result.system}")
    table.add_column("Campo", style="cyan")
    table.add_column("Valor", style="bold green")
    table.add_row("Linhas processadas", f"{result.rows:,}")
    table.add_row("Tempo total", f"{result.elapsed_seconds:.1f}s")
    table.add_row("CSV", str(result.output_csv) if result.output_csv else "(não)")
    table.add_row("Parquet", str(result.output_parquet) if result.output_parquet else "(não)")
    table.add_row("Procedimentos revogados (principal)", f"{len(result.revoked_procedures_main)}")
    table.add_row("Procedimentos revogados (secundário)", f"{len(result.revoked_procedures_sec)}")
    console.print(table)

    if result.warnings:
        console.print("[yellow]Avisos:")
        for w in result.warnings:
            console.print(f"  ⚠ {w}")
    return 0


def cmd_run_local(args: argparse.Namespace) -> int:
    ufs = [s.strip().upper() for s in args.state.split(",") if s.strip()]
    estab = [s.strip() for s in args.establishments.split(",")] if args.establishments else None
    counties = [s.strip() for s in args.counties.split(",")] if args.counties else None
    req = PipelineRequest(
        information_system=args.system,
        state_abbr=ufs,
        county_id=counties,
        health_establishment_id=estab,
        save_csv=not args.no_csv,
        save_parquet=not args.no_parquet,
        dbc_dir_path=Path(args.dbc_dir).resolve(),
    )
    with Progress(
        SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
        TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(),
    ) as progress:
        task_id = progress.add_task("Iniciando")
        result = run_pipeline(req, progress_cb=_progress_from_rich(progress, task_id))
    console.print(
        f"[green]Pronto.[/green] {result.rows} linhas em {result.elapsed_seconds:.1f}s"
        f" | CSV: {result.output_csv} | Parquet: {result.output_parquet}"
    )
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    from papahr.pipelines import _SYS_DATA_TYPE
    import time
    src, dtype = _SYS_DATA_TYPE[args.system]
    dr = DateRange(
        year_start=args.year_start, month_start=args.month_start,
        year_end=args.year_end, month_end=args.month_end,
    )
    start = date(dr.year_start, dr.month_start, 1)
    end = date(dr.year_end, dr.month_end, 1)
    state_abbr = [s.strip().upper() for s in args.state.split(",")]
    from papahr.pipelines import RemotePipeline
    p = RemotePipeline()
    files = p._list_remote_files(src, dtype, state_abbr, start, end)  # noqa: SLF001
    dest = ensure_dir(Path(args.dest).resolve() / args.system)
    ftp = FTPClient()
    base_url = f"ftp://{get_settings().datasus_ftp_host}/dissemin/publicos/{src}SUS/200801_/Dados/"
    with Progress(
        SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
        TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(),
    ) as progress:
        task_id = progress.add_task("Baixando", total=len(files))
        for i, (fname, _d, _uf) in enumerate(files, 1):
            try:
                ftp.download_ftp(base_url + fname, dest / fname)
            except Exception as e:  # noqa: PERF203
                console.print(f"[red]Falha: {fname} -> {e}")
            progress.update(task_id, completed=i)
            time.sleep(0.01)
    console.print(f"[green]{len(files)} arquivos em {dest}")
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    path = Path(args.data_path).resolve()
    if not path.exists():
        console.print(f"[red]Arquivo nao encontrado: {path}")
        return 2
    if path.suffix in (".parquet",):
        df = pl.read_parquet(path)
    else:
        df = pl.read_csv(path, separator=";", infer_schema_length=10_000)
    console.print(f"[cyan]Dados: {df.height} linhas x {df.width} colunas")
    analyst = LLMAnalyst()
    if not analyst.available:
        console.print("[yellow]LLM indisponivel.\n" + analyst.install_hint())
        return 1
    if args.question:
        resp = analyst.answer_question(df, args.question)
    else:
        resp = analyst.summarize_dataset(df)
    console.rule("[bold green]Análise LLM")
    console.print(resp.text)
    console.print(
        f"\n[dim]prompt={resp.tokens_prompt} tok | "
        f"generated={resp.tokens_generated} tok | "
        f"{resp.elapsed_seconds:.1f}s"
    )
    return 0


def cmd_prepare_aux(args: argparse.Namespace) -> int:
    """Baixa counties + CNES + SIGTAP mais recentes."""
    s = get_settings()
    console.print("[cyan]Preparando dados auxiliares...")
    if args.download_counties or args.all:
        CountiesRepository().load(download=True)
        console.print("  [green]Counties OK")
    if args.download_cnes or args.all:
        CNESRepository().load_health_establishments(newer=True)
        console.print("  [green]CNES OK")
    if args.download_sigtap or args.all:
        SIGTAPRepository().download(newer=True)
        console.print("  [green]SIGTAP OK")
    console.print(f"Cache: {s.cache_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gsphosp-cli", description="GSPHosp CLI — Gestão em Saúde Populacional Hospitalar")
    sub = p.add_subparsers(dest="command", required=True)

    # run
    r = sub.add_parser("run", help="Executa pipeline via FTP DATASUS")
    r.add_argument("--system", required=True, choices=["SIA-PA", "SIH-RD", "SIH-RJ", "SIH-SP"])
    r.add_argument("--state", required=True, help="Ex.: CE ou CE,PE,SP")
    r.add_argument("--year-start", type=int, required=True)
    r.add_argument("--month-start", type=int, required=True)
    r.add_argument("--year-end", type=int, required=True)
    r.add_argument("--month-end", type=int, required=True)
    r.add_argument("--counties", help="IDs IBGE separados por vírgula")
    r.add_argument("--establishments", help="CNES separados por vírgula")
    r.add_argument("--no-csv", action="store_true")
    r.add_argument("--no-parquet", action="store_true")
    r.set_defaults(func=cmd_run)

    # run-local
    rl = sub.add_parser("run-local", help="Executa pipeline com DBCs locais")
    rl.add_argument("--system", required=True, choices=["SIA-PA", "SIH-RD", "SIH-RJ", "SIH-SP"])
    rl.add_argument("--state", required=True)
    rl.add_argument("--dbc-dir", required=True)
    rl.add_argument("--counties", default=None)
    rl.add_argument("--establishments", default=None)
    rl.add_argument("--no-csv", action="store_true")
    rl.add_argument("--no-parquet", action="store_true")
    rl.set_defaults(func=cmd_run_local)

    # download
    d = sub.add_parser("download", help="Apenas baixa arquivos DBC")
    d.add_argument("--system", required=True, choices=["SIA-PA", "SIH-RD", "SIH-RJ", "SIH-SP"])
    d.add_argument("--state", required=True)
    d.add_argument("--year-start", type=int, required=True)
    d.add_argument("--month-start", type=int, required=True)
    d.add_argument("--year-end", type=int, required=True)
    d.add_argument("--month-end", type=int, required=True)
    d.add_argument("--dest", required=True)
    d.set_defaults(func=cmd_download)

    # summarize
    s = sub.add_parser("summarize", help="Resumo com LLM local de um CSV/Parquet")
    s.add_argument("data_path")
    s.add_argument("--question", "-q", help="Pergunta livre sobre os dados")
    s.set_defaults(func=cmd_summarize)

    # prepare-aux
    a = sub.add_parser("prepare-aux", help="Baixa dados auxiliares (counties/CNES/SIGTAP)")
    a.add_argument("--all", action="store_true")
    a.add_argument("--download-counties", action="store_true")
    a.add_argument("--download-cnes", action="store_true")
    a.add_argument("--download-sigtap", action="store_true")
    a.set_defaults(func=cmd_prepare_aux)

    return p


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    settings = get_settings()
    settings.ensure_dirs()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrompido pelo usuario")
        return 130
    except Exception as e:  # noqa: BLE001
        console.print(f"[bold red]Erro:[/bold red] {e}")
        if "--debug" in sys.argv:
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
