# Guia Rápido: Como usar o PROMPT MESTRE em OUTRO LLM (GPT-4o, Claude Opus, etc.)

Este guia passo a passo garante que o assistente de outro LLM assume o controle
como orquestrador do Gauntlet Loop sem pular regras críticas.

---

## 🛠️ Passo 1: Preparação (ANTES de abrir o LLM)

### 1.1 Verifique seu ambiente GSPHosp-Novo no Windows
Garanta que está tudo pronto:

```powershell
# 1. Sempre trabalhe DENTRO da pasta do PROJETO (não em d:\papapapa\ solto)
cd d:\papapapa\GSPHosp-Novo

# 2. Verifique se o .venv está ligado
.\.venv\Scripts\Activate.ps1

# 3. Smoke teste inicial
.venv\Scripts\python.exe Testar-GSPHosp.py
# Esperado: 16 PASS / 0 FAIL / 0 SKIP
```

👉 Se o Smoke não der 16 PASS, não adianta: o LLM vai perder tempo re-diagnosticando problemas de ambiente.

### 1.2 Verifique se você tem um GH_TOKEN (opcional, mas recomendado)
Para deploys sem janela de login GitHub. Gere em:  https://github.com/settings/tokens (classic, escopo `repo`)
```powershell
$env:GH_TOKEN = "ghp_xxxxxxxxxxxxxxxxxx"
```
Armazene esta linha no seu `$PROFILE` PowerShell se quiser permanente.

---

## 🚀 Passo 2: Abra o LLM e COLE o PROMPT MESTRE (3 CÓDIGO ABAIXO:

```
==============================
 Copiar marcador 1 começo
```

1. **Abra a plataforma do LLM (ex: chat.openai.com/?model=gpt-4o).
2. **Cole EXATAMENTE o conteúdo** de `d:\papapapa\GSPHosp-Novo\prompt-mestre\PROMPT-MESTRE-GSPHosp-v2.0.md
3. **NÃO RESUMA. NÃO EDITE. Cole o arquivo todo (ele foi formatado em Markdown puro. O LLM precisa receber INTEIRO.
4. **Role até o FINAL do PROMPT MESTRE para a seção **🎯 PRÓXIMA AÇÃO — O QUE EU, ORQUESTRADOR, QUERO QUE VOCÊ FAÇA AGORA.**
5. **Edite o placeholder com o texto da tarefa atual.

Exemplo de placeholder preenchido (tarefa corrigir LACE):

```
TAREFA ATUAL: Implementar F1 Índice LACE real (Faltante)
DETALHES: O índice LACE (LOS/Acuidade/Charlson/Visitas ED) em metrics.py está retornando 0 para 100% dos pacientes do SIH-RD. O resultado é que 100% dos pacientes caem em "BAIXO". Precisamos mapear as colunas reais do SIH-RD para os 4 componentes do LACE conforme MANUAL_BENCHMARKS e retornar estratificacao_risco shape (N,3) = estratos BAIXO/MÉDIO/ALTO com ao menos 5% de distribuição não BAIXO no dataset de teste.
ARQUIVOS ALVO PRINCIPAIS: papahr/dashboard/metrics.py, papahr/core/dashboards/cnes_loader.py (para colunas auxiliares)
MODO: Corretivo (bug)
PRIORIDADE: P0
SESSÃO DEBUG ID: metrics-lace-zero-bug
```

6. **Envie** a mensagem e AGUARDE. O LLM vai disparar o Gauntlet Loop.

---

## 🧠 Passo 3: O QUE ESPERAR do LLM (fluxo correto de mensagens)

### Primeira resposta do LLM deve conter 4 blocos:
- **(a) Cria arquivo `debug-metrics-lace-zero-bug.md com 5 hipóteses falseáveis (Evidence Gate).
- **(b) Leitura dos arquivos do projeto (via code search por LACE).
- **(c) Confirmação do Smoke 16 PASS rodou baseline.
- **(d) Menção:** Vou rodar 3 voltas Gauntlet Loop: Construtor → Crítico Cego → Catraca."

### Durante as próximas mensagens, o LLM deve invocar **subagentes (ferramentas nativas dele):
1. **Construtor implementa alteração de código.
2. **Crítico Cego cria _critico_lace.py com testes unitários.
3. **Catraca decide loop volta 2x, depois FECHADO.

### No final, o LLM deve responder com:
- Relatório de 3-4 frases curtas.
- **Resultado Final: 16 PASS / 0 FAIL.
- Headless dashboard smoke = 0 exceções.
- Link para Pull Request (se pediu deploy)

---

## ⚠️ Passo 4: SINAIS VERMELHOS (se o LLM fizer isto, ABORTE e reinicie com o prompt de novo)

- ❌ "Vou pular a etapa para economizar tokens"
- ❌ "Resumi o documento em 2 parágrafos"
- ❌ "Acho que vai funcionar confia em mim"
- ❌ Modificou mais de 10 arquivos sem nem ter rodado o smoke.
- ❌ Esqueceu do arquivo _critico_X.py de validação.

→ NESTES CASOS: responda com a frase exata abaixo.
```
"Você violou as regras do HC-1 e HC-2. Recomece. Volte 3 iterações.
Não prossiga sem ler INTEGRALMENTE prompt-mestre/PROMPT-MESTRE-GSPHosp-v2.0.md, INDEX.md, SUBAGENTS-SPEC-Gauntlet.md.
Não pule etapas. Não resuma nada."
```

---

## 📋 Passo 5: Workflow recomendado para tarefas GRANDES (ex: "implementar o 12 funcionalidades F1 a F12")

Não pedir tudo de uma vez. Fazer em **ondas de tarefas**:

### 🚢 ONDA 1 (F1, F2, F3, F4) — P0
1. Rodar LACE → smoke de novo.
2. Rodar CMI real via DRG map → smoke.
3. Rodar LOS O/E e Custo O/E benchmark.
4. Rodar ICSAP 16 grupos.
5. **Resultado esperado**: dashboard começa mostrar distribuição não uniforme!

### 🚢 ONDA 2 (F7, F8, F9) — Território completo
1. Huff integrado ao CNES.
2. Mapa concêntrico mostra círculos de 50%/80%.
3. Decaimento exponencial λ R².

### 🚢 ONDA 3 (F5, F6, F10, F11, F12) — Transição + Glosas + LLM
- Menor prioridade.

---

## 📋 Checklist de entrega POR TAREFA (antes de dizer "pronto")

Antes de aceitar que o LLM marcou a tarefa "entregue":
- [ ] Rodou `Testar-GSPHosp.py → 16 PASS / 0 FAIL?
- [ ] Arquivo debug-*.md §diagnóstico e status [CLOSED]?
- [ ] _critico_<TASK>.py PASS?
- [ ] Headless dashboard smoke NÃO tem [!!!]?
- [ ] (Se UI) abriu uma screenshot ou snapshot e botões estão com texto branco #FFFFFF e hover amarelo #FEF08A?
- [ ] (Se deploy) branch TRAE existe no GitHub e link PR válido?

Se marcou tudo sim → ok.
