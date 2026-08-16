"""Teste da configuração."""
from __future__ import annotations

from papahr.config import Settings, get_settings


def test_settings_singleton() -> None:
    a = get_settings()
    b = get_settings()
    assert a is b


def test_settings_defaults() -> None:
    s = Settings()
    assert s.app_name == "GSPHosp"
    assert s.ftp_timeout > 0
    assert 0 < s.chunk_memory_weight < 1
    assert s.cache_dir.exists()
