"""Módulo de persistência DuckDB (arquitetura Medallion Bronze/Silver/Gold)."""
from __future__ import annotations

from papahr.db.connection import DuckDBConnection, get_db

__all__ = ["DuckDBConnection", "get_db"]
