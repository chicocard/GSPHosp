# GSPHosp — pacote "layout + atratividade por área"

Conteúdo deste pacote (copiar por cima da raiz do projeto `D:\papapapa\GSPHosp-Novo\`;
nenhum arquivo existente é sobrescrito — são todos novos):

```
papahr/dashboard/huff_area.py              modelo de Huff por área da assistência (Polars)
papahr/ui/theme.qss                        folha de estilo global compacta
papahr/ui/mapa/__init__.py                 novo subpacote da tela do mapa
papahr/ui/mapa/widget.py                   MapaAtratividadeWidget (PySide6 + QWebEngineView)
papahr/ui/mapa/malha.py                    centroides a partir da malha embarcada
papahr/ui/mapa/assets/mapa_atratividade.html   página Leaflet (coroplético + hospitais + legenda)
papahr/ui/mapa/assets/malha_municipios.js      malha municipal IBGE simplificada (5,2 MB)
papahr/ui/mapa/assets/leaflet/                 Leaflet 1.9.4 (offline)
scripts/build_malha_ibge.py                regenera a malha com a versão vigente do IBGE
tests/test_huff_area.py                    7 testes (pytest)
```

## Como funciona

1. `classificar_areas(aih)` acrescenta `area_cod`/`area_nome` (subgrupo SIGTAP = 4 primeiros
   dígitos de `PROC_REA`), `cid_cap` (capítulo CID-10 de `DIAG_PRINC`) e `espec_nome`.
2. `huff_por_area(aih_cls, hospitais, municipios, beta)` devolve
   `od` (hospital × área × município: p_huff, demanda, n_esp, n_obs, share_obs, dist_km…) e
   `resumo` (hospital × área: capt_obs, capt_esp, razao_obs_esp, n_mun_80, dist_media_obs_km,
   pct_fora_mun, pct_fora_regiao, share_massa_area).
3. `MapaAtratividadeWidget.carregar(od, resumo, hospitais)` mostra tudo: combo de área,
   métrica, hospital, β com botão Recalcular, ranking ordenável, exportação CSV, e o mapa
   com fundo Esri cinza-claro ou OpenStreetMap (internet, sem chave de API; troca no controle
   de camadas no canto superior direito) sobre a malha IBGE (offline).

Para focar um hospital: clique no marcador (se houver vários no mesmo ponto, abre uma lista
para escolher), ou clique na linha do ranking, ou escolha no combo "Hospital". O mapa centra
nele; "limpar" no título volta à visão de todos.

Colunas exigidas:

| tabela      | colunas                                                         |
|-------------|-----------------------------------------------------------------|
| `aih`       | CNES (7), MUNIC_RES (6), PROC_REA (10); opcionais DIAG_PRINC, ESPEC, VAL_TOT |
| `hospitais` | CNES, NOME, LAT, LON, COD_MUN (6)                               |
| `municipios`| COD_MUN (6), NOME, LAT, LON; opcionais REGIAO (macro/DRS), POP  |

Se a tabela de municípios do app tiver LAT/LON nulos (o CNES cobre 5.571 dos 5.607 códigos),
`papahr.ui.mapa.malha.completar_centroides(municipios)` preenche com o centroide da malha.

## Demonstração isolada (antes de integrar)

```powershell
cd D:\papapapa\GSPHosp-Novo
.\.venv\Scripts\python.exe -m papahr.ui.mapa.widget
```

Abre uma janela com 4 hospitais fictícios em MG e AIH sintéticas. Serve para ver o layout,
o mapa de fundo, o coroplético e a interação (clicar num hospital, mudar métrica, mudar β).

## Integração no app (prompt para o TRAE/Antigravity)

> No GSPHosp, integrar a nova tela de atratividade por área sem alterar o pipeline:
>
> 1. Em `papahr/ui/pages/dashboard_page.py`, substituir a aba do "mapa concêntrico"
>    (hoje em pyqtgraph) por uma instância de `papahr.ui.mapa.MapaAtratividadeWidget`.
>    O import de `papahr.ui.mapa` deve acontecer no módulo de entrada do app
>    (onde o `QApplication` é criado), antes da linha `QApplication(...)` —
>    exigência do QtWebEngine.
> 2. Onde o dashboard hoje chama o Huff antigo (`papahr/dashboard/huff_model.py`,
>    chamado a partir de `metrics.py`), chamar também
>    `classificar_areas` + `huff_por_area` de `papahr/dashboard/huff_area.py`, com:
>    `aih` = o DataFrame analítico (silver.producao ou o Parquet completo) com as colunas
>    CNES, MUNIC_RES, PROC_REA, DIAG_PRINC, ESPEC, VAL_TOT;
>    `hospitais` = os hospitais do CNES com produção no período (CNES, NOME, LAT, LON, COD_MUN);
>    `municipios` = a tabela de centroides já existente (COD_MUN, NOME, LAT, LON) com a coluna
>    REGIAO preenchida pelo mesmo recorte Macro/DRS usado no filtro do dashboard.
>    Materializar `od` em `gold.huff_area` e `resumo` em `gold.atratividade_hospital_area` no DuckDB.
> 3. Conectar `widget.betaAlterado` a uma função que reexecuta `huff_por_area` com o novo β e
>    chama `widget.carregar(od, resumo, hospitais)` de novo.
> 4. Se o app tiver um filtro de UF/região, chamar `widget.view.page().runJavaScript("window.gsp.enquadrarUF('31')")`
>    após carregar, com a UF selecionada.
> 5. Não alterar `huff_area.py`, `widget.py` nem o HTML; se faltar coluna, adaptar na
>    chamada (rename) e registrar no log qual coluna faltou.
> 6. Rodar `pytest tests/test_huff_area.py` e `python -m papahr.ui.mapa.widget` ao final.

## Folha de estilo (tamanhos)

No módulo onde o `QApplication` é criado:

```python
from pathlib import Path
from PySide6.QtGui import QFont
app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setFont(QFont("Segoe UI", 10))
app.setStyleSheet((Path(__file__).resolve().parent / "ui" / "theme.qss").read_text(encoding="utf-8"))
```

(ajuste o caminho relativo conforme o arquivo onde isso ficar; o alvo é `papahr/ui/theme.qss`).

Depois, remover dos widgets todo tamanho de fonte e altura fixa. Para listar o que precisa sair:

```powershell
Get-ChildItem -Recurse -Filter *.py -Path .\papahr\ui | Select-String -Pattern "font-size|setPointSize|setPixelSize|setFixedHeight|setMinimumHeight|setFixedSize|QFont\(" | Select-Object Path, LineNumber, Line
```

Prompt para o TRAE: "Remova de `papahr/ui` todas as chamadas `setStyleSheet` que definem
font-size, todos os `setPointSize/setPixelSize/QFont(...)` com tamanho explícito e todos os
`setFixedHeight/setMinimumHeight/setFixedSize` em botões, combos e campos. A folha
`papahr/ui/theme.qss` passa a governar tamanhos. Mantenha larguras mínimas de colunas de
tabela e tamanhos de ícones. Para títulos de seção use `label.setProperty('titulo', True)`;
para notas, `setProperty('nota', True)`; para o botão principal de cada tela,
`setProperty('primario', True)`."

Se a tela ainda parecer grande demais, verificar no `Abrir-GSPHosp.bat` e no código se há
`QT_SCALE_FACTOR` ou `QT_FONT_DPI` definidos — devem ser removidos; o Qt 6 já respeita a
escala do Windows.

## Malha municipal

O `malha_municipios.js` que acompanha o pacote vem de uma cópia pública da malha IBGE de 2010
(5.564 municípios), simplificada com mapshaper preservando topologia. Faltam seis municípios
criados depois (Nazária/PI, Mojuí dos Campos/PA, Pescaria Brava/SC, Balneário Rincão/SC,
Paraíso das Águas/MS, Pinto Bandeira/RS): aparecem em branco no mapa. Para a malha vigente:

```powershell
.\.venv\Scripts\python.exe scripts\build_malha_ibge.py
```

O script baixa da API do IBGE (precisa de internet), guarda o arquivo anterior como `.js.bak`
e só substitui se vierem ≥ 5.500 municípios. Não foi possível testá-lo no ambiente onde este
pacote foi montado (sem acesso ao IBGE); se a API recusar, o arquivo atual continua valendo.

## Leitura dos indicadores (resumo hospital × área)

- `capt_esp` vs `capt_obs`: quanto o hospital deveria captar só pela massa e pela distância,
  contra o que captou. `razao_obs_esp` > 1 = atrai acima do que a geografia explica
  (reputação, referência pactuada, vazio assistencial ao redor); < 1 = perde pacientes para
  concorrentes mais distantes.
- `n_mun_80`: concentração da área de abrangência observada.
- `pct_fora_mun` / `pct_fora_regiao`: dependência de outros municípios/regiões (quando REGIAO
  vem do recorte Macro/DRS, é o índice de dependência regional).
- Limite do dado: a demanda é só o que aparece no SIH da(s) UF(s) carregada(s); residentes
  internados em outra UF não entram, a não ser que os arquivos dessa UF também sejam processados.
