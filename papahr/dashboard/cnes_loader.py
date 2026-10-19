"""Carregador da base CNES local (dicionário de dados do usuário).

Fontes:
    C:\\DICIONARIO_DADOS\\BASE_DE_DADOS_CNES_202512\\*.csv
    C:\\DICIONARIO_DADOS\\*.csv (fallback)

Objetivo: prover a camada de geolocalização (lat/lon), capacidade instalada
(leitos por tipo) e características do estabelecimento para o Dashboard de
Saúde Populacional (coeficiente de atração / modelo Huff / ICSAP / CMI / LACE /
transição de cuidado).
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

def is_hospital_unit(tipo_code_or_str: str, name_str: str) -> bool:
    """Retorna True estritamente para HOSPITAIS, MATERNIDADES, PRONTO-SOCORROS e UNIDADES MISTAS.

    Filtra fora farmácias, academias de saúde, UBS, postos de saúde, consultórios e laboratórios.
    """
    t = str(tipo_code_or_str or "").strip().upper()
    n = str(name_str or "").strip().upper()

    # Se nome contiver explicitamente termos de não-hospital -> rejeita
    non_hosp_words = ("FARMACIA", "FARMA ", "FARMA_", "ACADEMIA DE SAUDE", "ACADEMIA DA SAUDE",
                      "POSTO DE SAUDE", "UNIDADE DE SAUDE DA FAMILIA", "U S F ", "USF ",
                      "CONSULTORIO", "LABORATORIO", "DROGARIA", "ENDEMIAS", "APAE", "CAPS ")
    if any(nh in n for nh in non_hosp_words):
        return False

    # Códigos numéricos oficiais de hospitais no CNES DATASUS (CO_TIPO_ESTABELECIMENTO):
    # 05 = Hospital Geral, 07 = Hospital Especializado, 06 = Pronto Socorro Geral,
    # 15 = Unidade Mista, 20 = Pronto Socorro Especializado, 04 = Policlínica c/ internação
    hosp_codes = {"05", "5", "07", "7", "06", "6", "15", "20", "04", "4"}
    if t in hosp_codes:
        return True

    # Termos no tipo de estabelecimento
    hosp_type_keywords = ("HOSPITAL", "HOSP", "PRONTO SOCORRO", "UNIDADE MISTA", "EMERGENCIA", "MATERNIDADE", "CASA DE SAUDE")
    if any(k in t for k in hosp_type_keywords):
        return True

    # Termos no nome do estabelecimento
    name_keywords = ("HOSPITAL", "HOSP ", " HOSP", "MATERNIDADE", "PRONTO SOCORRO", "SANTA CASA", "UNIDADE MISTA", "CASA DE SAUDE", "UPA 24H", "PRONTO ATENDIMENTO", "PRONTO-SOCORRO")
    if any(k in n for k in name_keywords):
        return True

    return False

from functools import lru_cache
from pathlib import Path
from typing import Iterable

import polars as pl

from papahr.logging_config import get_logger

log = get_logger(__name__)

_SEARCH_ROOTS_BASE: tuple[Path, ...] = (
    Path(r"C:\DICIONARIO_DADOS"),
)

_BASE_DE_DADOS_CNES_GLOB = "BASE_DE_DADOS_CNES_*"


def _auto_discover_cnes_folders() -> tuple[Path, ...]:
    """Encontra automaticamente pastas 'BASE_DE_DADOS_CNES_YYYYMM' (YYYYMM decrescente).

    Tambem procura:
      - C:\\DICIONARIO_DADOS\\BASE_DE_DADOS_CNES_*
      - %USERPROFILE%\\Documentos\\DICIONARIO_DADOS\\*
      - %USERPROFILE%\\Desktop\\DICIONARIO_DADOS\\*
      - %USERPROFILE%\\Downloads\\DICIONARIO_DADOS\\*
      - %USERPROFILE%\\*
    Retorna pastas existentes ordenadas (mais recente YYYYMM PRIMEIRO).
    """
    candidates_roots: list[Path] = [Path(r"C:\DICIONARIO_DADOS")]
    try:
        home = Path.home()
        for sub in ("Documentos", "Documents", "Desktop", "Downloads", "Area de Trabalho"):
            p = home / sub
            if p.exists():
                candidates_roots.append(p)
                candidates_roots.append(p / "DICIONARIO_DADOS")
        candidates_roots.append(home)
    except Exception:  # noqa: BLE001
        pass

    discovered: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for root in candidates_roots:
        if not root.exists():
            continue
        try:
            for p in root.iterdir():
                if not p.is_dir():
                    continue
                abs_s = str(p.resolve())
                if abs_s in seen:
                    continue
                if p.name.startswith("BASE_DE_DADOS_CNES_") or (
                    p.name.upper().startswith("BASE DE DADOS CNES")
                ):
                    yyyymm = p.name[-6:] if p.name[-6:].isdigit() else "000000"
                    discovered.append((yyyymm, p))
                    seen.add(abs_s)
        except Exception:  # noqa: PERF203
            continue

    discovered.sort(key=lambda t: t[0], reverse=True)
    return tuple(p for _, p in discovered)


def _get_search_roots() -> tuple[Path, ...]:
    r"""Retorna raízes de busca (prioridade alta → baixa):

    1) Settings.custom_cnes_dir (Wizard pasta compactada ou picker usuario)
    2) Pastas auto-descobertas 'BASE_DE_DADOS_CNES_YYYYMM' (mais recente primeiro)
    3) C:\DICIONARIO_DADOS e %USERPROFILE%\*
    """
    result: list[Path] = []
    try:
        from papahr.config import get_settings
        s = get_settings()
        if s.custom_cnes_dir is not None:
            custom = Path(s.custom_cnes_dir)
            if custom.exists():
                result.append(custom)
                try:
                    for child in custom.iterdir():
                        if child.is_dir():
                            result.append(child)
                except Exception:  # noqa: PERF203
                    pass
    except Exception:  # noqa: BLE001
        pass

    auto = _auto_discover_cnes_folders()
    if auto:
        result.extend(auto)
        log.info("[CNES] Auto-descobri %d pasta(s) do CNES. Mais recente: %s",
                 len(auto), str(auto[0]))

    try:
        home = Path.home()
        for extra in (
            home / "Documentos" / "DICIONARIO_DADOS",
            home / "Documents" / "DICIONARIO_DADOS",
            home / "Desktop" / "DICIONARIO_DADOS",
            home / "Downloads" / "DICIONARIO_DADOS",
            home / "Area de Trabalho" / "DICIONARIO_DADOS",
        ):
            if extra.exists():
                result.append(extra)
    except Exception:  # noqa: BLE001
        pass

    for base in _SEARCH_ROOTS_BASE:
        if base.exists():
            result.append(base)

    if not result:
        log.warning(
            "[CNES] Nenhuma pasta do CNES encontrada! "
            "Espera-se C:\\DICIONARIO_DADOS\\BASE_DE_DADOS_CNES_YYYYMM\\*.csv "
            "ou use Wizard Passo 1 > 'Pasta compactada' para apontar o arquivo baixado."
        )
    return tuple(result)

_FILENAME_PATTERNS: dict[str, re.Pattern[str]] = {
    "estabelecimento": re.compile(r"tbEstabelecimento\d+"),
    "municipio": re.compile(r"tbMunicipio\d+"),
    "leito": re.compile(r"tbLeito\d+"),
    "tipo_estabelecimento": re.compile(r"tbTipoEstabelecimento\d+"),
    "estado": re.compile(r"tbEstado\d+"),
    "rl_estab_endcompl": re.compile(r"rlEstabEndCompl\d+"),
    "natureza_juridica": re.compile(r"tbNaturezaJuridica\d+"),
}


def _find_csv(key: str) -> Path | None:
    """Busca por CSV que bate com o pattern nas raízes de busca (inclui custom_cnes_dir)."""
    pat = _FILENAME_PATTERNS[key]
    for root in _get_search_roots():
        if not root.exists():
            continue
        for p in root.rglob("*.csv"):
            if pat.fullmatch(p.stem):
                return p
    return None


def _detect_csv_params(path: Path) -> dict[str, object]:
    """Autodetecta (separator, encoding, quote_char) para arquivos CSV do CNES/DATASUS.

    Motivo: CSVs do CNES variam muito por origem:
      - Separador: ';' (padrao DATASUS Windows export), ',' (Unix export), tab, pipe.
      - Encoding: UTF-8 com BOM / UTF-8 lossy, latin-1 (cp1252 é compatível).
      - Quote: '"' (padrão) ou sem aspas.
    Faz 12 tentativas e escolhe a configuração que retorna MAIS COLUNAS.
    """
    candidates_sep = [";", ",", "\t", "|"]
    candidates_enc = ["utf8-lossy", "latin-1", "utf8", "windows-1252"]
    best_cols = 0
    best: dict[str, object] | None = None
    for sep in candidates_sep:
        for enc in candidates_enc:
            try:
                df = pl.read_csv(
                    path,
                    n_rows=3,
                    separator=sep,
                    encoding=enc,
                    quote_char='"',
                    infer_schema=False,
                    low_memory=True,
                    ignore_errors=True,
                    truncate_ragged_lines=True,
                    try_parse_dates=False,
                )
                ncols = len(df.columns)
                tem_co_cnes = "CO_CNES" in df.columns
                params = dict(
                    separator=sep,
                    encoding=enc,
                    quote_char='"',
                    infer_schema=False,
                    low_memory=True,
                    ignore_errors=True,
                    truncate_ragged_lines=True,
                    try_parse_dates=False,
                    infer_schema_length=5000,
                )
                # Vitoria instantanea: CO_CNES presente e >= 20 colunas.
                if tem_co_cnes and ncols >= 20:
                    return params
                # Caso contrario guarda SEMPRE a combinacao com MAIS colunas
                # (empate: prefere a que parseou CO_CNES). NUNCA sobrescreve
                # a melhor com uma de menos colunas.
                if ncols > best_cols or (tem_co_cnes and ncols == best_cols):
                    best_cols = ncols
                    best = params
            except Exception:
                continue
    if best is None:
        # Fallback conservador (CSV DATASUS 99% das vezes)
        best = dict(
            separator=";", encoding="utf8-lossy", quote_char='"',
            infer_schema=False, low_memory=True, ignore_errors=True,
            truncate_ragged_lines=True, try_parse_dates=False, infer_schema_length=5000,
        )
    return best


def _read_cnes_csv(path: Path, **overrides) -> pl.DataFrame:
    """Leitor CNES com AUTO-DETECAO de separador/encoding.

    Importante: nao ha um separador universal entre os CSVs do dicionario do
    CNES exportados. O arquivo tbEstabelecimento*.csv costuma ser ';' + quoted.
    """
    auto = _detect_csv_params(path)
    auto.update(overrides)
    try:
        return pl.read_csv(path, **auto)
    except Exception as exc_read:  # noqa: BLE001
        log.warning("[CNES] Tentativa auto falhou (%s). Usando fallback leitura binaria.", exc_read)
        # Ultimo recurso: le como texto puro e parseia manualmente os params
        try:
            raw = Path(path).read_bytes()[:64000]
            has_semi = raw.count(b";")
            has_comma = raw.count(b",")
            sep = ";" if has_semi >= has_comma else ","
            return pl.read_csv(
                path,
                separator=sep, encoding="utf8-lossy", quote_char='"',
                infer_schema=False, low_memory=True, ignore_errors=True,
                truncate_ragged_lines=True, try_parse_dates=False,
                infer_schema_length=5000, **overrides,
            )
        except Exception as exc2:
            raise RuntimeError(
                f"Falhou ao ler {path.name} apos autodetect e fallback. {exc2}"
            ) from exc2


@dataclass(slots=True)
class CNESData:
    @property
    def hospitais(self) -> pl.DataFrame:
        if self.estabelecimentos.is_empty():
            return self.estabelecimentos
        # Filtra estabelecimentos que são hospitais
        df = self.estabelecimentos
        name_col = "NO_FANTASIA" if "NO_FANTASIA" in df.columns else ("NO_RAZAO_SOCIAL" if "NO_RAZAO_SOCIAL" in df.columns else None)
        type_col = "CO_TIPO_ESTABELECIMENTO" if "CO_TIPO_ESTABELECIMENTO" in df.columns else ("DS_TIPO_ESTABELECIMENTO" if "DS_TIPO_ESTABELECIMENTO" in df.columns else None)
        if type_col or name_col:
            types = df[type_col].cast(pl.Utf8).fill_null(pl.lit("")).to_list() if type_col else [""] * df.height
            names = df[name_col].cast(pl.Utf8).fill_null(pl.lit("")).to_list() if name_col else [""] * df.height
            mask = [is_hospital_unit(t, n) for t, n in zip(types, names)]
            return df.filter(pl.Series(mask))
        return df

    """Dados CNES carregados (lazy: cacheados em memória após 1ª carga)."""

    estabelecimentos: pl.DataFrame = field(default_factory=pl.DataFrame)
    municipios: pl.DataFrame = field(default_factory=pl.DataFrame)
    leitos: pl.DataFrame = field(default_factory=pl.DataFrame)
    tipo_estabelecimento: pl.DataFrame = field(default_factory=pl.DataFrame)
    estados: pl.DataFrame = field(default_factory=pl.DataFrame)
    natureza_juridica: pl.DataFrame = field(default_factory=pl.DataFrame)
    fonts_used: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ------------------------------------------------------------------
    @property
    def is_loaded(self) -> bool:
        return not self.estabelecimentos.is_empty()

    def load(self) -> "CNESData":
        """Popula dataframes (idempotente, thread-safe)."""
        with self._lock:
            if self.is_loaded:
                return self
            for key in ("estabelecimento", "municipio", "leito",
                        "tipo_estabelecimento", "estado", "natureza_juridica"):
                p = _find_csv(key)
                if p is None:
                    log.warning(f"[CNES] Nao encontrei csv para '{key}'")
                    continue
                df = _read_cnes_csv(p)
                self.fonts_used[key] = str(p)
                attr = {"estabelecimento": "estabelecimentos",
                        "municipio": "municipios",
                        "leito": "leitos",
                        "tipo_estabelecimento": "tipo_estabelecimento",
                        "estado": "estados",
                        "natureza_juridica": "natureza_juridica"}[key]
                setattr(self, attr, df)
                log.info(f"[CNES] Carregado {key}: {df.shape} cols={df.columns[:8]}...")

            # Join leitos (dicionário) com base CNES? A tbLeito é pequena (69 linhas).
            # Normaliza colunas de geolocalização para float.
            self._normalize_dicts()  # leitos/tipo/estado/natureza → códigos p/ Utf8
            self._normalize_estabs()
            self._normalize_municipios()
            log.info(f"[CNES] Pronto. Fontes: {self.fonts_used}")
            return self

    # ------------------------------------------------------------------
    def _normalize_dicts(self) -> None:
        """Garante que TODOS os códigos-chave das tabelas dicionário sejam Utf8
        (evita SchemaError str vs i64 no join com estabelecimentos/prod)."""
        for attr, code_col in (
            ("leitos", "CO_LEITO"),
            ("tipo_estabelecimento", "CO_TIPO_ESTABELECIMENTO"),
            ("estados", "CO_UF"),
            ("natureza_juridica", "CO_NATUREZA_JUR"),
        ):
            df: pl.DataFrame = getattr(self, attr, pl.DataFrame())
            if df.is_empty() or code_col not in df.columns:
                continue
            casted = df.with_columns(
                pl.col(code_col).cast(pl.Utf8, strict=False).str.strip_chars()
            )
            setattr(self, attr, casted)

    def _normalize_estabs(self) -> None:
        if self.estabelecimentos.is_empty():
            return
        df = self.estabelecimentos
        if "NU_LATITUDE" in df.columns:
            df = df.with_columns(
                pl.col("NU_LATITUDE").cast(pl.Float64, strict=False).alias("NU_LATITUDE"),
                pl.col("NU_LONGITUDE").cast(pl.Float64, strict=False).alias("NU_LONGITUDE"),
            )
        cast_codes = [
            "CO_CNES", "CO_MUNICIPIO_GESTOR", "CO_TIPO_ESTABELECIMENTO",
            "CO_UF", "CO_NATUREZA_JUR",
        ]
        exprs = []
        for c in cast_codes:
            if c not in df.columns:
                continue
            e = pl.col(c).cast(pl.Utf8, strict=False).str.strip_chars()
            # Padroniza largura OFICIAL dos códigos SUS (correção CNES 7 dígitos):
            #   CO_CNES = 7 dígitos, CO_MUNICIPIO_GESTOR = 7 dígitos IBGE,
            #   CO_UF = 2 dígitos IBGE, CO_TIPO_ESTABELECIMENTO = 4 dígitos,
            #   CO_NATUREZA_JUR = 4 dígitos.
            if c == "CO_CNES":
                e = e.str.zfill(7)
            elif c == "CO_MUNICIPIO_GESTOR":
                e = e.str.zfill(7)
            elif c == "CO_UF":
                e = e.str.zfill(2)
            elif c == "CO_TIPO_ESTABELECIMENTO":
                e = e.str.zfill(4)
            elif c == "CO_NATUREZA_JUR":
                e = e.str.zfill(4)
            exprs.append(e)
        if exprs:
            df = df.with_columns(exprs)

        # CRITICAL: Garante coluna SIGLA_UF sempre preenchida, independentemente
        # de qual coluna estiver disponível (CO_ESTADO_GESTOR, CO_UF (cód IBGE 2dig),
        # ou colunas diversas. Sem ela, uf_hospital=""" fica vazia e a cascata cai.
        from papahr.geo.reference import UF_CODIBGE_TO_SIGLA
        expr_cod_to_sigla = UF_CODIBGE_TO_SIGLA
        def _vazio_vira_null(e: pl.Expr) -> pl.Expr:
            # CRITICAL: pl.coalesce considera "" como VALOR VALIDO (nao-nulo).
            # Sem esta conversao, uma coluna existente porem vazia "ganha" da
            # cascata e os fallbacks (ex: UF via municipio) nunca rodam.
            return pl.when(e.is_not_null() & (e != "")).then(e).otherwise(None)

        uf_exprs: list[pl.Expr] = []
        if "SIGLA_ESTADO_GESTOR" in df.columns:
            uf_exprs.append(_vazio_vira_null(
                pl.col("SIGLA_ESTADO_GESTOR").cast(pl.Utf8).str.strip_chars().str.to_uppercase()))
        if "SIGLA_UF_GESTOR" in df.columns:
            uf_exprs.append(_vazio_vira_null(
                pl.col("SIGLA_UF_GESTOR").cast(pl.Utf8).str.strip_chars().str.to_uppercase()))
        if "SG_UF" in df.columns:
            uf_exprs.append(_vazio_vira_null(
                pl.col("SG_UF").cast(pl.Utf8).str.strip_chars().str.to_uppercase()))
        if "UF" in df.columns:
            uf_exprs.append(_vazio_vira_null(
                pl.col("UF").cast(pl.Utf8).str.strip_chars().str.to_uppercase()))
        for col_cod in ("CO_ESTADO_GESTOR", "CO_UF_GESTOR", "CO_UF_IBGE"):
            if col_cod in df.columns:
                cod_raw = pl.col(col_cod).cast(pl.Utf8).str.strip_chars().str.zfill(2)
                # default=None → codigo nao mapeado vira null e deixa a cascata seguir
                mapped = cod_raw.replace(expr_cod_to_sigla, default=None)
                uf_exprs.append(_vazio_vira_null(mapped))
        if "CO_MUNICIPIO_GESTOR" in df.columns:
            cod_raw = pl.col("CO_MUNICIPIO_GESTOR").cast(pl.Utf8).str.strip_chars()
            dois_primeiros = cod_raw.str.slice(0, 2).str.zfill(2)
            mapped_do_mun = dois_primeiros.replace(expr_cod_to_sigla, default=None)
            uf_exprs.append(_vazio_vira_null(mapped_do_mun))
        if uf_exprs:
            if "SIGLA_UF" in df.columns:
                df = df.with_columns(
                    pl.when(pl.col("SIGLA_UF").is_not_null() & (pl.col("SIGLA_UF") != ""))
                      .then(pl.col("SIGLA_UF"))
                      .otherwise(pl.coalesce(uf_exprs + [pl.lit("")]))
                      .alias("SIGLA_UF")
                )
            else:
                df = df.with_columns(
                    pl.coalesce(uf_exprs + [pl.lit("")]).alias("SIGLA_UF")
                )
        elif "SIGLA_UF" not in df.columns:
            df = df.with_columns(pl.lit("").alias("SIGLA_UF"))

        if "NU_LEITOS_SUS" in df.columns:
            df = df.with_columns(pl.col("NU_LEITOS_SUS").cast(pl.Float64, strict=False).fill_null(0))
        self.estabelecimentos = df

    def _normalize_municipios(self) -> None:
        if self.municipios.is_empty():
            return
        df = self.municipios
        if "CO_MUNICIPIO" in df.columns:
            df = df.with_columns(
                pl.col("CO_MUNICIPIO").cast(pl.Utf8, strict=False).str.strip_chars()
            )
        if "NU_POPULACAO" in df.columns:
            df = df.with_columns(
                pl.col("NU_POPULACAO").cast(pl.Float64, strict=False).fill_null(0)
            )
        if "NU_DENSIDADE" in df.columns:
            df = df.with_columns(
                pl.col("NU_DENSIDADE").cast(pl.Float64, strict=False).fill_null(0)
            )
        df = self._derivar_centroides_municipais(df)
        self.municipios = df

    def _derivar_centroides_municipais(self, mun: pl.DataFrame) -> pl.DataFrame:
        """Adiciona NU_LATITUDE/NU_LONGITUDE ao tbMunicipio quando ele não as traz.

        O tbMunicipio do CNES não tem coordenadas, e sem elas o modelo de Huff e as
        curvas de decaimento por distância ficavam sempre vazios. O centroide é
        estimado pela MÉDIA das coordenadas dos estabelecimentos de saúde do próprio
        município (tbEstabelecimento), sem nenhuma fonte externa. Municípios sem
        estabelecimento georreferenciado ficam com null (e são ignorados pelo modelo).
        """
        if mun.is_empty() or "CO_MUNICIPIO" not in mun.columns:
            return mun
        if "NU_LATITUDE" in mun.columns and "NU_LONGITUDE" in mun.columns:
            return mun
        e = self.estabelecimentos
        need = {"CO_MUNICIPIO_GESTOR", "NU_LATITUDE", "NU_LONGITUDE"}
        if e.is_empty() or not need.issubset(set(e.columns)):
            return mun
        cod = pl.col("CO_MUNICIPIO_GESTOR").cast(pl.Utf8, strict=False).str.strip_chars()
        # chave de 6 dígitos: "0120020" (zfill) → "120020"; "1200203" (7 com DV) → "120020"
        key6 = (
            pl.when(cod.str.starts_with("0") & (cod.str.len_chars() == 7))
            .then(cod.str.slice(1, 6))
            .otherwise(cod.str.slice(0, 6))
        )
        cent = (
            e.select(
                key6.alias("_key6"),
                pl.col("NU_LATITUDE").cast(pl.Float64, strict=False).alias("_lat"),
                pl.col("NU_LONGITUDE").cast(pl.Float64, strict=False).alias("_lon"),
            )
            .filter(
                pl.col("_lat").is_not_null() & pl.col("_lon").is_not_null()
                & (pl.col("_lat").abs() <= 90) & (pl.col("_lon").abs() <= 180)
                & (pl.col("_lat") != 0) & (pl.col("_lon") != 0)
            )
            .group_by("_key6")
            .agg(pl.col("_lat").median().alias("NU_LATITUDE"),
                 pl.col("_lon").median().alias("NU_LONGITUDE"))
        )
        mcod = pl.col("CO_MUNICIPIO").cast(pl.Utf8, strict=False).str.strip_chars()
        mun = mun.with_columns(
            pl.when(mcod.str.starts_with("0") & (mcod.str.len_chars() == 7))
            .then(mcod.str.slice(1, 6)).otherwise(mcod.str.slice(0, 6)).alias("_key6")
        )
        out = mun.join(cent, on="_key6", how="left").drop("_key6")
        n_ok = out["NU_LATITUDE"].is_not_null().sum() if "NU_LATITUDE" in out.columns else 0
        log.info(f"[CNES] Centroides municipais derivados dos estabelecimentos: {n_ok}/{out.height}")
        return out

    # ------------------------------------------------------------------
    def buscar_estabelecimentos(self, cnes_codes: Iterable[str]) -> pl.DataFrame:
        """Retorna estabelecimentos + município para os códigos CNES dados."""
        if not self.is_loaded:
            self.load()
        # CNES 7 dígitos OFICIAL (zfill preserva zero à esquerda). Normaliza
        # input e CO_CNES para evitar join truncado.
        codes_norm = [
            str(c).strip().zfill(7)
            for c in cnes_codes if c is not None and str(c).strip()
        ]
        if not codes_norm or self.estabelecimentos.is_empty():
            return pl.DataFrame()
        e = self.estabelecimentos
        if "CO_CNES" in e.columns:
            e = e.with_columns(pl.col("CO_CNES").cast(pl.Utf8, strict=False).str.strip_chars().str.zfill(7))
        e = e.filter(pl.col("CO_CNES").is_in(codes_norm))
        if not self.municipios.is_empty() and "CO_MUNICIPIO_GESTOR" in e.columns:
            e = e.join(self.municipios, left_on="CO_MUNICIPIO_GESTOR",
                        right_on="CO_MUNICIPIO", how="left")
        if not self.tipo_estabelecimento.is_empty() and "CO_TIPO_ESTABELECIMENTO" in e.columns:
            e = e.join(self.tipo_estabelecimento, on="CO_TIPO_ESTABELECIMENTO", how="left")
        return e

    def estabelecimentos_por_municipio(self, cod_ibge: Iterable[str]) -> pl.DataFrame:
        if not self.is_loaded:
            self.load()
        cods = [str(c).strip() for c in cod_ibge if c is not None and str(c).strip()]
        if not cods:
            return pl.DataFrame()
        return self.estabelecimentos.filter(pl.col("CO_MUNICIPIO_GESTOR").is_in(cods))


@lru_cache(maxsize=1)
def get_cnes_data() -> CNESData:
    """Singleton de CNESData."""
    return CNESData()
