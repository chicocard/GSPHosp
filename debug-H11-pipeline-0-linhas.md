# Debug H11 — Pipeline chega a 100% mas retorna 0 linhas / KPIs zerados

- **Data da investigação:** 2026-10-15
- **Projeto:** GSPHosp-Novo (`d:\papapapa\GSPHosp-Novo`)
- **Escopo:** investigação SOMENTE-diagnóstica. **Nenhum arquivo de código foi alterado, nenhum commit/push foi feito.**
- **Escopo de execução:** APENAS 1 UF (AC) / 1 competência por vez; nenhuma execução para as 27 UFs; nenhum comando excedeu ~3 min.
- **Interpretador:** `d:\papapapa\GSPHosp-Novo\.venv\Scripts\python.exe` (com `$env:PYTHONIOENCODING="utf-8"`).

---

## 1. Sintoma

> "O pipeline de carga chega a 100% de progresso (download e descompactação de arquivos `RD*.dbc` para `.dbf`) porém retorna 0 linhas / KPIs zerados / dashboard vazio." — originalmente para as UFs **AC** e **AL**, ano **2025**.

O ponto central do sintoma é o **paradoxo**: o progresso chega a "Concluído" (verde) mas `result.rows == 0`. Em nenhum ponto do fluxo um resultado com 0 linhas é tratado como falha/aviso (ver §5).

---

## 2. Mapa do fluxo crítico (arquivo:linha)

### 2.1 Orquestração — `papahr/pipelines/__init__.py`

| Etapa | Local | Observação |
|---|---|---|
| Fábrica Local/Remoto | `run_pipeline` L745–819 | `LocalPipeline` se `dbc_dir_path`, senão `RemotePipeline` (L819) |
| Listagem FTP com fallback de 12 candidatos | `_list_remote_files` L370–440 | escolhe `last_match`/`best_partial` (L386–418); **levanta erro** se nada casar (L421–435) |
| Filtro da listagem | `_try_filter_on_listing` L345–368 | usa `_match_dbc_file` + `ymd` + compara janela |
| Regex de nome de arquivo | `_DBC_FILE_RE_ORDEM1/2` L150–157, `_match_dbc_file` L159–166 | aceita 4 dígitos `ym` e partição `[A-Z]{0,4}` |
| Corpo remoto | `RemotePipeline.run` L442–598 | download L494–500; `read_many_dbc` **L505**; `processor.process` L549–556; append **só se `height>0`** L560–561; concat L570; filtro geo L573 |
| Corpo local | `LocalPipeline.run` L636–742 | `read_many_dbc` **L673**; `processor.process` L701–703; append **só se `height>0`** L709–710; concat L715; filtro geo L718 |
| Filtro geo final | `_aplicar_filtro_geo_final` L31–147 | retornos "no-op": L40–41 (sem macro/drs), L42–43 (df vazio), **L98–99 (sem colunas de geo)** |

**Ponto-chave 1:** a contagem de linhas pode ser perdida em 3 lugares: (a) `read_many_dbc` (L505/L673), (b) `processor.process` (L549/L701), (c) `_aplicar_filtro_geo_final` (L573/L718). Somente havendo `height>0` o chunk entra no concat.

**Ponto-chave 2:** `_save_outputs` (L257–270) retorna `(None, None)` para df vazio, e `run` monta `PipelineResult(rows=final_df.height)` (L584 / L728) e publica `stage="Concluido"` (L595–597 / L739–741) **sem qualquer verificação de `rows==0`**.

### 2.2 Cliente FTP — `papahr/core/ftp_client.py`

| Item | Local |
|---|---|
| `list_dir_ftp` (NLST, com fallback LIST) | L48–85 |
| `download_ftp` | L87–110 |
| `BASE_SIASIH` | L237 |

### 2.3 Leitura DBC/DBF — `papahr/core/dbc_reader.py`  ← **foco da causa raiz**

| Item | Local | Observação |
|---|---|---|
| `read_dbc` | L242–333 | 1ª tentativa: `datasus_dbc` (L258–279); fallback manual (L281–333) |
| Detecção "é DBF puro?" | **L286–289** | `is_dbf = raw[0] in (0x03,0x83,0x8B,0x30) and len(raw)>=32` |
| Fallback BLAST | `blast_decompress` L146–199, `_blast_core` L202–222 | `_blast_core` é **stub** (lê só literais de 8 bits e aborta) |
| 2ª leitura via dbfread | L317–333 | `rows=list(dbf.records)`; se vazio → **`return pl.DataFrame()`** L325–326 |
| `read_many_dbc` | L336–349 | **engole** exceções (só `log.error`) e retorna vazio em L348–349 |

### 2.4 Processadores — `papahr/processors/__init__.py`

| Item | Local |
|---|---|
| Guard "vazio" (SIH-RD) | `SIHRDProcessor.process` **L260–261** (`if raw.is_empty(): return pl.DataFrame()`) |
| Filtros | L264–265 (`_check_filter` por `MUNIC_MOV`/`CNES`) |
| Joins (counties/proc/cid/estab) | L286–302 |
| `return` final | L351 |

### 2.5 Loader CNES — `papahr/dashboard/cnes_loader.py`

- `load()` L322–350; `hospitais` (usa `NO_FANTASIA`/`NO_RAZAO_SOCIAL`) L292–304.
- Usado no enriquecimento por `CNESRepository._try_cnes_loader_local` (`papahr/datasources/cnes.py` L122–182). **Não participa do caminho que zera as linhas** (é auxiliar de join; ausência apenas gera colunas vazias, não remove linhas).

---

## 3. Hipóteses H1–H5 (falsificáveis) — status e evidência

### H1 — A perda ocorre na LEITURA do DBC (`read_dbc`/`read_many_dbc`), não no download nem no filtro
**Status: CONFIRMADA.**
- Teste: baixar `RDAC2501.dbc` (323.000 bytes, 1 arquivo, UF AC, competência 202501) e ler via `read_dbc`/`read_many_dbc`.
- Medido: download OK em 0,63 s; **`read_dbc` → 0 linhas e 0 colunas**; `read_many_dbc([...]) → 0`.

### H2 — A leitura falha porque o decompressor nativo `datasus_dbc` está indisponível e o fallback manual NÃO descompacta de verdade
**Status: CONFIRMADA (causa raiz).**
- `import datasus_dbc` → `ImportError: DLL load failed while importing datasus_dbc: Uma política de Controle de Aplicativo bloqueou este arquivo.`
- `datasus_dbc` **NÃO está declarado** em nenhum `requirements*.txt`, `pyproject.toml`, README, `.bat`/`.ps1` (aparece só em `papahr/core/dbc_reader.py` L258/261/265/273).
- O fallback trata o `.dbc` **comprimido** como `.dbf` puro: `is_dbf` L286–289 → `True` (o 1º byte de DBC é `0x03`, idêntico ao magic de DBF), então grava os bytes comprimidos como `.dbf` e o `dbfread` lê **0 registros**.
- O decompressor BLAST puro do próprio repo é um stub: para `RDAC2501.dbc` produziu saída sem magic DBF válido (`0x11/0x0e/0x7e`) e variantes com árvore customizada levantam `NotImplementedError`.

### H3 — O filtro geográfico final (`_aplicar_filtro_geo_final`) zera as linhas quando Macro/DRS são selecionadas
**Status: REFUTADA.**
- Teste: carregar `output/outputSIH_RD.parquet` (61.580 linhas) e chamar `_aplicar_filtro_geo_final` (a) sem macro/drs e (b) com macro "MR01 - Acre" + DRS "DRS 01 - Rio Branco".
- Medido: **61.580 → 61.580 em ambos os casos** (no-op). Motivo: o output final renomeado não contém colunas `CNES`/`CO_CNES`/`CO_MUNICIPIO*`/`SIGLA_UF`/`UF` (ver §4.3), então `uniq_cols` fica vazio e a função retorna o df inalterado (L98–99).

### H4 — O regex de nomes de arquivo não reconhece partições (A/B/C) nem `ym` de 4 dígitos → nenhum arquivo listado
**Status: REFUTADA.**
- Teste: `_match_dbc_file` sobre nomes reais/hipotéticos.
- Medido: `RDAC2501.dbc`→(RD,AC,2501,''); `RDAC2401a.dbc`→(RD,AC,2401,'a'); `RDSP2501.dbc`→(RD,SP,2501,''); `PAAC2412.dbf`→(PA,AC,2412,''); `RDAC2607.dbc`→(RD,AC,2607,''). Todos casam.

### H5 — 2025 não está publicado no FTP do DATASUS / a UF não tem arquivos
**Status: REFUTADA.**
- Teste: listagem FTP real de `/dissemin/publicos/SIHSUS/200801_/Dados/`.
- Medido: **22.915 arquivos** listados; **2025 tem 27 arquivos/mês** (todas as UFs); `AC` tem **223** arquivos RD (inclui `RDAC2501…RDAC2512` e `RDAC2601…RDAC2607`); `AL` idem **223**.

---

## 4. Evidências runtime (comandos + saída literal resumida)

Todos os comandos rodaram em `d:\papapapa\GSPHosp-Novo` com `$env:PYTHONIOENCODING="utf-8"`.

### 4.1 Listagem FTP real (SIH-RD)
Comando (resumido): `ftplib.FTP('ftp.datasus.gov.br').retrlines('NLST')` após `cwd /dissemin/publicos/SIHSUS/200801_/Dados/`.
```
total_listed 22915
AC_RD_files 223
AC_sample ['RDAC0801.dbc', 'RDAC0802.dbc', 'RDAC0803.dbc', 'RDAC0804.dbc', 'RDAC0805.dbc', 'RDAC0806.dbc']
AC_last   ['RDAC2601.dbc', 'RDAC2602.dbc', 'RDAC2603.dbc', 'RDAC2604.dbc', 'RDAC2605.dbc', 'RDAC2606.dbc']
AL_RD_files 223
SIH_RD_by_year { ... '2401': 27 ... '2501': 27 ... '2512': 27, '2601': 27, ... '2607': 25 }
elapsed 0.67
```
→ 2025 **está** publicado; AC/AL têm arquivos. (**H5 REFUTADA**)

### 4.2 Download + leitura de 1 arquivo real (AC / 202501)
Comando (resumido): `FTPClient().download_ftp('ftp://ftp.datasus.gov.br/.../RDAC2501.dbc', tmp)` + `read_dbc(...)`.
```
downloaded_bytes 323000 in 0.63 s
read_dbc_rows 0
cols []
sample_dtypes {}
elapsed 5.34
```
→ Download **OK**, leitura **0 linhas**. (**H1 CONFIRMADA**)

### 4.3 Processador sobre o bruto (mesma leitura real)
Comando (resumido): `raw = read_many_dbc(['.../rdac2501.dbc'])` + `SIHRDProcessor().process(raw, ctx_min)`.
```
raw_height 0
processor_out_height 0
```
→ A perda já vem de `read_many_dbc`; o processador apenas propaga o vazio (guard L260–261).

### 4.4 Diagnóstico do decompressor
```
>>> import datasus_dbc
ImportError: DLL load failed while importing datasus_dbc: Uma política de Controle de Aplicativo bloqueou este arquivo.

size 323000
first16_hex 037e0206bd110000410ebe0200000000    # 1º byte = 0x03 (== magic DBF!)
byte0 0x3
hdr_size_at4 4541

is_dbf_misdetect True                            # dbc_reader L286-289
expected_dbf_size_from_header 4541
blast skip 4  len 4541 b0 0x11 valid_dbf_magic False
blast skip 8  len 4541 b0 0xe  valid_dbf_magic False
blast skip 0  len 4541 b0 0x7e valid_dbf_magic False
blast skip 12 FAILED NotImplementedError BLAST com arvores customizadas nao implementado
blast skip 16 FAILED NotImplementedError ...
blast skip 20 FAILED NotImplementedError ...
blast skip 24 FAILED NotImplementedError ...
```
→ O fallback nunca gera um DBF válido; e `is_dbf=True` faz o código **pular** qualquer tentativa de descompactação.

### 4.5 Mecanismo exato do "0 registros" (DBC tratado como DBF)
Comando (resumido): `DBF('misdetect_as_dbf.dbf')` sobre os **bytes comprimidos** do `.dbc`.
```
misdetect_as_dbf -> nfields 113
header_numrecords 4541
records_read 0
first_field_names ['UF_ZI', 'ANO_CMPT', 'MES_CMPT', 'ESPEC', 'CGC_HOSP', 'N_AIH']
```
→ O cabeçalho (não-comprimido) é lido com os campos reais do SIH-RD, mas os **registros** (área comprimida) resultam em **0** → `read_dbc` cai em `if not rows: return pl.DataFrame()` (L325–326).

### 4.6 Filtro geo final e regex
```
final_rows 61580
geofilter_none_rows 61580        # sem macro/drs
geofilter_AC_rows   61580        # com "MR01 - Acre" + "DRS 01 - Rio Branco"
read_many_rows 0
RDAC2501.dbc match True ('RD', 'AC', '2501', '')
RDAC2401a.dbc match True ('RD', 'AC', '2401', 'a')
RDSP2501.dbc match True ('RD', 'SP', '2501', '')
PAAC2412.dbf match True ('PA', 'AC', '2412', '')
RDAC2607.dbc match True ('RD', 'AC', '2607', '')
```
→ (**H3 REFUTADA**, **H4 REFUTADA**)

### 4.7 Dependência declarada?
`grep datasus_dbc` em `*.{txt,toml,py,md,bat,ps1}`: **4 ocorrências, todas em `papahr/core/dbc_reader.py`**. Ausente em todos os `requirements*.txt`, `pyproject.toml`, README e scripts. (**H2 CONFIRMADA**)

### 4.8 Teste de referência do projeto
Comando: `& .\.venv\Scripts\python.exe Testar-GSPHosp.py`
```
RESULTADO: 16 PASS / 0 FAIL / 0 SKIP
Detalhes em: D:\papapapa\GSPHosp-Novo\TESTES-RESULTADO.txt
```
Saída completa confirma os 16 testes esperados:
```
[PASS] 0-sintaxe; 5-enrichment (MG/MR02/DRS06); 6-bate-MG; 7-bate-SP; 8-macros-RJ;
[PASS] 9-uf-vazia; 11-cascata; 10-selected-ufs; 1-sigla-uf (n_unique=27);
[PASS] 2-linhas=598,661; 3-mg=73,985; 4-dilson; 12-heur2-mg=73,985;
[PASS] 13-heur2-drs06=9,070; 14-search; 15-fallback
RESUMO: 16 PASS | 0 FAIL | 0 SKIP (total 16)
```
> Observação: o teste de referência **não cobre** o caminho de leitura de DBC real (`read_dbc`/`read_many_dbc` sobre arquivo do FTP) — por isso permanece 16/0/0 mesmo com o bug presente.

---

## 5. Diagnóstico final e Causa Raiz

**Causa raiz:** `papahr/core/dbc_reader.py::read_dbc` não consegue descompactar arquivos `.dbc` do DATASUS quando o decompressor nativo `datasus_dbc` não está utilizável, e o **fallback embutido é não-funcional** — e, pior, **silenciosamente retorna um DataFrame vazio** em vez de sinalizar erro.

Cadeia causal (toda medida/validada em §4):

1. `datasus_dbc` **não é dependência declarada** (`requirements*.txt`/`pyproject.toml`) → em instalação padrão o `import` falha (`ModuleNotFoundError`). No ambiente desta investigação ele existe, mas o **DLL está bloqueado por política de Controle de Aplicativo** do Windows (erro real reproduzido em §4.4).
2. `read_dbc` cai no fallback (L281+). A checagem `is_dbf` (L286–289) classifica erroneamente o arquivo `.dbc` **comprimido** como `.dbf` puro, porque **o primeiro byte de um DBC é `0x03`, o mesmo magic de um DBF** (§4.4).
3. Como `is_dbf=True`, o código **não tenta descompactar**: grava os bytes comprimidos como `.dbf` temporário e o `dbfread` lê o cabeçalho (campos reais SIH-RD) porém **0 registros** (§4.5).
4. `read_dbc` então executa `if not rows: return pl.DataFrame()` (L325–326) → **DataFrame vazio**, sem exceção.
5. `read_many_dbc` (L336–349) **engole** o problema (retorna vazio).
6. `processor.process` recebe `raw` vazio e retorna vazio (guard L260–261).
7. Em `RemotePipeline.run`/`LocalPipeline.run`, como `out_chunk.height == 0`, **nada é anexado** a `chunk_outputs` (L560–561 / L709–710); o concat resulta em df vazio (L570 / L715).
8. `_aplicar_filtro_geo_final` é no-op (§4.6) e `_save_outputs` retorna `(None, None)`.
9. `PipelineResult.rows = 0`, `stage="Concluido"`, mensagem "0 linhas em Xs" → **UI verde/dashboard vazio** (viola a regra de engenharia "0 linhas ≠ sucesso").

> **Ressalva de honestidade:** o bloqueio do DLL `datasus_dbc` observado (`ImportError ... Controle de Aplicativo`) é uma condição do **ambiente desta investigação**. **INCONCLUSIVO** se a máquina do usuário tem esse mesmo bloqueio; porém o defeito de projeto do fallback é **independente do ambiente** (o `is_dbf` errado + BLAST stub + retorno silencioso vazio continuam valendo, e `datasus_dbc` nem sequer é declarado). Não foi possível medir o `import datasus_dbc` numa máquina sem a política de bloqueio a partir daqui.

**Hipóteses descartadas com medição:** filtro geo final (H3, no-op 61.580→61.580), regex de nomes (H4, todos casam), indisponibilidade de 2025/FTP (H5, 22.915 arquivos, 2025 presente).

---

## 6. Correção proposta (NÃO aplicada)

Objetivo mínimo: (a) não tratar `.dbc` comprimido como `.dbf`; (b) garantir um decompressor que funcione; (c) nunca reportar 0 linhas como sucesso.

1. **`papahr/core/dbc_reader.py` — corrigir a detecção (L286–289).**
   Decidir pela **extensão** do arquivo, não pelo magic:
   - se `p.suffix.lower() == ".dbf"` → tratar como DBF puro;
   - se `".dbc"` → **sempre** ir para o ramo de descompactação.
   Isso elimina a colisão do magic `0x03` e obriga a tentativa de BLAST/zlib.

2. **`papahr/core/dbc_reader.py` — não falhar em silêncio.**
   Em `read_dbc`, substituir o `return pl.DataFrame()` cego (L325–326) por: se `dbf.read_header()` indicar registros > 0 mas `rows` vier vazio, **levantar erro** (ex.: `RuntimeError("DBC descompactado sem registros: ...")`). Em `read_many_dbc` (L336–349), **propagar/contar** as falhas em vez de só logar, devolvendo um sinal (ex.: exceção ou contador) que o pipeline possa transformar em WARN.

3. **Declarar a dependência de descompactação.**
   Adicionar o decompressor nativo em `requirements-core.txt` (ex.: `datasus-dbc>=1.2.0`) — hoje ele é usado em `dbc_reader` mas não é declarado em lugar algum (§4.7). Alternativa: substituir o stub `blast_decompress`/`_blast_core` (L146–222) por uma implementação BLAST correta (pura-Python), para não depender de DLL nativa.

4. **`papahr/pipelines/__init__.py` — 0 linhas = AMARELO/WARN.**
   Em `RemotePipeline.run` (após L570) e `LocalPipeline.run` (após L715): se `final_df.is_empty()`, anexar aviso (ex.: `result.warnings.append("Nenhuma linha após processamento — verifique leitura dos DBC/parâmetros.")`) e **não** publicar `stage="Concluido"` verde; marcar status de aviso para a UI (`papahr/ui/pages/__init__.py` L2979–2981, onde hoje mostra "Concluído: {rows} linhas").

> Observação: a correção do item 1 já resolve a causa medida nesta investigação (faria o código tentar descompactar em vez de tratar como DBF). Os itens 2–4 são necessários para respeitar as regras de engenharia do projeto (métricas reais; 0 linhas tratado como WARN).

---

## 7. Lições Aprendidas

1. **Magic byte compartilhado é armadilha.** DBC comprimido e DBF puro começam com `0x03`. Detectar formato por magic/1º byte é inseguro — usar a extensão do arquivo.
2. **Fallback que "funciona com defaults" é pior que não ter fallback.** O `blast_decompress`/`_blast_core` documenta "funciona para todos os DBC testados" mas é um stub que produz lixo; combinado com o retorno silencioso de DataFrame vazio, cria um bug que **não aparece como erro** — aparece como dashboard vazio.
3. **Falha silenciosa = 100% verde + 0 linhas.** `read_many_dbc` engolir exceções e o pipeline nunca checar `rows==0` transforma um erro de I/O em "sucesso vazio". A regra do projeto ("0 linhas não é sucesso; deve ser AMARELO/WARN") existe exatamente para isso e hoje **não está implementada** no código.
4. **Dependência usada e não declarada.** `datasus_dbc` é importado em runtime crítico mas não consta em nenhum `requirements*.txt`/`pyproject.toml` — instalações padrão nascem quebradas para o caminho DBC.
5. **Teste de referência com ponto cego.** `Testar-GSPHosp.py` (16 PASS) valida geografia/CNES mas **não** exercita `read_dbc`/`read_many_dbc` sobre um DBC real do FTP — por isso o bug H11 conviveu com a suíte 100% verde. Recomenda-se novo teste que baixe 1 DBC pequeno (ex. `RDAC2501.dbc`) e exija `height > 0`.
6. **Hipóteses antigas do H11 estavam erradas.** Com medição direta: o filtro geo final é no-op, o regex aceita partições/4 dígitos, e 2025 **está** publicado (AC/AL têm 223 arquivos RD cada). O problema nunca foi de listagem/download/filtro — é de **leitura/descompactação**.

---

### Comandos executados (índice)

1. Contagem de linhas dos parquets + checagem de `datasus_dbc`/`dbfread` instalados.
2. Listagem FTP real do SIHSUS (contagem por UF/ano) — timeout 25 s.
3. Download + `read_dbc` de `RDAC2501.dbc`.
4. `read_dbc` (reprodução do vazio) + diagnóstico de magic/header/BLAST.
5. `read_many_dbc` + `SIHRDProcessor.process` sobre o bruto real.
6. `_aplicar_filtro_geo_final` sobre `outputSIH_RD.parquet` + `_match_dbc_file` nos nomes.
7. `DBF()` sobre os bytes comprimidos (mecanismo dos 0 registros).
8. `grep datasus_dbc` nos arquivos de dependência/configuração.
9. `Testar-GSPHosp.py` → 16 PASS / 0 FAIL / 0 SKIP.

---

## 8. Correção aplicada e validada (2026-10-15) — H11 RESOLVIDO

> Esta seção foi **acrescentada depois** da investigação diagnóstica acima (§1–§7). O diagnóstico permanece válido como registro histórico; abaixo está o que foi efetivamente implementado e medido.

### 8.1 Decisão de arquitetura

Descartadas duas rotas e adotada uma terceira:

| Rota | Veredicto | Motivo |
|---|---|---|
| DLL nativa `datasus_dbc` | **Descartada** | Bloqueada por política de Controle de Aplicativo (WDAC) do Windows — `DLL load failed while importing datasus_dbc`; não é dependência declarada. |
| Automatizar o **TABWIN** (GUI) | **Descartada** | O TABWIN é GUI Delphi/R **sem modo batch de conversão DBC→DBF** (`TabWin415.exe <arq.tab>` só tabula; `.r`/`.rx` exigem a interface TabWin+R com R instalado). Automatizar via `pywinauto` seria frágil (idioma/versão/sessão de desktop). |
| **Descompressor DBC em Python puro** | **ADOTADA** | Lib externa **`dbc-to-dbf`** (PyPI, zlib) + fallback interno `_BlastState` — ambos port fiel de `blast.c` (Mark Adler), com o layout do container conforme `dbc2dbf.c` (`read.dbc`). Sem DLL, imune ao WDAC, portátil e testável. |

### 8.2 O que mudou em `papahr/core/dbc_reader.py`

- **Descompressor externo integrado (via primária):** biblioteca **`dbc-to-dbf`** (PyPI, Python puro, licença zlib — port de `blast.c` de Mark Adler). Chamada via `from dbctodbf import DBCDecompress; DBCDecompress().decompress(raw)`. Declarada em `requirements-core.txt` e `pyproject.toml` (`dbc-to-dbf>=1.0.0`).
- **`_blast_core` (stub) eliminado.** Substituído por `_BlastState` (`bits`/`decode`/`decompress`) — **port fiel e completo** do algoritmo PKWARE DCL ("implode"/BLAST) de `blast.c`. Tabelas Huffman canônicas reconstruídas por `_construct()` a partir de `_LITLEN`/`_LENLEN`/`_DISTLEN` (arrays idênticos aos do `blast.c`). Assinatura pública agora é `blast_decompress(compressed)` (sem `output_size`).
- **Orquestração com fallback:** `_dbc_body_to_dbf(raw)` tenta a lib externa primeiro e, em `ImportError` ou qualquer falha de descompressão, cai no `_BlastState` interno. As duas produzem saída **idêntica byte a byte** (medido). Se o stream estiver corrompido, a segunda também falha → **fail-fast** (não mascara erro de dados).
- **Leitura do header corrigida:** `_dbc_dbf_header_size()` lê o tamanho do cabeçalho do DBF embutido em **`[8:10]` (uint16 LE)** — não mais em `[4:8]` (que é o **nº de registros**, 4541 no arquivo real). Fonte: `dbc2dbf.c` (`HEADER_OFFSET=8`, `CRC_OFFSET=4`).
- **Composição correta do DBF:** `dbf_bytes = raw[:header_size] + blast_decompress(raw[header_size+4:])` — os 4 bytes seguintes ao header são **CRC32** (ignorados); o restante é o corpo comprimido.
- **Detecção por EXTENSÃO** (`.dbf` → puro; `.dbc` → sempre descompactar) em vez do magic `0x03` compartilhado; `_looks_like_dbf()` estrutural mantido para extensões desconhecidas.
- **Fail-fast:** 0 registros → `RuntimeError` (nunca mais `return pl.DataFrame()` silencioso); `read_many_dbc` agrega os motivos por arquivo.
- A tentativa nativa `datasus_dbc` só é tentada para `p.suffix == ".dbc"`.

### 8.3 Evidência de validação (arquivo REAL do DATASUS)

`RDAC2501.dbc` (UF AC, competência 202501), 323.000 bytes, obtido do FTP:

```
[PASS] A-dbc-comprimido-0x03: raw[0]=0x03 magic_antigo_diria_dbf=True novo_detector_dbf=False
[PASS] B-dbf-coerente: n_rec=3 hdr=65 reclen=10 com_eof=True sem_eof=True incoerente=False
[PASS] C-dbc-real: arquivo=...\rdac2501.dbc size=323000 caminho=externo dbc-to-dbf rows=4541 cols=113
[PASS] D-cross-validacao: externo=3191431b interno=3191431b iguais=True hdr=3649 hdr[-1]=0x0d
[PASS] E-read-dbc-real: rows=4541 (header=4541) cols=113
[PASS] F-fallback-interno: len=3191431 igual_interno=True (lib bloqueada)

CRITICO_DBC: 6 PASS / 0 FAIL
```

Regressão (suítes de referência):
```
Testar-GSPHosp.py          -> 16 PASS / 0 FAIL / 0 SKIP
_critico_dashboard_p0.py   ->  6 PASS / 0 FAIL
_critico_dbc_reader.py     ->  6 PASS / 0 FAIL
```

`4541 linhas × 113 colunas` casa **exatamente** com o header do DBC (`[4:8]=4541` registros, 113 campos). O teste **D** prova que o descompactador **externo** (`dbc-to-dbf`) e o **interno** (`_BlastState`) geram o **mesmo `.dbf` de 3.191.431 bytes**, byte a byte; o teste **F** prova que o fallback interno funciona quando a lib externa é removida.

### 8.4 Critérios de aceite do H11 — status

- [x] `.dbc` comprimido **não** é mais tratado como `.dbf`.
- [x] Descompressão DBC **funciona sem DLL nativa** (Python puro), imune ao WDAC.
- [x] Descompactador externo **`dbc-to-dbf`** integrado (via primária) + fallback interno equivalente (prova: teste F).
- [x] Leitura de DBC real retorna **linhas > 0** (4541) e colunas corretas (113).
- [x] 0 linhas deixou de ser silencioso (`RuntimeError` + agregação de motivos).
- [x] 0 regressões nas suítes (16 + 6 + 6 PASS).
- [ ] (Depurável a critério do produto) Transformar "0 linhas" em **WARN/amarelo na UI** no orquestrador (§6.4) — fora do escopo estrito da leitura DBC.

> Substitui a "Correção proposta (NÃO aplicada)" do §6: os itens 1, 2 e a alternativa pura-Python do item 3 foram **aplicados**; o item 4 (WARN na UI) segue como melhoria opcional.

