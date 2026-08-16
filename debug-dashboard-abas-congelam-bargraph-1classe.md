# DEBUG Dashboard GSPHosp-Novo: Abas congelam + BarGraph 1 classe
- **Session ID:** dashboard-abas-congelam-bargraph-1classe
- **Data:** 2026-08-15 BRT
- **Status:** [OPEN]
- **Sintomas Usuário (Reprodução Passo-a-Passo):**
  1. Abrir GSPHosp-Novo (main.py) → logar admin.
  2. Ir para Wizard → rodar pipeline SIH-RD UF qualquer com dados → Dashboard abre.
  3. **Sintoma 1:** Aba "Risco e Demanda" → BarGraphItem PyQtGraph mostra APENAS 1 bar (classe "BAIXO" = 100% dos dados porque LACE/Charlson retornam 0.0 por falta de colunas). As demais classes (MÉDIO/ALTO/CRÍTICO/MORTALIDADE) têm valor 0 e "desaparecem" do gráfico.
  4. **Sintoma 2:** Clicar em QUALQUER outra aba do QTabWidget ("Eficiência Clínica", "Território", etc.) → NÃO ACONTECE NADA. Aba atual permanece "Risco e Demanda". App NÃO crasha, não há erro visível, mas abas não trocam.
- **Causa Raiz SUSPEITA (usuário):** Exception Python (Polars ColumnNotFound / PyQtGraph TypeError `brushes=` list None) dentro de `_fill_tab_*` engolida por PySide6 → slot `currentChanged` é interrompido no meio → troca de aba não finaliza.

---

## 1. Hipóteses Falseáveis (H1-H5)
**Evidence Gate: NÃO alterar lógica ANTES de provar uma das hipóteses abaixo por runtime evidence (stack trace).**

- **H1:** `_fill_tab_risco()` usa `BarGraphItem(x=..., height=..., brushes=[QBrush(...), None, None, ...])` → lista `brushes` tem comprimento diferente de `height` ou contém `None` em posições válidas. PyQtGraph lança `TypeError: 'NoneType' object is not subscriptable` ou similar.
- **H2:** `_fill_tab_risco()` agrupa os estratos mas colunas de LACE (LOS_DIAS, ACUIDADE, COMORBIDADES, IDADE) não existem no df de entrada → Polars `pl.col("los_dias")` lança **ColumnNotFoundError** antes de plotar.
- **H3:** `_fill_tab_efic()` ou `_fill_tab_terr()` chamam `metrics.py` que retorna Polars LazyFrame não-coletado (`.collect()` faltando) → `df.to_numpy()` em PyQtGraph lança **AttributeError: 'LazyFrame' object has no attribute 'to_numpy'**.
- **H4:** `QTabWidget.currentChanged[int].connect(self._on_tab_changed)` em `__init__` do Dashboard não está conectado ou foi conectado a `_fill_tab_risco` que sempre retorna cedo → troca de aba nunca dispara `_fill_tab_*` correspondente.
- **H5:** Barras height=0 estão sendo removidas visualmente por `setXRange` ou `enableAutoRange` muito agressivo (apenas range da barra 1 barra). As barras existem mas o zoom corta as de altura 0 → usuário "acha" que só tem 1.

---

## 2. Instrumentação (Primeira alteração de código = só try/except + traceback print)
_Instrumentação mínima. NÃO corrige bugs ainda._
Alvo: todos os métodos `_fill_tab_*` no `dashboard_page.py`.
Bloco:
```python
import traceback
def _fill_tab_risco(self, df=None):
    try:
        ... (código original)
    except Exception as e:
        print(f"\n[!!!] EXCECAO _fill_tab_risco: {type(e).__name__}: {str(e)}")
        traceback.print_exc()
```

---

## 3. Evidências Runtime (stack trace + logs)
_A ser preenchido após rodar app com instrumentação._

---

## 4. Diagnóstico
_Aguardando evidências._
