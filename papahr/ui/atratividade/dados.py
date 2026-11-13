"""Página "Dados": o que analisar — pasta, UF(s), período, fontes — antes de qualquer análise.

    pg = PaginaDados()
    pg.carregarPedido.connect(lambda pasta, selecao, sem_ibge: ...)   # "Carregar e analisar"
    pg.baixarPedido.connect(lambda pasta, faltantes: ...)             # "Baixar do DATASUS"
    pg.demoPedido.connect(...)
    pg.registrar(msg) / pg.etapa(i, n) / pg.ocupado(True|False)

A página lê só os NOMES dos arquivos da pasta (inventário instantâneo) e mostra, para a seleção feita,
quantos arquivos existem e quais faltam; os que faltam podem ser baixados do FTP do DATASUS.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

import polars as pl
from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QProgressBar,
    QPushButton, QRadioButton, QSplitter, QVBoxLayout, QWidget,
)

from papahr.dashboard.atratividade import UF_NOME, UF_SIGLA
from papahr.dashboard.importar_rd import Selecao, arquivos_da_selecao, inventario, listar_arquivos, nome_arquivo
from papahr.ui.atratividade.estilo import RotuloElidido, aplicar_compacto, botao_pequeno, rotulo_chave
from papahr.ui.atratividade.tabela import TabelaPolars

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
COLS_INV = [("uf", "UF", "txt", 36), ("fonte", "Fonte", "txt", 48), ("n", "Arquivos", "int", 60), ("primeira", "De", "txt", 70),
            ("ultima", "Até", "txt", 70), ("mb", "MB", "num0", 64), ("na_selecao", "Na seleção", "int", 72), ("faltam", "Faltam", "int", 56)]


class PaginaDados(QWidget):
    carregarPedido = Signal(str, object, bool)      # pasta, Selecao, sem_ibge
    baixarPedido = Signal(str, object)              # pasta, [(fonte, uf, comp), …]
    demoPedido = Signal()
    cancelarPedido = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        aplicar_compacto(self)
        self._settings = QSettings("GSPHosp", "atratividade")
        self._inv = pl.DataFrame()
        self._faltam: list[tuple[str, str, str]] = []
        self._construir()
        self._restaurar()

    # ------------------------------------------------------------------ UI
    def _construir(self) -> None:
        raiz = QVBoxLayout(self); raiz.setContentsMargins(8, 6, 8, 6); raiz.setSpacing(4)

        # pasta
        l0 = QHBoxLayout(); l0.setSpacing(4)
        self.ed_pasta = QLineEdit(); self.ed_pasta.setPlaceholderText(r"pasta com os arquivos RD*/CIHA* do DATASUS (ex.: D:\AIH_RD_2025)")
        self.btn_pasta = botao_pequeno("Escolher…", "Escolhe a pasta de dados")
        self.btn_atualizar = botao_pequeno("Atualizar", "Relê a lista de arquivos da pasta")
        l0.addWidget(rotulo_chave("Pasta de dados")); l0.addWidget(self.ed_pasta, 1); l0.addWidget(self.btn_pasta); l0.addWidget(self.btn_atualizar)
        raiz.addLayout(l0)

        # seleção: UFs · período · fontes · população
        sel = QWidget(); sel.setProperty("painel", True); g = QGridLayout(sel); g.setContentsMargins(8, 6, 8, 6); g.setHorizontalSpacing(8); g.setVerticalSpacing(4)
        g.addWidget(rotulo_chave("UF dos hospitais"), 0, 0, Qt.AlignmentFlag.AlignTop)
        ufs = QWidget(); gu = QGridLayout(ufs); gu.setContentsMargins(0, 0, 0, 0); gu.setHorizontalSpacing(3); gu.setVerticalSpacing(3)
        self.chips: dict[str, QPushButton] = {}
        for i, sg in enumerate(sorted(UF_SIGLA.values())):
            b = QPushButton(sg); b.setCheckable(True); b.setToolTip(UF_NOME.get(sg, sg)); b.setFixedWidth(40)
            b.toggled.connect(lambda _v: self._resumir())
            self.chips[sg] = b; gu.addWidget(b, i // 14, i % 14)
        gu.setColumnStretch(14, 1)
        g.addWidget(ufs, 0, 1, 1, 3)
        self.chk_outras = QCheckBox("incluir os arquivos das demais UFs que estiverem na pasta (residentes internados em outro estado)")
        self.chk_outras.toggled.connect(lambda _v: self._resumir())
        g.addWidget(self.chk_outras, 1, 1, 1, 3)

        g.addWidget(rotulo_chave("Período"), 2, 0)
        per = QHBoxLayout(); per.setSpacing(4)
        self.cmb_a0 = QComboBox(); self.cmb_m0 = QComboBox(); self.cmb_a1 = QComboBox(); self.cmb_m1 = QComboBox()
        ano = _dt.date.today().year
        for c in (self.cmb_a0, self.cmb_a1):
            for a in range(2008, ano + 1):
                c.addItem(str(a), a)
        for c in (self.cmb_m0, self.cmb_m1):
            for i, m in enumerate(MESES, 1):
                c.addItem(m, i)
        self.btn_ano_fechado = botao_pequeno("ano fechado", "Último ano com 12 competências publicadas (ano passado)")
        self.btn_tudo = botao_pequeno("tudo na pasta", "Todo o período que a pasta contém para as UFs marcadas")
        for w in (rotulo_chave("de"), self.cmb_m0, self.cmb_a0, rotulo_chave("a"), self.cmb_m1, self.cmb_a1, self.btn_ano_fechado, self.btn_tudo):
            per.addWidget(w)
        per.addStretch(1)
        g.addLayout(per, 2, 1)
        for c in (self.cmb_a0, self.cmb_m0, self.cmb_a1, self.cmb_m1):
            c.currentIndexChanged.connect(lambda _i: self._resumir())

        g.addWidget(rotulo_chave("Fontes"), 3, 0)
        fon = QHBoxLayout(); fon.setSpacing(10)
        self.chk_sih = QCheckBox("SIH-RD (AIH do SUS)"); self.chk_sih.setChecked(True)
        self.chk_ciha = QCheckBox("CIHA (internações não SUS: planos, particular)")
        self.rb_ibge = QRadioButton("população IBGE (SIDRA, com cache)"); self.rb_ibge.setChecked(True)
        self.rb_cnes = QRadioButton("população do CNES")
        self.chk_auto = QCheckBox("carregar a última seleção ao abrir"); self.chk_auto.setChecked(True)
        self.chk_auto.setToolTip("Ao abrir o app, recarrega sozinho a última seleção (rápido: vem do cache) sem sair desta aba")
        for w in (self.chk_sih, self.chk_ciha):
            w.toggled.connect(lambda _v: self._resumir()); fon.addWidget(w)
        fon.addSpacing(16); fon.addWidget(self.rb_ibge); fon.addWidget(self.rb_cnes); fon.addSpacing(16); fon.addWidget(self.chk_auto); fon.addStretch(1)
        self.chk_auto.toggled.connect(lambda v: self._settings.setValue("carregar_ultima", "1" if v else "0"))
        g.addLayout(fon, 3, 1, 1, 3)
        g.setColumnStretch(1, 1)
        raiz.addWidget(sel)

        # resumo da seleção + ações
        l2 = QHBoxLayout(); l2.setSpacing(4)
        self.lbl_sel = RotuloElidido("—"); self.lbl_sel.setProperty("resumo", True)
        self.btn_baixar = botao_pequeno("Baixar do DATASUS os que faltam", "Baixa do FTP do DATASUS (ftp.datasus.gov.br) os arquivos da seleção que não estão na pasta")
        self.btn_carregar = botao_pequeno("Carregar e analisar", "Lê os arquivos da seleção e abre as telas de análise", primario=True)
        self.btn_demo = botao_pequeno("Demonstração", "Hospitais fictícios e AIH sintéticas, só para conhecer as telas")
        self.btn_cancelar = botao_pequeno("Cancelar", "Interrompe a carga ou o download em andamento"); self.btn_cancelar.hide()
        l2.addWidget(self.lbl_sel, 1)
        for b in (self.btn_baixar, self.btn_carregar, self.btn_demo, self.btn_cancelar):
            l2.addWidget(b)
        raiz.addLayout(l2)

        # inventário da pasta + registro
        corpo = QSplitter(Qt.Orientation.Horizontal)
        self.tbl = TabelaPolars(COLS_INV, esticar_primeira=False)
        self.tbl.setToolTip("O que há na pasta, por UF e fonte, e quanto disso a seleção usa. Clique numa linha para marcar a UF.")
        corpo.addWidget(self.tbl)
        dir_ = QWidget(); ld = QVBoxLayout(dir_); ld.setContentsMargins(0, 0, 0, 0); ld.setSpacing(2)
        self.barra = QProgressBar(); self.barra.setRange(0, 1); self.barra.setValue(0); self.barra.setFormat("%v de %m"); self.barra.hide()
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(800)
        self.log.setPlaceholderText("registro da carga / do download")
        ld.addWidget(self.barra); ld.addWidget(self.log, 1)
        corpo.addWidget(dir_); corpo.setSizes([520, 700]); corpo.setStretchFactor(0, 2); corpo.setStretchFactor(1, 3)
        raiz.addWidget(corpo, 1)

        self.btn_pasta.clicked.connect(self._escolher_pasta)
        self.btn_atualizar.clicked.connect(self.atualizar_inventario)
        self.ed_pasta.editingFinished.connect(self.atualizar_inventario)
        self.btn_ano_fechado.clicked.connect(self._ano_fechado)
        self.btn_tudo.clicked.connect(self._tudo_na_pasta)
        self.btn_carregar.clicked.connect(self._carregar)
        self.btn_baixar.clicked.connect(self._baixar)
        self.btn_demo.clicked.connect(self.demoPedido.emit)
        self.btn_cancelar.clicked.connect(self.cancelarPedido.emit)
        self.tbl.linhaSelecionada.connect(self._linha_inventario)

    # ------------------------------------------------------------------ estado
    def pasta(self) -> str:
        return self.ed_pasta.text().strip()

    def ufs(self) -> list[str]:
        return [sg for sg, b in self.chips.items() if b.isChecked()]

    def selecao(self) -> Selecao:
        a0, m0 = int(self.cmb_a0.currentData()), int(self.cmb_m0.currentData())
        a1, m1 = int(self.cmb_a1.currentData()), int(self.cmb_m1.currentData())
        fontes = [f for f, w in (("SIH", self.chk_sih), ("CIHA", self.chk_ciha)) if w.isChecked()]
        return Selecao(ufs=self.ufs(), inicio=f"{a0:04d}{m0:02d}", fim=f"{a1:04d}{m1:02d}", fontes=fontes, outras_ufs=self.chk_outras.isChecked())

    def sem_ibge(self) -> bool:
        return self.rb_cnes.isChecked()

    def carregar_ultima(self) -> bool:
        return self.chk_auto.isChecked()

    def definir(self, pasta: str | None = None, ufs: list[str] | None = None, inicio: str | None = None, fim: str | None = None,
                fontes: list[str] | None = None) -> None:
        """Preenche a página (CLI / QSettings)."""
        if pasta:
            self.ed_pasta.setText(pasta)
        if ufs is not None:
            for sg, b in self.chips.items():
                b.blockSignals(True); b.setChecked(sg in {u.upper() for u in ufs}); b.blockSignals(False)
        for comp, ca, cm in ((inicio, self.cmb_a0, self.cmb_m0), (fim, self.cmb_a1, self.cmb_m1)):
            if comp and len(comp) == 6:
                ia = ca.findData(int(comp[:4])); im = cm.findData(int(comp[4:6]))
                if ia >= 0 and im >= 0:
                    ca.blockSignals(True); cm.blockSignals(True); ca.setCurrentIndex(ia); cm.setCurrentIndex(im); ca.blockSignals(False); cm.blockSignals(False)
        if fontes is not None:
            self.chk_sih.setChecked("SIH" in fontes); self.chk_ciha.setChecked("CIHA" in fontes)
        self.atualizar_inventario()

    def _restaurar(self) -> None:
        s = self._settings
        ufs = s.value("ufs", ""); fontes = s.value("fontes", "SIH")
        self.chk_auto.blockSignals(True); self.chk_auto.setChecked(str(s.value("carregar_ultima", "1")) != "0"); self.chk_auto.blockSignals(False)
        if str(s.value("pop", "ibge")) == "cnes":
            self.rb_cnes.setChecked(True)
        self.definir(pasta=s.value("pasta", "") or "", ufs=[u for u in str(ufs).split(",") if u] or None,
                     inicio=s.value("inicio", None), fim=s.value("fim", None), fontes=[f for f in str(fontes).split(",") if f])
        if not s.value("inicio"):
            self._ano_fechado()

    def _guardar(self) -> None:
        sel = self.selecao(); s = self._settings
        s.setValue("pasta", self.pasta()); s.setValue("ufs", ",".join(sel.ufs)); s.setValue("inicio", sel.inicio)
        s.setValue("fim", sel.fim); s.setValue("fontes", ",".join(sel.fontes)); s.setValue("pop", "cnes" if self.sem_ibge() else "ibge")

    # ------------------------------------------------------------------ ações
    def _escolher_pasta(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Pasta com os arquivos RD/CIHA do DATASUS", self.pasta() or str(Path.home()))
        if d:
            self.ed_pasta.setText(d); self.atualizar_inventario()

    def _ano_fechado(self) -> None:
        ano = _dt.date.today().year - 1
        self.definir(inicio=f"{ano}01", fim=f"{ano}12")

    def _tudo_na_pasta(self) -> None:
        inv = self._inv
        if inv.is_empty():
            return
        ufs = self.ufs()
        sub = inv.filter(pl.col("uf").is_in(ufs)) if ufs else inv
        if sub.is_empty():
            sub = inv
        comps = sorted(sub["comp"].to_list())
        self.definir(ufs=ufs or sorted(sub["uf"].unique().to_list()), inicio=comps[0], fim=comps[-1],
                     fontes=sorted(sub["fonte"].unique().to_list(), reverse=True))

    def atualizar_inventario(self) -> None:
        p = Path(self.pasta()) if self.pasta() else None
        self._inv = inventario(p) if p and p.is_dir() else pl.DataFrame(schema={"arquivo": pl.Utf8, "fonte": pl.Utf8, "uf": pl.Utf8, "comp": pl.Utf8, "mb": pl.Float64, "mtime": pl.Int64})
        # chips: UFs presentes na pasta em destaque (negrito via texto "MG•")
        presentes = set(self._inv["uf"].to_list()) if not self._inv.is_empty() else set()
        for sg, b in self.chips.items():
            b.setProperty("presente", sg in presentes); b.style().unpolish(b); b.style().polish(b)
            b.setToolTip(UF_NOME.get(sg, sg) + (" — há arquivos na pasta" if sg in presentes else " — sem arquivos na pasta"))
        if not self.ufs() and presentes:
            for sg in presentes:
                self.chips[sg].blockSignals(True); self.chips[sg].setChecked(True); self.chips[sg].blockSignals(False)
        self._resumir()

    def _resumir(self) -> None:
        inv = self._inv
        sel = self.selecao()
        if inv.is_empty():
            outros = listar_arquivos(self.pasta())["tabular"] if self.pasta() and Path(self.pasta()).is_dir() else []
            msg = "pasta sem arquivos RD/CIHA" + (f" (há {len(outros)} parquet/csv do pipeline: use 'Carregar' para abri-los)" if outros else "")
            self.lbl_sel.setTexto(f"{sel.rotulo()} · {msg}"); self.tbl.definir(pl.DataFrame()); self._faltam = []
            self.btn_baixar.setEnabled(bool(sel.ufs) and bool(sel.fontes) and bool(sel.competencias()))
            self.btn_carregar.setEnabled(bool(outros) or False)
            return
        comps = set(sel.competencias())
        ufs = set(sel.ufs)
        achados, faltam = arquivos_da_selecao(self.pasta(), sel) if sel.ufs and sel.fontes and comps else ([], [])
        self._faltam = faltam
        nomes_ach = {a.name for a in achados}
        linhas = []
        for (uf, fonte), g in sorted(inv.group_by(["uf", "fonte"]), key=lambda kv: kv[0]):
            cs = sorted(g["comp"].to_list())
            na_sel = int(g.filter(pl.col("arquivo").is_in(list(nomes_ach))).height)
            falt = sum(1 for f, u, c in faltam if u == uf and f == fonte)
            linhas.append({"uf": uf, "fonte": fonte, "n": g.height, "primeira": f"{cs[0][:4]}-{cs[0][4:]}", "ultima": f"{cs[-1][:4]}-{cs[-1][4:]}",
                           "mb": float(g["mb"].sum()), "na_selecao": na_sel, "faltam": falt})
        self.tbl.definir(pl.DataFrame(linhas))
        txt = f"{sel.rotulo()} · {len(achados)} arquivo(s) na pasta"
        if faltam:
            txt += f" · FALTAM {len(faltam)}: " + ", ".join(nome_arquivo(*x) for x in faltam[:6]) + (" …" if len(faltam) > 6 else "")
        if not ufs:
            txt = "marque ao menos uma UF · " + txt
        self.lbl_sel.setTexto(txt)
        self.btn_baixar.setEnabled(bool(faltam))
        self.btn_carregar.setEnabled(bool(achados))

    def _linha_inventario(self, linha: dict) -> None:
        uf = str(linha.get("uf") or "")
        if uf in self.chips and not self.chips[uf].isChecked():
            self.chips[uf].setChecked(True)

    def _carregar(self) -> None:
        self._guardar()
        self.carregarPedido.emit(self.pasta(), self.selecao(), self.sem_ibge())

    def _baixar(self) -> None:
        self._guardar()
        if self._faltam:
            self.baixarPedido.emit(self.pasta(), list(self._faltam))

    # ------------------------------------------------------------------ progresso (chamado pela janela)
    def registrar(self, msg: str) -> None:
        self.log.appendPlainText(msg.rstrip())
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def etapa(self, feitos: int, total: int) -> None:
        self.barra.setRange(0, max(1, total)); self.barra.setValue(feitos)

    def ocupado(self, sim: bool, inicio: str | None = None) -> None:
        for w in (self.btn_carregar, self.btn_baixar, self.btn_demo, self.btn_pasta, self.btn_atualizar, self.btn_tudo, self.btn_ano_fechado):
            w.setEnabled(not sim)
        self.btn_cancelar.setVisible(sim); self.btn_cancelar.setEnabled(sim)
        self.barra.setVisible(sim)
        if sim:
            self.barra.setRange(0, 0)
            if inicio:
                self.log.clear(); self.registrar(inicio)
        else:
            self._resumir()
