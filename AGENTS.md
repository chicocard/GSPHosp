# Regras do Agente (AGENTS.md) — GSPHosp

## Execução Autônoma e Resolução Direta de Problemas

- **Autonomia Total**: Diagnosticar, modificar, testar e verificar automaticamente qualquer problema relatado no sistema sem solicitar autorizações intermediárias.
- **Validação de Testes Obrigatória**: Toda alteração de código deve ser verificada via `.venv\Scripts\python.exe Testar-GSPHosp.py`.
- **Garantia de Legibilidade (AAA)**: 0 botões com texto escuro sobre fundos coloridos. Sempre manter o padrão branco `#FFFFFF` e amarelo ouro `#FEF08A` no hover.
- **Resiliência Polars**: Todas as chaves CNES devem ser convertidas defensivamente com `cast(pl.Utf8, strict=False)`.
