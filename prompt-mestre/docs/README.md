# PaPAHR 2.0 — Python (clone melhorado do R)

> **P**acote da **P**rodutividade **A**mbulatorial e **H**ospitalar do **R** (agora em **Python**).

Ferramenta desktop para extrair, limpar e enriquecer microdados da produção
ambulatorial (SIA-PA) e hospitalar (SIH-RD, SIH-RJ, SIH-SP) do DATASUS/SUS,
integrando automaticamente CNES (estabelecimentos), SIGTAP (procedimentos,
CBO, CID, grupos, financiamento) e IBGE (municípios, regiões, estados).

## Diferenciais desta versão Python (vs pacote R original)

| Característica | R | Python 2.0 |
|---|---|---|
| Motor de dados | dplyr (R) | **Polars** (Rust, ~10× mais rápido, baixa memória) |
| UI | Nenhuma (linha de comando) | **PySide6/Qt6 + Material Design 3 Dark Teal** |
| CLI | Funções R | **CLI rich (typer/rich + barra de progresso)** |
| IA | Nenhuma | **LLM local (Phi-3/TinyLlama via llama.cpp)** — privado |
| Segurança | — | Validação Pydantic, path-traversal protection, keyring |
| Empacotamento | R package | **.exe instalável (PyInstaller + Inno Setup)** |
| Formatos de saída | CSV | **Parquet (zstd), CSV, XLSX** |
| Download | ftp (sync) | ftp sync + async + retry exponencial |
| Testes | — | pytest + ruff + mypy |

## Sistemas suportados

- **SIA-PA** — Produção Ambulatorial
- **SIH-RD** — AIH Reduzida (Aprovadas)
- **SIH-RJ** — AIH Rejeitadas
- **SIH-SP** — AIH Serviços Profissionais
- **CNES-ST** — Cadastro Nacional de Estabelecimentos de Saúde
- **SIGTAP** — Tabela Unificada de Procedimentos (procedimentos, CBO, CID, grupos, financiamento)
- **IBGE** — Municípios e regiões geográficas

## Início rápido

```powershell
cd D:\papapapa\PaPAHR-Python
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Baixa dados auxiliares (counties, CNES, SIGTAP)
papahr-cli prepare-aux --all

# GUI
python main.py

# CLI - Processa SIA-PA do Ceará, jan-mar/2023, dois hospitais
papahr-cli run --system SIA-PA --state CE `
    --year-start 2023 --month-start 1 --year-end 2023 --month-end 3 `
    --establishments 2561492,2481286

# Análise LLM local do resultado
papahr-cli summarize output\outputSIA_PA.parquet -q "Quais os top-5 procedimentos?"
```

## Estrutura

```
PaPAHR-Python/
├── papahr/
│   ├── __init__.py
│   ├── __main__.py              # python -m papahr → GUI
│   ├── cli.py                   # papahr-cli
│   ├── config.py                # Pydantic settings
│   ├── security.py              # Validação + path safe + keyring
│   ├── logging_config.py        # structlog
│   ├── core/
│   │   ├── dbc_reader.py        # Leitor DBC (BLAST decompress)
│   │   ├── ftp_client.py        # FTP sync + async + retry
│   │   └── utils.py
│   ├── datasources/
│   │   ├── counties.py          # IBGE municípios
│   │   ├── cnes.py              # CNES-ST + CADGER
│   │   └── sigtap.py            # SIGTAP TabelaUnificada
│   ├── processors/
│   │   └── __init__.py          # SIA-PA, SIH-RD/RJ/SP Polars
│   ├── pipelines/
│   │   └── __init__.py          # Remote (FTP) + Local (DBC)
│   ├── llm/
│   │   └── __init__.py          # llama-cpp-python, Phi-3 Mini
│   └── ui/
│       ├── app.py               # MainWindow Qt6
│       ├── pages/               # PipelinePage, LLMPage, SettingsPage
│       ├── widgets/             # Cards, KPIs, Toggle, Progress
│       └── styles/material_dark_teal.qss
├── tests/
├── build/
│   ├── papahr.spec              # PyInstaller
│   └── installer.iss            # Inno Setup (Windows installer)
├── main.py                      # Entry point GUI
├── pyproject.toml
└── requirements.txt
```

## LLM local (privacidade total, ~3GB RAM)

Baixe um modelo GGUF (recomendado):
- **Phi-3 Mini 4K Instruct Q4_K_M** — 3.8B params, ~2.6GB, ~10 tok/s CPU 4 threads
- TinyLlama-1.1B Chat Q4_K_M — 1.1B params, ~700MB, mais rápido
- Gemma 2 2B IT Q4_K_M — Google, ~1.5GB

Depois defina o caminho em **⚙ Configurações → Modelo (GGUF)**. Funciona 100%
offline e **nenhum dado do SUS é enviado para servidores externos**.

## Build instalável Windows

```powershell
pip install -e .[dev]
pip install pyinstaller
pyinstaller --clean build\papahr.spec
# Instalador:
# & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" build\installer.iss
```

Saídas:
- `dist\PaPAHR.exe` — single-file exe (~80-120MB)
- `dist-installer\PaPAHR-Setup-2.0.0.exe` — Instalador Wizard com associação de arquivos

## Segurança

- **Validação de entradas**: Todos os inputs passam por modelos Pydantic
  (UF válida, CNES 7 dígitos, códigos IBGE, intervalo de datas).
- **Path traversal protection**: `safe_path()` garante que caminhos de
  usuário permaneçam no diretório base.
- **Segredos**: Gerenciados via `keyring` (Windows Credential Manager).
- **Criptografia simétrica**: Helper `SecretManager` com Fernet (AES-128-CBC).
- **Sem envio de dados para cloud**: LLM opcional roda localmente.

## Qualidade de código

```powershell
pip install -e .[dev]
ruff check papahr tests           # lint
ruff format papahr tests          # formatter
mypy papahr                       # tipagem
pytest tests -q --cov=papahr     # testes + cobertura
```
