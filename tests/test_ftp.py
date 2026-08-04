"""Testes do cliente FTP (modo offline só com parse de nome de arquivo)."""
from __future__ import annotations

import re

import pytest


def test_file_regex_dbc() -> None:
    r = re.compile(r"^(?P<tipo>[A-Z]{2})(?P<uf>[A-Z]{2})(?P<ym>\d{4})\.dbc$", re.I)
    cases = [
        ("PACE2301.dbc", ("PA", "CE", "2301")),
        ("RDSP2212.dbc", ("RD", "SP", "2212")),
        ("RJMG0101.dbc", ("RJ", "MG", "0101")),
    ]
    for fname, expected in cases:
        m = r.match(fname)
        assert m is not None, fname
        assert (m.group("tipo").upper(), m.group("uf").upper(), m.group("ym")) == expected


def test_zip_regex_sigtap() -> None:
    r = re.compile(
        r"^TabelaUnificada_(?P<versao>\d+)_(?P<year>\d{4})(?P<month>\d{2})\.zip$", re.I
    )
    m = r.match("TabelaUnificada_202301_202301.zip")
    assert m is not None
    assert m.group("year") == "2023"
    assert m.group("month") == "01"
