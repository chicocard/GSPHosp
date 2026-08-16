# Especificação de Referência: Fórmulas e Algoritmos de Saúde (`refs/MANUAL_BENCHMARKS.md`)

Este arquivo serve como referência de solo verdadeiro para o Agente Crítico Cego (Subagente B) validar as saídas do Motor de Métricas (`papahr/dashboard/metrics.py`).

---

## 1. Modelo Gravitacional de Huff
Calcula a probabilidade $P_{ij}$ de um paciente do município $i$ buscar atendimento no hospital $j$:

$$P_{ij} = \frac{S_j \cdot d_{ij}^{-\beta}}{\sum_{k} S_k \cdot d_{ik}^{-\beta}}$$

Onde:
- $S_j$: Capacidade/Atratividade do hospital $j$ (leitos ou volume de produção).
- $d_{ij}$: Distância geográfica (Haversine em km) entre o município $i$ e o hospital $j$.
- $\beta$: Coeficiente de atrito/decaimento temporal:
  - $\beta = 2.2$: Atenção Ambulatorial / Básica (alta sensibilidade à distância).
  - $\beta = 1.5$: Internação de Média Complexidade.
  - $\beta = 0.9$: Alta Complexidade / Oncologia / Cirurgia Especializada.

---

## 2. ICSAP (Internações por Condições Sensíveis à Atenção Primária)
16 Grupos oficiais definidos pela **Portaria MS nº 2.206/2017**:
1. Doenças préveis por imunoprevenção
2. Gastroenterites infecciosas e complicações
3. Anemia
4. Deficiências nutricionais
5. Infecções de ouvido, nariz e garganta
6. Pneumonias bacterianas
7. Asma
8. Doenças pulmonares
9. Hipertensão arterial
10. Angina pectora
11. Insuficiência cardíaca
12. Doenças cerebrovasculares
13. Diabetes mellitus
14. Epilepsia
15. Infecção do trato urinário
16. Infecção da pele e tecido subcutâneo

$$ \% \text{ICSAP} = \frac{\text{Atendimentos com CID10 sensível à APS}}{\text{Total de Atendimentos}} \times 100 $$

---

## 3. Índice LACE (Risco de Reinternação / Mortalidade)
Calculado com a combinação:
- **L** (Length of Stay): Dias de internação (0 a 6 pontos).
- **A** (Acuity): Admissão de emergência (3 pontos se emergência).
- **C** (Comorbidities): Índice de Comorbidade de Charlson (0 a 5 pontos).
- **E** (Emergency visits): Visitas ao PS nos últimos 6 meses (0 a 4 pontos).

Categorias de Risco:
- **Baixo**: LACE < 5
- **Médio**: 5 <= LACE <= 9
- **Alto**: LACE >= 10

---

## 4. Eficiência Clínica (O/E Ratio)
- **LOS O/E** (Length of Stay Observado / Esperado): Média de permanência ajustada por diagnóstico CID10.
- **Custo O/E**: Custo médio observado / custo esperado para a mesma Severidade/DRG.

Se $\text{Ratio} < 1.0$: Eficiência superior à média esperada.  
Se $\text{Ratio} > 1.0$: Oportunidade de otimização de tempo ou custo.
