# Especificação de Referência: Design & Layout Visual (`refs/UI_LAYOUT_SPEC.md`)

Este arquivo serve como referência de solo verdadeiro para o Agente Crítico Cego (Subagente B) validar os layouts, contrastes e componentes de interface.

---

## 1. Regras de Contraste e Tipografia (Nível AAA)

1. **Botões de Ação Primária & Execução**:
   - Background: Gradiente Teal `#0D9488` -> `#0F766E` ou Verde `#10B981`.
   - Cor da Fonte: **Branco puro `#FFFFFF` em peso ultra-negrito (`font-weight: 800` ou `900`)**.
   - Estado Hover: Fonte **Amarelo Ouro `#FEF08A`**, sinalizando interatividade sem perder legibilidade.
   - Proibição Absoluta: NUNCA utilizar `color: #0F1A1C`, `color: black` ou tons escuros em botões coloridos.

2. **Chips de Seleção de Hospital/UF/Período (`ChipButton`)**:
   - Estado Desmarcado: Fundo `#162023`, borda `#2D4A4A`, fonte `#94A3B8`.
   - Estado Marcado (`checked`): Fundo `#0D9488`, borda `#5EEAD4`, **fonte `#FFFFFF` (100% legível)**.

---

## 2. Estrutura do Wizard e Dashboard

1. **Wizard Pipeline (4 Passos)**:
   - Passo 1: Seleção de Sistema & Fonte de Dados.
   - Passo 2: Período em Meses & Filtro Temporal.
   - Passo 3: Localização SUS (UF, Macrorregião, DRS, Seleção de Hospitais).
   - Passo 4: Execução & Prévia dos Dados Expansível.
   - **Botão `🚀 EXECUTAR EXTRAÇÃO E ABRIR DASHBOARD` visível e ativo em todos os passos**.

2. **Toolbar do Dashboard**:
   - Organizada em 3 linhas limpas sem sobreposição.
   - Botões `⚡ Calcular` (`min-width: 110px`), `📥 Exportar CSV` (`min-width: 120px`), `🔄 Recarregar` (`min-width: 120px`) totalmente visíveis sem cortes.

3. **Painel de Prévia dos Dados**:
   - Quando o pipeline é concluído, o container expande automaticamente (`setChecked(True)` e `setVisible(True)`).
   - Exibe estatísticas completas: Linhas, Colunas, Tempo e Valor Total Aprovado (R$).
