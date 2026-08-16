# Contrato Técnico de Arquitetura (contract.md) — GSPHosp v2.0.0

## 1. Visão Geral e Princípios Invioláveis

O **GSPHosp (Gestão em Saúde Populacional Hospitalar)** é um sistema desktop analítico de alta performance para processamento de microdados do DATASUS (SIH-RD, SIH-RJ, SIH-SP, SIA-PA) e inteligência em saúde pública.

### Princípios da Catraca (Ratchet):
1. **Sem Retração**: Uma funcionalidade aprovada no Gauntlet Loop jamais pode regredir ou ser removida.
2. **Contraste de Alto Nível (AAA)**: 0 botões com texto escuro sobre fundos coloridos. Todos os botões utilizam fonte branca `#FFFFFF` com destaque em amarelo ouro `#FEF08A` no hover.
3. **Resiliência de Tipos Polars**: Toda junção em colunas de identificação hospitalar (`cnes`, `hospital_cnes`, `CO_CNES`) deve ser explicitamente convertida para `pl.Utf8` (`cast(pl.Utf8, strict=False)`) em ambos os lados para prevenir pânicos de runtime.
4. **Sem Truncamento de Interface**: Nenhum elemento de ação ou indicador da toolbar pode ser cortado em resoluções padrão (1280x720 ou superior).

---

## 2. Decomposição dos Módulos do Sistema

### Módulo 1: Data Ingestion & Pipeline Engine (`papahr/pipelines/`, `papahr/datasources/`, `papahr/core/`)
- Suporte a múltiplos formatos (DBC, DBF, CSV, ZIP, RAR, FTP DATASUS).
- Pós-filtro geográfico vetorizado 100% nativo Polars em milissegundos.
- CNES Repository com fallback gracioso em 5 camadas.

### Módulo 2: Motor de Métricas de Saúde Populacional (`papahr/dashboard/metrics.py`)
- Modelo Gravitacional de Huff ($\beta$ decay 2.2, 1.5, 0.9).
- ICSAP (16 grupos de causas evitáveis - Portaria MS nº 2.206/2017).
- Índice LACE (Length of stay, Acuity, Comorbidities, ED visits).
- Case Mix Index (CMI) e Eficiência Clínica O/E (LOS O/E e Custo O/E).
- Transição de Cuidado 30d e Reinternação Estimada.

### Módulo 3: Interface Gráfica & Wizard Interativo (`papahr/ui/pages/`, `papahr/ui/widgets/`)
- Wizard de Extração em 4 Passos com prévia de dados expansível.
- Dashboard em 8 abas analíticas + visualização geográfica/mapa.
- Transições de tela sem engasgos (Threads em `QThread`/`DashboardWorker`).

### Módulo 4: Persistência & Camada Medallion/DuckDB (`papahr/db/`)
- Armazenamento em arquitetura Medallion (`bronze`, `silver`, `gold`).
- DuckDB com tratamento de locks concorrentes e fusos horários no Polars.

---

## 3. Garantias de Qualidade e Testes de Regressão
- A suíte `Testar-GSPHosp.py` deve retornar **16 PASS / 0 FAIL / 0 SKIP**.
- O teste offscreen `scratch/test_gui_pipeline_end_to_end.py` deve retornar **100% PASS**.
