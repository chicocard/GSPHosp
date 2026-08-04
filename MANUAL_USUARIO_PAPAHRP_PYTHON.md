# 🏥📘 Manual do Usuário: **PaPAHR-Python**

> **Pacote da Produtividade Ambulatorial e Hospitalar — Versão Python 2.0.0**
>
> **Plataforma:** Windows 10/11 (64 bits) · **Interface Gráfica:** Desktop PySide6 · **Tema:** Material Design 3 (Teal Escuro) · **Processamento:** Polars (Rust · Apache Arrow) · **IA Local (opcional):** llama-cpp-python (Phi-3 Mini / TinyLlama GGUF Q4)

---

## 📑 Sumário

1. [Visão Geral](#1-visão-geral)
2. [Instalação Passo a Passo (Windows 10/11)](#2-instalação-passo-a-passo-windows-1011)
3. [Como Usar — Aplicação Desktop](#3-como-usar--aplicação-desktop)
   - 3.1 [Aba 1 ▶️ — Wizard de Extração (4 Passos)](#31-aba-1️⃣--wizard-de-extração-4-passos)
   - 3.2 [Aba 2 📊 — Dashboard Saúde Populacional](#32-aba-2️⃣--dashboard-saúde-populacional)
   - 3.3 [🗺️ Aba Território: Mapa de Círculos Concêntricos SIGTAP](#34-aba-território-mapa-de-círculos-concêntricos-sigtap)
   - 3.4 [Aba 3 🧠 — Assistente LLM Local (contexto do seu dataset)](#35-aba-3-🧠--assistente-llm-local-contexto-do-seu-dataset)
   - 3.5 [Aba 4 ⚙️ — Configurações](#36-aba-4-️--configurações)
4. [📚 Anexo Metodológico e Conceitual — Gestão de Saúde Populacional em Hospitais](#4-📚-anexo-metodológico-e-conceitual--gestão-de-saúde-populacional-em-hospitais)
   - 4.1 [Modelo Gravitacional de Huff (Atração Espacial de Pacientes)](#41-modelo-gravitacional-de-huff-atração-espacial-de-pacientes)
   - 4.2 [Taxa de Internações por Condições Sensíveis à Atenção Primária (ICSAP) — 16 Grupos Portaria 2.206/2017 MS](#42-taxa-de-internações-por-condições-sensíveis-à-atenção-primária-icsap---16-grupos-portaria-22062017-ms)
   - 4.3 [Índice LACE (Risco de Readmissão / Mortalidade 30 dias)](#43-índice-lace-risco-de-readmissão--mortalidade-30-dias)
   - 4.4 [Case Mix Index (CMI) — Complexidade da Casuística]()
   - 4.5 [Eficiência Clínica e LOS O/E (Observado / Esperado)]()
   - 4.6 [Transição de Cuidado (Readmissão 30d + Contato 48h pós-alta)]()
   - 4.7 [Tabela SIGTAP (Sistema de Gerenciamento da Tabela de Procedimentos, Medicamentos e Materiais)](#47-tabela-sigtap-sistema-de-gerenciamento-da-tabela-de-procedimentos-medicamentos-e-materiais-órteses-próteses-e-materiais-especiais-do-sus)
   - 4.8 [10 Linhas de Cuidado Hospitalar Prioritárias (Padrões BR)]()
   - 4.9 [Índice de Comorbidade de Charlson (CCI) Adaptado ICD-10 (Deyo)]()
5. [🛠️ Anexo Técnico: Resolução de Problemas Comuns](#5-️-anexo-técnico-resolução-de-problemas-comuns)
6. [ℹ️ Sobre o Produto e a Equipe](#6-ℹ️-sobre-o-produto-e-a-equipe)

---

## 1. Visão Geral

**O que é o PaPAHR-Python?**
A versão Python moderna do legado `PaPAHR` (escrito originalmente em R). É o **kit completo de inteligência em saúde populacional** voltado para:
✅ Gestores públicos do SUS (estaduais e municipais)
✅ Hospitais filantrópicos e universitários
✅ Pesquisadores em Epidemiologia e Saúde Coletiva
✅ Níveis técnicos e não programadores (**não precisa digitar nada, é 100% visual**)

**4 pilares de análise (ver Anexo 4 p/ detalhes metodológicos):**
| Pilar | Descrição | Indicadores principais |
|---|---|---|
| 🟢 **Risco / Demanda** | Estratifica paciente por gravidade | CMI, LACE, Charlson, Estratos ALTO/MÉDIO/BAIXO (% de custo) |
| 🟡 **Eficiência / Valor** | Quanto tempo dinheiro cada caso gasta comparado ao esperado | LOS O/E, Custo O/E, Margem estimada |
| 🔵 **Integração Territorial** | Hospital atrai pacientes de quantos municípios? | Taxa ICSAP, **Modelo Huff 50% / 80% raio de atração**, mapa concêntrico |
| 🔴 **Transição de Cuidado** | O que acontece 30 dias depois da alta? | Readmissão≤30d, contato≤48h, linha de cuidado pós-alta |

Além disso, a **interface é 100% botões** — não tem terminal, não tem campo de comando. Você pode operar todo o pipeline apenas com o mouse.

---

## 2. Instalação Passo a Passo (Windows 10/11)

> ⚠️ **Pré-requisito obrigatório 1:** Ativar **Suporte a Caminhos Longos** no Windows (sem isso o LLM local NÃO INSTALA — ele tem >400 chars de caminho no pacote sdist).
> ⚠️ **Pré-requisito obrigatório 2:** Python **3.12 ou 3.13** (não use alpha/beta). Baixe em https://python.org — **durante a instalação MARQUE A OPÇÃO "Add Python to PATH"**.

### 2.1. Ativar Long Paths (1 clique, reinicia o PC depois)

1. Abra o **Prompt de Comando como ADMINISTRADOR** (menu Iniciar → digite `cmd` → clique direito → Executar como administrador).
2. Copie e cole:
   ```cmd
   reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f
   ```
3. **Reinicie o notebook/desktop agora** (não adianta pular esse passo!).

### 2.2. Extraia o projeto PaPAHR-Python

Coloque a pasta `PaPAHR-Python` em um caminho curto e **sem acentos / sem espaços no nome** (evita erros no C compiador do llama.cpp):
```
D:\papapapa\PaPAHR-Python   ← recomendado
```

### 2.3. Crie o ambiente virtual (Python venv) e ative-o

Abra o **PowerShell** (como usuário normal, NÃO precisa admin) e execute:
```powershell
cd D:\papapapa\PaPAHR-Python
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Se aparecer um erro "permissão de execução de script", rode ISSO e digite **S** para Sim:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### 2.4. Instale os pacotes em 3 CAMADAS (evita rollback atômico!)

A instalação é separada em 3 arquivos `requirements-*.txt`. Se 1 falhar, os outros continuam instalados. **Sempre executar nesta ordem:**

**Camada 1/3 — Core (processamento + dados):**
```powershell
$env:PIP_LONG_PATHS=1   # (necessário no llama-cpp também)
pip install -r requirements-core.txt
```

**Camada 2/3 — Interface Gráfica:**
```powershell
pip install -r requirements-ui.txt
```

**Camada 3/3 — IA Local (opcional, 2 jeitos: recomendamos WHEEL CPU primeiro):**
```powershell
# 🟢 PRIMEIRA OPÇÃO (Recomendado no Windows: WHEEL pré-compilado CPU - SEM compilador)
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

# 🔴 SEGUNDA OPÇÃO (Apenas se a primeira falhar e você tiver Visual Studio Build Tools)
pip install -r requirements-llm.txt
```

### 2.5. Instale o entry-point (cria o ícone .exe do app)

```powershell
pip install -e . --no-deps
```

Isso cria:
`D:\papapapa\PaPAHR-Python\.venv\Scripts\papahr-gui.exe`  ← Ícone do app
`D:\papapapa\PaPAHR-Python\.venv\Scripts\papahr-cli.exe`  ← Linha de comando (técnico)

### 2.6. Coloque seus arquivos CNES locais (OPCIONAL, mas deixa o app MUITO mais poderoso!)

Se tiver os CSVs do CNES baixados, coloque em:
```
C:\DICIONARIO_DADOS\BASE_DE_DADOS_CNES_202512\
    tbEstabelecimento202512.csv
    tbMunicipio202512.csv
    tbLeito202512.csv
    tbTipoEstabelecimento202512.csv
    tbEstado202512.csv
    tbNaturezaJuridica202512.csv
```

O app detecta automaticamente e cria o mapa com geolocalização dos 5.570 municípios BR.

### 2.7. Baixe os modelos GGUF de IA local (opcional)

Baixe o **Phi-3 Mini Instruct 4k Q4_K_M.gguf** (~3,8 GB) ou **TinyLlama 1.1B Q4_K_S.gguf** (~600 MB, mais leve). Coloque em:
```
~/.papahr/models/Phi-3-mini-4k-instruct-q4.gguf
```

---

## 3. Como Usar — Aplicação Desktop

### 3.1. Como abrir o app

2 maneiras equivalentes:
```powershell
cd D:\papapapa\PaPAHR-Python
.\.venv\Scripts\papahr-gui.exe     # ícone do app
# OU (mais seguro se algo quebrou o .exe):
.\.venv\Scripts\python.exe -m papahr.ui.app
```

> 📌 **Atalho para o Desktop:** Clique com o botão direito em `papahr-gui.exe` → Enviar para → Área de trabalho (criar atalho). Agora é só dar duplo clique!

A tela inicial que aparece é esta (4 abas na sidebar esquerda):

```
 ┌──────────────────────────────────────────────────────────────────┐
 │ PaPAHR-Python v2.0            [▶ Wizard] [📊 Dashboard] [🧠 LLM] │
 ├──────────────┬───────────────────────────────────────────────────┤
 │ 1️⃣ ▶ Wizard  │                                                   │
 │              │  🔵 Passo 1: Sistema e Fonte                      │
 │ 🟢 4 PASSOS  │   ⚪ SIA-PA  ⚪ SIH-RD  ⚪ SIH-RJ  ⚪ SIH-SP       │
 │              │   📦 FTP DATASUS  📂 Pasta DBC Local              │
 │ 2️⃣ 📊 Dash   │  ─────────────────────────  ┌── KPIs ─────────┐  │
 │              │  🔵 Passo 2: Período         │ Linhas 0  Col 0 │  │
 │ 3️⃣ 🧠 LLM    │   [Último mês][3m][6m]      │ Tempo 0   R$ 0 │  │
 │              │   📅 Janeiro 2025  📅 Jul 25 └────────────────┘  │
 │ 4️⃣ ⚙️ Config │  ─────────────────────────                        │
 │              │  🔵 Passo 3: Localização                          │
 │              │   [Norte][Nordeste][Sudeste]  🇧🇷 27 chips UF      │
 │              │                                                     │
 │              │  🔵 Passo 4: Hospitais + [🚀 Executar Extração]  │
 └──────────────┴───────────────────────────────────────────────────┘
```

---

### 3.1. Aba 1️⃣ — Wizard de Extração (4 Passos)

> **Regra de ouro do usuário leigo:** Basta ir clicando nas bolinhas dos passos. Tudo tem opção padrão, você pode chegar em "🚀 Executar" em menos de 10 segundos.

#### 🔵 Passo 1 — Sistema & Fonte
Clique em um dos 4 **chips de sistema SUS**:
| Opção | Cor | Usar quando |
|---|---|---|
| **SIA-PA** | Verde · Ambulatorial | Procedimentos ambulatoriais — consultas, exames, medicamentos |
| **SIH-RD** | Azul · AIH Reduzida | Internações em geral (usar esse 90% das vezes!) |
| **SIH-RJ** | Roxo · AIH Rejeitadas | Auditoria de glosa |
| **SIH-SP** | Amarelo · AIH Serviços Profissionais | Honorários médicos separados |

Depois escolha a fonte dos arquivos:
- 📦 **FTP DATASUS** (padrão) — baixa automaticamente os DBCs do servidor ftp.datasus.gov.br.
- 📂 **Pasta DBC Local** — se já baixou os arquivos DBC, clique em **Selecionar Pasta** e aponte para onde estão.

As opções inferiores de **formato de saída (CSV / Parquet)** já vem marcadas por padrão. Deixa assim.

#### 🟣 Passo 2 — Período
Você tem **5 botões preset de período** (só clicar, não precisa digitar datas!):
```
[Último mês]  [Últimos 3 meses]  [Últimos 6 meses]  [Últimos 12 meses]  [Ano atual inteiro]
```

Ou, se preferir um período manual: clique nos **dois calendários (de/até)** formatados em português como "Julho de 2025".

#### 🟠 Passo 3 — Localização (UF / Municípios)
1. Primeiro clique na **região** — Norte / Nordeste / Sudeste / Sul / Centro-Oeste — ou em **🇧🇷 TODAS**.
2. Depois clique nos **27 chips de UF (estados)**. Eles ficam verde claro quando selecionados.
3. A **lista de municípios ao centro** já mostra apenas os municípios das UFs selecionadas. Clique em quantos quiser (multi-seleção com Ctrl ou Shift).
4. Os municípios selecionados aparecem **como chips coloridos acima da lista** (dá para clicar no × do chip para remover 1 por 1).

#### 🟣 Passo 4 — Hospitais (CNES)
1. Busque o hospital digitando no campo de busca (ele já filtra a lista ao vivo). Ex.: "INSTITUTO DO CANCER"
2. Clique nos hospitais na lista → eles viram chips CNES.
3. **Super botão útil:** 📋 **Colar CNES em lote** — abre uma janela pra colar vários CNES de uma vez (separados por vírgula, quebra de linha ou espaço). Ex.: `2561492, 2481286, 2370923`
4. Quando terminar tudo, clique no botão **verde 🚀 EXECUTAR EXTRAÇÃO** (canto inferior direito do passo 4).

#### Após o resultado
A barra de progresso vai andar. Quando chegar a 100%:
- **4 KPIs coloridos** (Linhas / Colunas / Tempo / Valor R$) atualizam automaticamente.
- **Painel de Prévia** abre sozinho (mostra 50 primeiras linhas da tabela).
- Botão **📈 IR PARA DASHBOARD** fica verde e habilitado. Clique nele!

> 💡 **Dica do painel:** Não quer ver a prévia? Clique em **📋 Ocultar prévia de dados ▲** no topo direito. Arraste a alcinha do splitter para a direita para fechar 100% o painel de KPIs e ficar só com o Wizard.

---

### 3.2. Aba 2️⃣ — Dashboard Saúde Populacional

Ao abrir o dashboard você vê **6 cards de KPI gigantes na parte de cima**, depois **5 abas de detalhe** (Risco, Eficiência, Território, Transição, Visão Geral).

#### 🟡 Os 6 KPIs (cada um com cor própria)
| Ícone + Label | Como calcular (rápido) | Exemplo |
|---|---|---|
| 🏥 **Estabelecimentos** | Quantos CNES selecionados | 4 |
| 📄 **AIHs / APACs** | Total de linhas no dataset | 48.219 |
| 💸 **Custo Total Aprovado (R$)** | Soma valor aprovado (formato BR · 1.234,56) | R$ 4.128.777,12 |
| ⚠️ **ICSAP %** | Internações p/ condições evitáveis (ver anexo 4.2) | 32,4 % |
| 🛏️ **LOS Médio (dias)** | Tempo médio de internação | 5,2 dias |
| ↩️ **Readmissão ≤30d (est.)** | % estimado de retorno em 30 dias | 11,8 % |

Abaixo dos KPIs, existem os botões globais:
- [🔄 Recalcular indicadores]
- [📥 Carregar CSV / Parquet]
- [📤 Exportar 12 CSVs (todas as tabelas)]
- Menu **🧪 Sistema analisado** (SIA-PA / SIH-RD etc)
- Menu **🪨 Modelo Huff: β (fricção)** — altera o decaimento da distância
- Checkbox **Incluir Modelo Gravitacional Huff (lento)**

---

#### 1ª aba: 🎯 Estratificação Risco / Demanda
Aqui o risco do paciente. Colunas importantes:
| Hospital | pct_ALTO | pct_MEDIO | pct_BAIXO | LACE médio | CMI |
|---|---|---|---|---|---|
| ICESP | 44% | 37% | 19% | 11,8 | 1,82 |
| HCFMUSP | 38% | 41% | 21% | 10,2 | 1,67 |

- Estratos ALTO/MÉDIO/BAIXO: calculados com LACE + Charlson + linha de cuidado. **Alto ≈ 50% do custo**.
- CMI ≥ 1,5: casuística complexa (referenciada).
- **Gráfico pyqtgraph** ao lado: barras empilhadas por hospital.

---

#### 2ª aba: 💡 Eficiência Clínica / Valor
| Hospital | LOS Observado | LOS Esperado | LOS O/E | Custo O/E | Margem Est. |
|---|---|---|---|---|---|
| ICESP | 5,1 | 5,8 | **0,88** | **1,03** | R$ +245k |

- **LOS O/E < 1,00:** eficiente (paciente fica MENOS tempo que o benchmark nacional). ✅
- **LOS O/E > 1,05:** possível oportunidade de gestão de leitos. ⚠️
- Custo O/E análogo.
- Margem estimada = valor aprovado − benchmark LOS · custo/dia (medido por linha de cuidado).

---

#### 3ª aba: 🗺️ Território → **Mapa de Círculos Concêntricos SIGTAP** (o CARRO CHEFE!)

Esta é a visualização que você pediu no pedido original. **Funciona 100% com a geolocalização CNES local.**

##### Como o mapa é desenhado
Para **CADA hospital selecionado**, o app desenha **5 anéis concêntricos** ao redor da coordenada (lat,lon) do município do hospital:

```
       🩸 80 km (vermelho, taxa mais alta / translúcido)
   💧 40 km
  🌤️ 20 km
   🔵 10 km
     🟢 5 km  (verde, taxa mais baixa)
   ◉  PIN do hospital
```

Cada município do Brasil (com população + IBGE + CNES) é **segmentado no anel cuja distância Haversine para o hospital ele pertence**. Dentro do anel o sistema calcula:

```
Taxa bruta de internação (por 1.000 habitantes) do anel = 1.000 × (N internações anel) / (População do anel)
```

A **cor do anel é um QRadialGradient transparente→cor forte**, mapeada contra a legenda vertical do canto inferior direito (degradê verde→azul→amarelo→vermelho).

##### Interação com o mapa (100% mouse)
| Ação | Como fazer |
|---|---|
| **Mover (pan)** | Clique esquerdo + arrastar o mapa |
| **Zoom** | Rodinha do mouse (mais lenta do que o Google Maps, porque é QPainter) |
| **Selecionar um hospital** | Clique 1 vez no pin colorido (◆). Atualiza a legenda de status embaixo. |
| **Ver só o anel de um único hospital** | Clique no pin, o slot filtra. |

##### Os 2 FILTROS de visualização SIGTAP (topo da aba Território)

**Filtro 1 — Agrupar por:**
| Modo | O que colore | Colunas usadas |
|---|---|---|
| 🟢 **Grupo SIGTAP (padrão)** | Maior grupo dentro do anel (ex: "05-Oncologia") | `CO_GRUPO` / `NO_GRUPO` |
| 🟦 **Forma de Organização** | Maior forma organizacional (Ambulatório/Hospital Dia/HG...) | `CO_FORMA_ORGANIZACAO` / `NO_FORMA_ORGANIZACAO` |
| 🟠 **Procedimento individual** | Top procedimento por anel (ex: Quimioterapia Oncológica) | `CO_PROCEDIMENTO_REALIZADO` / `NO_PROCEDIMENTO_REALIZADO` |

**Filtro 2 — Filtrar item específico:**
Abre dropdown com todos os nomes de Grupo/Forma/Procedimento encontrados no dataset + opção padrão **🌐 TODOS GERAL (agregado)**.

Exemplo prático: Selecione "Agrupar por=Grupo SIGTAP", depois "Filtrar=05-Oncologia". O mapa mostra apenas internações ONCOLÓGICAS dentro dos anéis, destacando em VERMELHO os anéis onde o município tem taxa por 1000 habitantes mais alta de quimio/radioterapia.

> 💡 **Dica:** Para usar os 3 modos do mapa você precisa **baixar a tabela SIGTAP** (automático na primeira vez que o filtro pede). Usa mirror do GitHub RenatoKR antes do FTP: download em <10s.

---

#### 4ª aba: ↩️ Transição de Cuidado (10 linhas)
Mostra para cada linha de cuidado ICC, DPOC, IAM, AVC, Artroplastias Q/J, Sepse, Fratura de Fêmur, Câncer de Mama, CAP (Pneumonia):
| Linha | Altas | Contato 48h | Contato % | Readmissão 30d | Readm. % |
|---|---|---|---|---|---|
| IAM | 128 | 89 | **69,5%** | 14 | **10,9%** |
| DPOC | 301 | 211 | 70,1% | 32 | **10,6%** |

---

#### 5ª aba: 📋 Visão Geral (séries temporais + Top 10)
- Série temporal mensal: barras empilhadas de internações e linha da taxa ICSAP.
- Top 10 procedimentos + Top 10 especialidades do período.

---

### 3.5. Aba 3 🧠 — Assistente LLM Local (contexto do seu dataset)

Se você instalou o llama-cpp-python (camada 3) e baixou um `.gguf` válido:
1. Clique em **🔄 Ler cabeçalho do dataset**.
2. Pergunte coisas em português natural no campo de texto:
   - *"Qual hospital tem a taxa ICSAP mais alta? Porque?"*
   - *"Mostre-me os 5 procedimentos mais caros no 2º trimestre de 2025."*
3. Clique em **🧠 Perguntar** (tem barra de progresso, LLM local demora 5-30s dependendo da CPU).

Funciona **TOTALMENTE OFFLINE** (privacidade!). Nada é enviado para a OpenAI/Gemini.

---

### 3.6. Aba 4 ⚙️ — Configurações
- Pastas de dados / cache / saída / logs (cada uma com "Selecionar Pasta")
- Caminho do modelo LLM GGUF (clique em "Selecionar arquivo" e aponte para o `.gguf`)
- Nível de log: INFO / DEBUG / WARNING / ERROR
- Salvar: botão 💾 **Salvar Configurações**

---

## 4. 📚 Anexo Metodológico e Conceitual — Gestão de Saúde Populacional em Hospitais

> Referência: Ministério da Saúde (Brasil) · Portarias GM 2.206/2017, 399/2008 (PAB) · OMS · Eurostat (O/E standardização).

### 4.1. Modelo Gravitacional de Huff (Atração Espacial de Pacientes)

O **Modelo Gravitacional (Huff, 1963)** responde à pergunta:
> *"Qual a chance de um paciente no município i escolher o hospital j para se tratar (ao invés de outro hospital k)?"*

Fórmula canônica usada neste app:
```
               M_j / (d_ij ^ β)
P_ij = ────────────────────────────
          Σ_k   M_k / (d_ik ^ β)
```

Onde:
| Variável | Como medimos no PaPAHR-Python |
|---|---|
| **M_j**  (massa do hospital j) | `Número de leitos SUS do hospital j × (1 + CMI_j / 4)` (leitos do CNES local · CMI daquele hospital ajusta para complexidade) |
| **d_ij** (distância) | Distância **Haversine WGS84 em km** entre o centróide do município i e a localização do hospital j. |
| **β** (expoente de fricção espacial) | 3 valores padrão na literatura SUS: <br>• β = **2,2** (ambulatorial, mais sensível à distância)<br>• β = **1,5** (média complexidade)<br>• β = **0,9** (alta complexidade oncológica/transplante — paciente viaja mais) |

**Outputs no dashboard:**
- **Raio 50% de atendimento (km):** distância mínima do hospital tal que **≥50% da sua força de atração (probabilidade agregada)** caia dentro dele. Medido na tabela `catchment_profile` do `HuffResult`.
- **Raio 80% de atendimento (km):** idem para 80% (usado em contratos públicos para "área de cobertura obrigatória").
- **Matriz O-D completa:** Município origem × CNES destino com distância, força, probabilidade. Útil para auditoria de regulação.

> Limitações importantes: assume hospital homogêneo (mesma qualidade percebida) e não considera barreiras como rios/rodovias/linhas de ônibus. Beta=0,9 é uma aproximação.

---

### 4.2. Taxa de Internações por Condições Sensíveis à Atenção Primária (ICSAP) — 16 Grupos Portaria 2.206/2017 MS

ICSAP = internações que **poderiam ter sido evitadas** se a Atenção Primária à Saúde (APS / UBS / Estratégia Saúde da Família) fosse de qualidade e oportuna.

A Portaria GM 2.206/2017 define **16 grupos de CID-10**. Cada grupo no PaPAHR-Python tem uma lista de **100 padrões regex prefixo de CID-10** para match exato e tolerante a subcategorias.

| Grupo | Condição | Faixas CID-10 (exemplos) |
|---|---|---|
| 1 | Gastroenterite preventível (UBS poderia tratar) | **A00-A09, E86, K52.2, K52.8, K52.9, R19.5** |
| 2 | Anemia por deficiência de ferro | **D50, D50.0, D50.1, D50.8, D50.9, E61.1** |
| 3 | Deficiências nutricionais (criança e idoso) | **E40-E46, E50-E64, E66, R62, R63.4, R64** |
| 4 | Tuberculose (tratamento 100% ambulatorial RENASE) | **A15-A19, A32, A33, A34, A36, U88.0** |
| 5 | Hanseníase | **A30-A30.9** |
| 6 | Difteria, Coqueluche, Tétano (vacinas UBS) | **A36, A37, A37.0, A37.9, A35** |
| 7 | Paralisia infantil (polio, VOP/VIP) | **A80, A80.0-A80.9** |
| 8 | Meningite bacteriana prevenível por vacina | **A39, A39.0-A39.4, A39.8, G00, G00.0-G00.9** |
| 9 | **Hipertensão arterial (essencial + 2ª)** | **I10-I15 (9 grupos sub!) incluindo I11.0/I11.9/I13.0/I13.10/I13.11/I13.2/I50.9** |
| 10 | **Diabetes mellitus (tipo 1 e 2 + complicações agudas)** | **E10-E14 c/ subcategorias 0/1/2/3/4/5/8/9 c/ 5ª sub (sem 6 nefropatia)** |
| 11 | Epilepsia | **G40, G40.0-G40.9, G41.0, G41.1, G41.2, G41.8, G41.9, O15.0, O15.1** |
| 12 | **DPOC + Asma em adultos** | **J40-J47** (bronquite crônica/enfisema/asma/status asmático) exceto bronquiectasia/J47 isolada. |
| 13 | Insuficiência cardíaca congestiva | **I50, I50.0, I50.1, I50.20, I50.21, I50.23, I50.30, I50.31, I50.33, I50.40, I50.41, I50.42, I50.43, I50.810-I50.822, I50.84, I50.89, I50.9** |
| 14 | Angina pectoris sem infarto | **I20, I20.0/I20.1/I20.8/I20.9** |
| 15 | Infarto agudo do miocárdio (IAM) | **I21, I21.01-I22.9, I23.0-I23.8, I24.0-I24.1, I24.8, I24.9, I25.2** |
| 16 | **Infecções de urina e rins (ITU) — mulheres, idosos e gestante** | **N10, N11, N11.0, N11.1, N11.8, N11.9, N12, N13.6, N15.1, N15.9, N30.0-N34.1, N34.3, N39.0, O23.0-O23.4, O75.2, O86.1, P39.3** |

Como é medido no dashboard:
```
Taxa ICSAP global (%) = 100 × (N internações que deram match em qualquer um dos 16 grupos) / (Total AIHs)
Taxa ICSAP / município (ajustada) = Direta padronizada por idade/sexo (quando dado de censo estiver disponível)
```

> **Valor de corte (nos alertas automáticos do dashboard):**
> ICSAP > 20%: ⚠️ Atenção.
> ICSAP > 25% por hospital agregado: 🔴 "Alerta vermelho — integração APS→hospital frágil. Recomendar oficinas de regulação e e-SUS AB."

---

### 4.3. Índice LACE (Risco de Readmissão / Mortalidade 30 dias)

LACE é o acrônimo de: **Length of stay · Acuity (caráter) · Charlson · Emergency visits (6m)**.
Usamos lookup padrão OMS BR (adaptado do original canadense, válido para o SUS).

| Dimensão | Regra de pontuação no PaPAHR-Python |
|---|---|
| **LOS (L)** | 0 dias = 0; 1d=1; 2-3d=2; 4-6d=3; 7-13d=4; ≥14d=7 |
| **Acuity (A)** | Internação urgência/emergência (CIH coluna TIPO = 4-7) → **+3**. Eletivos = 0 |
| **Charlson (C)** | CCI adaptado Deyo ICD-10 (12 grupos + score por idade). Pontos: CCI 0→0; 1→1; 2→2; 3→3; ≥4→5 |
| **Visitas PS 6 meses (E)** | 0→0; 1→1; 2→2; 3→3; ≥4→4 (coluna `qt_visitas_pronto_socorro` OU calculado por join SIA) |

Depois soma tudo → índice LACE (0 a 19 máximo).

**Como transformamos em estratos de risco:**
| LACE | Categoria | Intervenção sugerida |
|---|---|---|
| 0 – 4 | 🟢 BAIXO | Alta direta, telefone de retorno em 48h. |
| 5 – 9 | 🟡 MÉDIO | Visita de ACS/ESF + contato telefônico. |
| 10+ | 🔴 ALTO | Plano de cuidado pós-alta estruturado + consulta em ≤15 dias + Home Care se elegível. |

---

### 4.4. Case Mix Index (CMI) — Complexidade da Casuística
CMI é um número: CMI = 1,00 significa "complexidade média nacional esperada".
CMI > 1,00 significa mais complexo. Medimos por:
```
CMI_hospital = Σ( DRG_c × peso_DRAGEM_c ) / N_altas
```
Onde `peso_DRAGEM_c` usa a **tabela de pesos DRG Brasil (1.250 grupos do SIHSUS)** — embutida hardcoded no módulo `papahr/dashboard/metrics.py` `_WEIGHTS_DRG`.

---

### 4.5. Eficiência Clínica e LOS O/E (Observado / Esperado)
Razão padrão EUROSTAT para comparar hospitais de diferentes tamanhos.
```
LOS O/E_i = (LOS médio OBSERVADO hospital i) / (LOS ESPERADO benchmark LINHA_CUIDADO)
```
- Benchmark LOS nacional das 10 linhas de cuidado (100-120 milhões de AIHs 2023) está embutido em `_BR_BENCHMARKS_LINHAS_CUIDADO` no `metrics.py`.
- Custo O/E = (R$ médio por episódio observado) / (R$ médio esperado por linha de cuidado).

---

### 4.6. Transição de Cuidado (Readmissão 30d + Contato 48h pós-alta)
- **Readmissão ≤ 30d**: mesma UBS + mesmo CID principal ou CID relacionado (mesmo capítulo ICD-10) em ≤30 dias. É uma **estimativa estatística** no modo batch.
- **Contato ≤ 48h pós-alta**: flag booleano 0/1, coluna `contato_48h_pos_alta` (preenchida na extração ou assumida 0 se faltar).
- Meta BR 2024-2026 (PNAISM): Contato 48h ≥ 70% das altas por linha de cuidado.

---

### 4.7. Tabela SIGTAP (Sistema de Gerenciamento da Tabela de Procedimentos, Medicamentos e Materiais, Órteses, Próteses e Materiais Especiais do SUS)

A SIGTAP é a base canônica do SUS para valores de procedimentos e agrupadores. Neste app nós usamos o espelho **GitHub RenatoKR/SIGTAP (https://github.com/RenatoKR/SIGTAP)** como **fonte primária** (mais rápido, não depende da disponibilidade do FTP DATASUS).

A tabela é **baixada e cacheada em `~/.papahr/cache/sigtap/` na primeira vez**. Depois, não precisa de internet nunca mais.

Ela habilita três coisas no app:
1. **Valores corretos por procedimento** (quando o DBC de origem não tem o VL_APROVADO, nós recalculamos via SIGTAP).
2. **3 níveis de agrupamento usados no mapa concêntrico:**
   - **Grupo SIGTAP:** ex: `01-Ações Promoção e Prevenção`, `05-Oncologia` (28 grupos).
   - **Forma de Organização:** ex: Unidade Básica, Ambulatório Especializado, Hospital Dia, Hospital Geral (7 formas).
   - **Procedimento individual:** código de 10 dígitos `CO_PROCEDIMENTO` + nome (14.000 itens total).
3. **Procedimentos revogados:** cada extração emite um aviso quando encontra um procedimento já retirado da tabela atual.

---

### 4.8. 10 Linhas de Cuidado Hospitalar Prioritárias (Padrão BR 2024)
Cada linha tem no código um conjunto de códigos CID/CBO/DRG e seu benchmark nacional LOS/R$/readmissão:
1. ❤️ Síndrome Coronariana Aguda (SCA / IAM)
2. 💨 Doença Pulmonar Obstrutiva Crônica (DPOC)
3. 🦵 Artroplastia de Quadril (protése Q)
4. 🦶 Artroplastia de Joelho (protése J)
5. ❤️‍🩹 Insuficiência Cardíaca Congestiva (ICC)
6. 🧠 Acidente Vascular Encefálico (AVC)
7. 🫁 Pneumonia Adquirida na Comunidade (PAC)
8. 🦠 Sepse / Choque séptico
9. 💔 Fratura de Fêmur Proximal (fratura fêmur idoso)
10. 🎗️ Linha Oncológica (Câncer) — mama, próstata, pulmão, colo, colorretal.

---

### 4.9. Índice de Comorbidade de Charlson (CCI) Adaptado ICD-10 (Deyo et al.)
16 grupos de doença crônica com pesos. Pesos por idade (≥50a +1 a cada 10a). 12 grupos regex estão no módulo metrics.py `_CHARLSON_DICT`. Usado no cálculo LACE (componente C).

---

## 5. 🛠️ Anexo Técnico: Resolução de Problemas Comuns

| Sintoma | Porquê | Como corrigir |
|---|---|---|
| Instalação `llama-cpp-python` dá erro de rollback | Path > 260 caracteres + falta wheel | Ative Long Paths no regedit. Depois use a wheel CPU: `pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu`. |
| `ModuleNotFoundError: papahr.core.utils` | Falhou `pip install -e .` | Rode `pip install -e . --no-deps` novamente. |
| `NameError: QLayout is not defined` no `FlowLayout` | Arquivos antigos em cache. | Apague todas as pastas `__pycache__` dentro de `papahr/`. |
| Erro `DataFrame or is ambiguous (Polars)` | Versão antiga do código | `git pull` ou apague `__pycache__` e reinstale entry-point. |
| `ColumnNotFound CO_TIPO_ESTABELECIMENTO` no join CNES | Dtype misturado i64 vs Utf8 em tabelas dicionário | Não precisa fazer nada: a versão atual do `CNESData._normalize_dicts` força cast Utf8 de todos os códigos ANTES do join. |
| Mapa concêntrico não abre (sem pins) | Não carregou CNES local. | Extraia o CNES CSV para `C:\DICIONARIO_DADOS` ou cole em `D:\...\BASE_DE_DADOS_CNES_202512\`. |
| App não abre (crash antes da janela) | structlog WindowsPath → `.write()` | Versão atualizada em `logging_config.py` resolveu: usa stdout PrintLoggerFactory + StreamHandler UTF-8. |

---

## 6. ℹ️ Sobre o Produto e a Equipe

**PaPAHR-Python** é um software **100% código-aberto**. O código original em R foi criado para uso em Oncologia e Saúde Populacional. Esta versão Python 2.0 foi reescrita com **Polars** (processamento vetorizado em Rust), **PySide6/Qt6** (UI desktop com tema teal Material Design 3), **structlog** (logging estruturado UTF-8) e **llama-cpp-python** (LLM local privado).

### Contatos / Repositórios importantes:
- RenatoKR / SIGTAP: https://github.com/RenatoKR/SIGTAP  (tabela de procedimentos — usamos como mirror principal)
- DATASUS FTP: ftp.datasus.gov.br  (fontes originais DBC)
- Polars: https://pola.rs  (Apache Arrow em Rust)
- PySide6 / Qt: https://wiki.qt.io/Qt_for_Python

---

> **✅ Fim do manual.** Em caso de dúvidas, vá direto para o **Passo 1 do Wizard** e vá clicando em "Próximo" — tudo tem padrão e funciona sem configuração!
