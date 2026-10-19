"""
widget.py — Tela "Atratividade por área" do GSPHosp (PySide6).

    from papahr.ui.mapa.widget import MapaAtratividadeWidget

    w = MapaAtratividadeWidget()
    w.carregar(od, resumo, hospitais)      # saídas de huff_por_area() + tabela de hospitais
    w.betaAlterado.connect(recalcular)     # usuário mudou β e pediu recálculo

Layout: painel de controles à esquerda (área, métrica, hospital, β, ranking) e
mapa Leaflet à direita (QWebEngineView), num QSplitter — o mapa ocupa ~72 %.

IMPORTANTE: este módulo deve ser importado ANTES de criar o QApplication
(exigência do QtWebEngine). O guarda abaixo cuida disso quando o import
acontece cedo; se o app já existir, aparece um aviso no console e o mapa
pode não desenhar.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import polars as pl
from PySide6.QtCore import (QAbstractTableModel, QCoreApplication, QModelIndex, QObject, QSortFilterProxyModel, Qt,
                            QUrl, Signal, Slot)
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QPushButton, QSplitter, QTableView, QVBoxLayout,
                               QWidget)

# --- QtWebEngine precisa disto antes do QApplication --------------------------
if QCoreApplication.instance() is None:
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
from PySide6.QtWebChannel import QWebChannel  # noqa: E402
from PySide6.QtWebEngineCore import QWebEngineSettings  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402

from papahr.dashboard.huff_area import METRICAS, listar_areas, payload_mapa  # noqa: E402

ASSETS = Path(__file__).resolve().parent / "assets"
PAGINA = ASSETS / "mapa_atratividade.html"


# ---------------------------------------------------------------------------
# utilidades
# ---------------------------------------------------------------------------

def _limpar(obj):
    """Troca NaN/inf por None para o JSON ser válido no JS."""
    if isinstance(obj, dict):
        return {k: _limpar(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_limpar(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


class _Ponte(QObject):
    """Objeto exposto ao JS como `ponte` (QWebChannel)."""
    clicado = Signal(str)

    @Slot(str)
    def hospitalClicado(self, cnes: str) -> None:
        self.clicado.emit(cnes)


# ---------------------------------------------------------------------------
# modelo da tabela de ranking
# ---------------------------------------------------------------------------

_COLS = [
    ("hosp_nome", "Hospital", "txt"),
    ("CNES", "CNES", "txt"),
    ("capt_obs", "Obs.", "int"),
    ("capt_esp", "Esp. (Huff)", "num1"),
    ("razao_obs_esp", "Obs/Esp", "num2"),
    ("n_mun_80", "Mun. 80%", "int"),
    ("dist_media_obs_km", "Dist. média km", "int"),
    ("pct_fora_mun", "% fora mun.", "pct"),
    ("pct_fora_regiao", "% fora região", "pct"),
    ("share_massa_area", "% massa área", "pct"),
]


def _fmt(v, kind):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "—"
    if kind == "txt":
        return str(v)
    if kind == "int":
        return f"{int(round(v)):n}".replace(",", ".")
    if kind == "num1":
        return f"{v:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if kind == "num2":
        return f"{v:.2f}".replace(".", ",")
    if kind == "pct":
        return f"{v * 100:.1f}%".replace(".", ",")
    return str(v)


class _RankingModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

    def set_rows(self, rows: list[dict]):
        self.beginResetModel(); self._rows = rows; self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(_COLS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return _COLS[section][1]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        key, _, kind = _COLS[index.column()]
        v = self._rows[index.row()].get(key)
        if role == Qt.ItemDataRole.DisplayRole:
            return _fmt(v, kind)
        if role == Qt.ItemDataRole.UserRole:        # valor bruto para ordenar
            return -math.inf if v is None else v
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter if kind == "txt" \
                else Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        return None

    def cnes_da_linha(self, row: int) -> str | None:
        return self._rows[row]["CNES"] if 0 <= row < len(self._rows) else None


# ---------------------------------------------------------------------------
# widget
# ---------------------------------------------------------------------------

class MapaAtratividadeWidget(QWidget):
    betaAlterado = Signal(float)          # usuário pediu recálculo com novo β
    hospitalSelecionado = Signal(str)     # "" quando volta para "todos"

    def __init__(self, parent: QWidget | None = None, beta: float = 1.5):
        super().__init__(parent)
        self._od: pl.DataFrame | None = None
        self._resumo: pl.DataFrame | None = None
        self._hosp: pl.DataFrame | None = None
        self._cnes_sel: str | None = None
        self._pagina_pronta = False
        self._pendente: dict | None = None
        self._montar(beta)

    # ---- construção ---------------------------------------------------------
    def _montar(self, beta: float):
        raiz = QHBoxLayout(self); raiz.setContentsMargins(0, 0, 0, 0); raiz.setSpacing(0)
        split = QSplitter(Qt.Orientation.Horizontal, self); raiz.addWidget(split)

        # painel esquerdo
        painel = QWidget(); painel.setMinimumWidth(320); painel.setMaximumWidth(560)
        pv = QVBoxLayout(painel); pv.setContentsMargins(8, 8, 8, 8); pv.setSpacing(8)

        form = QFormLayout(); form.setContentsMargins(0, 0, 0, 0); form.setSpacing(6)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.cmb_area = QComboBox(); self.cmb_area.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_metrica = QComboBox()
        for k, m in METRICAS.items():
            self.cmb_metrica.addItem(m["label"], k)
        self.cmb_hosp = QComboBox(); self.cmb_hosp.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        bh = QHBoxLayout(); bh.setSpacing(6)
        self.spn_beta = QDoubleSpinBox(); self.spn_beta.setRange(0.5, 3.0); self.spn_beta.setSingleStep(0.1)
        self.spn_beta.setDecimals(2); self.spn_beta.setValue(beta)
        self.btn_beta = QPushButton("Recalcular"); self.btn_beta.setToolTip("Recalcula o Huff com o novo β")
        bh.addWidget(self.spn_beta, 1); bh.addWidget(self.btn_beta, 0)
        form.addRow("Área", self.cmb_area)
        form.addRow("Métrica", self.cmb_metrica)
        form.addRow("Hospital", self.cmb_hosp)
        form.addRow("β (distância)", bh)
        pv.addLayout(form)

        self.lbl_resumo = QLabel("Carregue um resultado para ver o ranking."); self.lbl_resumo.setWordWrap(True)
        self.lbl_resumo.setStyleSheet("color: #52514e;")
        pv.addWidget(self.lbl_resumo)

        self.modelo = _RankingModel(self)
        self.proxy = QSortFilterProxyModel(self); self.proxy.setSourceModel(self.modelo)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.tbl = QTableView(); self.tbl.setModel(self.proxy)
        self.tbl.setSortingEnabled(True); self.tbl.sortByColumn(2, Qt.SortOrder.DescendingOrder)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.verticalHeader().setDefaultSectionSize(22)
        hdr = self.tbl.horizontalHeader(); hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.resizeSection(0, 190)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.setToolTip("Clique numa linha para focar o hospital no mapa")
        pv.addWidget(self.tbl, 1)

        bot = QHBoxLayout(); bot.setSpacing(6)
        self.btn_enquadrar = QPushButton("Enquadrar")
        self.btn_csv = QPushButton("Exportar CSV")
        bot.addWidget(self.btn_enquadrar); bot.addWidget(self.btn_csv); bot.addStretch(1)
        pv.addLayout(bot)

        # mapa
        self.view = QWebEngineView()
        st = self.view.settings()
        st.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)   # tiles https a partir de file://
        st.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        st.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        self.ponte = _Ponte(self)
        self.canal = QWebChannel(self.view.page())
        self.canal.registerObject("ponte", self.ponte)
        self.view.page().setWebChannel(self.canal)
        self.view.loadFinished.connect(self._ao_carregar)
        self.view.load(QUrl.fromLocalFile(str(PAGINA)))

        split.addWidget(painel); split.addWidget(self.view)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1)
        split.setSizes([400, 880])

        # sinais
        self.cmb_area.currentIndexChanged.connect(self._area_mudou)
        self.cmb_metrica.currentIndexChanged.connect(self._renderizar)
        self.cmb_hosp.currentIndexChanged.connect(self._hosp_combo_mudou)
        self.btn_beta.clicked.connect(lambda: self.betaAlterado.emit(self.spn_beta.value()))
        self.btn_enquadrar.clicked.connect(lambda: self._js("window.gsp.enquadrar()"))
        self.btn_csv.clicked.connect(self._exportar_csv)
        self.ponte.clicado.connect(self.selecionar_hospital)
        self.tbl.selectionModel().selectionChanged.connect(self._linha_mudou)

    # ---- dados ----------------------------------------------------------------
    def carregar(self, od: pl.DataFrame, resumo: pl.DataFrame, hospitais: pl.DataFrame) -> None:
        """Recebe as saídas de huff_por_area() e a tabela de hospitais (CNES, NOME, LAT, LON)."""
        self._od, self._resumo, self._hosp = od, resumo, hospitais
        self._cnes_sel = None
        area_atual = self.cmb_area.currentData()
        self.cmb_area.blockSignals(True); self.cmb_area.clear()
        for cod, nome, tot in listar_areas(resumo):
            self.cmb_area.addItem(f"{nome}  ({int(tot):,} AIH)".replace(",", "."), cod)
        if area_atual is not None:
            i = self.cmb_area.findData(area_atual)
            if i >= 0:
                self.cmb_area.setCurrentIndex(i)
        self.cmb_area.blockSignals(False)
        self._area_mudou()

    def beta(self) -> float:
        return self.spn_beta.value()

    def selecionar_hospital(self, cnes: str | None) -> None:
        """Seleciona (ou limpa, com None/"") o hospital em foco; sincroniza combo, tabela e mapa."""
        cnes = cnes or None
        self._cnes_sel = cnes
        i = self.cmb_hosp.findData(cnes if cnes else "")
        self.cmb_hosp.blockSignals(True); self.cmb_hosp.setCurrentIndex(max(i, 0)); self.cmb_hosp.blockSignals(False)
        # linha da tabela
        sm = self.tbl.selectionModel(); sm.blockSignals(True); self.tbl.clearSelection()
        if cnes:
            for r in range(self.proxy.rowCount()):
                src = self.proxy.mapToSource(self.proxy.index(r, 0)).row()
                if self.modelo.cnes_da_linha(src) == cnes:
                    self.tbl.selectRow(r); break
        sm.blockSignals(False)
        self.hospitalSelecionado.emit(cnes or "")
        self._renderizar()

    # ---- internos -------------------------------------------------------------
    def _area_mudou(self):
        if self._resumo is None:
            return
        cod = self.cmb_area.currentData()
        res = self._resumo.filter(pl.col("area_cod") == cod).sort("capt_obs", descending=True)
        self.modelo.set_rows(res.to_dicts())
        self.tbl.sortByColumn(2, Qt.SortOrder.DescendingOrder)
        self.cmb_hosp.blockSignals(True); self.cmb_hosp.clear()
        self.cmb_hosp.addItem("Todos — hospital dominante por município", "")
        for r in res.iter_rows(named=True):
            self.cmb_hosp.addItem(f"{r['hosp_nome']} ({r['CNES']})", r["CNES"])
        if self._cnes_sel and self.cmb_hosp.findData(self._cnes_sel) < 0:
            self._cnes_sel = None
        self.cmb_hosp.setCurrentIndex(max(self.cmb_hosp.findData(self._cnes_sel or ""), 0))
        self.cmb_hosp.blockSignals(False)
        tot_obs = res["capt_obs"].sum() if not res.is_empty() else 0
        self.lbl_resumo.setText(f"{res.height} hospitais com ≥ massa mínima na área · {int(tot_obs):,} internações observadas".replace(",", "."))
        self._renderizar()

    def _hosp_combo_mudou(self):
        self.selecionar_hospital(self.cmb_hosp.currentData() or None)

    def _linha_mudou(self, *_):
        idx = self.tbl.selectionModel().selectedRows()
        if not idx:
            return
        src = self.proxy.mapToSource(idx[0]).row()
        cnes = self.modelo.cnes_da_linha(src)
        if cnes and cnes != self._cnes_sel:
            self.selecionar_hospital(cnes)

    def _renderizar(self):
        if self._od is None or self.cmb_area.count() == 0:
            return
        payload = payload_mapa(
            self._od, self._resumo, self.cmb_area.currentData(),
            metrica=self.cmb_metrica.currentData(), cnes=self._cnes_sel, hospitais=self._hosp,
        )
        self._enviar(_limpar(payload))

    def _enviar(self, payload: dict):
        if not self._pagina_pronta:
            self._pendente = payload
            return
        self._js("window.gsp.setData(" + json.dumps(payload, ensure_ascii=False) + ")")

    def _js(self, codigo: str):
        self.view.page().runJavaScript(codigo)

    def _ao_carregar(self, ok: bool):
        self._pagina_pronta = bool(ok)
        if ok and self._pendente is not None:
            p, self._pendente = self._pendente, None
            self._enviar(p)

    def _exportar_csv(self):
        if self._od is None:
            return
        cod = self.cmb_area.currentData()
        sub = self._od.filter(pl.col("area_cod") == cod)
        if self._cnes_sel:
            sub = sub.filter(pl.col("CNES") == self._cnes_sel)
        nome = f"atratividade_{cod}" + (f"_{self._cnes_sel}" if self._cnes_sel else "") + ".csv"
        caminho, _ = QFileDialog.getSaveFileName(self, "Exportar CSV", nome, "CSV (*.csv)")
        if caminho:
            sub.write_csv(caminho, separator=";")


# ---------------------------------------------------------------------------
# demonstração: python -m papahr.ui.mapa.widget
# ---------------------------------------------------------------------------

def _dados_demo():
    """4 hospitais em MG e AIH sintéticas geradas por um Huff com ruído (só para ver a tela)."""
    import random
    from papahr.dashboard.huff_area import classificar_areas, huff_por_area
    from papahr.ui.mapa.malha import centroides_municipios

    random.seed(7)
    mun = centroides_municipios(ufs=["31"])
    cent = {r["COD_MUN"]: r for r in mun.to_dicts()}
    hosp = pl.DataFrame({
        "CNES": ["0026921", "0027014", "2126303", "2144228"],
        "NOME": ["Hospital A (Belo Horizonte)", "Hospital B (Belo Horizonte)", "Hospital C (Contagem)", "Hospital D (Montes Claros)"],
        "COD_MUN": ["310620", "310620", "311860", "314330"],
    }).with_columns(
        pl.col("COD_MUN").replace_strict({k: v["LAT"] for k, v in cent.items()}, return_dtype=pl.Float64).alias("LAT"),
        pl.col("COD_MUN").replace_strict({k: v["LON"] for k, v in cent.items()}, return_dtype=pl.Float64).alias("LON"),
    )
    mun = mun.with_columns(pl.when(pl.col("LAT") > -18).then(pl.lit("Norte")).otherwise(pl.lit("Centro-Sul")).alias("REGIAO"))
    massa = {"0026921": {"0408": 500, "0406": 300, "0303": 900}, "0027014": {"0408": 250, "0406": 60, "0303": 700},
             "2126303": {"0408": 180, "0406": 0, "0303": 500}, "2144228": {"0408": 220, "0406": 120, "0303": 600}}
    procs = {"0408": "0408050012", "0406": "0406010013", "0303": "0303010037"}

    def dist(a, b):
        r = 6371.0; p1, p2 = math.radians(a["LAT"]), math.radians(b["LAT"])
        dp = p2 - p1; dl = math.radians(b["LON"] - a["LON"])
        x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(x))

    H = hosp.to_dicts(); rows = []
    for m in mun.to_dicts():
        d = [max(3.0, dist(m, h)) for h in H]
        if min(d) > 350:
            continue
        for area, proc in procs.items():
            n = int(random.randint(1, 25) * max(0.05, 1 - min(d) / 350)) if area != "0406" else random.randint(0, 2)
            w = [massa[h["CNES"]][area] * dd ** -1.5 for h, dd in zip(H, d)]
            tot = sum(w)
            for _ in range(n):
                r = random.random() * tot; acc = 0
                for h, ww in zip(H, w):
                    acc += ww
                    if acc >= r:
                        break
                rows.append({"CNES": h["CNES"], "MUNIC_RES": m["COD_MUN"], "PROC_REA": proc,
                             "DIAG_PRINC": random.choice(["I219", "M171", "S720", "K359"]), "ESPEC": "01", "VAL_TOT": random.uniform(500, 9000)})
    aih = classificar_areas(pl.DataFrame(rows))
    return aih, hosp, mun


if __name__ == "__main__":
    import sys
    from papahr.dashboard.huff_area import huff_por_area

    app = QApplication(sys.argv)
    app.setStyle("Fusion"); app.setFont(QFont("Segoe UI", 10))
    qss = Path(__file__).resolve().parents[1] / "theme.qss"
    if qss.exists():
        app.setStyleSheet(qss.read_text(encoding="utf-8"))

    aih, hosp, mun = _dados_demo()
    w = MapaAtratividadeWidget(beta=1.5)
    w.setWindowTitle("GSPHosp — Atratividade por área (demonstração)")

    def recalcular(beta: float):
        od, res = huff_por_area(aih, hosp, mun, beta=beta)
        w.carregar(od, res, hosp)

    w.betaAlterado.connect(recalcular)
    recalcular(1.5)
    w.resize(1280, 800); w.show()
    sys.exit(app.exec())
