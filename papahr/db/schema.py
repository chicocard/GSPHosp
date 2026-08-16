"""Schemas SQL DuckDB para arquitetura Medallion (Bronze/Silver/Gold) + Auditoria."""
from __future__ import annotations

# -------------------------------------------------------------------
# AUDIT LOG (audit. tabela separada, schema audit)
# Registra TODAS as ações relevantes: login, logout, carga de dados,
# consulta, exportação, administração de usuários.
# -------------------------------------------------------------------
AUDIT_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS audit.audit_log (
    audit_id          VARCHAR(64)    PRIMARY KEY,
    ts_utc            TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id           VARCHAR(64),        -- se logado, NULL no setup
    username          VARCHAR(128),
    session_id        VARCHAR(128),
    action            VARCHAR(64)  NOT NULL,   -- LOGIN_OK, LOGIN_FAIL, LOGOUT,
                                               -- ETL_START, ETL_DONE, ETL_FAIL,
                                               -- DB_QUERY, EXPORT_CSV, EXPORT_XLSX,
                                               -- ADMIN_USER_CREATE, etc.
    component         VARCHAR(64)  NOT NULL,   -- AUTH, PIPELINE, DASHBOARD, ADMIN, WIZARD
    status            VARCHAR(16)  NOT NULL DEFAULT 'OK',  -- OK / FAIL / WARN
    duration_ms       BIGINT,
    ip_address        VARCHAR(64)  DEFAULT '127.0.0.1',
    user_agent        TEXT,
    details_json      JSON,              -- metadados (UFs selecionadas, qtd linhas, erro etc)
    extra_text        TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_ts       ON audit.audit_log (ts_utc DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user     ON audit.audit_log (user_id);
CREATE INDEX IF NOT EXISTS idx_audit_action   ON audit.audit_log (action);
CREATE INDEX IF NOT EXISTS idx_audit_component ON audit.audit_log (component);
CREATE INDEX IF NOT EXISTS idx_audit_status   ON audit.audit_log (status);
"""

# -------------------------------------------------------------------
# BRONZE (dados brutos 1:1 com arquivos DATASUS)
# -------------------------------------------------------------------
BRONZE_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS bronze;

CREATE TABLE IF NOT EXISTS bronze.load_runs (
    load_run_id        VARCHAR(64) PRIMARY KEY,
    ts_utc             TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id            VARCHAR(64),
    username           VARCHAR(128),
    information_system VARCHAR(8)   NOT NULL, -- SIA-PA, SIH-RD, SIH-RJ, SIH-SP
    source_type        VARCHAR(16)  NOT NULL, -- FTP_DATASUS / LOCAL_DBC / LOCAL_DBF / ARCHIVE_ZIP
    source_description TEXT,                   -- caminho da pasta, arquivo FTP etc.
    n_files            INTEGER DEFAULT 0,
    n_rows             BIGINT  DEFAULT 0,
    status             VARCHAR(16) NOT NULL DEFAULT 'RUNNING',  -- RUNNING, OK, FAIL, PARTIAL
    duration_ms        BIGINT,
    error_message      TEXT,
    details_json       JSON
);
CREATE INDEX IF NOT EXISTS idx_load_ts    ON bronze.load_runs (ts_utc DESC);
CREATE INDEX IF NOT EXISTS idx_load_sys   ON bronze.load_runs (information_system);
CREATE INDEX IF NOT EXISTS idx_load_stat  ON bronze.load_runs (status);

-- Colunas extra de monitoramento Medallion (adicionadas idempotentemente)
ALTER TABLE bronze.load_runs ADD COLUMN IF NOT EXISTS bronze_table   VARCHAR(255);
ALTER TABLE bronze.load_runs ADD COLUMN IF NOT EXISTS rows_bronze    BIGINT  DEFAULT 0;
ALTER TABLE bronze.load_runs ADD COLUMN IF NOT EXISTS rows_silver    BIGINT  DEFAULT 0;
ALTER TABLE bronze.load_runs ADD COLUMN IF NOT EXISTS rows_gold      BIGINT  DEFAULT 0;

-- SIH-RD: AIHs Aprovadas (arquivos RDufaaMM.dbc/dbf)
CREATE TABLE IF NOT EXISTS bronze.sih_rd (
    load_run_id VARCHAR(64) NOT NULL,
    source_file VARCHAR(255) NOT NULL,
    uf          VARCHAR(2) NOT NULL,
    ano         INTEGER  NOT NULL,
    mes         INTEGER  NOT NULL,
    raw_data    JSON     NOT NULL,          -- 1:1 colunas originais
    raw_bytes   BIGINT
);
CREATE INDEX IF NOT EXISTS idx_sih_rd_period ON bronze.sih_rd (ano, mes);
CREATE INDEX IF NOT EXISTS idx_sih_rd_load   ON bronze.sih_rd (load_run_id);

-- SIH-RJ: AIHs Rejeitadas (Glosas) — estrutura idêntica ao SIH-RD
CREATE TABLE IF NOT EXISTS bronze.sih_rj AS SELECT * FROM bronze.sih_rd WHERE 0 = 1;
CREATE INDEX IF NOT EXISTS idx_sih_rj_period ON bronze.sih_rj (ano, mes);
CREATE INDEX IF NOT EXISTS idx_sih_rj_load   ON bronze.sih_rj (load_run_id);

-- SIA-PA: APACs Ambulatoriais — estrutura idêntica ao SIH-RD
CREATE TABLE IF NOT EXISTS bronze.sia_pa AS SELECT * FROM bronze.sih_rd WHERE 0 = 1;
CREATE INDEX IF NOT EXISTS idx_sia_pa_period ON bronze.sia_pa (ano, mes);
CREATE INDEX IF NOT EXISTS idx_sia_pa_load   ON bronze.sia_pa (load_run_id);

-- SIH-SP: Serviços Profissionais AIH — estrutura idêntica ao SIH-RD
CREATE TABLE IF NOT EXISTS bronze.sih_sp AS SELECT * FROM bronze.sih_rd WHERE 0 = 1;
CREATE INDEX IF NOT EXISTS idx_sih_sp_period ON bronze.sih_sp (ano, mes);
CREATE INDEX IF NOT EXISTS idx_sih_sp_load   ON bronze.sih_sp (load_run_id);

-- CNES: Estabelecimentos de saúde
CREATE TABLE IF NOT EXISTS bronze.cnes_estabelecimentos (
    load_run_id   VARCHAR(64),
    source_file   VARCHAR(255),
    ano           INTEGER,
    mes           INTEGER,
    raw_data      JSON
);
CREATE INDEX IF NOT EXISTS idx_cnes_est_load ON bronze.cnes_estabelecimentos (load_run_id);

-- CNES: Municípios, Leitos, Tipos, Estados — mesma estrutura
CREATE TABLE IF NOT EXISTS bronze.cnes_municipios AS SELECT * FROM bronze.cnes_estabelecimentos WHERE 0 = 1;
CREATE TABLE IF NOT EXISTS bronze.cnes_leitos     AS SELECT * FROM bronze.cnes_estabelecimentos WHERE 0 = 1;
CREATE TABLE IF NOT EXISTS bronze.cnes_tipos      AS SELECT * FROM bronze.cnes_estabelecimentos WHERE 0 = 1;
CREATE TABLE IF NOT EXISTS bronze.cnes_estados    AS SELECT * FROM bronze.cnes_estabelecimentos WHERE 0 = 1;
"""

# -------------------------------------------------------------------
# SILVER (dados limpos, normalizados, deduplicados)
# -------------------------------------------------------------------
SILVER_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS silver;

-- Produção unificada (SIA + SIH): view materializada? não, tabela real para dash.
CREATE TABLE IF NOT EXISTS silver.producao (
    id_producao        VARCHAR(64) PRIMARY KEY,
    load_run_id        VARCHAR(64) NOT NULL,
    origem_sistema     VARCHAR(8)   NOT NULL, -- SIA-PA / SIH-RD / SIH-RJ / SIH-SP
    origem_arquivo     VARCHAR(255) NOT NULL,
    uf                 VARCHAR(2),
    municipio_cod      VARCHAR(7),            -- 7 dígitos IBGE
    municipio_nome     VARCHAR(120),
    cnes               VARCHAR(7),
    no_fantasia        VARCHAR(120),
    no_razao_social    VARCHAR(255),
    uf_gestora         VARCHAR(2),
    co_macrorregiao    VARCHAR(4),            -- MACRORREGIÃO DE SAÚDE (código 3+1 dígitos)
    no_macrorregiao    VARCHAR(120),
    co_regiao_saude    VARCHAR(6),            -- DRS / REGIÃO DE SAÚDE
    no_regiao_saude    VARCHAR(120),
    competencia_aaaa   INTEGER NOT NULL,
    competencia_mm     INTEGER NOT NULL,
    dt_internacao      DATE,
    dt_alta            DATE,
    dias_permanencia   INTEGER,
    cid_principal      VARCHAR(10),
    cid_secundaria     VARCHAR(10),
    procedimento       VARCHAR(10),           -- SIGTAP
    no_procedimento    VARCHAR(255),
    especialidade      VARCHAR(8),
    sexo               VARCHAR(1),
    idade_anos         INTEGER,
    raca_cor           VARCHAR(2),
    vl_total_apresentado DECIMAL(15, 2),
    vl_total_aprovado    DECIMAL(15, 2),
    vl_glosa_total       DECIMAL(15, 2) DEFAULT 0.0,
    qt_glosas            INTEGER      DEFAULT 0,
    codigo_glosa_1       VARCHAR(8),
    codigo_glosa_2       VARCHAR(8),
    codigo_glosa_3       VARCHAR(8),
    codigo_glosa_4       VARCHAR(8),
    codigo_glosa_5       VARCHAR(8),
    flg_glosado          BOOLEAN      DEFAULT FALSE,
    flg_icsap            BOOLEAN      DEFAULT FALSE,
    n_aih                VARCHAR(13),
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_producao_ukey ON silver.producao
    (origem_sistema, origem_arquivo, cnes, competencia_aaaa, competencia_mm, n_aih, procedimento);
CREATE INDEX IF NOT EXISTS idx_producao_periodo  ON silver.producao (competencia_aaaa, competencia_mm);
CREATE INDEX IF NOT EXISTS idx_producao_cnes     ON silver.producao (cnes);
CREATE INDEX IF NOT EXISTS idx_producao_geo      ON silver.producao (uf, co_regiao_saude, co_macrorregiao);
CREATE INDEX IF NOT EXISTS idx_producao_glosa    ON silver.producao (flg_glosado);
CREATE INDEX IF NOT EXISTS idx_producao_icsap    ON silver.producao (flg_icsap);
CREATE INDEX IF NOT EXISTS idx_producao_load     ON silver.producao (load_run_id);

-- Hospitais (SILVER: dimensão SCD tipo 1 normalizada para joins no Dashboard)
CREATE TABLE IF NOT EXISTS silver.dim_hospitais (
    cnes                 VARCHAR(7) PRIMARY KEY,
    no_fantasia          VARCHAR(120),
    no_razao_social      VARCHAR(255),
    uf                   VARCHAR(2),
    municipio_cod        VARCHAR(7),
    municipio_nome       VARCHAR(120),
    co_macrorregiao      VARCHAR(4),
    no_macrorregiao      VARCHAR(120),
    co_regiao_saude      VARCHAR(6),
    no_regiao_saude      VARCHAR(120),
    co_tipo_estab        VARCHAR(4),
    no_tipo_estab        VARCHAR(120),
    natureza_juridica    VARCHAR(10),
    no_natureza_jur      VARCHAR(120),
    leitos_sus           INTEGER DEFAULT 0,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dimh_uf_macro   ON silver.dim_hospitais (uf, co_macrorregiao);
CREATE INDEX IF NOT EXISTS idx_dimh_uf_regiao  ON silver.dim_hospitais (uf, co_regiao_saude);
CREATE INDEX IF NOT EXISTS idx_dimh_municipio  ON silver.dim_hospitais (municipio_cod);
"""

# -------------------------------------------------------------------
# GOLD (agregações pré-calculadas para Dashboard — carregamento instantâneo)
# -------------------------------------------------------------------
GOLD_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS gold;

-- Agregado MÊS × UF × CNES (KPIs do Dashboard por competência)
CREATE TABLE IF NOT EXISTS gold.aggr_mensal_hospital (
    competencia_aaaa      INTEGER NOT NULL,
    competencia_mm        INTEGER NOT NULL,
    uf                    VARCHAR(2),
    co_regiao_saude       VARCHAR(6),
    co_macrorregiao       VARCHAR(4),
    cnes                  VARCHAR(7),
    n_aihs_apacs          BIGINT NOT NULL DEFAULT 0,
    n_icsap               BIGINT NOT NULL DEFAULT 0,
    pct_icsap             DECIMAL(5, 2) DEFAULT 0,
    los_medio_dias        DECIMAL(8, 2),
    vl_total_aprovado     DECIMAL(18, 2) DEFAULT 0,
    vl_custo_medio        DECIMAL(18, 2) DEFAULT 0,
    n_glosadas            BIGINT  DEFAULT 0,
    pct_glosadas          DECIMAL(5, 2) DEFAULT 0,
    vl_glosado            DECIMAL(18, 2) DEFAULT 0,
    pct_vl_glosado        DECIMAL(5, 2) DEFAULT 0,
    n_readm30d            BIGINT  DEFAULT 0,
    pct_readm30d          DECIMAL(5, 2) DEFAULT 0,
    cmi                   DECIMAL(7, 3),
    PRIMARY KEY (competencia_aaaa, competencia_mm, cnes)
);
CREATE INDEX IF NOT EXISTS idx_aggr_geo    ON gold.aggr_mensal_hospital (uf, co_regiao_saude, co_macrorregiao);
CREATE INDEX IF NOT EXISTS idx_aggr_period ON gold.aggr_mensal_hospital (competencia_aaaa, competencia_mm);

-- Agregado por CAUSA DE GLOSA × MÊS (aba Glosas)
CREATE TABLE IF NOT EXISTS gold.aggr_glosas_causa_mensal (
    competencia_aaaa INTEGER NOT NULL,
    competencia_mm   INTEGER NOT NULL,
    codigo_glosa     VARCHAR(8) NOT NULL,
    no_causa_glosa   VARCHAR(255),
    n_eventos        BIGINT  DEFAULT 0,
    pct_eventos      DECIMAL(5, 2) DEFAULT 0,
    vl_glosado       DECIMAL(18, 2) DEFAULT 0,
    pct_vl_glosado   DECIMAL(5, 2) DEFAULT 0,
    PRIMARY KEY (competencia_aaaa, competencia_mm, codigo_glosa)
);

-- Cache última carga (para quick-start do dashboard sem recomputar tudo)
CREATE TABLE IF NOT EXISTS gold.last_pipeline_summary (
    ts_utc                 TIMESTAMPTZ PRIMARY KEY DEFAULT now(),
    load_run_id            VARCHAR(64),
    origens_sistema_json   JSON,
    n_total_registros      BIGINT,
    n_hospitais_unicos     BIGINT,
    periodos_json          JSON
);
"""

ALL_SCHEMAS = AUDIT_SCHEMA + "\n" + BRONZE_SCHEMA + "\n" + SILVER_SCHEMA + "\n" + GOLD_SCHEMA
