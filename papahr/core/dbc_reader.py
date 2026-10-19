"""Leitor de arquivos DBC (formato comprimido DATASUS, DBF + PKWARE DCL).

O formato DBC do DATASUS é um DBF em que:
 1. os primeiros `header_size` bytes (lidos em [8:10], uint16 LE) são o
    CABEÇALHO do DBF gravado SEM compressão;
 2. seguem 4 bytes de CRC32 (ignorados);
 3. o restante é o CORPO comprimido com o algoritmo PKWARE DCL ("implode",
    também chamado de BLAST), terminado por um código de fim de stream.

A descompressão é feita 100% em Python puro, sem DLL nativa — o que a torna
imune a bloqueios de política de Controle de Aplicativo (WDAC) do Windows.
Duas implementações equivalentes, com fallback entre elas:
 1. `dbc-to-dbf` (PyPI, licença zlib) — biblioteca externa, port de blast.c;
 2. `_BlastState` — port fiel interno de blast.c (usado se a lib faltar).
Ambas produzem saída idêntica byte a byte (ver `_critico_dbc_reader.py` teste D).

Referências:
 - blast.c (Mark Adler): https://github.com/madler/zlib/blob/master/contrib/blast/blast.c
 - dbc2dbf.c (Daniela Petruzalek, pacote R read.dbc)
 - dbc-to-dbf: https://github.com/mozaru/dbc-to-dbf
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Union

import polars as pl
from dbfread import DBF
from tenacity import retry, stop_after_attempt, wait_exponential

from papahr.logging_config import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Descompressor PKWARE DCL ("blast"/implode) — port fiel de blast.c
#
# O formato é: 1 byte (0 = literais sem código, 1 = literais com código
# Huffman), 1 byte (log2 do dicionário - 6, em 4..6), e então uma sequência de
# literais e pares comprimento/distância, terminada por um código de fim.
# Bits são lidos do menos significativo para o mais significativo.
# ---------------------------------------------------------------------------

_MAXBITS = 13

# base/extra bits por código de comprimento (símbolos 0..15)
_BASE = (3, 2, 4, 5, 6, 7, 8, 9, 10, 12, 16, 24, 40, 72, 136, 264)
_EXTRA = (0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8)

# Códigos comprimidos por "repeat count" (nibble alto = repetições - 1,
# nibble baixo = comprimento). Arrays idênticos aos de blast.c.
_LITLEN = bytes((
    11, 124, 8, 7, 28, 7, 188, 13, 76, 4, 10, 8, 12, 10, 12, 10, 8, 23, 8,
    9, 7, 6, 7, 8, 7, 6, 55, 8, 23, 24, 12, 11, 7, 9, 11, 12, 6, 7, 22, 5,
    7, 24, 6, 11, 9, 6, 7, 22, 7, 11, 38, 7, 9, 8, 25, 11, 8, 11, 9, 12,
    8, 12, 5, 38, 5, 38, 5, 11, 7, 5, 6, 21, 6, 10, 53, 8, 7, 24, 10, 27,
    44, 253, 253, 253, 252, 252, 252, 13, 12, 45, 12, 45, 12, 61, 12, 45,
    44, 173,
))
_LENLEN = bytes((2, 35, 36, 53, 38, 23))
_DISTLEN = bytes((2, 20, 53, 230, 247, 151, 248))


def _construct(rep: bytes, n: int) -> tuple[list[int], list[int]]:
    """Reconstrói a tabela de Huffman canônica (port de construct() do blast.c).

    Returns:
        (count, symbol): count[1..MAXBITS] = nº de códigos de cada comprimento;
        symbol = símbolos em ordem canônica (estáveis dentro de cada comprimento).
    """
    lengths: list[int] = []
    for i in range(n):
        b = rep[i]
        rep_count = (b >> 4) + 1
        length = b & 15
        lengths.extend([length] * rep_count)
    sym_count = len(lengths)

    count = [0] * (_MAXBITS + 1)
    for length in lengths:
        count[length] += 1
    if count[0] == sym_count:  # nenhum código
        return count, []

    offs = [0] * (_MAXBITS + 1)
    for length in range(1, _MAXBITS):
        offs[length + 1] = offs[length] + count[length]

    symbols = [0] * sym_count
    for symbol in range(sym_count):
        length = lengths[symbol]
        if length != 0:
            symbols[offs[length]] = symbol
            offs[length] += 1
    return count, symbols


_LITCODE = _construct(_LITLEN, len(_LITLEN))
_LENCODE = _construct(_LENLEN, len(_LENLEN))
_DISTCODE = _construct(_DISTLEN, len(_DISTLEN))


class _BlastState:
    """Estado de descompressão: leitor de bits LSB-first + tabelas Huffman."""

    __slots__ = ("_data", "_pos", "_bitbuf", "_bitcnt")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0
        self._bitbuf = 0
        self._bitcnt = 0

    def bits(self, need: int) -> int:
        """Lê `need` bits do stream (port de bits() do blast.c)."""
        val = self._bitbuf
        while self._bitcnt < need:
            if self._pos >= len(self._data):
                raise EOFError("Fim inesperado do stream BLAST")
            val |= self._data[self._pos] << self._bitcnt
            self._pos += 1
            self._bitcnt += 8
        self._bitbuf = val >> need
        self._bitcnt -= need
        return val & ((1 << need) - 1)

    def decode(self, h: tuple[list[int], list[int]]) -> int:
        """Decodifica um símbolo (port de decode() do blast.c)."""
        count_tbl, symbol_tbl = h
        bitbuf = self._bitbuf
        left = self._bitcnt
        code = 0
        first = 0
        index = 0
        length = 1
        nxt = 1  # C: h->count + 1
        while True:
            while left > 0:
                left -= 1
                code |= (bitbuf & 1) ^ 1  # código vem invertido no stream
                bitbuf >>= 1
                cnt = count_tbl[nxt]
                nxt += 1
                if code < first + cnt:
                    self._bitbuf = bitbuf
                    self._bitcnt = (self._bitcnt - length) & 7
                    return symbol_tbl[index + (code - first)]
                index += cnt
                first += cnt
                first <<= 1
                code <<= 1
                length += 1
            left = (_MAXBITS + 1) - length
            if left == 0:
                break
            if self._pos >= len(self._data):
                raise EOFError("Fim inesperado do stream BLAST (decode)")
            bitbuf = self._data[self._pos]
            self._pos += 1
            if left > 8:
                left = 8
        return -9

    def decompress(self) -> bytes:
        """Descomprime o stream PKWARE DCL (port de decomp() do blast.c)."""
        lit = self.bits(8)
        if lit > 1:
            raise ValueError("Formato BLAST inválido: flag de literal não é 0/1")
        dic = self.bits(8)
        if dic < 4 or dic > 6:
            raise ValueError(f"Formato BLAST inválido: tamanho de dicionário {dic} fora de 4..6")

        out = bytearray()
        while True:
            if self.bits(1):
                # par comprimento/distância
                symbol = self.decode(_LENCODE)
                if symbol < 0:
                    raise ValueError("Código de comprimento inválido no stream BLAST")
                length = _BASE[symbol] + self.bits(_EXTRA[symbol])
                if length == 519:  # código de fim de stream
                    break
                sym_bits = 2 if length == 2 else dic
                dist = self.decode(_DISTCODE)
                if dist < 0:
                    raise ValueError("Código de distância inválido no stream BLAST")
                dist = (dist << sym_bits) + self.bits(sym_bits) + 1
                if dist > len(out):
                    raise ValueError("Formato BLAST inválido: distância além do início da saída")
                start = len(out) - dist
                if dist >= length:
                    out += out[start:start + length]
                else:
                    # cópia sobreposta: distancia < comprimento (repite o padrão)
                    for i in range(length):
                        out.append(out[start + i])
            else:
                # literal
                if lit:
                    symbol = self.decode(_LITCODE)
                    if symbol < 0:
                        raise ValueError("Literal inválido no stream BLAST")
                else:
                    symbol = self.bits(8)
                out.append(symbol)
        return bytes(out)


def blast_decompress(compressed: bytes) -> bytes:
    """Descomprime um stream PKWARE DCL/BLAST e retorna os bytes descompactados."""
    return _BlastState(compressed).decompress()


# ---------------------------------------------------------------------------
# DBC -> DBF -> Polars DataFrame
# ---------------------------------------------------------------------------

_DBF_HEADER_SIZE = 32


def _dbc_dbf_header_size(raw: bytes) -> int:
    """Lê em [8:10] (uint16 LE) o tamanho do CABEÇALHO do DBF embutido no DBC.

    Os primeiros `header_size` bytes do DBC são o cabeçalho do DBF gravado sem
    compressão; seguem 4 bytes de CRC32 e então o corpo comprimido. (Fonte:
    dbc2dbf.c / read.dbc — HEADER_OFFSET=8, CRC_OFFSET=4.)
    """
    if len(raw) < 12:
        raise ValueError("Arquivo DBC muito pequeno")
    header_size = struct.unpack("<H", raw[8:10])[0]
    if header_size < _DBF_HEADER_SIZE or header_size + 4 > len(raw):
        raise ValueError(f"Cabeçalho DBC inválido: header_size={header_size}")
    return header_size


def _dbc_body_to_dbf(raw: bytes) -> bytes:
    """Converte bytes de um .dbc COMPRIMIDO em bytes de um .dbf puro.

    Ordem de tentativa:
      1. Biblioteca externa `dbc-to-dbf` (PyPI, Python puro, zlib) — port de
         blast.c mantido por terceiros;
      2. BLAST interno (`_BlastState`) — port fiel de blast.c, usado como
         fallback se a biblioteca não estiver instalada.
    Ambas produzem saída idêntica byte a byte (evidência: `_critico_dbc_reader.py`,
    teste D). Se o stream estiver corrompido, ambas falham (fail-fast) — o
    fallback NÃO mascara erro de dados.
    """
    try:
        from dbctodbf import DBCDecompress

        out = DBCDecompress().decompress(raw)
        log.debug("DBC descompactado via 'dbc-to-dbf' (externo)", out_len=len(out))
        return out
    except ImportError:
        log.debug("Biblioteca 'dbc-to-dbf' ausente; usando BLAST interno")
    except Exception as e:  # noqa: BLE001
        log.debug("'dbc-to-dbf' falhou; tentando BLAST interno", err=str(e))

    header_size = _dbc_dbf_header_size(raw)
    dbf_header = raw[:header_size]                 # cabeçalho DBF sem compressão
    # raw[header_size : header_size + 4] = CRC32 (ignorado)
    out = dbf_header + blast_decompress(raw[header_size + 4:])
    log.debug("DBC descompactado via BLAST interno (blast.c)", out_len=len(out))
    return out


def _looks_like_dbf(raw: bytes) -> bool:
    """Valida estruturalmente se os bytes são um DBF puro.

    NÃO confia apenas no primeiro byte (magic): um .dbc COMPRIMIDO do DATASUS
    começa com 0x03 por coincidência (é o próprio magic do cabeçalho do DBF
    embutido). Valida a coerência do header: bytes 4:8 = nº de registros
    (uint32 LE), 8:10 = tamanho do header, 10:12 = tamanho do registro; exige
    n_rec > 0, header_size > 0, record_len > 0 e tamanho do arquivo coerente
    com header_size + n_rec*record_len (tolerância de ~2 bytes para o marcador
    EOF 0x1A).
    """
    if len(raw) < _DBF_HEADER_SIZE:
        return False
    if raw[0] not in (0x03, 0x83, 0x8B, 0x30):
        return False
    n_rec = struct.unpack("<I", raw[4:8])[0]
    header_size = struct.unpack("<H", raw[8:10])[0]
    record_len = struct.unpack("<H", raw[10:12])[0]
    if n_rec <= 0 or header_size <= 0 or record_len <= 0:
        return False
    expected = header_size + n_rec * record_len
    return abs(expected - len(raw)) <= 2


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4))
def read_dbc(path: Union[str, Path], *, encoding: str = "latin-1", use_threads: bool = True) -> pl.DataFrame:
    """Lê um arquivo DBC (.dbc comprimido ou .dbf puro) e retorna Polars DataFrame.

    Args:
        path: Caminho para o arquivo
        encoding: Codificação do DBF (padrão latin-1 para DATASUS)
        use_threads: Paraleliza construção do DataFrame

    Returns:
        Polars DataFrame com os dados brutos do DBF.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)

    # 1. Tenta descompactação nativa e ultrarrápida via `datasus_dbc` (DLL)
    tmp_dbf_path = None
    if p.suffix.lower() == ".dbc":
        try:
            import datasus_dbc
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".dbf", delete=False) as tmp:
                tmp_dbf_path = Path(tmp.name)
            datasus_dbc.decompress(str(p), str(tmp_dbf_path))

            dbf = DBF(str(tmp_dbf_path), encoding=encoding, char_decode_errors="replace", load=True)
            rows = list(dbf.records)
            if not rows:
                raise RuntimeError(
                    f"Descompactacao nativa ('datasus_dbc') resultou em 0 registros: "
                    f"{p} (size={p.stat().st_size} bytes)"
                )
            return pl.DataFrame(rows)
        except Exception as e:
            log.debug("datasus_dbc indisponivel; usando descompressor Python puro", err=str(e))
        finally:
            if tmp_dbf_path and tmp_dbf_path.exists():
                try:
                    tmp_dbf_path.unlink()
                except Exception:
                    pass

    raw = p.read_bytes()
    if len(raw) < 10:
        raise ValueError(f"Arquivo vazio ou corrompido: {p}")

    # Decide o formato pela EXTENSAO do arquivo (nao pelo magic 0x03): um .dbc
    # COMPRIMIDO do DATASUS tambem comeca com 0x03 e seria misdetectado como DBF.
    suffix = p.suffix.lower()
    if suffix == ".dbf":
        is_dbf = True
    elif suffix == ".dbc":
        is_dbf = False
    else:
        is_dbf = _looks_like_dbf(raw)
    code_path = "dbf" if is_dbf else "blast"
    if is_dbf:
        log.debug("Arquivo detectado como DBF (nao compactado)", path=p.name)
        dbf_bytes = raw
    else:
        log.debug("Descompactando DBC (Python puro)", path=p.name, size=len(raw))
        dbf_bytes = _dbc_body_to_dbf(raw)
        if len(dbf_bytes) < _DBF_HEADER_SIZE or dbf_bytes[0] not in (0x03, 0x83, 0x8B, 0x30):
            raise RuntimeError(f"Falha ao descompactar DBC: resultado nao e um DBF valido ({p})")

    # Salva temporariamente em disco para dbfread ler sem erro de BytesIO
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".dbf", delete=False) as tmp:
        tmp.write(dbf_bytes)
        tmp_path = Path(tmp.name)
    try:
        dbf = DBF(str(tmp_path), encoding=encoding, char_decode_errors="replace", load=True)
        rows = list(dbf.records)
        if not rows:
            raise RuntimeError(
                f"Leitura resultou em 0 registros (caminho={code_path}, "
                f"arquivo={p}, size={len(raw)} bytes)."
            )
        return pl.DataFrame(rows)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


def read_many_dbc(paths: list[Union[str, Path]], *, encoding: str = "latin-1") -> pl.DataFrame:
    """Lê múltiplos DBC e concatena em único DataFrame.

    Falha explicitamente (RuntimeError) se NENHUM registro for lido, agregando os
    motivos por arquivo — em vez de retornar um DataFrame vazio em silêncio.
    """
    if not paths:
        return pl.DataFrame()
    frames = []
    errors: list[str] = []
    for p in paths:
        try:
            df = read_dbc(p, encoding=encoding)
            if df.height > 0:
                frames.append(df)
            else:
                errors.append(f"{p}: 0 linhas")
        except Exception as e:
            log.error("Falha ao ler DBC", path=str(p), err=str(e))
            errors.append(f"{p}: {type(e).__name__}: {e}")
    if not frames:
        raise RuntimeError(
            f"Nenhum registro lido de {len(paths)} arquivo(s) DBC. "
            "Motivos: " + " | ".join(errors)
        )
    return pl.concat(frames, how="diagonal_relaxed")
