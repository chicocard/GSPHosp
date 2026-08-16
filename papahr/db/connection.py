"""Conexão singleton DuckDB para persistência Medallion do PaPAHR."""
from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Optional

import duckdb
import polars as pl

from papahr.auth import current_user, get_current_session, is_authenticated
from papahr.config import get_settings
from papahr.db.schema import ALL_SCHEMAS
from papahr.geo.reference import resolve_geo_enrichment
from papahr.logging_config import get_logger

log = get_logger(__name__)


class DuckDBConnection:
    """Singleton encapsulando a conexão DuckDB + schemas Bronze/Silver/Gold/Audit."""

    def __init__(self, path: Optional[Path] = None):
        settings = get_settings().ensure_dirs()
        self._path = Path(path) if path else settings.duckdb_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # Diretório de extensões em uma pasta GRAVÁVEL garantida (evita bloqueio sandbox
        # em $HOME/.duckdb quando não há permissão para extensões globais).
        self._ext_dir = self._path.parent / "duckdb_extensions"
        self._ext_dir.mkdir(parents=True, exist_ok=True)
        # `threads=4` paralelismo, `enable_progress_bar` em queries longas
        self._conn = duckdb.connect(
            database=str(self._path),
            read_only=False,
            config={
                "threads": 4,
                "preserve_insertion_order": False,
                "extension_directory": str(self._ext_dir),
            },
        )
        try:
            self._conn.execute(f"SET extension_directory = '{str(self._ext_dir)}';")
        except Exception:
            pass
        self._conn.execute("INSTALL 'json';")
        self._conn.execute("LOAD 'json';")
        self._conn.execute("INSTALL 'parquet';")
        self._conn.execute("LOAD 'parquet';")
        self._conn.execute("SET enable_progress_bar = false;")
        # Cria todos os schemas/tabelas na 1ª inicialização
        # OBS: DuckDB.execute aceita múltiplas instruções em um só (diferente de SQLite-only `executescript`)
        with self._lock:
            self._conn.execute(ALL_SCHEMAS)
            self._conn.commit()

    @property
    def path(self) -> Path:
        return self._path

    # ---------------------- raw ----------------------
    def sql(self, query: str, params: Optional[Iterable[Any]] = None) -> duckdb.DuckDBPyConnection:
        with self._lock:
            return self._conn.execute(query, list(params) if params is not None else [])

    def df(self, query: str, params: Optional[Iterable[Any]] = None) -> pl.DataFrame:
        with self._lock:
            res = self._conn.execute(query, list(params) if params is not None else [])
            cols = [c[0] for c in res.description]
            rows = res.fetchall()
            return pl.DataFrame(rows, schema=cols, orient="row") if rows else pl.DataFrame(schema=cols)

    # ---------------------- audit ----------------------
    def audit_log(
        self,
        action: str,
        component: str,
        status: str = "OK",
        duration_ms: Optional[int] = None,
        details: Optional[dict] = None,
        extra_text: Optional[str] = None,
        user: Optional[Any] = None,
    ) -> str:
        """Grava um evento de auditoria. Thread-safe. Retorna audit_id gerado."""
        u = user if user is not None else (current_user() if is_authenticated() else None)
        sess = get_current_session()
        audit_id = uuid.uuid4().hex
        payload = (
            audit_id,
            u.user_id if u else None,
            u.username if u else None,
            sess.session_id,
            str(action).upper()[:64],
            str(component).upper()[:64],
            str(status).upper()[:16],
            int(duration_ms) if duration_ms is not None else None,
            "127.0.0.1",
            "GSPHosp-Desktop/v2.0",
            json.dumps(details, ensure_ascii=False) if details else None,
            extra_text,
        )
        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT INTO audit.audit_log (
                        audit_id, ts_utc, user_id, username, session_id,
                        action, component, status, duration_ms, ip_address,
                        user_agent, details_json, extra_text
                    ) VALUES (?, now(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    payload,
                )
                self._conn.commit()
        except Exception as exc:  # pragma: no cover - auditoria nunca deve quebrar a UX
            log.warning("Audit log FAIL: %s", exc, exc_info=True)
        return audit_id

    @contextmanager
    def audit_span(self, action: str, component: str, details: Optional[dict] = None):
        """Context manager: mede duração e grava OK/FAIL automaticamente."""
        t0 = time.perf_counter()
        try:
            yield self
            self.audit_log(
                action=action,
                component=component,
                status="OK",
                duration_ms=int(1000 * (time.perf_counter() - t0)),
                details=details,
            )
        except Exception as exc:
            self.audit_log(
                action=action,
                component=component,
                status="FAIL",
                duration_ms=int(1000 * (time.perf_counter() - t0)),
                details=details,
                extra_text=f"{type(exc).__name__}: {exc}",
            )
            raise

    # ---------------------- load runs bronze ----------------------
    def start_load_run(
        self,
        information_system: str,
        source_type: str,
        source_description: Optional[str] = None,
        details: Optional[dict] = None,
        load_run_id: Optional[str] = None,
        states: Optional[list[str]] = None,
    ) -> str:
        run_id = load_run_id or uuid.uuid4().hex
        if details is None:
            details = {}
        if states:
            details["states"] = list(states)
        u = current_user() if is_authenticated() else None
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO bronze.load_runs (
                    load_run_id, ts_utc, user_id, username,
                    information_system, source_type, source_description, details_json
                ) VALUES (?, now(), ?, ?, ?, ?, ?, ?::JSON)
                """,
                (
                    run_id,
                    u.user_id if u else None,
                    u.username if u else None,
                    str(information_system).upper()[:8],
                    str(source_type).upper()[:16],
                    source_description,
                    json.dumps(details, ensure_ascii=False) if details else None,
                ),
            )
            self._conn.commit()
        self.audit_log("ETL_START", "PIPELINE", details={
            "load_run_id": run_id,
            "information_system": information_system,
            "source_type": source_type,
            **(details or {}),
        })
        return run_id

    def finish_load_run(
        self,
        run_id: str,
        n_files: int,
        n_rows: int,
        status: str = "OK",
        error: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE bronze.load_runs
                   SET n_files = ?, n_rows = ?, status = ?,
                       error_message = ?, duration_ms = ?
                 WHERE load_run_id = ?
                """,
                (int(n_files), int(n_rows), str(status).upper()[:16], error, duration_ms, run_id),
            )
            self._conn.commit()
        self.audit_log("ETL_DONE" if status.upper() == "OK" else "ETL_FAIL", "PIPELINE",
                       status=status.upper() if status.upper() in {"OK", "FAIL", "PARTIAL"} else "FAIL",
                       duration_ms=duration_ms,
                       details={
                           "load_run_id": run_id,
                           "n_files": n_files,
                           "n_rows": n_rows,
                           "error": error,
                       })

    def persist_result_to_duckdb(
        self,
        *,
        system: str,
        df: "pl.DataFrame | None",
        ufs: list[str],
        output_parquet: Path | None = None,
        load_run_id: str | None = None,
        n_files: int = 0,
        n_rows: int = 0,
    ) -> str:
        """Persiste o resultado de um pipeline no DuckDB Medallion (Bronze→Silver→Gold).

        NÃO TRAVA NUNCA: se qualquer etapa (silver/gold) falhar, a bronze é gravada
        e o erro é salvo em load_runs.error_message e audit_log. Retorna o load_run_id
        (cria um novo se não foi fornecido).
        """
        import time
        import uuid
        import json
        from pathlib import Path

        t0 = time.time()
        rid = load_run_id or uuid.uuid4().hex

        # 1. -------------------------------------------------------------------- START LOAD_RUN
        if load_run_id is None:
            self.start_load_run(
                information_system=system,
                source_type="LOCAL" if output_parquet else "FTP",
                source_description=f"Persistência Medallion {system} • UFs: {', '.join(ufs)}",
                load_run_id=rid,
                states=ufs,
            )

        status_etl: str = "OK"
        error_etl: str | None = None
        bronze_rows: int = 0
        silver_rows: int = 0
        gold_rows: int = 0

        try:
            # ========================================================================
            # BRONZE LAYER — polars DataFrame (100% ORIGINAL, sem transformações)
            # ========================================================================
            if df is not None and len(df) > 0:
                bronze_rows = len(df)
                bronze_tbl = f"bronze.bronze_{system.replace('-', '_')}_{rid[:8]}"
                bronze_tbl_safe = bronze_tbl.replace(".", "_")  # paranoia
                try:
                    with self._lock:
                        # Polars → DuckDB (zero-copy nativa!)
                        self._conn.register("tmp_polars_bronze", df)
                        self._conn.execute(
                            f"CREATE OR REPLACE TABLE {bronze_tbl} AS SELECT * FROM tmp_polars_bronze"
                        )
                        try:
                            self._conn.unregister("tmp_polars_bronze")
                        except Exception:
                            pass

                        # Link reverso em load_runs.bronze_table
                        self._conn.execute(
                            "UPDATE bronze.load_runs SET bronze_table = ?, rows_bronze = ? WHERE load_run_id = ?",
                            (bronze_tbl, bronze_rows, rid),
                        )
                        self._conn.commit()
                except Exception as e:
                    # Não bloqueia, registra erro
                    with self._lock:
                        self._conn.execute(
                            "UPDATE bronze.load_runs SET error_message = ? WHERE load_run_id = ?",
                            (f"BRONZE_FAIL: {type(e).__name__}: {str(e)[:400]}", rid),
                        )
                        self._conn.commit()
                    self.audit_log("BRONZE_FAIL", "ETL", status="PARTIAL",
                                   details={"load_run_id": rid, "error": str(e)[:300]})

                # ====================================================================
                # SILVER LAYER — mapeamento heurístico colunas SUS → silver.producao
                # (todos os campos NOT NULL são preenchidos programaticamente para
                #  cumprir o schema rígido existente)
                # ====================================================================
                try:
                    cols_lower = {c.lower(): c for c in df.columns}

                    def pick(names_try: list[str]) -> str | None:
                        for nm in names_try:
                            if nm.lower() in cols_lower:
                                return cols_lower[nm.lower()]
                        return None

                    col_uf = pick(["SIGLA_UF", "UF", "SG_UF", "CO_UF", "uf_ibge", "uf", "PA_UFMUN", "SP_UFMUN", "UF_GESTOR"])
                    col_cnes = pick(["CNES_EXEC", "CODUH_EXEC", "CNES", "CO_CNES", "cnes", "NU_CNES",
                                     "PA_CODUNI", "SP_CNES", "COD_HOSPITAL"])
                    col_mun = pick(["CO_MUNICIP", "CO_MUNICIPIO", "MUNICIPIO_IBGE", "CO_MUNICIP", "munic_ibge",
                                    "PA_UFMUN", "MUNIC_MOV", "SP_M_HOSP", "SP_MUNIC_MOV"])
                    col_proc = pick(["PROC_REA", "CO_PROCEDIMENTO", "CD_PROCEDIMENTO", "COD_PROC_REA", "PROCEDIMENTO",
                                     "PA_PROC_ID", "SP_PROC_ID", "SP_PA_PROC"])
                    col_comp = pick(["COMPETEN", "ANO_MES", "AA_MM", "DT_INTER", "dt_entrada", "DATA",
                                     "PA_CMP", "ANOMES_CMPT", "DT_CMP", "ANO_CMPT", "COMPETENCIA"])
                    col_qt = pick(["QT_PROC", "QT_APRES", "QUANTIDADE", "QT_PROCEDIMENTOS", "qtde",
                                   "PA_QTDPRO", "SP_QT_PROC", "QT_CONS", "QT_SP"])
                    col_vapres = pick(["VAL_APRES", "VLR_APRES", "VALOR_APRESENTADO", "VLR_APRESENTACAO", "VAL_TOTAL",
                                       "PA_VALAPR", "SP_VL_APRES", "Valor Apresentado"])
                    col_vglosa = pick(["VAL_GLOSA", "VLR_GLOSA", "VALOR_GLOSADO", "GLOSA",
                                       "PA_VL_GLOSA", "SP_VL_GLOSA", "SP_GLOSA_TOT"])
                    col_vpago = pick(["VAL_PAGO", "VLR_PAGO", "VALOR_PAGO", "VAL_APROVADO", "VLR_APROVADO",
                                      "Valor Aprovado", "PA_VALPAG", "SP_VL_APROV"])
                    col_causagl = pick(["CB_COD", "COD_MOT_GLOSA", "MOTIVO_GLOSA", "CO_CAUSA_GLOSA",
                                        "SP_COD_GLOSA"])
                    col_sexo = pick(["SEXO", "TP_SEXO", "sexo", "SP_TP_SEXO"])
                    col_idade = pick(["IDADE", "NU_IDADE", "idade", "SP_NU_IDADE", "IDADE_ANOS"])
                    col_morte = pick(["MORTE", "DIARIA_ACOMPANH", "DESFECHO", "SP_DESFECHO", "FL_MORTE"])
                    col_nomehosp = pick(["NOME_HOSPITAL", "NOME_ESTAB", "NO_FANTASIA", "NM_FANTASIA_HOSP", "HOSPITAL",
                                         "NO_HOSPITAL", "SP_NO_HOSPITAL"])

                    with self._lock:
                        # Normaliza CNES / MUN / UF ANTES de registrar VIEW (largura OFICIAL):
                        # CNES=7 dígitos, MUN=7 dígitos IBGE, UF=2 letras maiúsculas.
                        # (Correção CNES 7 dígitos: evita "0123456" != "123456" em PK)
                        norm_cols: list[str] = list(df.columns)
                        norm_exprs: dict[str, pl.Expr] = {}
                        def _find_col(try_names: list[str]) -> str | None:
                            lowmap = {c.lower(): c for c in norm_cols}
                            for nm in try_names:
                                if nm.lower() in lowmap: return lowmap[nm.lower()]
                            return None
                        _c_col = _find_col(["CNES_EXEC","CODUH_EXEC","CNES","CO_CNES","cnes","NU_CNES",
                                            "PA_CODUNI","SP_CNES","COD_HOSPITAL"])
                        if _c_col:
                            norm_exprs[_c_col] = (
                                pl.col(_c_col).cast(pl.Utf8, strict=False).str.strip_chars().str.zfill(7)
                            )
                        _m_col = _find_col(["CO_MUNICIP","CO_MUNICIPIO","MUNICIPIO_IBGE","munic_ibge",
                                            "PA_UFMUN","MUNIC_MOV","SP_M_HOSP","SP_MUNIC_MOV"])
                        if _m_col:
                            norm_exprs[_m_col] = (
                                pl.col(_m_col).cast(pl.Utf8, strict=False).str.strip_chars().str.zfill(7)
                            )
                        _u_col = _find_col(["SIGLA_UF","UF","SG_UF","uf_gestora","uf"])
                        if _u_col:
                            norm_exprs[_u_col] = (
                                pl.col(_u_col).cast(pl.Utf8, strict=False).str.strip_chars().str.to_uppercase()
                            )
                        df_view = df.with_columns(norm_exprs) if norm_exprs else df
                        # Registra temporariamente o df como VIEW
                        self._conn.register("tmp_silver_src", df_view)

                        # Monta SELECT com mapeamento + CAST seguro
                        def q(col_df: str | None) -> str:
                            return f'"{col_df}"' if col_df else "NULL"

                        def tc(col_df: str | None, typ: str) -> str:
                            if col_df is None:
                                return f"NULL::{typ}"
                            return f"TRY_CAST({q(col_df)} AS {typ})"

                        # competencia_ano_mes (YYYYMM) → split em aaaa/mm NOT NULL
                        comp_yyyy_expr = (
                            f"CASE WHEN TRY_CAST({q(col_comp)} AS INTEGER) BETWEEN 199001 AND 209912 "
                            f"THEN TRY_CAST({q(col_comp)} AS INTEGER) // 100 "
                            f"WHEN REGEXP_MATCHES(TRY_CAST({q(col_comp)} AS VARCHAR), '^[0-9]{{6}}$') "
                            f"THEN TRY_CAST(LEFT(TRY_CAST({q(col_comp)} AS VARCHAR), 4) AS INTEGER) "
                            f"ELSE 0 END"
                        )
                        comp_mm_expr = (
                            f"CASE WHEN TRY_CAST({q(col_comp)} AS INTEGER) BETWEEN 199001 AND 209912 "
                            f"THEN TRY_CAST({q(col_comp)} AS INTEGER) % 100 "
                            f"WHEN REGEXP_MATCHES(TRY_CAST({q(col_comp)} AS VARCHAR), '^[0-9]{{6}}$') "
                            f"THEN TRY_CAST(RIGHT(TRY_CAST({q(col_comp)} AS VARCHAR), 2) AS INTEGER) "
                            f"ELSE 0 END"
                        )

                        # Campos NOT NULL:
                        #   id_producao: UUID por linha (gerada randomicamente se não existir)
                        #   origem_sistema: system (já temos)
                        #   origem_arquivo: bronze_table (se não soubermos o arquivo)
                        #   competencia_aaaa/mm: split acima
                        insert_sql = f"""
                        INSERT OR IGNORE INTO silver.producao (
                            id_producao, load_run_id, origem_sistema, origem_arquivo,
                            uf, municipio_cod, cnes, no_fantasia,
                            competencia_aaaa, competencia_mm, dt_internacao,
                            procedimento, sexo, idade_anos,
                            vl_total_apresentado, vl_total_aprovado, vl_glosa_total,
                            qt_glosas, codigo_glosa_1, flg_glosado,
                            created_at
                        )
                        SELECT
                            HEX(SHA256(CAST(random() AS VARCHAR)
                                || '-' || COALESCE({q(col_cnes)}, '')
                                || '-' || COALESCE({q(col_proc)}, '')
                                || '-' || COALESCE({q(col_comp)}, '')
                                || '-' || CAST(row_number() OVER () AS VARCHAR)))::VARCHAR(64)  AS id_producao,
                            '{rid}'::VARCHAR(64)                                                     AS load_run_id,
                            '{system}'::VARCHAR(8)                                                   AS origem_sistema,
                            COALESCE(NULLIF('{bronze_tbl}',''), 'bronze_{rid[:8]}')::VARCHAR(255)   AS origem_arquivo,
                            {tc(col_uf,'VARCHAR(2)')}                                                AS uf,
                            {tc(col_mun,'VARCHAR(7)')}                                               AS municipio_cod,
                            {tc(col_cnes,'VARCHAR(7)')}                                              AS cnes,
                            {tc(col_nomehosp,'VARCHAR(120)')}                                        AS no_fantasia,
                            CAST({comp_yyyy_expr} AS INTEGER)                                        AS competencia_aaaa,
                            CAST({comp_mm_expr}   AS INTEGER)                                        AS competencia_mm,
                            {tc(col_comp,'DATE')}                                                    AS dt_internacao,
                            {tc(col_proc,'VARCHAR(10)')}                                             AS procedimento,
                            {tc(col_sexo,'VARCHAR(1)')}                                              AS sexo,
                            {tc(col_idade,'INTEGER')}                                                AS idade_anos,
                            {tc(col_vapres,'DECIMAL(15,2)')}                                         AS vl_total_apresentado,
                            {tc(col_vpago, 'DECIMAL(15,2)')}                                         AS vl_total_aprovado,
                            COALESCE({tc(col_vglosa,'DECIMAL(15,2)')}, 0::DECIMAL(15,2))             AS vl_glosa_total,
                            CASE WHEN COALESCE({tc(col_vglosa,'DECIMAL(15,2)')}, 0) > 0
                                 THEN 1 ELSE 0 END                                                    AS qt_glosas,
                            {tc(col_causagl,'VARCHAR(8)')}                                           AS codigo_glosa_1,
                            CASE WHEN COALESCE({tc(col_vglosa,'DECIMAL(15,2)')}, 0) > 0
                                 THEN TRUE ELSE FALSE END                                             AS flg_glosado,
                            now()::TIMESTAMPTZ                                                        AS created_at
                        FROM tmp_silver_src
                        """

                        self._conn.execute(insert_sql)

                        silver_rows = int(self._conn.execute(
                            "SELECT COUNT(*) FROM silver.producao WHERE load_run_id = ?",
                            (rid,),
                        ).fetchone()[0])

                        # ------------------------------------------------ dim_hospitais (SCD1 por CNES)
                        if col_cnes:
                            cnes_distinct_sql = f"""
                                SELECT DISTINCT
                                    {tc(col_cnes,'VARCHAR(7)')}      AS cnes,
                                    {tc(col_nomehosp,'VARCHAR(120)')} AS no_fantasia,
                                    {tc(col_uf,'VARCHAR(2)')}        AS uf,
                                    {tc(col_mun,'VARCHAR(7)')}       AS municipio_cod
                                FROM tmp_silver_src
                                WHERE {tc(col_cnes,'VARCHAR(7)')} IS NOT NULL
                            """
                            tmp_rows = self._conn.execute(cnes_distinct_sql).fetchall()
                            enriched_rows = []
                            for (cnes_v, nome_v, uf_v, mun_v) in tmp_rows:
                                # CNES 7 dígitos OFICIAL (zfill) — evita conflitos PK
                                cnes_s = (str(cnes_v).strip().zfill(7) if cnes_v is not None else "")
                                if not cnes_s or len(cnes_s.replace("0","")) == 0:
                                    continue
                                mun_s = (str(mun_v).strip().zfill(7) if mun_v is not None else None)
                                uf_s  = (str(uf_v).strip().upper()  if uf_v  is not None else None)
                                (uf_f, mun_f,
                                 co_mac, no_mac,
                                 co_drs, no_drs) = resolve_geo_enrichment(
                                    uf_sigla=uf_s,
                                    co_municipio_6dig=mun_s,
                                )
                                enriched_rows.append((
                                    cnes_s,
                                    (str(nome_v).strip() if nome_v is not None else None),
                                    uf_f or None,
                                    mun_f or None,
                                    co_mac or None,
                                    no_mac or None,
                                    co_drs or None,
                                    no_drs or None,
                                ))
                            if enriched_rows:
                                tmp_df = pl.DataFrame(
                                    enriched_rows,
                                    schema=[
                                        "cnes", "no_fantasia", "uf", "municipio_cod",
                                        "co_macrorregiao", "no_macrorregiao",
                                        "co_regiao_saude", "no_regiao_saude",
                                    ],
                                )
                                self._conn.register("tmp_dimh_enriched", tmp_df)
                                try:
                                    self._conn.execute(
                                        """
                                        INSERT INTO silver.dim_hospitais
                                            (cnes, no_fantasia, uf, municipio_cod,
                                             co_macrorregiao, no_macrorregiao,
                                             co_regiao_saude, no_regiao_saude, updated_at)
                                        SELECT DISTINCT ON (cnes)
                                            cnes, no_fantasia, uf, municipio_cod,
                                            co_macrorregiao, no_macrorregiao,
                                            co_regiao_saude, no_regiao_saude, now()
                                        FROM tmp_dimh_enriched
                                        WHERE cnes IS NOT NULL
                                        ON CONFLICT (cnes) DO UPDATE SET
                                            no_fantasia     = COALESCE(EXCLUDED.no_fantasia,     silver.dim_hospitais.no_fantasia),
                                            uf              = COALESCE(EXCLUDED.uf,              silver.dim_hospitais.uf),
                                            municipio_cod   = COALESCE(EXCLUDED.municipio_cod,   silver.dim_hospitais.municipio_cod),
                                            co_macrorregiao = COALESCE(EXCLUDED.co_macrorregiao, silver.dim_hospitais.co_macrorregiao),
                                            no_macrorregiao = COALESCE(EXCLUDED.no_macrorregiao, silver.dim_hospitais.no_macrorregiao),
                                            co_regiao_saude = COALESCE(EXCLUDED.co_regiao_saude, silver.dim_hospitais.co_regiao_saude),
                                            no_regiao_saude = COALESCE(EXCLUDED.no_regiao_saude, silver.dim_hospitais.no_regiao_saude),
                                            updated_at      = now()
                                        """
                                    )
                                finally:
                                    try:
                                        self._conn.unregister("tmp_dimh_enriched")
                                    except Exception:
                                        pass

                        try:
                            self._conn.unregister("tmp_silver_src")
                        except Exception:
                            pass

                        self._conn.execute(
                            "UPDATE bronze.load_runs SET rows_silver = ? WHERE load_run_id = ?",
                            (silver_rows, rid),
                        )
                        self._conn.commit()
                except Exception as e:
                    status_etl = "PARTIAL"
                    error_etl = f"SILVER_FAIL: {type(e).__name__}: {str(e)[:400]}"
                    with self._lock:
                        try:
                            self._conn.unregister("tmp_silver_src")
                        except Exception:
                            pass
                        self._conn.execute(
                            "UPDATE bronze.load_runs SET error_message = ?, status='PARTIAL' WHERE load_run_id = ?",
                            (error_etl, rid),
                        )
                        self._conn.commit()
                    self.audit_log("SILVER_FAIL", "ETL", status="PARTIAL",
                                   details={"load_run_id": rid, "error": str(e)[:300]})

                # ====================================================================
                # GOLD LAYER — recria os agregados do schema GOLD baseados em silver.producao
                # (são as mesmas tabelas que o Dashboard já consulta)
                # ====================================================================
                try:
                    with self._lock:
                        # --- gold.aggr_mensal_hospital (KPI mensal x hospital x competencia)
                        self._conn.execute("DROP TABLE IF EXISTS gold.aggr_mensal_hospital")
                        self._conn.execute(
                            """
                            CREATE TABLE gold.aggr_mensal_hospital AS
                            SELECT
                                competencia_aaaa,
                                competencia_mm,
                                p.uf,
                                h.co_regiao_saude,
                                h.co_macrorregiao,
                                p.cnes,
                                COUNT(*)                                                                 AS n_aihs_apacs,
                                0                                                                        AS n_icsap,
                                0.0                                                                      AS pct_icsap,
                                CAST(NULL AS DECIMAL(8,2))                                               AS los_medio_dias,
                                CAST(COALESCE(SUM(p.vl_total_aprovado),    0) AS DECIMAL(18,2))          AS vl_total_aprovado,
                                CAST(COALESCE(AVG(NULLIF(p.vl_total_aprovado,0)), 0) AS DECIMAL(18,2))  AS vl_custo_medio,
                                CAST(SUM(CASE WHEN p.flg_glosado THEN 1 ELSE 0 END) AS BIGINT)           AS n_glosadas,
                                CAST(COALESCE(SUM(p.vl_glosa_total),          0) AS DECIMAL(18,2))       AS vl_glosado_total,
                                CASE WHEN SUM(COALESCE(p.vl_total_apresentado, 0)) > 0
                                     THEN CAST(ROUND( SUM(COALESCE(p.vl_glosa_total,0))
                                                    / SUM(COALESCE(p.vl_total_apresentado,0))*100,2) AS DECIMAL(5,2))
                                     ELSE 0.0 END                                                         AS pct_glosada_media,
                                now()::TIMESTAMPTZ                                                        AS updated_at
                            FROM silver.producao p
                            LEFT JOIN silver.dim_hospitais h ON p.cnes = h.cnes
                            WHERE p.cnes IS NOT NULL AND p.competencia_aaaa > 1990
                            GROUP BY competencia_aaaa, competencia_mm, p.uf,
                                     h.co_regiao_saude, h.co_macrorregiao, p.cnes
                            """
                        )
                        gold_rows += int(self._conn.execute(
                            "SELECT COUNT(*) FROM gold.aggr_mensal_hospital"
                        ).fetchone()[0])

                        # --- gold.aggr_glosas_causa_mensal
                        self._conn.execute("DROP TABLE IF EXISTS gold.aggr_glosas_causa_mensal")
                        self._conn.execute(
                            """
                            CREATE TABLE gold.aggr_glosas_causa_mensal AS
                            SELECT
                                p.competencia_aaaa,
                                p.competencia_mm,
                                p.uf,
                                p.codigo_glosa_1                                                AS causa_glosa_id,
                                COUNT(*)                                                        AS n_ocorrencias,
                                CAST(COALESCE(SUM(p.vl_glosa_total),0) AS DECIMAL(18,2))       AS valor_glosa,
                                CAST(COALESCE(SUM(p.vl_total_apresentado),0) AS DECIMAL(18,2)) AS valor_apresentado,
                                now()::TIMESTAMPTZ                                               AS updated_at
                            FROM silver.producao p
                            WHERE p.competencia_aaaa > 1990
                            GROUP BY 1, 2, 3, 4
                            """
                        )
                        gold_rows += int(self._conn.execute(
                            "SELECT COUNT(*) FROM gold.aggr_glosas_causa_mensal"
                        ).fetchone()[0])

                        self._conn.execute(
                            "UPDATE bronze.load_runs SET rows_gold = ? WHERE load_run_id = ?",
                            (gold_rows, rid),
                        )
                        self._conn.commit()
                except Exception as e:
                    with self._lock:
                        self._conn.execute(
                            "UPDATE bronze.load_runs SET error_message = COALESCE(error_message, '') || ' | GOLD_FAIL: ' || ? WHERE load_run_id = ?",
                            (f"{type(e).__name__}: {str(e)[:300]}", rid),
                        )
                        self._conn.commit()
                    self.audit_log("GOLD_FAIL", "ETL", status="PARTIAL",
                                   details={"load_run_id": rid, "error": str(e)[:300]})

        except Exception as e:
            status_etl = "FAIL"
            error_etl = f"ETL_FAIL: {type(e).__name__}: {str(e)[:400]}"
            self.audit_log("ETL_FAIL", "ETL", status="FAIL",
                           details={"load_run_id": rid, "error": str(e)[:300]})

        # ------------------------------------------------------------------------- FINISH
        elapsed_ms = int((time.time() - t0) * 1000.0)
        self.finish_load_run(
            run_id=rid,
            n_files=n_files,
            n_rows=n_rows or bronze_rows,
            status=status_etl,
            error=error_etl,
            duration_ms=elapsed_ms,
        )
        return rid

    # ------------------------------------------------------------------
    #  Helpers: DuckDB -> Polars (consumo das abas sem pipeline rodado)
    # ------------------------------------------------------------------
    def sql_to_polars(self, query: str, params: Iterable[Any] | None = None) -> pl.DataFrame:
        """Executa uma query SQL no DuckDB e retorna como Polars DataFrame.

        Auditoria a query em audit_log com usuário logado.
        """
        t0 = time.time()
        try:
            with self._lock:
                if params:
                    rel = self._conn.execute(query, list(params))
                else:
                    rel = self._conn.execute(query)
                try:
                    df = rel.pl()
                except AttributeError:
                    rows = rel.fetchall()
                    cols = [c[0] for c in (rel.description or [])]
                    df = pl.DataFrame(rows if rows else [], schema=cols if cols else None,
                                      orient="row")
            dur = int((time.time() - t0) * 1000.0)
            self.audit_log("DB_QUERY", "DASH", status="OK", duration_ms=dur,
                           details={"query": (query[:240] + "…") if len(query) > 240 else query,
                                    "rows": df.height})
            return df
        except Exception as e:
            dur = int((time.time() - t0) * 1000.0)
            self.audit_log("DB_QUERY_FAIL", "DASH", status="FAIL", duration_ms=dur,
                           details={"query": (query[:240] + "…") if len(query) > 240 else query,
                                    "error": f"{type(e).__name__}: {str(e)[:200]}"})
            raise

    def load_silver_production(
        self,
        *,
        ufs: list[str] | None = None,
        cnes_codes: list[str] | None = None,
        macro_regions: list[str] | None = None,
        health_regions: list[str] | None = None,
        year_start: int | None = None,
        month_start: int | None = None,
        year_end: int | None = None,
        month_end: int | None = None,
        limit: int | None = None,
        include_aliases: bool = True,
    ) -> pl.DataFrame:
        """Carrega `silver.producao` em Polars, com aliases compatíveis com pipeline.

        Filtros opcionais: UF, CNES, Macrorregião, DRS, faixa competência.
        Aliases (se include_aliases=True) renomeiam colunas p/ nomes esperados
        por compute_dashboard (UF, CNES_EXEC, NOME_HOSPITAL, PROC_REA, COMPETEN, etc.).
        """
        base_cols = [
            "id_producao", "load_run_id", "origem_sistema", "origem_arquivo",
            "uf", "municipio_cod", "municipio_nome", "cnes",
            "no_fantasia", "no_razao_social", "uf_gestora",
            "co_macrorregiao", "no_macrorregiao", "co_regiao_saude", "no_regiao_saude",
            "competencia_aaaa", "competencia_mm",
            "dt_internacao", "dt_alta", "dias_permanencia",
            "cid_principal", "cid_secundaria", "procedimento", "no_procedimento",
            "especialidade", "sexo", "idade_anos", "raca_cor",
            "vl_total_apresentado", "vl_total_aprovado", "vl_glosa_total",
            "qt_glosas", "codigo_glosa_1", "codigo_glosa_2", "codigo_glosa_3",
            "codigo_glosa_4", "codigo_glosa_5", "flg_glosado", "created_at",
        ]

        where: list[str] = []
        params: list[Any] = []

        def _int_comp(y: int, m: int) -> int:
            return int(y) * 100 + int(m)

        if ufs:
            where.append("uf IN (" + ",".join(["?"] * len(ufs)) + ")")
            params.extend([str(u).upper() for u in ufs])
        if cnes_codes:
            where.append("cnes IN (" + ",".join(["?"] * len(cnes_codes)) + ")")
            params.extend([str(c) for c in cnes_codes])
        if macro_regions:
            where.append("co_macrorregiao IN (" + ",".join(["?"] * len(macro_regions)) + ")")
            params.extend([str(m) for m in macro_regions])
        if health_regions:
            where.append("co_regiao_saude IN (" + ",".join(["?"] * len(health_regions)) + ")")
            params.extend([str(d) for d in health_regions])
        if year_start is not None and month_start is not None:
            where.append("competencia_aaaa * 100 + competencia_mm >= ?")
            params.append(_int_comp(year_start, month_start))
        if year_end is not None and month_end is not None:
            where.append("competencia_aaaa * 100 + competencia_mm <= ?")
            params.append(_int_comp(year_end, month_end))

        select_clause = ", ".join(base_cols)
        if include_aliases:
            select_clause += """,
                uf                                       AS "UF",
                cnes                                     AS "CNES_EXEC",
                no_fantasia                              AS "NOME_HOSPITAL",
                no_razao_social                          AS "RAZAO_SOCIAL",
                municipio_cod                            AS "COD_MUNICIPIO_IBGE",
                municipio_nome                           AS "MUNICIPIO",
                co_regiao_saude                          AS "CO_DRS",
                no_regiao_saude                          AS "NOME_DRS",
                co_macrorregiao                          AS "CO_MACRO_SAUDE",
                no_macrorregiao                          AS "NOME_MACRO_SAUDE",
                LPAD(CAST(competencia_aaaa AS VARCHAR), 4, '0')
                    || LPAD(CAST(competencia_mm AS VARCHAR), 2, '0') AS "COMPETEN",
                CAST(competencia_aaaa AS INTEGER)        AS "ANO",
                CAST(competencia_mm   AS INTEGER)        AS "MES",
                dt_internacao                            AS "DT_INTER",
                dt_alta                                  AS "DT_ALTA",
                dias_permanencia                         AS "QTD_DIARIAS",
                cid_principal                            AS "CID10",
                procedimento                             AS "PROC_REA",
                no_procedimento                          AS "NOME_PROCEDIMENTO",
                especialidade                            AS "ESPECIALIDADE",
                sexo                                     AS "SEXO",
                idade_anos                               AS "IDADE",
                vl_total_apresentado                     AS "VAL_APRES",
                vl_total_aprovado                        AS "VAL_PAGO",
                vl_glosa_total                           AS "VAL_GLOSA"
            """

        sql = f"SELECT {select_clause} FROM silver.producao"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY competencia_aaaa DESC, competencia_mm DESC, cnes"
        if limit is not None and limit > 0:
            sql += f" LIMIT {int(limit)}"

        return self.sql_to_polars(sql, params if params else None)

    def list_load_runs(self, limit: int = 50) -> pl.DataFrame:
        """Últimas N cargas ETL do bronze.load_runs (para UI selecionar carga)."""
        return self.sql_to_polars(
            """SELECT load_run_id, information_system, source_type, source_description,
                      status, n_files, rows_bronze, rows_silver, rows_gold,
                      duration_ms, error_message, ts_utc, details_json
               FROM bronze.load_runs
               ORDER BY ts_utc DESC
               LIMIT ?""",
            [int(limit)],
        )

    def has_persisted_data(self) -> bool:
        """True se existem ao menos 1 linha no silver.producao (há dados carregados)."""
        try:
            with self._lock:
                n = int(self._conn.execute(
                    "SELECT COUNT(*) FROM (SELECT 1 FROM silver.producao LIMIT 1) t"
                ).fetchone()[0])
            return bool(n)
        except Exception:
            return False

    def close(self) -> None:
        try:
            with self._lock:
                self._conn.close()
        except Exception:
            pass


_DB_LOCK = threading.RLock()
_DB: Optional[DuckDBConnection] = None


def get_db() -> DuckDBConnection:
    """Singleton thread-safe da conexão DuckDB."""
    global _DB
    if _DB is None:
        with _DB_LOCK:
            if _DB is None:
                _DB = DuckDBConnection()
    return _DB
