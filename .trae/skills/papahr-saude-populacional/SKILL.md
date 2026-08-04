---
name: "papahr-saude-populacional"
description: "Guia completo para evoluir o app desktop PaPAHR-Python (PySide6 + Polars + Huff + LLM local). Invoke SEMPRE que for adicionar feature, corrigir bug, refatorar UI/dashboard/backend ou melhorar o manual."
---

# 🏥 Skill: PaPAHR-Python (Saúde Populacional Desktop)

Skill de conhecimento **específico do projeto PaPAHR-Python** (versão Python 2.0). Use-a **a cada novo passo do projeto** (bugfix, nova feature, refatoração, UX, IA).

---

## 1. Visão Geral do Produto

**Stack:**
- **UI Desktop:** Qt6 via **PySide6 6.11.x**. Não usar PyQt5/6, nem PySide2.
- **Processamento:** **Polars 1.43.x (Apache Arrow em Rust)** — NUNCA usar pandas (foi banido do projeto).
- **Log estruturado:** **structlog 24.x** configurado via `papahr/logging_config.py`.
- **LLM local (privacidade):** **llama-cpp-python** (wheel CPU via extra-index). **Não usar OpenAI/Gemini API** (a promessa do produto é 100% offline).
- **Gráficos:** `pyqtgraph` (rápido, Qt nativo). Não usar matplotlib em runtime UI (muito lento). `ConcentricRingsMap` = **QPainter custom widget**.
- **Fonte dados:** DATASUS — `SIA-PA`, `SIH-RD/RJ/SP` (DBC), CNES (CSV local), SIGTAP (RenatoKR mirror GitHub primeiro, FTP fallback).

**Arquitetura dos arquivos principais:**
```
papahr/
├── __main__.py            (entry-point CLI)
├── config.py              (Settings.ensure_dirs, paths, @dataclass slots)
├── logging_config.py      (SEM WriteLoggerFactory! Ver seção 4)
├── core/                  (datasus_read.py, extract.py)
├── dashboard/
│   ├── cnes_loader.py     (CNESData singleton 598k estabelecimentos)
│   ├── metrics.py         (LACE, ICSAP, CMI, LOS O/E, 10 linhas cuidado)
│   └── huff_model.py      (HuffResult dataclass, catchment 50/80%)
└── ui/
    ├── app.py             (QMainWindow sidebar QListWidget + itemWidget QLabel)
    ├── widgets/__init__.py  (KpiCard, CardWidget, ChipButton, FlowLayout, ConcentricRingsMap)
    └── pages/
        ├── __init__.py       (PipelinePage = Wizard 4 passos + splitter colapsável)
        └── dashboard_page.py (DashboardPage = 5 abas + HuffWorker thread)
```

---

## 2. Regras CRÍTICAS de UX & UI (PySide6)

### 2.1 Layout & QSplitter
- **NÃO use `QHBoxLayout`/`QVBoxLayout` sozinhos** quando houver painel principal + painel lateral. Use **SEMPRE `QSplitter(Qt.Horizontal)`**.
- Sempre:
  ```python
  splitter = QSplitter(Qt.Horizontal)
  splitter.addWidget(wizard_card)     # principal
  splitter.addWidget(right_wrap)      # painel direito (KPIs, preview)
  splitter.setStretchFactor(0, 3)
  splitter.setStretchFactor(1, 1)
  splitter.setSizes([1100, 500])
  splitter.setCollapsible(0, False)   # NUNCA deixa o principal colapsar!
  splitter.setCollapsible(1, True)    # painel direito COLAPSÁVEL 100%
  splitter.setHandleWidth(8)
  root.addWidget(splitter)            # ÚNICO root = QxLayout(self)
  ```
- **PROIBIDO DUPLICAR QLayout no mesmo widget.** Nunca crie `main_root = QVBoxLayout(self)` se já existiu `root = QHBoxLayout(self)` na mesma classe. Avisa: "QLayout already has a layout".
- **`QListWidgetItem` não tem `setTextFormat()` no PySide6!** Crie item vazio + `setSizeHint(QSize(0,64))` + `list.setItemWidget(item, QLabel(html_rich))` com `WA_TransparentForMouseEvents`.
- `CardWidget` usa **`layout_()` interno**, não `setLayout()` de fora. `FlowLayout` herda de `QLayout` (importe `QLayout` e `QLayoutItem` do QtWidgets!).
- Preview de 50 linhas (resultado): **deve começar `setVisible(False)` por padrão no `__init__`**, só abre via `_show_preview_auto()` depois do callback `_on_result()`.
- Toggle button de preview: `checkable=True`. O método `_toggle_preview(checked)` DEVE sincronizar **os 3 ao mesmo tempo:** `self._preview_visible = bool(checked)` + `self._preview_card.setVisible(...)` + `btn.setText(Mostrar▼/Ocultar▲)`. **Não use só `setChecked(True)` — chame `_toggle_preview(True)`!**

### 2.2 KPIs e formatação BR
`KpiCard(valor: int|float, title, icon="..." , format=None)`:
- `format` aceita string Python f: `"{:.1f} %"`, `"R$ {:,.2f}"`.
- Formato BR: **`.` milhar, `,` decimal** (não trocar!). Implementado em `_format()`.
- Use `setMaximumHeight(92)` para cada KpiCard pra não crescer infinito.
- `right_wrap.setMaximumWidth(640)` em monitores gigantes.

---

## 3. Regras CRÍTICAS de Polars 1.43.x (MUITO IMPORTANTE!)

Polars NÃO tem "truth value" ambíguo. Estas regras quebraram o app 10+ vezes, **nunca repetir**:

| Erro | Causa | Solução CORRETA |
|---|---|---|
| `ValueError: mutable default <class 'pl.DataFrame'> ... use default_factory` | Python 3.14 proíbe `= pl.DataFrame()` no @dataclass. | **Sempre** `field(default_factory=pl.DataFrame)` (ex: HuffResult, DashboardMetrics, CNESData). |
| `TypeError: Truth value of a DataFrame is ambiguous.` | Escreveu `df or pl.DataFrame()` ou `if df:` ou `muni_df or X`. | **Sempre use ternário explícito:** `if df is not None and not df.is_empty() then df else vazio`. NUNCA deixe o Polars decidir! |
| `ColumnNotFound: "icsap_pct"` | Chamou `.sort("icsap_pct", descending=True)` em df vazio. | Sempre guarda no topo: `if df.is_empty() or "col" not in df.columns: return df`. |
| `SchemaError: datatype mismatch Utf8 vs i64 no join` | CNES/SIH colunas código: `CO_CNES`, `CO_TIPO_ESTABELECIMENTO`, `CO_LEITO`, `CO_MUNICIPIO`, `CO_NATUREZA_JUR`. | `CNESData._normalize_dicts()` faz cast `pl.col(x).cast(pl.Utf8, strict=False)` DEPOIS carregamento, ANTES de joins. |
| Pivot estrat ALTO/MÉDIO/BAIXO → 1 ou 2 colunas só (por ex. só tem ALTO) → depois `ColumnNotFound "MEDIO"` | Nenhum dado no estrato. | **Loop for garantido 3 colunas:** `for c in ("ALTO","MEDIO","BAIXO"): if c not in pvt: pvt = pvt.with_columns(pl.lit(0).alias(c))` |
| `_find_col("CID10")` não encontra `NU_CID10_PRINCIPAL` | Nome diferente por dataset. | `_find_col` normaliza (lower, tira - e espaço) + match bidirecional `pn in norm or norm in pn`. |

---

## 4. Regras CRÍTICAS de Logging (structlog)

**NUNCA MAIS use:**
```python
structlog.WriteLoggerFactory(file=settings.log_dir / "papahr.log")   # ❌ Erro WindowsPath.write!
```
**Use SEMPRE este padrão (já implementado em logging_config.py):**
```python
import io, sys
_OPEN_FILES: list[io.TextIOWrapper] = []   # GC protection

# PrintLoggerFactory para stdout
structlog.configure(
    processors=[...],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),  # ✅
)
# + stdlib root logger com 2 handlers:
#   - StreamHandler(sys.stdout)
#   - StreamHandler(open(Path, mode='a', encoding='utf-8', buffering=1))
# wrap tudo em try/except (sandbox TRAE pode bloquear arquivos; NÃO TRAVAR o app!)
```
- **Início `app.py` força UTF-8 stdout:** `sys.stdout.reconfigure(encoding='utf-8', errors='replace')` + env `PYTHONUTF8=1 PYTHONIOENCODING=utf-8` antes de imports structlog. Resolve `UnicodeEncodeError: 'charmap' codec can't encode` no PowerShell pt-BR (acentos, reticências … U+2026).

---

## 5. Módulos de Negócio (Saúde Populacional) — Saber de Cabeça!

### 5.1 `dashboard/metrics.py`
- **16 grupos ICSAP (Portaria MS 2206/2017)** → `_ICSAP_PATTERNS` — cada grupo tem lista de regex prefix CID-10. Não alterar sem citar Portaria.
- **Índice LACE** = L (LOS pontos) + A (urgência +3) + C (CCI Charlson adaptado Deyo) + E (PS 6m pts). Pontos → estratos `0-4 BAIXO / 5-9 MÉDIO / ≥10 ALTO`.
- **Charlson CCI (Deyo ICD-10)** → `_CHARLSON_DICT` 12 grupos regex + peso por idade.
- **CMI (Case Mix)** → `_WEIGHTS_DRG` tabela pesos DRG Brasil embutida.
- **LOS O/E, Custo O/E** → `_BR_BENCHMARKS_LINHAS_CUIDADO` 10 linhas (SCA/DPOC/Artroplastia Q+J / ICC / AVC / PAC / Sepse / Fêmur / Câncer).
- Metodo `_integracao_territorial`: sempre retorna df vazio se não houver muni + pop.

### 5.2 `dashboard/huff_model.py`
- **Modelo Gravitacional Huff (1963):** `Pij = (Mj / d_ij^β) / Σ_k (Mk / d_ik^β)`.
- **3 βs SUS default (armazenados no UI slider):** β=2,2 ambulatorial, β=1,5 média, β=0,9 alta-complex/onco.
- **Catchment `raio 50%` e `raio 80%`:** medidos em `catchment_profile` = menor raio cuja soma `Pij` acumulada ≥ 50% / ≥ 80%.
- **Tudo `@dataclass(slots=True)` + `field(default_factory=pl.DataFrame)`.**

### 5.3 `ConcentricRingsMap` (widget mapa)
- 5 anéis padrão: **5 / 10 / 20 / 40 / 80 km**.
- `QRadialGradient` de 4 stops: transparente → cor forte (Verde→Azul→Amarelo→Vermelho).
- Legenda de taxa por 1000 hab: 0 → verde forte, >100 → vermelho.
- Interação: drag (pan), wheel zoom, **`hospital_clicked(CNES)` sinal Qt** → slot `_on_hospital_clicked`.
- **SIGTAP 3 filtros:** Grupo / Forma Organização / Procedimento. Dropdown default "🌐 TODOS GERAL". Baixa tabela SIGTAP na primeira vez que é clicado. Usa `RenatoKR/SIGTAP` mirror GitHub PRIMEIRO (download <10s), fallback ftp.datasus.gov.br.

---

## 6. Instalação (sempre lembrar ao usuário)

**Windows 10/11 obrigatório:**
1. `reg add HKLM\...\LongPathsEnabled = 1` → RESTART PC.
2. Python 3.12/3.13, pip tiers:
   ```
   pip install -r requirements-core.txt
   pip install -r requirements-ui.txt
   pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu   # CPU wheel
   pip install -e . --no-deps
   ```
3. CNES local CSV em `C:\DICIONARIO_DADOS\BASE_DE_DADOS_CNES_202512\` → ativa o mapa concêntrico.
4. Modelo LLM em `~/.papahr/models/*.gguf` → ativa aba Assistente.

---

## 7. Troubleshooting Predefinido (colar no chat quando o usuário reportar erro)

| Erro reportado pelo usuário | Explicação em 1 linha + 1 clique pra corrigir |
|---|---|
| `NameError: name 'QLayout' is not defined` | FlowLayout herda de QLayout → faltou importar `QLayout, QLayoutItem` do QtWidgets. |
| `AttributeError: QListWidgetItem has no setTextFormat` | Use `QListWidgetItem()` vazio → `setItemWidget(item, QLabel(html))` |
| `WindowsPath has no attribute 'write'` (structlog crash no `log.info("UI inicializada")`) | Troque `WriteLoggerFactory(file=Path)` por `PrintLoggerFactory(file=sys.stdout)` + stdlib file handler (ver seção 4). |
| `AttributeError: 'WindowsPath' object has no attribute 'write'` | Idem acima. |
| `Polars Truth value ambiguous` | Troque `X or pl.DataFrame()` por ternário `if X not None and not empty else vazio` (seção 3). |
| Join CNES `SchemaError str vs i64` | Executar `CNESData._normalize_dicts()` / `_normalize_estabs()` cast Utf8. |
| `UnicodeEncodeError: 'charmap' codec can't encode character \u2026` | Adicionar `sys.stdout.reconfigure(encoding='utf-8', errors='replace')` no TOPO do `app.py`, antes de todos imports. |
| Mapa não abre (sem pins) | CNES CSVs não estão em `C:\DICIONARIO_DADOS\BASE_DE_DADOS_CNES_202512\`. |

---

## 8. Padrões de Qualidade para Novas Features

Quando implementar qualquer coisa nova, aplicar:

### 8.1 Nova feature de UI
- Sempre **botão toggle / colapsável** — painéis NUNCA começam abertos tampando o conteúdo principal!
- **Teste runtime ANTES de entregar** (mesmo 2s):
  ```powershell
  .\.venv\Scripts\python.exe -m py_compile papahr/xyz.py    # sintaxe
  .\.venv\Scripts\python.exe -c "from papahr.ui.pages import PipelinePage; p = PipelinePage(None); print(p._preview_card.isVisible())"  # init
  ```
- **MaxHeight / MaxWidth** para tabelas de preview — não deixe crescer infinitamente.

### 8.2 Nova feature de métricas
- Sempre **garanta colunas com `pl.lit(0)` se ausentes (pivot/join).**
- Sempre **guarda contra DataFrame vazio antes de sort/filter.**
- Sempre **cast Utf8 de códigos-chave (CNES, CID, município) no início.**
- **Nunca use `pd.read_csv` / numpy.** Só Polars.

### 8.3 Nova feature de thread / worker
- Dashboard tem `DashboardWorker(QThread)`: `finished_ok` / `failed` sinais.
- Não bloqueie UI: sempre que compute Huff > 10s → QThread + barra de progresso.

### 8.4 CNES (598k estabelecimentos / 5.607 municípios)
- **É um singleton!** `get_cnes_data().load()` carrega uma única vez, na primeira chamada. Não recarregue.
- Todas 6 tabelas: `tbEstabelecimento, tbMunicipio, tbLeito, tbTipoEstabelecimento, tbEstado, tbNaturezaJuridica`.

---

## 9. Comandos Predefinidos de Teste (colar no terminal)

```powershell
# 1. Compilação de sintaxe (todos arquivos Python do projeto)
cd D:\papapapa\PaPAHR-Python
Get-ChildItem -Path papahr -Recurse -Filter *.py | ForEach-Object { .venv\Scripts\python.exe -m py_compile $_.FullName } ; echo "Syntax OK"

# 2. Smoke test MainWindow (3s, NÃO mostra janela, só instancia)
.venv\Scripts\python.exe -c "
import sys, os
os.environ['QT_QPA_PLATFORM']='offscreen'
from papahr.ui.app import MainWindow
t=MainWindow(); import time
time.sleep(1)
print('MW OK, stack count=', t.stack.count())
sys.exit(0)
"

# 3. App completo (mostra janela para o usuário testar)
.venv\Scripts\python.exe -m papahr.ui.app
```

---

## 10. Terminologia de Saúde BR (sempre usar no UI, pt-BR)

UI = pt-BR; código = inglês. Estas palavras **corretas MS** obrigatórias:
- "Atenção Primária à Saúde (APS / ESF / UBS)" — não "primária health".
- "SIA-PA · SIH-RD / RJ / SP" — siglas maiúsculas com hífen.
- "AIH (Autorização de Internação Hospitalar)" · "APAC" (Ambulatorial).
- "ICSAP (Internações por Condições Sensíveis à APS) — 16 grupos, Portaria 2.206/2017".
- "Região de Saúde · Macrorregião de Saúde" — não "health region".
- "Tabela SIGTAP · Grupo / Forma de Organização / Procedimento".
- "SUS" sempre maiúsculo.
- "Case Mix Index (CMI)" entre parênteses no label.
- Valor monetário: prefixo **`R$`** e formatação BR `1.234,56`.

---

## 11. Lista de Melhorias Pendentes que o Usuário Já Pediu (próximos passos)

Quando o usuário não souber o que fazer, mostrar esta lista:

| # | Feature | Prioridade |
|---|---|---|
| P1 | Fonte Wizard Passo1: **Importar pasta Windows com arquivos compactados (CNES CSV .zip / SIA/SIH DBC .zip/.rar)** + extrair automaticamente. | 🔴 URGENTE |
| P2 | **Nova Aba Benchmarking**: Hospital selecionado vs todos os hospitais do mesmo Município / Região de Saúde / Macrorregião / UF. Gráficos interativos pyqtgraph (bares + scatter) + tabela ranking ICSAP/LOS/CMI/LACE/readmissão. | 🔴 URGENTE |
| P3 | **Curvas de decaimento exponencial**: plotar taxa de internação / 1000 hab em função de distância (5/10/20/40/80 km), fit `exp(-λ·d)` (least-squares), mostrar λ (coef de decaimento), tabela taxa por anel → **mostrar NO MESMO widget dos resultados Huff (Atratividade)**. | 🔴 URGENTE |
| P4 | Exportar mapa concêntrico PNG + SVG com qualidade alta para relatórios. | 🟡 |
| P5 | Exportar Dashboard completo para PDF. | 🟡 |
| P6 | Ícones SVG Material Design em todos os KPIs (em vez de emoji). | 🟢 |

---

> **Regra de ouro final desta skill:** Antes de entregar QUALQUER alteração, RODE SEMPRE: `py_compile` de tudo + smoke test MainWindow com `QT_QPA_PLATFORM=offscreen`. Não entregue nada sem esses dois passos — evita os 12 erros já conhecidos listados acima!
