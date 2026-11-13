# GSPHosp — atratividade por faixas de distância e taxas municipais

Dois recursos novos para o Dashboard, em substituição ao "mapa concêntrico" e à "curva de
decaimento" atuais:

1. **Atratividade por faixas de distância** — para o hospital em foco, a taxa per capita de
   internações de cada faixa de território (coroas entre círculos concêntricos), por tipo de
   internação (grupo, subgrupo, forma de organização ou procedimento SIGTAP; capítulo CID-10;
   diagnóstico; especialidade do leito; caráter; complexidade; financiamento; faixa etária;
   sexo) e para o hospital como um todo. Mapa Leaflet com os círculos e os municípios coloridos,
   curva taxa × distância com os modelos ajustados, tabela por faixa e tabela de índices por tipo.
2. **Taxas municipais de internação (TIH)** — internações de residentes ÷ população, por
   município da UF, com histograma (média, mediana, taxa da UF, município do hospital destacado),
   cartões de estatísticas, tabela ordenável e mapa coroplético.

Este pacote NÃO altera arquivos existentes do app (só acrescenta; `papahr/ui/mapa/malha.py` vem
corrigido — ver "Correção"). A integração no `dashboard_page.py` é feita pelo prompt do fim.

## Arquivos

```
papahr/dashboard/atratividade.py        cálculo: classificação por nível, distâncias, faixas, taxas, ajuste, índices,
                                        TIH municipal, histograma, payloads do mapa, adaptador insumos_do_app()
papahr/dashboard/populacao.py           população IBGE (SIDRA) com cache em GSPHOSP_HOME/cache e reserva no CNES
papahr/ui/mapa/assets/mapa_faixas.html  página Leaflet: gsp.setFaixas / gsp.setCoropleto / gsp.destacar
papahr/ui/mapa/visor.py                 QWebEngineView + ponte (clique no município → Python), fila de chamadas
papahr/ui/mapa/malha.py                 (correção) ignora geometrias vazias da malha simplificada
papahr/ui/atratividade/widget.py        AtratividadeFaixasWidget
papahr/ui/atratividade/grafico.py       GraficoDecaimento e GraficoHistograma (pyqtgraph, paleta validada)
papahr/ui/atratividade/tabela.py        TabelaPolars (QTableView sobre Polars, formatos pt-BR, CSV)
papahr/ui/atratividade/estilo.py        aplicar_compacto() (folha compacto.qss), RotuloElidido, botao_pequeno, rotulo_chave
papahr/ui/atratividade/compacto.qss     folha de estilo compacta das telas (9 pt, controles de 22 px, cores de texto explícitas)
papahr/ui/atratividade/check.svg        marca dos checkboxes (referenciada pela folha)
papahr/ui/atratividade/demo_dados.py    dados sintéticos para a demonstração
papahr/ui/atratividade/__main__.py      python -m papahr.ui.atratividade [--dados PASTA|ARQUIVO | --demo]
papahr/ui/atratividade/janela.py        JanelaAtratividade: abas Dados · Atratividade · Market share · TIH (sempre visíveis);
                                        CargaWorker e DownloadWorker (QThread); recarga automática da última seleção
papahr/ui/atratividade/dados.py         PaginaDados: pasta, chips de UF, período, fontes, população, inventário, download, registro
papahr/dashboard/importar_rd.py         Selecao (UF × período × fontes), inventário da pasta, download do FTP do DATASUS,
                                        leitura rápida (numpy, paralela, partes + consolidado em cache), CIHA, nomes SIGTAP
papahr/ui/tih/widget.py                 TaxasMunicipaisWidget
papahr/ui/atratividade/seletor.py       SeletorHospital: UF → região de saúde → município → hospital (+ busca livre)
papahr/ui/atratividade/market_share.py  MarketShareWidget + DonutWidget (participação por faixa de distância)
papahr/dashboard/regioes_saude.py       município → região de saúde (CIR) e macrorregião
papahr/data/regioes_saude.parquet       tabela embarcada: 5.570 municípios, 450 regiões de saúde (CO_REGSAUD do DATASUS)
scripts/build_regioes_saude.py          regenera a tabela (DATASUS base territorial via FTP; reserva covid19br/UFV)
scripts/corrigir_sigtap_app.py          corrige papahr/datasources/sigtap.py do app (nomes de grupo/subgrupo/forma — ver abaixo)
tests/test_atratividade.py, test_populacao.py, test_regioes_saude.py, test_importar_rd.py   36 testes
```

**Atenção ao `.gitignore`**: ele ignora `*.parquet` e abre exceção só para `counties.parquet`.
Acrescente a linha `!papahr/data/regioes_saude.parquet`, senão a tabela de regiões não sobe para o
GitHub e o build do instalador fica sem ela.

Instalação: extraia o zip na raiz do projeto (`D:\papapapa\GSPHosp-Novo`), sobrescrevendo
`papahr/ui/mapa/malha.py`. Depois:

```powershell
cd D:\papapapa\GSPHosp-Novo
.\.venv\Scripts\python.exe -m pytest tests\test_atratividade.py tests\test_populacao.py tests\test_regioes_saude.py tests\test_importar_rd.py -q
.\.venv\Scripts\python.exe -m papahr.ui.atratividade                                    # abre na aba Dados (última seleção)
.\.venv\Scripts\python.exe -m papahr.ui.atratividade --dados D:\AIH_RD_2025 --uf MG --periodo 2025-01:2025-12
.\.venv\Scripts\python.exe -m papahr.ui.atratividade --dados D:\AIH_RD_2025 --uf MG --periodo 2024-01:2024-12 --fontes SIH,CIHA --baixar
.\.venv\Scripts\python.exe -m papahr.ui.atratividade --dados output\outputSIH_RD.parquet   # parquet/csv do pipeline
.\.venv\Scripts\python.exe -m papahr.ui.atratividade --demo                             # hospitais fictícios
```

## Fluxo: aba "Dados" → análise

Quatro abas, **sempre visíveis**: **Dados · Atratividade por faixas · Market share · Taxas municipais
(TIH)**. O app abre na aba Dados; as três de análise mostram o aviso "sem dados carregados" até os
dados chegarem e então ficam preenchidas. Ao abrir, a última seleção usada é recarregada sozinha
(rápido, vem do cache) sem sair da aba Dados — a opção "carregar a última seleção ao abrir"
desliga isso (`--nao-recarregar` na linha de comando). O **market share** tem aba própria, com a
largura toda para o donut e as duas tabelas; o hospital em foco, o tipo e as faixas são os da aba
de atratividade (botão "escolher hospital/tipo…" leva até lá; clicar num hospital do market share
passa o foco a ele nas três abas). Se a montagem das telas falhar, o erro vai para o registro da
aba Dados e para uma caixa de mensagem — as abas nunca "somem". Na aba Dados escolhe-se, nesta ordem:

1. **Pasta de dados** — onde estão (ou vão ficar) os arquivos `RDUFAAMM.dbc` (SIH) e
   `CIHAUFAAMM.dbc` (CIHA) do DATASUS. Só os nomes são lidos: o inventário (tabela à esquerda, por
   UF e fonte: quantos arquivos, de quando a quando, MB, quantos a seleção usa, quantos faltam)
   é instantâneo. As UFs com arquivos na pasta aparecem em destaque nos chips.
2. **UF dos hospitais** — chips das 27 UFs; mais de uma pode ser marcada (os hospitais de todas
   entram no seletor em cascata). "incluir os arquivos das demais UFs" acrescenta, só como
   internações, os arquivos das outras UFs que estiverem na pasta (residentes internados em outro
   estado; relevante em hospitais de fronteira).
3. **Período** — de mês/ano a mês/ano (competências, inclusive). "ano fechado" = o ano passado
   inteiro; "tudo na pasta" = todo o período disponível para as UFs marcadas.
4. **Fontes** — SIH-RD e/ou CIHA; **população** IBGE (SIDRA, com cache) ou CNES.

A linha de resumo diz o que a seleção vale ("MG · 2025-01 a 2025-12 · SIH+CIHA · 24 arquivo(s) na
pasta · FALTAM 2: …"). **Baixar do DATASUS os que faltam** busca no FTP
(`ftp.datasus.gov.br/dissemin/publicos/SIHSUS/200801_/Dados/` e `…/CIHA/201101_/Dados/`) os
arquivos da seleção que não estão na pasta — grava em `.part` e renomeia no fim, nunca deixa
arquivo pela metade; competência ainda não publicada vira erro listado, não trava. **Carregar e
analisar** lê só os arquivos da seleção e abre as telas. Voltar à aba Dados, mudar período/UF/fonte
e carregar de novo substitui os dados — cada arquivo lido fica como parte em cache
(`GSPHOSP_HOME\cache\rd_pastas\partes\`, por nome+tamanho+data), então outra seleção reaproveita
o que já foi lido e só lê o que falta; o consolidado de cada seleção também fica em cache. A última
seleção é lembrada entre sessões (QSettings). `--dados PASTA --uf MG --periodo AAAA-MM:AAAA-MM
[--fontes SIH,CIHA] [--baixar]` preenche a aba e já carrega; um `--dados ARQUIVO.parquet` abre o
parquet/csv do pipeline sem seleção.

Leitura dos arquivos: o `.dbc` é descomprimido pelo `datasus_dbc` nativo se estiver instalado
(`pip install datasus-dbc`, segundos por arquivo) ou pelo descompressor em Python puro do app
(`papahr.core.dbc_reader`, ~30–60 s por arquivo mensal de MG); o DBF é fatiado com numpy só nas
colunas úteis (1 s por arquivo). Até 3 arquivos em paralelo, em processos separados
(`GSPHOSP_PROCESSOS=1` desliga; no exe do PyInstaller é sequencial). Carga e download têm
"Cancelar"; uma carga interrompida continua de onde parou. `--sem-cache` força a releitura.

Nomes de grupo, subgrupo, forma de organização, procedimento, financiamento e complexidade vêm do
SIGTAP que o app já tem em cache (`~/.gsphosp/cache/SIGTAP/<AAAAMM>/`, versão mais recente, sem
FTP), montados por `importar_rd.tabela_sigtap()` com as chaves certas (grupo = dígitos 1–2 do
procedimento, subgrupo = 3–4, forma = 5–6). Sem esse cache, grupo e subgrupo usam os dicionários
embutidos e forma/procedimento ficam só com o código — a janela avisa. Basta rodar uma vez o
pipeline do app (que baixa o SIGTAP) para os nomes aparecerem; não é preciso rodar o pipeline
sobre as AIH.

## CIHA: internações não SUS lado a lado com o SIH

Decisão: as duas fontes ficam **lado a lado**, nunca somadas às cegas. Cada linha dos dados leva
a coluna `FONTE` — `SIH` (AIH do SUS, arquivos RD) ou `CIHA` (Comunicação de Informação Hospitalar
e Ambulatorial: internações pagas por planos, particulares etc., arquivos CIHA). Na pasta aberta,
os `CIHAUFAAMM.dbc/.dbf` são lidos com o mesmo leitor rápido e as mesmas partes/cache dos RD.

Nas duas telas aparece a caixa **Fonte** (ambas · SIH (SUS) · CIHA (não SUS)) — só quando o dataset
tem as duas fontes. Ela recorta tudo: taxas por faixa, ajuste, mapa, gráfico, market share e TIH.
Com "ambas", a tabela de market share ganha a coluna **% SUS** (parte das internações de cada
hospital na faixa que veio do SIH): um hospital com 0 % é só privado; 100 %, só SUS. Surgem também
três níveis de agregação: **Fonte** (SIH × CIHA), **Pagador (CIHA)** e **Modalidade (CIHA)**, com
os códigos do arquivo. O resumo da barra de status passa a dizer "N AIH (n SIH + n CIHA)".

Layout da CIHA. Não consegui alcançar daqui o dicionário oficial (FTP e site do DATASUS bloqueados
neste ambiente), então o leitor é tolerante: reconhece os nomes canônicos (`CNES`, `MUNIC_RES`,
`MUNIC_MOV`, `PROC_REA`, `DIAG_PRINC`, `SEXO`, `IDADE`, `ANO_CMPT`/`MES_CMPT`, `CAR_INT`, `ESPEC`,
`MODALIDADE`, `DT_INTER`, `NASC`) e sinônimos prováveis (`CNES_EXEC`, `MUNRES`, `PROC_PRINC`,
`CID_PRINC`, `MODAL`, `TP_FINANC` …, tabela `_CIHA_SINONIMOS` em `importar_rd.py`); o campo de
fonte pagadora do arquivo (`FONTE`/`TP_FINANC`/…) vira `PAGADOR`; sem `IDADE`, calcula de `NASC` e
`DT_INTER`; sem competência, usa a do nome do arquivo. Na carga, o painel imprime a distribuição de
`MODALIDADE`, `PAGADOR` e `CAR_INT` da CIHA e avisa se alguma coluna essencial não foi reconhecida.
**Primeira coisa a fazer com um arquivo CIHA real**:

```powershell
.\.venv\Scripts\python.exe -m papahr.ui.atratividade --inspecionar D:\AIH_RD_2025\CIHAMG2501.dbc
```

Isso lista todas as colunas (nome, tipo, tamanho) e, para as de poucos valores, a distribuição.
Com essa saída ajusta-se `_CIHA_SINONIMOS` e decide-se o **filtro de modalidade**: a CIHA também
registra atendimentos ambulatoriais de alta complexidade (quimioterapia, hemodiálise…), que não
são internações; hoje nada é filtrado — use o nível "Modalidade (CIHA)" para ver o que há e, com
os códigos confirmados, o filtro entra na leitura. Como `FONTE` passou a existir nos dados
consolidados, o cache foi versionado (`rd-v2`): a primeira abertura de uma pasta já lida relê os
arquivos uma vez.

## Defeito encontrado no app: todos os procedimentos com o mesmo grupo/subgrupo/forma

`SIGTAPRepository.get_procedure_details()` (`papahr/datasources/sigtap.py`) junta tb_procedimento
com tb_grupo/tb_sub_grupo/tb_forma_organizacao pelas colunas CO_GRUPO/CO_SUB_GRUPO/
CO_FORMA_ORGANIZACAO — que não existem em tb_procedimento (o layout oficial só tem
CO_PROCEDIMENTO, NO_PROCEDIMENTO, TP_COMPLEXIDADE…). Os joins caem só em `file_version_id`,
viram produto cartesiano, e o `unique(keep="last")` final dá a TODOS os procedimentos o mesmo
(último) grupo, subgrupo e forma. Foi isso que apareceu na tela: todos os subgrupos "Atenção em
Nefrologia". **Os parquet/csv já gerados pelo pipeline têm NO_GRUPO, NO_SUB_GRUPO e
NO_FORMA_ORGANIZACAO errados**, e qualquer análise por grupo/subgrupo do Dashboard que use essas
colunas está errada. Três providências, nesta ordem:

1. As telas deste pacote já não dependem da função defeituosa: montam os nomes por conta própria
   (`tabela_sigtap()`) e, ao ler um parquet do pipeline, detectam colunas de nome corrompidas (um
   único nome para vários códigos), descartam-nas e refazem os nomes (`sanear_nomes_sigtap()`, com
   aviso na janela).
2. Corrigir o app: `python scripts\corrigir_sigtap_app.py` (na raiz do projeto) insere, antes dos
   joins, a derivação dos três códigos a partir de CO_PROCEDIMENTO; guarda cópia `.bak-…` e só
   grava se o arquivo compilar. `--ver` apenas diz se a correção é necessária. O teste
   `test_sigtap_do_app_da_um_nome_por_procedimento` falha antes e passa depois.
3. Regenerar os parquet/csv do pipeline depois da correção (os nomes são gravados na extração).

Carga e download rodam em threads (`CargaWorker`, `DownloadWorker`); o progresso (barra "n de N",
registro das etapas) fica na aba Dados, na barra de status e no terminal. Todos os hospitais das
UFs marcadas saem da base do CNES (a mesma que o app usa), com o nº de AIH dos dados. `--listar`
mostra os arquivos de dados encontrados nas pastas usuais (`output\`, `Documentos\GSPHosp\output`,
`%LOCALAPPDATA%\GSPHosp`, `Downloads`, pastas de `D:\`/`C:\` com AIH/RD/SIH/CIHA no nome);
`--cnes 1234567` abre com esse hospital em foco.

Dos dados são lidas só as colunas que as telas usam (`COLUNAS_UTEIS` em `importar_rd.py`): um ano
de MG inteiro (~1,3 milhão de AIH × 113 colunas do RD) ocuparia vários GB lido por inteiro; com a
projeção, centenas de MB. As linhas impressas na carga dizem o que os dados contêm:
nº de competências (meses), nº de municípios dos hospitais (`MUNIC_MOV`), nº de hospitais da UF
na base do CNES e **nº de CNES com AIH no arquivo** — este último é o que limita a análise. Para
MG inteira ele fica na casa das centenas (≈ 500–600 hospitais com produção SUS); se vier em
dezenas, o arquivo saiu de uma rodada do Wizard filtrada por município/região ou de poucos meses:
rode o pipeline de novo para a UF inteira, sem chips de município nem de região no Passo 3, e com
pelo menos 12 meses (um ano fechado) para que a anualização das taxas não dependa de sazonalidade.

## Layout (versão compacta)

Regra: o máximo de área para tabelas, gráficos e mapa. Cada tela tem só **duas linhas de 22 px de
configuração** — linha 1 = hospital em cascata (UF → região → município → hospital + busca; sem
rótulos, a dica de cada caixa diz o campo), linha 2 = agregação, faixas e ações — e um botão
"▴ configuração" no canto das abas que as recolhe, deixando tudo para os resultados. Não há títulos
nem cabeçalhos dentro das telas: a aba já diz o que é; o resumo do dataset vai para a barra de
status, a fórmula do tipo selecionado e o título das faixas são uma linha cortada com "…" (texto
inteiro na dica). Na TIH, os seis cartões viraram uma linha de estatísticas. No market share, a
tabela por faixa divide a altura com o donut por um divisor arrastável. A escolha dos dados
(pasta, UF, período, fontes, download) é a aba "Dados", a primeira; não há toolbar.

Tudo que é tamanho vem de `compacto.qss` (fonte 9 pt; notas 8 pt; cabeçalhos de tabela 8 pt;
linhas de tabela de 18 px; abas, botões e campos de 22 px), aplicada por `aplicar_compacto()` no
widget raiz de cada tela — por isso vale igual dentro do app, cuja folha global é outra. **Toda cor
de texto é explícita na folha** (campos, tabelas, cabeçalhos, abas, menus, dicas, caixas de
mensagem): sem isso, com o Windows em modo escuro a paleta do sistema punha texto branco em caixa
branca. Pelo mesmo motivo os gráficos e o donut deixaram de escolher as cores pela paleta do
sistema (`tema_de()` devolve "claro" salvo propriedade `tema="escuro"` no widget). A legenda do
mapa recolhe ao clicar no título. Feito para 1280 × 720 e conferido a 1600 × 900.

## Seleção do hospital

Nas duas telas o hospital é escolhido em cascata: **UF → região de saúde → município → hospital**,
com os contadores de hospitais em cada nível, as opções "(todas as regiões)" e "(todos os
municípios)" — que colocam todos os hospitais do estado na caixa Hospital, do maior volume para o
menor — e uma caixa de busca livre por nome ou CNES (em qualquer UF) que reposiciona a cascata.
Aparecem todos os hospitais do dataset que têm coordenadas no CNES; com o SIH-RD completo de uma UF,
isso é o conjunto dos hospitais do estado com internações no período. A região de saúde é a CIR do DATASUS
(`papahr/data/regioes_saude.parquet`; 89 em MG, 450 no país); o contorno da região do hospital
é desenhado nos dois mapas. Na aba de taxas, a "UF comparada" acompanha a UF do hospital, mas pode
ser trocada.

## Quem é hospital (critérios, conforme IT_CNES 2017-06)

`hospitais_da_uf()` aceita a base CSV do app (tbEstabelecimento) e o layout de disseminação
ST*.dbf descrito no Informe Técnico CNES 2017-06. É hospital quem satisfaz **qualquer** critério:

* tipo de unidade (`TP_UNID` / `TP_UNIDADE` / `CO_TIPO_UNIDADE`) 05 hospital geral, 07 hospital
  especializado, 15 unidade mista, 20/21 pronto-socorro, 61 centro de parto normal, 62 hospital/dia;
* flag de internação = 1 no layout ST: `ATENDHOS` (atendimento hospitalar), `LEITHOSP` (leitos
  hospitalares), `NIVATE_H` (nível de atenção hospitalar) ou `AP01CV01…06` (internação por convênio
  SUS, particular, planos); ou `QTLEITP1+2+3` > 0 (leitos cirúrgicos, clínicos, complementares);
* leitos existentes > 0 numa tabela de leitos por estabelecimento (LT*.dbf ou rlEstabComplementar),
  quando fornecida;
* nome com termo hospitalar (hospital, maternidade, santa casa, pronto-socorro, unidade mista,
  casa de saúde);
* CNES presente no dataset de AIH — quem faturou internação é hospital para esta análise.

Cada hospital sai com `TIPO` (nome do tipo de unidade), `COORD` ("CNES" ou "centroide do
município") e `n` (AIH no dataset). O layout ST não tem nome nem coordenadas: o nome vem do
dataset de AIH (`NO_ESTABELECIMENTO`) ou fica "CNES 1234567", e a coordenada é o centroide.
No seletor, "≈" marca coordenada estimada e "sem AIH" marca hospital sem internações no dataset.

## Market share por faixa (aba "Market share" da tela de atratividade)

Para a faixa escolhida (coroa em torno do hospital em foco, ou acumulada até o limite da faixa),
conta as internações do tipo selecionado dos residentes dessa faixa em **qualquer** hospital e
mostra a fatia de cada um: donut com as N maiores fatias (3 a 8; o hospital em foco sempre na
primeira cor, com contorno) + "Outros", percentual escrito nas fatias, nome/CNES/percentual ao
passar o mouse, tabela completa (cor, hospital, CNES, município, distância, internações, %, %
acumulado) e, embaixo, a participação do hospital em foco em cada faixa, com o nº de
concorrentes, o líder da faixa e o HHI (índice de Herfindahl, 0–1; > 0,25 = mercado concentrado).
Clicar numa fatia ou numa linha põe aquele hospital em foco. Exporta CSV.

## Método

Para o hospital *h* com coordenadas do CNES, raios R₁ < R₂ < … < Rₖ (padrão 10, 25, 50, 100,
200, 400 km; editáveis na tela), a faixa *f* é a coroa (R₍f−1₎, R_f]; a última é aberta (> Rₖ).
O município-sede do hospital tem distância 0 (fica sempre na 1ª faixa, mesmo quando o hospital
está longe do centroide de uma cidade grande).

* **População da faixa** = soma da população de **todos** os municípios cujo centroide cai na
  faixa, tenham ou não mandado pacientes (esta era a falha da versão anterior, que somava só os
  municípios com internação e inflava as taxas distantes).
* **Taxa** da faixa *f* para o tipo *k*:
  `T_hkf = n_hkf × (12 / meses carregados) / pop_f × 10.000` → internações por 10 mil hab./ano.
* **Distância de referência** da faixa = média das distâncias dos municípios ponderada pela
  população (na 1ª faixa, nunca abaixo do ponto médio da faixa).
* **Fórmula de atratividade** — dois modelos ajustados por mínimos quadrados ponderados (peso =
  internações) em escala log, com d₀ = 10 km; fica o de maior R²:
  * potência  `T(d) = T₀ · (d / 10)^(−β)`   — β = decaimento; meia-distância d½ = 10 · 2^(1/β)
  * exponencial `T(d) = T₀ · e^(−λ (d − 10))` — d½ = 10 + ln 2 / λ
* **Índices observados** por tipo e para o hospital como um todo: d50 e d80 (distância que
  acumula 50 % / 80 % das internações, interpolada dentro da faixa), DMT (distância média
  ponderada pela taxa — independe de onde a população está), % local (1ª faixa) e **IA**,
  alcance relativo = ∫₁₀^D T̂(d) dd ÷ [T₀ · (D − 10)], com D = último raio: 0 = atração
  puramente local, 1 = atrai igualmente de perto e de longe.
* **TIH municipal** = internações de residentes do município (em qualquer hospital do dataset)
  × (12/meses) ÷ população × 1.000. Estatísticas: taxa da UF (∑n/∑pop), média, mediana, P10–P90,
  CV. Histograma com classes de Freedman–Diaconis (8 a 40), truncado no P99.

Limitações a declarar nos relatórios: distâncias em linha reta entre centroides (não tempo de
viagem); os arquivos SIH-RD são por UF do hospital, então residentes internados em outra UF não
entram na TIH municipal (carregue as UFs vizinhas para hospitais de fronteira); taxas de
municípios pequenos oscilam muito — leia-as junto com n e população.

População: `carregar_populacao()` baixa a estimativa mais recente do IBGE (SIDRA, tabela 6579)
uma vez e guarda em `GSPHOSP_HOME/cache/populacao_ibge_ultima.parquet` (validade 180 dias);
`carregar_populacao(ano=2022)` usa o Censo 2022 (tabela 4714). Sem internet, usa o cache antigo
ou a coluna NU_POPULACAO do CNES. O rótulo da fonte aparece em `descrever_fonte(pop)`.

Malha: 36 municípios minúsculos perderam a geometria na simplificação da malha embarcada
(ficam sem centroide quando o CNES também não tem coordenadas). `scripts\build_malha_ibge.py`
(pacote anterior) regenera a malha completa a partir do IBGE.

## Prompt de integração (TRAE / Antigravity)

> No GSPHosp, integrar as telas `papahr.ui.atratividade.widget.AtratividadeFaixasWidget` e
> `papahr.ui.tih.widget.TaxasMunicipaisWidget` ao Dashboard (`papahr/ui/pages/dashboard_page.py`),
> sem alterar os módulos novos (`papahr/dashboard/atratividade.py`, `populacao.py`,
> `papahr/ui/atratividade/*`, `papahr/ui/tih/*`, `papahr/ui/mapa/visor.py`, `mapa_faixas.html`).
>
> 1. Importar as duas classes no topo de `dashboard_page.py` (os módulos precisam ser importados
>    antes de o `QApplication` ser criado, por causa do QtWebEngine; `dashboard_page` já é
>    importado por `papahr/ui/app.py` antes do `QApplication`, confirme).
> 2. Em `_build_ui`, criar `self.tab_atrat = AtratividadeFaixasWidget()` e
>    `self.tab_tih = TaxasMunicipaisWidget()` e adicioná-los ao `QTabWidget` do dashboard como
>    "📍 Atratividade por faixas" e "📊 Taxas municipais", logo depois da aba "🗺️ Território / Huff".
>    Conectar `self.tab_atrat.hospitalSelecionado` a `self.tab_tih.selecionar_hospital`.
> 3. Em `_on_ready(self, m)`, depois de preencher as outras abas:
>    ```python
>    try:
>        from papahr.dashboard.atratividade import insumos_do_app
>        from papahr.dashboard.cnes_loader import get_cnes_data
>        df = self._current_active_df()                      # dataset COMPLETO (todos os hospitais da UF)
>        cnes_data = get_cnes_data().load()
>        from papahr.dashboard.atratividade import hospitais_da_uf
>        cm = "MUNIC_MOV" if "MUNIC_MOV" in df.columns else "MUNIC_RES"
>        uf = df[cm].cast(pl.Utf8).str.zfill(6).str.slice(0, 2).mode().item()     # UF mais frequente
>        _, mun = insumos_do_app(pl.DataFrame(), cnes_data.municipios, populacao=self._populacao_ibge)
>        hosp = hospitais_da_uf(cnes_data.estabelecimentos, uf, df, municipios=mun)   # TODOS os hospitais da UF + nº de AIH
>        cnes0 = (self._last_cnes or [None])[0]
>        self.tab_atrat.carregar(df, hosp, mun, cnes_inicial=cnes0)
>        self.tab_tih.carregar(df, hosp, mun, cnes_inicial=cnes0)
>    except Exception as exc:
>        log.warning("Abas de atratividade/TIH não preenchidas: %s", exc, exc_info=True)
>    ```
>    Não usar `m.hospitais_geo` aqui: ele só contém os CNES filtrados no Wizard. `hospitais_da_uf`
>    lista todos os hospitais da UF a partir do tbEstabelecimento do CNES (critérios na seção
>    "Quem é hospital"), com o nº de AIH do dataset — os sem AIH aparecem com 0 e a análise avisa;
>    hospital sem coordenada no CNES recebe o centroide do município (não some mais da lista).
>    Para o market share fazer sentido, o dataset tem de ser o SIH-RD completo da UF (pipeline
>    rodado sem filtrar hospitais no Wizard).
> 4. População IBGE: criar `self._populacao_ibge = pl.DataFrame()` no `__init__` e um
>    `QThread` (`PopulacaoWorker`) que chama `papahr.dashboard.populacao.carregar_populacao()`
>    ao abrir o dashboard; ao terminar, guardar o resultado em `self._populacao_ibge` e, se as abas
>    já tiverem sido carregadas, chamar de novo o bloco do item 3 (as taxas passam a usar o IBGE).
>    Mostrar `descrever_fonte(self._populacao_ibge)` num `QLabel` pequeno nas duas abas (nota de rodapé).
> 5. Na aba "🗺️ Território / Huff" (`_build_tab_terr` e `_fill_tab_terr`), remover o
>    `ConcentricRingsMap`, os combos `cbo_map_group`/`cbo_map_filter`/`cbo_map_radii`, a barra
>    `decay_bar`, `cbo_decay_hospital`, `lbl_decay_params`, `tbl_distance_decay`,
>    `plot_distance_decay` e os métodos `_rebuild_map_group_options`, `_refresh_filter_combo`,
>    `_on_map_group_changed`, `_on_map_filter_changed`, `_on_map_hospital_clicked`,
>    `_on_map_radii_changed` e `_on_decay_hospital_changed` — tudo isso foi substituído pelas abas
>    novas. Manter o gráfico ICSAP e as tabelas "Unidades por % ICSAP" e "Força de Atração (Huff)".
>    Em `papahr/dashboard/metrics.py`, deixar de chamar `_build_distance_decay` em
>    `compute_dashboard` (o campo `distance_decay` pode ficar vazio) — economiza tempo de cálculo.
> 6. A aba "Território" deve continuar funcionando sem o mapa; o widget do mapa de atratividade por
>    área (`papahr.ui.mapa.widget.MapaAtratividadeWidget`, do pacote anterior) não é afetado.
> 7. No `.gitignore`, acrescentar `!papahr/data/regioes_saude.parquet` (há uma regra `*.parquet`).
>    No `pyproject.toml`, em `[tool.setuptools.package-data]`, acrescentar `"ui/mapa/assets/**/*"`,
>    `"ui/*.qss"`, `"ui/atratividade/*.qss"` e `"ui/atratividade/*.svg"` à lista de `papahr`, para
>    instalação não-editável, Docker e o instalador levarem os assets do mapa e a folha compacta.
>    As telas aplicam `compacto.qss` em si mesmas (`aplicar_compacto`), então não precisam da folha
>    global do app; os sinais `resumoMudou(str)` das duas telas servem para a barra de status do app.
> 8. Importação direta de uma pasta de arquivos RD: na aba "📍 Atratividade por faixas" (ou no
>    menu do Dashboard), acrescentar um botão "Abrir pasta de arquivos RD…" que chama
>    `QFileDialog.getExistingDirectory` e, numa thread (`papahr.ui.atratividade.janela.CargaWorker`,
>    já pronta: sinais `progresso(str)`, `pronto(Carga)`, `erro(str)`), roda
>    `papahr.dashboard.importar_rd.carregar_fonte(pasta, uf=None, sem_ibge=False, progresso=...)`.
>    Ao terminar, `carga.aih`, `carga.hospitais`, `carga.municipios` alimentam `self.tab_atrat.carregar`
>    e `self.tab_tih.carregar` — sem passar pelo pipeline nem pelo Wizard. Mostrar `carga.avisos`
>    (SIGTAP sem cache, menos de 12 competências). `JanelaAtratividade` em `janela.py` é a referência
>    de como ligar botão → worker → telas.
> 8b. A caixa "Fonte" (SIH/CIHA/ambas) já existe dentro das duas telas e aparece sozinha quando o
>    dataset tem a coluna FONTE com as duas fontes; nada a fazer no Dashboard além de passar o
>    dataset com a coluna (o pipeline do app ainda não lê CIHA — a leitura está em
>    `papahr.dashboard.importar_rd`, função `ler_arquivo`, e pode ser chamada pelo processador).
> 8c. O download do DATASUS sem pipeline está em `importar_rd.baixar_datasus(faltantes, pasta, …)`
>    e a seleção UF × período × fontes em `importar_rd.Selecao` / `arquivos_da_selecao`; o Wizard
>    pode usá-los para "baixar só o que falta" em vez de rebaixar tudo a cada rodada.
> 9. No Passo 3 do Wizard, acrescentar a nota: "Para atratividade e market share, extraia a UF
>    inteira (sem chips de município/região): o filtro de município é aplicado ao município do
>    hospital e deixa de fora as internações dos residentes em outros hospitais".
> 10. Rodar `python -m pytest tests -q` e abrir o app com um dataset real: a aba de atratividade deve
>    listar todos os hospitais da UF (os sem AIH marcados); a aba de taxas deve abrir na UF do hospital.

## API rápida (para scripts e para o CLI)

```python
import polars as pl
from papahr.dashboard import atratividade as A
from papahr.dashboard.populacao import carregar_populacao

from papahr.dashboard.importar_rd import carregar_fonte, ler_pasta_rd

carga = carregar_fonte(r"D:\AIH_RD_2025", uf="MG", progresso=print)   # pasta de RD*.dbc OU parquet/csv
aih, hosp, mun = carga.aih, carga.hospitais, carga.municipios        # tudo que as telas usam
h = A.hospital_dict(hosp.row(0, named=True))                        # hospital com mais AIH
# só as AIH, sem CNES/IBGE: ler_pasta_rd(r"D:\AIH_RD_2025") (consolidado em cache)

perfil = A.perfil_completo(aih, h, mun, nivel="subgrupo")       # taxa por (categoria, faixa)
ajuste = A.ajustar_decaimento(perfil, limites=A.LIMITES_PADRAO)  # fórmula e índices por categoria
tih    = A.taxas_municipais(aih, mun, uf="MG", cnes_destaque=h["cnes"])
print(A.estatisticas_taxas(tih))
```

Níveis aceitos em `nivel`: todos, grupo, subgrupo, forma, procedimento, cid_capitulo, cid,
especialidade, carater, complexidade, financiamento, faixa_etaria, sexo (`A.niveis_disponiveis(aih)`
diz quais o dataset permite). Os nomes SIGTAP vêm das colunas `NO_GRUPO`/`NO_SUB_GRUPO`/
`NO_FORMA_ORGANIZACAO`/`NO_PROCEDIMENTO` do dataset; sem elas, usa-se o código de `PROC_REA`
com nomes de reserva para grupos e subgrupos.
