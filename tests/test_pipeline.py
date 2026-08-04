"""Testes do pipeline e processadores (sem dependências externas)."""
from __future__ import annotations

import polars as pl
import pytest

from papahr.core.utils import trim_all_strings, to_numeric
from papahr.processors import (
    PROCESSOR_MAP,
    ProcessorContext,
    SIAPAProcessor,
    get_processor,
)
from papahr.datasources.counties import CountyData
from papahr.datasources.cnes import CNESHealthEstablishment
from papahr.datasources.sigtap import ProcedureDetails


@pytest.fixture()
def mini_ctx() -> ProcessorContext:
    counties = CountyData(pl.DataFrame({
        "id_municipio": ["230440", "230370"],
        "nome_municipio": ["Fortaleza", "Caucaia"],
        "nome_regiao": ["Nordeste", "Nordeste"],
        "nome_microrregiao": ["Metropolitana", "Metropolitana"],
        "nome_mesorregiao": ["Nordeste Cearense", "Nordeste Cearense"],
        "nome_estado": ["Ceará", "Ceará"],
        "sigla_estado": ["CE", "CE"],
        "id_estado": ["23", "23"],
    }))
    estab = CNESHealthEstablishment(pl.DataFrame({
        "CNES": ["2561492", "2481286"],
        "FANTASIA": ["Hospital A", "Hospital B"],
        "COMPETEN": ["202301", "202301"],
        "NO_ESTABELECIMENTO": ["2561492-Hospital A", "2481286-Hospital B"],
    }))
    proc = ProcedureDetails(pl.DataFrame({
        "CO_PROCEDIMENTO": ["0301010010", "0301020017"],
        "file_version_id": ["202301", "202301"],
        "NO_PROCEDIMENTO": ["0301010010-CONSULTA", "0301020017-EXAME"],
        "CO_GRUPO": ["03", "03"],
        "NO_GRUPO": ["03-CLINICA", "03-CLINICA"],
        "CO_SUB_GRUPO": ["01", "02"],
        "NO_SUB_GRUPO": ["01-CONSULTAS", "02-EXAMES"],
        "CO_FORMA_ORGANIZACAO": ["00", "00"],
        "NO_FORMA_ORGANIZACAO": ["00-BASICA", "00-BASICA"],
        "CO_FINANCIAMENTO": ["01", "06"],
        "NO_FINANCIAMENTO": ["01-PAB", "06-MAC"],
        "NO_SUB_FINANCIAMENTO": ["01-Atenção Básica", "06-Média e Alta Complexidade"],
        "COMPLEXIDADE": ["1-Atenção Básica", "2-Média Complexidade"],
    }))
    cbo = pl.DataFrame({
        "CO_OCUPACAO": ["2251", "2235"],
        "NO_OCUPACAO": ["2251-MEDICO", "2235-ENFERMEIRO"],
        "file_version_id": ["202301", "202301"],
    })
    cid = pl.DataFrame({
        "CO_CID": ["I10", "J45"],
        "NO_CID": ["I10-HIPERTENSAO", "J45-ASMA"],
        "file_version_id": ["202301", "202301"],
    })
    return ProcessorContext(
        counties=counties, establishments=estab,
        procedure_details=proc, cbo=cbo, cid=cid,
    )


class TestProcessorMap:
    def test_all_systems_present(self) -> None:
        for s in ["SIA-PA", "SIH-RD", "SIH-RJ", "SIH-SP"]:
            assert s in PROCESSOR_MAP
            assert get_processor(s) is not None


class TestSIAPAProcessor:
    def test_process_empty(self, mini_ctx: ProcessorContext) -> None:
        proc = SIAPAProcessor()
        out = proc.process(pl.DataFrame(), mini_ctx)
        assert out.is_empty()

    def test_process_sample(self, mini_ctx: ProcessorContext) -> None:
        proc = SIAPAProcessor()
        raw = pl.DataFrame({
            "PA_CODUNI": ["2561492"],
            "PA_UFMUN": ["230440"],
            "PA_MUNPCN": ["230440"],
            "PA_PROC_ID": ["0301010010"],
            "PA_CBOCOD": ["2251"],
            "PA_CIDPRI": ["I10"],
            "PA_CMP": ["202301"],
            "PA_MVM": ["202302"],
            "PA_DOCORIG": ["I"],
            "PA_QTDAPR": ["10"],
            "PA_QTDPRO": ["10"],
            "PA_VALAPR": ["100.00"],
            "PA_VALPRO": ["120.00"],
        })
        out = proc.process(raw, mini_ctx)
        assert not out.is_empty()
        assert "Procedimentos realizados" in out.columns
        assert out["Frequencia"].dtype.is_numeric()
        assert "Estabelecimento CNES" in out.columns


class TestDataUtils:
    def test_trim_strings(self) -> None:
        df = pl.DataFrame({"a": [" abc  ", "de f "]})
        trimmed = trim_all_strings(df)
        assert trimmed["a"][0] == " abc"  # strip_end

    def test_to_numeric(self) -> None:
        df = pl.DataFrame({"val": ["1", "2.5", "x"]})
        conv = to_numeric(df, ["val"])
        assert conv["val"].dtype == pl.Float64
        assert conv["val"][0] == 1.0
