---
name: gsphosp-autopilot
description: >-
  Piloto automático e especialista em auditoria, execução, testes e inteligência hospitalar do GSPHosp v2.0.0.
  Use quando o usuário solicitar execução autônoma, testes de dados, auditoria de métricas de saúde, correção de bugs,
  ou operação completa do sistema GSPHosp sem requerer conhecimentos de programação.
---

# GSPHosp Autopilot — Especialista Autônomo em Gestão de Saúde Populacional

Esta skill estende as capacidades do agente para operar o sistema **GSPHosp v2.0.0** de forma 100% autônoma, transparente e sem necessidade de intervenção técnica do usuário.

---

## 🎯 Capacidades Autônomas

1. **Ingestão Automática de Dados DATASUS**:
   - Auto-descoberta de bases locais em `C:\DICIONARIO_DADOS\BASE_DE_DADOS_CNES_*`.
   - Suporte transparente a arquivos DBC, DBF, CSV e ZIP de SIH-RD, SIH-RJ, SIH-SP e SIA-PA.
   - Pós-filtro geográfico vetorizado 100% nativo Polars em milissegundos.

2. **Cálculo Completo de Indicadores de Saúde Populacional**:
   - **Modelo Gravitacional de Huff**: Atração hospitalar e decaimento Haversine ($\beta = 2.2, 1.5, 0.9$).
   - **ICSAP**: 16 Grupos de Condições Sensíveis à Atenção Primária (Portaria MS nº 2.206/2017).
   - **Índice LACE**: Severidade, comorbidades de Charlson, tempo de internação e emergências.
   - **Eficiência Clínica O/E**: Razão observada/esperada para permanência e custo.
   - **Glosas & Reinternação 30d**: Auditoria financeira e acompanhamento de linhas de cuidado.

3. **Verificação de Resiliência de Interface e Tipos**:
   - Garantia de 0 botões com texto escuro sobre fundo colorido (Contraste AAA: fonte branca `#FFFFFF`, hover em amarelo ouro `#FEF08A`).
   - Blindagem defensiva de tipos Polars (`cast(pl.Utf8, strict=False)`) em todas as junções de CNES.
   - Toolbar em 3 linhas sem corte de botões em qualquer resolução.

---

## 🛠️ Procedimento de Execução Autônoma

### Passo 1: Diagnóstico e Validação do Sistema
Executar a compilação e a suíte de testes de regressão:
```bash
.venv\Scripts\python.exe Testar-GSPHosp.py
```

### Passo 2: Verificação do Pipeline e GUI Offscreen
Testar o pipeline da interface gráfica sem depender de servidor externo:
```bash
.venv\Scripts\python.exe scratch/test_gui_pipeline_end_to_end.py
```

### Passo 3: Auto-Fix e Atualização dos Indicadores
Se qualquer inconsistência for detectada:
- Aplicar correção defensiva no código.
- Re-compilar os arquivos modificados via `py_compile`.
- Re-executar a suíte de testes até obter `16 PASS / 0 FAIL`.
