# -*- coding: utf-8 -*-
"""Teste critico do leitor de DBC (GSPHosp) — prova o comportamento do fix H11.

Objetivo: provar que
  A) um buffer que finge ser um DBC COMPRIMIDO cujo 1o byte e 0x03 (mesmo magic
     de DBF) NAO e classificado como DBF pelo novo helper estrutural;
  B) um header DBF sintetico coerente (n_rec>0, header_size/record_len validos e
     tamanho de arquivo coerente) E classificado como DBF;
  C) ler um arquivo .dbc REAL do DATASUS e reportar quantas linhas foram lidas e
     por qual caminho de codigo (nativo `datasus_dbc` ou BLAST puro em Python).
     Se o BLAST puro estiver quebrado, o teste FALHA de forma explicita — sem
     mascarar o resultado e sem inventar dados.

Uso (a partir de d:\\papapapa\\GSPHosp-Novo):
    $env:PYTHONIOENCODING="utf-8"
    & .\\.venv\\Scripts\\python.exe _critico_dbc_reader.py
"""
from __future__ import annotations

import glob
import os
import struct
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

_RESULTS: list[tuple[str, bool, str]] = []
_MISSING = object()


def rec(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


# --------------------------------------------------------------------------- A
def test_a():
    """DBC comprimido com 1o byte 0x03 NAO pode ser classificado como DBF."""
    try:
        from papahr.core.dbc_reader import _looks_like_dbf

        # Buffer sintetico cujo header imita o RDAC2501.dbc real (1o byte 0x03).
        raw = bytes.fromhex("037e0206bd110000410ebe02") + b"\x00" * 300
        first_byte_is_magic = raw[0] in (0x03, 0x83, 0x8B, 0x30)
        novo = _looks_like_dbf(raw)
        ok = (first_byte_is_magic is True) and (novo is False)
        rec(
            "A-dbc-comprimido-0x03",
            ok,
            f"len={len(raw)} raw[0]=0x{raw[0]:02x} "
            f"magic_antigo_diria_dbf={first_byte_is_magic} novo_detector_dbf={novo}",
        )
    except Exception as e:  # noqa: BLE001
        rec("A-dbc-comprimido-0x03", False, f"excecao: {type(e).__name__}: {e}")


# --------------------------------------------------------------------------- B
def test_b():
    """Header DBF sintetico coerente DEVE ser classificado como DBF."""
    try:
        from papahr.core.dbc_reader import _looks_like_dbf

        n_rec = 3
        header_size = 32 + 1 * 32 + 1  # 1 campo + terminador do header
        record_len = 10
        body_size = header_size + n_rec * record_len  # 95

        buf = bytearray(body_size + 1)  # + marcador EOF 0x1A
        buf[0] = 0x03
        buf[4:8] = struct.pack("<I", n_rec)
        buf[8:10] = struct.pack("<H", header_size)
        buf[10:12] = struct.pack("<H", record_len)
        buf[body_size] = 0x1A

        com_eof = _looks_like_dbf(bytes(buf))
        sem_eof = _looks_like_dbf(bytes(buf[:body_size]))  # tolerancia ~2 bytes
        incoerente = _looks_like_dbf(bytes(buf) + b"\x00" * 100)  # tamanho nao bate
        ok = (com_eof is True) and (sem_eof is True) and (incoerente is False)
        rec(
            "B-dbf-coerente",
            ok,
            f"n_rec={n_rec} hdr={header_size} reclen={record_len} size={len(buf)} "
            f"com_eof={com_eof} sem_eof={sem_eof} incoerente={incoerente}",
        )
    except Exception as e:  # noqa: BLE001
        rec("B-dbf-coerente", False, f"excecao: {type(e).__name__}: {e}")


# --------------------------------------------------------------------------- C
def _find_real_dbc() -> Path | None:
    """Procura RDAC2501.dbc em %TEMP%, pasta do projeto, output e cache."""
    roots = [
        tempfile.gettempdir(),
        str(BASE),
        str(BASE / "output"),
        str(BASE / "cache"),
        str(BASE / "data"),
    ]
    hits: list[str] = []
    for root in roots:
        if root and os.path.isdir(root):
            hits += glob.glob(os.path.join(root, "**", "*rdac2501*"), recursive=True)
            hits += glob.glob(os.path.join(root, "**", "RDAC2501*"), recursive=True)
    if hits:
        # maior arquivo (evita stubs/parciais)
        return Path(max(hits, key=lambda p: os.path.getsize(p)))
    return None


def test_c():
    """Ler um .dbc REAL do DATASUS e reportar linhas/caminho. BLAST quebrado => FAIL."""
    from papahr.core.dbc_reader import read_dbc

    # 1) Localizar (ou baixar) o arquivo real
    p = _find_real_dbc()
    if p is None:
        try:
            from papahr.core.ftp_client import BASE_SIASIH, FTPClient

            dest = Path(tempfile.gettempdir()) / "rdac2501.dbc"
            url = BASE_SIASIH.format(src="SIH") + "RDAC2501.dbc"
            print(f"  baixando {url} -> {dest}")
            p = FTPClient().download_ftp(url, dest)
        except Exception as e:  # noqa: BLE001
            rec("C-dbc-real", False, f"nao consegui obter RDAC2501.dbc: {type(e).__name__}: {e}")
            return

    size = p.stat().st_size
    # 2) Qual caminho esta disponivel?
    nativo_ok = False
    nativo_err = ""
    try:
        import datasus_dbc  # noqa: F401

        nativo_ok = True
    except Exception as e:  # noqa: BLE001
        nativo_err = f"{type(e).__name__}: {e}"
    externo_ok = False
    try:
        import dbctodbf  # noqa: F401

        externo_ok = True
    except Exception:  # noqa: BLE001
        pass
    caminho = (
        "nativo datasus_dbc"
        if nativo_ok
        else ("externo dbc-to-dbf" if externo_ok else "BLAST interno")
    )

    # 3) Ler de verdade
    try:
        df = read_dbc(str(p))
        rows = df.height
        ncols = df.width
        rec(
            "C-dbc-real",
            rows > 0,
            f"arquivo={p} size={size} caminho={caminho} rows={rows} cols={ncols}",
        )
    except Exception as e:  # noqa: BLE001
        # Desembrulha RetryError (tenacity) para exibir o motivo real da falha.
        causa = e
        try:
            from tenacity import RetryError

            if isinstance(e, RetryError):
                causa = e.last_attempt.exception() or e
        except Exception:  # noqa: BLE001
            pass
        det = f"arquivo={p} size={size} caminho={caminho} rows=0"
        if nativo_err:
            det += f" nativo_indisponivel=({nativo_err})"
        det += f" ERRO={type(causa).__name__}: {causa}"
        rec("C-dbc-real", False, det)


# --------------------------------------------------------------------------- D
def test_d():
    """Cross-valida o descompactador EXTERNO (dbc-to-dbf, PyPI) contra o BLAST
    interno no arquivo REAL: ambos devem produzir o MESMO .dbf, byte a byte.
    Mede tambem o byte terminal do header (0x0D vs outro) para explicar diferencas.
    """
    from papahr.core.dbc_reader import _dbc_dbf_header_size, blast_decompress

    p = _find_real_dbc()
    if p is None:
        rec("D-cross-validacao", False, "RDAC2501.dbc nao encontrado")
        return
    raw = p.read_bytes()
    try:
        from dbctodbf import DBCDecompress
    except Exception as e:  # noqa: BLE001
        rec("D-cross-validacao", False, f"dbc-to-dbf ausente: {type(e).__name__}: {e}")
        return

    try:
        externo = DBCDecompress().decompress(raw)
        hdr = _dbc_dbf_header_size(raw)
        interno = raw[:hdr] + blast_decompress(raw[hdr + 4:])
        igual = externo == interno
        det = (
            f"externo={len(externo)}b interno={len(interno)}b iguais={igual} "
            f"hdr={hdr} hdr[-1]=0x{raw[hdr - 1]:02x}"
        )
        if not igual:
            n = min(len(externo), len(interno))
            dif = next((i for i in range(n) if externo[i] != interno[i]), n)
            lim = min(dif, hdr)
            det += (
                f" primeiro_diff_idx={dif} "
                f"(header? {'sim' if lim < hdr else 'nao'}) "
                f"ext[0:4]={externo[:4].hex()} int[0:4]={interno[:4].hex()}"
            )
        rec("D-cross-validacao", igual, det)
    except Exception as e:  # noqa: BLE001
        rec("D-cross-validacao", False, f"excecao: {type(e).__name__}: {e}")


# --------------------------------------------------------------------------- E
def test_e():
    """`read_dbc` deve ler o .dbc REAL com o mesmo DataFrame, seja qual for o
    descompactador que atenda (externo dbc-to-dbf ou BLAST interno)."""
    from papahr.core.dbc_reader import read_dbc

    p = _find_real_dbc()
    if p is None:
        rec("E-read-dbc-real", False, "RDAC2501.dbc nao encontrado")
        return
    try:
        df = read_dbc(str(p))
        # coerencia: linhas x colunas batem com o header DBF embutido
        raw = p.read_bytes()
        import struct as _s

        n_rec = _s.unpack("<I", raw[4:8])[0]
        ok = df.height == n_rec and df.width == 113
        rec(
            "E-read-dbc-real",
            ok,
            f"rows={df.height} (header={n_rec}) cols={df.width}",
        )
    except Exception as e:  # noqa: BLE001
        rec("E-read-dbc-real", False, f"excecao: {type(e).__name__}: {e}")


# --------------------------------------------------------------------------- F
def test_f():
    """Fallback: com 'dbc-to-dbf' indisponivel, `_dbc_body_to_dbf` deve usar o
    BLAST interno e entregar EXATAMENTE o mesmo .dbf."""
    import sys as _sys

    import papahr.core.dbc_reader as dr

    p = _find_real_dbc()
    if p is None:
        rec("F-fallback-interno", False, "RDAC2501.dbc nao encontrado")
        return
    raw = p.read_bytes()
    hdr = dr._dbc_dbf_header_size(raw)
    esperado = raw[:hdr] + dr.blast_decompress(raw[hdr + 4:])

    saved = _sys.modules.get("dbctodbf", _MISSING)
    _sys.modules["dbctodbf"] = None  # força ImportError no 'from dbctodbf import ...'
    try:
        obtido = dr._dbc_body_to_dbf(raw)
    finally:
        if saved is _MISSING:
            _sys.modules.pop("dbctodbf", None)
        else:
            _sys.modules["dbctodbf"] = saved

    ok = obtido == esperado
    rec("F-fallback-interno", ok, f"len={len(obtido)} igual_interno={ok} (lib bloqueada)")


def main() -> int:
    print("=" * 70)
    print("CRITICO DBC READER — GSPHosp (fix H11: roteamento por extensao + fail-fast)")
    print("=" * 70)
    test_a()
    test_b()
    test_c()
    test_d()
    test_e()
    test_f()
    n_pass = sum(1 for _, ok, _ in _RESULTS if ok)
    n_fail = sum(1 for _, ok, _ in _RESULTS if not ok)
    print()
    print("=" * 70)
    print(f"CRITICO_DBC: {n_pass} PASS / {n_fail} FAIL")
    print("=" * 70)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
