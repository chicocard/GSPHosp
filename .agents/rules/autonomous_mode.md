# Modo Autônomo de Resolução Solitária (Self-Healing & Auto-Fix)

## Diretrizes de Autonomia Máxima para o Agente Antigravity

1. **Resolução Autônoma Sem Interrupções**:
   - Sempre que o usuário relatar um erro, comportamento inesperado ou pedir melhorias, o agente deve diagnosticar a causa raiz, inspecionar o código, aplicar as correções necessárias, testar a compilação e rodar a suíte de testes de regressão **de forma 100% autônoma**, sem parar para pedir autorizações intermediárias ou confirmações desnecessárias.

2. **Fluxo Autônomo de Execução**:
   - **Passo 1 (Diagnóstico)**: Ler os arquivos envolvidos e verificar os logs com ferramentas internas.
   - **Passo 2 (Correção)**: Aplicar a melhoria cirúrgica no código.
   - **Passo 3 (Validação Automática)**: Compilar com `py_compile` e rodar a suíte de testes (`Testar-GSPHosp.py`).
   - **Passo 4 (Entrega)**: Apresentar o resultado concluído e testado diretamente ao usuário.

3. **Recomendação de Comandos Nativos**:
   - Para tarefas de longa duração ou varreduras completas do sistema, recomendar o uso do comando `/goal` no chat.
