================================================================================
 PROMPT DE CORREÇÃO, VALIDAÇÃO E TESTE DO PROJETO GSPHosp
 Copie TODO este texto (do início ao fim) em outra ferramenta de IA para
 pedir: (a) auditoria independente das correções; (b) testes; (c) novas
 features; (d) refatoração. Inclui CONTEXTO COMPLETO, BUGS corrigidos com
 causa raiz + solução + arquivo + linha, CHECKLIST de 20 testes PASS e
 guia de como reproduzir UI real.
================================================================================

==============================
 PROJETO: GSPHosp (ex-PaPAHR)
 Objetivo: Gestão em Saúde Populacional Hospitalar sobre dados do DATASUS/SUS
 Linguagem: Python 3.11
 Stack: PySide6 (Qt GUI) + Polars (ETL rápido) + DuckDB (Medallion embedded)
 Repositório GitHub: https://github.com/chicocard/GSPHosp.git
 Pasta física local: d:\papapapa\GSPHosp-Python\
 Entry point GUI (shim .venv): .venv\Scripts\papahr-gui.exe
 Entry point Python: main.py (apenas chama papahr.ui.app.launch)
 Ambiente: Windows 10/11, PowerShell 7+
==============================

================================================================================
 1. RESUMO DO PROBLEMA QUE O USUÁRIO BATEU
================================================================================
O usuário é gestor de saúde e estava reportando 6 sintomas recorrentes:

  Sintoma A) Botão "📍 Aplicar filtros geográficos" não funciona (não carrega
             a lista de hospitais no Passo 4 ou lista fica vazia).
  Sintoma B) Botão "🔄 Carregar hospitais do CNES" não funciona (mesmo acima).
  Sintoma C) Digitar um CNES CONHECIDO do usuário (2219646 = Hospital Dilson
             Godinho, MG) → sistema exibe ALERTA FALSO:
             "CNES não correspondem à área selecionada" e rejeita.
  Sintoma D) Campo 🔎 "Buscar Hospital" (na verdade é um QLineEdit com
             textChanged, não botão) não encontra resultados.
  Sintoma E) Após selecionar 1 hospital e clicar "Executar Extração", o
             pipeline SIA começa a baixar mas PARA LOGO NO INÍCIO e dá erro:
             "Nenhum arquivo encontrado no FTP para os parâmetros informados.
             Sistema: SIA tipo=PA UFs=['AC','AL','AM',..., 'SP']
             Janela: 2025-01-01..2025-12-01"
             (note: UFs tinha 27 items = TODO O BRASIL, embora o usuário tivesse
             marcado só MG no Passo 2!)
  Sintoma F) O usuário até cogitou levar para outra LLM, mas não era falha do
             modelo: eram bugs de código e lógica reais.

================================================================================
 2. 8 BUGS RAIZ ENCONTRADOS E CORRIGIDOS (com localização exata)
================================================================================

BUG 1/8 — Leitura do CSV CNES com separador ERRADO
  Causa: O CSV do CNES do usuário (C:\DICIONARIO_DADOS\BASE_DE_DADOS_CNES_*)
         usa separador ";" (ponto-e-vírgula) + UTF-8 (padrão DATASUS Windows).
         O código usava defaults Polars que lêem ",". Resultado: 56 colunas
         viram 1 coluna só "CO_UNIDADE\";\"CO_CNES\";...". Tabela vazia na
         prática (não existia CO_CNES).
  Sintomas: A, B, C, D.
  Arquivo corrigido: papahr/dashboard/cnes_loader.py linhas 161-247
  Solução:
    - NOVO método _detect_csv_params(path) → tenta 4 separadores (; , tab |)
      × 4 encodings (utf8-lossy, latin-1, utf8, windows-1252) = 16 tentativas.
      Escolhe a combinação que retorna MAIOR quantidade de colunas. Vitória
      instantânea se encontrar coluna CO_CNES e ≥ 20 cols. Tem fallback
      binário também (lê 64KB raw bytes compara b';' vs b',').
    - REFATORADO _read_cnes_csv() → usa o _detect_csv_params, não mais defaults.

BUG 2/8 — Coluna de SIGLA_UF faltava. O CSV do usuário NÃO TEM coluna de sigla
  Causa: O código antigo procurava coluna "CO_UF" primeiro, mas ela NÃO EXISTE
         nos arquivos do usuário. O que existe é CO_ESTADO_GESTOR (ex: 31)
         código IBGE de 2 dígitos. Sem mapeamento, uf_hospital = "" vazio
         no filtro cascata.
  Sintomas: C, D.
  Arquivo corrigido: papahr/dashboard/cnes_loader.py linhas 316-377
  Solução: _normalize_estabs() cria coluna SINTÉTICA "SIGLA_UF" 100%
    preenchida com POLARS VETORIZADO (sem Python UDFs). Ordem de fallback:
      SIGLA_ESTADO_GESTOR → SIGLA_UF_GESTOR → SG_UF → UF
      → (CO_ESTADO_GESTOR via .replace(UF_CODIBGE_TO_SIGLA, default=""))
      → (2 primeiros dígitos de CO_MUNICIPIO_GESTOR via .replace).
    Resultado: CNES=2219646 → SIGLA_UF="MG". (Antes ficava vazio.)

BUG 3/8 — hospital_bate_geo aceitava MACROS/DRS de OUTRA UF
  Causa: Filtro cascata usava match FUZZY (prefixo 8 chars + código MR01).
         MG tem "MR01 - Metropolitana Belo Horizonte" e RJ também tem
         "MR01 - Metropolitana I" → mesmo código MR01, UFs diferentes.
         O usuário marcava UF=MG mas (por engano) macros do RJ → hospitais
         de MG apareciam. Controle negativo quebrado.
  Sintoma: Recorte geográfico parecia "frouxo" / incorreto.
  Arquivo corrigido: papahr/geo/reference.py linhas 688-714
  Solução: REGRA RÍGIDA 3 (dentro de hospital_bate_geo). Agora
    sel_macros / sel_drs usam IGUALDADE EXATA via set intersection:
      macros_da_uf = set(lista_macrorregioes(uf_do_hospital))
      def _qualquer_macro_da_uf(selecionadas):
          sels = set(s.strip() for s in selecionadas if s and s.strip())
          return len(sels & macros_da_uf) > 0
    NÃO USAR FUZZY NESTA FASE (a validação de pertencimento à UF TEM QUE
    ser booleana precisa). Match fuzzy é aplicado SÓ NA FASE FINAL de
    detalhe do hospital individual contra a própria macro/DRS selecionada.

BUG 4/8 — hospital_bate_geo retornava False se só tivesse município
  Causa: Se uf_hospital = "" (vazio, BUG 2 ainda presente) mas
         co_municipio_hospital="314330" vinha preenchido, a função
         rejeitava direto sem tentar enriquecer.
  Sintomas: C (alerta falso).
  Arquivo corrigido: papahr/geo/reference.py linhas 667-674
  Solução: Início da função hospital_bate_geo:
      if not uf_h and (mun_h or cod_reg_saude_h):
          uf_h, _, _, _, _, _ = resolve_geo_enrichment(
              co_municipio_6dig=mun_h, co_regiao_saude_cnes_cod=cod_reg_saude_h)
    Agora inferiu UF=MG corretamente para 314330 antes do filtro.

BUG 5/8 — (MAIS GRAVE) Pipeline FTP baixava 27 UFs ao invés da seleção
  Causa: Função _selected_ufs() em pages/__init__.py tinha FALLBACK
         DESASTROSO: return _selected_ufs_nonempty() or list(get_ufs()).
         Ou seja: se por QUALQUER motivo o chip de UF não estivesse marcado
         (bugs de clique, distração do usuário, chip renderizado antes de
          paint etc), ele baixava AUTOMATICAMENTE TODAS AS 27 UFs do Brasil.
         Resultado: 27 UFs × 12 meses = 324 arquivos SIA-PA de uma vez no FTP
         do DATASUS que recusa por volume. O pipeline "travava no começo".
  Sintoma: E (erro "Nenhum arquivo encontrado no FTP" com lista 27 UFs).
  Arquivos corrigidos:
    1) papahr/ui/pages/__init__.py L1035-1043 (removeu fallback)
    2) papahr/ui/pages/__init__.py L2124-2144 (NOVO BLOQUEIO EXPLÍCITO)
  Solução:
    a) _selected_ufs() AGORA retorna APENAS _selected_ufs_nonempty().
       Lista vazia significa lista vazia. NÃO EXPANDE.
    b) _on_run() no clique de "Executar Extração" tem NOVO CHECK:
          if not ufs: QMessageBox.warning "Selecione pelo menos 1 UF no Passo 2"
          if not dr: QMessageBox.warning "Selecione período no Passo 3"
       E dá "return" → não deixa o pipeline iniciar SEM seleção.

BUG 6/8 — Colar CNES 2219646 ANTES de clicar em "Aplicar filtros" → ERRO
  Causa: _add_cnes_codes_from_text() validava SÓ contra _hospitals_data que
         já estava carregada na memória. Mas a lista só carrega (1) ao entrar
         no Passo 4 (via QTimer.singleShot 50ms com a seleção DAQUELE
         momento), ou (2) ao clicar no botão. Ordem problemática do usuário:
         entra Passo 4 → volta Passo 2 e MUDA seleção para MG → volta Passo 4
         → COLA CNES 2219646 (sem clicar no botão) → _hospitals_data ainda
         estava com filtro anterior OU VAZIA → CNES não encontrado → alerta.
  Sintoma: C (alerta falso "CNES não correspondem à área selecionada").
  Arquivo corrigido: papahr/ui/pages/__init__.py linhas 1979-2051
  Solução: FALLBACK de ~80 linhas na validação de cada CNES colado.
    Se CNES não existir no lookup de _hospitals_data:
      1. Abre base CNES via get_cnes_data().load() / CNESData.buscar_...
      2. Extrai nome, UF, município, cod_reg_saude COM A MESMA cascata
         do BUG 2 (busca 8 colunas de UF com fallback para IBGE mapping).
      3. Roda hospital_bate_geo com A SELEÇÃO ATUAL de UF/Macro/DRS do
         usuário obtida via self._selected_ufs_nonempty() / chips_drs.
      4. Se BATER: cria chip VERDE normalmente, NÃO exibe alerta, e ADICIONA
         aquele hospital dinamicamente em _hospitals_data e lookup.
      5. Se NÃO BATER: invalida com mensagem DETALHADA
         "(fora da area: UF=MG mun=314330 reg=0006)" para debug rápido.
    Resultado: usuário pode colar CNES A QUALQUER MOMENTO (mesmo sem clicar
    no botão) que vai funcionar se o hospital pertencer à área selecionada.

BUG 7/8 — NameError QObject não importado em login_page.py → crash GUI
  Causa: referência runtime a QObject no eventFilter do "olhinho" da senha
         tinha esquecido de importar QObject explícitamente. A diretiva
         from __future__ import annotations protege type hints, mas referências
         runtime não são adiadas. Resultado: app fecha instantaneamente sem
         stack trace visível (foi o caso de um .bat "Resetar-E-Abrir" que
         não abria nada).
  Solução: Adicionar `from PySide6.QtCore import QObject` explícito em
    papahr/ui/pages/login_page.py. Já aplicado em sessão anterior.

BUG 8/8 — DuckDB locks após crash anterior + taskkill genérico matava processos
  Causa: DuckDB crasha → deixa wal/shm como lock. Taskkill /F /IM python.exe
         matava até terminal do IDE do usuário (porque o CMD roda python.exe
         também).
  Solução: kill_gsphosp_processes.py (criado na sessão anterior) +
    Resetar-E-Abrir-GSPHosp.bat atualizado que deleta arquivos
    *.duckdb-wal *.duckdb-shm antes de abrir GUI.

================================================================================
 3. DADOS DE REFERÊNCIA (TABELA CNES DO USUÁRIO)
================================================================================

Caminho local CNES do usuário:
  C:\DICIONARIO_DADOS\BASE_DE_DADOS_CNES_202512\

Parâmetros reais do arquivo tbEstabelecimento202512.csv:
  - Tamanho: ~600 MB
  - Shape: 598.661 linhas × 56 colunas
  - Após _normalize_estabs: 598.661 × 57 colunas (+SIGLA_UF sintética)
  - Separator: ";" (ponto-e-vírgula)
  - Quote: aspas duplas
  - Encoding: UTF-8

REGISTRO DO HOSPITAL "DILSON GODINHO" (CNES 2219646) — VALOR GOLDEN:
  CO_CNES              = 2219646
  NO_FANTASIA          = HOSPITAL DILSON GODINHO
  CO_ESTADO_GESTOR     = 31          (código IBGE de Minas Gerais)
  CO_MUNICIPIO_GESTOR  = 314330      (município: Patos de Minas, MG)
  CO_REGIAO_SAUDE      = 0006        (DRS 06 Patos de Minas)
  CO_TIPO_ESTABELECIMENTO = 006      (Hospital Geral)
  SIGLA_UF (sintética) = MG
  Hierarquia SUS esperada:
      UF = MG
      MACRORREGIÃO = MR02 - Triângulo / Alto Paranaíba
      DRS = DRS 06 - Patos de Minas
  Contagem apenas de MG: 73.985 hospitais (SIGLA_UF == 'MG')
  Busca textual por nome:
      "HOSPITAL DILSON GODINHO"  → 1 match exato na base total
      "dilson"                   → 241 matches totais (outros hospitais do
                                   país com "Dilson" no nome)
      "dilson" só em MG          → 27 matches

================================================================================
 4. CHECKLIST DE 20 TESTES (PARA OUTRA IA VALIDAR AUTÔNOMAMENTE)
================================================================================

FASE 1 — TESTES SEM GUI (rápidos, 3-10 s total, só Python)
Rode usando: .venv\Scripts\python.exe <seu_script.py>
Salve resultado em arquivo UTF-8 e NÃO use print() no PS para dataframes
(com acentos → UnicodeEncodeError cp1252).

[ ] 1. CNES loader lê CSV e retorna coluna SIGLA_UF.
       Verificar: "SIGLA_UF" in estabs.columns e n_unique(SIGLA_UF) >= 26.
       Resultado esperado: PASS (27 UFs).

[ ] 2. Contagem linhas estabelecimentos >= 500.000.
       Esperado: ~598.661 (± 1000).

[ ] 3. Filtrar MG via SIGLA_UF → >= 70.000 e <= 80.000.
       Esperado: ~73.985.

[ ] 4. Localizar CNES 2219646 por CO_CNES (cast Utf8 + strip + igualdade).
       Verificar NO_FANTASIA startswith "HOSPITAL DILSON GODINHO",
       CO_ESTADO_GESTOR = '31', CO_MUNICIPIO_GESTOR = '314330',
       CO_REGIAO_SAUDE = '0006'.
       Esperado: 1 linha encontrada, todos os campos acima corretos.

[ ] 5. resolve_geo_enrichment(co_municipio_6dig='314330',
       co_regiao_saude_cnes_cod='0006') retorna:
       (uf_final='MG', mun_final_zfill7='0314330',
        co_macro='MR02', no_macro contendo 'Triângulo',
        co_drs='06', no_drs contendo 'Patos de Minas').
       Esperado: Todos os 6 valores batem.

[ ] 6. hospital_bate_geo(uf_hospital='MG',
       co_municipio_hospital='314330', co_regiao_saude_hospital='0006',
       sel_ufs=['MG'], sel_macros=[], sel_drs=[]) → True.
       Esperado: True.

[ ] 7. hospital_bate_geo(uf_hospital='MG',
       sel_ufs=['SP'], sel_macros=[], sel_drs=[]) → False.
       (controle negativo de UF errada.) Esperado: False.

[ ] 8. hospital_bate_geo(uf_hospital='MG',
       sel_ufs=['MG'],
       sel_macros= todas_macros_do_RJ,  ← gerar via lista_macrorregioes('RJ')
       sel_drs=[]) → False.
       (controle negativo RÍGIDO 3 — macros de outra UF NÃO PODEM passar.)
       Esperado: False. (ANTES das correções retornava True = bug.)

[ ] 9. hospital_bate_geo(uf_hospital='',
       co_municipio_hospital='314330', co_regiao_saude_hospital='0006',
       sel_ufs=['MG'], sel_macros=[], sel_drs=[]) → True.
       (fallback UF vazia via município. Antigo bug 4.)
       Esperado: True.

[ ] 10. Simulacao da funcao _selected_ufs() com chips NAO marcados → [].
       Esperado: retorna lista VAZIA [].
       (De maneira NENHUMA pode retornar 27 UFs!)

[ ] 11. hospital_bate_geo(uf_hospital='MG',
        sel_ufs=['MG'],
        sel_macros=[MR02_TRIANGULO], sel_drs=[DRS_06_PATOS_DE_MINAS]) → True.
        (filtro cascata CERTO.) Esperado: True.

[ ] 12. Heurística 2 COMPLETA: varrer 598k linhas + hospital_bate_geo(MG só).
        Esperado: ~73.985 na lista final, CNES=2219646 ESTÁ na lista.

[ ] 13. Heurística 2 MG + MR02 + DRS 06.
        Esperado: ~9.070, CNES=2219646 ESTÁ na lista.

[ ] 14. search_filter(lista_do_teste_12, query='HOSPITAL DILSON GODINHO')
        → len == 1.
        search_filter(..., query='dilson') → len >= 10.
        search_filter(..., query='2219646') → len == 1.

[ ] 15. FALLBACK DE VALIDAÇÃO DO BUG R1:
        _hospitals_data = [] (vazia) + CNES colado=2219646 + sel_ufs=['MG'].
        Buscar fallback direto CNES + hospital_bate_geo → added=1, invalid=[].

FASE 2 — TESTES GUI REAL (PySide6)
Rodar a aplicação e clicar manualmente passo a passo.

[ ] 16. Login usuário/senha → abre Wizard Passo 1.

[ ] 17. Passo 2: NÃO marque NENHUMA UF. Avance para Passo 3, marque um mês
        qualquer. Avance para Passo 4. Clique "Executar Extração".
        Resultado esperado: NÃO INICIA PIPELINE;
        QMessageBox aparece: "Selecione pelo menos 1 UF no Passo 2..."
        (Isso garante que o BUG 5 está bloqueado; se o pipeline começar,
        as correções não foram aplicadas.)

[ ] 18. Volte Passo 2: marque MG. Opcionalmente MR02 Triângulo + DRS 06.
        Passo 3: marque JANEIRO-DEZEMBRO de 2024 (ANO ANTERIOR, sempre
        publicado). Passo 4.
        Botão 📍 Aplicar filtros → status mostra 73.985 hospital(is) em MG.
        Esperado: Lista QListWidget exibe 5.000 itens (limite de exibição).

[ ] 19. SEM clicar em "Aplicar filtros" (simulando o usuário impaciente):
        Cole "2219646" diretamente no campo "Colar CNES (separados por vírgula)"
        e pressione Enter.
        Resultado esperado:
          - NÃO aparece "CNES não correspondem à área selecionada"
          - Chip VERDE [MG] HOSPITAL DILSON GODINHO (9646) aparece.
        (BUG 6 resolvido.)

[ ] 20. Campo 🔎 Buscar Hospital → digite "DILSON GODINHO".
        Resultado esperado: lista diminui para 1 item só →
        2219646 — HOSPITAL DILSON GODINHO — MG.
        Digite "2219646" → 1 item. Limpe o campo → lista volta.

================================================================================
 5. MAPEAMENTO ARQUIVO + LINHA → FUNÇÃO AFETADA
================================================================================

papahr/dashboard/cnes_loader.py
  L0161-0216   def _detect_csv_params(path: Path) -> dict              [BUG 1]
  L0219-0247   def _read_cnes_csv(path, **overrides)                  [BUG 1]
  L0316-0377   CNESData._normalize_estabs  [cria coluna SIGLA_UF]     [BUG 2]

papahr/geo/reference.py
  L0633-0748   def hospital_bate_geo(...)                             [BUG 3 + 4]
  L0667-0674   fallback UF vazia por município                       [BUG 4]
  L0688-0714   REGRA RÍGIDA 3 igualdade exata macros/DRS por UF       [BUG 3]
  L0590-0630   def resolve_geo_enrichment(...)  (usado no fallback)

papahr/ui/pages/__init__.py
  L0830-0900   inicialização do QListWidget list_hosp / chips hosp
  L1035-1043   def _selected_ufs(self)  [sem fallback 27 ufs!]        [BUG 5]
  L1677-1886   def _reload_hospitals_from_cnes(self)  [Heurísticas 1+2]
  L1934-2064   def _add_cnes_codes_from_text(self, text)              [BUG 6]
  L1979-2051   bloco FALLBACK DIRETO CNES na validação                [BUG 6]
  L2103-2123   def _on_run(self) - início com extração parâmetros
  L2124-2144   NOVO BLOQUEIO ufs==[] OU dr==[] QMessageBox            [BUG 5]
  L2145+       montagem de PipelineRequest

papahr/ui/pages/login_page.py
  Linha ~340  from PySide6.QtCore import QObject (garantir presente)  [BUG 7]
  Linha ~346  _FIELD_PAD_RIGHT = "46px"
  eventFilter do QToolButton "olhinho" de visibilidade de senha
               sempre em cor #5EEAD4 (MD3 Teal 300)

================================================================================
 6. COMO TESTAR COMANDOS RÁPIDOS NO WINDOWS (POWER SHELL 7+)
================================================================================

1) Abrir app:
   cd d:\papapapa\GSPHosp-Python
   .venv\Scripts\papahr-gui.exe

2) Resetar DuckDB locks (se travou após crash):
   Duplo clique em: d:\papapapa\GSPHosp-Python\Resetar-E-Abrir-GSPHosp.bat

3) Compilação estática dos 3 arquivos críticos (sintaxe):
   .venv\Scripts\python.exe -m py_compile papahr/dashboard/cnes_loader.py `
       papahr/geo/reference.py papahr/ui/pages/__init__.py
   Exit code 0 = sintaxe ok.

4) Exemplo de script para reproduzir testes 1-15 (rodar isolado):
   --- início script ---
   import sys, os
   sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
   import polars as pl
   from papahr.dashboard.cnes_loader import get_cnes_data
   from papahr.geo.reference import (
       hospital_bate_geo, resolve_geo_enrichment,
       lista_macrorregioes, lista_drs, UF_CODIBGE_TO_SIGLA)

   c = get_cnes_data()
   c.load()
   e = c.estabelecimentos
   print(("SIGLA_UF" in e.columns, e.height,
          e.filter(pl.col("SIGLA_UF") == "MG").height))
   r = e.filter(pl.col("CO_CNES").cast(pl.Utf8).str.strip_chars() == "2219646")
   print(r.select(["CO_CNES","NO_FANTASIA","CO_ESTADO_GESTOR",
                   "CO_MUNICIPIO_GESTOR","CO_REGIAO_SAUDE","SIGLA_UF"]))
   print("bate_MG:", hospital_bate_geo('MG','314330','0006',
                                       ['MG'], [], []))
   print("bate_SP:", hospital_bate_geo('MG','314330','0006',
                                       ['SP'], [], []))
   rj = lista_macrorregioes('RJ')
   print("bate_macros_RJ_em_MG:", hospital_bate_geo('MG','314330','0006',
                                       ['MG'], rj, []))
   print("bate_uf_vazia:", hospital_bate_geo('','314330','0006',
                                       ['MG'], [], []))
   --- fim script ---

================================================================================
 7. HARD CONSTRAINTS E CONVENÇÕES NÃO QUEBRAR
================================================================================

- HC-1: Sempre artigo MASCULINO para o CNES → "o CNES" (não "a CNES").
- HC-2: Campos de senha SEMPRE tem botão de visibilidade "olhinho" na cor
        #5EEAD4 (teal 300 MD3). Implementar com QToolButton SOBREPOSTO via
        eventFilter. Nunca usar addAction(TrailingPosition) — quebra no
        Windows com QSS customizado do tema Material 3.
- HC-3: Senhas de entrada SEMPRE passam por .rstrip("\r\n\t ") antes de
        comparar hash / enviar.
- HC-4: NUNCA, em hipótese alguma, expandir "lista UFs vazia" para 27 UFs.
        Se vazia → bloquear e pedir ao usuário.
- HC-5: Georreferenciamento SEMPRE via resolve_geo_enrichment e
        hospital_bate_geo. NUNCA reimplementar a regra inline.
- HC-6: Leitura CSV CNES SEMPRE via _read_cnes_csv / _detect_csv_params.
        Nunca defaults Polars.
- HC-7: Enviar output com acentos para ARQUIVO UTF-8 e ler via
        [System.IO.File]::ReadAllText(path, [System.Text.Encoding]::UTF8).
        Jamais use print(dataframe) no PowerShell sem configurar
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8, e mesmo
        assim é flaky.

================================================================================
 8. PROXIMOS TESTES / FEATURES PENDENTES (APÓS VALIDAR OS 20 CHECKS)
================================================================================

  P1) Reproduzir o cenário exato do usuário:
      - Login → Passo 1 SIA-PA + FTP
      - Passo 2 = MG só / ou MR02 + DRS 06
      - Passo 3 = ANO INTEIRO ANTERIOR 2024 (não 2025, provavelmente ainda
        não publicado)
      - Passo 4 = colar 2219646 + chip criado.
      - Clicar "Executar Extração" → esperar 10-60 segundos (12 arquivos SIA
        de MG) → progresso encher 100% → Dashboard abre com preview.
  P2) Substituir 2024 por 2025 em meses que já foram publicados (Jan/Fev)
      para confirmar o erro de "Nenhum arquivo encontrado" só ocorre quando
      realmente não tem arquivo, e não por causa de 27 UFs.
  P3) Testar pipeline SIH-SUS (internações) — mesma lógica de UFs.
  P4) Validar DuckDB silver.dim_hospitais após rodar pipeline: Heurística 1
      deve retornar a mesma quantidade ou mais que a Heurística 2 (DuckDB
      enriquece com geolocalização própria).

================================================================================
 FIM DO PROMPT
================================================================================
