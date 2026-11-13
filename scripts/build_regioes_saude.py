"""
build_regioes_saude.py — gera papahr/data/regioes_saude.parquet (município → região de saúde/CIR).

Fontes, em ordem de tentativa:
  1. DATASUS, base territorial: ftp.datasus.gov.br/territorio/tabelas/base_territorial.zip
     (tabelas rl_municip_regsaud.dbf e tb_regsaud.dbf; e, se existirem, rl_municip_macsaud.dbf
     e tb_macsaud.dbf para a macrorregião). Fonte oficial; usa o pacote `dbfread`, já instalado no app.
  2. Reserva: tabela consolidada pelo projeto covid19br (UFV, W. Cota), coluna cod_RegiaoDeSaude
     (= CO_REGSAUD do DATASUS, 5 dígitos: UF + sequencial), 5.570 municípios, 450 regiões:
     https://raw.githubusercontent.com/wcota/covid19br/master/cities_info.csv

Uso (na raiz do projeto):
    .\\.venv\\Scripts\\python.exe scripts\\build_regioes_saude.py            # tenta DATASUS, senão reserva
    .\\.venv\\Scripts\\python.exe scripts\\build_regioes_saude.py --reserva  # só a reserva (GitHub)

Saída: papahr/data/regioes_saude.parquet com COD_MUN (6), COD_MUN7, NOME_MUN, UF, CO_REGSAUD,
NO_REGSAUD, CO_MACSAUD, NO_MACSAUD (macro só quando a fonte DATASUS estiver disponível), FONTE.
Faz backup do arquivo anterior em .parquet.bak.
"""
from __future__ import annotations

import io
import sys
import urllib.request
import zipfile
from ftplib import FTP
from pathlib import Path

import polars as pl

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "papahr" / "data" / "regioes_saude.parquet"
URL_RESERVA = "https://raw.githubusercontent.com/wcota/covid19br/master/cities_info.csv"
FTP_HOST, FTP_DIR, FTP_ARQ = "ftp.datasus.gov.br", "/territorio/tabelas", "base_territorial.zip"

SCHEMA = {"COD_MUN": pl.Utf8, "COD_MUN7": pl.Utf8, "NOME_MUN": pl.Utf8, "UF": pl.Utf8, "CO_REGSAUD": pl.Utf8,
          "NO_REGSAUD": pl.Utf8, "CO_MACSAUD": pl.Utf8, "NO_MACSAUD": pl.Utf8, "FONTE": pl.Utf8}
UF_SIGLA = {"11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO", "21": "MA", "22": "PI",
            "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE", "29": "BA", "31": "MG", "32": "ES",
            "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF"}


def _dbf_para_df(dados: bytes) -> pl.DataFrame:
    from dbfread import DBF  # type: ignore
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".dbf", delete=False) as f:
        f.write(dados); caminho = f.name
    regs = [dict(r) for r in DBF(caminho, encoding="latin-1", char_decode_errors="replace")]
    return pl.DataFrame(regs) if regs else pl.DataFrame()


def _coluna(df: pl.DataFrame, *prefixos: str) -> str | None:
    for p in prefixos:
        for c in df.columns:
            if c.upper().startswith(p.upper()):
                return c
    return None


def de_datasus() -> pl.DataFrame:
    print(f"-- DATASUS: ftp://{FTP_HOST}{FTP_DIR}/{FTP_ARQ}")
    buf = io.BytesIO()
    ftp = FTP(FTP_HOST, timeout=60); ftp.login(); ftp.cwd(FTP_DIR); ftp.retrbinary(f"RETR {FTP_ARQ}", buf.write); ftp.quit()
    z = zipfile.ZipFile(buf)
    nomes = {n.lower(): n for n in z.namelist()}

    def tabela(chave: str) -> pl.DataFrame:
        n = next((v for k, v in nomes.items() if chave in k and k.endswith(".dbf")), None)
        return _dbf_para_df(z.read(n)) if n else pl.DataFrame()

    rl = tabela("rl_municip_regsaud"); tb = tabela("tb_regsaud")
    if rl.is_empty() or tb.is_empty():
        raise RuntimeError("zip sem rl_municip_regsaud/tb_regsaud")
    c_mun, c_reg = _coluna(rl, "CO_MUNICIP"), _coluna(rl, "CO_REGSAUD")
    t_reg, t_nome = _coluna(tb, "CO_REGSAUD"), _coluna(tb, "DS_NOME", "NO_REGSAUD", "DS_REGSAUD")
    out = (
        rl.select(pl.col(c_mun).cast(pl.Utf8).str.zfill(6).alias("COD_MUN"), pl.col(c_reg).cast(pl.Utf8).alias("CO_REGSAUD"))
        .join(tb.select(pl.col(t_reg).cast(pl.Utf8).alias("CO_REGSAUD"), pl.col(t_nome).cast(pl.Utf8).str.strip_chars().alias("NO_REGSAUD")), on="CO_REGSAUD", how="left")
    )
    rlm = tabela("rl_municip_macsaud"); tbm = tabela("tb_macsaud")
    if not rlm.is_empty() and not tbm.is_empty():
        m_mun, m_mac = _coluna(rlm, "CO_MUNICIP"), _coluna(rlm, "CO_MACSAUD")
        tm_mac, tm_nome = _coluna(tbm, "CO_MACSAUD"), _coluna(tbm, "DS_NOME", "NO_MACSAUD")
        out = out.join(
            rlm.select(pl.col(m_mun).cast(pl.Utf8).str.zfill(6).alias("COD_MUN"), pl.col(m_mac).cast(pl.Utf8).alias("CO_MACSAUD"))
            .join(tbm.select(pl.col(tm_mac).cast(pl.Utf8).alias("CO_MACSAUD"), pl.col(tm_nome).cast(pl.Utf8).str.strip_chars().alias("NO_MACSAUD")), on="CO_MACSAUD", how="left"),
            on="COD_MUN", how="left")
    else:
        out = out.with_columns(pl.lit(None, dtype=pl.Utf8).alias("CO_MACSAUD"), pl.lit(None, dtype=pl.Utf8).alias("NO_MACSAUD"))
    tbmun = tabela("tb_municip")
    if not tbmun.is_empty():
        mm, mn = _coluna(tbmun, "CO_MUNICIP"), _coluna(tbmun, "DS_NOME", "NO_MUNICIP")
        out = out.join(tbmun.select(pl.col(mm).cast(pl.Utf8).str.zfill(6).alias("COD_MUN"), pl.col(mn).cast(pl.Utf8).str.strip_chars().alias("NOME_MUN")), on="COD_MUN", how="left")
    else:
        out = out.with_columns(pl.lit(None, dtype=pl.Utf8).alias("NOME_MUN"))
    return out.with_columns(
        pl.lit(None, dtype=pl.Utf8).alias("COD_MUN7"),
        pl.col("COD_MUN").str.slice(0, 2).replace_strict(UF_SIGLA, default="?").alias("UF"),
        pl.lit("DATASUS base territorial").alias("FONTE"),
    ).select(list(SCHEMA)).unique(subset=["COD_MUN"], keep="first", maintain_order=True)


def de_reserva() -> pl.DataFrame:
    print(f"-- reserva: {URL_RESERVA}")
    with urllib.request.urlopen(URL_RESERVA, timeout=60) as r:  # noqa: S310
        csv = r.read()
    df = pl.read_csv(io.BytesIO(csv), schema_overrides={"ibge": pl.Utf8, "cod_RegiaoDeSaude": pl.Utf8})
    return df.select(
        pl.col("ibge").str.slice(0, 6).alias("COD_MUN"), pl.col("ibge").alias("COD_MUN7"),
        pl.col("city").str.replace(r"/[A-Z]{2}$", "").alias("NOME_MUN"), pl.col("state").alias("UF"),
        pl.col("cod_RegiaoDeSaude").alias("CO_REGSAUD"), pl.col("name_RegiaoDeSaude").str.strip_chars().str.replace_all(r"\s*/\s*", " / ").alias("NO_REGSAUD"),
        pl.lit(None, dtype=pl.Utf8).alias("CO_MACSAUD"), pl.lit(None, dtype=pl.Utf8).alias("NO_MACSAUD"),
        pl.lit("covid19br/UFV (CO_REGSAUD do DATASUS)").alias("FONTE"),
    ).unique(subset=["COD_MUN"], keep="first", maintain_order=True)


def main() -> int:
    so_reserva = "--reserva" in sys.argv
    df = pl.DataFrame()
    if not so_reserva:
        try:
            df = de_datasus()
        except Exception as exc:  # noqa: BLE001
            print(f"   DATASUS indisponível ({exc}); usando a reserva")
    if df.is_empty():
        df = de_reserva()
    df = df.sort("COD_MUN")
    n_reg = df["CO_REGSAUD"].n_unique()
    if df.height < 5000 or n_reg < 400:
        print(f"ERRO: tabela suspeita ({df.height} municípios, {n_reg} regiões)"); return 1
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    if DESTINO.exists():
        DESTINO.replace(DESTINO.with_suffix(".parquet.bak"))
    df.write_parquet(DESTINO)
    print(f"== {DESTINO}: {df.height} municípios, {n_reg} regiões de saúde, fonte {df['FONTE'][0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
