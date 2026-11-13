"""
populacao.py — população residente por município (IBGE), com cache e reserva.

Ordem de busca de `carregar_populacao()`:
  1. cache local  (GSPHOSP_HOME/cache/populacao_ibge_<ano>.parquet)
  2. API SIDRA do IBGE — tabela 6579 (estimativas anuais; último ano disponível)
     e, se o ano pedido for 2022, tabela 4714 (Censo 2022)
  3. reserva: coluna NU_POPULACAO do tbMunicipio do CNES (via papahr.dashboard.cnes_loader)

Saída (Polars): COD_MUN (6 dígitos, sem DV) | COD_MUN7 | NOME | UF | POP | ANO | FONTE

    from papahr.dashboard.populacao import carregar_populacao
    pop = carregar_populacao()            # último ano do IBGE (ou cache/CNES)
    pop = carregar_populacao(ano=2022)    # Censo 2022

Sem internet e sem cache, devolve o CNES (FONTE="CNES") ou um DataFrame vazio;
nunca levanta exceção para a interface — o chamador decide o que mostrar.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.request
from datetime import date
from pathlib import Path

import polars as pl

try:
    from papahr.logging_config import get_logger  # type: ignore
    log = get_logger(__name__)
except Exception:  # noqa: BLE001  (fora do app: logging padrão)
    import logging
    log = logging.getLogger(__name__)

SIDRA = "https://apisidra.ibge.gov.br/values"
# tabela 6579 = "População residente estimada" (variável 9324), por município (n6)
URL_ESTIMATIVA = SIDRA + "/t/6579/n6/all/v/9324/p/{periodo}?formato=json"
# tabela 4714 = Censo 2022, variável 93 = "População residente"
URL_CENSO_2022 = SIDRA + "/t/4714/n6/all/v/93/p/all?formato=json"
TIMEOUT_S = 60

SCHEMA = {"COD_MUN": pl.Utf8, "COD_MUN7": pl.Utf8, "NOME": pl.Utf8, "UF": pl.Utf8,
          "POP": pl.Float64, "ANO": pl.Int32, "FONTE": pl.Utf8}


# ---------------------------------------------------------------------------
# pastas
# ---------------------------------------------------------------------------

def pasta_cache() -> Path:
    base = os.environ.get("GSPHOSP_HOME")
    if base:
        p = Path(base) / "cache"
    else:
        p = Path.home() / ".gsphosp" / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _arquivo_cache(ano: int | str) -> Path:
    return pasta_cache() / f"populacao_ibge_{ano}.parquet"


# ---------------------------------------------------------------------------
# IBGE (SIDRA)
# ---------------------------------------------------------------------------

def _baixar_json(url: str, timeout: int = TIMEOUT_S) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "GSPHosp/1.0 (NESCON/UFMG)",
                                               "Accept": "application/json"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:  # noqa: S310
        dados = json.loads(r.read().decode("utf-8"))
    if not isinstance(dados, list) or len(dados) < 2:
        raise ValueError("resposta SIDRA inesperada (lista vazia)")
    return dados


def _sidra_para_df(dados: list[dict], fonte: str) -> pl.DataFrame:
    """Converte a resposta do SIDRA (1ª linha = cabeçalho) na tabela padrão."""
    cab = dados[0]
    # localiza as chaves pelo rótulo do cabeçalho (a ordem D1/D2/D3 depende da URL)
    k_mun = next((k for k, v in cab.items() if k.startswith("D") and k.endswith("C") and "Munic" in str(v)), "D1C")
    k_nome = k_mun[:-1] + "N"
    k_ano = next((k for k, v in cab.items() if k.startswith("D") and k.endswith("C") and "Ano" in str(v)), "D3C")
    rows = []
    for r in dados[1:]:
        cod7 = str(r.get(k_mun) or "").strip()
        v = str(r.get("V") or "").strip()
        if len(cod7) != 7 or not v or v in ("-", "...", "X", ".."):
            continue
        try:
            pop = float(v)
        except ValueError:
            continue
        nome = str(r.get(k_nome) or "")
        uf = nome[-3:-1] if nome.endswith(")") and len(nome) > 4 else ""
        nome = nome[:-5] if uf else nome
        try:
            ano = int(str(r.get(k_ano) or "0")[:4])
        except ValueError:
            ano = 0
        rows.append((cod7[:6], cod7, nome, uf, pop, ano, fonte))
    if not rows:
        raise ValueError("resposta SIDRA sem linhas válidas")
    return pl.DataFrame(rows, schema=list(SCHEMA.items()), orient="row")


def baixar_ibge(ano: int | None = None, timeout: int = TIMEOUT_S) -> pl.DataFrame:
    """Baixa do SIDRA. `ano=None` → última estimativa; `ano=2022` → Censo 2022."""
    if ano == 2022:
        dados = _baixar_json(URL_CENSO_2022, timeout)
        return _sidra_para_df(dados, "IBGE Censo 2022")
    periodo = "last%201" if ano is None else str(int(ano))
    dados = _baixar_json(URL_ESTIMATIVA.format(periodo=periodo), timeout)
    df = _sidra_para_df(dados, "IBGE estimativa")
    a = int(df["ANO"].max() or 0)
    return df.with_columns(pl.lit(f"IBGE estimativa {a}").alias("FONTE"))


# ---------------------------------------------------------------------------
# CNES (reserva)
# ---------------------------------------------------------------------------

def populacao_cnes() -> pl.DataFrame:
    """População do tbMunicipio do CNES (NU_POPULACAO), se o app tiver a base carregada."""
    try:
        from papahr.dashboard.cnes_loader import get_cnes_data  # type: ignore
        mun = get_cnes_data().load().municipios
    except Exception as exc:  # noqa: BLE001
        log.warning("população do CNES indisponível: %s", exc)
        return pl.DataFrame(schema=SCHEMA)
    if mun is None or mun.is_empty() or "CO_MUNICIPIO" not in mun.columns or "NU_POPULACAO" not in mun.columns:
        return pl.DataFrame(schema=SCHEMA)
    cod = pl.col("CO_MUNICIPIO").cast(pl.Utf8, strict=False).str.strip_chars()
    cod6 = pl.when(cod.str.starts_with("0") & (cod.str.len_chars() == 7)).then(cod.str.slice(1, 6)).otherwise(cod.str.slice(0, 6))
    nome = pl.col("NO_MUNICIPIO").cast(pl.Utf8) if "NO_MUNICIPIO" in mun.columns else pl.lit("")
    uf = pl.col("CO_SIGLA_ESTADO").cast(pl.Utf8) if "CO_SIGLA_ESTADO" in mun.columns else pl.lit("")
    return (
        mun.select(
            cod6.alias("COD_MUN"), pl.lit(None, dtype=pl.Utf8).alias("COD_MUN7"), nome.alias("NOME"), uf.alias("UF"),
            pl.col("NU_POPULACAO").cast(pl.Float64, strict=False).alias("POP"),
            pl.lit(None, dtype=pl.Int32).alias("ANO"), pl.lit("CNES").alias("FONTE"),
        )
        .filter(pl.col("POP").is_not_null() & (pl.col("POP") > 0) & (pl.col("COD_MUN").str.len_chars() == 6))
        .unique(subset=["COD_MUN"], keep="first")
    )


# ---------------------------------------------------------------------------
# API principal
# ---------------------------------------------------------------------------

def carregar_populacao(ano: int | None = None, *, online: bool = True, cache: bool = True,
                       timeout: int = TIMEOUT_S) -> pl.DataFrame:
    """Tabela COD_MUN | COD_MUN7 | NOME | UF | POP | ANO | FONTE (vazia se nada disponível)."""
    chave = "ultima" if ano is None else str(ano)
    arq = _arquivo_cache(chave)
    # 1. cache (a "última" vale por 180 dias; anos fixos valem para sempre)
    if cache and arq.exists():
        try:
            idade = (date.today() - date.fromtimestamp(arq.stat().st_mtime)).days
            if ano is not None or idade <= 180:
                df = pl.read_parquet(arq)
                if not df.is_empty():
                    return df
        except Exception as exc:  # noqa: BLE001
            log.warning("cache de população ilegível (%s): %s", arq, exc)
    # 2. IBGE
    if online:
        try:
            df = baixar_ibge(ano, timeout)
            if cache:
                try:
                    df.write_parquet(arq)
                except Exception as exc:  # noqa: BLE001
                    log.warning("não gravou cache de população: %s", exc)
            return df
        except Exception as exc:  # noqa: BLE001
            log.warning("IBGE/SIDRA indisponível (%s); usando reserva", exc)
    # 3. cache antigo, mesmo vencido
    if cache and arq.exists():
        try:
            df = pl.read_parquet(arq)
            if not df.is_empty():
                return df
        except Exception:  # noqa: BLE001
            pass
    # 4. CNES
    return populacao_cnes()


def descrever_fonte(pop: pl.DataFrame) -> str:
    """Texto curto para a interface: 'IBGE estimativa 2024 · 5.570 municípios'."""
    if pop is None or pop.is_empty():
        return "população indisponível"
    fonte = str(pop["FONTE"][0]) if "FONTE" in pop.columns else "?"
    return f"{fonte} · {pop.height:,} municípios".replace(",", ".")


def juntar_populacao(municipios: pl.DataFrame, pop: pl.DataFrame) -> pl.DataFrame:
    """Acrescenta/atualiza a coluna POP de uma tabela de municípios (chave COD_MUN 6 dígitos)."""
    if pop is None or pop.is_empty():
        return municipios if "POP" in municipios.columns else municipios.with_columns(pl.lit(None, dtype=pl.Float64).alias("POP"))
    p = pop.select(pl.col("COD_MUN").cast(pl.Utf8).str.slice(0, 6), pl.col("POP").alias("_pop_ibge"))
    out = municipios.with_columns(pl.col("COD_MUN").cast(pl.Utf8).str.slice(0, 6)).join(p, on="COD_MUN", how="left")
    if "POP" in out.columns:
        out = out.with_columns(pl.coalesce([pl.col("_pop_ibge"), pl.col("POP")]).alias("POP"))
    else:
        out = out.with_columns(pl.col("_pop_ibge").alias("POP"))
    return out.drop("_pop_ibge")
