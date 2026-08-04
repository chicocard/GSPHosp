"""Utilitários diversos."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable, Iterator, Sequence, TypeVar

import polars as pl

T = TypeVar("T")

_MES_PORTUGUES = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}


def month_name_pt(month: int, numbered: bool = True) -> str:
    """Nome do mês em português, opcionalmente com prefixo numérico."""
    m = _MES_PORTUGUES.get(month, str(month))
    return f"{month:02d} - {m}" if numbered else m


def year_month_iter(year_start: int, month_start: int, year_end: int, month_end: int) -> Iterator[tuple[int, int]]:
    """Itera (ano, mes) no intervalo fechado."""
    y, m = year_start, month_start
    while (y, m) <= (year_end, month_end):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def ymd(yyyymm: str) -> date:
    """Converte string AAAAMM para date(dia=1)."""
    s = str(yyyymm).strip()
    return date(int(s[:4]), int(s[4:6]), 1)


def chunkify(items: Sequence[T], data_type: str | None = None) -> list[list[T]]:
    """Divide items em chunks para processamento em lote.

    Equivalente à função chunk() do R.
    """
    from papahr.config import get_settings
    s = get_settings()
    peso = s.chunk_memory_weight if data_type in {"PA", "SP"} else s.chunk_default_weight
    n = len(items)
    if n == 0:
        return []
    chunk_size = max(1, int(peso * n) or 1)
    n_chunks = (n + chunk_size - 1) // chunk_size
    return [items[i * chunk_size : (i + 1) * chunk_size] for i in range(n_chunks)]


def ensure_dir(path: Path | str) -> Path:
    """Cria diretório (se não existir) e retorna o Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def trim_all_strings(df: pl.DataFrame) -> pl.DataFrame:
    """Remove espaços à direita em todas as colunas de string."""
    return df.with_columns(
        pl.col(c).str.strip_chars_end() for c in df.columns if df[c].dtype == pl.Utf8
    )


def to_numeric(df: pl.DataFrame, columns: Iterable[str], strict: bool = False) -> pl.DataFrame:
    """Converte colunas para numérico (coerce em não estrito)."""
    cols = [c for c in columns if c in df.columns]
    if strict:
        return df.with_columns(pl.col(cols).cast(pl.Float64))
    return df.with_columns(pl.col(cols).cast(pl.Float64, strict=False))


def human_readable_size(num_bytes: int) -> str:
    """Formata bytes em string legível."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"
