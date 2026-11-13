"""Importação dos dados das telas de atratividade a partir de uma PASTA ou de um ARQUIVO.

Aceita, na pasta escolhida pelo usuário:
  * arquivos RD do SIH (RDMG2501.dbc, RDMG2502.dbf …) baixados do DATASUS — todos são lidos e
    concatenados; a leitura (descompressão + DBF) é feita com o leitor do próprio app
    (papahr.core.dbc_reader), e o resultado consolidado fica em cache
    (GSPHOSP_HOME/cache/rd_pastas/<assinatura>.parquet) para a próxima abertura ser imediata;
  * parquet/csv gerados pelo pipeline do app (usa o mais recente da pasta).
Só as colunas que as telas usam (COLUNAS_UTEIS) são mantidas: um ano de MG inteiro
(~1,3 milhão de AIH × 113 colunas do RD) ocuparia vários GB; com a projeção, centenas de MB.

Os nomes de grupo/subgrupo/forma/procedimento vêm do SIGTAP em cache no app (sem FTP);
sem cache, o grupo e o subgrupo usam os dicionários embutidos e o resto fica com o código.

Uso sem Qt:
    carga = carregar_fonte(r"D:\\AIH_RD_2025", uf="MG", progresso=print)
    carga.aih, carga.hospitais, carga.municipios, carga.descricao
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

import polars as pl

from papahr.dashboard.populacao import pasta_cache

Progresso = Callable[[str], None]
Etapa = Callable[[int, int], None]          # (feitos, total)
Cancelado = Callable[[], bool]

# Colunas do RD/pipeline que as telas usam (nomes do RD bruto e os nomes renomeados pelo pipeline).
COLUNAS_UTEIS: tuple[str, ...] = (
    "CNES", "CO_CNES", "_cnes", "NO_ESTABELECIMENTO", "Hospital (CNES)", "NO_FANTASIA", "NOME_HOSPITAL",
    "MUNIC_RES", "PA_MUNPCN", "SP_M_PAC", "municipio_residencia", "MUNIC_MOV",
    "PROC_REA", "CO Procedimentos realizados", "SP_PROCREA", "PA_PROC_ID",
    "NO_GRUPO", "Grupo de Procedimentos", "NO_SUB_GRUPO", "SubGrupo de Procedimentos",
    "NO_FORMA_ORGANIZACAO", "Forma de organizacao", "NO_PROCEDIMENTO", "Procedimentos realizados",
    "DIAG_PRINC", "Nome da Mobirdade (CID)", "ESPEC", "CAR_INT", "COMPLEXIDADE", "Complexidade do Procedimento",
    "COMPLEX", "NO_FINANCIAMENTO", "Financiamento", "FINANC", "IDADE", "COD_IDADE", "SEXO",
    "ANOMES_CMPT", "Ano/Mes processamento", "ANOMES_MVM", "Ano/Mes Processamento", "PA_CMP",
    "ANO_CMPT", "SP_AA", "MES_CMPT", "SP_MM", "DT_INTER", "Data Internacao", "DT_CMPT",
    "VAL_TOT", "DIAS_PERM", "MORTE",   # baratas; úteis para benchmarks futuros
    "FONTE",                           # SIH / CIHA (nos dados consolidados)
    # ---- candidatos da CIHA (o layout de disseminação varia entre versões; ver _CIHA_SINONIMOS) ----
    "MODALIDADE", "MODAL", "TP_MODALIDADE", "NASC", "DT_NASC", "DT_SAIDA", "CMPT", "COMPETEN", "COMPETENCIA",
    "PROC_PRINC", "PROC", "CO_PROCEDIMENTO", "PROCEDIMENTO", "CID_PRINC", "CID", "DIAGPRINC", "CARATER", "CAR_ATEND",
    "TP_FINANC", "FONTE_PAG", "PAGADOR", "TIPO_FINANC", "FIN", "ESPECIALIDADE", "MUNRES", "CO_MUN_RES", "MUNMOV",
    "CO_MUN_MOV", "CNES_EXEC", "SEXO_PAC", "DT_ATEND", "DT_INTERN",
)
_NUMERICAS = ("IDADE", "VAL_TOT", "DIAS_PERM", "MORTE")
_VERSAO_CACHE = "rd-v2"   # mude quando COLUNAS_UTEIS ou a normalização mudarem (invalida o cache)

# CIHA: nomes do arquivo → nomes canônicos das telas. A CIHA (Comunicação de Informação Hospitalar e
# Ambulatorial, não SUS) tem layout parecido com o RD, mas não idêntico; o primeiro nome encontrado vence.
# O campo de fonte pagadora da CIHA (plano/particular/…) vira PAGADOR, porque FONTE aqui é SIH/CIHA.
_CIHA_SINONIMOS: dict[str, tuple[str, ...]] = {
    "CNES": ("CNES", "CNES_EXEC", "CO_CNES"),
    "MUNIC_RES": ("MUNIC_RES", "MUNRES", "CO_MUN_RES"),
    "MUNIC_MOV": ("MUNIC_MOV", "MUNMOV", "CO_MUN_MOV"),
    "PROC_REA": ("PROC_REA", "PROC_PRINC", "CO_PROCEDIMENTO", "PROCEDIMENTO", "PROC"),
    "DIAG_PRINC": ("DIAG_PRINC", "CID_PRINC", "DIAGPRINC", "CID"),
    "SEXO": ("SEXO", "SEXO_PAC"),
    "CAR_INT": ("CAR_INT", "CARATER", "CAR_ATEND"),
    "ESPEC": ("ESPEC", "ESPECIALIDADE"),
    "MODALIDADE": ("MODALIDADE", "MODAL", "TP_MODALIDADE"),
    "PAGADOR": ("FONTE", "TP_FINANC", "FONTE_PAG", "PAGADOR", "TIPO_FINANC", "FIN"),
    "DT_INTER": ("DT_INTER", "DT_INTERN", "DT_ATEND"),
    "NASC": ("NASC", "DT_NASC"),
}

# RDMG2501.dbc / rdmg2501.DBF … (RD + UF + AAMM); CIHAMG2501.dbc … (CIHA + UF + AAMM).
PADRAO_RD = re.compile(r"^RD([A-Z]{2})(\d{2})(\d{2})\.(dbc|dbf)$", re.I)
PADRAO_CIHA = re.compile(r"^CIHA([A-Z]{2})(\d{2})(\d{2})\.(dbc|dbf)$", re.I)
EXT_TABULAR = (".parquet", ".csv")


@dataclass
class Carga:
    aih: pl.DataFrame
    hospitais: pl.DataFrame
    municipios: pl.DataFrame
    uf: str | None
    descricao: str = ""
    origem: str = ""
    avisos: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. O que há na pasta · seleção de UF, período e fontes · download do DATASUS
# ---------------------------------------------------------------------------

FTP_DATASUS = "ftp.datasus.gov.br"
FTP_DIR = {"SIH": "/dissemin/publicos/SIHSUS/200801_/Dados", "CIHA": "/dissemin/publicos/CIHA/201101_/Dados"}
PREFIXO = {"SIH": "RD", "CIHA": "CIHA"}


@dataclass
class Selecao:
    """O que analisar: UFs dos hospitais, período (competências AAAAMM, inclusive) e fontes."""
    ufs: list[str]                              # ["MG"] ou várias
    inicio: str                                 # "202501"
    fim: str                                    # "202512"
    fontes: list[str] = field(default_factory=lambda: ["SIH"])      # "SIH" e/ou "CIHA"
    outras_ufs: bool = False                    # incluir também os arquivos das demais UFs da pasta (residentes internados fora)

    def competencias(self) -> list[str]:
        return competencias(self.inicio, self.fim)

    def rotulo(self) -> str:
        c = self.competencias()
        per = f"{c[0][:4]}-{c[0][4:]} a {c[-1][:4]}-{c[-1][4:]}" if c else "—"
        return f"{'/'.join(self.ufs)} · {per} · {'+'.join(self.fontes)}" + (" (+ demais UFs da pasta)" if self.outras_ufs else "")


def competencias(inicio: str, fim: str) -> list[str]:
    """['202501', …, '202512'] (AAAAMM inclusive); vazio se fim < início."""
    a0, m0 = int(inicio[:4]), int(inicio[4:6]); a1, m1 = int(fim[:4]), int(fim[4:6])
    out = []
    a, m = a0, m0
    while (a, m) <= (a1, m1):
        out.append(f"{a:04d}{m:02d}")
        m += 1
        if m > 12:
            a, m = a + 1, 1
    return out


def nome_arquivo(fonte: str, uf: str, comp: str, ext: str = "dbc") -> str:
    return f"{PREFIXO[fonte]}{uf.upper()}{comp[2:4]}{comp[4:6]}.{ext}"


def inventario(pasta: str | Path) -> pl.DataFrame:
    """Um registro por arquivo RD/CIHA da pasta: arquivo | fonte | uf | comp (AAAAMM) | mb | mtime."""
    a = listar_arquivos(pasta)
    linhas = []
    for fonte, rx, lst in (("SIH", PADRAO_RD, a["rd"]), ("CIHA", PADRAO_CIHA, a["ciha"])):
        for f in lst:
            m = rx.match(f.name)
            st = f.stat()
            linhas.append({"arquivo": f.name, "fonte": fonte, "uf": m.group(1).upper(), "comp": f"20{m.group(2)}{m.group(3)}",
                           "mb": round(st.st_size / 1e6, 1), "mtime": int(st.st_mtime)})
    return pl.DataFrame(linhas, schema={"arquivo": pl.Utf8, "fonte": pl.Utf8, "uf": pl.Utf8, "comp": pl.Utf8, "mb": pl.Float64, "mtime": pl.Int64})


def arquivos_da_selecao(pasta: str | Path, sel: Selecao) -> tuple[list[Path], list[tuple[str, str, str]]]:
    """(arquivos existentes na pasta que a seleção pede, [(fonte, uf, comp) que faltam])."""
    p = Path(pasta)
    inv = inventario(p)
    comps = set(sel.competencias())
    ufs = {u.upper() for u in sel.ufs}
    achados: list[Path] = []
    faltam: list[tuple[str, str, str]] = []
    existentes = {(r["fonte"], r["uf"], r["comp"]): r["arquivo"] for r in inv.to_dicts()}
    for fonte in sel.fontes:
        for uf in sorted(ufs):
            for c in sorted(comps):
                nome = existentes.get((fonte, uf, c))
                if nome:
                    achados.append(p / nome)
                else:
                    faltam.append((fonte, uf, c))
    if sel.outras_ufs:
        for r in inv.to_dicts():
            if r["fonte"] in sel.fontes and r["uf"] not in ufs and r["comp"] in comps:
                achados.append(p / r["arquivo"])
    return achados, faltam


def baixar_datasus(faltantes: Sequence[tuple[str, str, str]], pasta: str | Path, *, progresso: Progresso | None = None,
                   etapa: Etapa | None = None, cancelado: Cancelado | None = None, tentativas: int = 3,
                   ftp_factory=None) -> tuple[list[Path], list[str]]:
    """Baixa do FTP do DATASUS os arquivos (fonte, uf, comp) que faltam na pasta. Devolve (baixados, erros).

    SIH-RD: ftp.datasus.gov.br/dissemin/publicos/SIHSUS/200801_/Dados/RDUFAAMM.dbc
    CIHA:   ftp.datasus.gov.br/dissemin/publicos/CIHA/201101_/Dados/CIHAUFAAMM.dbc
    Grava em .part e renomeia no fim (nunca deixa arquivo pela metade). Competência ainda não publicada = erro
    listado, não exceção. ftp_factory: injeção para testes (padrão ftplib.FTP).
    """
    import ftplib
    dizer = progresso or (lambda s: None)
    passo = etapa or (lambda i, n: None)
    parar = cancelado or (lambda: False)
    pasta = Path(pasta); pasta.mkdir(parents=True, exist_ok=True)
    fabrica = ftp_factory or (lambda: ftplib.FTP(FTP_DATASUS, timeout=60))
    baixados: list[Path] = []; erros: list[str] = []
    total = len(faltantes); passo(0, total)
    ftp = None
    try:
        for i, (fonte, uf, comp) in enumerate(faltantes, 1):
            if parar():
                raise InterruptedError("download cancelado")
            nome = nome_arquivo(fonte, uf, comp)
            destino = pasta / nome
            if destino.exists() and destino.stat().st_size > 0:
                baixados.append(destino); passo(i, total); continue
            ok = False
            for tent in range(1, tentativas + 1):
                try:
                    if ftp is None:
                        dizer(f"-- conectando a {FTP_DATASUS}…")
                        ftp = fabrica(); ftp.login()
                    ftp.cwd(FTP_DIR[fonte])
                    dizer(f"-- baixando {nome} ({i}/{total}; tentativa {tent})…")
                    tmp = destino.with_suffix(destino.suffix + ".part")
                    with open(tmp, "wb") as fh:
                        ftp.retrbinary(f"RETR {nome}", fh.write, blocksize=1 << 16)
                    if tmp.stat().st_size < 1000:
                        tmp.unlink(missing_ok=True)
                        raise OSError("arquivo vazio")
                    os.replace(tmp, destino)
                    dizer(f"   {nome}: {destino.stat().st_size / 1e6:.1f} MB")
                    baixados.append(destino); ok = True
                    break
                except ftplib.error_perm as e:          # 550 = não existe (competência não publicada)
                    destino.with_suffix(destino.suffix + ".part").unlink(missing_ok=True)
                    erros.append(f"{nome}: não está no FTP ({e})"); dizer(f"   {nome}: não está no FTP ({e})")
                    break
                except (OSError, EOFError, ftplib.error_temp, ftplib.error_reply) as e:
                    destino.with_suffix(destino.suffix + ".part").unlink(missing_ok=True)
                    dizer(f"   {nome}: falhou ({type(e).__name__}: {e})")
                    try:
                        if ftp is not None:
                            ftp.quit()
                    except Exception:  # noqa: BLE001
                        pass
                    ftp = None
                    if tent == tentativas:
                        erros.append(f"{nome}: {type(e).__name__}: {e}")
                    else:
                        time.sleep(2 * tent)
            passo(i, total)
            if not ok and not any(er.startswith(nome) for er in erros):
                erros.append(f"{nome}: não baixado")
    finally:
        try:
            if ftp is not None:
                ftp.quit()
        except Exception:  # noqa: BLE001
            pass
    return baixados, erros


def listar_arquivos(pasta: str | Path) -> dict[str, list[Path]]:
    """{'rd': [RD*.dbc/.dbf …], 'ciha': [...], 'tabular': [parquet/csv mais recentes primeiro], 'outros': [...]}."""
    p = Path(pasta)
    out: dict[str, list[Path]] = {"rd": [], "ciha": [], "tabular": [], "outros": []}
    if not p.is_dir():
        return out
    for f in sorted(p.iterdir()):
        if not f.is_file():
            continue
        if PADRAO_RD.match(f.name):
            out["rd"].append(f)
        elif PADRAO_CIHA.match(f.name):
            out["ciha"].append(f)
        elif f.suffix.lower() in EXT_TABULAR and f.stat().st_size > 1000:
            out["tabular"].append(f)
        elif f.suffix.lower() in (".dbc", ".dbf"):
            out["outros"].append(f)
    out["tabular"].sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return out


def descrever_pasta(pasta: str | Path) -> str:
    a = listar_arquivos(pasta)
    partes = []
    if a["rd"]:
        ufs = sorted({PADRAO_RD.match(f.name).group(1).upper() for f in a["rd"]})
        comps = sorted(f"20{PADRAO_RD.match(f.name).group(2)}-{PADRAO_RD.match(f.name).group(3)}" for f in a["rd"])
        partes.append(f"{len(a['rd'])} arquivo(s) RD do SIH ({'/'.join(ufs)}; {comps[0]} a {comps[-1]})")
    if a["ciha"]:
        ufs = sorted({PADRAO_CIHA.match(f.name).group(1).upper() for f in a["ciha"]})
        comps = sorted(f"20{PADRAO_CIHA.match(f.name).group(2)}-{PADRAO_CIHA.match(f.name).group(3)}" for f in a["ciha"])
        partes.append(f"{len(a['ciha'])} arquivo(s) CIHA, não SUS ({'/'.join(ufs)}; {comps[0]} a {comps[-1]})")
    if a["tabular"]:
        partes.append(f"{len(a['tabular'])} parquet/csv (mais recente: {a['tabular'][0].name})")
    if a["outros"]:
        partes.append(f"{len(a['outros'])} dbc/dbf de outros sistemas (ignorados)")
    return "; ".join(partes) if partes else "nenhum arquivo de dados reconhecido"


# ---------------------------------------------------------------------------
# 2. Leitura de um arquivo (dbc/dbf/parquet/csv) só com as colunas úteis
# ---------------------------------------------------------------------------

def _projetar(df: pl.DataFrame) -> pl.DataFrame:
    uteis = [c for c in df.columns if c in COLUNAS_UTEIS]
    return df.select(uteis) if uteis else df


def _normalizar(df: pl.DataFrame) -> pl.DataFrame:
    """Tipos homogêneos entre arquivos: texto em tudo, exceto as numéricas; zeros à esquerda preservados."""
    exprs = []
    for c, t in df.schema.items():
        if c in _NUMERICAS:
            exprs.append(pl.col(c).cast(pl.Float64, strict=False).alias(c))
        elif t != pl.Utf8:
            exprs.append(pl.col(c).cast(pl.Utf8, strict=False).alias(c))
    if exprs:
        df = df.with_columns(exprs)
    strip = [pl.col(c).str.strip_chars().alias(c) for c, t in df.schema.items() if t == pl.Utf8]
    if strip:
        df = df.with_columns(strip)
    if "ANOMES_CMPT" not in df.columns and "ANO_CMPT" in df.columns and "MES_CMPT" in df.columns:
        df = df.with_columns(pl.concat_str([pl.col("ANO_CMPT"), pl.col("MES_CMPT").str.zfill(2)]).alias("ANOMES_CMPT"))
    if "FONTE" not in df.columns:
        df = df.with_columns(pl.lit("SIH").alias("FONTE"))
    return df


# ---- DBF/DBC: leitura rápida ---------------------------------------------------------------
# O leitor do app (dbfread) monta um dict por registro com as 113 colunas do RD: ~1 minuto e >1 GB por
# arquivo mensal de MG. Aqui o DBF é fatiado com numpy só nas colunas úteis (segundos). O .dbc é
# descomprimido pelo `datasus_dbc` nativo, se instalado (segundos), senão pelo descompressor em Python
# puro do app (minutos). Qualquer falha cai no leitor do app.

def _dbf_bytes(p: Path) -> bytes:
    """Bytes do DBF (descomprimindo o .dbc)."""
    if p.suffix.lower() == ".dbf":
        return p.read_bytes()
    try:
        import datasus_dbc  # nativo (Rust); opcional: pip install datasus-dbc
        fd, tmp = tempfile.mkstemp(suffix=".dbf"); os.close(fd)
        try:
            datasus_dbc.decompress(str(p), tmp)
            dados = Path(tmp).read_bytes()
            if len(dados) > 32 and dados[0] in (0x03, 0x83, 0x8B, 0x30):
                return dados
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass
    from papahr.core.dbc_reader import _dbc_body_to_dbf
    return _dbc_body_to_dbf(p.read_bytes())


def _campos_dbf(dbf: bytes) -> tuple[int, int, int, list[tuple[str, str, int, int, int]]]:
    """(n_registros, tamanho_cabeçalho, tamanho_registro, [(nome, tipo, tamanho, decimais, offset)])."""
    n_reg, tam_cab, tam_reg = struct.unpack_from("<IHH", dbf, 4)
    campos = []
    off = 1                      # byte 0 do registro = marca de exclusão
    pos = 32
    while pos + 32 <= tam_cab and dbf[pos] != 0x0D:
        nome = dbf[pos:pos + 11].split(b"\0", 1)[0].decode("latin-1").strip()
        tipo = chr(dbf[pos + 11]); tam = dbf[pos + 16]; dec = dbf[pos + 17]
        campos.append((nome, tipo, tam, dec, off))
        off += tam; pos += 32
    return n_reg, tam_cab, tam_reg, campos


def _ler_dbf_rapido(dbf: bytes, desejadas: Sequence[str] = COLUNAS_UTEIS, numericas: bool = True) -> tuple[pl.DataFrame, int]:
    """Fatia o DBF com numpy só nas colunas desejadas. Devolve (df, nº de colunas do arquivo)."""
    import numpy as np
    n_reg, tam_cab, tam_reg, campos = _campos_dbf(dbf)
    if not campos or tam_reg <= 1:
        raise ValueError("cabeçalho DBF inválido")
    n_disp = (len(dbf) - tam_cab) // tam_reg
    n = min(n_reg, n_disp) if n_reg else n_disp
    quer = [c for c in campos if c[0] in desejadas]
    nomes = ["_del"] + [c[0] for c in quer]
    dt = np.dtype({"names": nomes, "formats": ["S1"] + [f"S{c[2]}" for c in quer],
                   "offsets": [0] + [c[4] for c in quer], "itemsize": tam_reg})
    arr = np.frombuffer(dbf, dtype=dt, count=n, offset=tam_cab)
    vivos = arr["_del"] != b"*"
    cols = {}
    for nome, tipo, tam, dec, _ in quer:
        bruto = arr[nome][vivos]
        if numericas and tipo in ("N", "F") and nome in _NUMERICAS:
            txt = np.char.strip(np.char.decode(bruto, "latin-1"))
            cols[nome] = pl.Series(nome, txt, dtype=pl.Utf8).cast(pl.Float64, strict=False)
        else:
            cols[nome] = pl.Series(nome, np.char.decode(bruto, "latin-1"), dtype=pl.Utf8)
    return pl.DataFrame(cols), len(campos)


def fonte_do_nome(p: str | Path) -> str | None:
    """'SIH' para RDUFAAMM, 'CIHA' para CIHAUFAAMM, None para o resto (parquet/csv do pipeline: FONTE já vem ou é SIH)."""
    n = Path(p).name
    if PADRAO_RD.match(n):
        return "SIH"
    if PADRAO_CIHA.match(n):
        return "CIHA"
    return None


def _normalizar_ciha(df: pl.DataFrame, nome: str = "") -> pl.DataFrame:
    """Renomeia as colunas da CIHA para os nomes canônicos e deriva o que faltar (IDADE, ANO/MES_CMPT)."""
    cols = list(df.columns)
    ren: dict[str, str] = {}
    for canon, cands in _CIHA_SINONIMOS.items():
        if canon in cols:
            continue
        for c in cands:
            if c in cols and c not in ren:
                ren[c] = canon
                break
    if "FONTE" in cols and "FONTE" not in ren:        # FONTE do arquivo (pagadora) nunca sobrevive com esse nome
        ren["FONTE"] = "PAGADOR" if "PAGADOR" not in cols and "PAGADOR" not in ren.values() else "FONTE_ARQ"
    df = df.rename(ren) if ren else df
    cols = list(df.columns)
    txt = lambda c: pl.col(c).cast(pl.Utf8, strict=False).str.strip_chars()  # noqa: E731
    exprs = []
    # competência: ANO_CMPT/MES_CMPT, senão CMPT/COMPETEN (AAAAMM), senão o nome do arquivo (CIHAUFAAMM)
    comp = next((c for c in ("CMPT", "COMPETEN", "COMPETENCIA") if c in cols), None)
    m = PADRAO_CIHA.match(nome or "")
    if "ANO_CMPT" not in cols and comp:
        exprs += [txt(comp).str.slice(0, 4).alias("ANO_CMPT"), txt(comp).str.slice(4, 2).alias("MES_CMPT")]
    elif "ANO_CMPT" not in cols and m:
        exprs += [pl.lit(f"20{m.group(2)}").alias("ANO_CMPT"), pl.lit(m.group(3)).alias("MES_CMPT")]
    # idade: se não houver IDADE, calcula de NASC e DT_INTER (AAAAMMDD)
    if "IDADE" not in cols and "NASC" in cols and "DT_INTER" in cols:
        n_ = txt("NASC").str.to_date("%Y%m%d", strict=False); d_ = txt("DT_INTER").str.to_date("%Y%m%d", strict=False)
        exprs.append(((d_ - n_).dt.total_days() / 365.25).floor().alias("IDADE"))
        exprs.append(pl.lit("4").alias("COD_IDADE"))
    if exprs:
        df = df.with_columns(exprs)
    return df.with_columns(pl.lit("CIHA").alias("FONTE"))


def ler_arquivo(p: str | Path, progresso: Progresso | None = None) -> tuple[pl.DataFrame, int]:
    """Lê um arquivo (dbc/dbf/parquet/csv) e devolve (df só com as colunas úteis, nº de colunas originais).
    Arquivos CIHA* passam pela normalização da CIHA e saem com FONTE = 'CIHA'; RD* com FONTE = 'SIH'."""
    p = Path(p)
    ext = p.suffix.lower()
    fonte = fonte_do_nome(p)

    def _fechar(df: pl.DataFrame) -> pl.DataFrame:
        if fonte == "CIHA":
            df = _normalizar_ciha(df, p.name)
        elif fonte == "SIH" and "FONTE" in df.columns:
            df = df.drop("FONTE")
        return _normalizar(df)

    if ext in (".dbc", ".dbf"):
        try:
            df, n = _ler_dbf_rapido(_dbf_bytes(p))
            return _fechar(df), n
        except Exception as e:  # noqa: BLE001
            if progresso:
                progresso(f"   leitura rápida falhou ({type(e).__name__}: {e}); usando o leitor do app")
        from papahr.core.dbc_reader import read_dbc
        bruto = read_dbc(p)
        n = bruto.width
        return _fechar(_projetar(bruto)), n
    lf = pl.scan_parquet(p) if ext == ".parquet" else pl.scan_csv(p, infer_schema_length=10000)
    nomes = lf.collect_schema().names()
    uteis = [c for c in nomes if c in COLUNAS_UTEIS]
    df = lf.select(uteis).collect() if uteis else lf.collect()
    return _normalizar(df), len(nomes)


def inspecionar_arquivo(p: str | Path, n_linhas: int = 3) -> str:
    """Relatório de um dbc/dbf: nº de registros, TODAS as colunas (nome, tipo, tamanho) e, para as de poucos
    valores distintos, a distribuição. Serve para conferir o layout da CIHA e ajustar _CIHA_SINONIMOS."""
    p = Path(p)
    dbf = _dbf_bytes(p)
    n_reg, tam_cab, tam_reg, campos = _campos_dbf(dbf)
    n_fmt = f"{n_reg:,}".replace(",", ".")
    linhas = [f"{p.name}: {n_fmt} registros, {len(campos)} campos, {tam_reg} bytes/registro"]
    linhas.append("campos: " + ", ".join(f"{n}({t}{tam}{',' + str(d) if d else ''})" for n, t, tam, d, _ in campos))
    df, _ = _ler_dbf_rapido(dbf, desejadas=[c[0] for c in campos], numericas=False)
    for nome, _t, _tam, _d, _o in campos:
        if nome not in df.columns:
            continue
        col = df[nome].cast(pl.Utf8, strict=False).str.strip_chars()
        k = col.n_unique()
        if k <= 12:
            dist = df.select(col.alias("v")).group_by("v").agg(pl.len().alias("n")).sort(["n", "v"], descending=[True, False]).to_dicts()
            linhas.append(f"  {nome}: " + ", ".join(f"{r['v']!r}={str(r['n']).replace(',', '.')}" for r in dist))
        else:
            kf = f"{k:,}".replace(",", ".")
            linhas.append(f"  {nome}: {kf} valores distintos; ex.: {', '.join(repr(v) for v in col.head(n_linhas).to_list())}")
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# 3. Pasta com arquivos RD/CIHA → um DataFrame (com cache)
# ---------------------------------------------------------------------------

def _assinatura(arquivos: list[Path]) -> str:
    h = hashlib.sha1(_VERSAO_CACHE.encode())
    for f in sorted(arquivos):
        st = f.stat()
        h.update(f"{f.name.lower()}|{st.st_size}|{int(st.st_mtime)}".encode())
    return h.hexdigest()[:16]


def _pasta_cache_rd() -> Path:
    p = pasta_cache() / "rd_pastas"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ler_rd_parte(arquivo: str, destino: str) -> tuple[str, int, float]:
    """Função dos processos auxiliares: lê um RD e grava a parte em parquet. Devolve (nome, linhas, segundos)."""
    t = time.time()
    f = Path(arquivo)
    df, _ = ler_arquivo(f)
    df = df.with_columns(pl.lit(f.name).alias("ARQUIVO"))
    tmp = Path(destino).with_suffix(".tmp.parquet")
    df.write_parquet(tmp)
    os.replace(tmp, destino)
    return f.name, df.height, time.time() - t


def _n_processos() -> int:
    """Processos paralelos na leitura: cada um segura ~1 GB; 3 é seguro em 16 GB. GSPHOSP_PROCESSOS=1 desliga."""
    try:
        n = int(os.environ.get("GSPHOSP_PROCESSOS", "0"))
    except ValueError:
        n = 0
    if n > 0:
        return n
    if getattr(sys, "frozen", False):          # exe do PyInstaller: sem freeze_support garantido → sequencial
        return 1
    return max(1, min(3, (os.cpu_count() or 2) - 1))


def _parte_de(f: Path) -> Path:
    """Parquet da parte de UM arquivo, identificado por nome+tamanho+mtime: reaproveitado por qualquer seleção."""
    st = f.stat()
    d = _pasta_cache_rd() / "partes"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{f.name.lower()}-{st.st_size}-{int(st.st_mtime)}.parquet"


def ler_pasta_rd(pasta: str | Path, *, progresso: Progresso | None = None, usar_cache: bool = True,
                 etapa: Etapa | None = None, cancelado: Cancelado | None = None) -> pl.DataFrame:
    """Lê e concatena TODOS os RD*.dbc/.dbf (SIH) e CIHA*.dbc/.dbf (não SUS) da pasta (ver ler_arquivos)."""
    lista = listar_arquivos(pasta)
    arquivos = lista["rd"] + lista["ciha"]
    if not arquivos:
        raise FileNotFoundError(f"nenhum arquivo RD (RDUFAAMM.dbc/.dbf) nem CIHA (CIHAUFAAMM.dbc/.dbf) em {pasta}")
    return ler_arquivos(arquivos, progresso=progresso, usar_cache=usar_cache, etapa=etapa, cancelado=cancelado)


def ler_arquivos(arquivos: Sequence[Path], *, progresso: Progresso | None = None, usar_cache: bool = True,
                 etapa: Etapa | None = None, cancelado: Cancelado | None = None) -> pl.DataFrame:
    """Lê e concatena os RD/CIHA dados (FONTE = 'SIH' / 'CIHA').

    Cada arquivo vira uma "parte" em parquet (GSPHOSP_HOME/cache/rd_pastas/partes/, por nome+tamanho+data), lida
    em processos paralelos; partes já prontas não são relidas — uma carga interrompida continua de onde parou e
    outra seleção (outro período/UF) reaproveita o que já foi lido. No fim as partes da seleção são consolidadas
    num parquet único (cache por assinatura da seleção).
    """
    dizer = progresso or (lambda s: None)
    passo = etapa or (lambda i, n: None)
    parar = cancelado or (lambda: False)
    arquivos = sorted({Path(a) for a in arquivos}, key=lambda f: f.name.lower())
    if not arquivos:
        raise FileNotFoundError("nenhum arquivo RD/CIHA na seleção")
    chave = _assinatura(arquivos)
    cache = _pasta_cache_rd() / f"{chave}.parquet"
    total = len(arquivos)
    if usar_cache and cache.exists():
        dizer(f"-- {total} arquivos RD/CIHA já consolidados em cache ({cache.name}); lendo…")
        passo(total, total)
        return pl.read_parquet(cache)
    destino = {f: _parte_de(f) for f in arquivos}
    prontas = [f for f in arquivos if usar_cache and destino[f].exists()]
    pendentes = [f for f in arquivos if f not in prontas]
    if prontas:
        dizer(f"-- {len(prontas)} de {total} arquivos já lidos numa carga anterior; faltam {len(pendentes)}")
    passo(len(prontas), total)
    erros: list[str] = []
    t0 = time.time()
    feitos = len(prontas)
    tempos: list[float] = []

    def _registrar(nome: str, linhas: int, seg: float) -> None:
        nonlocal feitos
        feitos += 1; tempos.append(seg)
        resta = (total - feitos) * (sum(tempos) / len(tempos)) / max(1, min(n_proc, total - feitos) or 1)
        dizer(f"   {nome}: {linhas:,} AIH em {seg:.0f} s ({feitos}/{total}; faltam ~{resta / 60:.0f} min)".replace(",", "."))
        passo(feitos, total)

    n_proc = min(_n_processos(), max(1, len(pendentes)))
    if pendentes:
        tam = sum(f.stat().st_size for f in pendentes) / 1e6
        dizer(f"-- lendo {len(pendentes)} arquivo(s) RD/CIHA ({tam:.0f} MB) com {n_proc} processo(s)…")
    if pendentes and n_proc > 1:
        try:
            import concurrent.futures as cf
            import multiprocessing as mp
            with cf.ProcessPoolExecutor(max_workers=n_proc, mp_context=mp.get_context("spawn")) as ex:
                futs = {ex.submit(_ler_rd_parte, str(f), str(destino[f])): f for f in pendentes}
                for fut in cf.as_completed(futs):
                    f = futs[fut]
                    if parar():
                        ex.shutdown(wait=False, cancel_futures=True)
                        raise InterruptedError("carga cancelada")
                    try:
                        _registrar(*fut.result())
                    except Exception as e:  # noqa: BLE001
                        erros.append(f"{f.name}: {type(e).__name__}: {e}")
                        dizer(f"   {f.name} FALHOU: {type(e).__name__}: {e}")
            pendentes = [f for f in pendentes if not destino[f].exists() and not any(e.startswith(f.name) for e in erros)]
        except InterruptedError:
            raise
        except Exception as e:  # noqa: BLE001   (pool indisponível: segue sequencial)
            dizer(f"-- leitura paralela indisponível ({type(e).__name__}: {e}); lendo em sequência")
            pendentes = [f for f in pendentes if not destino[f].exists()]
    for f in pendentes:
        if parar():
            raise InterruptedError("carga cancelada")
        dizer(f"-- lendo {f.name} ({f.stat().st_size / 1e6:.0f} MB)…")
        try:
            _registrar(*_ler_rd_parte(str(f), str(destino[f])))
        except Exception as e:  # noqa: BLE001
            erros.append(f"{f.name}: {type(e).__name__}: {e}")
            dizer(f"   {f.name} FALHOU: {type(e).__name__}: {e}")
    lidas = [destino[f] for f in arquivos if destino[f].exists()]
    if not lidas:
        raise RuntimeError("nenhum arquivo RD pôde ser lido:\n" + "\n".join(erros))
    aih = pl.concat([pl.read_parquet(x) for x in lidas], how="diagonal_relaxed")
    dizer(f"-- {aih.height:,} AIH de {len(lidas)} arquivo(s) em {time.time() - t0:.0f} s".replace(",", "."))
    if usar_cache and not erros:
        try:
            tmp = cache.with_suffix(".tmp.parquet")
            aih.write_parquet(tmp); os.replace(tmp, cache)
            (cache.with_suffix(".json")).write_text(json.dumps({
                "pasta": str(arquivos[0].parent), "arquivos": [f.name for f in arquivos], "linhas": aih.height,
                "criado": time.strftime("%Y-%m-%d %H:%M"), "versao": _VERSAO_CACHE,
            }, ensure_ascii=False, indent=1), encoding="utf-8")
            dizer(f"-- consolidado guardado em {cache} (a próxima abertura desta seleção é imediata)")
        except Exception as e:  # noqa: BLE001
            dizer(f"-- não consegui gravar o cache ({e}); segue sem cache")
    elif erros:
        dizer(f"-- {len(erros)} arquivo(s) falharam; consolidado NÃO guardado (as partes lidas ficam para a próxima tentativa)")
    return aih


# ---------------------------------------------------------------------------
# 4. Nomes do SIGTAP (cache do app, sem FTP)
# ---------------------------------------------------------------------------

_COLS_SIGTAP = ("NO_PROCEDIMENTO", "NO_GRUPO", "NO_SUB_GRUPO", "NO_FORMA_ORGANIZACAO", "NO_FINANCIAMENTO", "COMPLEXIDADE")


_COMPLEXIDADE = {"0": "Não se aplica", "1": "Atenção básica", "2": "Média complexidade", "3": "Alta complexidade"}
# pares (coluna de nome, nº de dígitos do código correspondente) para o saneamento
_NOMES_POR_CODIGO = (("NO_GRUPO", 2), ("Grupo de Procedimentos", 2), ("NO_SUB_GRUPO", 4), ("SubGrupo de Procedimentos", 4),
                     ("NO_FORMA_ORGANIZACAO", 6), ("Forma de organizacao", 6), ("NO_PROCEDIMENTO", 10), ("Procedimentos realizados", 10))


def _ultima_versao(df: pl.DataFrame) -> pl.DataFrame:
    if df.is_empty() or "file_version_id" not in df.columns:
        return df
    v = df["file_version_id"].max()
    return df.filter(pl.col("file_version_id") == v).drop("file_version_id")


def tabela_sigtap() -> pl.DataFrame | None:
    """CO_PROCEDIMENTO → nomes (grupo, subgrupo, forma, procedimento, financiamento, complexidade), da versão
    mais recente do SIGTAP em cache no app; None se não houver cache.

    Monta os nomes a partir das tabelas de detalhe, com as chaves certas: CO_GRUPO = 2 primeiros dígitos do
    procedimento, CO_SUB_GRUPO = dígitos 3–4, CO_FORMA_ORGANIZACAO = dígitos 5–6. NÃO usa
    SIGTAPRepository.get_procedure_details(), que junta grupo/subgrupo/forma só pela versão e dá a todos os
    procedimentos o mesmo (último) grupo/subgrupo/forma — ver scripts/corrigir_sigtap_app.py.
    """
    try:
        from papahr.datasources.sigtap import SIGTAPRepository
        repo = SIGTAPRepository()
        if not repo.available_versions():
            return None
        proc = _ultima_versao(repo.get_detail("Procedimento"))
        if proc.is_empty() or "CO_PROCEDIMENTO" not in proc.columns:
            return None
        proc = proc.with_columns(pl.col("CO_PROCEDIMENTO").cast(pl.Utf8).str.strip_chars().str.zfill(10)).with_columns(
            pl.col("CO_PROCEDIMENTO").str.slice(0, 2).alias("CO_GRUPO"),
            pl.col("CO_PROCEDIMENTO").str.slice(2, 2).alias("CO_SUB_GRUPO"),
            pl.col("CO_PROCEDIMENTO").str.slice(4, 2).alias("CO_FORMA_ORGANIZACAO"),
        ).unique(subset=["CO_PROCEDIMENTO"], keep="last")
        out = proc.select(["CO_PROCEDIMENTO", "CO_GRUPO", "CO_SUB_GRUPO", "CO_FORMA_ORGANIZACAO"]
                          + [c for c in ("NO_PROCEDIMENTO", "TP_COMPLEXIDADE", "CO_FINANCIAMENTO") if c in proc.columns])

        def _juntar(detalhe: str, chaves: list[str], nome: str) -> None:
            nonlocal out
            d = _ultima_versao(repo.get_detail(detalhe))
            if d.is_empty() or nome not in d.columns or any(k not in d.columns for k in chaves):
                return
            d = d.select(chaves + [nome]).with_columns([pl.col(k).cast(pl.Utf8).str.strip_chars().str.zfill(2) for k in chaves]) \
                 .unique(subset=chaves, keep="last")
            out = out.join(d, on=chaves, how="left")

        _juntar("Grupo", ["CO_GRUPO"], "NO_GRUPO")
        _juntar("Subgrupo", ["CO_GRUPO", "CO_SUB_GRUPO"], "NO_SUB_GRUPO")
        _juntar("Forma de organização", ["CO_GRUPO", "CO_SUB_GRUPO", "CO_FORMA_ORGANIZACAO"], "NO_FORMA_ORGANIZACAO")
        if "CO_FINANCIAMENTO" in out.columns:
            _juntar("Financiamento", ["CO_FINANCIAMENTO"], "NO_FINANCIAMENTO")
        if "TP_COMPLEXIDADE" in out.columns:
            out = out.with_columns(pl.col("TP_COMPLEXIDADE").cast(pl.Utf8).str.strip_chars()
                                   .replace_strict(_COMPLEXIDADE, default="Não informada", return_dtype=pl.Utf8).alias("COMPLEXIDADE"))
        cols = ["CO_PROCEDIMENTO"] + [c for c in _COLS_SIGTAP if c in out.columns]
        out = out.select(cols).with_columns([pl.col(c).cast(pl.Utf8).str.strip_chars() for c in cols[1:]])
        # sanidade: se a tabela resultante tiver um só nome para muitos códigos, não serve
        for c, nd in (("NO_GRUPO", 2), ("NO_SUB_GRUPO", 4), ("NO_FORMA_ORGANIZACAO", 6)):
            if c in out.columns and out[c].drop_nulls().n_unique() <= 1 < out["CO_PROCEDIMENTO"].str.slice(0, nd).n_unique():
                out = out.drop(c)
        return out if out.width > 1 else None
    except Exception:  # noqa: BLE001
        return None


def sanear_nomes_sigtap(aih: pl.DataFrame) -> tuple[pl.DataFrame, list[str]]:
    """Remove colunas de nome SIGTAP corrompidas (um único nome para vários códigos), para serem
    refeitas por enriquecer_sigtap(). Acontece nos parquet do pipeline gerados com o
    get_procedure_details() defeituoso (todas as AIH com o mesmo grupo/subgrupo/forma)."""
    proc = next((c for c in ("PROC_REA", "CO Procedimentos realizados", "SP_PROCREA", "PA_PROC_ID") if c in aih.columns), None)
    if proc is None:
        return aih, []
    pc = pl.col(proc).cast(pl.Utf8, strict=False).str.zfill(10)
    removidas: list[str] = []
    for col, nd in _NOMES_POR_CODIGO:
        if col not in aih.columns:
            continue
        nome = pl.col(col).cast(pl.Utf8, strict=False).str.replace(r"^\d{2,10}\s*-\s*", "")
        st = aih.select(pc.str.slice(0, nd).n_unique().alias("k"), nome.drop_nulls().n_unique().alias("n")).row(0)
        if st[0] > 1 and st[1] <= 1:
            removidas.append(col)
    avisos = []
    if removidas:
        aih = aih.drop(removidas)
        avisos.append("colunas de nome SIGTAP do arquivo estavam corrompidas (um só nome para todos os códigos: "
                      + ", ".join(removidas) + ") e foram descartadas; os nomes são refeitos a partir do SIGTAP em cache ou dos dicionários embutidos")
    return aih, avisos


def enriquecer_sigtap(aih: pl.DataFrame, tabela: pl.DataFrame | None = None) -> tuple[pl.DataFrame, str]:
    """Acrescenta os nomes SIGTAP que faltarem (grupo, subgrupo, forma, procedimento…). Devolve (df, aviso)."""
    proc = next((c for c in ("PROC_REA", "CO Procedimentos realizados", "SP_PROCREA", "PA_PROC_ID") if c in aih.columns), None)
    if proc is None:
        return aih, "sem coluna de procedimento"
    faltam = [c for c in _COLS_SIGTAP if c not in aih.columns]
    if not faltam:
        return aih, "nomes SIGTAP já presentes no arquivo"
    tab = tabela if tabela is not None else tabela_sigtap()
    if tab is None:
        return aih, "SIGTAP não está em cache no app: grupo/subgrupo pelos dicionários embutidos; forma e procedimento só com o código"
    tab = tab.select(["CO_PROCEDIMENTO"] + [c for c in faltam if c in tab.columns])
    out = aih.with_columns(pl.col(proc).cast(pl.Utf8, strict=False).str.zfill(10).alias("_p10")) \
             .join(tab, left_on="_p10", right_on="CO_PROCEDIMENTO", how="left").drop("_p10")
    return out, f"nomes SIGTAP do cache do app: {', '.join(c for c in faltam if c in tab.columns)}"


# ---------------------------------------------------------------------------
# 5. Fonte (pasta ou arquivo) → tudo que as telas precisam
# ---------------------------------------------------------------------------

def ler_fonte(caminho: str | Path, progresso: Progresso | None = None, *, etapa: Etapa | None = None,
              cancelado: Cancelado | None = None, selecao: Selecao | None = None) -> tuple[pl.DataFrame, str]:
    """Pasta (RD*/CIHA* ou parquet/csv) ou arquivo → (aih, descrição da origem). Com `selecao`, só os arquivos
    da pasta que ela pede (UFs × competências × fontes); os que faltam são listados no progresso."""
    dizer = progresso or (lambda s: None)
    p = Path(caminho)
    if p.is_dir():
        a = listar_arquivos(p)
        if selecao is not None and (a["rd"] or a["ciha"]):
            achados, faltam = arquivos_da_selecao(p, selecao)
            dizer(f"-- pasta {p}: {descrever_pasta(p)}")
            dizer(f"-- seleção {selecao.rotulo()}: {len(achados)} arquivo(s) na pasta" + (f"; FALTAM {len(faltam)}: " + ", ".join(nome_arquivo(*x) for x in faltam[:12]) + (" …" if len(faltam) > 12 else "") if faltam else ""))
            if not achados:
                raise FileNotFoundError(f"nenhum arquivo da seleção {selecao.rotulo()} está em {p} — baixe-os do DATASUS")
            return ler_arquivos(achados, progresso=progresso, etapa=etapa, cancelado=cancelado), f"{len(achados)} arquivos · {selecao.rotulo()}"
        if a["rd"] or a["ciha"]:
            dizer(f"-- pasta {p}: {descrever_pasta(p)}")
            rot = " + ".join(x for x in (f"{len(a['rd'])} RD" if a["rd"] else "", f"{len(a['ciha'])} CIHA" if a["ciha"] else "") if x)
            return ler_pasta_rd(p, progresso=progresso, etapa=etapa, cancelado=cancelado), f"{rot} de {p.name}"
        if a["tabular"]:
            f = a["tabular"][0]
            dizer(f"-- pasta informada sem arquivos RD/CIHA; usando o parquet/csv mais recente: {f.name}")
            p = f
        else:
            raise FileNotFoundError(f"nenhum arquivo RD/CIHA (RDUFAAMM / CIHAUFAAMM .dbc/.dbf) nem parquet/csv em {p}")
    if not p.exists():
        raise FileNotFoundError(str(p))
    t = time.time()
    aih, n_cols = ler_arquivo(p)
    dizer(f"-- {p.name}: {aih.height:,} linhas; {aih.width} de {n_cols} colunas lidas ({time.time() - t:.1f} s)".replace(",", "."))
    return aih, p.name


def carregar_fonte(caminho: str | Path, *, uf: str | None = None, sem_ibge: bool = False,
                   progresso: Progresso | None = None, etapa: Etapa | None = None,
                   cancelado: Cancelado | None = None, selecao: Selecao | None = None) -> Carga:
    """Lê a fonte, carrega a base do CNES, a população e monta hospitais/municípios das telas.
    Com `selecao`, lê só os arquivos pedidos e lista os hospitais de TODAS as UFs da seleção."""
    from papahr.dashboard.atratividade import hospitais_da_uf, insumos_do_app, meses_competencia
    from papahr.dashboard.cnes_loader import get_cnes_data

    dizer = progresso or (lambda s: None)
    avisos: list[str] = []
    aih, origem = ler_fonte(caminho, progresso, etapa=etapa, cancelado=cancelado, selecao=selecao)
    if selecao is not None:
        comps = selecao.competencias()
        if "ANOMES_CMPT" in aih.columns and comps:
            antes = aih.height
            aih = aih.filter(pl.col("ANOMES_CMPT").is_in(comps))
            if aih.height != antes:
                dizer(f"-- {antes - aih.height:,} linhas fora do período {comps[0]}–{comps[-1]} descartadas".replace(",", "."))
    if cancelado and cancelado():
        raise InterruptedError("carga cancelada")
    col = next((c for c in ("CNES", "CO_CNES", "_cnes") if c in aih.columns), None)
    if col is None:
        raise ValueError(f"os dados não têm coluna CNES (colunas: {aih.columns[:15]}…)")
    aih, av_san = sanear_nomes_sigtap(aih)
    for a in av_san:
        dizer(f"-- AVISO: {a}")
    avisos += av_san
    aih, aviso = enriquecer_sigtap(aih)
    dizer(f"-- {aviso}")
    if "SIGTAP não está em cache" in aviso:
        avisos.append(aviso)
    if "FONTE" not in aih.columns:
        aih = aih.with_columns(pl.lit("SIH").alias("FONTE"))
    por_fonte = {r["FONTE"]: r["n"] for r in aih.group_by("FONTE").agg(pl.len().alias("n")).to_dicts()}
    dizer("-- fontes: " + " · ".join(f"{k} {v:,}".replace(",", ".") for k, v in sorted(por_fonte.items())))
    if "CIHA" in por_fonte:
        ciha = aih.filter(pl.col("FONTE") == "CIHA")
        for c in ("MODALIDADE", "PAGADOR", "CAR_INT"):
            if c in ciha.columns:
                top = ciha.group_by(c).agg(pl.len().alias("n")).sort("n", descending=True).head(8).to_dicts()
                dizer(f"   CIHA · {c}: " + ", ".join(f"{r[c]!r}={r['n']:,}".replace(",", ".") for r in top))
        faltam = [c for c in ("CNES", "MUNIC_RES", "PROC_REA", "DIAG_PRINC", "SEXO", "IDADE", "ANO_CMPT") if c not in ciha.columns or ciha[c].null_count() == ciha.height]
        if faltam:
            avisos.append("CIHA: colunas não reconhecidas no layout desses arquivos: " + ", ".join(faltam)
                          + " — use --inspecionar ARQUIVO.dbc e me envie a lista de colunas para ajustar o mapeamento")
    cm_mov = "MUNIC_MOV" if "MUNIC_MOV" in aih.columns else None
    meses = meses_competencia(aih)
    dizer(f"-- competências: {meses}; municípios dos hospitais (MUNIC_MOV): {aih[cm_mov].n_unique() if cm_mov else 0}")
    if meses < 12:
        avisos.append(f"{meses} competência(s): as taxas são anualizadas (× 12/{meses}) e sofrem sazonalidade")

    dizer("-- carregando a base do CNES…")
    cnes = get_cnes_data().load()
    if cnes.estabelecimentos.is_empty():
        raise RuntimeError("base do CNES não encontrada (C:\\DICIONARIO_DADOS\\BASE_DE_DADOS_CNES_*). Baixe-a no Wizard do app.")
    todos = aih[col].cast(pl.Utf8).str.strip_chars().str.zfill(7).unique().to_list()
    from papahr.dashboard.atratividade import UF_SIGLA
    ufs: list[str] = []
    if selecao is not None and selecao.ufs:
        ufs = [UF_SIGLA.get(str(u).strip().zfill(2), str(u).strip().upper()) for u in selecao.ufs]
    elif uf:
        ufs = [UF_SIGLA.get(str(uf).strip().zfill(2), str(uf).strip().upper())]
    else:
        cm = next((c for c in ("MUNIC_MOV", "MUNIC_RES") if c in aih.columns), None)
        if cm:
            u = aih[cm].cast(pl.Utf8).str.zfill(6).str.slice(0, 2).mode().item()
            ufs = [UF_SIGLA.get(str(u).zfill(2), str(u))]
    uf = "/".join(ufs) if ufs else None
    pop = None
    if not sem_ibge:
        from papahr.dashboard.populacao import carregar_populacao, descrever_fonte
        dizer("-- população: IBGE/SIDRA (na primeira vez baixa ~1 MB; depois usa o cache)…")
        try:
            pop = carregar_populacao()
            dizer(f"   {descrever_fonte(pop)}")
        except Exception as e:  # noqa: BLE001
            avisos.append(f"população do IBGE indisponível ({type(e).__name__}); usando a do CNES")
            dizer(f"   {avisos[-1]}")
    _, mun = insumos_do_app(pl.DataFrame(), cnes.municipios, populacao=pop)
    hosp = pl.concat([hospitais_da_uf(cnes.estabelecimentos, u, aih, municipios=mun) for u in ufs], how="diagonal_relaxed") if ufs else pl.DataFrame()
    if not hosp.is_empty():
        hosp = hosp.unique(subset=["CNES"], keep="first", maintain_order=True).sort(["n", "NOME"], descending=[True, False])
    n_cnes = int(hosp.filter(pl.col("COORD") == "CNES").height) if not hosp.is_empty() else 0
    n_apr = int(hosp.filter(pl.col("COORD") == "centroide do município").height) if not hosp.is_empty() else 0
    n_com_aih = int(hosp.filter(pl.col("n") > 0).height) if not hosp.is_empty() else 0
    n_aih = f"{aih.height:,}".replace(",", ".")
    desc = (f"UF {uf}: {hosp.height} hospitais na base do CNES; {len(todos)} CNES com AIH nos dados "
            f"({n_com_aih} deles hospitais da UF); coordenadas: {n_cnes} do CNES, {n_apr} pelo centroide do município, "
            f"{hosp.height - n_cnes - n_apr} sem nenhuma (ficam de fora); {n_aih} AIH em {meses} competência(s)")
    dizer(f"-- {desc}")
    return Carga(aih=aih, hospitais=hosp, municipios=mun, uf=uf, descricao=desc, origem=origem, avisos=avisos)
