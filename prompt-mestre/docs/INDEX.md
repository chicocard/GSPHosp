# Índice da Documentação do Projeto GSPHosp (Prompt Mestre v2.0)

Pasta consolidada para leitura por outro LLM (GPT-4o, Claude Opus etc.).
Todos os arquivos abaixo são a **solo verdadeiro** e devem ser lidos na íntegra
pelo subagente "Crítico Cego" durante o Gauntlet Loop.

---

## 📚 Camada 1: Identidade, Arquitetura e Hard Constraints (LER PRIMEIRO)

| # | Arquivo | Objetivo | Páginas estimadas |
|---|---|---|---|
| 01 | [MANUAL_USUARIO_PAPAHRP_PYTHON.md](./MANUAL_USUARIO_PAPAHRP_PYTHON.md) | Documento de produto completo. Visão geral, 4 pilares, instalação Windows, Wizard 4 passos, Dashboard 8 abas, LLM local, anexo metodológico completo (Huff, ICSAP 16 grupos, LACE, CMI, O/E, Transição de Cuidado, SIGTAP, 10 linhas de cuidado, Charlson Deyo CCI ICD-10). **É o documento de negócio.** | 50+ |
| 02 | [contract.md](./contract.md) | **Contrato técnico de arquitetura.** Princípios da Catraca (Ratchet = sem regressão). 4 módulos. Garantias: `Testar-GSPHosp.py` = 16 PASS / 0 FAIL. **Qualquer quebra neste arquivo = PR rejeitado.** | 2 |
| 03 | [AGENTS.md](./AGENTS.md) | Regras do próprio repositório para agentes. **Autonomia Total**. Validação obrigatória `.venv\Scripts\python.exe Testar-GSPHosp.py`. WCAG AAA branco `#FFFFFF` + hover amarelo ouro `#FEF08A`. Resiliência CNES `cast(pl.Utf8, strict=False)`. | 1 |
| 04 | [README.md](./README.md) | Visão geral do projeto, objetivo e uso rápido. | 2 |

---

## 🔬 Camada 2: Especificações de Solo Verdadeiro para Validação

| # | Arquivo | Objetivo |
|---|---|---|
| 05 | [refs-originais/MANUAL_BENCHMARKS.md](./refs-originais/MANUAL_BENCHMARKS.md) | **Fórmulas matemáticas de saúde: SOLO VERDADEIRO.** Modelo Huff β=2.2/1.5/0.9. 16 Grupos ICSAP (Portaria 2.206/2017). Índice LACE categorias. O/E Ratio. Crítico Cego **DEVE** comparar saídas do `metrics.py` com estas fórmulas. |
| 06 | [refs-originais/UI_LAYOUT_SPEC.md](./refs-originais/UI_LAYOUT_SPEC.md) | **Espec visual SOLO VERDADEIRO.** Botões branco `#FFFFFF` + hover `#FEF08A`. Chips selecionados fonte branca. Layout 1366x768. 4 passos Wizard. Toolbar 3 linhas. **Qualquer quebra visual = PR rejeitado.** |
| 07 | [gauntlet-status.html](./gauntlet-status.html) | Painel de status do último Gauntlet Loop executado. Visualiza aprovações do Construtor vs. Crítico Cego vs. Catraca. |

---

## 🐛 Camada 3: Bugs Históricos, Sessões Debug e Evidências

| # | Arquivo | Objetivo |
|---|---|---|
| 08 | [PROMPT-DE-CORRECAO-E-TESTE-GSPHosp.md](./PROMPT-DE-CORRECAO-E-TESTE-GSPHosp.md) | **Sessão histórica 8 bugs (CNES/Filtros/UFs).** 8 bugs raiz documentados (CSV CNES separador ; , coluna UF faltava, macros cruzavam UF, fallback 27 UFs, CNES colado sem botão, QObject import, DuckDB locks). Checklist de 20 testes PASS. Mapeamento exato arquivo + linha → função. HARD CONSTRAINTS HC1-HC7. **NÃO REINVENTAR A RODA: se o problema for semelhante, use a solução documentada.** |
| 09 | [debug-dashboard-abas-congelam-bargraph-1classe.md](./debug-dashboard-abas-congelam-bargraph-1classe.md) | **Sessão ATUAL bugs Dashboard (2026-08-15).** Status: CLOSED. 5 hipóteses falseáveis (H1-H5). Evidências runtime smoke headless com dataset REAL (61580 linhas SIH-RD, 25 hospitais, 0 exceções, 0 abas quebraram). Síndrome abas congeladas + BarGraph 1 classe: causas raiz e fixes aplicados. |
| 10 | [TESTES-RESULTADO.txt](./TESTES-RESULTADO.txt) | Resultado do último Smoke Test executado local (16 PASS). Comparar com novas execuções. |
| 11 | [_smoke_dashboard_headless.py](./_smoke_dashboard_headless.py) | Script de smoke GUI OFFSCREEN (não precisa abrir janela). Usa `QT_QPA_PLATFORM=offscreen` + `compute_dashboard` + `DashboardPage._on_ready`. Captura stdout stack trace das exceções [!!!] que PySide6 engole. **Roda em < 15s, SEMPRE executar antes de considerar "pronto".** |

---

## 🧪 Camada 4: Testes e Validação (EXECUÇÃO OBRIGATÓRIA)

| # | Arquivo | Objetivo |
|---|---|---|
| 12 | [Testar-GSPHosp.py](./Testar-GSPHosp.py) | Smoke teste OFICIAL do projeto (AGENTS.md item 2 obrigatório). 16 testes: py_compile 4 arquivos, CNES Loader 598k estabelecimentos, geo/reference hospital Dilson 314330 MG. Retorna exit 0 só se TODOS 16 PASS. **Qualquer saída diferente = não pode entregar.** |
| 13 | [Enviar-TRAE-para-GitHub.ps1](./Enviar-TRAE-para-GitHub.ps1) | Script PowerShell idempotente para deploy em branch TRAE/<timestamp>-<sufixo> isolado no chicocard/GSPHosp.git. Suporta GH_TOKEN > gh CLI > Git Credential Manager. Não toca branches main/gsphosp-novo-codigo. |

---

## 🧱 Camada 5: Stack Técnica e Dependências

| # | Arquivo | Objetivo |
|---|---|---|
| 14 | [pyproject.toml](./pyproject.toml) | Configuração do pacote Python papahr. Entry points: `papahr-gui`, `papahr-cli`. |
| 15 | [requirements-core.txt](./requirements-core.txt) | Camada 1/3: Polars, DuckDB, structlog, FTP, DBC readers. SEM interface. |
| 16 | [requirements-ui.txt](./requirements-ui.txt) | Camada 2/3: PySide6, PyQtGraph. |
| 17 | [requirements-llm.txt](./requirements-llm.txt) | Camada 3/3: llama-cpp-python (LLM local). |
| 18 | [requirements.txt](./requirements.txt) | Meta (tudo junto). **Instale na ordem: core → ui → llm.** Evita rollback atômico de pip. |

---

## 🔍 Como usar na prática (Instrução para o outro LLM)

1. **Primeira coisa:** ler Camada 1 inteira.
2. **Segundo:** ler Camada 2 (especificações = SOLO VERDADEIRO para validações).
3. **Terceiro:** antes de implementar QUALQUER correção similar, ler Camada 3 (evita re-descobrir bug já resolvido).
4. **Implementou algo:** rodar `Testar-GSPHosp.py` + `_smoke_dashboard_headless.py` (resultados gravados em `TESTES-RESULTADO.txt`).
5. **Crítico Cego valida:** tudo que está nestes arquivos + outputs comparados com fórmulas Camada 2.
6. **Pronto para deploy:** script `Enviar-TRAE-para-GitHub.ps1`.

---

> **Aviso para subagentes:** Proibido documentar "funcionalidade implementada" em documento deste repositório sem existir arquivo de teste `_critico_<X>.py` correspondente (regra de project_memory Lições Aprendidas: "Intolerância a documentação fake"). Todo padrão documentado → exige código funcional + teste unitário.
