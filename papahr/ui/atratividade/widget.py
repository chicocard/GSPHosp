"""
widget.py — tela "Atratividade por faixas de distância" do GSPHosp (PySide6).

    from papahr.ui.atratividade.widget import AtratividadeFaixasWidget
    w = AtratividadeFaixasWidget()
    w.carregar(aih, hospitais, municipios)     # Polars: dataset analítico, hospitais (CNES, NOME, LAT, LON, COD_MUN),
                                               # municípios (COD_MUN, NOME, LAT, LON, POP)
    w.hospitalSelecionado.connect(fn)          # CNES do hospital em foco

Para cada hospital, tipo de internação (nível SIGTAP/CID/…) e faixa de distância:
taxa = internações × (12 / meses) / população da faixa × 10.000. A curva taxa × distância
é a atratividade; o módulo papahr.dashboard.atratividade ajusta os modelos e calcula os índices.

Importar este módulo ANTES de criar o QApplication (QtWebEngine).
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QSplitter, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from papahr.ui.mapa.visor import VisorMapa  # noqa: E402  (antes do QApplication)
from papahr.dashboard import atratividade as A
from papahr.ui.atratividade.estilo import RotuloElidido, aplicar_compacto, botao_pequeno, rotulo_chave
from papahr.ui.atratividade.grafico import GraficoDecaimento, INK, cor_categoria, tema_de
from papahr.ui.atratividade.market_share import MarketShareWidget
from papahr.ui.atratividade.seletor import SeletorHospital
from papahr.ui.atratividade.tabela import TabelaPolars

COLS_AJUSTE = [
    ("categoria", "Tipo de internação", "txt", 230),
    ("n", "Intern.", "int", 66),
    ("modelo_txt", "Modelo", "txt", 78),
    ("T0", "T₀ (10 km)", "num2", 72),
    ("decaimento", "β / λ", "sci", 60),
    ("d_meia", "d½", "km", 58),
    ("R2", "R²", "num2", 46),
    ("ia", "IA", "num2", 46),
    ("d50", "d50", "km", 60),
    ("d80", "d80", "km", 60),
    ("dmt", "DMT", "km", 60),
    ("taxa_local", "Taxa 1ª faixa", "num1", 78),
    ("pct_local", "% local", "pct", 58),
    ("formula", "Fórmula de atratividade", "txt", 260),
]
COLS_FAIXA = [
    ("rotulo", "Faixa", "txt", 98),
    ("n_mun", "Munic.", "int", 52),
    ("pop", "População", "int", 80),
    ("n", "Intern.", "int", 60),
    ("taxa", "Taxa", "num2", 60),
    ("pct", "% intern.", "pct", 66),
    ("cum_pct", "% acum.", "pct", 62),
    ("d_ref", "Dist. ref.", "km", 70),
]
MAX_SERIES = 8   # "hospital como um todo" + 7 categorias (paleta categórica validada)


class AtratividadeFaixasWidget(QWidget):
    hospitalSelecionado = Signal(str)
    resumoMudou = Signal(str)          # "14.643.879 AIH · 12 competências · …" (para a barra de status de quem hospeda)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._aih = pl.DataFrame(); self._hosp = pl.DataFrame(); self._mun = pl.DataFrame()
        self._hosp_ds = pl.DataFrame()
        self._perfil = pl.DataFrame(); self._ajuste = pl.DataFrame(); self._dist = pl.DataFrame()
        self._limites = A.LIMITES_PADRAO
        self._meses = 12
        self._categoria_atual: str = A.TODOS
        self._construir()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _construir(self) -> None:
        aplicar_compacto(self)
        raiz = QVBoxLayout(self); raiz.setContentsMargins(4, 3, 4, 3); raiz.setSpacing(3)

        # ---- painel de configuração: 2 linhas de 22 px, recolhível ------------------
        self.painel_cfg = QWidget(); cfg = QVBoxLayout(self.painel_cfg); cfg.setContentsMargins(0, 0, 0, 0); cfg.setSpacing(3)
        # linha 1: hospital em cascata (UF → região de saúde → município → hospital) + busca
        self.seletor = SeletorHospital(horizontal=True)
        cfg.addWidget(self.seletor)
        # linha 2: agregação, faixas, ações
        l2 = QHBoxLayout(); l2.setSpacing(4)
        self.cmb_nivel = QComboBox(); self.cmb_nivel.setMinimumContentsLength(12); self.cmb_nivel.setToolTip("Nível de agregação dos tipos de internação")
        self.cmb_fonte = QComboBox(); self.cmb_fonte.setMinimumContentsLength(9)
        for rot, val in (("ambas", None), ("SIH (SUS)", "SIH"), ("CIHA (não SUS)", "CIHA")):
            self.cmb_fonte.addItem(rot, val)
        self.cmb_fonte.setToolTip("Fonte das internações: SIH = AIH do SUS; CIHA = internações não SUS (planos/particular). Só aparece quando o dataset tem as duas.")
        self.lbl_fonte = rotulo_chave("Fonte"); self.lbl_fonte.hide(); self.cmb_fonte.hide()
        self.ed_faixas = QLineEdit(", ".join(f"{x:g}" for x in self._limites)); self.ed_faixas.setMinimumWidth(170); self.ed_faixas.setMaximumWidth(210)
        self.ed_faixas.setCursorPosition(0)
        self.ed_faixas.setToolTip("Raios dos círculos em km, separados por vírgula. A última faixa é aberta (> último raio). Enter recalcula.")
        self.btn_calc = botao_pequeno("Recalcular", "Recalcula taxas, ajustes, mapa e gráfico", primario=True)
        self.btn_csv = botao_pequeno("CSV", "Exporta perfil por faixa e tabela de atratividade (2 arquivos CSV)")
        self.btn_enq = botao_pequeno("Enquadrar", "Enquadra o mapa nas faixas do hospital")
        for w in (rotulo_chave("Agregar por"), self.cmb_nivel, self.lbl_fonte, self.cmb_fonte, rotulo_chave("Faixas (km)"), self.ed_faixas):
            l2.addWidget(w)
        l2.addStretch(1)
        for w in (self.btn_calc, self.btn_csv, self.btn_enq):
            l2.addWidget(w)
        cfg.addLayout(l2)
        raiz.addWidget(self.painel_cfg)

        # ---- corpo: resultados à esquerda (abas), mapa e gráfico à direita ------------
        self.split = QSplitter(Qt.Orientation.Horizontal, self)
        raiz.addWidget(self.split, 1)

        self.abas = QTabWidget(); self.abas.setDocumentMode(True)
        self.btn_cfg = QToolButton(); self.btn_cfg.setText("▴ configuração"); self.btn_cfg.setCheckable(True); self.btn_cfg.setChecked(True)
        self.btn_cfg.setToolTip("Mostra/oculta as linhas de configuração (hospital, agregação, faixas) para dar mais altura aos resultados")
        self.abas.setCornerWidget(self.btn_cfg, Qt.Corner.TopRightCorner)
        # aba 1: atratividade por tipo (tabela principal) + fórmula do tipo selecionado (1 linha)
        a1 = QWidget(); l_a1 = QVBoxLayout(a1); l_a1.setContentsMargins(2, 2, 2, 2); l_a1.setSpacing(2)
        self.tbl_ajuste = TabelaPolars(COLS_AJUSTE)
        self.tbl_ajuste.setToolTip("Clique numa linha: a aba Faixas, o mapa e o gráfico passam para esse tipo. Ordene pelas colunas.")
        self.lbl_formula = RotuloElidido("—"); self.lbl_formula.setProperty("nota", True)
        l_a1.addWidget(self.tbl_ajuste, 1); l_a1.addWidget(self.lbl_formula)
        self.abas.addTab(a1, "Atratividade por tipo")
        # aba 2: faixas do tipo selecionado
        a2 = QWidget(); l_a2 = QVBoxLayout(a2); l_a2.setContentsMargins(2, 2, 2, 2); l_a2.setSpacing(2)
        self.tbl_faixas = TabelaPolars(COLS_FAIXA, esticar_primeira=False); self.tbl_faixas.setSortingEnabled(False)
        self.lbl_faixas_titulo = RotuloElidido("Faixas — Hospital como um todo"); self.lbl_faixas_titulo.setProperty("nota", True)
        l_a2.addWidget(self.tbl_faixas, 1); l_a2.addWidget(self.lbl_faixas_titulo)
        self.abas.addTab(a2, "Faixas do tipo")
        # aba 3: tipos marcados para o gráfico (filtro e marcação ficam AQUI, não na barra geral)
        a3 = QWidget(); l_a3 = QVBoxLayout(a3); l_a3.setContentsMargins(2, 2, 2, 2); l_a3.setSpacing(2)
        l3 = QHBoxLayout(); l3.setSpacing(4)
        self.ed_busca = QLineEdit(); self.ed_busca.setPlaceholderText("filtrar tipos…"); self.ed_busca.setClearButtonEnabled(True)
        self.btn_todas = botao_pequeno("Marcar 7+", "Marca os 7 tipos de maior volume (o gráfico mostra no máximo 7, além do hospital como um todo)")
        self.btn_nenhuma = botao_pequeno("Limpar", "Desmarca todos")
        l3.addWidget(self.ed_busca, 1); l3.addWidget(self.btn_todas); l3.addWidget(self.btn_nenhuma)
        self.lst_cat = QListWidget(); self.lst_cat.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.lst_cat.setToolTip("Tipos marcados entram no gráfico (até 7). A tabela da 1ª aba tem todos.")
        l_a3.addLayout(l3); l_a3.addWidget(self.lst_cat, 1)
        self.abas.addTab(a3, "Tipos no gráfico")
        # aba 4: market share por faixa (concorrentes do hospital em foco)
        self.market = MarketShareWidget()
        self.abas.addTab(self.market, "Market share")
        self.split.addWidget(self.abas)

        dir_ = QSplitter(Qt.Orientation.Vertical)
        self.mapa = VisorMapa()
        self.graf = GraficoDecaimento(unidade="internações por 10 mil hab./ano")
        dir_.addWidget(self.mapa); dir_.addWidget(self.graf)
        dir_.setSizes([380, 300]); dir_.setStretchFactor(0, 5); dir_.setStretchFactor(1, 4)
        self.split.addWidget(dir_)
        self.split.setSizes([620, 660])
        self.split.setStretchFactor(0, 1); self.split.setStretchFactor(1, 1)

        # ---- sinais ---------------------------------------------------------
        self.btn_cfg.toggled.connect(self._alternar_cfg)
        self.seletor.hospitalMudou.connect(self._hospital_mudou)
        self.cmb_nivel.currentIndexChanged.connect(lambda _i: self.recalcular())
        self.cmb_fonte.currentIndexChanged.connect(lambda _i: self._fonte_mudou())
        self.ed_faixas.editingFinished.connect(self.recalcular)
        self.btn_calc.clicked.connect(self.recalcular)
        self.btn_csv.clicked.connect(self._exportar)
        self.btn_enq.clicked.connect(lambda: self.mapa.chamar("enquadrar"))
        self.ed_busca.textChanged.connect(self._filtrar_lista)
        self.btn_todas.clicked.connect(lambda: self._marcar(7))
        self.btn_nenhuma.clicked.connect(lambda: self._marcar(0))
        self.lst_cat.itemChanged.connect(lambda _it: self._atualizar_vista())
        self.tbl_ajuste.linhaSelecionada.connect(self._linha_ajuste)
        self.mapa.municipioClicado.connect(self._municipio_clicado)
        self.market.hospitalClicado.connect(self.selecionar_hospital)
        self._ctx_market: tuple | None = None
        self._resumo = "Carregue um dataset para começar."

    def destacar_market(self) -> MarketShareWidget:
        """Tira o market share das sub-abas para quem hospeda colocá-lo numa aba própria (a janela faz isso).
        O widget continua alimentado por esta tela: hospital em foco, tipo e faixas vêm daqui."""
        i = self.abas.indexOf(self.market)
        if i >= 0:
            self.abas.removeTab(i)
        self.market.setParent(None)
        return self.market

    def _alternar_cfg(self, mostrar: bool) -> None:
        self.painel_cfg.setVisible(mostrar)
        self.btn_cfg.setText("▴ configuração" if mostrar else "▾ configuração")

    def resumo(self) -> str:
        return self._resumo

    # ------------------------------------------------------------------
    # dados
    # ------------------------------------------------------------------
    def carregar(self, aih: pl.DataFrame, hospitais: pl.DataFrame, municipios: pl.DataFrame,
                 populacao: pl.DataFrame | None = None, cnes_inicial: str | None = None) -> None:
        """aih: dataset analítico; hospitais: CNES, NOME, LAT, LON, COD_MUN; municipios: COD_MUN, NOME, LAT, LON, POP."""
        self._aih_total = aih
        fontes = A.fontes_presentes(aih)
        self.lbl_fonte.setVisible(len(fontes) > 1); self.cmb_fonte.setVisible(len(fontes) > 1)
        if len(fontes) <= 1:
            self.cmb_fonte.blockSignals(True); self.cmb_fonte.setCurrentIndex(0); self.cmb_fonte.blockSignals(False)
        self._aih = A.filtrar_fonte(aih, self.cmb_fonte.currentData())
        self._mun = municipios
        if populacao is not None and not populacao.is_empty():
            from papahr.dashboard.populacao import juntar_populacao
            self._mun = juntar_populacao(self._mun, populacao)
        self._hosp = hospitais
        self._hosp_ds = A.hospitais_do_dataset(aih, hospitais, municipios=self._mun)
        self._meses = A.meses_competencia(aih)
        # combos (o seletor dispara hospitalMudou → recalcular; bloqueado até os níveis existirem)
        self.cmb_nivel.blockSignals(True); self.cmb_nivel.clear()
        for k, rot in A.niveis_disponiveis(aih):
            if k != "todos":
                self.cmb_nivel.addItem(rot, k)
        pref = self.cmb_nivel.findData("subgrupo")
        self.cmb_nivel.setCurrentIndex(pref if pref >= 0 else 0)
        self.cmb_nivel.blockSignals(False)
        pop_ok = int(self._mun.filter(pl.col("POP") > 0).height) if "POP" in self._mun.columns else 0
        por_fonte = ""
        if len(fontes) > 1:
            cont = {r["FONTE"]: r["n"] for r in aih.group_by("FONTE").agg(pl.len().alias("n")).to_dicts()}
            por_fonte = " (" + " + ".join(f"{cont.get(f, 0):,} {f}" for f in fontes) + ")"
        self._resumo = (f"{aih.height:,} AIH{por_fonte} · {self._meses} competências (fator anual {A.fator_anual(self._meses):.2f}) · "
                        f"{self._hosp_ds.height} hospitais com coordenadas · população em {pop_ok:,} municípios").replace(",", ".")
        self.setToolTip(self._resumo); self.resumoMudou.emit(self._resumo)
        if self._hosp_ds.is_empty():
            QMessageBox.warning(self, "Atratividade", "Nenhum hospital do dataset tem coordenadas (CNES). Verifique a base do CNES.")
            return
        self.seletor.carregar(self._hosp_ds, self._mun, cnes_inicial=cnes_inicial)   # dispara hospitalMudou → recalcular

    def _fonte_mudou(self) -> None:
        if not hasattr(self, "_aih_total") or self._aih_total.is_empty():
            return
        self._aih = A.filtrar_fonte(self._aih_total, self.cmb_fonte.currentData())
        self._ctx_market = None
        self.recalcular()

    def fonte(self) -> str | None:
        return self.cmb_fonte.currentData()

    def hospital_atual(self) -> dict | None:
        cnes = self.seletor.cnes()
        if not cnes or self._hosp_ds.is_empty():
            return None
        r = self._hosp_ds.filter(pl.col("CNES") == cnes)
        return A.hospital_dict(r.row(0, named=True)) if not r.is_empty() else None

    def _hospital_mudou(self, cnes: str) -> None:
        if cnes:
            self.hospitalSelecionado.emit(cnes)
        self.recalcular()

    def selecionar_hospital(self, cnes: str) -> None:
        if str(cnes).zfill(7) != (self.seletor.cnes() or ""):
            self.seletor.selecionar(cnes)

    def _ler_limites(self) -> tuple[float, ...]:
        txt = self.ed_faixas.text().replace(";", ",").replace(" ", "")
        try:
            lim = A.validar_limites(float(x.replace(",", ".")) for x in txt.split(",") if x)
        except ValueError as exc:
            QMessageBox.warning(self, "Faixas", str(exc))
            lim = self._limites
            self.ed_faixas.setText(", ".join(f"{x:g}" for x in lim)); self.ed_faixas.setCursorPosition(0)
        return lim

    def recalcular(self) -> None:
        h = self.hospital_atual()
        if h is None or self._aih.is_empty():
            return
        nivel = self.cmb_nivel.currentData() or "todos"
        self._limites = self._ler_limites()
        try:
            self._dist = A.distancias(h, self._mun, self._limites)
            self._perfil = A.perfil_completo(self._aih, h, self._mun, nivel=nivel, limites=self._limites, meses=self._meses)
            self._ajuste = A.ajustar_decaimento(self._perfil, limites=self._limites)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Atratividade", f"Falha ao calcular: {exc}")
            return
        self._preencher_lista()
        self._preencher_ajuste()
        self._categoria_atual = A.TODOS
        self.tbl_ajuste.selecionar_onde("categoria", A.TODOS)
        self._atualizar_vista()

    # ------------------------------------------------------------------
    # lista de categorias
    # ------------------------------------------------------------------
    def _preencher_lista(self) -> None:
        self.lst_cat.blockSignals(True); self.lst_cat.clear()
        cats = self._ajuste.filter(pl.col("categoria") != A.TODOS).sort("n", descending=True)
        for i, r in enumerate(cats.to_dicts()):
            it = QListWidgetItem(f"{r['categoria']}  ·  {int(r['n']):,}".replace(",", "."))
            it.setData(Qt.ItemDataRole.UserRole, r["categoria"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if i < 4 else Qt.CheckState.Unchecked)
            self.lst_cat.addItem(it)
        self.lst_cat.blockSignals(False)
        self._filtrar_lista(self.ed_busca.text())

    def _filtrar_lista(self, txt: str) -> None:
        t = (txt or "").strip().lower()
        for i in range(self.lst_cat.count()):
            it = self.lst_cat.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def _marcar(self, n: int) -> None:
        self.lst_cat.blockSignals(True)
        k = 0
        for i in range(self.lst_cat.count()):
            it = self.lst_cat.item(i)
            marcar = (not it.isHidden()) and k < n
            it.setCheckState(Qt.CheckState.Checked if marcar else Qt.CheckState.Unchecked)
            if marcar:
                k += 1
        self.lst_cat.blockSignals(False)
        self._atualizar_vista()

    def _marcadas(self) -> list[str]:
        out = []
        for i in range(self.lst_cat.count()):
            it = self.lst_cat.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append(it.data(Qt.ItemDataRole.UserRole))
        return out

    # ------------------------------------------------------------------
    # tabelas, gráfico e mapa
    # ------------------------------------------------------------------
    def _preencher_ajuste(self) -> None:
        if self._ajuste.is_empty():
            self.tbl_ajuste.definir(pl.DataFrame()); return
        df = self._ajuste.with_columns(
            pl.when(pl.col("modelo") == "potencia").then(pl.lit("potência")).when(pl.col("modelo") == "exponencial").then(pl.lit("exponencial")).otherwise(pl.lit("—")).alias("modelo_txt"),
            pl.coalesce([pl.col("beta"), pl.col("lambda")]).alias("decaimento"),
            (pl.col("categoria") == A.TODOS).alias("_todos"),
        )
        self.tbl_ajuste.definir(df, negrito_onde="_todos")

    def _linha_ajuste(self, linha: dict) -> None:
        self._categoria_atual = linha.get("categoria") or A.TODOS
        self._atualizar_vista()

    def _atualizar_vista(self) -> None:
        if self._perfil.is_empty():
            return
        h = self.hospital_atual()
        nivel = self.cmb_nivel.currentData() or "todos"
        cat = self._categoria_atual
        # faixas da categoria
        pc = self._perfil.filter(pl.col("categoria") == cat).sort("faixa")
        if pc.is_empty():
            cat = A.TODOS; self._categoria_atual = cat
            pc = self._perfil.filter(pl.col("categoria") == cat).sort("faixa")
        self.tbl_faixas.definir(pc)
        self.lbl_faixas_titulo.setTexto(f"Faixas de distância — {cat} · taxa = internações por 10 mil hab./ano; d. ref. = distância média ponderada pela população")
        # market share: só recalcula quando hospital, nível, tipo ou faixas mudam
        if h:
            chave = (h["cnes"], nivel, cat, self._limites)
            if chave != self._ctx_market:
                self._ctx_market = chave
                self.market.atualizar(self._aih, h, self._dist, self._limites, nivel if cat != A.TODOS else "todos",
                                      None if cat == A.TODOS else cat, self._hosp_ds)
        aj = self._ajuste.filter(pl.col("categoria") == cat)
        n_hosp = int(self._perfil.filter(pl.col("categoria") == A.TODOS)["n"].sum() or 0)
        if n_hosp == 0:
            self.lbl_formula.setTexto("Este hospital não tem AIH no dataset carregado: consta na base do CNES, mas os dados em uso não trazem "
                                      "internações dele — carregue o SIH-RD completo da UF.")
        else:
            self.lbl_formula.setTexto(f"{cat} — " + A.descrever_ajuste(aj.row(0, named=True) if not aj.is_empty() else {}))
        # mapa
        if h:
            nm = A.internacoes_por_municipio(self._aih, h["cnes"], nivel if cat != A.TODOS else "todos", cat)
            pay = A.payload_faixas(pc, self._dist, h, self._limites, nm, titulo=f"{cat} · internações por 10 mil hab./ano")
            reg = self.seletor.regiao()
            if reg:
                pay["regiao"] = {"nome": reg[1], "cods": self.seletor.municipios_da_regiao()}
            self.mapa.chamar("setFaixas", pay)
        # gráfico: hospital como um todo + categorias marcadas (máx. 7), a selecionada em destaque
        tema = tema_de(self.graf)
        marc = self._marcadas()
        if cat != A.TODOS and cat not in marc:
            marc = [cat] + marc
        marc = marc[:MAX_SERIES - 1]
        series = []
        for i, c in enumerate([A.TODOS] + marc):
            p = self._perfil.filter(pl.col("categoria") == c).sort("faixa")
            a = self._ajuste.filter(pl.col("categoria") == c)
            if p.is_empty():
                continue
            ar = a.row(0, named=True) if not a.is_empty() else {}
            series.append({
                "label": c, "x": p["d_ref"].to_list(), "y": p["taxa"].to_list(), "n": p["n"].to_list(), "rotulos": p["rotulo"].to_list(),
                "modelo": ar.get("modelo"), "T0": ar.get("T0"), "beta": ar.get("beta"), "lambda": ar.get("lambda"), "d0": A.D0_KM,
                "cor": INK[tema] if c == A.TODOS else cor_categoria(i - 1, tema), "principal": c == cat,
            })
        self.graf.desenhar(series)

    def _municipio_clicado(self, cod: str) -> None:
        if self._dist.is_empty():
            return
        r = self._dist.filter(pl.col("COD_MUN") == cod)
        if r.is_empty():
            return
        f = int(r["faixa"][0])
        # seleciona a faixa correspondente na tabela
        for i, l in enumerate(self.tbl_faixas.modelo.linhas):
            if int(l.get("faixa", -1)) == f:
                idx = self.tbl_faixas.proxy.mapFromSource(self.tbl_faixas.modelo.index(i, 0))
                self.tbl_faixas.setCurrentIndex(idx)
                break

    # ------------------------------------------------------------------
    def _exportar(self) -> None:
        if self._perfil.is_empty():
            return
        h = self.hospital_atual() or {}
        base = f"atratividade_{h.get('cnes', 'hospital')}"
        caminho, _ = QFileDialog.getSaveFileName(self, "Exportar CSV", base + ".csv", "CSV (*.csv)")
        if not caminho:
            return
        p = Path(caminho)
        self._perfil.write_csv(p, separator=";")
        self._ajuste.write_csv(p.with_name(p.stem + "_ajuste.csv"), separator=";")
        QMessageBox.information(self, "Exportar", f"Gravados:\n{p}\n{p.with_name(p.stem + '_ajuste.csv')}")
