"""Processadores de dados de produção ambulatorial/hospitalar.

Cada processador recebe o DataFrame bruto lido de DBC + tabelas auxiliares
(counties, CNES, SIGTAP procedure_details, CBO, CID) e retorna o output
estruturado conforme as especificações do pacote R original, com melhorias
de performance via Polars (LazyFrame + coelecimento de joins).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Optional

import polars as pl

from papahr.core.utils import month_name_pt, to_numeric, trim_all_strings, ymd
from papahr.datasources.counties import CountyData
from papahr.datasources.cnes import CNESHealthEstablishment
from papahr.datasources.sigtap import ProcedureDetails

from papahr.logging_config import get_logger

log = get_logger(__name__)


def _check_filter(df: pl.DataFrame, values: Optional[list[str]], col: str) -> pl.DataFrame:
    """Equivalente à função check_filter do R."""
    if not values or col not in df.columns:
        return df
    return df.filter(pl.col(col).is_in(values))


@dataclass(slots=True)
class ProcessorContext:
    counties: CountyData
    establishments: CNESHealthEstablishment
    procedure_details: ProcedureDetails
    cbo: pl.DataFrame
    cid: pl.DataFrame

    def cid_enhanced(self) -> pl.DataFrame:
        if self.cid.is_empty():
            return pl.DataFrame(schema={"CO_CID": pl.Utf8, "NO_CID": pl.Utf8, "file_version_id": pl.Utf8})
        df = self.cid
        for c in df.columns:
            if c.endswith("CID") and df[c].dtype == pl.Utf8:
                df = df.with_columns(pl.col(c).str.strip_chars())
        if "CO_CID" in df.columns and "NO_CID" in df.columns:
            df = df.with_columns(
                pl.concat_str([pl.col("CO_CID"), pl.lit("-"), pl.col("NO_CID").fill_null("")]).alias("NO_CID")
            )
        return df

    def cbo_enhanced(self) -> pl.DataFrame:
        if self.cbo.is_empty():
            return pl.DataFrame(schema={"CO_OCUPACAO": pl.Utf8, "NO_OCUPACAO": pl.Utf8, "file_version_id": pl.Utf8})
        return self.cbo


class BaseProcessor(ABC):
    """Interface comum para todos os processadores SIA/SIH."""

    system_name: str = ""
    numeric_cols: tuple[str, ...] = ()
    #: Mapa coluna_interna -> rótulo de EXPORTAÇÃO (layout do pacote R original).
    #: O DataFrame devolvido por `process()` contém estas colunas renomeadas E
    #: TODAS as colunas analíticas brutas/enriquecidas (CNES, DIAG_PRINC, QT_DIARIAS,
    #: MUNIC_RES, ...). Só o CSV/XLSX de exportação é restrito aos rótulos (ver
    #: `export_view`). Dashboard, DuckDB e Parquet consomem o DataFrame completo.
    EXPORT_RENAME: dict[str, str] = {}

    @abstractmethod
    def process(
        self,
        raw: pl.DataFrame,
        ctx: ProcessorContext,
        *,
        county_id: Optional[list[str]] = None,
        health_establishment_id: Optional[list[str]] = None,
    ) -> pl.DataFrame: ...

    # Helpers comuns
    @staticmethod
    def _trim_and_convert(df: pl.DataFrame, numeric_cols: tuple[str, ...]) -> pl.DataFrame:
        df = trim_all_strings(df)
        return to_numeric(df, list(numeric_cols))

    def _finalize_output(self, df: pl.DataFrame) -> pl.DataFrame:
        """Renomeia as colunas de exportação e PRESERVA as colunas analíticas.

        Ordem final: [rótulos de exportação..., colunas analíticas...]. Colunas
        temporárias (prefixo "_") são descartadas. A chave `CNES` é normalizada
        para 7 dígitos (Utf8, zero à esquerda) — contrato do dashboard/DuckDB.
        """
        rename_map = self.EXPORT_RENAME
        existing = {k: v for k, v in rename_map.items() if k in df.columns and k != v}
        # Cópia com o rótulo de exportação; o nome ORIGINAL permanece disponível
        # (ex.: "Ano processamento" E "ANO_CMPT"), pois dashboard/DuckDB usam o original.
        if existing:
            df = df.with_columns([pl.col(k).alias(v) for k, v in existing.items()])
        export_cols = [c for c in rename_map.values() if c in df.columns]
        export_set = set(export_cols)
        analytic_cols = [c for c in df.columns if c not in export_set and not c.startswith("_")]
        df = df.select(export_cols + analytic_cols)
        if "CNES" in df.columns:
            df = df.with_columns(
                pl.col("CNES").cast(pl.Utf8, strict=False).str.strip_chars().str.zfill(7).alias("CNES")
            )
        return self._trim_and_convert(df, self.numeric_cols)

    @classmethod
    def export_view(cls, df: pl.DataFrame) -> pl.DataFrame:
        """Projeção SOMENTE com as colunas de exportação (CSV/XLSX, layout R)."""
        cols = [c for c in cls.EXPORT_RENAME.values() if c in df.columns]
        return df.select(cols) if cols else df


# ---------------------------------------------------------------------------
# SIA-PA: Produção Ambulatorial
# ---------------------------------------------------------------------------

class SIAPAProcessor(BaseProcessor):
    system_name = "SIA-PA"
    numeric_cols = ("Frequencia", "Quantidade Apresentada", "Valor Aprovado", "Valor Apresentado")
    EXPORT_RENAME = {
        "DT_CMP": "Mes/Ano de Atendimento",
        "ANO_CMP": "Ano de Atendimento",
        "MES_CMP": "Mes de Atendimento (Numero)",
        "NM_MES_CMP": "Mes de Atendimento",
        "PA_CMP": "Ano/Mes de Atendimento",
        "PA_MVM": "Ano/Mes de Processamento",
        "DT_MVM": "Mes/Ano de Processamento",
        "ANO_MVM": "Ano de Processamento",
        "MES_MVM": "Mes de Processamento (Numero)",
        "NM_MES_MVM": "Mes de Processamento",
        "NO_CID": "Nome da Morbidade (CID)",
        "PA_PROC_ID": "CO Procedimentos realizados",
        "NO_PROCEDIMENTO": "Procedimentos realizados",
        "NO_GRUPO": "Grupo de Procedimentos",
        "NO_SUB_GRUPO": "SubGrupo de Procedimentos",
        "NO_FORMA_ORGANIZACAO": "Forma de organizacao",
        "NO_FINANCIAMENTO": "Tipo de Financiamento",
        "NO_SUB_FINANCIAMENTO": "TipoFin/Subtipo Financiamento",
        "NO_OCUPACAO": "Profissional-CBO",
        "TIPO_REGISTRO": "Instrumento de registro",
        "COMPLEXIDADE": "Complexidade Procedimento",
        "PA_QTDAPR": "Frequencia",
        "PA_QTDPRO": "Quantidade Apresentada",
        "PA_VALAPR": "Valor Aprovado",
        "PA_VALPRO": "Valor Apresentado",
        "nome_municipio": "Municipio Residencia",
        "nome_regiao": "Regiao Residencia",
        "nome_microrregiao": "Micro IBGE Residencia",
        "nome_mesorregiao": "Meso IBGE Residencia",
        "nome_estado": "Estado Residencia",
        "NO_ESTABELECIMENTO": "Estabelecimento CNES",
        "PA_UFMUN": "Cod do Municipio do Estabelecimento",
        "municipio_estabelecimento": "Municipio do Estabelecimento",
        "nome_regiao_estabelecimento": "Regiao do Estabelecimento",
    }

    def process(
        self,
        raw: pl.DataFrame,
        ctx: ProcessorContext,
        *,
        county_id: Optional[list[str]] = None,
        health_establishment_id: Optional[list[str]] = None,
    ) -> pl.DataFrame:
        if raw.is_empty():
            return pl.DataFrame()
        cid_df = ctx.cid_enhanced()
        cbo_df = ctx.cbo_enhanced()
        proc_df = ctx.procedure_details.df

        municipios_estab = ctx.counties.df.select(
            pl.col("id_municipio"),
            pl.col("nome_municipio").alias("municipio_estabelecimento"),
            pl.col("nome_regiao").alias("nome_regiao_estabelecimento"),
        )

        df = raw.with_columns(pl.col("PA_CODUNI").cast(pl.Utf8).alias("CNES"))
        df = _check_filter(df, county_id, "PA_UFMUN")
        df = _check_filter(df, health_establishment_id, "CNES")

        # Joins de município (residência e estabelecimento)
        counties_df = ctx.counties.df
        if "PA_MUNPCN" in df.columns and "id_municipio" in counties_df.columns:
            df = df.join(counties_df, left_on="PA_MUNPCN", right_on="id_municipio", how="left")
        if "PA_UFMUN" in df.columns:
            df = df.join(municipios_estab, left_on="PA_UFMUN", right_on="id_municipio", how="left")

        # SIGTAP procedures
        if not proc_df.is_empty() and "PA_PROC_ID" in df.columns and "PA_CMP" in df.columns:
            df = df.join(
                proc_df, left_on=["PA_PROC_ID", "PA_CMP"], right_on=["CO_PROCEDIMENTO", "file_version_id"], how="left"
            )

        # CBO
        if not cbo_df.is_empty() and "PA_CBOCOD" in df.columns and "PA_CMP" in df.columns:
            join_keys = [k for k in ["PA_CBOCOD", "PA_CMP"] if k in df.columns]
            right_keys = [k for k in ["CO_OCUPACAO", "file_version_id"]]
            if len(join_keys) == 2:
                df = df.join(cbo_df, left_on=join_keys, right_on=right_keys, how="left")

        # CID
        if not cid_df.is_empty() and "PA_CIDPRI" in df.columns and "PA_CMP" in df.columns:
            df = df.join(
                cid_df, left_on=["PA_CIDPRI", "PA_CMP"], right_on=["CO_CID", "file_version_id"], how="left"
            )

        # CNES estabelecimentos
        if not ctx.establishments.df.is_empty() and "CNES" in df.columns:
            df = df.join(ctx.establishments.df, on="CNES", how="left", coalesce=True)

        # Colunas derivadas de data
        df = df.with_columns(
            pl.col("PA_CMP").cast(pl.Utf8).map_elements(lambda s: ymd(s) if s else None, return_dtype=pl.Date).alias("DT_CMP"),
        )
        df = df.with_columns(
            pl.col("DT_CMP").dt.year().alias("ANO_CMP"),
            pl.col("DT_CMP").dt.month().alias("MES_CMP"),
        ).with_columns(
            pl.col("MES_CMP").map_elements(month_name_pt, return_dtype=pl.Utf8).alias("NM_MES_CMP"),
        )

        if "PA_MVM" in df.columns:
            df = df.with_columns(
                pl.col("PA_MVM").cast(pl.Utf8).map_elements(lambda s: ymd(s) if s else None, return_dtype=pl.Date).alias("DT_MVM"),
            ).with_columns(
                pl.col("DT_MVM").dt.year().alias("ANO_MVM"),
                pl.col("DT_MVM").dt.month().alias("MES_MVM"),
            ).with_columns(
                pl.col("MES_MVM").map_elements(month_name_pt, return_dtype=pl.Utf8).alias("NM_MES_MVM"),
            )

        # TIPO_REGISTRO
        if "PA_DOCORIG" in df.columns:
            df = df.with_columns(
                pl.when(pl.col("PA_DOCORIG") == "C")
                .then(pl.lit("BPA - Consolidado"))
                .when(pl.col("PA_DOCORIG") == "I")
                .then(pl.lit("BPA - Individualizado"))
                .when(pl.col("PA_DOCORIG") == "P")
                .then(pl.lit("APAC - Procedimento Principal"))
                .when(pl.col("PA_DOCORIG") == "S")
                .then(pl.lit("APAC - Procedimento Secundário"))
                .otherwise(pl.lit(""))
                .alias("TIPO_REGISTRO")
            )

        # Trata NO_CID faltante
        if "NO_CID" in df.columns:
            df = df.with_columns(
                pl.col("NO_CID").fill_null("0000-Nao informado")
            )

        # Trata residência 999999
        if "PA_MUNPCN" in df.columns and "nome_municipio" in df.columns:
            geo_cols = ["nome_estado", "nome_microrregiao", "nome_mesorregiao", "nome_municipio", "nome_regiao"]
            df = df.with_columns(
                [
                    pl.when(pl.col("PA_MUNPCN").cast(pl.Utf8) == "999999")
                    .then(pl.lit("Nao informado"))
                    .otherwise(pl.col(c).fill_null(""))
                    .alias(c)
                    for c in geo_cols if c in df.columns
                ]
            )

        return self._finalize_output(df)


# ---------------------------------------------------------------------------
# SIH-RD: AIH Reduzida (Aprovada)
# ---------------------------------------------------------------------------

class SIHRDProcessor(BaseProcessor):
    system_name = "SIH-RD"
    numeric_cols = ("Frequencia", "Valor Total")
    EXPORT_RENAME = {
        "TIPO": "Situacao da AIH",
        "ANO_CMPT": "Ano processamento",
        "MES_CMPT": "Mes processamento (Numero)",
        "NM_MES_CMPT": "Mes processamento",
        "DT_CMPT": "Mes/Ano processamento",
        "ANOMES_CMPT": "Ano/Mes processamento",
        "PROC_REA": "CO Procedimentos realizados",
        "NO_PROCEDIMENTO": "Procedimentos realizados",
        "NO_GRUPO": "Grupo de Procedimentos",
        "NO_SUB_GRUPO": "SubGrupo de Procedimentos",
        "NO_FORMA_ORGANIZACAO": "Forma de organizacao",
        "COMPLEXIDADE": "Complexidade do Procedimento",
        "NO_FINANCIAMENTO": "Financiamento",
        "NO_SUB_FINANCIAMENTO": "SubTp FAEC",
        "NO_CID": "Nome da Mobirdade (CID)",
        "VAL_TOT": "Valor Total",
        "QTD_AIH": "Frequencia",
        "N_AIH": "Sequencial",
        "nome_municipio": "Municipio de Residencia",
        "nome_regiao": "Regiao Residencia",
        "nome_estado": "Estado de Residencia",
        "nome_mesorregiao": "Mesorregiao IBGE de Resid.",
        "nome_microrregiao": "Microrregiao IBGE de Resid.",
        "NO_ESTABELECIMENTO": "Hospital (CNES)",
        "MUNIC_MOV": "Cod do Municipio do Estabelecimento",
        "municipio_estabelecimento": "Municipio do Estabelecimento",
        "nome_regiao_estabelecimento": "Regiao do Estabelecimento",
    }

    def process(
        self,
        raw: pl.DataFrame,
        ctx: ProcessorContext,
        *,
        county_id: Optional[list[str]] = None,
        health_establishment_id: Optional[list[str]] = None,
    ) -> pl.DataFrame:
        if raw.is_empty():
            return pl.DataFrame()
        cid_df = ctx.cid_enhanced()
        proc_df = ctx.procedure_details.df
        df = _check_filter(raw, county_id, "MUNIC_MOV")
        df = _check_filter(df, health_establishment_id, "CNES")

        municipios_estab = ctx.counties.df.select(
            pl.col("id_municipio"),
            pl.col("nome_municipio").alias("municipio_estabelecimento"),
            pl.col("nome_regiao").alias("nome_regiao_estabelecimento"),
        )

        df = df.with_columns(
            pl.lit("Aprovada").alias("TIPO"),
            pl.concat_str(
                [pl.col("ANO_CMPT").cast(pl.Utf8), pl.col("MES_CMPT").cast(pl.Utf8).str.zfill(2)],
            ).alias("ANOMES_CMPT"),
        )
        df = df.with_columns(
            pl.col("ANOMES_CMPT").cast(pl.Utf8).map_elements(lambda s: ymd(s) if s else None, return_dtype=pl.Date).alias("DT_CMPT"),
            pl.lit(1).alias("QTD_AIH"),
        ).with_columns(
            pl.col("DT_CMPT").dt.month().map_elements(month_name_pt, return_dtype=pl.Utf8).alias("NM_MES_CMPT"),
        )

        counties_df = ctx.counties.df
        if "MUNIC_RES" in df.columns:
            df = df.join(counties_df, left_on="MUNIC_RES", right_on="id_municipio", how="left")
        if "MUNIC_MOV" in df.columns:
            df = df.join(municipios_estab, left_on="MUNIC_MOV", right_on="id_municipio", how="left")

        if not proc_df.is_empty() and "PROC_REA" in df.columns:
            df = df.join(
                proc_df, left_on=["PROC_REA", "ANOMES_CMPT"], right_on=["CO_PROCEDIMENTO", "file_version_id"], how="left"
            )
        if not cid_df.is_empty() and "DIAG_PRINC" in df.columns:
            df = df.join(
                cid_df, left_on=["DIAG_PRINC", "ANOMES_CMPT"], right_on=["CO_CID", "file_version_id"], how="left"
            )
        if not ctx.establishments.df.is_empty() and "CNES" in df.columns:
            df = df.with_columns(pl.col("CNES").cast(pl.Utf8, strict=False))
            df = df.join(ctx.establishments.df, on="CNES", how="left", coalesce=True)

        if "NO_CID" in df.columns:
            df = df.with_columns(pl.col("NO_CID").fill_null("0000-Nao informado"))
        if "MUNIC_RES" in df.columns:
            geo = ["nome_estado", "nome_microrregiao", "nome_mesorregiao", "nome_municipio", "nome_regiao"]
            df = df.with_columns(
                [
                    pl.when(pl.col("MUNIC_RES").cast(pl.Utf8) == "999999")
                    .then(pl.lit("Nao informado"))
                    .otherwise(pl.col(c).fill_null(""))
                    .alias(c)
                    for c in geo if c in df.columns
                ]
            )

        return self._finalize_output(df)


# ---------------------------------------------------------------------------
# SIH-RJ: AIH Rejeitada (mesma estrutura do RD, só TIPO="Rejeitada")
# ---------------------------------------------------------------------------

class SIHRJProcessor(SIHRDProcessor):
    system_name = "SIH-RJ"

    def process(self, raw: pl.DataFrame, ctx: ProcessorContext, *, county_id=None, health_establishment_id=None):
        if raw.is_empty():
            return pl.DataFrame()
        raw = raw.with_columns(pl.lit("Rejeitada").alias("__FORCE_TIPO__"))
        # executa o processamento normal do RD
        df = super().process(raw, ctx, county_id=county_id, health_establishment_id=health_establishment_id)
        # Força "Rejeitada" na coluna Situacao da AIH
        if "Situacao da AIH" in df.columns:
            df = df.with_columns(pl.lit("Rejeitada").alias("Situacao da AIH"))
        return df


# ---------------------------------------------------------------------------
# SIH-SP: Serviços Profissionais (2 joins de procedimento)
# ---------------------------------------------------------------------------

class SIHSPProcessor(BaseProcessor):
    system_name = "SIH-SP"
    numeric_cols = ("Quantidade Aprovada", "Valor Aprovado", "Quantidade de Ato", "Frequencia")
    EXPORT_RENAME = {
        "DT_INTER": "Data Internacao",
        "MESANO_INT": "Mes/Ano Internacao",
        "ANOMES_INT": "Ano/Mes Internacao",
        "ANO_INT": "Ano Internacao",
        "MES_INT": "Mes Internacao",
        "MESANO_MVM": "Mes/Ano Processamento",
        "ANOMES_MVM": "Ano/Mes Processamento",
        "SP_AA": "Ano Processamento",
        "SP_MM": "Mes Processamento (Numero)",
        "NM_MES_MVM": "Mes Processamento",
        "SP_QT_PROC": "Quantidade Aprovada",
        "SP_VALATO": "Valor Aprovado",
        "SP_QTD_ATO": "Quantidade de Ato",
        "SP_U_AIH": "Frequencia",
        "SP_NAIH": "No da AIH",
        "SP_PROCREA": "CO Procedimentos realizados",
        "proc_principal_NO_PROCEDIMENTO": "Procedimentos realizados",
        "proc_principal_NO_GRUPO": "Grupo de Procedimentos",
        "proc_principal_NO_SUB_GRUPO": "SubGrupo de Procedimentos",
        "proc_principal_NO_FORMA_ORGANIZACAO": "Forma de organizacao",
        "proc_principal_COMPLEXIDADE": "Complexidade do Procedimento",
        "proc_principal_NO_FINANCIAMENTO": "Financiamento",
        "proc_principal_NO_SUB_FINANCIAMENTO": "SubTp FAEC",
        "SP_ATOPROF": "CO Procedimentos Secundarios",
        "proc_sec_NO_PROCEDIMENTO": "Procedimentos Secundarios",
        "proc_sec_NO_GRUPO": "Grupo Proc. Secundario",
        "proc_sec_NO_SUB_GRUPO": "Sub-grupo Proc. Secundario",
        "proc_sec_NO_FORMA_ORGANIZACAO": "Forma de organizacao Secundaria",
        "proc_sec_COMPLEXIDADE": "Complexidade do ato profissional",
        "proc_sec_NO_FINANCIAMENTO": "Tipo de financiamento do ato profissional",
        "proc_sec_NO_SUB_FINANCIAMENTO": "Subtipo FAEC do ato profissional",
        "IN_TP_VAL": "Tipo de Valor",
        "NO_OCUPACAO": "Ocupacao",
        "NO_CID": "Nome da Mobirdade (CID)",
        "nome_municipio": "Municipio Residencia",
        "nome_regiao": "Regiao Residencia",
        "nome_microrregiao": "Microrregião IBGE de Residência",
        "nome_mesorregiao": "Mesorregião IBGE de Residência",
        "nome_estado": "Estado Residencia",
        "NO_ESTABELECIMENTO": "Estabelecimento CNES",
        "SP_M_HOSP": "Cod do Municipio do Estabelecimento",
        "municipio_estabelecimento": "Municipio do Estabelecimento",
        "nome_regiao_estabelecimento": "Regiao do Estabelecimento",
    }

    def process(
        self,
        raw: pl.DataFrame,
        ctx: ProcessorContext,
        *,
        county_id: Optional[list[str]] = None,
        health_establishment_id: Optional[list[str]] = None,
    ) -> pl.DataFrame:
        if raw.is_empty():
            return pl.DataFrame()
        cid_df = ctx.cid_enhanced()
        cbo_df = ctx.cbo_enhanced()
        proc_df = ctx.procedure_details.df

        df = raw.with_columns(pl.col("SP_CNES").cast(pl.Utf8).alias("CNES"))
        df = _check_filter(df, county_id, "SP_M_HOSP")
        df = _check_filter(df, health_establishment_id, "CNES")

        municipios_estab = ctx.counties.df.select(
            pl.col("id_municipio"),
            pl.col("nome_municipio").alias("municipio_estabelecimento"),
            pl.col("nome_regiao").alias("nome_regiao_estabelecimento"),
        )

        df = df.with_columns(
            pl.concat_str([pl.col("SP_AA"), pl.col("SP_MM").cast(pl.Utf8).str.zfill(2)]).alias("ANOMES_MVM"),
            pl.concat_str([pl.col("SP_MM").cast(pl.Utf8).str.zfill(2), pl.lit("-"), pl.col("SP_AA")]).alias("MESANO_MVM"),
        )
        df = df.with_columns(
            pl.col("ANOMES_MVM").cast(pl.Utf8).map_elements(lambda s: ymd(s) if s else None, return_dtype=pl.Date).alias("DT_MVM"),
        ).with_columns(
            pl.col("DT_MVM").dt.month().map_elements(month_name_pt, return_dtype=pl.Utf8).alias("NM_MES_MVM"),
        )

        if "SP_DTINTER" in df.columns:
            df = df.with_columns(
                pl.col("SP_DTINTER").cast(pl.Utf8).str.slice(0, 8).alias("_dtinter"),
            ).with_columns(
                pl.col("_dtinter").map_elements(
                    lambda s: (
                        date(int(s[:4]), int(s[4:6]), int(s[6:8])) if isinstance(s, str) and len(s) >= 8 else None
                    ),
                    return_dtype=pl.Date,
                ).alias("DT_INTER")
            ).drop("_dtinter")
            df = df.with_columns(
                pl.col("DT_INTER").dt.year().cast(pl.Utf8).alias("ANO_INT"),
                pl.col("DT_INTER").dt.month().cast(pl.Utf8).str.zfill(2).alias("MES_INT"),
            )
            df = df.with_columns(
                pl.concat_str([pl.col("ANO_INT"), pl.col("MES_INT")]).alias("ANOMES_INT"),
                pl.concat_str([pl.col("MES_INT"), pl.lit("-"), pl.col("ANO_INT")]).alias("MESANO_INT"),
            )

        # Geografia
        if "SP_M_PAC" in df.columns:
            df = df.join(ctx.counties.df, left_on="SP_M_PAC", right_on="id_municipio", how="left")
        if "SP_M_HOSP" in df.columns:
            df = df.join(municipios_estab, left_on="SP_M_HOSP", right_on="id_municipio", how="left")
        # CBO
        if not cbo_df.is_empty() and "SP_PF_CBO" in df.columns:
            df = df.join(
                cbo_df, left_on=["SP_PF_CBO", "ANOMES_MVM"], right_on=["CO_OCUPACAO", "file_version_id"], how="left"
            )
            df = df.with_columns(
                pl.concat_str(
                    [pl.col("SP_PF_CBO").cast(pl.Utf8), pl.lit("-"), pl.col("NO_OCUPACAO").fill_null("")]
                ).alias("NO_OCUPACAO")
            )
        # CNES
        if not ctx.establishments.df.is_empty() and "CNES" in df.columns:
            df = df.join(ctx.establishments.df, on="CNES", how="left", coalesce=True)
        # CID
        if not cid_df.is_empty() and "SP_CIDPRI" in df.columns:
            df = df.join(
                cid_df, left_on=["SP_CIDPRI", "ANOMES_MVM"], right_on=["CO_CID", "file_version_id"], how="left"
            )
        if "NO_CID" in df.columns:
            df = df.with_columns(pl.col("NO_CID").fill_null("0000-Nao informado"))

        if "IN_TP_VAL" in df.columns:
            df = df.with_columns(
                pl.when(pl.col("IN_TP_VAL").cast(pl.Int64, strict=False) == 1)
                .then(pl.lit("Servico Hospitalar"))
                .when(pl.col("IN_TP_VAL").cast(pl.Int64, strict=False) == 2)
                .then(pl.lit("Servico Profissional"))
                .otherwise(pl.lit(""))
                .alias("IN_TP_VAL")
            )

        # Join procedimento PRINCIPAL
        if not proc_df.is_empty() and "SP_PROCREA" in df.columns:
            df = df.join(
                proc_df.rename(
                    {
                        "NO_PROCEDIMENTO": "proc_principal_NO_PROCEDIMENTO",
                        "NO_GRUPO": "proc_principal_NO_GRUPO",
                        "NO_SUB_GRUPO": "proc_principal_NO_SUB_GRUPO",
                        "NO_FORMA_ORGANIZACAO": "proc_principal_NO_FORMA_ORGANIZACAO",
                        "COMPLEXIDADE": "proc_principal_COMPLEXIDADE",
                        "NO_FINANCIAMENTO": "proc_principal_NO_FINANCIAMENTO",
                        "NO_SUB_FINANCIAMENTO": "proc_principal_NO_SUB_FINANCIAMENTO",
                    }
                ),
                left_on=["SP_PROCREA", "ANOMES_MVM"],
                right_on=["CO_PROCEDIMENTO", "file_version_id"],
                how="left",
            )
        # Join procedimento SECUNDARIO
        if not proc_df.is_empty() and "SP_ATOPROF" in df.columns:
            df = df.join(
                proc_df.rename(
                    {
                        "NO_PROCEDIMENTO": "proc_sec_NO_PROCEDIMENTO",
                        "NO_GRUPO": "proc_sec_NO_GRUPO",
                        "NO_SUB_GRUPO": "proc_sec_NO_SUB_GRUPO",
                        "NO_FORMA_ORGANIZACAO": "proc_sec_NO_FORMA_ORGANIZACAO",
                        "COMPLEXIDADE": "proc_sec_COMPLEXIDADE",
                        "NO_FINANCIAMENTO": "proc_sec_NO_FINANCIAMENTO",
                        "NO_SUB_FINANCIAMENTO": "proc_sec_NO_SUB_FINANCIAMENTO",
                    }
                ),
                left_on=["SP_ATOPROF", "ANOMES_MVM"],
                right_on=["CO_PROCEDIMENTO", "file_version_id"],
                how="left",
            )

        return self._finalize_output(df)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

PROCESSOR_MAP: dict[str, type[BaseProcessor]] = {
    "SIA-PA": SIAPAProcessor,
    "SIH-RD": SIHRDProcessor,
    "SIH-RJ": SIHRJProcessor,
    "SIH-SP": SIHSPProcessor,
}


def get_processor(system: str) -> BaseProcessor:
    try:
        return PROCESSOR_MAP[system]()
    except KeyError as e:
        raise ValueError(f"Sistema invalido: {system}") from e


def export_view(df: pl.DataFrame, system: str) -> pl.DataFrame:
    """Projeção de exportação (CSV/XLSX) para o sistema informado.

    Aceita "SIH-RD" ou "SIH_RD". Se o sistema for desconhecido devolve o df intacto.
    """
    key = (system or "").replace("_", "-").upper()
    cls = PROCESSOR_MAP.get(key)
    return cls.export_view(df) if cls else df
