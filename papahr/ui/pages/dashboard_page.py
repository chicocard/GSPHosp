"""Página 4 do Dashboard: Saúde Populacional e Dinâmica Territorial.

Layout (Qt + pyqtgraph):
  · KPIs globais (top row): estabelecimentos, produção, valor, ICSAP%, readmissão%
  · QTabWidget 5 abas:
      1) ESTRATIFICAÇÃO DE RISCO → LACE bar, estratos donut, tabela CMI
      2) EFICIÊNCIA CLÍNICA → LOS O/E, custo O/E, margem% por hospital
      3) TERRITÓRIO → ICSAP por condição + mapa scatter (lat x lon cor=pct_icsap) + HUFF
      4) TRANSIÇÃO DE CUIDADO → linhas de cuidado (barras duplas: contato48h vs readmissão)
      5) VISÃO GERAL → série temporal + top10s + alertas
"""
from __future__ import annotations

import math
import traceback
from pathlib import Path
from typing import Any

import polars as pl

try:
    import pyqtgraph as pg
    from pyqtgraph import PlotWidget
except ImportError:  # pragma: no cover
    pg = None
    PlotWidget = Any  # type: ignore

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from papahr.dashboard import DashboardMetrics, compute_dashboard
from papahr.logging_config import get_logger
from papahr.db import get_db
from papahr.ui.widgets import (
    ConcentricRingsMap,
    DeferredSplitter,
    HospitalMapPoint,
    KpiCard,
    StatusBadge,
)

from papahr.ui import charts

log = get_logger(__name__)

# Paleta teal Material 3 (dark background + teal accents, compatível com QSS)
_BG = "#162023"
_PRI = "#14B8A6"
_PRI_DARK = "#0D9488"
_WARN = "#F59E0B"
_DANG = "#EF4444"
_OK = "#22C55E"
_TXT = "#E6FFFA"
_TXT_DIM = "#94A3B8"
_LINE = "#233539"


def _color_for_pct(pct: float) -> QColor:
    """Para ICSAP/readmissão: verde ⇒ amarelo ⇒ vermelho."""
    pct = min(100.0, max(0.0, pct))
    if pct < 15:
        t = pct / 15.0
        return _lerp_color(_OK, _PRI, t)
    if pct < 30:
        t = (pct - 15) / 15.0
        return _lerp_color(_PRI, _WARN, t)
    t = min(1.0, (pct - 30) / 30.0)
    return _lerp_color(_WARN, _DANG, t)


def _lerp_color(a: str, b: str, t: float) -> QColor:
    ca = QColor(a); cb = QColor(b); t = max(0.0, min(1.0, t))
    return QColor(
        int(ca.red()   + (cb.red()   - ca.red())   * t),
        int(ca.green() + (cb.green() - ca.green()) * t),
        int(ca.blue()  + (cb.blue()  - ca.blue())  * t),
    )


# ---------------------------------------------------------------------------
# Worker thread (dashboard pode ser pesado, 100k+ linhas)
# ---------------------------------------------------------------------------
class DashboardWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, df: pl.DataFrame, cnes: list[str] | None = None, *,
                 system: str = "SIA_PA", beta: float | None = None,
                 compute_huff: bool = True) -> None:
        super().__init__()
        self._df = df
        self._cnes = cnes
        self._system = system
        self._beta = beta
        self._huff = compute_huff

    def run(self) -> None:  # pragma: no cover
        try:
            self.progress.emit("Calculando KPIs globais e estratificação de risco...")
            m = compute_dashboard(self._df, self._cnes, system_kind=self._system,
                                  beta=self._beta, compute_huff=self._huff)
            self.finished_ok.emit(m)
        except Exception as exc:  # noqa: BLE001
            log.exception("Dashboard worker falhou")
            self.failed.emit(str(exc))


# ---------------------------------------------------------------------------
# Helpers de plot
# ---------------------------------------------------------------------------
def _setup_pg_theme() -> None:  # pragma: no cover
    if pg is None:
        return
    pg.setConfigOptions(
        background=_BG, foreground=_TXT, antialias=True,
        imageAxisOrder="row-major",
    )


def _brush(color: str) -> QBrush:
    return QBrush(QColor(color))


def _pen(color: str, width: int = 1, style: Qt.PenStyle = Qt.SolidLine) -> QPen:
    p = QPen(QColor(color), width)
    p.setStyle(style)
    return p


# ---------------------------------------------------------------------------
class DashboardPage(QWidget):
    """Página de Saúde Populacional."""

    hospital_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _setup_pg_theme()
        self._metrics: DashboardMetrics | None = None
        self._last_df: pl.DataFrame | None = None
        self._last_system: str = "SIA_PA"
        self._last_cnes: list[str] | None = None
        self._muni_df: pl.DataFrame | None = None
        self._map_group_options: dict[str, list[str]] = {"grupo": [], "forma": [], "procedimento": []}
        # Fonte dos dados: "session" (df pipeline atual) | "duckdb" (persistido DuckDB)
        self._fonte_atual: str = "session"
        self._df_duckdb: pl.DataFrame | None = None
        # Camada Medallion de origem: "GOLD_OTIMIZADO" | "SILVER_FALLBACK" | "SESSAO_PIPELINE" | ""
        self._fonte_dados_camada: str = ""
        self._build_ui()

    # ------------------------------------------------------------------
    @staticmethod
    def _compact_combo(cbo: QComboBox, chars: int = 18) -> None:
        """Combo que NÃO exige a largura do maior item (antes forçava a página a 2.300 px)."""
        cbo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        cbo.setMinimumContentsLength(chars)
        cbo.setMinimumWidth(120)
        cbo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        cbo.setStyleSheet("padding:6px 10px;")

    def _build_ui(self) -> None:
        self.setObjectName("DashboardPage")
        self.setAutoFillBackground(True)
        self.setStyleSheet("QWidget#DashboardPage{background:#0F1A1C;}")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # --- Header Card (Toolbar em 3 linhas limpas e espaçosas sem sobreposição ou corte de texto) ---
        head_card = QFrame()
        head_card.setStyleSheet("QFrame{background:#121C1E;border-radius:10px;border:1px solid #233539;}")
        h_layout = QVBoxLayout(head_card)
        h_layout.setContentsMargins(16, 16, 16, 16)
        h_layout.setSpacing(10)

        # Linha 0: Título + Status
        row0 = QHBoxLayout()
        title = QLabel("📊 Gestão de Saúde Populacional — Dinâmica Territorial")
        title.setStyleSheet("font-size:17px; font-weight:700; color:#4FD1C5;")
        self.lbl_status = StatusBadge("Aguardando dataset", status="info")
        row0.addWidget(title)
        row0.addStretch(1)
        row0.addWidget(self.lbl_status)
        h_layout.addLayout(row0)

        # Linha 1: Filtros de Sistema & Parâmetros + Botões de Ação
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        lbl_sys = QLabel("Sistema:")
        lbl_sys.setStyleSheet("font-weight:600;color:#94A3B8;")
        self.cbo_system = QComboBox()
        self.cbo_system.addItem("SIH-RD (Internação Hospitalar — AIHs)", "SIH_RD")
        self.cbo_system.addItem("SIH-RJ (AIHs Rejeitadas)", "SIH_RJ")
        self.cbo_system.addItem("SIH-SP (Serviços Profissionais — AIH)", "SIH_SP")
        self.cbo_system.addItem("SIA-PA (Produção Ambulatorial)", "SIA_PA")
        self.cbo_system.setCurrentIndex(0)
        self._compact_combo(self.cbo_system, 22)

        self.chk_huff = QCheckBox("Modelo Huff")
        self.chk_huff.setChecked(True)
        self.chk_huff.setStyleSheet("font-weight:600;color:#E6FFFA;")

        self.lbl_beta = QLabel("β decay:")
        self.lbl_beta.setStyleSheet("font-weight:600;color:#94A3B8;")
        self.cbo_beta = QComboBox()
        self.cbo_beta.addItem("2.2 — Ambulatorial", 2.2)
        self.cbo_beta.addItem("1.5 — Média Complexidade", 1.5)
        self.cbo_beta.addItem("0.9 — Alta Complexidade", 0.9)
        self.cbo_beta.setCurrentIndex(1)
        self._compact_combo(self.cbo_beta, 16)

        self.btn_calc = QPushButton("⚡ Calcular")
        self.btn_calc.setMinimumWidth(110)
        self.btn_calc.setMinimumHeight(34)
        self.btn_calc.clicked.connect(self._on_calculate)
        self.btn_calc.setStyleSheet(
            "QPushButton{background:#0D9488;color:#FFFFFF;padding:6px 14px;border-radius:8px;font-weight:800;border:1px solid #2DD4BF;}"
            "QPushButton:hover{background:#0F766E;color:#FEF08A;}"
        )

        self.btn_export = QPushButton("📥 Exportar CSV")
        self.btn_export.setMinimumWidth(120)
        self.btn_export.setMinimumHeight(34)
        self.btn_export.clicked.connect(self._on_export)
        self.btn_export.setEnabled(False)
        self.btn_export.setStyleSheet(
            "QPushButton{background:#233539;color:#E6FFFA;padding:6px 12px;border-radius:8px;border:1px solid #2D4A4A;}"
            "QPushButton:hover{background:#2D4A4A;}"
        )

        row1.addWidget(lbl_sys); row1.addWidget(self.cbo_system)
        row1.addSpacing(10)
        row1.addWidget(self.chk_huff)
        row1.addSpacing(10)
        row1.addWidget(self.lbl_beta); row1.addWidget(self.cbo_beta)
        row1.addStretch(1)
        row1.addWidget(self.btn_calc); row1.addWidget(self.btn_export)
        h_layout.addLayout(row1)

        # Linha 2: Fonte de Dados + Carga Persistida
        row2 = QHBoxLayout()
        row2.setSpacing(10)
        lbl_fonte = QLabel("📦 Fonte dos dados:")
        lbl_fonte.setStyleSheet("font-weight:600;color:#94A3B8;")
        self.cbo_fonte_dados = QComboBox()
        self.cbo_fonte_dados.addItem("🟢 Sessão Atual", "session")
        self.cbo_fonte_dados.addItem("💾 DuckDB — Dados Persistidos", "duckdb")
        self._compact_combo(self.cbo_fonte_dados, 18)
        self.cbo_fonte_dados.currentIndexChanged.connect(self._on_fonte_dados_changed)

        lbl_carga = QLabel("🗂 Carga persistida:")
        lbl_carga.setStyleSheet("font-weight:600;color:#94A3B8;")
        self.cbo_load_runs = QComboBox()
        self.cbo_load_runs.addItem("⏳ Todas as cargas", None)
        self._compact_combo(self.cbo_load_runs, 16)
        self.cbo_load_runs.setEnabled(False)

        self.btn_reload_fonte = QPushButton("🔄 Recarregar")
        self.btn_reload_fonte.setMinimumWidth(120)
        self.btn_reload_fonte.setMinimumHeight(32)
        self.btn_reload_fonte.setStyleSheet(
            "QPushButton{background:#0D9488;color:#FFFFFF;padding:6px 12px;border-radius:8px;font-weight:800;border:1px solid #2DD4BF;}"
            "QPushButton:hover{background:#0F766E;color:#FEF08A;}"
        )
        self.btn_reload_fonte.clicked.connect(self._on_reload_fonte_clicked)

        self.btn_load_files = QPushButton("📂 Carregar arquivos")
        self.btn_load_files.setMinimumWidth(170)
        self.btn_load_files.setMinimumHeight(32)
        self.btn_load_files.setStyleSheet(
            "QPushButton{background:#233539;color:#E6FFFA;padding:6px 12px;border-radius:8px;border:1px solid #2D4A4A;}"
            "QPushButton:hover{background:#2D4A4A;color:#FEF08A;}"
        )
        self.btn_load_files.setToolTip(
            "Carrega CSV, Parquet, DBF ou DBC (DBF/DBC via leitor DATASUS)."
        )
        self.btn_load_files.clicked.connect(self._on_load_files)

        self.lbl_fonte_info = QLabel("Fonte: sessão atual | 0 linhas")
        self.lbl_fonte_info.setStyleSheet("color:#8FA6A3;font-weight:600;padding-left:6px;")
        self._badge_fonte_camada = StatusBadge("Camada: —", status="info")

        row2.addWidget(lbl_fonte); row2.addWidget(self.cbo_fonte_dados)
        row2.addSpacing(10)
        row2.addWidget(lbl_carga); row2.addWidget(self.cbo_load_runs)
        row2.addWidget(self.btn_reload_fonte)
        row2.addSpacing(10)
        row2.addWidget(self.btn_load_files)
        row2.addStretch(1)
        row2.addWidget(self.lbl_fonte_info)
        row2.addWidget(self._badge_fonte_camada)
        h_layout.addLayout(row2)

        root.addWidget(head_card)

        # --- KPI row ---
        self.kpi_estabs  = KpiCard("Estabelecimentos", 0, icon="🏥", accent=_PRI)
        self.kpi_prod    = KpiCard("Nº AIHs / APACs", 0, icon="📄", accent="#6366F1")
        self.kpi_valor   = KpiCard("Valor Total (R$)", 0, icon="💸", accent="#F59E0B")
        self.kpi_icsap   = KpiCard("% ICSAP (evitáveis)", 0.0, icon="⚠️", accent=_WARN, format="{:.1f} %")
        self.kpi_los     = KpiCard("LOS médio (dias)", 0.0, icon="🛏️", accent="#818CF8", format="{:.2f}")
        self.kpi_readm   = KpiCard("Readmissão 30d estimada", 0.0, icon="↩️", accent=_DANG, format="{:.1f} %")

        kpi_grid = QGridLayout()
        kpi_grid.setSpacing(10)
        kpis = [self.kpi_estabs, self.kpi_prod, self.kpi_valor,
                 self.kpi_icsap, self.kpi_los, self.kpi_readm]
        # KPI cards em UMA linha de 6 (cabe em 1366 px; cada card ~210 px)
        for col in range(6):
            kpi_grid.setColumnStretch(col, 1)
        for idx, k in enumerate(kpis):
            row, col = divmod(idx, 6)
            kpi_grid.addWidget(k, row, col)
        root.addLayout(kpi_grid)

        # --- Abas ---
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabBar::tab{background:#162023;color:#94A3B8;padding:10px 18px;border:1px solid #233539;
                         border-top-left-radius:10px;border-top-right-radius:10px;margin-right:4px;}
            QTabBar::tab:selected{background:#1B2B2F;color:#4FD1C5;border-bottom:2px solid #4FD1C5;}
            QTabWidget::pane{border:1px solid #233539;border-radius:12px;top:-1px;}""")
        self.tabs.addTab(self._scroll(self._build_tab_risco()),   "🎯 Risco e Demanda")
        self.tabs.addTab(self._scroll(self._build_tab_efic()),    "💎 Eficiência Clínica")
        self.tabs.addTab(self._scroll(self._build_tab_terr()),    "🗺️ Território / Huff")
        self.tabs.addTab(self._scroll(self._build_tab_trans()),   "🔁 Transição de Cuidado")
        self.tabs.addTab(self._scroll(self._build_tab_geral()),   "🧭 Visão Geral")
        self.tabs.addTab(self._scroll(self._build_tab_bench()),   "🏁 Benchmarking Hospitalar")
        self.tabs.addTab(self._scroll(self._build_tab_glosas()),  "💸 Glosas e Não Pagamentos")
        root.addWidget(self.tabs, 1)
        self.tabs.currentChanged.connect(lambda _i: QTimer.singleShot(0, self._resync_plots))

    def _resync_plots(self) -> None:
        """Realinha a cena de cada PlotWidget ao tamanho atual do widget.

        Quando o gráfico é preenchido numa aba oculta (ou o splitter muda de tamanho
        depois), o GraphicsView fica com a cena do tamanho antigo e o gráfico aparece
        cortado à direita. Reaplicar o range do viewport corrige sem redesenhar dados.
        """
        if pg is None:
            return
        try:
            for pw in self.findChildren(pg.PlotWidget):
                charts.relayout(pw)
        except Exception as _e:  # noqa: BLE001
            log.debug("resync_plots: %s", _e)

    def _refresh_scroll_heights(self) -> None:
        """Depois de preencher, o conteúdo de cada aba assume a altura mínima real do layout."""
        try:
            for i in range(self.tabs.count()):
                sa = self.tabs.widget(i)
                inner = sa.widget() if isinstance(sa, QScrollArea) else None
                if inner is None or inner.layout() is None:
                    continue
                inner.layout().activate()
                h = max(560, inner.layout().minimumSize().height())
                inner.setMinimumHeight(h)
        except Exception as _e:  # noqa: BLE001
            log.debug("refresh_scroll_heights: %s", _e)
        self._resync_plots()

    @staticmethod
    def _scroll(inner: QWidget) -> QScrollArea:
        """Área rolável vertical (nunca horizontal) para o conteúdo de uma aba."""
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        sa.setStyleSheet("QScrollArea{background:#0F1A1C;border:none;}"
                         "QScrollArea > QWidget > QWidget{background:#0F1A1C;}")
        sa.viewport().setAutoFillBackground(False)
        inner.setMinimumHeight(max(inner.minimumSizeHint().height(), 560))
        sa.setWidget(inner)
        return sa

    # ------------------------------------------------------------------
    # Abas
    # ------------------------------------------------------------------
    def _build_tab_risco(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(10)
        self.plot_estratos = self._mk_plot_widget("Estratos de risco por hospital (% dos casos)")
        self.plot_cmi = self._mk_plot_widget("Case Mix Index (CMI) por hospital")
        self.tbl_risco = self._mk_table()
        top = DeferredSplitter(Qt.Horizontal, [720, 560])
        top.setChildrenCollapsible(False); top.setHandleWidth(4)
        top.addWidget(self.plot_estratos); top.addWidget(self.plot_cmi)
        spl = DeferredSplitter(Qt.Vertical, [400, 260])
        spl.setChildrenCollapsible(False); spl.setHandleWidth(4)
        spl.addWidget(top)
        spl.addWidget(self._mk_group("Estratificação de risco por hospital", self.tbl_risco))
        l.addWidget(spl, 1)
        return w

    def _build_tab_efic(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(10)
        self.plot_los = self._mk_plot_widget("Permanência observada / esperada (O/E)")
        self.plot_custo = self._mk_plot_widget("Custo médio / benchmark (O/E)")
        self.tbl_efic = self._mk_table()
        top = DeferredSplitter(Qt.Horizontal, [640, 640])
        top.setChildrenCollapsible(False); top.setHandleWidth(4)
        top.addWidget(self.plot_los); top.addWidget(self.plot_custo)
        spl = DeferredSplitter(Qt.Vertical, [400, 260])
        spl.setChildrenCollapsible(False); spl.setHandleWidth(4)
        spl.addWidget(top)
        spl.addWidget(self._mk_group("Eficiência clínica por hospital", self.tbl_efic))
        l.addWidget(spl, 1)
        return w

    def _build_tab_terr(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(12)

        # -----------------------------
        # Barra de filtros do mapa
        # -----------------------------
        filter_bar = QFrame()
        filter_bar.setStyleSheet(
            "QFrame{background:#121C1E;border-radius:10px;border:1px solid #233539;}"
        )
        fb_l = QGridLayout(filter_bar)
        fb_l.setContentsMargins(16, 10, 16, 10)
        fb_l.setHorizontalSpacing(10); fb_l.setVerticalSpacing(8)

        lbl_map_tip = QLabel("🗺️ Mapa de anéis concêntricos — taxa de internação por 1.000 hab. ao redor de cada hospital")
        lbl_map_tip.setStyleSheet("color:#4FD1C5;font-weight:700;")
        lbl_map_tip.setWordWrap(True)
        self.lbl_map_hover = StatusBadge("Selecione um hospital no mapa", status="info")
        fb_l.addWidget(lbl_map_tip, 0, 0, 1, 5)
        fb_l.addWidget(self.lbl_map_hover, 0, 5)

        self.cbo_map_group = QComboBox()
        self.cbo_map_group.addItem("Grupo SIGTAP", "grupo")
        self.cbo_map_group.addItem("Forma de Organização", "forma")
        self.cbo_map_group.addItem("Procedimento (código/nome)", "procedimento")
        self._compact_combo(self.cbo_map_group, 16)
        self.cbo_map_group.currentIndexChanged.connect(self._on_map_group_changed)

        self.cbo_map_filter = QComboBox()
        self.cbo_map_filter.addItem("🔎 Todos (agregado geral)", None)
        self._compact_combo(self.cbo_map_filter, 24)
        self.cbo_map_filter.currentIndexChanged.connect(self._on_map_filter_changed)

        self.cbo_map_radii = QComboBox()
        self.cbo_map_radii.addItem("5 anéis padrão (5,10,20,40,80 km)", (5.0, 10.0, 20.0, 40.0, 80.0))
        self.cbo_map_radii.addItem("Curto alcance (3,6,12,25,50 km)", (3.0, 6.0, 12.0, 25.0, 50.0))
        self.cbo_map_radii.addItem("Longo alcance (10,20,40,80,160 km)", (10.0, 20.0, 40.0, 80.0, 160.0))
        self._compact_combo(self.cbo_map_radii, 18)
        self.cbo_map_radii.currentIndexChanged.connect(self._on_map_radii_changed)

        fb_l.addWidget(QLabel("Agrupar por:"), 1, 0)
        fb_l.addWidget(self.cbo_map_group, 1, 1)
        fb_l.addWidget(QLabel("Filtrar item:"), 1, 2)
        fb_l.addWidget(self.cbo_map_filter, 1, 3)
        fb_l.addWidget(QLabel("Anéis:"), 1, 4)
        fb_l.addWidget(self.cbo_map_radii, 1, 5)
        fb_l.setColumnStretch(3, 2); fb_l.setColumnStretch(1, 1); fb_l.setColumnStretch(5, 1)
        l.addWidget(filter_bar)

        # -----------------------------
        # Container Mapa
        # -----------------------------
        map_container = QFrame()
        map_container.setStyleSheet("QFrame{background:transparent;border:none;}")
        mc_l = QVBoxLayout(map_container)
        mc_l.setContentsMargins(0, 0, 0, 0)
        mc_l.setSpacing(8)

        # Splitter principal: mapa + ICSAP
        spl_top = DeferredSplitter(Qt.Horizontal, [760, 460])
        spl_top.setChildrenCollapsible(False)
        spl_top.setHandleWidth(4)
        spl_top.setMinimumHeight(420)

        # Mapa concêntrico
        self.concentric_map = ConcentricRingsMap()
        self.concentric_map.hospital_clicked.connect(self._on_map_hospital_clicked)
        spl_top.addWidget(self.concentric_map)

        # ICSAP por condição (mantido)
        self.plot_icsap = self._mk_plot_widget("Internações por Condição Sensível à APS (ICSAP)")
        spl_top.addWidget(self.plot_icsap)
        mc_l.addWidget(spl_top, 1)

        l.addWidget(map_container, 2)

        # -----------------------------
        # Tabelas inferiores
        # -----------------------------
        self.tbl_terr = self._mk_table()
        self.tbl_huff = self._mk_table()
        h2 = QHBoxLayout()
        h2.setContentsMargins(0, 0, 0, 0)
        h2.setSpacing(12)
        h2.addWidget(self._mk_group("Unidades por % ICSAP", self.tbl_terr), 1)
        h2.addWidget(self._mk_group("Força de Atração (Huff) por Hospital", self.tbl_huff), 1)
        l.addLayout(h2, 1)

        # -----------------------------
        # NOVO: Curvas de decaimento exponencial + tabela por anel concêntrico
        # -----------------------------
        decay_bar = QFrame()
        decay_bar.setStyleSheet(
            "QFrame{background:#121C1E;border-radius:10px;border:1px solid #233539;}"
        )
        dbl = QGridLayout(decay_bar)
        dbl.setContentsMargins(16, 10, 16, 10); dbl.setHorizontalSpacing(10); dbl.setVerticalSpacing(6)
        dbl.addWidget(QLabel("📉 Curva de decaimento — hospital:"), 0, 0)
        self.cbo_decay_hospital = QComboBox()
        self._compact_combo(self.cbo_decay_hospital, 30)
        self.cbo_decay_hospital.currentIndexChanged.connect(self._on_decay_hospital_changed)
        dbl.addWidget(self.cbo_decay_hospital, 0, 1)
        self.lbl_decay_params = QLabel(
            "Ajuste exponencial: y = A · exp(−λ · km)   λ = ——   R² = ——   DMP (km) = ——")
        self.lbl_decay_params.setStyleSheet("color:#4FD1C5;font-weight:600;")
        self.lbl_decay_params.setWordWrap(True)
        dbl.addWidget(self.lbl_decay_params, 1, 0, 1, 2)
        dbl.setColumnStretch(1, 1)
        l.addWidget(decay_bar)

        self.tbl_distance_decay = self._mk_table()
        self.plot_distance_decay = self._mk_plot_widget(
            "Curva de Decaimento: Taxa Internação / 1000 hab × Distância (km)"
        )
        spl_decay = DeferredSplitter(Qt.Horizontal, [620, 520])
        spl_decay.setChildrenCollapsible(False)
        spl_decay.setHandleWidth(4)
        spl_decay.addWidget(self.plot_distance_decay)
        spl_decay.addWidget(self.tbl_distance_decay)
        l.addWidget(spl_decay, 2)

        return w

    def _build_tab_trans(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(10)
        self.plot_trans = self._mk_plot_widget("% Contato 48h (verde) vs % Readmissão 30d (vermelho)")
        self.tbl_trans = self._mk_table()
        spl = DeferredSplitter(Qt.Vertical, [260, 400])
        spl.setChildrenCollapsible(False)
        spl.setHandleWidth(4)
        spl.addWidget(self.plot_trans); spl.addWidget(self.tbl_trans)
        l.addWidget(spl, 1)
        return w

    def _build_tab_geral(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(10)
        self.plot_ts = self._mk_plot_widget("Série Temporal — Internações (barra) e Taxa ICSAP (linha)")
        self.tbl_alerts = self._mk_table()
        self.tbl_top10_p = self._mk_table()
        self.tbl_top10_e = self._mk_table()
        h = QHBoxLayout()
        h.addWidget(self._mk_group("🚨 Alertas Operacionais", self.tbl_alerts), 1)
        h.addWidget(self._mk_group("🥇 Top-10 Procedimentos", self.tbl_top10_p), 1)
        h.addWidget(self._mk_group("🏆 Top-10 Especialidades/CBO", self.tbl_top10_e), 1)
        spl = DeferredSplitter(Qt.Vertical, [260, 380])
        spl.setChildrenCollapsible(False)
        spl.setHandleWidth(4)
        spl.addWidget(self.plot_ts)
        w2 = QWidget(); w2.setLayout(h); spl.addWidget(w2)
        l.addWidget(spl, 1)
        return w

    def _build_tab_bench(self) -> QWidget:
        """Nova aba: Benchmarking Hospitalar (1 hospital escolhido vs 4 níveis geográficos)."""
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(10)

        # Barra superior de seleção (grade de 2 linhas: cabe em 1366 px)
        sel_bar = QFrame()
        sel_bar.setStyleSheet(
            "QFrame{background:#121C1E;border-radius:10px;border:1px solid #233539;}")
        sbl = QGridLayout(sel_bar)
        sbl.setContentsMargins(16, 10, 16, 10); sbl.setHorizontalSpacing(12); sbl.setVerticalSpacing(8)

        self.cbo_bench_hospital = QComboBox()
        self._compact_combo(self.cbo_bench_hospital, 30)
        self.cbo_bench_hospital.currentIndexChanged.connect(self._on_bench_changed)

        self.cbo_bench_level = QComboBox()
        self.cbo_bench_level.addItem("1. Mesmo Município (ibge 6-7 dígitos)", "nivel_municipio")
        self.cbo_bench_level.addItem("2. Microrregião (5 dígitos IBGE)", "nivel_micro")
        self.cbo_bench_level.addItem("3. Macrorregião (3 dígitos IBGE)", "nivel_macro")
        self.cbo_bench_level.addItem("4. Mesmo Estado (UF — 2 dígitos)", "nivel_uf")
        self.cbo_bench_level.setCurrentIndex(3)   # default: UF (maior amostra)
        self._compact_combo(self.cbo_bench_level, 20)
        self.cbo_bench_level.currentIndexChanged.connect(self._on_bench_changed)

        self.cbo_bench_ind = QComboBox()
        self.cbo_bench_ind.addItem("% ICSAP (evitáveis)", "icsap_pct")
        self.cbo_bench_ind.addItem("LOS médio (dias)", "los_medio_dias")
        self.cbo_bench_ind.addItem("CMI (complexidade)", "cmi")
        self.cbo_bench_ind.addItem("LACE médio (risco readmissão)", "lace_medio")
        self.cbo_bench_ind.addItem("% Readmissão ≤30d (est.)", "readmissao_30d_pct")
        self.cbo_bench_ind.addItem("Custo O/E (eficiência)", "custo_oe")
        self.cbo_bench_ind.addItem("LOS O/E (tempo x benchmark)", "los_o_e")
        self.cbo_bench_ind.addItem("% Casos ALTO risco", "pct_alto")
        self._compact_combo(self.cbo_bench_ind, 20)
        self.cbo_bench_ind.currentIndexChanged.connect(self._on_bench_changed)

        self.lbl_bench_summary = QLabel(
            "Hospital: ——   Nível geográfico: ——   Comparando com —— hospitais.")
        self.lbl_bench_summary.setStyleSheet("color:#4FD1C5;font-weight:700;")
        self.lbl_bench_summary.setWordWrap(True)

        sbl.addWidget(QLabel("🏥 Hospital selecionado:"), 0, 0)
        sbl.addWidget(self.cbo_bench_hospital, 0, 1, 1, 3)
        sbl.addWidget(QLabel("🌐 Nível comparativo:"), 1, 0)
        sbl.addWidget(self.cbo_bench_level, 1, 1)
        sbl.addWidget(QLabel("📈 Indicador:"), 1, 2)
        sbl.addWidget(self.cbo_bench_ind, 1, 3)
        sbl.addWidget(self.lbl_bench_summary, 2, 0, 1, 4)
        sbl.setColumnStretch(1, 1); sbl.setColumnStretch(3, 1)
        l.addWidget(sel_bar)

        # 2 gráficos + tabela
        self.plot_bench_bar = self._mk_plot_widget(
            "Benchmark: Barras ordenadas do indicador selecionado"
        )
        self.plot_bench_scatter = self._mk_plot_widget(
            "Scatter: efeito conjunto (X=% ICSAP, Y=LOS médio dias) — o hospital selecionado é a cor VERDE"
        )
        self.tbl_bench = self._mk_table()
        spl_top = DeferredSplitter(Qt.Horizontal, [600, 600])
        spl_top.setChildrenCollapsible(False)
        spl_top.setHandleWidth(4)
        spl_top.addWidget(self.plot_bench_bar)
        spl_top.addWidget(self.plot_bench_scatter)
        spl_full = DeferredSplitter(Qt.Vertical, [300, 420])
        spl_full.setChildrenCollapsible(False)
        spl_full.setHandleWidth(4)
        spl_full.addWidget(spl_top)
        spl_full.addWidget(self.tbl_bench)
        l.addWidget(spl_full, 1)

        return w

    def _build_tab_glosas(self) -> QWidget:
        """Nova aba: Glosas e Não Pagamentos (causas + série histórica)."""
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(10)

        # Header com KPIs rápidos de glosa — tamanhos mínimos p/ não sobrepor
        kpi_row = QHBoxLayout(); kpi_row.setSpacing(8)
        self.kpi_glosas_n = KpiCard("AIHs/APACs glosadas", 0, icon="💸", accent=_DANG, format="{:,}")
        self.kpi_glosas_pct = KpiCard("% de internações glosadas", 0.0, icon="⚠️", accent=_WARN, format="{:.2f} %")
        self.kpi_glosas_val = KpiCard("Valor total glosado (R$)", 0.0, icon="💰", accent="#F97316", format="R$ {:,.2f}")
        self.kpi_glosas_top = KpiCard("1ª causa de glosa", "—", icon="🔍", accent=_PRI, format="{}")
        for k in (self.kpi_glosas_n, self.kpi_glosas_pct, self.kpi_glosas_val, self.kpi_glosas_top):
            k.setMinimumHeight(90)
            k.setMaximumHeight(120)
            kpi_row.addWidget(k)
        l.addLayout(kpi_row)

        # Gráficos lado a lado (top)
        self.plot_glosas_causas = self._mk_plot_widget(
            "Distribuição de causas de glosa (Top 15 mais frequentes)"
        )
        self.plot_glosas_serie = self._mk_plot_widget(
            "Série histórica: barras = Nº glosadas | linha = % valor glosado ($)"
        )
        spl_plots = DeferredSplitter(Qt.Horizontal, [620, 660])
        spl_plots.setChildrenCollapsible(False)
        spl_plots.setHandleWidth(4)
        spl_plots.addWidget(self.plot_glosas_causas); spl_plots.addWidget(self.plot_glosas_serie)
        spl_plots.setStretchFactor(0, 1); spl_plots.setStretchFactor(1, 1)
        spl_plots.setCollapsible(0, False); spl_plots.setCollapsible(1, False)
        spl_plots.setMinimumHeight(280)

        # Tabelas lado a lado (inferior)
        self.tbl_glosas_causas = self._mk_table()
        self.tbl_glosas_serie = self._mk_table()
        grp_causas = self._mk_group("📋 Causas de glosa — ocorrências e R$", self.tbl_glosas_causas)
        grp_serie = self._mk_group("📅 Série histórica de glosas", self.tbl_glosas_serie)
        spl_tabs = DeferredSplitter(Qt.Horizontal, [620, 660])
        spl_tabs.setChildrenCollapsible(False)
        spl_tabs.setHandleWidth(4)
        spl_tabs.addWidget(grp_causas); spl_tabs.addWidget(grp_serie)
        spl_tabs.setStretchFactor(0, 1); spl_tabs.setStretchFactor(1, 1)
        spl_tabs.setCollapsible(0, False); spl_tabs.setCollapsible(1, False)
        spl_tabs.setMinimumHeight(260)

        spl_full = DeferredSplitter(Qt.Vertical, [330, 380])
        spl_full.setChildrenCollapsible(False)
        spl_full.setHandleWidth(4)
        spl_full.addWidget(spl_plots); spl_full.addWidget(spl_tabs)
        spl_full.setCollapsible(0, False); spl_full.setCollapsible(1, False)
        l.addWidget(spl_full, 1)

        return w

    # ------------------------------------------------------------------
    # Helpers de UI
    # ------------------------------------------------------------------
    def _mk_plot_widget(self, title: str) -> "PlotWidget | QLabel":
        if pg is None:  # pragma: no cover
            ql = QLabel("pyqtgraph não instalado — instale com: pip install pyqtgraph")
            ql.setAlignment(Qt.AlignCenter); ql.setFrameShape(QFrame.StyledPanel)
            return ql
        plt: PlotWidget = pg.PlotWidget()
        charts.style_plot(plt, title)
        plt.setMinimumHeight(220)
        return plt

    def _mk_table(self) -> QTableWidget:
        t = QTableWidget(0, 0)
        t.setStyleSheet("""
            QTableWidget{background:#121C1E;alternate-background-color:#172426;color:#E6FFFA;
                         gridline-color:#233539;font-size:12px;border-radius:10px;}
            QTableWidget::item{padding:4px 6px;}
            QHeaderView::section{background:#1B2B2F;color:#4FD1C5;padding:8px;border:none;
                                 border-right:1px solid #233539;border-bottom:2px solid #14B8A6;
                                 font-weight:600;}""")
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
        t.setMinimumHeight(160)
        t.setAlternatingRowColors(True)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        return t

    def _mk_group(self, title: str, inner: QWidget) -> QGroupBox:
        gb = QGroupBox(title)
        gb.setStyleSheet("""
            QGroupBox{border:1px solid #233539;border-radius:10px;margin-top:16px;
                      color:#4FD1C5;font-weight:600;padding:8px;}
            QGroupBox::title{subcontrol-origin:margin;left:12px;padding:0 6px 0 6px;}""")
        lv = QVBoxLayout(gb); lv.setContentsMargins(16, 18, 16, 16)
        lv.addWidget(inner)
        return gb

    # ------------------------------------------------------------------
    # População (pública API)
    # ------------------------------------------------------------------
    def set_dataset(self, df: pl.DataFrame, *, system: str = "SIH_RD",
                    cnes: list[str] | None = None,
                    compute_huff: bool | None = None) -> None:
        """Dispara o cálculo do dashboard a partir do DataFrame final do pipeline.

        compute_huff: se None, usa o valor do checkbox da UI (chk_huff).
                      Força True/False para sobrescrever em chamadas programáticas.
        """
        self._last_df = df
        self._last_system = system
        self._last_cnes = cnes
        # Força fonte para a sessão atual (index 0) para que o novo dataset seja processado
        self.cbo_fonte_dados.blockSignals(True)
        self.cbo_fonte_dados.setCurrentIndex(0)
        self.cbo_fonte_dados.blockSignals(False)
        self._fonte_atual = "session"
        self._fonte_dados_camada = "SESSAO_PIPELINE"
        self.cbo_load_runs.setEnabled(False)

        # Ajusta combo de sistema
        for i in range(self.cbo_system.count()):
            if self.cbo_system.itemData(i) == system:
                self.cbo_system.setCurrentIndex(i); break
        # Atualiza info da fonte (para garantir que ao trocar para sessão, mostra linhas novas)
        self._update_fonte_info_label()
        try:
            self._refresh_load_runs()
        except Exception:
            pass
        self._on_calculate(compute_huff=compute_huff)

    # ------------------------------------------------------------------
    # Fonte dos Dados (Sessão vs DuckDB persistido)
    # ------------------------------------------------------------------
    def _current_active_df(self) -> pl.DataFrame | None:
        """Retorna o df ativo, conforme seleção no combo fonte de dados."""
        if self._fonte_atual == "duckdb":
            return self._df_duckdb
        return self._last_df

    def _update_fonte_info_label(self) -> None:
        df = self._current_active_df()
        n = df.height if df is not None and not df.is_empty() else 0
        if self._fonte_atual == "duckdb":
            extra = ""
            if df is not None and not df.is_empty() and "CNES_EXEC" in df.columns:
                hosp = df["CNES_EXEC"].n_unique()
                extra = f" | {hosp} hospitais"
            self.lbl_fonte_info.setText(f"Fonte: DuckDB persistido | {n:,} linhas{extra}")
        else:
            extra = ""
            if self._last_df is not None and not self._last_df.is_empty():
                cols = [c for c in ("CNES_EXEC", "cnes", "CO_CNES") if c in self._last_df.columns]
                if cols:
                    hosp = self._last_df[cols[0]].n_unique()
                    extra = f" | {hosp} hospitais"
            self.lbl_fonte_info.setText(f"Fonte: sessão atual (pipeline) | {n:,} linhas{extra}")
        try:
            self._update_badge_fonte_camada()
        except Exception:
            pass

    def _refresh_load_runs(self) -> None:
        """Popula combo_cargas com as últimas runs do bronze.load_runs."""
        self.cbo_load_runs.blockSignals(True)
        self.cbo_load_runs.clear()
        self.cbo_load_runs.addItem("⏳ Todas as cargas (todas as runs de ETL)", None)
        try:
            db = get_db()
            runs_df = db.list_load_runs(limit=30)
            if runs_df.height > 0:
                runs_df = runs_df.with_columns(pl.all().cast(pl.Utf8, strict=False))
                for r in runs_df.iter_rows(named=True):
                    rid = str(r.get("load_run_id") or "")
                    sys = str(r.get("information_system") or "—")
                    status = str(r.get("status") or "—")
                    n_rows = int(r.get("rows_silver") or 0)
                    dur = int(r.get("duration_ms") or 0)
                    created = str(r.get("ts_utc") or "")[:16].replace("T", " ")
                    label = f"{created}  ·  {sys:<6s}  ·  {status:<6s}  ·  {n_rows:,} linhas  ·  {dur}ms"
                    if len(rid) > 10:
                        label += f"  ·  id {rid[:10]}…"
                    self.cbo_load_runs.addItem(label, rid)
            has_data = db.has_persisted_data()
            if has_data:
                item = self.cbo_fonte_dados.model().item(1)
                if item is not None:
                    item.setText("💾 DuckDB — Dados Persistidos (há dados salvos!)")
        except Exception as e:  # pragma: no cover
            log.warning("Falha ao popular combo load_runs: %s", e)
        self.cbo_load_runs.blockSignals(False)

    def _on_fonte_dados_changed(self, _idx: int = 0) -> None:
        self._fonte_atual = self.cbo_fonte_dados.currentData() or "session"
        is_duck = (self._fonte_atual == "duckdb")
        self.cbo_load_runs.setEnabled(is_duck)
        if is_duck:
            if self._df_duckdb is None or self._df_duckdb.is_empty():
                self._load_duckdb_into_df()
            if self._df_duckdb is not None and not self._df_duckdb.is_empty():
                self._on_calculate()
        else:
            if self._last_df is not None and not self._last_df.is_empty():
                self._on_calculate()
        self._update_fonte_info_label()

    def _on_reload_fonte_clicked(self) -> None:
        if self._fonte_atual == "duckdb":
            self._load_duckdb_into_df()
            if self._df_duckdb is not None and not self._df_duckdb.is_empty():
                self._on_calculate()
        else:
            if self._last_df is None or self._last_df.is_empty():
                try:
                    if get_db().has_persisted_data():
                        self.cbo_fonte_dados.setCurrentIndex(1)  # pula para DuckDB automaticamente
                        return
                except Exception:
                    pass
                self.lbl_status.set_status("Sem dados na sessão — rode um pipeline primeiro.", "warn")
                return
            self._on_calculate()

    def _load_duckdb_into_df(self) -> None:
        """Carrega silver.producao para self._df_duckdb usando o filtro de load_run selecionado."""
        try:
            self.lbl_status.set_status("Carregando dados persistidos do DuckDB...", "info")
            QApplication.processEvents()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            db = get_db()
            load_run_id: str | None = self.cbo_load_runs.currentData()
            # Fallback Medallion (P3-R4): GOLD otimizado se aggr_mensal_hospital populado,
            # senão SILVER_FALLBACK transparente (silver.producao como detalhe).
            gold_ok = self._gold_aggr_has_rows()
            self._fonte_dados_camada = "GOLD_OTIMIZADO" if gold_ok else "SILVER_FALLBACK"
            if not gold_ok:
                log.warning(
                    "[Fallback-P3R4] gold.aggr_mensal_hospital VAZIO ou inexistente. "
                    "Fallback automático → silver.producao (transparente para usuário)."
                )
            if load_run_id:
                # Carregamento filtrado por load_run_id
                params: list = [load_run_id]
                cols_sql = (
                    "id_producao, load_run_id, origem_sistema, origem_arquivo, "
                    "uf, municipio_cod, municipio_nome, cnes, no_fantasia, no_razao_social, "
                    "uf_gestora, co_macrorregiao, no_macrorregiao, co_regiao_saude, no_regiao_saude, "
                    "competencia_aaaa, competencia_mm, dt_internacao, dt_alta, dias_permanencia, "
                    "cid_principal, cid_secundaria, procedimento, no_procedimento, especialidade, "
                    "sexo, idade_anos, raca_cor, vl_total_apresentado, vl_total_aprovado, "
                    "vl_glosa_total, qt_glosas, codigo_glosa_1, codigo_glosa_2, codigo_glosa_3, "
                    "codigo_glosa_4, codigo_glosa_5, flg_glosado, created_at, "
                    'uf AS "UF", cnes AS "CNES_EXEC", no_fantasia AS "NOME_HOSPITAL", '
                    'no_razao_social AS "RAZAO_SOCIAL", municipio_cod AS "COD_MUNICIPIO_IBGE", '
                    'municipio_nome AS "MUNICIPIO", co_regiao_saude AS "CO_DRS", '
                    'no_regiao_saude AS "NOME_DRS", co_macrorregiao AS "CO_MACRO_SAUDE", '
                    'no_macrorregiao AS "NOME_MACRO_SAUDE", '
                    'LPAD(CAST(competencia_aaaa AS VARCHAR),4,\'0\')'
                    '  || LPAD(CAST(competencia_mm   AS VARCHAR),2,\'0\') AS "COMPETEN", '
                    'competencia_aaaa AS "ANO", competencia_mm AS "MES", '
                    'dt_internacao AS "DT_INTER", dt_alta AS "DT_ALTA", '
                    'dias_permanencia AS "QTD_DIARIAS", cid_principal AS "CID10", '
                    'procedimento AS "PROC_REA", no_procedimento AS "NOME_PROCEDIMENTO", '
                    'especialidade AS "ESPECIALIDADE", sexo AS "SEXO", idade_anos AS "IDADE", '
                    'vl_total_apresentado AS "VAL_APRES", vl_total_aprovado AS "VAL_PAGO", '
                    'vl_glosa_total AS "VAL_GLOSA"'
                )
                sql = (f"SELECT {cols_sql} FROM silver.producao WHERE load_run_id = ? "
                       f"ORDER BY competencia_aaaa DESC, competencia_mm DESC, cnes")
                self._df_duckdb = db.sql_to_polars(sql, params)
            else:
                self._df_duckdb = db.load_silver_production()
            if self._df_duckdb is not None and not self._df_duckdb.is_empty():
                self._df_duckdb = self._df_duckdb.with_columns(
                    pl.lit(self._fonte_dados_camada).alias("_fonte_dados")
                )
            self._last_system = "SIA_PA"
            self._last_cnes = None
            self._update_fonte_info_label()
            if gold_ok:
                self.lbl_status.set_status(
                    f"DuckDB [GOLD⚡]: {self._df_duckdb.height:,} linhas ✓",
                    "ok",
                )
            else:
                self.lbl_status.set_status(
                    f"DuckDB [SILVER↩ fallback]: {self._df_duckdb.height:,} linhas (gold vazio)",
                    "warn",
                )
        except Exception as e:
            self._df_duckdb = None
            self._fonte_dados_camada = ""
            log.error("Falha ao carregar DuckDB: %s", e)
            self.lbl_status.set_status(f"Erro DuckDB: {type(e).__name__}", "error")
            try:
                self._update_badge_fonte_camada()
            except Exception:
                pass
            QMessageBox.warning(
                self, "Sem dados no DuckDB",
                f"Não foi possível carregar dados persistidos:\n\n{type(e).__name__}: {str(e)[:300]}\n\n"
                "Execute um pipeline (Wizard) primeiro para gravar dados no DuckDB."
            )

    def _gold_aggr_has_rows(self) -> bool:
        """Retorna True se gold.aggr_mensal_hospital existir E tiver > 0 linhas."""
        try:
            db = get_db()
            r = db.sql_to_polars(
                "SELECT COUNT(*) AS n FROM information_schema.tables "
                "WHERE table_schema = 'gold' AND table_name = 'aggr_mensal_hospital'"
            )
            if r.is_empty() or int(r[0, "n"]) == 0:
                return False
            rows = db.sql_to_polars("SELECT COUNT(*) AS n FROM gold.aggr_mensal_hospital")
            return (not rows.is_empty()) and int(rows[0, "n"]) > 0
        except Exception:
            return False

    def _update_badge_fonte_camada(self) -> None:
        badge = getattr(self, "_badge_fonte_camada", None)
        if badge is None:
            return
        fonte = self._fonte_dados_camada or ""
        if fonte == "GOLD_OTIMIZADO":
            badge.set_status("Camada: GOLD ⚡ Otimizado", "ok")
        elif fonte == "SILVER_FALLBACK":
            badge.set_status("Camada: SILVER ↩ Fallback", "warn")
        elif fonte == "SESSAO_PIPELINE":
            badge.set_status("Camada: Sessão Pipeline", "info")
        else:
            badge.set_status("Camada: —", "info")

    def try_auto_persisted_fallback(self) -> bool:
        """Chamado após login: se sem sessão E há DuckDB com dados → carrega automaticamente.

        Retorna True se carregou algo.
        """
        try:
            db = get_db()
            sessao_vazia = (self._last_df is None or self._last_df.is_empty())
            duck_ok = db.has_persisted_data()
            if sessao_vazia and duck_ok:
                self.cbo_fonte_dados.blockSignals(True)
                self.cbo_fonte_dados.setCurrentIndex(1)  # DuckDB
                self.cbo_fonte_dados.blockSignals(False)
                self._fonte_atual = "duckdb"
                self.cbo_load_runs.setEnabled(True)
                self._refresh_load_runs()
                self._load_duckdb_into_df()
                if self._df_duckdb is not None and not self._df_duckdb.is_empty():
                    self._on_calculate()
                    return True
        except Exception as e:
            log.warning("Fallback DuckDB não rodou: %s", e)
        return False

    # ------------------------------------------------------------------
    def _on_calculate(self, *, compute_huff: bool | None = None) -> None:
        df = self._current_active_df()
        if df is None or df.is_empty():
            self.lbl_status.set_status(
                "Nenhum dado — execute pipeline ou selecione 'DuckDB persistido' na barra Fonte.",
                "warn",
            )
            # Sugere fallback
            try:
                db = get_db()
                if db.has_persisted_data() and self._fonte_atual == "session":
                    msg = (
                        "Nenhum dado na sessão atual, mas há dados PERSISTIDOS no DuckDB "
                        "(de execuções anteriores). Deseja carregar os dados do DuckDB agora?"
                    )
                    r = QMessageBox.question(self, "Carregar DuckDB?", msg,
                                             QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                    if r == QMessageBox.Yes:
                        self.cbo_fonte_dados.setCurrentIndex(1)
            except Exception:
                pass
            return
        system_cur = self.cbo_system.currentData()
        system = system_cur if (system_cur is not None and system_cur != "") else self._last_system
        beta_cur = self.cbo_beta.currentData()
        beta = beta_cur if (beta_cur is not None and beta_cur != "") else None
        use_huff = self.chk_huff.isChecked() if compute_huff is None else bool(compute_huff)
        self.btn_calc.setEnabled(False)
        self.lbl_status.set_status("Processando indicadores...", "info")
        cnes_filter = (self._last_cnes if self._fonte_atual == "session" else None)
        self._worker = DashboardWorker(df, cnes_filter,
                                       system=system,
                                       beta=beta,
                                       compute_huff=use_huff)
        self._worker.progress.connect(lambda s: self.lbl_status.set_status(s, "info"))
        self._worker.failed.connect(self._on_fail)
        self._worker.finished_ok.connect(self._on_ready)
        self._worker.start()

    def _on_fail(self, msg: str) -> None:  # pragma: no cover
        self.btn_calc.setEnabled(True)
        self.lbl_status.set_status(f"Erro: {msg[:80]}", "error")
        QMessageBox.critical(self, "Dashboard falhou", msg)

    def _on_ready(self, m: DashboardMetrics) -> None:
        self.btn_calc.setEnabled(True)
        self.btn_export.setEnabled(True)
        self._metrics = m
        tabs = [
            ("KPIs Cards", self._fill_kpis),
            ("Estratificação de Risco", self._fill_tab_risco),
            ("Eficiência Clínica", self._fill_tab_efic),
            ("Integração Territorial & Anéis", self._fill_tab_terr),
            ("Transição de Cuidados", self._fill_tab_trans),
            ("Visão Geral do Estado", self._fill_tab_geral),
            ("Benchmarking Hospitalar", self._fill_tab_bench),
            ("Auditoria de Glosas", self._fill_tab_glosas),
        ]
        for name, fn in tabs:
            try:
                fn(m)
            except Exception as exc:
                print(f"[!!!] EXCECAO {name}: {type(exc).__name__}: {exc}", flush=True)
                traceback.print_exc()
                log.error("Erro ao popular %s no Dashboard: %s", name, exc, exc_info=True)
        self.lbl_status.set_status("Dashboard atualizado com sucesso ✓", "ok")
        self._refresh_scroll_heights()
        QTimer.singleShot(0, self._refresh_scroll_heights)

    # ------------------------------------------------------------------
    def _fill_kpis(self, m: DashboardMetrics) -> None:
        try:
            k = m.kpis
            self.kpi_estabs.set_value(int(k.get("n_estabelecimentos", 0)))
            self.kpi_prod.set_value(int(k.get("n_aihs_ou_apacs", 0)))
            self.kpi_valor.set_value(float(k.get("valor_total_R$", 0.0)))
            self.kpi_icsap.set_value(float(k.get("icsap_pct", 0.0)))
            self.kpi_los.set_value(float(k.get("dias_permanencia_total", 0.0))
                                    / max(1, int(k.get("n_aihs_ou_apacs", 1)))
                                    if k.get("n_aihs_ou_apacs", 0) > 0 else 0.0)
            self.kpi_readm.set_value(float(k.get("readmissao_30d_est_pct", 0.0)))
        except Exception as _e:
            print(f"[!!!] EXCECAO _fill_kpis: {type(_e).__name__}: {_e}", flush=True)
            traceback.print_exc()

    # ------------------------------------------------------------------
    _nome_por_cnes: dict[str, str] = {}

    def _nomes_hospitais(self, m: DashboardMetrics) -> dict[str, str]:
        """CNES → nome fantasia (do CNES) ou nome do benchmarking. Para rótulos de eixo."""
        nomes: dict[str, str] = {}
        try:
            g = m.hospitais_geo
            if g is not None and not g.is_empty() and "CO_CNES" in g.columns:
                ncol = next((c for c in ("NO_FANTASIA", "NO_RAZAO_SOCIAL") if c in g.columns), None)
                if ncol:
                    for c, nm in zip(g["CO_CNES"].cast(pl.Utf8).to_list(), g[ncol].cast(pl.Utf8).to_list()):
                        if c and nm:
                            nomes[str(c).strip().zfill(7)] = str(nm).strip()
        except Exception:  # noqa: BLE001
            pass
        try:
            b = m.benchmarking
            if b is not None and not b.is_empty() and "hospital_nome" in b.columns:
                for c, nm in zip(b["hospital_cnes"].cast(pl.Utf8).to_list(), b["hospital_nome"].cast(pl.Utf8).to_list()):
                    if c and nm:
                        nomes.setdefault(str(c).strip().zfill(7), str(nm).strip())
        except Exception:  # noqa: BLE001
            pass
        self._nome_por_cnes = nomes
        return nomes

    def _rotulo_hosp(self, cnes: object, n: int = 30) -> str:
        c = str(cnes or "").strip().zfill(7)
        nm = self._nome_por_cnes.get(c)
        return charts.short(nm, n) if nm else f"CNES {c}"

    def _fill_tab_risco(self, m: DashboardMetrics) -> None:
        try:
            self._nomes_hospitais(m)
            df = m.estratificacao_risco
            if pg is not None:
                if df is None or df.is_empty():
                    charts.empty_message(self.plot_estratos, "Sem dados de estratificação de risco")
                else:
                    d = df
                    for c in ("pct_ALTO", "pct_MEDIO", "pct_BAIXO"):
                        if c not in d.columns:
                            d = d.with_columns(pl.lit(0.0).alias(c))
                    d = d.with_columns([
                        pl.col(c).cast(pl.Float64, strict=False).fill_null(0.0).fill_nan(0.0)
                        for c in ("pct_ALTO", "pct_MEDIO", "pct_BAIXO")
                    ])
                    ma = float(d["pct_ALTO"].mean() or 0.0)
                    mm = float(d["pct_MEDIO"].mean() or 0.0)
                    mb = float(d["pct_BAIXO"].mean() or 0.0)
                    d = d.sort(["pct_ALTO", "pct_MEDIO"], descending=[True, True]).head(30)
                    cnes_col = next((c for c in ("_cnes", "cnes", "CNES") if c in d.columns), d.columns[0])
                    labels = [self._rotulo_hosp(c) for c in d[cnes_col].to_list()]
                    titulo = f"Estratos de risco por hospital (média: alto {ma:.0f}% · médio {mm:.0f}% · baixo {mb:.0f}%)"
                    charts.stacked_bars_h(
                        self.plot_estratos, labels,
                        [("Alto", d["pct_ALTO"].to_list(), charts.DANG),
                         ("Médio", d["pct_MEDIO"].to_list(), charts.WARN),
                         ("Baixo", d["pct_BAIXO"].to_list(), charts.OK)],
                        title=titulo,
                    )
            ef = m.eficiencia_clinica
            if pg is not None:
                if ef is None or ef.is_empty() or "case_mix_index" not in ef.columns:
                    charts.empty_message(self.plot_cmi, "Case Mix Index indisponível")
                else:
                    cmi = ef["case_mix_index"].cast(pl.Float64, strict=False).fill_null(0.0).fill_nan(0.0).to_list()
                    ccol = "cnes" if "cnes" in ef.columns else ef.columns[0]
                    if not cmi or max(cmi) <= 0:
                        # Sem pesos DRG o CMI é 0 para todos: mostra o volume por hospital no lugar
                        ncol = "n_episodios" if "n_episodios" in ef.columns else None
                        if ncol:
                            vol = ef[ncol].cast(pl.Float64, strict=False).fill_null(0.0).to_list()
                            labs_v = [self._rotulo_hosp(c) for c in ef[ccol].to_list()]
                            mxv = max(vol) if vol and max(vol) > 0 else 1.0
                            charts.bar_categorical(
                                self.plot_cmi, labs_v, vol,
                                colors=[charts.lerp(charts.PRI_DARK, charts.PRI, v / mxv) for v in vol],
                                title="Internações por hospital (CMI indisponível: pesos DRG não integrados)",
                                value_fmt="{:,.0f}", sort_desc=True, horizontal=True,
                                axis_label="nº de internações no período",
                            )
                        else:
                            charts.empty_message(self.plot_cmi,
                                                 "Case Mix Index indisponível: tabela de pesos DRG ainda não integrada")
                        cmi = []
                    labels = [self._rotulo_hosp(c) for c in ef[ccol].to_list()] if cmi else []
                    avg = (sum(cmi) / len(cmi)) if cmi else 0.0
                    mx = max(cmi) if cmi and max(cmi) > 0 else 1.0
                    colors = [charts.lerp("#818CF8", "#A855F7", v / mx) for v in cmi]
                    if cmi:
                      charts.bar_categorical(
                        self.plot_cmi, labels, cmi, colors=colors,
                        title=f"Case Mix Index por hospital — média {avg:.2f}",
                        value_fmt="{:.2f}", ref=avg, ref_label=f"média {avg:.2f}",
                        sort_desc=True, horizontal=True,
                        axis_label="CMI (1,0 = complexidade média do conjunto)",
                      )
            self._plfill(self.tbl_risco, df)
        except Exception as _e:
            print(f"[!!!] EXCECAO _fill_tab_risco: {type(_e).__name__}: {_e}", flush=True)
            traceback.print_exc()

    # ------------------------------------------------------------------
    def _fill_tab_efic(self, m: DashboardMetrics) -> None:
        try:
            self._nomes_hospitais(m)
            df = m.eficiencia_clinica
            ccol = "cnes" if (df is not None and "cnes" in df.columns) else None
            for plot, col, titulo, eixo in (
                (self.plot_los, "los_o_e", "Permanência observada / esperada (O/E) por hospital",
                 "O/E · esperado = benchmark da linha de cuidado ou média do conjunto (>1 = permanece mais)"),
                (self.plot_custo, "custo_o_e", "Custo médio / esperado (O/E) por hospital",
                 "O/E · esperado = benchmark da linha de cuidado ou média do conjunto (>1 = custa mais)"),
            ):
                if pg is None:
                    continue
                if df is None or df.is_empty() or col not in df.columns or ccol is None:
                    charts.empty_message(plot, f"{titulo}: indisponível")
                    continue
                vals = df[col].cast(pl.Float64, strict=False).fill_null(0.0).fill_nan(0.0).to_list()
                labels = [self._rotulo_hosp(c) for c in df[ccol].to_list()]
                n_out = sum(1 for v in vals if v > 5.0)
                if n_out:
                    eixo += f"  ·  eixo cortado em 5× ({n_out} hospitais acima, valor real no rótulo ›)"
                charts.bar_categorical(
                    plot, labels, vals, colors=[charts.color_for_ratio(v) for v in vals],
                    title=titulo, value_fmt="{:.2f}", ref=1.0, ref_label="benchmark 1,0",
                    sort_desc=True, horizontal=True, axis_label=eixo, clip_at=5.0,
                )
            self._plfill(self.tbl_efic, df)
        except Exception as _e:
            print(f"[!!!] EXCECAO _fill_tab_efic: {type(_e).__name__}: {_e}", flush=True)
            traceback.print_exc()

    # ------------------------------------------------------------------
    def _fill_tab_terr(self, m: DashboardMetrics) -> None:
        try:
            self._nomes_hospitais(m)
            # ICSAP por condição (barras horizontais, maiores primeiro)
            if pg is not None:
                ic = m.icsap_por_condicao
                if ic is None or ic.is_empty() or "condicao_evitavel" not in ic.columns:
                    charts.empty_message(self.plot_icsap, "Sem internações por condição sensível (ICSAP)")
                else:
                    conds = ic["condicao_evitavel"].cast(pl.Utf8).to_list()
                    ns = ic["n_internacoes"].cast(pl.Float64, strict=False).fill_null(0.0).to_list()
                    mx = max(ns) if ns and max(ns) > 0 else 1.0
                    cores = [charts.color_for_pct(60.0 * v / mx) for v in ns]
                    total = sum(ns)
                    charts.bar_categorical(
                        self.plot_icsap, conds, ns, colors=cores,
                        title=f"ICSAP por condição (total {int(total):,})".replace(",", "."),
                        value_fmt="{:,.0f}", sort_desc=True, horizontal=True, max_items=15,
                        axis_label="nº de internações",
                    )
            # Tabelas
            self._plfill(self.tbl_terr, m.integracao_territorial)
            if m.huff and not m.huff.hospital_attraction.is_empty():
                self._plfill(self.tbl_huff, m.huff.hospital_attraction)
            else:
                self._plfill(self.tbl_huff, pl.DataFrame())

            # =====================================================
            # Mapa concêntrico: SOMENTE os hospitais presentes no dataset
            # (antes, sem seleção, entravam os 598 mil estabelecimentos do CNES
            #  e o mapa nunca terminava de desenhar)
            # =====================================================
            try:
                from papahr.dashboard.cnes_loader import get_cnes_data
                cnes_data = get_cnes_data().load()
                muni_df = cnes_data.municipios
                self._muni_df = muni_df
            except Exception as exc:  # noqa: BLE001
                log.warning("Não foi possível carregar CNES para o mapa: %s", exc)
                muni_df = pl.DataFrame()

            points: list[HospitalMapPoint] = []
            geo = m.hospitais_geo
            alvo = {str(c).strip().zfill(7) for c in (self._last_cnes or []) if str(c).strip()}
            volume: dict[str, float] = {}
            try:
                dfp = self._current_active_df()
                if dfp is not None and not dfp.is_empty() and "CNES" in dfp.columns:
                    g = dfp.group_by("CNES").len()
                    volume = {str(k).strip().zfill(7): float(v)
                              for k, v in zip(g["CNES"].to_list(), g["len"].to_list()) if k is not None}
            except Exception:  # noqa: BLE001
                volume = {}
            if geo is not None and not geo.is_empty() and {"CO_CNES", "NU_LATITUDE", "NU_LONGITUDE"} <= set(geo.columns):
                ncol = next((c for c in ("NO_FANTASIA", "NO_RAZAO_SOCIAL") if c in geo.columns), None)
                for row in geo.to_dicts():
                    cnes = str(row.get("CO_CNES") or "").strip().zfill(7)
                    if alvo and cnes not in alvo:
                        continue
                    try:
                        lat = float(row.get("NU_LATITUDE")); lon = float(row.get("NU_LONGITUDE"))
                    except (TypeError, ValueError):
                        continue
                    if not (math.isfinite(lat) and math.isfinite(lon)) or lat == 0.0 or lon == 0.0:
                        continue
                    nome = str(row.get(ncol) or cnes) if ncol else cnes
                    points.append(HospitalMapPoint(cnes=cnes, nome=nome, latitude=lat, longitude=lon,
                                                   leitos=volume.get(cnes, 50.0)))
            points.sort(key=lambda p: -p.leitos)
            points = points[:40]
            try:
                self.concentric_map.set_data(
                    points,
                    muni_df if (muni_df is not None and not muni_df.is_empty()) else pl.DataFrame(),
                    self._current_active_df() if self._current_active_df() is not None else pl.DataFrame(),
                )
                self.lbl_map_hover.set_status(
                    f"{len(points)} hospital(is) no mapa — clique num pino para selecionar", "info")
            except Exception as _exc_map:  # noqa: BLE001
                log.warning("Falha ao renderizar mapa concêntrico: %s", _exc_map)
                self.lbl_map_hover.set_status("Mapa indisponível (ver log)", "warn")

            # Popula combos de filtro
            self._rebuild_map_group_options()

            # =====================================================
            # Curvas de decaimento exponencial: popula combo hospital
            # =====================================================
            decay_df = m.distance_decay
            self.cbo_decay_hospital.blockSignals(True)
            self.cbo_decay_hospital.clear()
            if decay_df is not None and not decay_df.is_empty():
                vistos: set[str] = set()
                for c in decay_df["hospital_cnes"].cast(pl.Utf8).to_list():
                    if c in vistos:
                        continue
                    vistos.add(c)
                    self.cbo_decay_hospital.addItem(f"{self._rotulo_hosp(c, 48)}  ({c})", c)
            self.cbo_decay_hospital.blockSignals(False)
            if self.cbo_decay_hospital.count() > 0:
                self.cbo_decay_hospital.setCurrentIndex(0)
                self._on_decay_hospital_changed(0)
            else:
                self._on_decay_hospital_changed(0)
        except Exception as _e:
            print(f"[!!!] EXCECAO _fill_tab_terr: {type(_e).__name__}: {_e}", flush=True)
            traceback.print_exc()

    # ------------------------------------------------------------------
    def _fill_tab_trans(self, m: DashboardMetrics) -> None:
        try:
            df = m.transicao_cuidado
            if pg is not None:
                if df is None or df.is_empty() or "linha_cuidado" not in df.columns:
                    charts.empty_message(self.plot_trans, "Sem dados de transição de cuidado "
                                                          "(requer datas de internação e saída)")
                else:
                    linhas = df["linha_cuidado"].cast(pl.Utf8).to_list()
                    contato = (df["contato_48h_pct"].cast(pl.Float64, strict=False).fill_null(0.0).to_list()
                               if "contato_48h_pct" in df.columns else [0.0] * len(linhas))
                    readm = (df["readmissao_30d_pct_est"].cast(pl.Float64, strict=False).fill_null(0.0).to_list()
                             if "readmissao_30d_pct_est" in df.columns else [0.0] * len(linhas))
                    if max(contato + readm + [0.0]) > 0:
                        charts.grouped_bars(
                            self.plot_trans, linhas,
                            [("Contato em 48h após alta (%)", contato, charts.OK),
                             ("Readmissão em 30 dias (%)", readm, charts.DANG)],
                            title="Transição de cuidado por linha de cuidado",
                            value_fmt="{:.1f}", unit="%", axis_label="% dos episódios",
                        )
                    else:
                        # SIH-RD não identifica o paciente: readmissão/contato pós-alta não são
                        # mensuráveis. Mostra o que é real: o volume de altas por linha de cuidado.
                        altas = (df["n_altas"].cast(pl.Float64, strict=False).fill_null(0.0).to_list()
                                 if "n_altas" in df.columns else [0.0] * len(linhas))
                        bench = (df["benchmark_readmissao_pct"].cast(pl.Float64, strict=False).fill_null(0.0).to_list()
                                 if "benchmark_readmissao_pct" in df.columns else [0.0] * len(linhas))
                        mx = max(altas) if altas and max(altas) > 0 else 1.0
                        charts.bar_categorical(
                            self.plot_trans, linhas, altas,
                            colors=[charts.lerp(charts.PRI_DARK, charts.PRI, v / mx) for v in altas],
                            title="Altas por linha de cuidado",
                            value_fmt="{:,.0f}", sort_desc=True, horizontal=True,
                            axis_label=("nº de altas  ·  readmissão 30d e contato 48h não mensuráveis "
                                        "neste dataset (SIH-RD não identifica o paciente)"),
                        )
            self._plfill(self.tbl_trans, df)
        except Exception as _e:
            print(f"[!!!] EXCECAO _fill_tab_trans: {type(_e).__name__}: {_e}", flush=True)
            traceback.print_exc()

    # ------------------------------------------------------------------
    def _fill_tab_geral(self, m: DashboardMetrics) -> None:
        try:
            ts = m.serie_temporal
            if pg is not None:
                if ts is None or ts.is_empty() or "n_internacoes" not in ts.columns:
                    charts.empty_message(self.plot_ts, "Sem série temporal (requer competência ano/mês)")
                else:
                    cols = ts.columns
                    n = ts["n_internacoes"].cast(pl.Float64, strict=False).fill_null(0.0).to_list()
                    try:
                        k1, k2 = cols[0], cols[1]
                        labels = [f"{int(a)}-{int(b):02d}" for a, b in zip(ts[k1].to_list(), ts[k2].to_list())]
                    except (TypeError, ValueError, IndexError):
                        labels = [str(i + 1) for i in range(len(n))]
                    taxa = (ts["taxa_icsap_pct"].cast(pl.Float64, strict=False).fill_null(0.0).to_list()
                            if "taxa_icsap_pct" in ts.columns else None)
                    periodo = f"{labels[0]} a {labels[-1]}" if len(labels) > 1 else (labels[0] if labels else "")
                    charts.bars_with_line(
                        self.plot_ts, labels, n, taxa,
                        title=f"Internações por competência e taxa ICSAP — {periodo}",
                        bar_name="Internações", line_name="Taxa ICSAP (%)", line_max=None,
                    )
            # Alertas
            if m.alerts:
                df_alerts = pl.DataFrame({"#": list(range(1, len(m.alerts) + 1)),
                                          "Alerta Operacional": m.alerts})
            else:
                df_alerts = pl.DataFrame({"#": ["1"], "Alerta Operacional":
                                          ["Nenhum alerta. Todos os indicadores dentro dos limites recomendados."]})
            self._plfill(self.tbl_alerts, df_alerts)
            self._plfill(self.tbl_top10_p, m.top10_procedimentos)
            self._plfill(self.tbl_top10_e, m.top10_especialidades)
        except Exception as _e:
            print(f"[!!!] EXCECAO _fill_tab_geral: {type(_e).__name__}: {_e}", flush=True)
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Slots mapa concêntrico e filtros de agrupamento SIGTAP
    # ------------------------------------------------------------------
    def _rebuild_map_group_options(self) -> None:
        """Popula os combos de agrupamento com valores únicos do dataset."""
        opts: dict[str, list[str]] = {"grupo": [], "forma": [], "procedimento": []}
        df = self._last_df
        if df is not None and not df.is_empty():
            # Grupo SIGTAP
            col_g = self._find_col(df, ("NO_GRUPO", "CO_GRUPO", "grupo", "DS_GRUPO"))
            if col_g:
                vals = df[col_g].drop_nulls().cast(pl.Utf8).unique().sort().to_list()
                opts["grupo"] = [v for v in vals if v and v.strip()]
            # Forma de Organização
            col_f = self._find_col(df, ("NO_FORMA_ORGANIZACAO", "CO_FORMA_ORGANIZACAO",
                                        "forma_organizacao", "DS_FORMA_ORGANIZACAO"))
            if col_f:
                vals = df[col_f].drop_nulls().cast(pl.Utf8).unique().sort().to_list()
                opts["forma"] = [v for v in vals if v and v.strip()]
            # Procedimento
            col_p = self._find_col(df, ("NO_PROCEDIMENTO", "CO_PROCEDIMENTO",
                                        "procedimento", "DS_PROCEDIMENTO"))
            if col_p:
                vals = df[col_p].drop_nulls().cast(pl.Utf8).unique().sort().to_list()
                opts["procedimento"] = [v for v in vals if v and v.strip()][:500]
        self._map_group_options = opts
        self._refresh_filter_combo()

    @staticmethod
    def _find_col(df: pl.DataFrame, candidates: tuple[str, ...]) -> str | None:
        for c in candidates:
            if c in df.columns:
                return c
        return None

    def _refresh_filter_combo(self) -> None:
        mode = self.cbo_map_group.currentData() or "grupo"
        self.cbo_map_filter.blockSignals(True)
        self.cbo_map_filter.clear()
        self.cbo_map_filter.addItem("🔎 Todos (agregado geral)", None)
        for val in self._map_group_options.get(mode, [])[:400]:
            label = val if len(val) <= 55 else val[:53] + "…"
            self.cbo_map_filter.addItem(label, val)
        self.cbo_map_filter.blockSignals(False)

    def _on_map_group_changed(self, _idx: int) -> None:
        try:
            self._refresh_filter_combo()
            mode = self.cbo_map_group.currentData() or "grupo"
            self.concentric_map.set_group_mode(mode, None)
        except Exception as _e:
            print(f"[!!!] EXCECAO _on_map_group_changed: {type(_e).__name__}: {_e}", flush=True)
            traceback.print_exc()

    def _on_map_filter_changed(self, _idx: int) -> None:
        try:
            mode = self.cbo_map_group.currentData() or "grupo"
            try:
                val = self.cbo_map_filter.currentData()
            except Exception:
                val = None
            self.concentric_map.set_group_mode(mode, val)
        except Exception as _e:
            print(f"[!!!] EXCECAO _on_map_filter_changed: {type(_e).__name__}: {_e}", flush=True)
            traceback.print_exc()

    def _on_map_hospital_clicked(self, cnes: str) -> None:
        try:
            self.hospital_selected.emit(cnes)
            self.lbl_map_hover.set_status(f"🏥 Hospital selecionado — CNES {cnes}", "ok")
            # Seleciona aba Risco e destaca o hospital (se possível)
            self.tabs.setCurrentIndex(0)
        except Exception as _e:
            print(f"[!!!] EXCECAO _on_map_hospital_clicked: {type(_e).__name__}: {_e}", flush=True)
            traceback.print_exc()

    def _on_map_radii_changed(self, _idx: int) -> None:
        try:
            radii = self.cbo_map_radii.currentData()
            if radii is not None:
                self.concentric_map.set_radii(radii)
                if self._metrics is not None:
                    self._fill_tab_terr(self._metrics)
        except Exception as _e:
            print(f"[!!!] EXCECAO _on_map_radii_changed: {type(_e).__name__}: {_e}", flush=True)
            traceback.print_exc()

    # ------------------------------------------------------------------
    def _plfill(self, table: QTableWidget, df: pl.DataFrame) -> None:
        table.clear()
        if df is None or df.is_empty():
            table.setRowCount(0); table.setColumnCount(1)
            table.setHorizontalHeaderLabels(["Sem dados"])
            return
        view = df.head(500)
        cols = [str(c) for c in view.columns]
        # Nome do hospital ao lado da coluna de CNES (quando a tabela não traz nome)
        cnes_like = next((c for c in cols if c.lower() in ("_cnes", "cnes", "hospital_cnes", "co_cnes")), None)
        tem_nome = any(c.lower() in ("hospital", "hospital_nome", "nm_hospital", "no_fantasia") for c in cols)
        if cnes_like and self._nome_por_cnes and not tem_nome:
            try:
                view = view.with_columns(
                    pl.col(cnes_like).cast(pl.Utf8, strict=False).str.strip_chars().str.zfill(7)
                    .replace_strict(self._nome_por_cnes, default="").alias("Hospital")
                )
                cols.insert(cols.index(cnes_like) + 1, "Hospital")
                view = view.select(cols)
            except Exception:  # noqa: BLE001
                cols = [str(c) for c in view.columns]
        table.setColumnCount(len(cols))
        table.setHorizontalHeaderLabels(cols)
        rows = min(view.height, 500)
        table.setRowCount(rows)
        for r in range(rows):
            for c, col in enumerate(cols):
                v = view[col][r]
                if v is None:
                    s = ""
                elif isinstance(v, float):
                    if math.isfinite(v):
                        is_pct = ("pct" in col.lower() or "%" in col)
                        s = (f"{v:,.2f}" if (abs(v) >= 1000 or is_pct) else f"{v:.4f}" if abs(v) < 1
                             else f"{v:.2f}").replace(",", "_").replace(".", ",").replace("_", ".")
                    else:
                        s = ""
                else:
                    s = str(v)
                item = QTableWidgetItem(s)
                if isinstance(v, (int, float)):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                # semáforo só em colunas onde "alto = ruim" (ICSAP, readmissão, risco alto, glosa, custo alto)
                cl = col.lower()
                ruim_alto = any(k in cl for k in ("icsap", "readm", "pct_alto", "glos", "custo_alto", "o_e", "oe"))
                if ruim_alto and ("pct" in cl or "%" in col) and isinstance(v, (int, float)):
                    item.setForeground(QBrush(QColor(_TXT)))
                    try:
                        cor = _color_for_pct(float(v))
                        item.setBackground(QBrush(cor))
                        item.setForeground(QBrush(Qt.white if (cor.red()*0.299 + cor.green()*0.587 + cor.blue()*0.114) < 140 else Qt.black))
                    except Exception:
                        pass
                table.setItem(r, c, item)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)

    def _on_load_files(self) -> None:
        """Importa arquivos locais (CSV/Parquet/DBF/DBC) direto no dashboard, sem pipeline.

        - CSV: detecta separador/codificação de CSV BR (DATASUS).
        - DBF/DBC: lidos via read_dbc (roteia por extensão: .dbf puro ou .dbc comprimido).
        Depois infere o sistema SUS pelas colunas e chama set_dataset() para
        popular KPIs + cálculo.
        """
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Selecionar arquivos (CSV, Parquet, DBF ou DBC)",
            str(Path.home()),
            "Dados (*.csv *.parquet *.pq *.dbf *.dbc *.DBC *.DBF);;Todos (*)",
        )
        if not files:
            return
        self.lbl_status.set_status(f"⏳ Carregando {len(files)} arquivo(s)...", "info")
        QApplication.processEvents()
        try:
            dfs = []
            for f in files:
                p = Path(f)
                ext = p.suffix.lower()
                if ext in (".parquet", ".pq"):
                    df = pl.read_parquet(p)
                elif ext in (".dbf", ".dbc"):
                    # read_dbc roteia por extensão: .dbf → DBF puro; .dbc → descompacta.
                    from papahr.core.dbc_reader import read_dbc
                    df = read_dbc(p)
                elif ext == ".csv":
                    # CSV BR (DATASUS/GSPHosp) usa (; sep) + windows-1252/utf8-bom.
                    from papahr.dashboard.cnes_loader import _detect_csv_params
                    auto_params = dict(_detect_csv_params(p))
                    auto_params.update(
                        try_parse_dates=True,
                        infer_schema_length=5000,
                        infer_schema=True,
                    )
                    try:
                        df = pl.read_csv(p, **auto_params)
                    except Exception as _e_csv1:
                        # Fallback: detecta separador por contagem bruta em 64KB.
                        try:
                            raw = p.read_bytes()[:65536]
                            c_semi = raw.count(b";")
                            c_comma = raw.count(b",")
                            sep = ";" if c_semi >= c_comma else ","
                        except Exception:
                            sep = ","
                        try:
                            df = pl.read_csv(
                                p,
                                separator=sep,
                                encoding="utf8-lossy",
                                quote_char='"',
                                infer_schema=True,
                                low_memory=True,
                                ignore_errors=True,
                                truncate_ragged_lines=True,
                                try_parse_dates=True,
                                infer_schema_length=5000,
                            )
                        except Exception:
                            raise RuntimeError(
                                f"CSV {p.name}: falhou autodetect e fallback. "
                                f"Erro1: {type(_e_csv1).__name__}: {_e_csv1}"
                            )
                else:
                    continue
                if not df.is_empty():
                    dfs.append(df)
            if not dfs:
                raise ValueError("Nenhum dado válido foi carregado dos arquivos.")
            df_final = dfs[0] if len(dfs) == 1 else pl.concat(dfs, how="diagonal_relaxed")

            # Auto-detecta o sistema SUS por heurística de colunas.
            cols_norm: set[str] = {c.strip().lower() for c in getattr(df_final, "columns", [])}

            def _has(*candidates: str) -> bool:
                return any(c.strip().lower() in cols_norm for c in candidates)

            if _has("n_aih", "aih", "cod_aih", "no_aih", "co_aih", "valor_aprovado", "val_tot", "diag_princ"):
                auto_system = "SIH_RD"
            elif _has("pa_proc_id", "pa_valapr", "pa_qtdapr", "pa_ufmun", "pa_munpcnt",
                      "pa", "co_pa", "apac", "no_pa", "valor_apresentado", "vl_hospitalar",
                      "cod_procedimento", "co_procedimento"):
                auto_system = "SIA_PA"
            elif _has("cnes_exec", "cnes", "no_fantasia", "co_cnes") and _has("sp_proc", "sp_valor", "co_ato", "sp_cnes"):
                auto_system = "SIH_SP"
            elif _has("cnes_exec", "cnes_prestador") and _has("rejeicao", "mot_rejeit", "co_mot_rejeicao", "cod_rejeicao"):
                auto_system = "SIH_RJ"
            else:
                auto_system = self._last_system or "SIH_RD"

            self._fonte_atual = "session"
            idx = self.cbo_fonte_dados.findData("session")
            if idx >= 0:
                self.cbo_fonte_dados.blockSignals(True)
                self.cbo_fonte_dados.setCurrentIndex(idx)
                self.cbo_fonte_dados.blockSignals(False)
            self.set_dataset(df_final, system=auto_system, cnes=None, compute_huff=None)
            resumo = f"{len(files)} arquivo(s) · {df_final.height:,} linhas × {df_final.width} colunas · {auto_system}"
            try:
                self.lbl_fonte_info.setText(f"✅ {resumo}")
            except Exception:
                pass
            self.lbl_status.set_status(f"✅ {resumo} — pronto para calcular", "ok")
            QApplication.processEvents()
        except Exception as exc:
            print(f"[!!!] EXCECAO _on_load_files: {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
            log.exception("Erro ao importar arquivos no dashboard")
            self.lbl_status.set_status(f"Erro ao importar: {type(exc).__name__}", "error")
            QMessageBox.warning(
                self, "Falha ao importar arquivos",
                f"Não foi possível carregar os arquivos:\n\n{type(exc).__name__}: {str(exc)[:300]}",
            )

    # ------------------------------------------------------------------
    def _on_export(self) -> None:
        if self._metrics is None:
            return
        base = QFileDialog.getExistingDirectory(self, "Escolha pasta para exportar CSVs do Dashboard")
        if not base:
            return
        p = Path(base)
        try:
            for name, df in [
                ("kpis.csv", pl.DataFrame([
                    {"kpi": k, "valor": v} for k, v in self._metrics.kpis.items()
                ])),
                ("estratificacao_risco.csv", self._metrics.estratificacao_risco),
                ("eficiencia_clinica.csv",   self._metrics.eficiencia_clinica),
                ("integracao_territorial.csv", self._metrics.integracao_territorial),
                ("transicao_cuidado.csv",    self._metrics.transicao_cuidado),
                ("linhas_cuidado_hospital.csv", self._metrics.linhas_cuidado_por_hospital),
                ("serie_temporal.csv",       self._metrics.serie_temporal),
                ("icsap_por_condicao.csv",   self._metrics.icsap_por_condicao),
                ("top10_procedimentos.csv",  self._metrics.top10_procedimentos),
                ("top10_especialidades.csv", self._metrics.top10_especialidades),
                ("huff_hospital_attraction.csv", self._metrics.huff.hospital_attraction if self._metrics.huff else pl.DataFrame()),
                ("huff_catchment_profile.csv",   self._metrics.huff.catchment_profile if self._metrics.huff else pl.DataFrame()),
                ("huff_od_matrix_head.csv",   self._metrics.huff.od_matrix.head(500000) if self._metrics.huff else pl.DataFrame()),
                ("glosas_causas.csv", self._metrics.glosas_causas),
                ("glosas_serie_temporal.csv", self._metrics.glosas_serie_temporal),
            ]:
                if df.is_empty():
                    continue
                try:
                    df.write_csv(p / name, separator=";")
                except Exception as exc:  # noqa: BLE001
                    log.warning(f"Erro ao exportar {name}: {exc}")
            QMessageBox.information(self, "Exportação OK",
                                    f"Dados do dashboard salvos em:\n{p}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Erro na exportação", str(exc))

    # ------------------------------------------------------------------
    # Glosas e Não Pagamentos (preenchimento da aba)
    # ------------------------------------------------------------------
    def _fill_tab_glosas(self, m: DashboardMetrics) -> None:
        """Popula KPIs, 2 gráficos e 2 tabelas de glosas."""
        try:
            causas_df = m.glosas_causas
            serie_df = m.glosas_serie_temporal
            kpi_total = m.kpis.get("n_aihs_ou_apacs", 0.0) or 0.0

            # ---- KPIs rápidos (4 cards) ----
            if causas_df.is_empty() and serie_df.is_empty():
                self.kpi_glosas_n.set_value(0)
                self.kpi_glosas_pct.set_value(0.0)
                self.kpi_glosas_val.set_value(0.0)
                self.kpi_glosas_top.set_value("Sem dados de glosa no dataset")
                if pg is not None:
                    self.plot_glosas_causas.clear(); self.plot_glosas_serie.clear()
                self._plfill(self.tbl_glosas_causas, pl.DataFrame({"Informação": [
                    "Nenhuma coluna de glosa foi detectada no dataset carregado.",
                    "Colunas procuradas: VL_GLOSA_PARCIAL, VL_GLOSA_TOTAL, QT_GLOSA_*, CO_GLOSA1..5,",
                    "  MOTIVO_GLOSA, COMPETÊNCIA, ANO/MÊS, DATAS de internação/alta.",
                    "Solução: use arquivos SIH-RD, SIH-RJ ou SIA-PA do DATASUS que contenham essas colunas de auditoria/glosa."
                ]}))
                self._plfill(self.tbl_glosas_serie, pl.DataFrame({"Informação": [
                    "Série histórica indisponível — faltam colunas de competência/ano/mês ou de glosa."
                ]}))
                return

            # --- Agregados globais para KPI ---
            n_glosas: int = 0
            pct_glosas: float = 0.0
            valor_glosado: float = 0.0
            top_causa: str = "—"
            if not serie_df.is_empty():
                col_n_tot = "N total AIHs/APACs"
                col_n_gl = "N glosadas"
                col_vl_gl = "Valor glosado (R$)"
                if col_n_tot in serie_df.columns and col_n_gl in serie_df.columns:
                    n_total = int(serie_df[col_n_tot].cast(pl.Int64, strict=False).fill_null(0).sum() or 0)
                    n_glosas = int(serie_df[col_n_gl].cast(pl.Int64, strict=False).fill_null(0).sum() or 0)
                    if kpi_total > 0:
                        pct_glosas = 100.0 * n_glosas / float(kpi_total)
                    elif n_total > 0:
                        pct_glosas = 100.0 * n_glosas / float(n_total)
                if col_vl_gl in serie_df.columns:
                    valor_glosado = float(serie_df[col_vl_gl].cast(pl.Float64, strict=False).fill_null(0).sum() or 0.0)
            if not causas_df.is_empty():
                col_causa = "Motivo / Descrição" if "Motivo / Descrição" in causas_df.columns else "Código Glosa"
                col_n = "Nº AIHs/APACs afetadas"
                if col_causa in causas_df.columns and col_n in causas_df.columns:
                    linha0 = causas_df.select([col_causa, col_n]).sort(col_n, descending=True).head(1)
                    if linha0.height:
                        top_causa_val = str(linha0[col_causa][0] or "").strip()
                        if not top_causa_val and "Código Glosa" in causas_df.columns:
                            top_causa_val = str(causas_df["Código Glosa"][0] or "")
                        if len(top_causa_val) > 60:
                            top_causa_val = top_causa_val[:57] + "…"
                        top_causa = top_causa_val or "—"
                # Se série_df não existir mas causas sim, calcular KPI por causas
                if n_glosas == 0 and ("Nº AIHs/APACs afetadas" in causas_df.columns):
                    n_glosas = int(causas_df["Nº AIHs/APACs afetadas"].cast(pl.Int64, strict=False).fill_null(0).sum() or 0)
                    if kpi_total > 0:
                        pct_glosas = 100.0 * n_glosas / float(kpi_total)
                if valor_glosado == 0 and ("Valor total glosado (R$)" in causas_df.columns):
                    valor_glosado = float(causas_df["Valor total glosado (R$)"].cast(pl.Float64, strict=False).fill_null(0).sum() or 0.0)
            self.kpi_glosas_n.set_value(n_glosas)
            self.kpi_glosas_pct.set_value(pct_glosas)
            self.kpi_glosas_val.set_value(valor_glosado)
            self.kpi_glosas_top.set_value(top_causa)

            # ---- Gráfico 1: Top causas de glosa (barras horizontais/verticais ordenadas) ----
            if pg is not None:
                self.plot_glosas_causas.clear()
                if not causas_df.is_empty():
                    col_n = "Nº AIHs/APACs afetadas"
                    col_pct = "% sobre todas as glosas"
                    col_lbl = "Motivo / Descrição" if "Motivo / Descrição" in causas_df.columns else "Código Glosa"
                    if col_n in causas_df.columns and col_lbl in causas_df.columns:
                        top = causas_df.sort(col_n, descending=True).head(15)
                        top = top.reverse()  # barras menores em baixo; top N com ordenação invertida -> primeira em cima
                        vals = top[col_n].cast(pl.Float64, strict=False).fill_null(0).to_list()
                        labels_full = top[col_lbl].to_list()
                        # Se col_pct existir → cor por % (maior = mais vermelho)
                        pcts = top[col_pct].cast(pl.Float64, strict=False).fill_null(0).to_list() if col_pct in top.columns else vals
                        x = list(range(len(vals)))
                        brushes_list = []
                        max_p = max((p for p in pcts if p is not None and math.isfinite(float(p))), default=1.0) or 1.0
                        for i, p in enumerate(pcts):
                            t = min(1.0, (float(p) if p is not None and math.isfinite(float(p)) else 0.0) / max_p) if max_p > 0 else 0.0
                            color = _lerp_color(_OK, _DANG, t)
                            brushes_list.append(QBrush(color))
                        bg = pg.BarGraphItem(x=x, height=vals, width=0.4, brushes=brushes_list)
                        self.plot_glosas_causas.addItem(bg)
                        # Ticks: rótulos curtos
                        def _label_curto(s: str, lim: int = 22) -> str:
                            ss = str(s or "").strip()
                            return ss if len(ss) <= lim else ss[:lim-1] + "…"
                        ticks = [(i, _label_curto(labels_full[i])) for i in range(len(labels_full))]
                        self.plot_glosas_causas.getAxis("bottom").setTicks([ticks])
                        self.plot_glosas_causas.getAxis("bottom").setStyle(tickTextHeight=110, autoExpandTextSpace=True)
                        self.plot_glosas_causas.setXRange(-0.5, len(x)-0.5 if x else 0.5, padding=0)
                        if vals:
                            mx = max((v for v in vals if v is not None and math.isfinite(float(v))), default=1.0) or 1.0
                            self.plot_glosas_causas.setYRange(0, float(mx) * 1.18, padding=0)
                        titulo = f"Top {len(vals)} causas de glosa | total glosadas = {n_glosas:,}"
                        self.plot_glosas_causas.setTitle(titulo, color=_TXT, size="10pt")

                # ---- Gráfico 2: Série temporal - barras (N glosadas) + linha (% valor glosado) ----
                self.plot_glosas_serie.clear()
                if not serie_df.is_empty():
                    col_x_candidates = [
                        ["Competência (AAAAMM)"],
                        ["Ano", "Mês"],
                    ]
                    x_labels: list[str] = []
                    xs: list[int] = []
                    col_n_gl = "N glosadas"
                    col_pct_vl = "% valor glosado $"
                    col_vl_gl = "Valor glosado (R$)"
                    heights_bar: list[float] = []
                    line_vals: list[float] = []
                    rows = serie_df.to_dicts()
                    for idx, r in enumerate(rows):
                        xs.append(idx)
                        # label do eixo
                        if "Competência (AAAAMM)" in serie_df.columns:
                            x_labels.append(str(r.get("Competência (AAAAMM)") or ""))
                        elif "Ano" in serie_df.columns and "Mês" in serie_df.columns:
                            a = r.get("Ano"); m = r.get("Mês")
                            try:
                                x_labels.append(f"{int(a)}-{int(m):02d}")
                            except (TypeError, ValueError):
                                x_labels.append(str(idx))
                        else:
                            x_labels.append(str(idx))
                        heights_bar.append(float(r.get(col_n_gl) or 0.0) if col_n_gl in serie_df.columns else 0.0)
                        pct_v = r.get(col_pct_vl) if col_pct_vl in serie_df.columns else None
                        if pct_v is not None and math.isfinite(float(pct_v)):
                            line_vals.append(float(pct_v))
                        else:
                            # fallback: valor_glosado apresentado normalizado em % do max valor
                            line_vals.append(float(r.get(col_vl_gl) or 0.0) if col_vl_gl in serie_df.columns else 0.0)
                    # Barras
                    if heights_bar:
                        bg_serie = pg.BarGraphItem(x=xs, height=heights_bar, width=0.35, brush=_brush(_DANG))
                        self.plot_glosas_serie.addItem(bg_serie)
                    # Linha amarela (eixo 2 simplificado): normaliza para 0..max(heights_bar)*1.12
                    if line_vals and any(math.isfinite(v) and v > 0 for v in line_vals):
                        is_pct = (col_pct_vl in serie_df.columns)
                        if is_pct:
                            # escala % 0..~100 → normaliza para range do gráfico de barras (se barras não zero)
                            max_bar = max((v for v in heights_bar if math.isfinite(v)), default=1.0) or 1.0
                            max_line = max((v for v in line_vals if math.isfinite(v)), default=1.0) or 1.0
                            sc = (max_bar / 100.0) if max_line <= 101 else (max_bar / max_line)
                            line_scaled = [v * sc for v in line_vals]
                        else:
                            line_scaled = line_vals
                        # plota linha amarela grossa
                        self.plot_glosas_serie.plot(xs, line_scaled, pen=_pen(_WARN, 3), name="% valor glosado $")
                        try:
                            self.plot_glosas_serie.showAxis("right")
                            self.plot_glosas_serie.getAxis("right").setPen(_pen(_WARN))
                            self.plot_glosas_serie.getAxis("right").setTextPen(_WARN)
                            self.plot_glosas_serie.getAxis("right").setLabel(
                                "% valor glosado ($)" if is_pct else "Valor glosado R$",
                                color=_WARN)
                            # escala eixo direito: mapeia 0..max_scaled → 0..max_original
                            max_sc = max(line_scaled) if line_scaled else 1.0
                            max_orig = max(line_vals) if line_vals else 1.0
                            ratio = (max_orig / max_sc) if max_sc > 0 else 1.0
                            self.plot_glosas_serie.getAxis("right").setScale(ratio)
                        except Exception:
                            pass
                    # Ticks
                    if x_labels and xs:
                        passo = max(1, len(xs)//14)
                        ticks_s = [(xs[i], x_labels[i]) for i in range(0, len(xs), passo)]
                        self.plot_glosas_serie.getAxis("bottom").setTicks([ticks_s])
                        self.plot_glosas_serie.getAxis("bottom").setStyle(tickTextHeight=90, autoExpandTextSpace=True)
                        self.plot_glosas_serie.setXRange(-0.5, xs[-1]+0.5, padding=0)
                    if heights_bar:
                        mx = max((v for v in heights_bar if math.isfinite(v)), default=1.0) or 1.0
                        self.plot_glosas_serie.setYRange(0, float(mx) * 1.25, padding=0)
                    self.plot_glosas_serie.setTitle(
                        f"Série histórica — {len(rows)} períodos (competência/ano-mês)",
                        color=_TXT, size="10pt")

            # ---- Tabelas ----
            if not causas_df.is_empty():
                self._plfill(self.tbl_glosas_causas, causas_df)
            else:
                self._plfill(self.tbl_glosas_causas, pl.DataFrame({"Observação": [
                    "Sem causas detalhadas de glosa no dataset.",
                    "Solução: use SIA/SIH com colunas CO_GLOSA1..CO_GLOSA5 ou MOTIVO_GLOSA."
                ]}))
            if not serie_df.is_empty():
                self._plfill(self.tbl_glosas_serie, serie_df)
            else:
                self._plfill(self.tbl_glosas_serie, pl.DataFrame({"Observação": [
                    "Sem série temporal de glosas. Faltam colunas de competência/ano/mês ou data.",
                    "Solução: arquivos DATASUS com coluna COMPETÊNCIA ou ANO/MÊS ou DT_INTERNACAO."
                ]}))
        except Exception as _e:
            print(f"[!!!] EXCECAO _fill_tab_glosas: {type(_e).__name__}: {_e}", flush=True)
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Benchmarking Hospitalar
    # ------------------------------------------------------------------
    _INDICADOR_PIOR_ALTO = {
        "icsap_pct", "los_medio_dias", "readmissao_30d_pct",
        "pct_alto", "custo_oe", "los_o_e", "lace_medio",
    }

    def _fill_tab_bench(self, m: DashboardMetrics) -> None:
        """Popula o combo de hospitais e dispara a renderização inicial."""
        try:
            df = m.benchmarking
            self.cbo_bench_hospital.blockSignals(True)
            self.cbo_bench_hospital.clear()
            if df is not None and not df.is_empty():
                # Garante colunas
                cols = df.columns
                col_c = "hospital_cnes"
                col_n = "hospital_nome" if "hospital_nome" in cols else col_c
                for r in df.select([col_c, col_n]).iter_rows(named=True):
                    cnes = str(r[col_c] or "")
                    nome = str(r.get(col_n, "") or "").strip()
                    if not cnes:
                        continue
                    label = f"{nome}" if nome else f"CNES {cnes}"
                    if nome and cnes not in nome:
                        label = f"{cnes} — {nome}"
                    self.cbo_bench_hospital.addItem(label, cnes)
            self.cbo_bench_hospital.blockSignals(False)
            if self.cbo_bench_hospital.count() > 0:
                target_idx = 0
                if getattr(m, "cnes_sel", None):
                    target_cnes = {str(c).strip().zfill(7) for c in m.cnes_sel if c}
                    for idx in range(self.cbo_bench_hospital.count()):
                        c_val = str(self.cbo_bench_hospital.itemData(idx) or "").strip().zfill(7)
                        if c_val in target_cnes:
                            target_idx = idx
                            break
                self.cbo_bench_hospital.setCurrentIndex(target_idx)
                self._on_bench_changed(target_idx)
            else:
                self._on_bench_changed(0)
        except Exception as _e:
            print(f"[!!!] EXCECAO _fill_tab_bench: {type(_e).__name__}: {_e}", flush=True)
            traceback.print_exc()

    def _on_bench_changed(self, _idx: int = 0) -> None:
        """Atualiza os gráficos e tabela de benchmarking conforme seleção."""
        try:
            m = self._metrics
            if m is None or m.benchmarking.is_empty():
                self.lbl_bench_summary.setText(
                    "Hospital: ——   Nível geográfico: ——   Comparando com —— hospitais (sem benchmarking no momento).")
                if pg is not None:
                    self.plot_bench_bar.clear(); self.plot_bench_scatter.clear()
                self._plfill(self.tbl_bench, pl.DataFrame({"Info": [
                    "Nenhum dado de benchmarking disponível. Rode um pipeline com dados CNES/SIA/SIH carregado."]}))
                return
            df = m.benchmarking
            alvo_cnes = self.cbo_bench_hospital.currentData()
            nivel_chave = self.cbo_bench_level.currentData() or "nivel_uf"
            ind = self.cbo_bench_ind.currentData() or "icsap_pct"
            nivel_label = dict(
                (self.cbo_bench_level.itemText(i), self.cbo_bench_level.itemText(i))
                for i in range(self.cbo_bench_level.count())
            ).get(
                self.cbo_bench_level.itemText(self.cbo_bench_level.currentIndex()),
                nivel_chave,
            )
            # --- Acha a linha do hospital alvo
            linha_alvo = df.filter(pl.col("hospital_cnes") == str(alvo_cnes))
            if linha_alvo.is_empty():
                self.lbl_bench_summary.setText(
                    f"Hospital não encontrado no conjunto; {df.height} hospitais totais.")
                return
            alvo_chave = str(linha_alvo[nivel_chave][0] or "")
            if alvo_chave:
                df_comp = df.filter(pl.col(nivel_chave) == alvo_chave)
            else:
                df_comp = df.clone()
            # Remove linhas onde o indicador não existe
            if ind in df_comp.columns:
                df_comp = df_comp.filter(pl.col(ind).is_not_null())
            n_comp = df_comp.height
            if n_comp == 0:
                self.lbl_bench_summary.setText(
                    f"Hospital: ——   Nível geográfico: {nivel_label}   Nenhum hospital com dados para o indicador selecionado.")
                return
            # Ordena pelo indicador (decrescente se MAIS_ALTO_PIOR para colocar os piores primeiro)
            sort_desc = ind in self._INDICADOR_PIOR_ALTO
            df_sorted = df_comp.sort(by=ind, descending=sort_desc)
            # Encontra a posição do alvo no dataframe ordenado
            alvo_i = None
            ordered_cnes = df_sorted["hospital_cnes"].to_list()
            try:
                alvo_i = ordered_cnes.index(str(alvo_cnes))
            except ValueError:
                alvo_i = None
            self.lbl_bench_summary.setText(
                f"Hospital: {self.cbo_bench_hospital.currentText()}   "
                f"Nível geográfico: {nivel_label}   "
                f"Comparando com {n_comp} hospitais."
            )
            # ---------- Gráfico de barras (horizontal, hospital alvo destacado)
            if pg is not None:
                vals = df_sorted[ind].cast(pl.Float64, strict=False).fill_nan(None).to_list()
                labels_b = [self._rotulo_hosp(c) for c in df_sorted["hospital_cnes"].to_list()]
                max_v = max((float(v) for v in vals if v is not None), default=1.0) or 1.0
                colors_b: list[QColor] = []
                for v in vals:
                    if v is None:
                        colors_b.append(QColor(charts.NEUTRAL)); continue
                    t = min(1.0, float(v) / max_v) if max_v > 0 else 0.0
                    if ind in self._INDICADOR_PIOR_ALTO:
                        colors_b.append(charts.lerp(charts.OK, charts.DANG, t))
                    else:
                        colors_b.append(charts.lerp("#818CF8", "#A855F7", t))
                mediana = 0.0
                if any(v is not None for v in vals):
                    mediana = float(df_sorted[ind].cast(pl.Float64, strict=False)
                                    .drop_nans().drop_nulls().median() or 0.0)
                charts.bar_categorical(
                    self.plot_bench_bar, labels_b, [0.0 if v is None else v for v in vals],
                    colors=colors_b,
                    title=f"{self.cbo_bench_ind.currentText()} — {n_comp} hospitais · mediana {mediana:.2f}",
                    value_fmt="{:.2f}", ref=mediana, ref_label="mediana",
                    horizontal=True, highlight=alvo_i, max_items=40,
                )
                # ---------- Dispersão (X=%ICSAP, Y=LOS medio)
                col_x, col_y = "icsap_pct", "los_medio_dias"
                if col_x in df_sorted.columns and col_y in df_sorted.columns:
                    xs_all = df_sorted[col_x].cast(pl.Float64, strict=False).fill_nan(None).to_list()
                    ys_all = df_sorted[col_y].cast(pl.Float64, strict=False).fill_nan(None).to_list()
                    cn = [str(c) for c in df_sorted["hospital_cnes"].to_list()]
                    hi = cn.index(str(alvo_cnes)) if str(alvo_cnes) in cn else None
                    charts.scatter_xy(
                        self.plot_bench_scatter, xs_all, ys_all,
                        title="% ICSAP × permanência média — posição do hospital selecionado",
                        xlabel="% ICSAP (internações evitáveis)", ylabel="LOS médio (dias)",
                        highlight=hi, highlight_label=self._rotulo_hosp(alvo_cnes),
                    )
                else:
                    charts.empty_message(self.plot_bench_scatter)
            # ---------- Tabela
            colunas_uteis = [
                "hospital_cnes", "hospital_nome", "CO_UF", "CO_MUNICIPIO",
                "n_altas", ind, "icsap_pct", "los_medio_dias", "cmi", "lace_medio",
                "pct_alto", "readmissao_30d_pct", "custo_oe", "los_o_e",
            ]
            cols_existem = list(dict.fromkeys(c for c in colunas_uteis if c in df_sorted.columns))
            self._plfill(self.tbl_bench, df_sorted.select(cols_existem) if cols_existem else df_sorted)
            QTimer.singleShot(0, self._refresh_scroll_heights)
        except Exception as _e:
            print(f"[!!!] EXCECAO _on_bench_changed: {type(_e).__name__}: {_e}", flush=True)
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Curvas de decaimento exponencial
    # ------------------------------------------------------------------
    def _on_decay_hospital_changed(self, _idx: int = 0) -> None:
        try:
            m = self._metrics
            if m is None or m.distance_decay.is_empty():
                if pg is not None:
                    charts.empty_message(self.plot_distance_decay,
                                         "Sem curvas de decaimento (requer coordenadas de hospitais e municípios)")
                self.lbl_decay_params.setText(
                    "Ajuste exponencial: y = A · exp(−λ · km)   λ = ——   R² = ——   DMP (km) = ——")
                self._plfill(self.tbl_distance_decay, pl.DataFrame(
                    {"Observação": ["Sem curvas de decaimento. Dados CNES/Municípios faltando."]}))
                return
            cnes = self.cbo_decay_hospital.currentData()
            if not cnes:
                return
            df = m.distance_decay.filter(pl.col("hospital_cnes") == str(cnes))
            if df.is_empty():
                return
            # Parâmetros do ajuste (primeira linha, repetido em todas)
            A = float(df["A_exp"][0]) if "A_exp" in df.columns and df.height else 0.0
            lam = float(df["lambda_exp"][0]) if "lambda_exp" in df.columns and df.height else 0.0
            R2 = float(df["R2_exp"][0]) if "R2_exp" in df.columns and df.height else 0.0
            dmp = float(df["dmp_km"][0]) if "dmp_km" in df.columns and df.height else 0.0
            self.lbl_decay_params.setText(
                f"Ajuste exponencial:  y = {A:.2f} · exp(−{lam:.4f} · km)    "
                f"λ = {lam:.4f}     R² = {R2:.3f}     DMP (km) = {dmp:.1f}")
            # --- Gráfico
            if pg is not None:
                charts.clear(self.plot_distance_decay)
                charts.style_plot(
                    self.plot_distance_decay,
                    f"Curva de decaimento — {self._rotulo_hosp(cnes, 40)}",
                    xlabel="Distância do hospital (km)", ylabel="Taxa de internação / 1.000 hab.",
                    grid_x=True,
                )
                mid_km = df["faixa_mid_km"].cast(pl.Float64).to_list()
                taxa = df["taxa_por_1000"].cast(pl.Float64).to_list()
                # Pontos observados (laranja)
                xs_obs = [float(x) for x, y in zip(mid_km, taxa) if y is not None and math.isfinite(float(y))]
                ys_obs = [float(y) for x, y in zip(mid_km, taxa) if y is not None and math.isfinite(float(y))]
                if xs_obs:
                    sp = pg.ScatterPlotItem(x=xs_obs, y=ys_obs, size=12, symbol="o",
                                            pen=_pen(_WARN), brush=_brush(_WARN))
                    self.plot_distance_decay.addItem(sp)
                # Curva ajustada: 100 amostras de 0 a 80 km
                if lam >= 0 and A > 0:
                    n_pts = 100
                    xs_curve = [80.0 * (i / (n_pts - 1)) for i in range(n_pts)]
                    ys_curve = [A * math.exp(-lam * xx) for xx in xs_curve]
                    self.plot_distance_decay.plot(
                        xs_curve, ys_curve, pen=_pen(_PRI, 3), name="Ajuste exp."
                    )
                self.plot_distance_decay.setLabel("bottom", "Distância do hospital (km)")
                self.plot_distance_decay.setLabel("left", "Taxa de internação / 1.000 hab.")
                self.plot_distance_decay.setXRange(0, 82, padding=0)
                max_y = max(ys_obs + [A * 1.05]) if ys_obs else 1.0
                if max_y <= 0:
                    max_y = 1.0
                self.plot_distance_decay.setYRange(0, float(max_y) * 1.15, padding=0)
                for xo, yo in zip(xs_obs, ys_obs):
                    charts._value_text(f"{yo:.1f}", (0.5, 1.4), xo, yo, self.plot_distance_decay, color=charts.DIM)
                charts.add_legend(self.plot_distance_decay,
                                  [("Taxa observada por anel", charts.WARN), ("Ajuste exponencial", charts.PRI)])
            # --- Tabela 5 anéis
            cols_tab = ["faixa_km_label", "pop_anel", "n_internacoes", "taxa_por_1000"]
            cols_show = [c for c in cols_tab if c in df.columns]
            tab_df = df.select(cols_show) if cols_show else df
            # Renomeia cabeçalhos
            rename_map = {
                "faixa_km_label": "Faixa de distância",
                "pop_anel": "População anel",
                "n_internacoes": "Nº internações",
                "taxa_por_1000": "Taxa / 1.000 hab.",
            }
            final_cols = {}
            for c in tab_df.columns:
                final_cols[c] = rename_map.get(c, c)
            tab_df = tab_df.rename(final_cols)
            self._plfill(self.tbl_distance_decay, tab_df)
        except Exception as _e:
            print(f"[!!!] EXCECAO _on_decay_hospital_changed: {type(_e).__name__}: {_e}", flush=True)
            traceback.print_exc()
