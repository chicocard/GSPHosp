"""Testes de segurança e validação de entrada."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from papahr.config import Settings, get_settings
from papahr.core.utils import chunkify, month_name_pt, ymd
from papahr.security import (
    DateRange,
    PipelineRequest,
    SecretManager,
    safe_path,
    validate_cnes_ids,
    validate_procedure_codes,
)


class TestDateRange:
    def test_valid_range(self) -> None:
        dr = DateRange(year_start=2023, month_start=1, year_end=2023, month_end=3)
        assert dr.year_start == 2023
        assert dr.month_start == 1

    def test_invalid_order(self) -> None:
        with pytest.raises(Exception):
            DateRange(year_start=2024, month_start=1, year_end=2023, month_end=3)

    def test_invalid_month(self) -> None:
        with pytest.raises(Exception):
            DateRange(year_start=2023, month_start=13, year_end=2023, month_end=3)


class TestPipelineRequest:
    def test_valid_remote(self) -> None:
        req = PipelineRequest(
            information_system="SIA-PA",
            state_abbr="CE",
            date_range=DateRange(year_start=2023, month_start=1, year_end=2023, month_end=3),
        )
        assert req.state_abbr == ["CE"]
        assert req.information_system == "SIA-PA"

    def test_invalid_system(self) -> None:
        with pytest.raises(Exception):
            PipelineRequest(
                information_system="INVALID",
                state_abbr="CE",
                date_range=DateRange(year_start=2023, month_start=1, year_end=2023, month_end=3),
            )

    def test_invalid_uf(self) -> None:
        with pytest.raises(Exception):
            PipelineRequest(
                information_system="SIA-PA",
                state_abbr="ZZ",
                date_range=DateRange(year_start=2023, month_start=1, year_end=2023, month_end=3),
            )

    def test_county_truncation(self) -> None:
        req = PipelineRequest(
            information_system="SIA-PA",
            state_abbr="CE",
            county_id=["2304400"],  # 7 dígitos → deve virar 6
            date_range=DateRange(year_start=2023, month_start=1, year_end=2023, month_end=3),
        )
        assert req.county_id == ["230440"]

    def test_multi_uf_all(self) -> None:
        req = PipelineRequest(
            information_system="SIA-PA",
            state_abbr=["CE", "PE"],
            date_range=DateRange(year_start=2023, month_start=1, year_end=2023, month_end=3),
        )
        assert req.state_abbr == ["CE", "PE"]

    def test_local_path_validation(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            req = PipelineRequest(
                information_system="SIA-PA",
                state_abbr="CE",
                dbc_dir_path=Path(td),
            )
            assert req.dbc_dir_path is not None

    def test_missing_dates_and_local(self) -> None:
        with pytest.raises(Exception):
            PipelineRequest(information_system="SIA-PA", state_abbr="CE")


class TestSafePath:
    def test_safe_child(self, tmp_path: Path) -> None:
        assert safe_path(tmp_path, "sub/file.csv") == (tmp_path / "sub" / "file.csv").resolve()

    def test_traversal_blocked(self, tmp_path: Path) -> None:
        with pytest.raises(PermissionError):
            safe_path(tmp_path, "../malicious.csv")


class TestValidators:
    def test_cnes_valid(self) -> None:
        assert validate_cnes_ids(["1234567", "7654321"])

    def test_cnes_invalid(self) -> None:
        with pytest.raises(ValueError):
            validate_cnes_ids(["abc"])

    def test_procedure_valid(self) -> None:
        assert validate_procedure_codes(["1234567890"])

    def test_procedure_invalid(self) -> None:
        with pytest.raises(ValueError):
            validate_procedure_codes(["123"])


class TestUtils:
    def test_ymd(self) -> None:
        assert ymd("202301") == date(2023, 1, 1)

    def test_month_name(self) -> None:
        assert "Janeiro" in month_name_pt(1)

    def test_chunkify(self) -> None:
        items = list(range(20))
        chunks = chunkify(items, "PA")
        assert sum(len(c) for c in chunks) == 20
        assert all(c for c in chunks)
