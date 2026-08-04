---
name: "gsphosp"
description: "Arquitetura, convenções e fluxo de trabalho do projeto GSPHosp (Gestão Saúde Populacional). Invoke ao: corrigir bugs, refatorar, adicionar features no Wizard Passo 4, pipeline ETL, CNESLoader ou georreferenciamento."
---

# GSPHosp / PaPAHR — Skill de Projeto

> **Skill mantida viva**: refletir aqui decisões técnicas relevantes após cada feature/refactor/bug fix.

---

## 1. Contexto do Negócio e Stack

| Item | Valor |
|------|-------|
| **Projeto** | GSPHosp (anteriormente PaPAHR) — Gestão em Saúde Populacional Hospitalar sobre dados do DATASUS/SUS |
| **Stack** | Python 3.11 · PySide6 (Qt for Python GUI) · Polars (ETL rápido em Rust) · DuckDB (Medallion lakehouse embedded) |
| **Arq. Dados** | Medallion: `bronze` (raw DBC→parquet) → `silver` (dicionário + limpeza + geo) → `gold` (agregados / ICSAP / CMI / LACE / Huff) |
| **GUI** | Wizard 4 passos QStackedWidget: (1) Sistema/Fonte → (2) UF/Macro/DRS/Município → (3) Período → (4) Hospitais + Executar |
| **CNES Fonte** | `C:\DICIONARIO_DADOS\BASE_DE_DADOS_CNES_YYYYMM\` (6 CSVs: tbEstabelecimento, tbMunicipio, tbLeito, tbTipoEstabelecimento, tbEstado, tbNaturezaJuridica) |
| **Executáveis** | `papahr-gui.exe` (shim de 108 KB do `.venv\Scripts\` — lê arquivos `.py` em tempo real, NÃO é bundle PyInstaller) · bundle real via `build/papahr.spec` → `dist\PaPAHR.exe` |
| **Git remote** | `https://github.com/chicocard/GSPHosp.git` (branch main) |
| **Idioma de UI** | 100% visual, Material Design 3, para usuários leigos (campo de senha tem "olhinho" de visibilidade obrigatório) |

---

## 2. Hard Constraints (NÃO QUEBRAR)

| ID | Constraint | Onde |
|----|------------|------|
| HC-1 | Sempre usar artigo masculino para o CNES → **"o CNES"** (não "a CNES") | Todo o UI / feedback / prompts |
| HC-2 | Campos de senha → botão de visibilidade ("olhinho") na cor **`#5EEAD4`** (QToolButton sobreposto via eventFilter; NÃO usar `addAction(TrailingPosition)` que quebra no Windows com QSS customizado) | `login_page.py` / qualquer outro diálogo de senha |
| HC-3 | Senhas no login → `.rstrip("\r\n\t ")` obrigatório ANTES de validar hash | `login_page.py`, handlers de `edt_password.returnPressed` |
| HC-4 | NUNCA deixar o pipeline baixar TODAS as 27 UFs por padrão. Se `_selected_ufs_nonempty() == []` → bloquear o usuário e pedir para marcar um chip. | `_selected_ufs()` + `_on_run()` em `pages/__init__.py` |
| HC-5 | Enriquecimento geográfico → SEMPRE passar pelo helper centralizado `resolve_geo_enrichment(...)` e cascata `hospital_bate_geo(...)`; NUNCA reimplementar a regra inline. | `geo/reference.py` |
| HC-6 | Leitura CSV do CNES → SEMPRE usar `_read_cnes_csv()` / `_detect_csv_params()`; NUNCA defaults Polars (separador `,` é incorreto para o DATASUS que usa `;`). | `dashboard/cnes_loader.py` |

---

## 3. Estrutura de Arquivos (Crítica)

```
GSPHosp-Python/
├── main.py                                    ← entry-point 168 bytes (apenas chama papahr.ui.app.launch)
├── papahr/
│   ├── dashboard/
│   │   └── cnes_loader.py                     ← SINGLETON CNESData: autodetect CSV + garante SIGLA_UF (L161-377)
│   ├── geo/
│   │   └── reference.py                       ← Hierarquia SUS 27 UFs + resolve_geo_enrichment + hospital_bate_geo (L633-748)
│   ├── pipelines/                             ← run_pipeline(request), PipelineRequest/DuckDB/SIA/SIH
│   ├── ui/
│   │   ├── pages/__init__.py                  ← PipelinePage (Wizard 4 passos). ~2200 linhas, arquivo MAIS CRÍTICO.
│   │   ├── pages/login_page.py                ← Login com "olhinho" (QToolButton sobreposto + eventFilter)
│   │   └── styles/*.qss                       ← Material Design 3 customizado
│   └── etl/                                   ← ETL Medallion Bronze → Silver → Gold
├── build/papahr.spec                          ← PyInstaller spec (bundle → dist/PaPAHR.exe, uso raro)
├── Resetar-E-Abrir-GSPHosp.bat                ← Launcher #2 (mata python.exe, limpa WAL/shm do DuckDB + abre GUI)
├── Abrir-GSPHosp.bat                          ← Launcher #3 (só abre GUI)
└── .gitignore                                 ← contém _tmp_*, _teste_*, _resultado_* para scripts de debug
```

### Arquivos a Tocar SEMPRE Juntos em Bugs de Carga
Se corrigir algo no carregamento de hospitais / CNES / georreferenciamento → é BLOQUEANTE revisar e sincronizar estes 3 arquivos juntos:
1. [cnes_loader.py](file:///d:/papapapa/GSPHosp-Python/papahr/dashboard/cnes_loader.py#L161-L377)
2. [reference.py](file:///d:/papapapa/GSPHosp-Python/papahr/geo/reference.py#L633-L748)
3. [pages/__init__.py](file:///d:/papapapa/GSPHosp-Python/papahr/ui/pages/__init__.py#L1677-L1886)

---

## 4. Workflow de Correção de Bug (Ordem Obrigatória)

Todo bug de "carga dos dados / CNES não carrega / Aplicar filtros não funciona / Botão Buscar Hospital não funciona" → seguir exatamente esta ordem de investigação:

### 4.1 Diagnóstico (sem alterar código)

| Passo | Comando / Ação |
|-------|----------------|
| D1 | py_compile os 3 arquivos acima: `python -m py_compile papahr/dashboard/cnes_loader.py papahr/geo/reference.py papahr/ui/pages/__init__.py` |
| D2 | Verificar se CSV CNES existe e formato: `Test-Path C:\DICIONARIO_DADOS\BASE_DE_DADOS_CNES_202512\tbEstabelecimento202512.csv` |
| D3 | Checar se o erro é: **(a)** CNES inválido alertado falsamente | (b) 27 UFs baixando de uma vez | (c) Lista de hospitais vazia no Passo 4 | (d) Datasis FTP retorna nada |
| D4 | Rodar script de debug isolado que: carrega CNES via get_cnes_data() → filtra CO_CNES=2219646 → checa coluna SIGLA_UF → chama hospital_bate_geo |

### 4.2 Implementação (antes de editar, ler as seções 5,6,7)

### 4.3 Validação (pós-edição, BLOQUEANTE)
- py_compile os 3 arquivos
- Script simulacao E2E (sem GUI) que replica o `_reload_hospitals_from_cnes` e `_add_cnes_codes_from_text`
- Só depois abrir GUI real e testar manualmente

### 4.4 Fechamento
- `git add -A && git commit -m "fix(...): ..." && git push origin main`
- Atualizar esta skill com a nova regra aprendida, se for relevante
- Apagar scripts `_tmp_* / _teste_* / _resultado_*` (já estão no .gitignore)

---

## 5. Bugs Históricos Corrigidos + Causa Raiz + Solução

| Bug ID (interno) | Sintoma do Usuário | Causa Raiz | Como Corrigir (arquivo + linha) |
|------------------|---------------------|------------|---------------------------------|
| Bug CSV | Lista CNES vazia, colunas 56→1 | CSV CNES DATASUS usa `;` + UTF-8, Polars default lê `,` | [cnes_loader.py:161-247](file:///d:/papapapa/GSPHosp-Python/papahr/dashboard/cnes_loader.py#L161-L247) `_detect_csv_params` (4 sep × 4 enc + fallback binário) |
| Bug UF faltante | "CNES 2219646 não corresponde à área" | CNES do usuário NÃO tem coluna `CO_UF` de sigla → só `CO_ESTADO_GESTOR=31` IBGE. Código antigo buscava `CO_UF` → caía vazio. | [cnes_loader.py:316-377](file:///d:/papapapa/GSPHosp-Python/papahr/dashboard/cnes_loader.py#L316-L377) `_normalize_estabs()` garante coluna `SIGLA_UF` 100% preenchida via 6 fallbacks. |
| Bug cascata Macros | Macros do RJ selecionadas por engano → hospitais de MG apareciam | hospital_bate_geo usava prefixo 8 chars/código MR01 para match. Ambos MG e RJ tem "MR01". | [reference.py:688-714](file:///d:/papapapa/GSPHosp-Python/papahr/geo/reference.py#L688-L714) REGRA RÍGIDA 3: igualdade EXATA via `set intersection` `sels & macros_da_uf`. NUNCA fuzzy aqui. |
| Bug UF vazia fallback | hospital_bate_geo retorna False se só tem município | Se coluna SIGLA_UF falhar e só vier CO_MUNICIPIO_GESTOR=314330 → função rejeitava sem tentar enriquecer. | [reference.py:667-674](file:///d:/papapapa/GSPHosp-Python/papahr/geo/reference.py#L667-L674) Início da função: se `uf_h` vazio e município preenchido → `resolve_geo_enrichment()` para inferir UF. |
| Bug R2 | FTP SIA-PA: "Nenhum arquivo encontrado. UFs=['AC','AL', ..., 'SP'] (27 UFs!)" | `_selected_ufs()` tinha fallback `or list(get_ufs())` (todas 27). Chip de UF não marcado → baixava Brasil inteiro → DATASUS recusa por volume. | [pages/__init__.py:1035-1043](file:///d:/papapapa/GSPHosp-Python/papahr/ui/pages/__init__.py#L1035-L1043) Remover fallback. [pages/__init__.py:2124-2144](file:///d:/papapapa/GSPHosp-Python/papahr/ui/pages/__init__.py#L2124-L2144) Bloqueio `_on_run()` com QMessageBox se `ufs=[] or dr=[]`. |
| Bug R1 | Colar CNES 2219646 → alerta "CNES não correspondem à área" mesmo pertencente a MG | `_add_cnes_codes_from_text()` só validava contra `_hospitals_data` carregada em memória. Usuário cola ANTES de clicar em "Aplicar filtros geográficos" → lista desatualizada/vazia. | [pages/__init__.py:1979-2051](file:///d:/papapapa/GSPHosp-Python/papahr/ui/pages/__init__.py#L1979-L2051) Fallback ~80 linhas: busca DIRETO no `CNESData.estabelecimentos`, extrai UF/mun/reg, roda `hospital_bate_geo`, se bate → cria chip e adiciona hospital à lista. |
| Bug NameError login | App fecha instantaneamente ao carregar GUI, sem erro console | `QObject` usado via type hint em login_page.py mas não importado; `from __future__ import annotations` não protege runtime references fora de assinatura. | Adicionar `from PySide6.QtCore import QObject` no topo de login_page.py. |
| Bug DuckDB lock | Resetar-E-Abrir fecha instantaneamente ou app não inicia após crash | DuckDB crasha → deixa `*.duckdb-wal` e `*.duckdb-shm` como lock; `taskkill /F /IM python.exe` genérico matava processo errado no CMD. | `kill_gsphosp_processes.py` kill seletivo + script deleta arquivos WAL/shm antes de abrir GUI. |

---

## 6. Dicionário de Dados CNES do Usuário (Reais)

**Caminho raiz**: `C:\DICIONARIO_DADOS\BASE_DE_DADOS_CNES_YYYYMM\`
**Parâmetros CSV reais confirmados**:
- Separator: `;` (ponto-e-vírgula)
- Quote char: `"` (aspas duplas)
- Encoding: UTF-8
- tbEstabelecimento shape: 598.661 rows × 56 columns (após `_normalize_estabs` vira 57 cols + `SIGLA_UF`)

**Colunas-chave (NÃO existem colunas de sigla; usar IBGE mapping)**:

| Coluna | Valor de Exemplo (Dilson Godinho CNES=2219646) | Observação |
|--------|-------------------------------------------------|------------|
| `CO_CNES` | 2219646 | PK; padronizar para Utf8 e `.strip()` |
| `NO_FANTASIA` | HOSPITAL DILSON GODINHO | Nome de busca principal |
| `NO_RAZAO_SOCIAL` | (outro nome) | Fallback se fantasia faltar |
| `CO_ESTADO_GESTOR` | 31 | Código IBGE 2 dígitos → `UF_CODIBGE_TO_SIGLA["31"] = "MG"` |
| `CO_MUNICIPIO_GESTOR` | 314330 | IBGE 6 dígitos do município |
| `CO_REGIAO_SAUDE` | 0006 | Código DRS dentro da UF (CNES não divulga nome do DRS) |
| `CO_TIPO_ESTABELECIMENTO` | 006 | Hospital Geral; join com tbTipoEstabelecimento |
| **(coluna sintética nova)** `SIGLA_UF` | **MG** | Garantida 100% por `_normalize_estabs()`. SEMPRE usar esta na Heurística 2 primeiro. |

---

## 7. Funções Core (Assinatura + Como Usar)

### 7.1 `CNESData` singleton
Use SEMPRE `from papahr.dashboard.cnes_loader import get_cnes_data; c = get_cnes_data(); c.load()`.
- `c.estabelecimentos` → DataFrame Polars com coluna `SIGLA_UF` garantida
- `c.buscar_estabelecimentos(["2219646", ...])` → join com Município + Tipo
- `c.fonts_used` → dict com arquivos CSV físicos que foram usados (para debug de qual pasta)

### 7.2 `resolve_geo_enrichment(uf_sigla=None, co_municipio_6dig=None, co_regiao_saude_cnes_cod=None)`
Retorna tuple `(uf_final, mun_final_zfill7, co_macro, no_macro, co_drs, no_drs)` onde:
- no_macro = "MR02 - Triângulo / Alto Paranaíba" (nome completo compatível com chips da UI)
- co_drs = "06", no_drs = "DRS 06 - Patos de Minas"
Usar para INFERIR macro/DRS a partir de município, quando dados do CNES só tem código.

### 7.3 `hospital_bate_geo(uf_hospital, co_municipio_hospital, co_regiao_saude_hospital, sel_ufs, sel_macros, sel_drs)`
ÚNICA função de filtro cascata — use tanto na Heurística 1 (DuckDB) quanto Heurística 2 (CNES local).
- Inputs: todos opcionais exceto keyword-only
- Retorna `True` se o hospital passar pelo filtro
- Regras internas: (1) fallback UF por município, (2) bate UF individual, (3) sel_macros/sel_drs INTERSECTAM com UF do hospital POR IGUALDADE EXATA, (4) match final macro/drs individual do hospital com fuzzy

### 7.4 `PipelinePage._reload_hospitals_from_cnes()`
Popula `self._hospitals_data: list[tuple[str,str,str,str]]` = `(cnes, nome, uf, tipo)`. Tem duas heurísticas:
1. Heurística 1 (prioridade): `silver.dim_hospitais` DuckDB se já houve pipeline prévio. Filtrado no SQL.
2. Heurística 2 (fallback): varre `cnes.estabelecimentos` 598k linhas e valida uma a uma via `hospital_bate_geo`.
Ao final: chama `self._filter_hosp_list(self.search_hosp.text())` para popular `QListWidget list_hosp` com limite de 5.000 itens exibidos (mas a lista completa está em `_hospitals_data`).

### 7.5 `PipelinePage._add_cnes_codes_from_text(text: str)`
Valida códigos colados. Ordem: regex dígitos → já existe como chip? → lookup em `_hospitals_data` → FALLBACK NO CNESLoader + hospital_bate_geo → cria chip.
Retorna tuple `(added, invalid, skipped, added_labels)`. O componente crítico é o fallback que resolve o Bug R1.

---

## 8. Cenários de Teste Obrigatórios (Antes de Entregar ao Usuário)

Sempre testar esses 4+2 casos + confirmar 11/11 + 9/9 PASS.

### Grupo A: Testes sem GUI (rápidos, sem PySide6 event loop)
1. CSV load shape → (598661, 57) + coluna SIGLA_UF presente
2. Count MG via SIGLA_UF → ≥ 70.000 (73.985 é o baseline)
3. CNES 2219646 localizado via CO_CNES → UF=MG, mun=314330, reg=0006, nome=DILSON GODINHO
4. hospital_bate_geo só MG → True
5. hospital_bate_geo SP selecionado (hospital MG) → False
6. hospital_bate_geo MG + MACROS DO RJ → False (controle negativo RIGIDO 3)
7. hospital_bate_geo uf="" + mun=314330 → True (fallback UF vazia)
8. Simulacao `_selected_ufs()` com chips não marcados → retorna `[]`, NÃO retorna 27 UFs
9. Simulação fallback `_add_cnes_codes_from_text` com `_hospitals_data=[]` vazio + CNES=2219646 + sel_ufs=["MG"] → added=1, invalid=[]

### Grupo B: Testes E2E (simula UI, sem GUI)
10. Heurística 2 completa `simula_reload(UF=MG)` → 73.985 hospitais na lista, Dilson Godinho incluso
11. Filtro cascata UF=MG + MR02 Triângulo + DRS06 → 9.070 + Dilson incluso
12. search_filter query "DILSON GODINHO" → 1 match exato; "dilson" → 27 matches (apenas MG); query "2219646" → 1 match

### Grupo C: Testes GUI real (obrigatório se a queixa foi de comportamento visual)
13. Passo 2 sem UF marcada → clique Passo 4 Executar Extração → QMessageBox de bloqueio aparece, pipeline NÃO inicia
14. Passo 4 → botão 📍 Aplicar filtros → status mostra qtd e UF
15. Colar CNES 2219646 em campo "Colar CNES" SEM ter clicado no botão → chip verde criado SEM alerta de erro
16. Campo Buscar Hospital digitar "DILSON GODINHO" → 1 resultado

---

## 9. Erros Comuns em Debug no Windows

| Erro | Causa | Solução |
|------|-------|---------|
| `UnicodeEncodeError: cp1252 codec can't encode character` no PowerShell | `print()` de dataframe com acentos; Windows console usa cp1252. | NÃO dar print. Salvar resultado em arquivo UTF-8: `with open("r.txt","w",encoding="utf-8") as f: f.write(...)` e ler via `[System.IO.File]::ReadAllText("r.txt", [System.Text.Encoding]::UTF8)` |
| `NameError: name QObject is not defined` (sem stack trace visível em modo gui-bundle) | Import faltante. O `from __future__ import annotations` não adianta referências runtime fora de type hints. | Adicionar `from PySide6.QtCore import QObject` explícito. |
| App não inicia após crash anterior | DuckDB WAL/shm + processo python.exe órfão segurando lock | Rodar `Resetar-E-Abrir-GSPHosp.bat` (chama `kill_gsphosp_processes.py` seletivo e deleta arquivos lock) |
| Executável não atualiza com os commits do GitHub | `papahr-gui.exe` é shim do `.venv` → lê `.py` em tempo real; se for bundle PyInstaller precisa rebuildar `pyinstaller --clean build/papahr.spec` | Para dia-a-dia é shim: correções chegam só de editar o `.py` (quando executa via `.venv/Scripts`). Bundle só para release a usuário final externo. |

---

## 10. Palavras-chave para Trigger desta Skill

Aplicar quando o usuário disser qualquer uma dessas frases / padrões:
- "A carga dos dados não funciona" / "Aplicar filtros geográficos não funciona"
- "Carregar hospitais do CNES não carrega" / "botão não funciona"
- "CNES não encontrado" / "CNES não corresponde à área selecionada" (em especial 2219646 / Hospital Dilson Godinho)
- "Baixar arquivos para e trava no começo" / "Nenhum arquivo encontrado no FTP SIA"
- "Sistema começa a baixar e pára"
- "Buscar Hospital não retorna resultados"
- Qualquer bug report que mencione: Wizard Passo 4, CNES, UF, Macro, DRS, MG/Minas Gerais, Dilson Godinho
- Refactor do pipeline, GUI Wizard ou qualidade geral de UI
