"""Corrige papahr/datasources/sigtap.py (app GSPHosp): nomes de grupo/subgrupo/forma por procedimento.

O defeito
    `SIGTAPRepository.get_procedure_details()` junta tb_procedimento com tb_grupo, tb_sub_grupo e
    tb_forma_organizacao usando como chave as colunas CO_GRUPO / CO_SUB_GRUPO / CO_FORMA_ORGANIZACAO —
    que NÃO existem em tb_procedimento (o layout oficial só tem CO_PROCEDIMENTO, NO_PROCEDIMENTO,
    TP_COMPLEXIDADE, …). Os joins caem então só em `file_version_id`, viram produto cartesiano e o
    `unique(keep="last")` final dá a TODOS os procedimentos o mesmo (último) grupo, subgrupo e forma.
    Efeito visível: toda AIH com NO_SUB_GRUPO = "Atenção em Nefrologia" (ou outro nome único), nos
    parquet/csv do pipeline e em qualquer análise por grupo/subgrupo do Dashboard.

A correção
    Derivar os três códigos de CO_PROCEDIMENTO (dígitos 1–2, 3–4, 5–6) ANTES dos joins. O bloco
    "Derivações" que já existe depois dos joins fica sem efeito (as colunas já existem).

Uso (na raiz do projeto, com o venv):
    python scripts\\corrigir_sigtap_app.py            # aplica (guarda cópia .bak-AAAAMMDD-HHMM)
    python scripts\\corrigir_sigtap_app.py --ver      # só mostra se o arquivo precisa da correção
Depois: regenerar os parquet/csv do pipeline (os já gerados têm os nomes errados). As telas de
atratividade não dependem disto — montam os nomes por conta própria (importar_rd.tabela_sigtap).
"""
from __future__ import annotations

import py_compile
import sys
import time
from pathlib import Path

ALVO = Path("papahr/datasources/sigtap.py")
MARCA = "CORREÇÃO: tb_procedimento não traz CO_GRUPO"
ANCORA = (
    "        if proc.is_empty():\n"
    "            return ProcedureDetails(df=pl.DataFrame())\n"
)
INSERIR = (
    "        # " + MARCA + "/CO_SUB_GRUPO/CO_FORMA_ORGANIZACAO; sem eles os joins abaixo\n"
    "        # caíam só em file_version_id (produto cartesiano) e davam a TODOS os procedimentos o mesmo\n"
    "        # grupo/subgrupo/forma. Os códigos são os dígitos 1-2, 3-4 e 5-6 de CO_PROCEDIMENTO.\n"
    "        proc = proc.with_columns(pl.col(\"CO_PROCEDIMENTO\").cast(pl.Utf8).str.strip_chars().str.zfill(10))\n"
    "        for _co, _ini, _n in ((\"CO_GRUPO\", 0, 2), (\"CO_SUB_GRUPO\", 2, 2), (\"CO_FORMA_ORGANIZACAO\", 4, 2)):\n"
    "            if _co not in proc.columns:\n"
    "                proc = proc.with_columns(pl.col(\"CO_PROCEDIMENTO\").str.slice(_ini, _n).alias(_co))\n"
)


def main(argv: list[str]) -> int:
    so_ver = "--ver" in argv
    caminho = next((Path(a) for a in argv[1:] if not a.startswith("--")), ALVO)
    if not caminho.is_file():
        print(f"não achei {caminho}; rode na raiz do projeto (D:\\papapapa\\GSPHosp-Novo) ou passe o caminho do sigtap.py")
        return 2
    texto = caminho.read_text(encoding="utf-8")
    if MARCA in texto:
        print(f"{caminho}: correção JÁ aplicada; nada a fazer")
        return 0
    if ANCORA not in texto:
        print(f"{caminho}: não encontrei o trecho esperado em get_procedure_details(); o arquivo mudou.\n"
              "Aplique à mão: logo depois de `if proc.is_empty(): return ProcedureDetails(df=pl.DataFrame())`,\n"
              "derive CO_GRUPO (dígitos 1-2), CO_SUB_GRUPO (3-4) e CO_FORMA_ORGANIZACAO (5-6) de CO_PROCEDIMENTO,\n"
              "antes dos joins com grupo/subgrupo/forma.")
        return 3
    if so_ver:
        print(f"{caminho}: PRECISA da correção (get_procedure_details junta grupo/subgrupo/forma só por versão)")
        return 1
    bak = caminho.with_name(caminho.name + ".bak-" + time.strftime("%Y%m%d-%H%M"))
    bak.write_text(texto, encoding="utf-8")
    novo = texto.replace(ANCORA, ANCORA + INSERIR, 1)
    caminho.write_text(novo, encoding="utf-8")
    try:
        py_compile.compile(str(caminho), doraise=True)
    except py_compile.PyCompileError as e:
        caminho.write_text(texto, encoding="utf-8")
        print(f"o arquivo corrigido não compila ({e}); restaurei o original")
        return 4
    print(f"{caminho}: correção aplicada (cópia do original em {bak.name}).\n"
          "Agora: python -m pytest tests\\test_importar_rd.py -q  → o teste test_sigtap_do_app_* deve passar;\n"
          "depois regenere os parquet/csv do pipeline, que foram gravados com os nomes errados.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
