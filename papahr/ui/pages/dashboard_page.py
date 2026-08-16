
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
from pathlib import Path
from typing import Any

import polars as pl

try:
    import pyqtgraph as pg
    from pyqtgraph import PlotWidget
except ImportError:  # pragma: no cover
    pg = None
    PlotWidget = Any  # type: ignore

from PySide6.QtCore import QSize, Qt, QThread, Signal
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
    QSplitter,
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
    HospitalMapPoint,
    KpiCard,
    StatusBadge,
)

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


def _pen(color: str, width: int = 1) -> QPen:
    return QPen(QColor(color), width)


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
        # Fonte de dados da camada (GOLD_OTIMIZADO ou SILVER_FALLBACK)
        self._fonte_dados_camada: str = ""
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.setObjectName("DashboardPage")
        # =============================================================
        # LAYOUT DE SOBREVIVENCIA: ENVELOPAMOS TODO O DASHBOARD EM UM
        # QScrollArea VERTICAL. Assim, se a tela do usuario for MENOR
        # que o minimo do app, ele SCROLLA e NUNCA HAVERA SOBREPOSICAO
        # DE WIDGETS (que e o que voce estava vendo).
        # =============================================================
        root_outer = QVBoxLayout(self)
        root_outer.setContentsMargins(0, 0, 0, 0)
        root_outer.setSpacing(0)

        scroll_vert = QScrollArea()
        scroll_vert.setWidgetResizable(True)
        scroll_vert.setFrameShape(QFrame.Shape.NoFrame)
        scroll_vert.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_vert.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_vert.setStyleSheet(
            "QScrollArea{background:#0F1A1C;}"
            "QScrollBar:vertical{width:10px;background:#121C1E;border-radius:5px;margin:4px 2px;}"
            "QScrollBar::handle:vertical{background:#14B8A6;border-radius:5px;min-height:40px;}"
            "QScrollBar:horizontal{height:10px;background:#121C1E;border-radius:5px;margin:2px 4px;}"
            "QScrollBar::handle:horizontal{background:#14B8A6;border-radius:5px;min-width:40px;}"
            "QScrollBar::add-line,QScrollBar::sub-line{height:0px;width:0px;}"
        )
        host = QWidget()
        host.setStyleSheet("#DashHost{background:#0F1A1C;}")
        host.setObjectName("DashHost")
        root = QVBoxLayout(host)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # =============================================================
        # HEADER: QGridLayout 4 x 2 (nao min-width fixo!)
        # ESPACO E DISTRIBUIDO PELO QT, SEM SOBREPOR.
        # Col 0 (esq, stretch 3) = Titulo / Sistema / Huff / Beta
        # Col 1 (dir, stretch 4) = BOTOES CTA (grid 2x2 word wrap!)
        # =============================================================
        head_frame = QFrame()
        head_frame.setStyleSheet(
            "QFrame{background:#121C1E;border-radius:12px;border:1px solid #233539;}"
        )
        gl_head = QGridLayout(head_frame)
        gl_head.setContentsMargins(16, 16, 16, 16)
        gl_head.setSpacing(16)
        gl_head.setColumnStretch(0, 3)
        gl_head.setColumnStretch(1, 4)

        # --- COLUNA 0: Controles basicos (empilham verticalmente) --
        title = QLabel("📊 Gestão de Saúde Populacional")
        title.setStyleSheet(
            "font-size:16px; font-weight:700; color:#4FD1C5; padding:2px 0px;"
        )
        title.setMinimumHeight(24)
        gl_head.addWidget(title, 0, 0, 1, 1)

        row_sys = QHBoxLayout()
        row_sys.setSpacing(6)
        lbl_s = QLabel("🧪 Sistema:")
        lbl_s.setStyleSheet("color:#CBD5E1;font-weight:600;min-height:26px;")
        self.cbo_system = QComboBox()
        self.cbo_system.addItem("SIH-RD (AIHs Aprovadas — padrão)", "SIH_RD")
        self.cbo_system.addItem("SIA-PA (Ambulatorial / APACs)", "SIA_PA")
        self.cbo_system.addItem("SIH-RJ (Rejeitadas)", "SIH_RJ")
        self.cbo_system.addItem("SIH-SP (Serviços Profissionais)", "SIH_SP")
        self.cbo_system.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cbo_system.setMinimumContentsLength(24)
        self.cbo_system.setStyleSheet(
            "QComboBox{min-height:34px;padding:4px 10px;"
            "background:#162023;color:#E6FFFA;border:1px solid #2D4A4A;border-radius:8px;"
            "font-weight:600;font-size:9.5pt;selection-background-color:#4FD1C5;selection-color:#0F1A1C;}"
            "QComboBox::drop-down{border:none;width:24px;}"
            "QComboBox QAbstractItemView{background:#121C1E;color:#E6FFFA;selection-background-color:#14B8A6;font-size:9.5pt;}"
        )
        row_sys.addWidget(lbl_s, 0)
        row_sys.addWidget(self.cbo_system, 1)
        gl_head.addLayout(row_sys, 1, 0, 1, 1)

        self.chk_huff = QCheckBox("☑ Mapa Huff (Cobertura Gravitacional)")
        self.chk_huff.setChecked(True)
        self.chk_huff.setStyleSheet(
            "QCheckBox{min-height:30px;padding:2px 4px;font-weight:600;color:#94A3B8;font-size:9.5pt;}"
            "QCheckBox::indicator{width:16px;height:16px;border-radius:4px;}"
        )
        gl_head.addWidget(self.chk_huff, 2, 0, 1, 1)

        row_beta = QHBoxLayout()
        row_beta.setSpacing(6)
        self.lbl_beta = QLabel("β decay:")
        self.lbl_beta.setStyleSheet("padding:0 4px;color:#94A3B8;font-weight:600;min-height:26px;")
        self.cbo_beta = QComboBox()
        self.cbo_beta.addItem("2.2 — Ambulatorial (PSF/UBS)", 2.2)
        self.cbo_beta.addItem("1.5 — Média (Clínica, Urgência)", 1.5)
        self.cbo_beta.addItem("0.9 — Alta Complexidade (Onco, Cirurgia)", 0.9)
        self.cbo_beta.setCurrentIndex(1)
        self.cbo_beta.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cbo_beta.setMinimumContentsLength(28)
        self.cbo_beta.setStyleSheet(
            "QComboBox{min-height:34px;padding:4px 10px;"
            "background:#162023;color:#E6FFFA;border:1px solid #2D4A4A;border-radius:8px;"
            "font-weight:600;font-size:9.5pt;selection-background-color:#4FD1C5;selection-color:#0F1A1C;}"
            "QComboBox::drop-down{border:none;width:24px;}"
            "QComboBox QAbstractItemView{background:#121C1E;color:#E6FFFA;selection-background-color:#14B8A6;font-size:9.5pt;}"
        )
        row_beta.addWidget(self.lbl_beta, 0)
        row_beta.addWidget(self.cbo_beta, 1)
        gl_head.addLayout(row_beta, 3, 0, 1, 1)

        # --- COLUNA 1: BOTOES CTA em GRID 2x2 ---
        _cta_padrao = (
            "QPushButton{{border-radius:10px;font-weight:700;font-size:9.5pt;"
            "padding:8px 10px;min-height:44px;white-space:pre-line;}}"
            "QPushButton:hover{{filter:brightness(1.08);}}"
            "QPushButton:disabled{{background:#2D4A4A;color:#64748B;border:1px solid #2D4A4A;}}"
        )
        grid_cta = QGridLayout()
        grid_cta.setSpacing(6)
        grid_cta.setContentsMargins(0, 0, 0, 0)
        grid_cta.setColumnStretch(0, 1)
        grid_cta.setColumnStretch(1, 1)
        grid_cta.setRowStretch(0, 1)
        grid_cta.setRowStretch(1, 1)

        self.btn_calc = QPushButton("🔄 Calcular\nDashboard")
        self.btn_calc.clicked.connect(self._on_calculate)
        self.btn_calc.setStyleSheet(
            _cta_padrao +
            "QPushButton{background:linear-gradient(90deg,#4FD1C5,#14B8A6);color:#000000;"
            "border:2px solid #5EEAD4;}"
        )
        self.btn_calc.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        grid_cta.addWidget(self.btn_calc, 0, 0)

        self.btn_load_csv_parquet = QPushButton("📂 Carregar CSV /\nParquet")
        self.btn_load_csv_parquet.clicked.connect(self._on_load_csv_parquet)
        self.btn_load_csv_parquet.setStyleSheet(
            _cta_padrao +
            "QPushButton{background:#14B8A6;color:#000000;border:2px solid #0D9488;}"
        )
        self.btn_load_csv_parquet.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        grid_cta.addWidget(self.btn_load_csv_parquet, 0, 1)

        self.btn_export = QPushButton("📤 Exportar 12\nCSVs")
        self.btn_export.clicked.connect(self._on_export)
        self.btn_export.setEnabled(False)
        self.btn_export.setStyleSheet(
            _cta_padrao +
            "QPushButton{background:#121C1E;color:#E6FFFA;border:2px solid #2D4A4A;}"
        )
        self.btn_export.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        grid_cta.addWidget(self.btn_export, 1, 0)

        self.btn_reload_fonte = QPushButton("🔄 Recarregar\nFonte")
        self.btn_reload_fonte.clicked.connect(self._on_reload_fonte_clicked)
        self.btn_reload_fonte.setStyleSheet(
            _cta_padrao +
            "QPushButton{background:#0F766E;color:#E6FFFA;border:2px solid #115E59;}"
        )
        self.btn_reload_fonte.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        grid_cta.addWidget(self.btn_reload_fonte, 1, 1)

        # Container col1: StatusBadge + CTAs
        col1_v = QVBoxLayout()
        col1_v.setSpacing(6)
        self.lbl_status = StatusBadge("Aguardando dataset", status="info")
        self.lbl_status.setStyleSheet(
            self.lbl_status.styleSheet() + "QLabel{min-height:28px;font-size:9.5pt;padding:4px 10px;}"
        )
        self.lbl_status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        col1_v.addWidget(self.lbl_status, 0)
        col1_v.addLayout(grid_cta, 1)
        gl_head.addLayout(col1_v, 0, 1, 4, 1)  # ocupa 4 linhas (mesma altura col0)

        root.addWidget(head_frame, 0)

        # =============================================================
        # LINHA FONTE DOS DADOS (combo fonte + combo carga + info)
        # =============================================================
        fonte_frame = QFrame()
        fonte_frame.setStyleSheet(
            "QFrame{background:#121C1E;border-radius:12px;border:1px solid #233539;}"
        )
        ffl = QGridLayout(fonte_frame)
        ffl.setContentsMargins(16, 12, 16, 12)
        ffl.setSpacing(16)
        ffl.setColumnStretch(1, 3)
        ffl.setColumnStretch(3, 3)
        ffl.setColumnStretch(4, 2)

        lbl_f = QLabel("📦 Fonte:")
        lbl_f.setStyleSheet("color:#94A3B8;font-weight:600;min-height:26px;")
        self.cbo_fonte_dados = QComboBox()
        self.cbo_fonte_dados.addItem("🟢 Sessão Atual (pipeline agora)", "session")
        self.cbo_fonte_dados.addItem("💾 DuckDB Persistidos (cargas salvas)", "duckdb")
        self.cbo_fonte_dados.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cbo_fonte_dados.setMinimumContentsLength(30)
        self.cbo_fonte_dados.setStyleSheet(
            "QComboBox{min-height:34px;padding:4px 10px;"
            "background:#162023;color:#E6FFFA;border:1px solid #2D4A4A;border-radius:8px;"
            "font-weight:600;font-size:9.5pt;selection-background-color:#4FD1C5;selection-color:#0F1A1C;}"
            "QComboBox::drop-down{border:none;width:24px;}"
            "QComboBox QAbstractItemView{background:#121C1E;color:#E6FFFA;selection-background-color:#14B8A6;font-size:9.5pt;}"
        )
        self.cbo_fonte_dados.currentIndexChanged.connect(self._on_fonte_dados_changed)
        ffl.addWidget(lbl_f, 0, 0)
        ffl.addWidget(self.cbo_fonte_dados, 0, 1)

        lbl_r = QLabel("🗂 Carga:")
        lbl_r.setStyleSheet("color:#94A3B8;font-weight:600;min-height:26px;")
        self.cbo_load_runs = QComboBox()
        self.cbo_load_runs.addItem("⏳ Todas as cargas (todas runs)", None)
        self.cbo_load_runs.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cbo_load_runs.setMinimumContentsLength(28)
        self.cbo_load_runs.setStyleSheet(
            "QComboBox{min-height:34px;padding:4px 10px;"
            "background:#162023;color:#E6FFFA;border:1px solid #2D4A4A;border-radius:8px;"
            "font-weight:600;font-size:9.5pt;selection-background-color:#4FD1C5;selection-color:#0F1A1C;}"
            "QComboBox::drop-down{border:none;width:24px;}"
            "QComboBox QAbstractItemView{background:#121C1E;color:#E6FFFA;selection-background-color:#14B8A6;font-size:9.5pt;}"
        )
        self.cbo_load_runs.setEnabled(False)
        ffl.addWidget(lbl_r, 0, 2)
        ffl.addWidget(self.cbo_load_runs, 0, 3)

        self.lbl_fonte_info = QLabel("Fonte: sessão atual | 0 linhas")
        self.lbl_fonte_info.setStyleSheet(
            "color:#8FA6A3;font-weight:600;padding:4px 10px;min-height:26px;font-size:9pt;"
        )
        self.lbl_fonte_info.setWordWrap(True)
        self.lbl_fonte_info.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        ffl.addWidget(self.lbl_fonte_info, 0, 4)

        root.addWidget(fonte_frame, 0)

        # =============================================================
        # KPIs: QGridLayout 2 x 3 (2 linhas / 3 colunas)
        # CADA CARD PREENCHE A CÉLULA. NUNCA MAIS SCROLL HORIZONTAL!
        # =============================================================
        self.kpi_estabs  = KpiCard("Estabelecimentos", 0, icon="🏥", accent=_PRI)
        self.kpi_prod    = KpiCard("Nº AIHs / APACs", 0, icon="📄", accent="#6366F1")
        self.kpi_valor   = KpiCard("Valor Total (R$)", 0, icon="💸", accent="#F59E0B")
        self.kpi_icsap   = KpiCard("% ICSAP (evitáveis)", 0.0, icon="⚠️", accent=_WARN, format="{:.1f} %")
        self.kpi_los     = KpiCard("LOS médio (dias)", 0.0, icon="🛏️", accent="#818CF8", format="{:.2f}")
        self.kpi_readm   = KpiCard("Readmissão 30d estimada", 0.0, icon="↩️", accent=_DANG, format="{:.1f} %")

        kpi_grid = QGridLayout()
        kpi_grid.setContentsMargins(0, 0, 0, 0)
        kpi_grid.setSpacing(16)
        kpi_grid.setColumnStretch(0, 1)
        kpi_grid.setColumnStretch(1, 1)
        kpi_grid.setColumnStretch(2, 1)
        kpi_grid.setRowStretch(0, 1)
        kpi_grid.setRowStretch(1, 1)
        kpi_grid.addWidget(self.kpi_estabs, 0, 0)
        kpi_grid.addWidget(self.kpi_prod,   0, 1)
        kpi_grid.addWidget(self.kpi_valor,  0, 2)
        kpi_grid.addWidget(self.kpi_icsap,  1, 0)
        kpi_grid.addWidget(self.kpi_los,    1, 1)
        kpi_grid.addWidget(self.kpi_readm,  1, 2)

        kpi_frame = QFrame()
        kpi_frame.setLayout(kpi_grid)
        kpi_frame.setStyleSheet("QFrame{background:transparent;}")
        root.addWidget(kpi_frame, 0)

        # =============================================================
        # Abas: min-width reduzido, fonte 9.5pt
        # =============================================================
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabBar::tab{background:#162023;color:#94A3B8;padding:8px 12px;border:1px solid #233539;
                         border-top-left-radius:10px;border-top-right-radius:10px;margin-right:3px;
                         font-weight:600;font-size:9pt;min-width:140px;max-width:170px;}
            QTabBar::tab:selected{background:#1B2B2F;color:#4FD1C5;border-bottom:2px solid #4FD1C5;}
            QTabWidget::pane{border:1px solid #233539;border-radius:10px;top:-1px;}
            QTabWidget QWidget{background:#0F1A1C;}""")
        self.tabs.setIconSize(QSize(0, 0))
        self.tabs.addTab(self._build_tab_risco(),   "🎯 Risco")
        self.tabs.addTab(self._build_tab_efic(),    "💎 Eficiência")
        self.tabs.addTab(self._build_tab_terr(),    "🗺️ Território")
        self.tabs.addTab(self._build_tab_trans(),   "🔁 Transição")
        self.tabs.addTab(self._build_tab_geral(),   "🧭 Visão Geral")
        self.tabs.addTab(self._build_tab_bench(),   "🏁 Benchmarking")
        self.tabs.addTab(self._build_tab_glosas(),  "💸 Glosas")
        root.addWidget(self.tabs, 1)

        scroll_vert.setWidget(host)
        root_outer.addWidget(scroll_vert, 1)

        footer_frame = QFrame()
        footer_frame.setStyleSheet(
            "QFrame{background:#0D1719;border-top:1px solid #233539;}"
        )
        footer_hl = QHBoxLayout(footer_frame)
        footer_hl.setContentsMargins(10, 6, 10, 6)
        footer_hl.setSpacing(10)
        self._badge_fonte_camada = StatusBadge("Camada: —", status="info")
        self._badge_fonte_camada.setStyleSheet(
            self._badge_fonte_camada.styleSheet()
            + "QLabel{min-height:26px;font-size:9pt;padding:3px 10px;}"
        )
        self._badge_fonte_camada.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        footer_hl.addWidget(self._badge_fonte_camada, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        footer_hl.addStretch(1)
        root_outer.addWidget(footer_frame, 0)

    # ------------------------------------------------------------------
    # Abas
    # ------------------------------------------------------------------
    def _build_tab_risco(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(16)
        self.plot_estratos = self._mk_plot_widget("Estratos de Risco — % Casos")
        self.plot_cmi = self._mk_plot_widget("Case Mix Index (CMI) por Hospital")
        self.tbl_risco = self._mk_table()
        spl = QSplitter(Qt.Vertical)
        spl.setChildrenCollapsible(False)
        spl.setHandleWidth(4)
        spl.addWidget(self.plot_estratos); spl.addWidget(self.plot_cmi); spl.addWidget(self.tbl_risco)
        spl.setSizes([180, 180, 300])
        l.addWidget(spl, 1)
        return w

    def _build_tab_efic(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(16)
        self.plot_los = self._mk_plot_widget("Tempo Permanência: Observado vs Esperado (O/E)")
        self.plot_custo = self._mk_plot_widget("Custo Médio x Benchmark (O/E)")
        self.tbl_efic = self._mk_table()
        spl = QSplitter(Qt.Vertical)
        spl.setChildrenCollapsible(False)
        spl.setHandleWidth(4)
        spl.addWidget(self.plot_los); spl.addWidget(self.plot_custo); spl.addWidget(self.tbl_efic)
        spl.setSizes([180, 180, 300])
        l.addWidget(spl, 1)
        return w

    def _build_tab_terr(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(16)

        # -----------------------------
        # Barra de filtros do mapa
        # -----------------------------
        filter_bar = QFrame()
        filter_bar.setStyleSheet(
            "QFrame{background:#121C1E;border-radius:10px;border:1px solid #233539;}"
        )
        fb_l = QHBoxLayout(filter_bar)
        fb_l.setContentsMargins(16, 12, 16, 12)
        fb_l.setSpacing(16)

        lbl_map_tip = QLabel("🗺️ Mapa de anéis concêntricos (taxa internação / 1000 hab)")
        lbl_map_tip.setStyleSheet("color:#4FD1C5;font-weight:700;")
        fb_l.addWidget(lbl_map_tip, 1)

        fb_l.addWidget(QLabel("Agrupar por:"))
        self.cbo_map_group = QComboBox()
        self.cbo_map_group.addItem("Grupo SIGTAP", "grupo")
        self.cbo_map_group.addItem("Forma de Organização", "forma")
        self.cbo_map_group.addItem("Procedimento (código/nome)", "procedimento")
        self.cbo_map_group.setStyleSheet("min-width:220px;padding:6px 10px;")
        self.cbo_map_group.currentIndexChanged.connect(self._on_map_group_changed)
        fb_l.addWidget(self.cbo_map_group)

        fb_l.addWidget(QLabel("Filtrar item:"))
        self.cbo_map_filter = QComboBox()
        self.cbo_map_filter.addItem("🔎 Todos (agregado geral)", None)
        self.cbo_map_filter.setStyleSheet("min-width:300px;padding:6px 10px;")
        self.cbo_map_filter.currentIndexChanged.connect(self._on_map_filter_changed)
        fb_l.addWidget(self.cbo_map_filter, 1)

        self.lbl_map_hover = StatusBadge("Selecione um hospital no mapa", status="info")
        fb_l.addWidget(self.lbl_map_hover)

        l.addWidget(filter_bar)

        # -----------------------------
        # Splitter principal: mapa + ICSAP
        # -----------------------------
        spl_top = QSplitter(Qt.Horizontal)
        spl_top.setChildrenCollapsible(False)
        spl_top.setHandleWidth(4)

        # Mapa concêntrico
        self.concentric_map = ConcentricRingsMap()
        self.concentric_map.hospital_clicked.connect(self._on_map_hospital_clicked)
        spl_top.addWidget(self.concentric_map)

        # ICSAP por condição (mantido)
        self.plot_icsap = self._mk_plot_widget("Internações por Condição Sensível à APS (ICSAP)")
        spl_top.addWidget(self.plot_icsap)
        spl_top.setSizes([720, 400])
        l.addWidget(spl_top, 2)

        # -----------------------------
        # Tabelas inferiores
        # -----------------------------
        self.tbl_terr = self._mk_table()
        self.tbl_huff = self._mk_table()
        h2 = QHBoxLayout()
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
        dbl = QHBoxLayout(decay_bar)
        dbl.setContentsMargins(16, 12, 16, 12); dbl.setSpacing(16)
        dbl.addWidget(QLabel("📉 Curva Decaimento — Hosp:"))
        self.cbo_decay_hospital = QComboBox()
        self.cbo_decay_hospital.setStyleSheet("min-width:360px;padding:6px 10px;")
        self.cbo_decay_hospital.currentIndexChanged.connect(self._on_decay_hospital_changed)
        dbl.addWidget(self.cbo_decay_hospital, 1)
        self.lbl_decay_params = QLabel(
            "Ajuste exponencial: y = A · exp(−λ · km)   λ = ——   R² = ——   DMP (km) = ——")
        self.lbl_decay_params.setStyleSheet("color:#4FD1C5;font-weight:600;")
        dbl.addWidget(self.lbl_decay_params, 2)
        l.addWidget(decay_bar)

        self.tbl_distance_decay = self._mk_table()
        self.plot_distance_decay = self._mk_plot_widget(
            "Curva de Decaimento: Taxa Internação / 1000 hab × Distância (km)"
        )
        spl_decay = QSplitter(Qt.Horizontal)
        spl_decay.setChildrenCollapsible(False)
        spl_decay.setHandleWidth(4)
        spl_decay.addWidget(self.plot_distance_decay)
        spl_decay.addWidget(self.tbl_distance_decay)
        spl_decay.setSizes([620, 520])
        l.addWidget(spl_decay, 2)

        return w

    def _build_tab_trans(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(16)
        self.plot_trans = self._mk_plot_widget("% Contato 48h (verde) vs % Readmissão 30d (vermelho)")
        self.tbl_trans = self._mk_table()
        spl = QSplitter(Qt.Vertical)
        spl.setChildrenCollapsible(False)
        spl.setHandleWidth(4)
        spl.addWidget(self.plot_trans); spl.addWidget(self.tbl_trans)
        spl.setSizes([260, 400])
        l.addWidget(spl, 1)
        return w

    def _build_tab_geral(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(16)
        self.plot_ts = self._mk_plot_widget("Série Temporal — Internações (barra) e Taxa ICSAP (linha)")
        self.tbl_alerts = self._mk_table()
        self.tbl_top10_p = self._mk_table()
        self.tbl_top10_e = self._mk_table()
        h = QHBoxLayout()
        h.addWidget(self._mk_group("🚨 Alertas Operacionais", self.tbl_alerts), 1)
        h.addWidget(self._mk_group("🥇 Top-10 Procedimentos", self.tbl_top10_p), 1)
        h.addWidget(self._mk_group("🏆 Top-10 Especialidades/CBO", self.tbl_top10_e), 1)
        spl = QSplitter(Qt.Vertical)
        spl.setChildrenCollapsible(False)
        spl.setHandleWidth(4)
        spl.addWidget(self.plot_ts)
        w2 = QWidget(); w2.setLayout(h); spl.addWidget(w2)
        spl.setSizes([260, 380])
        l.addWidget(spl, 1)
        return w

    def _build_tab_bench(self) -> QWidget:
        """Nova aba: Benchmarking Hospitalar (1 hospital escolhido vs 4 níveis geográficos)."""
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(16)

        # Barra superior de seleção
        sel_bar = QFrame()
        sel_bar.setStyleSheet(
            "QFrame{background:#121C1E;border-radius:10px;border:1px solid #233539;}")
        sbl = QHBoxLayout(sel_bar)
        sbl.setContentsMargins(12, 10, 12, 10); sbl.setSpacing(14)
        sbl.addWidget(QLabel("🏥 Hospital selecionado:"))
        self.cbo_bench_hospital = QComboBox()
        self.cbo_bench_hospital.setStyleSheet("min-width:460px;padding:7px 10px;")
        self.cbo_bench_hospital.currentIndexChanged.connect(self._on_bench_changed)
        sbl.addWidget(self.cbo_bench_hospital, 2)

        sbl.addWidget(QLabel("🌐 Nível geográfico comparativo:"))
        self.cbo_bench_level = QComboBox()
        self.cbo_bench_level.addItem("1. Mesmo Município (ibge 6-7 dígitos)", "nivel_municipio")
        self.cbo_bench_level.addItem("2. Microrregião (5 dígitos IBGE)", "nivel_micro")
        self.cbo_bench_level.addItem("3. Macrorregião (3 dígitos IBGE)", "nivel_macro")
        self.cbo_bench_level.addItem("4. Mesmo Estado (UF — 2 dígitos)", "nivel_uf")
        self.cbo_bench_level.setCurrentIndex(3)   # default: UF (maior amostra)
        self.cbo_bench_level.setStyleSheet("min-width:340px;padding:7px 10px;")
        self.cbo_bench_level.currentIndexChanged.connect(self._on_bench_changed)
        sbl.addWidget(self.cbo_bench_level, 2)

        sbl.addWidget(QLabel("📈 Indicador principal:"))
        self.cbo_bench_ind = QComboBox()
        self.cbo_bench_ind.addItem("% ICSAP (evitáveis)", "icsap_pct")
        self.cbo_bench_ind.addItem("LOS médio (dias)", "los_medio_dias")
        self.cbo_bench_ind.addItem("CMI (complexidade)", "cmi")
        self.cbo_bench_ind.addItem("LACE médio (risco readmissão)", "lace_medio")
        self.cbo_bench_ind.addItem("% Readmissão ≤30d (est.)", "readmissao_30d_pct")
        self.cbo_bench_ind.addItem("Custo O/E (eficiência)", "custo_oe")
        self.cbo_bench_ind.addItem("LOS O/E (tempo x benchmark)", "los_o_e")
        self.cbo_bench_ind.addItem("% Casos ALTO risco", "pct_alto")
        self.cbo_bench_ind.setStyleSheet("min-width:300px;padding:7px 10px;")
        self.cbo_bench_ind.currentIndexChanged.connect(self._on_bench_changed)
        sbl.addWidget(self.cbo_bench_ind, 2)

        self.lbl_bench_summary = QLabel(
            "Hospital: ——   Nível geográfico: ——   Comparando com —— hospitais.")
        self.lbl_bench_summary.setStyleSheet("color:#4FD1C5;font-weight:700;")
        sbl.addStretch(1)
        sbl.addWidget(self.lbl_bench_summary, 3)
        l.addWidget(sel_bar)

        # 2 gráficos + tabela
        self.plot_bench_bar = self._mk_plot_widget(
            "Benchmark: Barras ordenadas do indicador selecionado"
        )
        self.plot_bench_scatter = self._mk_plot_widget(
            "Scatter: efeito conjunto (X=% ICSAP, Y=LOS médio dias) — o hospital selecionado é a cor VERDE"
        )
        self.tbl_bench = self._mk_table()
        spl_top = QSplitter(Qt.Horizontal)
        spl_top.setChildrenCollapsible(False)
        spl_top.setHandleWidth(4)
        spl_top.addWidget(self.plot_bench_bar)
        spl_top.addWidget(self.plot_bench_scatter)
        spl_top.setSizes([600, 600])
        spl_full = QSplitter(Qt.Vertical)
        spl_full.setChildrenCollapsible(False)
        spl_full.setHandleWidth(4)
        spl_full.addWidget(spl_top)
        spl_full.addWidget(self.tbl_bench)
        spl_full.setSizes([300, 420])
        l.addWidget(spl_full, 1)

        return w

    def _build_tab_glosas(self) -> QWidget:
        """Nova aba: Glosas e Não Pagamentos (causas + série histórica)."""
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(16)

        # Header com KPIs rápidos de glosa — Grid 2x2 p/ não sobrepor em 1366x768
        kpi_grid = QGridLayout(); kpi_grid.setSpacing(8)
        kpi_grid.setColumnStretch(0, 1); kpi_grid.setColumnStretch(1, 1)
        kpi_grid.setRowStretch(0, 1); kpi_grid.setRowStretch(1, 1)
        self.kpi_glosas_n = KpiCard("AIHs/APACs glosadas", 0, icon="💸", accent=_DANG, format="{:,}")
        self.kpi_glosas_pct = KpiCard("% de internações glosadas", 0.0, icon="⚠️", accent=_WARN, format="{:.2f} %")
        self.kpi_glosas_val = KpiCard("Valor total glosado (R$)", 0.0, icon="💰", accent="#F97316", format="R$ {:,.2f}")
        self.kpi_glosas_top = KpiCard("1ª causa de glosa", "—", icon="🔍", accent=_PRI, format="{}")
        _kpis_glosas = [self.kpi_glosas_n, self.kpi_glosas_pct, self.kpi_glosas_val, self.kpi_glosas_top]
        for idx, k in enumerate(_kpis_glosas):
            k.setMinimumHeight(90)
            k.setMaximumHeight(140)
            r, c = divmod(idx, 2)
            kpi_grid.addWidget(k, r, c)
        l.addLayout(kpi_grid)

        # Gráficos lado a lado (top)
        self.plot_glosas_causas = self._mk_plot_widget(
            "Distribuição de causas de glosa (Top 15 mais frequentes)"
        )
        self.plot_glosas_serie = self._mk_plot_widget(
            "Série histórica: barras = Nº glosadas | linha = % valor glosado ($)"
        )
        spl_plots = QSplitter(Qt.Horizontal)
        spl_plots.setChildrenCollapsible(False)
        spl_plots.setHandleWidth(4)
        spl_plots.addWidget(self.plot_glosas_causas); spl_plots.addWidget(self.plot_glosas_serie)
        spl_plots.setStretchFactor(0, 1); spl_plots.setStretchFactor(1, 1)
        spl_plots.setSizes([620, 660])
        spl_plots.setCollapsible(0, False); spl_plots.setCollapsible(1, False)
        spl_plots.setMinimumHeight(280)

        # Tabelas lado a lado (inferior)
        self.tbl_glosas_causas = self._mk_table()
        self.tbl_glosas_serie = self._mk_table()
        grp_causas = self._mk_group("📋 Causas de glosa — ocorrências e R$", self.tbl_glosas_causas)
        grp_serie = self._mk_group("📅 Série histórica de glosas", self.tbl_glosas_serie)
        spl_tabs = QSplitter(Qt.Horizontal)
        spl_tabs.setChildrenCollapsible(False)
        spl_tabs.setHandleWidth(4)
        spl_tabs.addWidget(grp_causas); spl_tabs.addWidget(grp_serie)
        spl_tabs.setStretchFactor(0, 1); spl_tabs.setStretchFactor(1, 1)
        spl_tabs.setSizes([620, 660])
        spl_tabs.setCollapsible(0, False); spl_tabs.setCollapsible(1, False)
        spl_tabs.setMinimumHeight(260)

        spl_full = QSplitter(Qt.Vertical)
        spl_full.setChildrenCollapsible(False)
        spl_full.setHandleWidth(4)
        spl_full.addWidget(spl_plots); spl_full.addWidget(spl_tabs)
        spl_full.setSizes([330, 380])
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
        plt.setTitle(title, color=_TXT, size="10pt")
        plt.getAxis("left").setPen(_pen(_TXT_DIM)); plt.getAxis("bottom").setPen(_pen(_TXT_DIM))
        plt.getAxis("left").setTextPen(_TXT_DIM); plt.getAxis("bottom").setTextPen(_TXT_DIM)
        plt.setBackground(_BG)
        plt.showGrid(x=False, y=True, alpha=0.2)
        plt.setMinimumHeight(180)
        return plt

    def _mk_table(self) -> QTableWidget:
        t = QTableWidget(0, 0)
        t.setStyleSheet("""
            QTableWidget{background:#121C1E;alternate-background-color:#172426;color:#E6FFFA;
                         gridline-color:#233539;font-size:13px;border-radius:10px;}
            QTableWidget::item{padding:4px 6px;}
            QHeaderView::section{background:#1B2B2F;color:#8F9EAB;padding:8px;border:none;
                                 border-right:1px solid #233539;border-bottom:2px solid #14B8A6;
                                 font-weight:600;}""")
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
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
        lv = QVBoxLayout(gb); lv.setContentsMargins(16, 24, 16, 16)
        lv.addWidget(inner)
        return gb

    # ------------------------------------------------------------------
    # População (pública API)
    # ------------------------------------------------------------------
    def set_dataset(self, df: pl.DataFrame, *, system: str = "SIA_PA",
                    cnes: list[str] | None = None,
                    compute_huff: bool | None = None) -> None:
        """Dispara o cálculo do dashboard a partir do DataFrame final do pipeline.

        compute_huff: se None, usa o valor do checkbox da UI (chk_huff).
                      Força True/False para sobrescrever em chamadas programáticas.
        """
        # #region debug-point H3_H5: dashboard_set_dataset_ENTRY
        try:
            import json, urllib.request as _ur, pathlib as _pl
            _p = str(_pl.Path('.dbg')/'pipeline-rows-loaded-but-not-showing.env'); _u='http://127.0.0.1:7777/event'; _s='pipeline-rows-loaded-but-not-showing'
            try:
                with open(_p) as _f:
                    _c = _f.read()
                    for _l in _c.split('\n'):
                        if _l.startswith('DEBUG_SERVER_URL='): _u=_l.split('=',1)[1]
                        elif _l.startswith('DEBUG_SESSION_ID='): _s=_l.split('=',1)[1]
            except Exception: pass
            _h = -1; _w=-1; _empty = True
            if df is not None:
                try: _empty = bool(df.is_empty()); _h = int(df.height); _w = int(df.width)
                except Exception: pass
            _n_hosp_uniq=0
            for _col in ("CNES_EXEC","cnes","CO_CNES","CNES"):
                if df is not None and _col in getattr(df, "columns", []):
                    try: _n_hosp_uniq = int(df[_col].n_unique()); break
                    except Exception: pass
            _pld = json.dumps({"sessionId":_s,"runId":"pre","hypothesisId":"H3","location":"dashboard_page.py:set_dataset:L797","msg":"[DEBUG] Dashboard set_dataset ENTRY","data":{"df_is_None":df is None,"df_empty":_empty,"df_height":_h,"df_width":_w,"system":system,"n_hosp_cnes":_n_hosp_uniq,"cnes_list_count":len(cnes or [])}}).encode()
            try: _ur.urlopen(_ur.Request(_u, data=_pld, headers={"Content-Type":"application/json"}), timeout=1).read()
            except Exception: pass
        except Exception: pass
        # #endregion
        self._fonte_dados_camada = "SESSAO_PIPELINE"
        if df is not None and not df.is_empty() and "_fonte_dados" not in df.columns:
            df = df.with_columns(pl.lit("SESSAO_PIPELINE").alias("_fonte_dados"))
        self._last_df = df
        self._last_system = system
        self._last_cnes = cnes
        # === H3a FIX: Sempre volta para 'Sessao Atual' apos receber df do pipeline ===
        self._fonte_atual = "session"
        try:
            self._update_badge_fonte_camada()
        except Exception:
            pass
        try:
            self.cbo_fonte_dados.blockSignals(True)
            self.cbo_fonte_dados.setCurrentIndex(0)
            self.cbo_fonte_dados.blockSignals(False)
            self.cbo_load_runs.setEnabled(False)
        except Exception:
            pass
        # Ajusta combo de sistema
        for i in range(self.cbo_system.count()):
            if self.cbo_system.itemData(i) == system:
                self.cbo_system.setCurrentIndex(i)
                break
        # =============================================================
        # POPULA KPI BÁSICOS IMEDIATAMENTE (antes do worker / cálculo)
        # Se houver df carregado mas algum modulo falhar, usuario ainda
        # vê algo (0 não é o estado verdadeiro do pipeline de carga!)
        # =============================================================
        try:
            if df is not None and not df.is_empty():
                cols = list(df.columns)
                # ---- 1) Estabelecimentos (CNES unique) ----
                n_estabs = 0
                from papahr.dashboard.metrics import _detect_cnes_col
                c_col = _detect_cnes_col(df)
                if c_col:
                    try:
                        n_estabs = int(df[c_col].cast(pl.Utf8, strict=False).n_unique())
                    except Exception:
                        n_estabs = 0
                self.kpi_estabs.set_value(n_estabs)

                # ---- 2) Nº AIHs/APACs (count linhas) ----
                self.kpi_prod.set_value(int(df.height))

                # ---- 3) Valor Total R$ ----
                val_col = None
                for _vc in ("VAL_TOTAL", "VL_TOTAL", "VAL_PAGO", "VAL_APRES",
                            "valor_total", "vl_total_aprovado"):
                    if _vc in cols:
                        val_col = _vc
                        break
                if val_col is None:
                    for _c in cols:
                        _lc = str(_c).lower()
                        if "valor" in _lc and ("total" in _lc or "aprovado" in _lc or "pago" in _lc):
                            val_col = _c
                            break
                try:
                    vtot = 0.0
                    if val_col:
                        vtot = float(
                            df[val_col].cast(pl.Float64, strict=False).fill_null(0.0).sum()
                            or 0.0
                        )
                    self.kpi_valor.set_value(vtot)
                except Exception:
                    pass

                # ---- 4) LOS medio (dias de internacao) ----
                los_col = None
                for _lc in ("QTD_DIARIAS", "QT_DIARIAS", "dias_permanencia",
                            "QT_PERMANENCIA", "los"):
                    if _lc in cols:
                        los_col = _lc
                        break
                if los_col is None:
                    for _c in cols:
                        if ("diaria" in str(_c).lower()) or ("permanencia" in str(_c).lower()):
                            los_col = _c
                            break
                try:
                    if los_col:
                        los_medio = float(
                            df[los_col].cast(pl.Float64, strict=False).fill_null(0.0).mean()
                            or 0.0
                        )
                        self.kpi_los.set_value(round(los_medio, 2))
                except Exception:
                    pass
        except Exception:
            pass  # KPI bruto: se falhar, zeros ainda são mostrados (espera worker)

        # Atualiza info da fonte (para garantir que ao trocar para sessão, mostra linhas novas)
        self._update_fonte_info_label()
        try:
            self._refresh_load_runs()
        except Exception:
            pass
        # #region debug-point H5: dashboard_before_on_calculate_NEW
        try:
            import json, urllib.request as _ur, pathlib as _pl
            _p = str(_pl.Path('.dbg')/'pipeline-rows-loaded-but-not-showing.env'); _u='http://127.0.0.1:7777/event'; _s='pipeline-rows-loaded-but-not-showing'
            try:
                with open(_p) as _f:
                    _c = _f.read()
                    for _l in _c.split('\n'):
                        if _l.startswith('DEBUG_SERVER_URL='): _u=_l.split('=',1)[1]
                        elif _l.startswith('DEBUG_SESSION_ID='): _s=_l.split('=',1)[1]
            except Exception: pass
            _huff_ui = None
            try: _huff_ui = getattr(self.chk_huff, "isChecked", lambda: None)()
            except Exception: pass
            _kpi_raw = {
              "kpi_estabs": getattr(self.kpi_estabs, "_v", -1) if hasattr(self.kpi_estabs,"_v") else None,
              "kpi_prod": getattr(self.kpi_prod, "_v", -1) if hasattr(self.kpi_prod,"_v") else None,
              "kpi_valor": getattr(self.kpi_valor, "_v", -1) if hasattr(self.kpi_valor,"_v") else None,
              "kpi_los": getattr(self.kpi_los, "_v", -1) if hasattr(self.kpi_los,"_v") else None,
            }
            _active = self._current_active_df()
            _act_is_last = _active is self._last_df
            _act_h = -1; _act_empty = True
            if _active is not None:
                try: _act_empty=bool(_active.is_empty()); _act_h=int(_active.height)
                except Exception: pass
            _pld = json.dumps({"sessionId":_s,"runId":"pre","hypothesisId":"H5","location":"dashboard_page.py:set_dataset:L914","msg":"[DEBUG] Dashboard set_dataset → KPI bruto gravado, ANTES _on_calculate (worker DashboardWorker)","data":{"compute_huff_arg":compute_huff,"chk_huff_ui_checked":_huff_ui,"_last_df.height":-1 if self._last_df is None else (int(self._last_df.height) if hasattr(self._last_df,'height') else -1),"_fonte_atual":self._fonte_atual,"_current_active_df is _last_df":_act_is_last,"_current_active_df.height":_act_h,"_current_active_df_empty":_act_empty,"kpi_raw_pos_pupulate":_kpi_raw}}).encode()
            try: _ur.urlopen(_ur.Request(_u, data=_pld, headers={"Content-Type":"application/json"}), timeout=1).read()
            except Exception: pass
        except Exception: pass
        # #endregion
        try:
            self._on_calculate(compute_huff=compute_huff)
            # #region debug-point H5: dashboard_after_on_calculate_ok_NEW
            try:
                import json, urllib.request as _ur, pathlib as _pl
                _p = str(_pl.Path('.dbg')/'pipeline-rows-loaded-but-not-showing.env'); _u='http://127.0.0.1:7777/event'; _s='pipeline-rows-loaded-but-not-showing'
                try:
                    with open(_p) as _f:
                        _c = _f.read()
                        for _l in _c.split('\n'):
                            if _l.startswith('DEBUG_SERVER_URL='): _u=_l.split('=',1)[1]
                            elif _l.startswith('DEBUG_SESSION_ID='): _s=_l.split('=',1)[1]
                except Exception: pass
                _worker_live = self._worker is not None
                _pld = json.dumps({"sessionId":_s,"runId":"pre","hypothesisId":"H5","location":"dashboard_page.py:set_dataset:L946","msg":"[DEBUG] DashboardWorker.start() disparou SEM throw (worker assíncrono; o cálculo final em _on_ready ainda vai acontecer)","data":{"worker_is_not_None":_worker_live}}).encode()
                try: _ur.urlopen(_ur.Request(_u, data=_pld, headers={"Content-Type":"application/json"}), timeout=1).read()
                except Exception: pass
            except Exception: pass
            # #endregion
        except Exception as _e_calc:
            # #region debug-point H5: dashboard_on_calculate_EXCEPTION_NEW
            try:
                import json, urllib.request as _ur, pathlib as _pl
                _p = str(_pl.Path('.dbg')/'pipeline-rows-loaded-but-not-showing.env'); _u='http://127.0.0.1:7777/event'; _s='pipeline-rows-loaded-but-not-showing'
                try:
                    with open(_p) as _f:
                        _c = _f.read()
                        for _l in _c.split('\n'):
                            if _l.startswith('DEBUG_SERVER_URL='): _u=_l.split('=',1)[1]
                            elif _l.startswith('DEBUG_SESSION_ID='): _s=_l.split('=',1)[1]
                except Exception: pass
                import traceback as _tb
                _tb_str = "".join(_tb.format_exception(type(_e_calc), _e_calc, _e_calc.__traceback__)[:8])
                _pld = json.dumps({"sessionId":_s,"runId":"pre","hypothesisId":"H5","location":"dashboard_page.py:set_dataset:L959","msg":"[DEBUG] EXCEPTION lançada em _on_calculate antes do worker.start()","data":{"error_type":type(_e_calc).__name__,"error_msg":str(_e_calc)[:300],"traceback":_tb_str[:2000]}}).encode()
                try: _ur.urlopen(_ur.Request(_u, data=_pld, headers={"Content-Type":"application/json"}), timeout=1).read()
                except Exception: pass
            except Exception: pass
            # #endregion
            raise

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
                cols = runs_df.columns
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
        if is_duck and (self._df_duckdb is None or self._df_duckdb.is_empty()):
            self._load_duckdb_into_df()
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

    def _gold_aggr_has_rows(self) -> bool:
        """Retorna True se gold.aggr_mensal_hospital existir E tiver > 0 rows."""
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

    def _load_duckdb_into_df(self) -> None:
        """Carrega dados persistidos para self._df_duckdb.

        Estratégia Medallion Fallback (P3 R4):
          1. Verifica se gold.aggr_mensal_hospital existe e tem linhas (GOLD).
          2. Se GOLD ok → carrega silver.producao (fonte de detalhe),
             marca como GOLD_OTIMIZADO (camada gold íntegra).
          3. Se GOLD vazio/inexistente → fallback transparente para silver.producao,
             marca como SILVER_FALLBACK (log warning, sem bloqueio para o usuário).
          4. Sempre adiciona a coluna _fonte_dados no resultado.
        """
        try:
            self.lbl_status.set_status("Carregando dados persistidos do DuckDB...", "info")
            QApplication.processEvents()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            db = get_db()
            load_run_id: str | None = self.cbo_load_runs.currentData()

            gold_ok = self._gold_aggr_has_rows()
            if gold_ok:
                self._fonte_dados_camada = "GOLD_OTIMIZADO"
                log.info("[Fallback-P3R4] gold.aggr_mensal_hospital populado → usando GOLD_OTIMIZADO (silver como detalhe).")
            else:
                self._fonte_dados_camada = "SILVER_FALLBACK"
                log.warning(
                    "[Fallback-P3R4] gold.aggr_mensal_hospital VAZIO ou inexistente. "
                    "Fallback automático → silver.producao (transparente para usuário)."
                )

            if load_run_id:
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
                    'uf AS "UF", LPAD(CAST(cnes AS VARCHAR),7,\'0\') AS "CNES_EXEC", no_fantasia AS "NOME_HOSPITAL", '
                    'no_razao_social AS "RAZAO_SOCIAL", LPAD(CAST(municipio_cod AS VARCHAR),7,\'0\') AS "COD_MUNICIPIO_IBGE", '
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
            self._update_badge_fonte_camada()
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
        # #region debug-point H5: dashboard_on_fail_worker_REAL_ERROR
        try:
            import json, urllib.request as _ur, pathlib as _pl
            _p = str(_pl.Path('.dbg')/'pipeline-rows-loaded-but-not-showing.env'); _u='http://127.0.0.1:7777/event'; _s='pipeline-rows-loaded-but-not-showing'
            try:
                with open(_p) as _f:
                    _c = _f.read()
                    for _l in _c.split('\n'):
                        if _l.startswith('DEBUG_SERVER_URL='): _u=_l.split('=',1)[1]
                        elif _l.startswith('DEBUG_SESSION_ID='): _s=_l.split('=',1)[1]
            except Exception: pass
            _pld = json.dumps({"sessionId":_s,"runId":"pre","hypothesisId":"H5","location":"dashboard_page.py:_on_fail:L1190","msg":"[DEBUG] DashboardWorker.failed → _on_fail CHAMADO (esse é o caminho do erro real do cálculo)","data":{"error_msg_full":msg[:3000]}}).encode()
            try: _ur.urlopen(_ur.Request(_u, data=_pld, headers={"Content-Type":"application/json"}), timeout=2).read()
            except Exception: pass
        except Exception: pass
        # #endregion
        self.btn_calc.setEnabled(True)
        self.lbl_status.set_status(f"Erro: {msg[:80]}", "error")
        QMessageBox.critical(self, "Dashboard falhou", msg)

    def _on_ready(self, m: DashboardMetrics) -> None:
        # #region debug-point H5: dashboard_on_ready_worker_FINISHED
        try:
            import json, urllib.request as _ur, pathlib as _pl
            _p = str(_pl.Path('.dbg')/'pipeline-rows-loaded-but-not-showing.env'); _u='http://127.0.0.1:7777/event'; _s='pipeline-rows-loaded-but-not-showing'
            try:
                with open(_p) as _f:
                    _c = _f.read()
                    for _l in _c.split('\n'):
                        if _l.startswith('DEBUG_SERVER_URL='): _u=_l.split('=',1)[1]
                        elif _l.startswith('DEBUG_SESSION_ID='): _s=_l.split('=',1)[1]
            except Exception: pass
            _kp = dict(m.kpis) if getattr(m, 'kpis', None) is not None else {}
            # Corta valores > 80 chars para não infar payload
            for _k, _v in list(_kp.items()):
                if isinstance(_v, str) and len(_v) > 120: _kp[_k] = _v[:120] + "…"
            # Listas/DataFrame shapes
            _df_shapes = {
              "estratificacao_risco": [int(getattr(m.estratificacao_risco,'height',-1)), int(getattr(m.estratificacao_risco,'width',-1))],
              "eficiencia_clinica": [int(getattr(m.eficiencia_clinica,'height',-1)), int(getattr(m.eficiencia_clinica,'width',-1))],
              "territorio_uf_mapa": [int(getattr(m.territorio_uf_mapa,'height',-1)), int(getattr(m.territorio_uf_mapa,'width',-1))],
              "transparencia_prestador": [int(getattr(m.transparencia_prestador,'height',-1)), int(getattr(m.transparencia_prestador,'width',-1))],
              "geral_historico_mensal": [int(getattr(m.geral_historico_mensal,'height',-1)), int(getattr(m.geral_historico_mensal,'width',-1))],
              "benchmarking": [int(getattr(m.benchmarking,'height',-1)), int(getattr(m.benchmarking,'width',-1))],
              "glosas_apac_aih": [int(getattr(m.glosas_apac_aih,'height',-1)), int(getattr(m.glosas_apac_aih,'width',-1))],
            }
            _pld = json.dumps({"sessionId":_s,"runId":"pre","hypothesisId":"H5","location":"dashboard_page.py:_on_ready:L1195","msg":"[DEBUG] DashboardWorker.finished_ok → _on_ready chamado. KPIs e shapes de cada tab para conferir se retornou vazio ou populado","data":{"kpis":_kp,"dfs_shape":_df_shapes}}).encode()
            try: _ur.urlopen(_ur.Request(_u, data=_pld, headers={"Content-Type":"application/json"}), timeout=2).read()
            except Exception: pass
        except Exception: pass
        # #endregion
        self.btn_calc.setEnabled(True)
        self.btn_export.setEnabled(True)
        self._metrics = m
        self._fill_kpis(m)
        self._fill_tab_risco(m)
        self._fill_tab_efic(m)
        self._fill_tab_terr(m)
        self._fill_tab_trans(m)
        self._fill_tab_geral(m)
        self._fill_tab_bench(m)
        self._fill_tab_glosas(m)
        self.lbl_status.set_status("Dashboard atualizado ✓", "ok")

    # ------------------------------------------------------------------
    def _fill_kpis(self, m: DashboardMetrics) -> None:
        k = m.kpis
        self.kpi_estabs.set_value(int(k.get("n_estabelecimentos", 0)))
        self.kpi_prod.set_value(int(k.get("n_aihs_ou_apacs", 0)))
        self.kpi_valor.set_value(float(k.get("valor_total_R$", 0.0)))
        self.kpi_icsap.set_value(float(k.get("icsap_pct", 0.0)))
        self.kpi_los.set_value(float(k.get("dias_permanencia_total", 0.0))
                                / max(1, int(k.get("n_aihs_ou_apacs", 1)))
                                if k.get("n_aihs_ou_apacs", 0) > 0 else 0.0)
        self.kpi_readm.set_value(float(k.get("readmissao_30d_est_pct", 0.0)))

    # ------------------------------------------------------------------
    def _fill_tab_risco(self, m: DashboardMetrics) -> None:
        # Gráfico de estratos (médio de ALTO/MEDIO/BAIXO nos hospitais)
        df = m.estratificacao_risco
        if pg is not None and not df.is_empty():
            self.plot_estratos.clear()
            cat = ["ALTO", "MEDIO", "BAIXO"]
            vals = []
            for c in cat:
                col = f"pct_{c}"
                vals.append(float(df[col].mean()) if col in df.columns else 0.0)
            x = list(range(len(cat)))
            bg = pg.BarGraphItem(x=x, height=vals, width=0.55,
                                 brushes=[_brush(_DANG), _brush(_WARN), _brush(_OK)])
            self.plot_estratos.addItem(bg)
            ax = self.plot_estratos.getAxis("bottom")
            ax.setTicks([(i, c) for i, c in enumerate(cat)])
            self.plot_estratos.setYRange(0, 100)
            self.plot_estratos.setTitle(f"Estratos — ALTO={vals[0]:.1f}%, MÉDIO={vals[1]:.1f}%, BAIXO={vals[2]:.1f}%",
                                        color=_TXT, size="10pt")
        # CMI
        if pg is not None and not m.eficiencia_clinica.is_empty() and "case_mix_index" in m.eficiencia_clinica.columns:
            self.plot_cmi.clear()
            cmi = m.eficiencia_clinica["case_mix_index"].to_numpy()
            cnes = m.eficiencia_clinica["cnes"].to_list()
            y = cmi
            x = list(range(len(y)))
            sp = pg.ScatterPlotItem(x=x, y=list(y), size=8,
                                    pen=_pen(_PRI_DARK), brush=_brush(_PRI))
            self.plot_cmi.addItem(sp)
            if len(x):
                self.plot_cmi.getAxis("bottom").setTicks(
                    [(x[i], (c if len(c) <= 5 else c[-5:]))
                     for i, c in enumerate(cnes) if i % max(1, len(x)//12) == 0])
                self.plot_cmi.setXRange(-0.5, len(x)-0.5, padding=0)
            # média CMI
            avg = float(y.mean()) if len(y) else 0.0
            ln = pg.InfiniteLine(pos=avg, angle=0, pen=_pen(_WARN, 2))
            self.plot_cmi.addItem(ln)
            self.plot_cmi.setTitle(f"Case Mix Index — média={avg:.2f}", color=_TXT, size="10pt")
        # Tabela estratos
        self._plfill(self.tbl_risco, df)

    # ------------------------------------------------------------------
    def _fill_tab_efic(self, m: DashboardMetrics) -> None:
        df = m.eficiencia_clinica
        if pg is not None and not df.is_empty() and "los_o_e" in df.columns:
            self.plot_los.clear()
            labels = df["cnes"].to_list()
            oe = df["los_o_e"].to_numpy().tolist()
            x = list(range(len(oe)))
            colors = [_color_for_pct(50 if v < 1 else 50 + (v-1)*50).name() for v in oe]
            bg = pg.BarGraphItem(x=x, height=oe, width=0.65,
                                 brushes=[QBrush(QColor(c)) for c in colors])
            self.plot_los.addItem(bg)
            self.plot_los.addItem(pg.InfiniteLine(pos=1.0, angle=0, pen=_pen(_WARN, 2)))
            if x:
                self.plot_los.getAxis("bottom").setTicks(
                    [(x[i], (c if len(c) <= 5 else c[-5:]))
                     for i, c in enumerate(labels) if i % max(1, len(x)//12) == 0])
                self.plot_los.setXRange(-0.5, len(x)-0.5, padding=0)
        if pg is not None and not df.is_empty() and "custo_o_e" in df.columns:
            self.plot_custo.clear()
            labels = df["cnes"].to_list() if "cnes" in df.columns else []
            ce = df["custo_o_e"].to_numpy().tolist()
            colors_c = [_color_for_pct(50 if v < 1 else 50 + (v-1)*50).name() for v in ce]
            x = list(range(len(ce)))
            bg = pg.BarGraphItem(x=x, height=ce, width=0.65,
                                 brushes=[QBrush(QColor(c)) for c in colors_c])
            self.plot_custo.addItem(bg)
            self.plot_custo.addItem(pg.InfiniteLine(pos=1.0, angle=0, pen=_pen(_WARN, 2)))
            if x and labels:
                self.plot_custo.getAxis("bottom").setTicks(
                    [(x[i], (c if len(c) <= 5 else c[-5:]))
                     for i, c in enumerate(labels) if i % max(1, len(x)//12) == 0])
                self.plot_custo.setXRange(-0.5, len(x)-0.5, padding=0)
        self._plfill(self.tbl_efic, df)

    # ------------------------------------------------------------------
    def _fill_tab_terr(self, m: DashboardMetrics) -> None:
        # ICSAP por condição
        if pg is not None and not m.icsap_por_condicao.is_empty():
            self.plot_icsap.clear()
            conds = m.icsap_por_condicao["condicao_evitavel"].to_list()
            ns = m.icsap_por_condicao["n_internacoes"].to_numpy().tolist()
            y = list(reversed(ns)); labels = list(reversed(conds))
            brushes = [QBrush(_color_for_pct(50 - 50*(y[i]/(max(y) if max(y) else 1)))) for i in range(len(y))]
            bg = pg.BarGraphItem(x=list(range(len(y))), height=y, width=0.7, brushes=brushes)
            self.plot_icsap.addItem(bg)
            self.plot_icsap.getAxis("bottom").setTicks(
                [(i, c if len(c) < 26 else c[:24] + "…") for i, c in enumerate(labels)])
            ax = self.plot_icsap.getAxis("bottom")
            ax.setStyle(tickTextHeight=110, autoExpandTextSpace=True)
            self.plot_icsap.setXRange(-0.5, len(y)-0.5, padding=0)
        # Tabelas
        self._plfill(self.tbl_terr, m.integracao_territorial)
        if m.huff and not m.huff.hospital_attraction.is_empty():
            self._plfill(self.tbl_huff, m.huff.hospital_attraction)

        # =====================================================
        # Mapa concêntrico + filtros de agrupamento SIGTAP
        # =====================================================
        try:
            from papahr.dashboard.cnes_loader import get_cnes_data
            cnes_data = get_cnes_data().load()
            muni_df = cnes_data.municipios
            estab_df = cnes_data.estabelecimentos
            self._muni_df = muni_df
        except Exception as exc:
            log.warning("Não foi possível carregar CNES para o mapa: %s", exc)
            muni_df = pl.DataFrame()
            estab_df = pl.DataFrame()

        points: list[HospitalMapPoint] = []
        target_cnes = set(self._last_cnes or [])
        if estab_df is not None and not estab_df.is_empty() and "CO_CNES" in estab_df.columns:
            cols = [
                "CO_CNES", "NO_FANTASIA", "NO_RAZAO_SOCIAL",
                "NU_LATITUDE", "NU_LONGITUDE", "NU_LEITOS_SUS",
            ]
            avail = [c for c in cols if c in estab_df.columns]
            if len(avail) >= 5:
                filt = estab_df.select(avail)
                if target_cnes:
                    filt = filt.filter(pl.col("CO_CNES").cast(pl.Utf8).is_in(list(target_cnes)))
                for row in filt.to_dicts():
                    cnes = str(row.get("CO_CNES", ""))
                    nome = str(
                        row.get("NO_FANTASIA") or row.get("NO_RAZAO_SOCIAL") or cnes
                    )
                    try:
                        lat = float(row.get("NU_LATITUDE") or 0.0)
                        lon = float(row.get("NU_LONGITUDE") or 0.0)
                    except (TypeError, ValueError):
                        continue
                    if not lat or not lon:
                        continue
                    try:
                        leitos = float(row.get("NU_LEITOS_SUS") or 50.0)
                    except (TypeError, ValueError):
                        leitos = 50.0
                    points.append(HospitalMapPoint(
                        cnes=cnes, nome=nome, latitude=lat, longitude=lon, leitos=leitos,
                    ))

        # Ajusta o max_rate baseado nos dados
        if points and self._last_df is not None and not self._last_df.is_empty():
            # Estimativa rápida de faixa
            try:
                total_int = self._last_df.height
                if muni_df is not None and "NU_POPULACAO" in muni_df.columns and not muni_df.is_empty():
                    pop_total = float(muni_df["NU_POPULACAO"].cast(pl.Float64, strict=False).fill_null(0).sum() or 1)
                    taxa_geral = 1000.0 * total_int / pop_total
                    self.concentric_map.set_max_rate(max(10.0, taxa_geral * 2.5))
            except Exception:
                pass
        self.concentric_map.set_data(
            points,
            muni_df if (muni_df is not None and not muni_df.is_empty()) else pl.DataFrame(),
            self._last_df if self._last_df is not None else pl.DataFrame(),
        )

        # Popula combos de filtro
        self._rebuild_map_group_options()

        # =====================================================
        # Curvas de decaimento exponencial: popula combo hospital
        # =====================================================
        decay_df = m.distance_decay
        bench_df = m.benchmarking
        self.cbo_decay_hospital.blockSignals(True)
        self.cbo_decay_hospital.clear()
        if decay_df is not None and not decay_df.is_empty():
            # nome do hospital: se bench_df tem hospital_nome usa senão usa CNES
            nome_lookup: dict[str, str] = {}
            if bench_df is not None and not bench_df.is_empty() and "hospital_nome" in bench_df.columns:
                for r in bench_df.select(["hospital_cnes", "hospital_nome"]).unique(
                        subset=["hospital_cnes"], keep="first").iter_rows(named=True):
                    nome_lookup[str(r["hospital_cnes"])] = str(r["hospital_nome"] or "")
            # CNES únicos presentes em decay_df (ordem original)
            cnes_unicos: list[str] = []
            vistos: set[str] = set()
            for c in decay_df["hospital_cnes"].cast(pl.Utf8).to_list():
                if c in vistos:
                    continue
                vistos.add(c); cnes_unicos.append(c)
            for cnes in cnes_unicos:
                nome = nome_lookup.get(cnes, "").strip()
                label = f"{cnes} — {nome}" if nome else f"CNES {cnes}"
                self.cbo_decay_hospital.addItem(label, cnes)
        self.cbo_decay_hospital.blockSignals(False)
        # Dispara render inicial do primeiro hospital (se tiver)
        if self.cbo_decay_hospital.count() > 0:
            self.cbo_decay_hospital.setCurrentIndex(0)
        else:
            self._on_decay_hospital_changed(0)

    # ------------------------------------------------------------------
    def _fill_tab_trans(self, m: DashboardMetrics) -> None:
        df = m.transicao_cuidado
        if pg is not None and not df.is_empty():
            self.plot_trans.clear()
            linhas = df["linha_cuidado"].to_list()
            x = list(range(len(linhas)))
            contato = df["contato_48h_pct"].to_numpy().tolist() if "contato_48h_pct" in df.columns else [0]*len(linhas)
            readm = df["readmissao_30d_pct_est"].to_numpy().tolist() if "readmissao_30d_pct_est" in df.columns else [0]*len(linhas)
            # Dois grupos de barras (offset esquerda/direita)
            w = 0.38
            bg1 = pg.BarGraphItem(x=[i-w for i in x], height=contato, width=w,
                                  brush=_brush(_OK))
            bg2 = pg.BarGraphItem(x=[i+w for i in x], height=readm, width=w,
                                  brush=_brush(_DANG))
            self.plot_trans.addItem(bg1); self.plot_trans.addItem(bg2)
            ax = self.plot_trans.getAxis("bottom")
            ax.setTicks([(i, (c if len(c) < 24 else c[:22] + "…")) for i, c in enumerate(linhas)])
            ax.setStyle(tickTextHeight=110, autoExpandTextSpace=True)
            self.plot_trans.setXRange(-1, len(x), padding=0)
            self.plot_trans.setYRange(0, 100)
            legend = self.plot_trans.addLegend(offset=(20, 20), brush=None, labelTextColor=_TXT)
            # O addLegend só adiciona itens quando há gráficos plotados com nome; workaround:
            # usamos duas linhas pontilhadas "dummy"
            self.plot_trans.plot([], [], pen=_pen(_OK, 6), name="Contato 48h")
            self.plot_trans.plot([], [], pen=_pen(_DANG, 6), name="Readmissão 30d")
            legend = self.plot_trans.getPlotItem().legend
        self._plfill(self.tbl_trans, df)

    # ------------------------------------------------------------------
    def _fill_tab_geral(self, m: DashboardMetrics) -> None:
        ts = m.serie_temporal
        if pg is not None and not ts.is_empty() and ts.width >= 3:
            self.plot_ts.clear()
            cols = ts.columns
            label_x = f"{cols[0]}-{cols[1]}" if len(cols) >= 2 else ""
            n = ts["n_internacoes"].to_numpy().tolist()
            x = list(range(len(n)))
            # Labels bottom (col1-col2 concat)
            try:
                k1, k2 = cols[0], cols[1]
                labels = [f"{int(a)}-{int(b):02d}" for a, b in zip(ts[k1].to_list(), ts[k2].to_list())]
            except (TypeError, ValueError):
                labels = [str(i) for i in x]
            bg = pg.BarGraphItem(x=x, height=n, width=0.7, brush=_brush(_PRI))
            self.plot_ts.addItem(bg)
            if "taxa_icsap_pct" in ts.columns:
                y2 = ts["taxa_icsap_pct"].to_numpy().tolist()
                if y2 and max(y2) > 0:
                    # escala 0-100 em eixo 2
                    # (simples: escalona y2 para intervalo do mesmo gráfico usando eixo 2)
                    try:
                        self.plot_ts.showAxis("right")
                        self.plot_ts.getAxis("right").setPen(_pen(_WARN))
                        self.plot_ts.getAxis("right").setTextPen(_WARN)
                        self.plot_ts.setYRange(0, max(100.0, float(max(n) or 0) * 1.1), padding=0.0)
                        # Segundo plotitem com viewbox secundário? Simples aqui: usar escala normalizada
                        # Simplificação: plotar normalizado em % do max de n
                        mx = max(n) if n else 1
                        y2_sc = [(y / 100.0) * mx for y in y2]
                        self.plot_ts.plot(x, y2_sc, pen=_pen(_WARN, 2), name="Taxa ICSAP (%)")
                    except Exception:
                        pass
            if x and labels:
                self.plot_ts.getAxis("bottom").setTicks(
                    [(x[i], c) for i, c in enumerate(labels) if i % max(1, len(x)//10) == 0])
                self.plot_ts.setXRange(-0.5, len(x)-0.5, padding=0)
        # Alertas
        if m.alerts:
            df_alerts = pl.DataFrame({"#": list(range(1, len(m.alerts)+1)),
                                       "Alerta Operacional": m.alerts})
        else:
            df_alerts = pl.DataFrame({"#": ["1"], "Alerta Operacional": ["Nenhum alerta. Todos os indicadores dentro dos limites recomendados."]})
        self._plfill(self.tbl_alerts, df_alerts)
        # Top 10
        self._plfill(self.tbl_top10_p, m.top10_procedimentos)
        self._plfill(self.tbl_top10_e, m.top10_especialidades)

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
        self._refresh_filter_combo()
        mode = self.cbo_map_group.currentData() or "grupo"
        self.concentric_map.set_group_mode(mode, None)

    def _on_map_filter_changed(self, _idx: int) -> None:
        mode = self.cbo_map_group.currentData() or "grupo"
        try:
            val = self.cbo_map_filter.currentData()
        except Exception:
            val = None
        self.concentric_map.set_group_mode(mode, val)

    def _on_map_hospital_clicked(self, cnes: str) -> None:
        self.hospital_selected.emit(cnes)
        self.lbl_map_hover.set_status(f"🏥 Hospital selecionado — CNES {cnes}", "ok")
        # Seleciona aba Risco e destaca o hospital (se possível)
        self.tabs.setCurrentIndex(0)

    # ------------------------------------------------------------------
    def _plfill(self, table: QTableWidget, df: pl.DataFrame) -> None:
        table.clear()
        if df is None or df.is_empty():
            table.setRowCount(1); table.setColumnCount(1)
            table.setHorizontalHeaderLabels(["Status"])
            item = QTableWidgetItem("Nenhum dado disponível para exibição.")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setForeground(QBrush(QColor("#8F9EAB")))
            font = QFont()
            font.setItalic(True)
            item.setFont(font)
            table.setItem(0, 0, item)
            table.horizontalHeader().setStretchLastSection(True)
            return
        cols = [str(c) for c in df.columns]
        table.setColumnCount(len(cols))
        table.setHorizontalHeaderLabels(cols)
        rows = min(df.height, 500)
        table.setRowCount(rows)
        view = df.head(500)
        for r in range(rows):
            for c, col in enumerate(cols):
                v = view[col][r]
                if v is None:
                    s = ""
                elif isinstance(v, float):
                    if math.isfinite(v):
                        s = (f"{v:,.2f}" if abs(v) >= 1000 else f"{v:.4f}" if abs(v) < 1
                             else f"{v:.2f}").replace(",", "_").replace(".", ",").replace("_", ".")
                    else:
                        s = ""
                else:
                    s = str(v)
                item = QTableWidgetItem(s)
                # colorir colunas percentuais
                if ("pct" in col.lower() or "%" in col) and isinstance(v, (int, float)):
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

    # ------------------------------------------------------------------
    def _on_load_csv_parquet(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Selecionar arquivos CSV ou Parquet",
            str(Path.home()),
            "Dados (*.csv *.parquet *.pq)",
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
                elif ext == ".csv":
                    # FIX GAP-P0-2 MINIMO #1: CSV BR (DATASUS/GSPHosp) usa (; sep) + windows-1252/utf8-bom.
                    # Reutiliza CNESLoader._detect_csv_params (4 sep x 4 enc = 16 combinações).
                    from papahr.dashboard.cnes_loader import _detect_csv_params
                    auto_params = dict(_detect_csv_params(p))
                    # Depois de detectar sep/enc, habilita parse de datas + schema largo
                    auto_params.update(
                        try_parse_dates=True,
                        infer_schema_length=5000,
                        infer_schema=True,
                    )
                    try:
                        df = pl.read_csv(p, **auto_params)
                    except Exception as _e_csv1:
                        # Fallback leve (caso _detect_csv_params falhe):
                        # Tenta detectar sep por contagem bruta 64KB (CNESLoader fallback binario).
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

            # FIX GAP-P0-2 MINIMO #2: AUTO-DETECTA SISTEMA SUS via heurística de colunas.
            # Motivo: _last_system = SIHRD/SIAPA etc é obrigatório no _on_calculate p/ montar
            # coluna ano/mes e pesos clínicos. Sem isso, usa sistema do pipeline anterior (errado).
            cols_norm: set[str] = {c.strip().lower() for c in getattr(df_final, "columns", [])}
            def _has(*candidates: str) -> bool:
                return any(c.strip().lower() in cols_norm for c in candidates)
            if _has("n_aih","aih","cod_aih","no_aih","co_aih","valor_aprovado","val_tot","diag_princ"):
                auto_system = "SIH-RD"
            elif _has("pa","co_pa","apac","no_pa","valor_apresentado","vl_hospitalar","cod_procedimento","co_procedimento"):
                auto_system = "SIA-PA"
            elif _has("cnes_exec","cnes","no_fantasia","co_cnes") and _has("sp_proc","sp_valor","co_ato"):
                auto_system = "SIH-SP"
            elif _has("cnes_exec") and _has("rejeicao","mot_rejeit","co_mot_rejeicao"):
                auto_system = "SIH-RJ"
            else:
                # Fallback seguro: SIH-RD pois é padrão sus default
                auto_system = self._last_system or "SIH-RD"

            # FIX GAP-P0-2 MINIMO #3: USA API PUBLICA set_dataset() (NÃO raw self._last_df = df_final).
            # Motivo: set_dataset popula os 6 KPIs básicos IMEDIATAMENTE, seta cbo_sistema,
            # salva _last_system _last_cnes corretamente, e evita "usuário vê tudo 0" antes do worker.
            # Além disso: _fonte_atual combo 'session' é setado dentro do set_dataset se precisar?
            # → Não: set_dataset NÃO mexe no fonte. Nós ajustamos fonte ANTES:
            self._fonte_atual = "session"
            idx = self.cbo_fonte_dados.findData("session")
            if idx >= 0:
                self.cbo_fonte_dados.blockSignals(True)
                self.cbo_fonte_dados.setCurrentIndex(idx)
                self.cbo_fonte_dados.blockSignals(False)
            self.set_dataset(df_final, system=auto_system, cnes=None, compute_huff=None)
            resumo = f"{len(files)} arquivo(s) · {df_final.height():,.0f} linhas × {df_final.width()} colunas · {auto_system}"
            try:
                self.lbl_fonte_info.setText(f"✅ {resumo}")
            except Exception:
                pass
            self.lbl_status.set_status(f"✅ {resumo} — pronto para calcular", "ok")
            QApplication.processEvents()
            # _on_calculate agora será chamado? → set_dataset NÃO dispara cálculo (design original:
            # usuário confirma manualmente o compute). Mas refs/p3_dashboard README B1 pede "carregar
            # sem pipeline" — UX melhor: cálculo automático.
            self._on_calculate()
        except Exception as exc:
            log.exception("Erro ao importar CSV/Parquet no dashboard")
            self.lbl_status.set_status(f"❌ Erro ao carregar: {exc!r}", "error")
            QMessageBox.warning(
                self,
                "Erro ao carregar arquivos",
                f"Não foi possível carregar os arquivos selecionados.\n\n"
                f"Detalhe: {exc}\n\n"
                f"Dicas: verifique se o arquivo está aberto em outro programa, "
                f"ou se o CSV usa separadores padrão (; ou ,).",
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
                ("huff_catchment_profile.csv",   self._metrics.huff.catchment_profile if self._metrics.huff else pl.DataFrame()),
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
                    bg = pg.BarGraphItem(x=x, height=vals, width=0.72, brushes=brushes_list)
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
                    bg_serie = pg.BarGraphItem(x=xs, height=heights_bar, width=0.62, brush=_brush(_DANG))
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

    # ------------------------------------------------------------------
    # Benchmarking Hospitalar
    # ------------------------------------------------------------------
    _INDICADOR_PIOR_ALTO = {
        "icsap_pct", "los_medio_dias", "readmissao_30d_pct",
        "pct_alto", "custo_oe", "los_o_e", "lace_medio",
    }

    def _fill_tab_bench(self, m: DashboardMetrics) -> None:
        """Popula o combo de hospitais e dispara a renderização inicial."""
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
            self.cbo_bench_hospital.setCurrentIndex(0)
        else:
            self._on_bench_changed(0)

    def _on_bench_changed(self, _idx: int = 0) -> None:
        """Atualiza os gráficos e tabela de benchmarking conforme seleção."""
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
        # ---------- Gráfico de barras
        if pg is not None:
            self.plot_bench_bar.clear()
            vals = df_sorted[ind].cast(pl.Float64, strict=False).fill_nan(None).to_list()
            xs = list(range(len(vals)))
            # Cores de barra
            brushes_list = []
            max_v = max((v for v in vals if v is not None), default=1.0) or 1.0
            for i, v in enumerate(vals):
                if i == alvo_i:
                    brushes_list.append(QBrush(QColor(_OK)))
                    continue
                if v is None:
                    brushes_list.append(QBrush(QColor("#475569")))
                    continue
                t = min(1.0, float(v) / max_v) if max_v > 0 else 0.0
                if ind in self._INDICADOR_PIOR_ALTO:
                    color = _lerp_color(_OK, _DANG, t)
                else:  # CMI etc.: maior = complexo, então azul→roxo
                    color = _lerp_color("#818CF8", "#A855F7", t)
                brushes_list.append(QBrush(color))
            bg = pg.BarGraphItem(x=xs, height=vals, width=0.65, brushes=brushes_list)
            self.plot_bench_bar.addItem(bg)
            mediana = 0.0
            if vals and any(v is not None for v in vals):
                mediana = float(df_sorted[ind].cast(pl.Float64, strict=False).drop_nans().median() or 0.0)
                self.plot_bench_bar.addItem(
                    pg.InfiniteLine(pos=mediana, angle=0, pen=_pen(_WARN, 2),
                                     style=Qt.DashLine))
            # Labels do eixo X: nome CNES ou últimos 7
            def _short(t: str) -> str:
                tt = (t or "").strip()
                return tt if len(tt) <= 7 else tt[-7:]
            tick_eixo = [
                (_i, _short(str(c)))
                for _i, c in enumerate(df_sorted["hospital_cnes"].to_list())
                if _i % max(1, len(xs)//15) == 0
            ]
            self.plot_bench_bar.getAxis("bottom").setTicks([tick_eixo])
            self.plot_bench_bar.setXRange(-0.5, len(xs)-0.5, padding=0)
            self.plot_bench_bar.setTitle(
                f"Benchmark {self.cbo_bench_ind.currentText()} — {n_comp} hospitais (mediana {mediana:.2f} em amarelo)",
                color=_TXT, size="10pt")
            # ---------- Scatter (X=%ICSAP, Y=LOS medio)
            self.plot_bench_scatter.clear()
            col_x = "icsap_pct"
            col_y = "los_medio_dias"
            if col_x in df_sorted.columns and col_y in df_sorted.columns:
                scat = df_sorted.select([col_x, col_y, "hospital_cnes"])
                xs_all = scat[col_x].cast(pl.Float64, strict=False).fill_nan(None).to_list()
                ys_all = scat[col_y].cast(pl.Float64, strict=False).fill_nan(None).to_list()
                alvo_cnes_s = str(alvo_cnes)
                others_x, others_y, target_x, target_y = [], [], None, None
                for _i, _c in enumerate(scat["hospital_cnes"].to_list()):
                    xi = xs_all[_i]; yi = ys_all[_i]
                    if xi is None or yi is None or not math.isfinite(float(xi)) or not math.isfinite(float(yi)):
                        continue
                    if str(_c) == alvo_cnes_s:
                        target_x, target_y = float(xi), float(yi)
                    else:
                        others_x.append(float(xi)); others_y.append(float(yi))
                if others_x:
                    sp_o = pg.ScatterPlotItem(x=others_x, y=others_y, size=8,
                                               pen=_pen("#475569"), brush=_brush(_TXT_DIM))
                    self.plot_bench_scatter.addItem(sp_o)
                if target_x is not None and target_y is not None:
                    sp_t = pg.ScatterPlotItem(x=[target_x], y=[target_y], size=18,
                                               pen=_pen(_OK), brush=_brush(_OK))
                    self.plot_bench_scatter.addItem(sp_t)
                # Medianas tracejadas
                try:
                    med_x = float(df_sorted[col_x].cast(pl.Float64, strict=False).drop_nans().median() or 0.0)
                    med_y = float(df_sorted[col_y].cast(pl.Float64, strict=False).drop_nans().median() or 0.0)
                    self.plot_bench_scatter.addItem(pg.InfiniteLine(pos=med_x, angle=90, pen=_pen(_WARN, 1), style=Qt.DashLine))
                    self.plot_bench_scatter.addItem(pg.InfiniteLine(pos=med_y, angle=0, pen=_pen(_WARN, 1), style=Qt.DashLine))
                except Exception:
                    pass
                self.plot_bench_scatter.setLabel("bottom", "% ICSAP (evitáveis)")
                self.plot_bench_scatter.setLabel("left", "LOS médio (dias)")
        # ---------- Tabela
        colunas_uteis = [
            "hospital_cnes", "hospital_nome", "CO_UF", "CO_MUNICIPIO",
            "n_altas", ind, "icsap_pct", "los_medio_dias", "cmi", "lace_medio",
            "pct_alto", "readmissao_30d_pct", "custo_oe", "los_o_e",
        ]
        cols_existem = [c for c in colunas_uteis if c in df_sorted.columns]
        self._plfill(self.tbl_bench, df_sorted.select(cols_existem) if cols_existem else df_sorted)

    # ------------------------------------------------------------------
    # Curvas de decaimento exponencial
    # ------------------------------------------------------------------
    def _on_decay_hospital_changed(self, _idx: int = 0) -> None:
        m = self._metrics
        if m is None or m.distance_decay.is_empty():
            if pg is not None:
                self.plot_distance_decay.clear()
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
            self.plot_distance_decay.clear()
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

# -*- coding: utf-8 -*-
"""
Métodos defensivos e protegidos para o dashboard_page.py
"""

import math
import logging
import traceback
from typing import Any, List, Optional

import polars as pl
try:
    import pyqtgraph as pg
except ImportError:
    pg = None

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import (
    QTableWidget,
    QTableWidgetItem,
)

from papahr.dashboard import DashboardMetrics
from papahr.ui.widgets import HospitalMapPoint

log = logging.getLogger(__name__)

# Paleta base
_BG = "#162023"
_PRI = "#14B8A6"
_PRI_DARK = "#0D9488"
_WARN = "#F59E0B"
_DANG = "#EF4444"
_OK = "#22C55E"
_TXT = "#E6FFFA"
_TXT_DIM = "#94A3B8"
_LINE = "#233539"


# ---------------------------------------------------------------------------
# Helpers Defensivos de Cor e Estilo
# ---------------------------------------------------------------------------
def _brush(color: str) -> QBrush:
    return QBrush(QColor(color))


def _pen(color: str, width: int = 1, style: Qt.PenStyle = Qt.SolidLine) -> QPen:
    p = QPen(QColor(color), width)
    p.setStyle(style)
    return p


def _lerp_color(a: str, b: str, t: float) -> QColor:
    if t is None or not isinstance(t, (int, float)) or not math.isfinite(t):
        t = 0.0
    t = max(0.0, min(1.0, float(t)))
    ca = QColor(a)
    cb = QColor(b)
    return QColor(
        int(ca.red() + (cb.red() - ca.red()) * t),
        int(ca.green() + (cb.green() - ca.green()) * t),
        int(ca.blue() + (cb.blue() - ca.blue()) * t),
    )


def _color_for_pct(pct: float) -> QColor:
    """Para ICSAP/readmissão: verde ⇒ amarelo ⇒ vermelho (a prova de NaN/None)."""
    if pct is None or not isinstance(pct, (int, float)) or not math.isfinite(pct):
        return QColor(_TXT_DIM)
    pct = min(100.0, max(0.0, float(pct)))
    if pct < 15.0:
        t = pct / 15.0
        return _lerp_color(_OK, _PRI, t)
    if pct < 30.0:
        t = (pct - 15.0) / 15.0
        return _lerp_color(_PRI, _WARN, t)
    t = min(1.0, (pct - 30.0) / 30.0)
    return _lerp_color(_WARN, _DANG, t)


# ---------------------------------------------------------------------------
# Métodos de Preenchimento com Isolamento Total (Slots / UI)
# ---------------------------------------------------------------------------
def _plfill(self, table: QTableWidget, df: Optional[pl.DataFrame]) -> None:
    """Popula QTableWidget defensivamente contra NaNs, tipos incompatíveis e DataFrames vazios."""
    try:
        table.clear()
        if df is None or not isinstance(df, pl.DataFrame) or df.is_empty():
            table.setRowCount(0)
            table.setColumnCount(1)
            table.setHorizontalHeaderLabels(["Sem dados"])
            return

        cols = [str(c) for c in df.columns]
        table.setColumnCount(len(cols))
        table.setHorizontalHeaderLabels(cols)
        rows = min(df.height, 500)
        table.setRowCount(rows)
        view = df.head(500)

        for r in range(rows):
            for c, col in enumerate(cols):
                v = view[col][r]
                if v is None:
                    s = ""
                elif isinstance(v, float):
                    if math.isfinite(v):
                        s = (f"{v:,.2f}" if abs(v) >= 1000 else f"{v:.4f}" if abs(v) < 1
                             else f"{v:.2f}").replace(",", "_").replace(".", ",").replace("_", ".")
                    else:
                        s = "—"
                else:
                    s = str(v)

                item = QTableWidgetItem(s)
                # Colorir colunas de percentual sem estourar em NaNs
                if ("pct" in col.lower() or "%" in col) and isinstance(v, (int, float)) and math.isfinite(v):
                    try:
                        cor = _color_for_pct(float(v))
                        item.setBackground(QBrush(cor))
                        lum = (cor.red() * 0.299 + cor.green() * 0.587 + cor.blue() * 0.114)
                        item.setForeground(QBrush(Qt.white if lum < 140 else Qt.black))
                    except Exception:
                        item.setForeground(QBrush(QColor(_TXT)))
                else:
                    item.setForeground(QBrush(QColor(_TXT)))

                table.setItem(r, c, item)

        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
    except Exception as exc:
        log.error("Erro no preenchimento de tabela (_plfill): %s", exc, exc_info=True)


def _fill_kpis(self, m: DashboardMetrics) -> None:
    try:
        k = m.kpis or {}
        n_aihs = max(0, int(k.get("n_aihs_ou_apacs", 0)))
        dias_perm = float(k.get("dias_permanencia_total", 0.0) or 0.0)
        
        self.kpi_estabs.set_value(int(k.get("n_estabelecimentos", 0)))
        self.kpi_prod.set_value(n_aihs)
        self.kpi_valor.set_value(float(k.get("valor_total_R$", 0.0) or 0.0))
        self.kpi_icsap.set_value(float(k.get("icsap_pct", 0.0) or 0.0))
        self.kpi_los.set_value((dias_perm / n_aihs) if n_aihs > 0 else 0.0)
        self.kpi_readm.set_value(float(k.get("readmissao_30d_est_pct", 0.0) or 0.0))
    except Exception as exc:
        log.error("Erro ao preencher KPIs globais: %s", exc, exc_info=True)


def _fill_tab_risco(self, m: DashboardMetrics) -> None:
    try:
        df = m.estratificacao_risco
        if pg is not None:
            self.plot_estratos.clear()
            cat = ["ALTO", "MEDIO", "BAIXO"]
            vals = [0.0, 0.0, 0.0]

            if df is not None and not df.is_empty():
                for idx_c, c in enumerate(cat):
                    col = f"pct_{c}"
                    if col in df.columns:
                        mean_val = df[col].drop_nulls().mean()
                        if mean_val is not None and math.isfinite(float(mean_val)):
                            vals[idx_c] = float(mean_val)

            # Garante altura mínima visual (0.2) para que classes zeradas fiquem legíveis
            vals_vis = [max(v, 0.2) for v in vals]
            x = list(range(len(cat)))
            brushes = [_brush(_DANG), _brush(_WARN), _brush(_OK)]
            
            bg = pg.BarGraphItem(x=x, height=vals_vis, width=0.45, brushes=brushes)
            self.plot_estratos.addItem(bg)

            ax = self.plot_estratos.getAxis("bottom")
            ax.setTicks([[(i, c) for i, c in enumerate(cat)]])
            self.plot_estratos.setXRange(-0.6, len(cat) - 0.4, padding=0)
            
            max_v = max(vals) if vals else 0.0
            upper_y = max(100.0, max_v * 1.15) if max_v > 0 else 100.0
            self.plot_estratos.setYRange(0, upper_y, padding=0)
            self.plot_estratos.setTitle(
                f"Estratos de Risco — ALTO: {vals[0]:.1f}% | MÉDIO: {vals:.1f}% | BAIXO: {vals:.1f}%",
                color=_TXT, size="10pt"
            )

        # Plot CMI por Hospital
        if pg is not None:
            self.plot_cmi.clear()
            df_ef = m.eficiencia_clinica
            if df_ef is not None and not df_ef.is_empty() and "case_mix_index" in df_ef.columns:
                cmi_raw = df_ef["case_mix_index"].to_numpy().tolist()
                cnes_list = df_ef["cnes"].to_list() if "cnes" in df_ef.columns else []
                valid_pts = [
                    (i, float(v), str(cnes_list[i]) if i < len(cnes_list) else str(i))
                    for i, v in enumerate(cmi_raw)
                    if v is not None and math.isfinite(float(v))
                ]
                if valid_pts:
                    xs = [p[0] for p in valid_pts]
                    ys = [p[1] for p in valid_pts]
                    sp = pg.ScatterPlotItem(x=xs, y=ys, size=9, pen=_pen(_PRI_DARK), brush=_brush(_PRI))
                    self.plot_cmi.addItem(sp)

                    step = max(1, len(xs) // 12)
                    ticks = [
                        (xs[i], (valid_pts[i][-5:] if len(valid_pts[i]) > 5 else valid_pts[i]))
                        for i in range(0, len(xs), step)
                    ]
                    self.plot_cmi.getAxis("bottom").setTicks([ticks])
                    self.plot_cmi.setXRange(-0.5, max(xs) + 0.5, padding=0)

                    avg = float(sum(ys) / len(ys)) if ys else 0.0
                    ln = pg.InfiniteLine(pos=avg, angle=0, pen=_pen(_WARN, 2))
                    self.plot_cmi.addItem(ln)
                    max_cmi = max(ys) if ys else 1.0
                    self.plot_cmi.setYRange(0, max(2.0, max_cmi * 1.2), padding=0)
                    self.plot_cmi.setTitle(f"Case Mix Index (CMI) — Média: {avg:.3f}", color=_TXT, size="10pt")
                else:
                    self.plot_cmi.setXRange(-0.5, 0.5)
                    self.plot_cmi.setYRange(0, 1.0)
                    self.plot_cmi.setTitle("Case Mix Index (CMI) — Sem dados", color=_TXT, size="10pt")

        self._plfill(self.tbl_risco, df if df is not None else pl.DataFrame())
    except Exception as exc:
        log.error("Erro na aba Risco (_fill_tab_risco): %s", exc, exc_info=True)


def _fill_tab_efic(self, m: DashboardMetrics) -> None:
    try:
        df = m.eficiencia_clinica
        if pg is not None:
            self.plot_los.clear()
            self.plot_custo.clear()
            if df is not None and not df.is_empty():
                labels = df["cnes"].cast(pl.Utf8).to_list() if "cnes" in df.columns else []

                def _pct_safe(val: float) -> float:
                    try:
                        return 50.0 if val < 1.0 else min(100.0, 50.0 + float(val - 1.0) * 50.0)
                    except Exception:
                        return 50.0

                # 1. LOS O/E
                if "los_o_e" in df.columns:
                    oe_raw = df["los_o_e"].to_numpy().tolist()
                    oe = [float(v) if v is not None and math.isfinite(float(v)) else 0.0 for v in oe_raw]
                    if oe:
                        x = list(range(len(oe)))
                        colors = [_color_for_pct(_pct_safe(v)).name() for v in oe]
                        heights = [max(v, 0.05) for v in oe]
                        bg = pg.BarGraphItem(x=x, height=heights, width=0.35,
                                             brushes=[QBrush(QColor(c)) for c in colors])
                        self.plot_los.addItem(bg)
                        self.plot_los.addItem(pg.InfiniteLine(pos=1.0, angle=0, pen=_pen(_WARN, 2, Qt.DashLine)))

                        step = max(1, len(x) // 12)
                        ticks = [
                            (x[i], (labels[i][-5:] if len(labels[i]) > 5 else labels[i]))
                            for i in range(0, len(x), step) if i < len(labels)
                        ]
                        self.plot_los.getAxis("bottom").setTicks([ticks])
                        self.plot_los.setXRange(-0.5, len(x) - 0.5, padding=0)
                        max_los = max(oe) if oe else 1.0
                        self.plot_los.setYRange(0, max(1.5, max_los * 1.15), padding=0)

                # 2. Custo O/E
                if "custo_o_e" in df.columns:
                    ce_raw = df["custo_o_e"].to_numpy().tolist()
                    ce = [float(v) if v is not None and math.isfinite(float(v)) else 0.0 for v in ce_raw]
                    if ce:
                        x = list(range(len(ce)))
                        colors_c = [_color_for_pct(_pct_safe(v)).name() for v in ce]
                        heights_c = [max(v, 0.05) for v in ce]
                        bg_c = pg.BarGraphItem(x=x, height=heights_c, width=0.35,
                                               brushes=[QBrush(QColor(c)) for c in colors_c])
                        self.plot_custo.addItem(bg_c)
                        self.plot_custo.addItem(pg.InfiniteLine(pos=1.0, angle=0, pen=_pen(_WARN, 2, Qt.DashLine)))

                        step = max(1, len(x) // 12)
                        ticks_c = [
                            (x[i], (labels[i][-5:] if len(labels[i]) > 5 else labels[i]))
                            for i in range(0, len(x), step) if i < len(labels)
                        ]
                        self.plot_custo.getAxis("bottom").setTicks([ticks_c])
                        self.plot_custo.setXRange(-0.5, len(x) - 0.5, padding=0)
                        max_ce = max(ce) if ce else 1.0
                        self.plot_custo.setYRange(0, max(1.5, max_ce * 1.15), padding=0)

        self._plfill(self.tbl_efic, df if df is not None else pl.DataFrame())
    except Exception as exc:
        log.error("Erro na aba Eficiência (_fill_tab_efic): %s", exc, exc_info=True)


def _fill_tab_terr(self, m: DashboardMetrics) -> None:
    try:
        # ICSAP por condição
        if pg is not None:
            self.plot_icsap.clear()
            if m.icsap_por_condicao is not None and not m.icsap_por_condicao.is_empty():
                conds = m.icsap_por_condicao["condicao_evitavel"].to_list()
                ns_raw = m.icsap_por_condicao["n_internacoes"].to_numpy().tolist()
                ns = [float(v) if v is not None and math.isfinite(float(v)) else 0.0 for v in ns_raw]
                y = list(reversed(ns))
                labels = list(reversed(conds))
                m_y = max(y) if y and max(y) > 0 else 1.0

                def _cor_pct(val: float, my: float) -> QColor:
                    try:
                        base = 50.0 - 50.0 * (val / my) if my > 0 else 50.0
                        return _color_for_pct(min(100.0, max(0.0, base)))
                    except Exception:
                        return _color_for_pct(50.0)

                brushes = [QBrush(_cor_pct(val, m_y)) for val in y]
                heights = [max(val, 0.1) for val in y]
                x = list(range(len(y)))
                bg = pg.BarGraphItem(x=x, height=heights, width=0.45, brushes=brushes)
                self.plot_icsap.addItem(bg)

                ticks = [(i, c if len(c) < 26 else c[:24] + "…") for i, c in enumerate(labels)]
                self.plot_icsap.getAxis("bottom").setTicks([ticks])
                ax = self.plot_icsap.getAxis("bottom")
                ax.setStyle(tickTextHeight=110, autoExpandTextSpace=True)
                self.plot_icsap.setXRange(-0.5, len(y) - 0.5, padding=0)
                self.plot_icsap.setYRange(0, m_y * 1.15, padding=0)

        # Tabelas de Território e Huff
        self._plfill(self.tbl_terr, m.integracao_territorial if m.integracao_territorial is not None else pl.DataFrame())
        if m.huff and m.huff.hospital_attraction is not None and not m.huff.hospital_attraction.is_empty():
            self._plfill(self.tbl_huff, m.huff.hospital_attraction)
        else:
            self._plfill(self.tbl_huff, pl.DataFrame())

        # Mapa Concêntrico e Geocódigo
        muni_df = pl.DataFrame()
        estab_df = pl.DataFrame()
        try:
            from papahr.dashboard.cnes_loader import get_cnes_data
            cnes_data = get_cnes_data().load()
            muni_df = cnes_data.municipios
            estab_df = cnes_data.estabelecimentos
            self._muni_df = muni_df
        except Exception as exc:
            log.warning("CNES indisponível para renderização do mapa: %s", exc)

        points: list[HospitalMapPoint] = []
        target_cnes = set(self._last_cnes or [])
        if estab_df is not None and not estab_df.is_empty() and "CO_CNES" in estab_df.columns:
            cols = ["CO_CNES", "NO_FANTASIA", "NO_RAZAO_SOCIAL", "NU_LATITUDE", "NU_LONGITUDE", "NU_LEITOS_SUS"]
            avail = [c for c in cols if c in estab_df.columns]
            if len(avail) >= 5:
                filt = estab_df.select(avail)
                if target_cnes:
                    filt = filt.filter(pl.col("CO_CNES").cast(pl.Utf8).is_in(list(target_cnes)))
                for row in filt.to_dicts():
                    cnes = str(row.get("CO_CNES", ""))
                    nome = str(row.get("NO_FANTASIA") or row.get("NO_RAZAO_SOCIAL") or cnes)
                    try:
                        lat = float(row.get("NU_LATITUDE") or 0.0)
                        lon = float(row.get("NU_LONGITUDE") or 0.0)
                    except (TypeError, ValueError):
                        continue
                    if not lat or not lon or not math.isfinite(lat) or not math.isfinite(lon):
                        continue
                    try:
                        leitos = float(row.get("NU_LEITOS_SUS") or 50.0)
                    except (TypeError, ValueError):
                        leitos = 50.0
                    points.append(HospitalMapPoint(cnes=cnes, nome=nome, latitude=lat, longitude=lon, leitos=leitos))

        if points and self._last_df is not None and not self._last_df.is_empty():
            try:
                total_int = self._last_df.height
                if muni_df is not None and "NU_POPULACAO" in muni_df.columns and not muni_df.is_empty():
                    pop_total = float(muni_df["NU_POPULACAO"].cast(pl.Float64, strict=False).fill_null(0).sum() or 1)
                    taxa_geral = 1000.0 * total_int / pop_total
                    self.concentric_map.set_max_rate(max(10.0, taxa_geral * 2.5))
            except Exception:
                pass

        try:
            self.concentric_map.set_data(
                points,
                muni_df if (muni_df is not None and not muni_df.is_empty()) else pl.DataFrame(),
                self._last_df if self._last_df is not None else pl.DataFrame(),
            )
        except Exception as _exc_map:
            log.warning("Falha ao desenhar mapa concêntrico: %s", _exc_map)

        self._rebuild_map_group_options()

        # Decaimento Exponencial
        decay_df = m.distance_decay
        bench_df = m.benchmarking
        self.cbo_decay_hospital.blockSignals(True)
        self.cbo_decay_hospital.clear()
        if decay_df is not None and not decay_df.is_empty():
            nome_lookup: dict[str, str] = {}
            if bench_df is not None and not bench_df.is_empty() and "hospital_nome" in bench_df.columns:
                for r in bench_df.select(["hospital_cnes", "hospital_nome"]).unique(
                        subset=["hospital_cnes"], keep="first").iter_rows(named=True):
                    nome_lookup[str(r["hospital_cnes"])] = str(r["hospital_nome"] or "")
            cnes_unicos: list[str] = []
            vistos: set[str] = set()
            for c in decay_df["hospital_cnes"].cast(pl.Utf8).to_list():
                if c not in vistos:
                    vistos.add(c)
                    cnes_unicos.append(c)
            for cnes in cnes_unicos:
                nome = nome_lookup.get(cnes, "").strip()
                label = f"{cnes} — {nome}" if nome else f"CNES {cnes}"
                self.cbo_decay_hospital.addItem(label, cnes)
        self.cbo_decay_hospital.blockSignals(False)

        if self.cbo_decay_hospital.count() > 0:
            self.cbo_decay_hospital.setCurrentIndex(0)
        else:
            self._on_decay_hospital_changed(0)
    except Exception as exc:
        log.error("Erro na aba Território (_fill_tab_terr): %s", exc, exc_info=True)


def _fill_tab_trans(self, m: DashboardMetrics) -> None:
    try:
        df = m.transicao_cuidado
        if pg is not None:
            self.plot_trans.clear()
            if df is not None and not df.is_empty() and "linha_cuidado" in df.columns:
                linhas = df["linha_cuidado"].to_list()
                x = list(range(len(linhas)))
                contato_raw = df["contato_48h_pct"].to_numpy().tolist() if "contato_48h_pct" in df.columns else [0] * len(linhas)
                readm_raw = df["readmissao_30d_pct_est"].to_numpy().tolist() if "readmissao_30d_pct_est" in df.columns else [0] * len(linhas)

                contato = [float(v) if v is not None and math.isfinite(float(v)) else 0.0 for v in contato_raw]
                readm = [float(v) if v is not None and math.isfinite(float(v)) else 0.0 for v in readm_raw]

                contato_vis = [max(v, 0.1) for v in contato]
                readm_vis = [max(v, 0.1) for v in readm]

                w = 0.35
                bg1 = pg.BarGraphItem(x=[i - w / 2 for i in x], height=contato_vis, width=w, brush=_brush(_OK))
                bg2 = pg.BarGraphItem(x=[i + w / 2 for i in x], height=readm_vis, width=w, brush=_brush(_DANG))
                self.plot_trans.addItem(bg1)
                self.plot_trans.addItem(bg2)

                ax = self.plot_trans.getAxis("bottom")
                ticks = [(i, (c if len(c) < 24 else c[:22] + "…")) for i, c in enumerate(linhas)]
                ax.setTicks([ticks])
                ax.setStyle(tickTextHeight=110, autoExpandTextSpace=True)
                self.plot_trans.setXRange(-0.8, len(x) - 0.2, padding=0)
                max_val = max(contato + readm) if (contato or readm) else 0.0
                self.plot_trans.setYRange(0, max(100.0, max_val * 1.15), padding=0)

                self.plot_trans.addLegend(offset=(20, 20), brush=None, labelTextColor=_TXT)
                self.plot_trans.plot([], [], pen=_pen(_OK, 6), name="Contato 48h")
                self.plot_trans.plot([], [], pen=_pen(_DANG, 6), name="Readmissão 30d")

        self._plfill(self.tbl_trans, df if df is not None else pl.DataFrame())
    except Exception as exc:
        log.error("Erro na aba Transição (_fill_tab_trans): %s", exc, exc_info=True)


def _fill_tab_geral(self, m: DashboardMetrics) -> None:
    try:
        ts = m.serie_temporal
        if pg is not None:
            self.plot_ts.clear()
            if ts is not None and not ts.is_empty() and ts.width >= 3:
                cols = ts.columns
                n_raw = ts["n_internacoes"].to_numpy().tolist() if "n_internacoes" in cols else []
                n = [float(v) if v is not None and math.isfinite(float(v)) else 0.0 for v in n_raw]
                n_vis = [max(v, 0.1) for v in n]
                x = list(range(len(n)))
                try:
                    k1, k2 = cols[0], cols
                    labels = [f"{int(a)}-{int(b):02d}" for a, b in zip(ts[k1].to_list(), ts[k2].to_list())]
                except Exception:
                    labels = [str(i) for i in x]

                bg = pg.BarGraphItem(x=x, height=n_vis, width=0.4, brush=_brush(_PRI))
                self.plot_ts.addItem(bg)

                if "taxa_icsap_pct" in ts.columns:
                    y2_raw = ts["taxa_icsap_pct"].to_numpy().tolist()
                    y2 = [float(v) if v is not None and math.isfinite(float(v)) else 0.0 for v in y2_raw]
                    if y2 and any(v > 0 for v in y2):
                        try:
                            self.plot_ts.showAxis("right")
                            self.plot_ts.getAxis("right").setPen(_pen(_WARN))
                            self.plot_ts.getAxis("right").setTextPen(_WARN)
                            mx_n = max(n) if n and max(n) > 0 else 1.0
                            y2_sc = [(y / 100.0) * mx_n for y in y2]
                            self.plot_ts.plot(x, y2_sc, pen=_pen(_WARN, 2), name="Taxa ICSAP (%)")
                        except Exception:
                            pass

                if x and labels:
                    step = max(1, len(x) // 10)
                    self.plot_ts.getAxis("bottom").setTicks([[(x[i], labels[i]) for i in range(0, len(x), step)]])
                    self.plot_ts.setXRange(-0.5, len(x) - 0.5, padding=0)
                    max_n = max(n) if n else 1.0
                    self.plot_ts.setYRange(0, max_n * 1.15, padding=0)

        if m.alerts:
            df_alerts = pl.DataFrame({"#": list(range(1, len(m.alerts) + 1)), "Alerta Operacional": m.alerts})
        else:
            df_alerts = pl.DataFrame({"#": ["1"], "Alerta Operacional": ["Nenhum alerta. Indicadores dentro dos limites operacionais."]})

        self._plfill(self.tbl_alerts, df_alerts)
        self._plfill(self.tbl_top10_p, m.top10_procedimentos if m.top10_procedimentos is not None else pl.DataFrame())
        self._plfill(self.tbl_top10_e, m.top10_especialidades if m.top10_especialidades is not None else pl.DataFrame())
    except Exception as exc:
        log.error("Erro na aba Geral (_fill_tab_geral): %s", exc, exc_info=True)


def _fill_tab_glosas(self, m: DashboardMetrics) -> None:
    try:
        causas_df = m.glosas_causas if m.glosas_causas is not None else pl.DataFrame()
        serie_df = m.glosas_serie_temporal if m.glosas_serie_temporal is not None else pl.DataFrame()
        kpi_total = m.kpis.get("n_aihs_ou_apacs", 0.0) or 0.0

        if causas_df.is_empty() and serie_df.is_empty():
            self.kpi_glosas_n.set_value(0)
            self.kpi_glosas_pct.set_value(0.0)
            self.kpi_glosas_val.set_value(0.0)
            self.kpi_glosas_top.set_value("Sem dados de glosa no dataset")
            if pg is not None:
                self.plot_glosas_causas.clear()
                self.plot_glosas_serie.clear()
            self._plfill(self.tbl_glosas_causas, pl.DataFrame({"Informação": ["Nenhuma glosa detectada no dataset."]}))
            self._plfill(self.tbl_glosas_serie, pl.DataFrame({"Informação": ["Série histórica de glosas indisponível."]}))
            return

        n_glosas = 0
        pct_glosas = 0.0
        valor_glosado = 0.0
        top_causa = "—"

        if not serie_df.is_empty():
            col_n_gl = "N glosadas"
            col_vl_gl = "Valor glosado (R$)"
            if col_n_gl in serie_df.columns:
                n_glosas = int(serie_df[col_n_gl].cast(pl.Int64, strict=False).fill_null(0).sum() or 0)
                if kpi_total > 0:
                    pct_glosas = 100.0 * n_glosas / float(kpi_total)
            if col_vl_gl in serie_df.columns:
                valor_glosado = float(serie_df[col_vl_gl].cast(pl.Float64, strict=False).fill_null(0).sum() or 0.0)

        if not causas_df.is_empty():
            col_causa = "Motivo / Descrição" if "Motivo / Descrição" in causas_df.columns else "Código Glosa"
            col_n = "Nº AIHs/APACs afetadas"
            if col_causa in causas_df.columns and col_n in causas_df.columns:
                linha0 = causas_df.select([col_causa, col_n]).sort(col_n, descending=True).head(1)
                if linha0.height:
                    top_causa_val = str(linha0[col_causa][0] or "").strip()
                    top_causa = top_causa_val if len(top_causa_val) <= 60 else top_causa_val[:57] + "…"

        self.kpi_glosas_n.set_value(n_glosas)
        self.kpi_glosas_pct.set_value(pct_glosas)
        self.kpi_glosas_val.set_value(valor_glosado)
        self.kpi_glosas_top.set_value(top_causa)

        # Plot Causas
        if pg is not None:
            self.plot_glosas_causas.clear()
            if not causas_df.is_empty():
                col_n = "Nº AIHs/APACs afetadas"
                col_pct = "% sobre todas as glosas"
                col_lbl = "Motivo / Descrição" if "Motivo / Descrição" in causas_df.columns else "Código Glosa"
                if col_n in causas_df.columns and col_lbl in causas_df.columns:
                    top = causas_df.sort(col_n, descending=True).head(15).reverse()
                    vals = [float(v) if v is not None and math.isfinite(float(v)) else 0.0
                            for v in top[col_n].cast(pl.Float64, strict=False).fill_null(0).to_list()]
                    labels_full = top[col_lbl].to_list()
                    pcts = [float(p) if p is not None and math.isfinite(float(p)) else 0.0
                            for p in (top[col_pct].cast(pl.Float64, strict=False).fill_null(0).to_list() if col_pct in top.columns else vals)]

                    if vals:
                        x = list(range(len(vals)))
                        max_p = max(pcts) if pcts and max(pcts) > 0 else 1.0
                        brushes_list = [_brush(_lerp_color(_OK, _DANG, min(1.0, p / max_p)).name()) for p in pcts]
                        bg = pg.BarGraphItem(x=x, height=[max(v, 0.1) for v in vals], width=0.4, brushes=brushes_list)
                        self.plot_glosas_causas.addItem(bg)

                        ticks = [(i, str(labels_full[i])[:22] + "…" if len(str(labels_full[i])) > 22 else str(labels_full[i])) for i in range(len(labels_full))]
                        self.plot_glosas_causas.getAxis("bottom").setTicks([ticks])
                        self.plot_glosas_causas.getAxis("bottom").setStyle(tickTextHeight=110, autoExpandTextSpace=True)
                        self.plot_glosas_causas.setXRange(-0.5, len(x) - 0.5, padding=0)
                        mx = max(vals) if vals else 1.0
                        self.plot_glosas_causas.setYRange(0, float(mx) * 1.18, padding=0)
                        self.plot_glosas_causas.setTitle(f"Top {len(vals)} causas de glosa", color=_TXT, size="10pt")

            # Plot Série Histórica
            self.plot_glosas_serie.clear()
            if not serie_df.is_empty():
                x_labels: list[str] = []
                xs: list[int] = []
                heights_bar: list[float] = []
                line_vals: list[float] = []
                col_n_gl = "N glosadas"
                col_pct_vl = "% valor glosado $"
                col_vl_gl = "Valor glosado (R$)"

                for idx, r in enumerate(serie_df.to_dicts()):
                    xs.append(idx)
                    if "Competência (AAAAMM)" in serie_df.columns:
                        x_labels.append(str(r.get("Competência (AAAAMM)") or ""))
                    elif "Ano" in serie_df.columns and "Mês" in serie_df.columns:
                        try:
                            x_labels.append(f"{int(r.get('Ano'))}-{int(r.get('Mês')):02d}")
                        except Exception:
                            x_labels.append(str(idx))
                    else:
                        x_labels.append(str(idx))

                    v_bar = r.get(col_n_gl) if col_n_gl in serie_df.columns else 0.0
                    heights_bar.append(float(v_bar) if v_bar is not None and math.isfinite(float(v_bar)) else 0.0)

                    pct_v = r.get(col_pct_vl) if col_pct_vl in serie_df.columns else None
                    if pct_v is not None and math.isfinite(float(pct_v)):
                        line_vals.append(float(pct_v))
                    else:
                        v_vl = r.get(col_vl_gl) if col_vl_gl in serie_df.columns else 0.0
                        line_vals.append(float(v_vl) if v_vl is not None and math.isfinite(float(v_vl)) else 0.0)

                if heights_bar and xs:
                    bg_serie = pg.BarGraphItem(x=xs, height=[max(v, 0.1) for v in heights_bar], width=0.35, brush=_brush(_DANG))
                    self.plot_glosas_serie.addItem(bg_serie)

                    if line_vals and any(v > 0 for v in line_vals):
                        is_pct = (col_pct_vl in serie_df.columns)
                        max_bar = max(heights_bar) if max(heights_bar) > 0 else 1.0
                        max_line = max(line_vals) if max(line_vals) > 0 else 1.0
                        sc = (max_bar / 100.0) if is_pct and max_line <= 101.0 else (max_bar / max_line)
                        line_scaled = [v * sc for v in line_vals]

                        self.plot_glosas_serie.plot(xs, line_scaled, pen=_pen(_WARN, 3), name="% valor glosado $")
                        try:
                            self.plot_glosas_serie.showAxis("right")
                            self.plot_glosas_serie.getAxis("right").setPen(_pen(_WARN))
                            self.plot_glosas_serie.getAxis("right").setTextPen(_WARN)
                            max_sc = max(line_scaled) if line_scaled else 1.0
                            ratio = (max_line / max_sc) if max_sc > 0 else 1.0
                            self.plot_glosas_serie.getAxis("right").setScale(ratio)
                        except Exception:
                            pass

                    passo = max(1, len(xs) // 14)
                    ticks_s = [(xs[i], x_labels[i]) for i in range(0, len(xs), passo)]
                    self.plot_glosas_serie.getAxis("bottom").setTicks([ticks_s])
                    self.plot_glosas_serie.getAxis("bottom").setStyle(tickTextHeight=90, autoExpandTextSpace=True)
                    self.plot_glosas_serie.setXRange(-0.5, xs[-1] + 0.5, padding=0)
                    mx = max(heights_bar) if heights_bar else 1.0
                    self.plot_glosas_serie.setYRange(0, float(mx) * 1.25, padding=0)

        self._plfill(self.tbl_glosas_causas, causas_df if not causas_df.is_empty() else pl.DataFrame({"Info": ["Sem dados"]}))
        self._plfill(self.tbl_glosas_serie, serie_df if not serie_df.is_empty() else pl.DataFrame({"Info": ["Sem dados"]}))
    except Exception as exc:
        log.error("Erro na aba Glosas (_fill_tab_glosas): %s", exc, exc_info=True)


def _on_bench_changed(self, _idx: int = 0) -> None:
    try:
        m = self._metrics
        if m is None or m.benchmarking is None or m.benchmarking.is_empty():
            self.lbl_bench_summary.setText("Hospital: —— | Sem dados de benchmarking.")
            if pg is not None:
                self.plot_bench_bar.clear()
                self.plot_bench_scatter.clear()
            self._plfill(self.tbl_bench, pl.DataFrame({"Info": ["Nenhum dado de benchmarking disponível."]}))
            return

        df = m.benchmarking
        alvo_cnes = str(self.cbo_bench_hospital.currentData() or "")
        nivel_chave = self.cbo_bench_level.currentData() or "nivel_uf"
        ind = self.cbo_bench_ind.currentData() or "icsap_pct"

        linha_alvo = df.filter(pl.col("hospital_cnes") == alvo_cnes)
        if linha_alvo.is_empty():
            return

        alvo_chave = str(linha_alvo[nivel_chave][0] or "")
        df_comp = df.filter(pl.col(nivel_chave) == alvo_chave) if alvo_chave else df.clone()
        if ind in df_comp.columns:
            df_comp = df_comp.filter(pl.col(ind).is_not_null())

        n_comp = df_comp.height
        if n_comp == 0:
            return

        sort_desc = ind in self._INDICADOR_PIOR_ALTO
        df_sorted = df_comp.sort(by=ind, descending=sort_desc)
        ordered_cnes = df_sorted["hospital_cnes"].to_list()
        alvo_i = ordered_cnes.index(alvo_cnes) if alvo_cnes in ordered_cnes else None

        self.lbl_bench_summary.setText(
            f"Hospital: {self.cbo_bench_hospital.currentText()} | Comparando com {n_comp} hospitais."
        )

        if pg is not None:
            self.plot_bench_bar.clear()
            vals_raw = df_sorted[ind].cast(pl.Float64, strict=False).fill_nan(None).to_list()
            vals = [float(v) if v is not None and math.isfinite(float(v)) else 0.0 for v in vals_raw]
            xs = list(range(len(vals)))

            max_v = max(vals) if vals and max(vals) > 0 else 1.0
            brushes_list = []
            for i, v in enumerate(vals):
                if i == alvo_i:
                    brushes_list.append(QBrush(QColor(_OK)))
                else:
                    t = min(1.0, v / max_v)
                    color = _lerp_color(_OK, _DANG, t) if ind in self._INDICADOR_PIOR_ALTO else _lerp_color("#818CF8", "#A855F7", t)
                    brushes_list.append(QBrush(color))

            bg = pg.BarGraphItem(x=xs, height=[max(v, 0.05) for v in vals], width=0.35, brushes=brushes_list)
            self.plot_bench_bar.addItem(bg)

            tick_eixo = [
                (i, (str(c)[-7:] if len(str(c)) > 7 else str(c)))
                for i, c in enumerate(df_sorted["hospital_cnes"].to_list())
                if i % max(1, len(xs) // 15) == 0
            ]
            self.plot_bench_bar.getAxis("bottom").setTicks([tick_eixo])
            self.plot_bench_bar.setXRange(-0.5, len(xs) - 0.5 if xs else 0.5, padding=0)
            self.plot_bench_bar.setYRange(0, max_v * 1.15, padding=0)

            # Scatter
            self.plot_bench_scatter.clear()
            col_x, col_y = "icsap_pct", "los_medio_dias"
            if col_x in df_sorted.columns and col_y in df_sorted.columns:
                xs_all = df_sorted[col_x].cast(pl.Float64, strict=False).fill_nan(None).to_list()
                ys_all = df_sorted[col_y].cast(pl.Float64, strict=False).fill_nan(None).to_list()
                others_x, others_y, target_x, target_y = [], [], None, None

                for i, c in enumerate(df_sorted["hospital_cnes"].to_list()):
                    xi, yi = xs_all[i], ys_all[i]
                    if xi is None or yi is None or not math.isfinite(float(xi)) or not math.isfinite(float(yi)):
                        continue
                    if str(c) == alvo_cnes:
                        target_x, target_y = float(xi), float(yi)
                    else:
                        others_x.append(float(xi))
                        others_y.append(float(yi))

                if others_x:
                    self.plot_bench_scatter.addItem(pg.ScatterPlotItem(x=others_x, y=others_y, size=8, pen=_pen("#475569"), brush=_brush(_TXT_DIM)))
                if target_x is not None and target_y is not None:
                    self.plot_bench_scatter.addItem(pg.ScatterPlotItem(x=[target_x], y=[target_y], size=18, pen=_pen(_OK), brush=_brush(_OK)))

        colunas_uteis = ["hospital_cnes", "hospital_nome", "CO_UF", "n_altas", ind, "icsap_pct", "los_medio_dias", "cmi"]
        cols_existem = [c for c in colunas_uteis if c in df_sorted.columns]
        self._plfill(self.tbl_bench, df_sorted.select(cols_existem) if cols_existem else df_sorted)
    except Exception as exc:
        log.error("Erro no benchmarking (_on_bench_changed): %s", exc, exc_info=True)


def _on_decay_hospital_changed(self, _idx: int = 0) -> None:
    try:
        m = self._metrics
        if m is None or m.distance_decay is None or m.distance_decay.is_empty():
            if pg is not None:
                self.plot_distance_decay.clear()
            self.lbl_decay_params.setText("Ajuste exponencial: y = A · exp(−λ · km)   λ = ——   R² = ——")
            self._plfill(self.tbl_distance_decay, pl.DataFrame({"Observação": ["Sem curvas de decaimento disponíveis."]}))
            return

        cnes = self.cbo_decay_hospital.currentData()
        if not cnes:
            return

        df = m.distance_decay.filter(pl.col("hospital_cnes") == str(cnes))
        if df.is_empty():
            return

        A = float(df["A_exp"][0]) if "A_exp" in df.columns and df.height else 0.0
        lam = float(df["lambda_exp"][0]) if "lambda_exp" in df.columns and df.height else 0.0
        R2 = float(df["R2_exp"][0]) if "R2_exp" in df.columns and df.height else 0.0
        dmp = float(df["dmp_km"][0]) if "dmp_km" in df.columns and df.height else 0.0

        self.lbl_decay_params.setText(
            f"Ajuste exponencial: y = {A:.2f} · exp(−{lam:.4f} · km) | λ = {lam:.4f} | R² = {R2:.3f} | DMP = {dmp:.1f} km"
        )

        if pg is not None:
            self.plot_distance_decay.clear()
            mid_km = df["faixa_mid_km"].cast(pl.Float64).to_list()
            taxa = df["taxa_por_1000"].cast(pl.Float64).to_list()

            xs_obs = [float(x) for x, y in zip(mid_km, taxa) if y is not None and math.isfinite(float(y))]
            ys_obs = [float(y) for x, y in zip(mid_km, taxa) if y is not None and math.isfinite(float(y))]

            if xs_obs:
                sp = pg.ScatterPlotItem(x=xs_obs, y=ys_obs, size=12, symbol="o", pen=_pen(_WARN), brush=_brush(_WARN))
                self.plot_distance_decay.addItem(sp)

            if lam >= 0 and A > 0:
                n_pts = 100
                xs_curve = [80.0 * (i / (n_pts - 1)) for i in range(n_pts)]
                ys_curve = [A * math.exp(-lam * xx) for xx in xs_curve]
                self.plot_distance_decay.plot(xs_curve, ys_curve, pen=_pen(_PRI, 3), name="Ajuste exp.")

            self.plot_distance_decay.setXRange(0, 82, padding=0)
            max_y = max(ys_obs + [A * 1.05]) if ys_obs else 1.0
            if max_y <= 0 or not math.isfinite(max_y):
                max_y = 1.0
            self.plot_distance_decay.setYRange(0, float(max_y) * 1.15, padding=0)

        cols_tab = ["faixa_km_label", "pop_anel", "n_internacoes", "taxa_por_1000"]
        cols_show = [c for c in cols_tab if c in df.columns]
        self._plfill(self.tbl_distance_decay, df.select(cols_show) if cols_show else df)
    except Exception as exc:
        log.error("Erro no decaimento exponencial (_on_decay_hospital_changed): %s", exc, exc_info=True)"""Página 4 do Dashboard: Saúde Populacional e Dinâmica Territorial.

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
from pathlib import Path
from typing import Any

import polars as pl

try:
    import pyqtgraph as pg
    from pyqtgraph import PlotWidget
except ImportError:  # pragma: no cover
    pg = None
    PlotWidget = Any  # type: ignore

from PySide6.QtCore import QSize, Qt, QThread, Signal
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
    QSplitter,
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
    HospitalMapPoint,
    KpiCard,
    StatusBadge,
)

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


def _pen(color: str, width: int = 1) -> QPen:
    return QPen(QColor(color), width)


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
        # Fonte de dados da camada (GOLD_OTIMIZADO ou SILVER_FALLBACK)
        self._fonte_dados_camada: str = ""
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.setObjectName("DashboardPage")
        # =============================================================
        # LAYOUT DE SOBREVIVENCIA: ENVELOPAMOS TODO O DASHBOARD EM UM
        # QScrollArea VERTICAL. Assim, se a tela do usuario for MENOR
        # que o minimo do app, ele SCROLLA e NUNCA HAVERA SOBREPOSICAO
        # DE WIDGETS (que e o que voce estava vendo).
        # =============================================================
        root_outer = QVBoxLayout(self)
        root_outer.setContentsMargins(0, 0, 0, 0)
        root_outer.setSpacing(0)

        scroll_vert = QScrollArea()
        scroll_vert.setWidgetResizable(True)
        scroll_vert.setFrameShape(QFrame.Shape.NoFrame)
        scroll_vert.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_vert.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_vert.setStyleSheet(
            "QScrollArea{background:#0F1A1C;}"
            "QScrollBar:vertical{width:10px;background:#121C1E;border-radius:5px;margin:4px 2px;}"
            "QScrollBar::handle:vertical{background:#14B8A6;border-radius:5px;min-height:40px;}"
            "QScrollBar:horizontal{height:10px;background:#121C1E;border-radius:5px;margin:2px 4px;}"
            "QScrollBar::handle:horizontal{background:#14B8A6;border-radius:5px;min-width:40px;}"
            "QScrollBar::add-line,QScrollBar::sub-line{height:0px;width:0px;}"
        )
        host = QWidget()
        host.setStyleSheet("#DashHost{background:#0F1A1C;}")
        host.setObjectName("DashHost")
        root = QVBoxLayout(host)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # =============================================================
        # HEADER: QGridLayout 4 x 2 (nao min-width fixo!)
        # ESPACO E DISTRIBUIDO PELO QT, SEM SOBREPOR.
        # Col 0 (esq, stretch 3) = Titulo / Sistema / Huff / Beta
        # Col 1 (dir, stretch 4) = BOTOES CTA (grid 2x2 word wrap!)
        # =============================================================
        head_frame = QFrame()
        head_frame.setStyleSheet(
            "QFrame{background:#121C1E;border-radius:12px;border:1px solid #233539;}"
        )
        gl_head = QGridLayout(head_frame)
        gl_head.setContentsMargins(16, 16, 16, 16)
        gl_head.setSpacing(16)
        gl_head.setColumnStretch(0, 3)
        gl_head.setColumnStretch(1, 4)

        # --- COLUNA 0: Controles basicos (empilham verticalmente) --
        title = QLabel("📊 Gestão de Saúde Populacional")
        title.setStyleSheet(
            "font-size:16px; font-weight:700; color:#4FD1C5; padding:2px 0px;"
        )
        title.setMinimumHeight(24)
        gl_head.addWidget(title, 0, 0, 1, 1)

        row_sys = QHBoxLayout()
        row_sys.setSpacing(6)
        lbl_s = QLabel("🧪 Sistema:")
        lbl_s.setStyleSheet("color:#CBD5E1;font-weight:600;min-height:26px;")
        self.cbo_system = QComboBox()
        self.cbo_system.addItem("SIH-RD (AIHs Aprovadas — padrão)", "SIH_RD")
        self.cbo_system.addItem("SIA-PA (Ambulatorial / APACs)", "SIA_PA")
        self.cbo_system.addItem("SIH-RJ (Rejeitadas)", "SIH_RJ")
        self.cbo_system.addItem("SIH-SP (Serviços Profissionais)", "SIH_SP")
        self.cbo_system.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cbo_system.setMinimumContentsLength(24)
        self.cbo_system.setStyleSheet(
            "QComboBox{min-height:34px;padding:4px 10px;"
            "background:#162023;color:#E6FFFA;border:1px solid #2D4A4A;border-radius:8px;"
            "font-weight:600;font-size:9.5pt;selection-background-color:#4FD1C5;selection-color:#0F1A1C;}"
            "QComboBox::drop-down{border:none;width:24px;}"
            "QComboBox QAbstractItemView{background:#121C1E;color:#E6FFFA;selection-background-color:#14B8A6;font-size:9.5pt;}"
        )
        row_sys.addWidget(lbl_s, 0)
        row_sys.addWidget(self.cbo_system, 1)
        gl_head.addLayout(row_sys, 1, 0, 1, 1)

        self.chk_huff = QCheckBox("☑ Mapa Huff (Cobertura Gravitacional)")
        self.chk_huff.setChecked(True)
        self.chk_huff.setStyleSheet(
            "QCheckBox{min-height:30px;padding:2px 4px;font-weight:600;color:#94A3B8;font-size:9.5pt;}"
            "QCheckBox::indicator{width:16px;height:16px;border-radius:4px;}"
        )
        gl_head.addWidget(self.chk_huff, 2, 0, 1, 1)

        row_beta = QHBoxLayout()
        row_beta.setSpacing(6)
        self.lbl_beta = QLabel("β decay:")
        self.lbl_beta.setStyleSheet("padding:0 4px;color:#94A3B8;font-weight:600;min-height:26px;")
        self.cbo_beta = QComboBox()
        self.cbo_beta.addItem("2.2 — Ambulatorial (PSF/UBS)", 2.2)
        self.cbo_beta.addItem("1.5 — Média (Clínica, Urgência)", 1.5)
        self.cbo_beta.addItem("0.9 — Alta Complexidade (Onco, Cirurgia)", 0.9)
        self.cbo_beta.setCurrentIndex(1)
        self.cbo_beta.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cbo_beta.setMinimumContentsLength(28)
        self.cbo_beta.setStyleSheet(
            "QComboBox{min-height:34px;padding:4px 10px;"
            "background:#162023;color:#E6FFFA;border:1px solid #2D4A4A;border-radius:8px;"
            "font-weight:600;font-size:9.5pt;selection-background-color:#4FD1C5;selection-color:#0F1A1C;}"
            "QComboBox::drop-down{border:none;width:24px;}"
            "QComboBox QAbstractItemView{background:#121C1E;color:#E6FFFA;selection-background-color:#14B8A6;font-size:9.5pt;}"
        )
        row_beta.addWidget(self.lbl_beta, 0)
        row_beta.addWidget(self.cbo_beta, 1)
        gl_head.addLayout(row_beta, 3, 0, 1, 1)

        # --- COLUNA 1: BOTOES CTA em GRID 2x2 ---
        _cta_padrao = (
            "QPushButton{{border-radius:10px;font-weight:700;font-size:9.5pt;"
            "padding:8px 10px;min-height:44px;white-space:pre-line;}}"
            "QPushButton:hover{{filter:brightness(1.08);}}"
            "QPushButton:disabled{{background:#2D4A4A;color:#64748B;border:1px solid #2D4A4A;}}"
        )
        grid_cta = QGridLayout()
        grid_cta.setSpacing(6)
        grid_cta.setContentsMargins(0, 0, 0, 0)
        grid_cta.setColumnStretch(0, 1)
        grid_cta.setColumnStretch(1, 1)
        grid_cta.setRowStretch(0, 1)
        grid_cta.setRowStretch(1, 1)

        self.btn_calc = QPushButton("🔄 Calcular\nDashboard")
        self.btn_calc.clicked.connect(self._on_calculate)
        self.btn_calc.setStyleSheet(
            _cta_padrao +
            "QPushButton{background:linear-gradient(90deg,#4FD1C5,#14B8A6);color:#000000;"
            "border:2px solid #5EEAD4;}"
        )
        self.btn_calc.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        grid_cta.addWidget(self.btn_calc, 0, 0)

        self.btn_load_csv_parquet = QPushButton("📂 Carregar CSV /\nParquet")
        self.btn_load_csv_parquet.clicked.connect(self._on_load_csv_parquet)
        self.btn_load_csv_parquet.setStyleSheet(
            _cta_padrao +
            "QPushButton{background:#14B8A6;color:#000000;border:2px solid #0D9488;}"
        )
        self.btn_load_csv_parquet.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        grid_cta.addWidget(self.btn_load_csv_parquet, 0, 1)

        self.btn_export = QPushButton("📤 Exportar 12\nCSVs")
        self.btn_export.clicked.connect(self._on_export)
        self.btn_export.setEnabled(False)
        self.btn_export.setStyleSheet(
            _cta_padrao +
            "QPushButton{background:#121C1E;color:#E6FFFA;border:2px solid #2D4A4A;}"
        )
        self.btn_export.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        grid_cta.addWidget(self.btn_export, 1, 0)

        self.btn_reload_fonte = QPushButton("🔄 Recarregar\nFonte")
        self.btn_reload_fonte.clicked.connect(self._on_reload_fonte_clicked)
        self.btn_reload_fonte.setStyleSheet(
            _cta_padrao +
            "QPushButton{background:#0F766E;color:#E6FFFA;border:2px solid #115E59;}"
        )
        self.btn_reload_fonte.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        grid_cta.addWidget(self.btn_reload_fonte, 1, 1)

        # Container col1: StatusBadge + CTAs
        col1_v = QVBoxLayout()
        col1_v.setSpacing(6)
        self.lbl_status = StatusBadge("Aguardando dataset", status="info")
        self.lbl_status.setStyleSheet(
            self.lbl_status.styleSheet() + "QLabel{min-height:28px;font-size:9.5pt;padding:4px 10px;}"
        )
        self.lbl_status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        col1_v.addWidget(self.lbl_status, 0)
        col1_v.addLayout(grid_cta, 1)
        gl_head.addLayout(col1_v, 0, 1, 4, 1)  # ocupa 4 linhas (mesma altura col0)

        root.addWidget(head_frame, 0)

        # =============================================================
        # LINHA FONTE DOS DADOS (combo fonte + combo carga + info)
        # =============================================================
        fonte_frame = QFrame()
        fonte_frame.setStyleSheet(
            "QFrame{background:#121C1E;border-radius:12px;border:1px solid #233539;}"
        )
        ffl = QGridLayout(fonte_frame)
        ffl.setContentsMargins(16, 12, 16, 12)
        ffl.setSpacing(16)
        ffl.setColumnStretch(1, 3)
        ffl.setColumnStretch(3, 3)
        ffl.setColumnStretch(4, 2)

        lbl_f = QLabel("📦 Fonte:")
        lbl_f.setStyleSheet("color:#94A3B8;font-weight:600;min-height:26px;")
        self.cbo_fonte_dados = QComboBox()
        self.cbo_fonte_dados.addItem("🟢 Sessão Atual (pipeline agora)", "session")
        self.cbo_fonte_dados.addItem("💾 DuckDB Persistidos (cargas salvas)", "duckdb")
        self.cbo_fonte_dados.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cbo_fonte_dados.setMinimumContentsLength(30)
        self.cbo_fonte_dados.setStyleSheet(
            "QComboBox{min-height:34px;padding:4px 10px;"
            "background:#162023;color:#E6FFFA;border:1px solid #2D4A4A;border-radius:8px;"
            "font-weight:600;font-size:9.5pt;selection-background-color:#4FD1C5;selection-color:#0F1A1C;}"
            "QComboBox::drop-down{border:none;width:24px;}"
            "QComboBox QAbstractItemView{background:#121C1E;color:#E6FFFA;selection-background-color:#14B8A6;font-size:9.5pt;}"
        )
        self.cbo_fonte_dados.currentIndexChanged.connect(self._on_fonte_dados_changed)
        ffl.addWidget(lbl_f, 0, 0)
        ffl.addWidget(self.cbo_fonte_dados, 0, 1)

        lbl_r = QLabel("🗂 Carga:")
        lbl_r.setStyleSheet("color:#94A3B8;font-weight:600;min-height:26px;")
        self.cbo_load_runs = QComboBox()
        self.cbo_load_runs.addItem("⏳ Todas as cargas (todas runs)", None)
        self.cbo_load_runs.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cbo_load_runs.setMinimumContentsLength(28)
        self.cbo_load_runs.setStyleSheet(
            "QComboBox{min-height:34px;padding:4px 10px;"
            "background:#162023;color:#E6FFFA;border:1px solid #2D4A4A;border-radius:8px;"
            "font-weight:600;font-size:9.5pt;selection-background-color:#4FD1C5;selection-color:#0F1A1C;}"
            "QComboBox::drop-down{border:none;width:24px;}"
            "QComboBox QAbstractItemView{background:#121C1E;color:#E6FFFA;selection-background-color:#14B8A6;font-size:9.5pt;}"
        )
        self.cbo_load_runs.setEnabled(False)
        ffl.addWidget(lbl_r, 0, 2)
        ffl.addWidget(self.cbo_load_runs, 0, 3)

        self.lbl_fonte_info = QLabel("Fonte: sessão atual | 0 linhas")
        self.lbl_fonte_info.setStyleSheet(
            "color:#8FA6A3;font-weight:600;padding:4px 10px;min-height:26px;font-size:9pt;"
        )
        self.lbl_fonte_info.setWordWrap(True)
        self.lbl_fonte_info.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        ffl.addWidget(self.lbl_fonte_info, 0, 4)

        root.addWidget(fonte_frame, 0)

        # =============================================================
        # KPIs: QGridLayout 2 x 3 (2 linhas / 3 colunas)
        # CADA CARD PREENCHE A CÉLULA. NUNCA MAIS SCROLL HORIZONTAL!
        # =============================================================
        self.kpi_estabs  = KpiCard("Estabelecimentos", 0, icon="🏥", accent=_PRI)
        self.kpi_prod    = KpiCard("Nº AIHs / APACs", 0, icon="📄", accent="#6366F1")
        self.kpi_valor   = KpiCard("Valor Total (R$)", 0, icon="💸", accent="#F59E0B")
        self.kpi_icsap   = KpiCard("% ICSAP (evitáveis)", 0.0, icon="⚠️", accent=_WARN, format="{:.1f} %")
        self.kpi_los     = KpiCard("LOS médio (dias)", 0.0, icon="🛏️", accent="#818CF8", format="{:.2f}")
        self.kpi_readm   = KpiCard("Readmissão 30d estimada", 0.0, icon="↩️", accent=_DANG, format="{:.1f} %")

        kpi_grid = QGridLayout()
        kpi_grid.setContentsMargins(0, 0, 0, 0)
        kpi_grid.setSpacing(16)
        kpi_grid.setColumnStretch(0, 1)
        kpi_grid.setColumnStretch(1, 1)
        kpi_grid.setColumnStretch(2, 1)
        kpi_grid.setRowStretch(0, 1)
        kpi_grid.setRowStretch(1, 1)
        kpi_grid.addWidget(self.kpi_estabs, 0, 0)
        kpi_grid.addWidget(self.kpi_prod,   0, 1)
        kpi_grid.addWidget(self.kpi_valor,  0, 2)
        kpi_grid.addWidget(self.kpi_icsap,  1, 0)
        kpi_grid.addWidget(self.kpi_los,    1, 1)
        kpi_grid.addWidget(self.kpi_readm,  1, 2)

        kpi_frame = QFrame()
        kpi_frame.setLayout(kpi_grid)
        kpi_frame.setStyleSheet("QFrame{background:transparent;}")
        root.addWidget(kpi_frame, 0)

        # =============================================================
        # Abas: min-width reduzido, fonte 9.5pt
        # =============================================================
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabBar::tab{background:#162023;color:#94A3B8;padding:8px 12px;border:1px solid #233539;
                         border-top-left-radius:10px;border-top-right-radius:10px;margin-right:3px;
                         font-weight:600;font-size:9pt;min-width:140px;max-width:170px;}
            QTabBar::tab:selected{background:#1B2B2F;color:#4FD1C5;border-bottom:2px solid #4FD1C5;}
            QTabWidget::pane{border:1px solid #233539;border-radius:10px;top:-1px;}
            QTabWidget QWidget{background:#0F1A1C;}""")
        self.tabs.setIconSize(QSize(0, 0))
        self.tabs.addTab(self._build_tab_risco(),   "🎯 Risco")
        self.tabs.addTab(self._build_tab_efic(),    "💎 Eficiência")
        self.tabs.addTab(self._build_tab_terr(),    "🗺️ Território")
        self.tabs.addTab(self._build_tab_trans(),   "🔁 Transição")
        self.tabs.addTab(self._build_tab_geral(),   "🧭 Visão Geral")
        self.tabs.addTab(self._build_tab_bench(),   "🏁 Benchmarking")
        self.tabs.addTab(self._build_tab_glosas(),  "💸 Glosas")
        root.addWidget(self.tabs, 1)

        scroll_vert.setWidget(host)
        root_outer.addWidget(scroll_vert, 1)

        footer_frame = QFrame()
        footer_frame.setStyleSheet(
            "QFrame{background:#0D1719;border-top:1px solid #233539;}"
        )
        footer_hl = QHBoxLayout(footer_frame)
        footer_hl.setContentsMargins(10, 6, 10, 6)
        footer_hl.setSpacing(10)
        self._badge_fonte_camada = StatusBadge("Camada: —", status="info")
        self._badge_fonte_camada.setStyleSheet(
            self._badge_fonte_camada.styleSheet()
            + "QLabel{min-height:26px;font-size:9pt;padding:3px 10px;}"
        )
        self._badge_fonte_camada.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        footer_hl.addWidget(self._badge_fonte_camada, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        footer_hl.addStretch(1)
        root_outer.addWidget(footer_frame, 0)

    # ------------------------------------------------------------------
    # Abas
    # ------------------------------------------------------------------
    def _build_tab_risco(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(16)
        self.plot_estratos = self._mk_plot_widget("Estratos de Risco — % Casos")
        self.plot_cmi = self._mk_plot_widget("Case Mix Index (CMI) por Hospital")
        self.tbl_risco = self._mk_table()
        spl = QSplitter(Qt.Vertical)
        spl.setChildrenCollapsible(False)
        spl.setHandleWidth(4)
        spl.addWidget(self.plot_estratos); spl.addWidget(self.plot_cmi); spl.addWidget(self.tbl_risco)
        spl.setSizes([180, 180, 300])
        l.addWidget(spl, 1)
        return w

    def _build_tab_efic(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(16)
        self.plot_los = self._mk_plot_widget("Tempo Permanência: Observado vs Esperado (O/E)")
        self.plot_custo = self._mk_plot_widget("Custo Médio x Benchmark (O/E)")
        self.tbl_efic = self._mk_table()
        spl = QSplitter(Qt.Vertical)
        spl.setChildrenCollapsible(False)
        spl.setHandleWidth(4)
        spl.addWidget(self.plot_los); spl.addWidget(self.plot_custo); spl.addWidget(self.tbl_efic)
        spl.setSizes([180, 180, 300])
        l.addWidget(spl, 1)
        return w

    def _build_tab_terr(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(16)

        # -----------------------------
        # Barra de filtros do mapa
        # -----------------------------
        filter_bar = QFrame()
        filter_bar.setStyleSheet(
            "QFrame{background:#121C1E;border-radius:10px;border:1px solid #233539;}"
        )
        fb_l = QHBoxLayout(filter_bar)
        fb_l.setContentsMargins(16, 12, 16, 12)
        fb_l.setSpacing(16)

        lbl_map_tip = QLabel("🗺️ Mapa de anéis concêntricos (taxa internação / 1000 hab)")
        lbl_map_tip.setStyleSheet("color:#4FD1C5;font-weight:700;")
        fb_l.addWidget(lbl_map_tip, 1)

        fb_l.addWidget(QLabel("Agrupar por:"))
        self.cbo_map_group = QComboBox()
        self.cbo_map_group.addItem("Grupo SIGTAP", "grupo")
        self.cbo_map_group.addItem("Forma de Organização", "forma")
        self.cbo_map_group.addItem("Procedimento (código/nome)", "procedimento")
        self.cbo_map_group.setStyleSheet("min-width:220px;padding:6px 10px;")
        self.cbo_map_group.currentIndexChanged.connect(self._on_map_group_changed)
        fb_l.addWidget(self.cbo_map_group)

        fb_l.addWidget(QLabel("Filtrar item:"))
        self.cbo_map_filter = QComboBox()
        self.cbo_map_filter.addItem("🔎 Todos (agregado geral)", None)
        self.cbo_map_filter.setStyleSheet("min-width:300px;padding:6px 10px;")
        self.cbo_map_filter.currentIndexChanged.connect(self._on_map_filter_changed)
        fb_l.addWidget(self.cbo_map_filter, 1)

        self.lbl_map_hover = StatusBadge("Selecione um hospital no mapa", status="info")
        fb_l.addWidget(self.lbl_map_hover)

        l.addWidget(filter_bar)

        # -----------------------------
        # Splitter principal: mapa + ICSAP
        # -----------------------------
        spl_top = QSplitter(Qt.Horizontal)
        spl_top.setChildrenCollapsible(False)
        spl_top.setHandleWidth(4)

        # Mapa concêntrico
        self.concentric_map = ConcentricRingsMap()
        self.concentric_map.hospital_clicked.connect(self._on_map_hospital_clicked)
        spl_top.addWidget(self.concentric_map)

        # ICSAP por condição (mantido)
        self.plot_icsap = self._mk_plot_widget("Internações por Condição Sensível à APS (ICSAP)")
        spl_top.addWidget(self.plot_icsap)
        spl_top.setSizes([720, 400])
        l.addWidget(spl_top, 2)

        # -----------------------------
        # Tabelas inferiores
        # -----------------------------
        self.tbl_terr = self._mk_table()
        self.tbl_huff = self._mk_table()
        h2 = QHBoxLayout()
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
        dbl = QHBoxLayout(decay_bar)
        dbl.setContentsMargins(16, 12, 16, 12); dbl.setSpacing(16)
        dbl.addWidget(QLabel("📉 Curva Decaimento — Hosp:"))
        self.cbo_decay_hospital = QComboBox()
        self.cbo_decay_hospital.setStyleSheet("min-width:360px;padding:6px 10px;")
        self.cbo_decay_hospital.currentIndexChanged.connect(self._on_decay_hospital_changed)
        dbl.addWidget(self.cbo_decay_hospital, 1)
        self.lbl_decay_params = QLabel(
            "Ajuste exponencial: y = A · exp(−λ · km)   λ = ——   R² = ——   DMP (km) = ——")
        self.lbl_decay_params.setStyleSheet("color:#4FD1C5;font-weight:600;")
        dbl.addWidget(self.lbl_decay_params, 2)
        l.addWidget(decay_bar)

        self.tbl_distance_decay = self._mk_table()
        self.plot_distance_decay = self._mk_plot_widget(
            "Curva de Decaimento: Taxa Internação / 1000 hab × Distância (km)"
        )
        spl_decay = QSplitter(Qt.Horizontal)
        spl_decay.setChildrenCollapsible(False)
        spl_decay.setHandleWidth(4)
        spl_decay.addWidget(self.plot_distance_decay)
        spl_decay.addWidget(self.tbl_distance_decay)
        spl_decay.setSizes([620, 520])
        l.addWidget(spl_decay, 2)

        return w

    def _build_tab_trans(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(16)
        self.plot_trans = self._mk_plot_widget("% Contato 48h (verde) vs % Readmissão 30d (vermelho)")
        self.tbl_trans = self._mk_table()
        spl = QSplitter(Qt.Vertical)
        spl.setChildrenCollapsible(False)
        spl.setHandleWidth(4)
        spl.addWidget(self.plot_trans); spl.addWidget(self.tbl_trans)
        spl.setSizes([260, 400])
        l.addWidget(spl, 1)
        return w

    def _build_tab_geral(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(16)
        self.plot_ts = self._mk_plot_widget("Série Temporal — Internações (barra) e Taxa ICSAP (linha)")
        self.tbl_alerts = self._mk_table()
        self.tbl_top10_p = self._mk_table()
        self.tbl_top10_e = self._mk_table()
        h = QHBoxLayout()
        h.addWidget(self._mk_group("🚨 Alertas Operacionais", self.tbl_alerts), 1)
        h.addWidget(self._mk_group("🥇 Top-10 Procedimentos", self.tbl_top10_p), 1)
        h.addWidget(self._mk_group("🏆 Top-10 Especialidades/CBO", self.tbl_top10_e), 1)
        spl = QSplitter(Qt.Vertical)
        spl.setChildrenCollapsible(False)
        spl.setHandleWidth(4)
        spl.addWidget(self.plot_ts)
        w2 = QWidget(); w2.setLayout(h); spl.addWidget(w2)
        spl.setSizes([260, 380])
        l.addWidget(spl, 1)
        return w

    def _build_tab_bench(self) -> QWidget:
        """Nova aba: Benchmarking Hospitalar (1 hospital escolhido vs 4 níveis geográficos)."""
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(16)

        # Barra superior de seleção
        sel_bar = QFrame()
        sel_bar.setStyleSheet(
            "QFrame{background:#121C1E;border-radius:10px;border:1px solid #233539;}")
        sbl = QHBoxLayout(sel_bar)
        sbl.setContentsMargins(12, 10, 12, 10); sbl.setSpacing(14)
        sbl.addWidget(QLabel("🏥 Hospital selecionado:"))
        self.cbo_bench_hospital = QComboBox()
        self.cbo_bench_hospital.setStyleSheet("min-width:460px;padding:7px 10px;")
        self.cbo_bench_hospital.currentIndexChanged.connect(self._on_bench_changed)
        sbl.addWidget(self.cbo_bench_hospital, 2)

        sbl.addWidget(QLabel("🌐 Nível geográfico comparativo:"))
        self.cbo_bench_level = QComboBox()
        self.cbo_bench_level.addItem("1. Mesmo Município (ibge 6-7 dígitos)", "nivel_municipio")
        self.cbo_bench_level.addItem("2. Microrregião (5 dígitos IBGE)", "nivel_micro")
        self.cbo_bench_level.addItem("3. Macrorregião (3 dígitos IBGE)", "nivel_macro")
        self.cbo_bench_level.addItem("4. Mesmo Estado (UF — 2 dígitos)", "nivel_uf")
        self.cbo_bench_level.setCurrentIndex(3)   # default: UF (maior amostra)
        self.cbo_bench_level.setStyleSheet("min-width:340px;padding:7px 10px;")
        self.cbo_bench_level.currentIndexChanged.connect(self._on_bench_changed)
        sbl.addWidget(self.cbo_bench_level, 2)

        sbl.addWidget(QLabel("📈 Indicador principal:"))
        self.cbo_bench_ind = QComboBox()
        self.cbo_bench_ind.addItem("% ICSAP (evitáveis)", "icsap_pct")
        self.cbo_bench_ind.addItem("LOS médio (dias)", "los_medio_dias")
        self.cbo_bench_ind.addItem("CMI (complexidade)", "cmi")
        self.cbo_bench_ind.addItem("LACE médio (risco readmissão)", "lace_medio")
        self.cbo_bench_ind.addItem("% Readmissão ≤30d (est.)", "readmissao_30d_pct")
        self.cbo_bench_ind.addItem("Custo O/E (eficiência)", "custo_oe")
        self.cbo_bench_ind.addItem("LOS O/E (tempo x benchmark)", "los_o_e")
        self.cbo_bench_ind.addItem("% Casos ALTO risco", "pct_alto")
        self.cbo_bench_ind.setStyleSheet("min-width:300px;padding:7px 10px;")
        self.cbo_bench_ind.currentIndexChanged.connect(self._on_bench_changed)
        sbl.addWidget(self.cbo_bench_ind, 2)

        self.lbl_bench_summary = QLabel(
            "Hospital: ——   Nível geográfico: ——   Comparando com —— hospitais.")
        self.lbl_bench_summary.setStyleSheet("color:#4FD1C5;font-weight:700;")
        sbl.addStretch(1)
        sbl.addWidget(self.lbl_bench_summary, 3)
        l.addWidget(sel_bar)

        # 2 gráficos + tabela
        self.plot_bench_bar = self._mk_plot_widget(
            "Benchmark: Barras ordenadas do indicador selecionado"
        )
        self.plot_bench_scatter = self._mk_plot_widget(
            "Scatter: efeito conjunto (X=% ICSAP, Y=LOS médio dias) — o hospital selecionado é a cor VERDE"
        )
        self.tbl_bench = self._mk_table()
        spl_top = QSplitter(Qt.Horizontal)
        spl_top.setChildrenCollapsible(False)
        spl_top.setHandleWidth(4)
        spl_top.addWidget(self.plot_bench_bar)
        spl_top.addWidget(self.plot_bench_scatter)
        spl_top.setSizes([600, 600])
        spl_full = QSplitter(Qt.Vertical)
        spl_full.setChildrenCollapsible(False)
        spl_full.setHandleWidth(4)
        spl_full.addWidget(spl_top)
        spl_full.addWidget(self.tbl_bench)
        spl_full.setSizes([300, 420])
        l.addWidget(spl_full, 1)

        return w

    def _build_tab_glosas(self) -> QWidget:
        """Nova aba: Glosas e Não Pagamentos (causas + série histórica)."""
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 16, 16, 16); l.setSpacing(16)

        # Header com KPIs rápidos de glosa — Grid 2x2 p/ não sobrepor em 1366x768
        kpi_grid = QGridLayout(); kpi_grid.setSpacing(8)
        kpi_grid.setColumnStretch(0, 1); kpi_grid.setColumnStretch(1, 1)
        kpi_grid.setRowStretch(0, 1); kpi_grid.setRowStretch(1, 1)
        self.kpi_glosas_n = KpiCard("AIHs/APACs glosadas", 0, icon="💸", accent=_DANG, format="{:,}")
        self.kpi_glosas_pct = KpiCard("% de internações glosadas", 0.0, icon="⚠️", accent=_WARN, format="{:.2f} %")
        self.kpi_glosas_val = KpiCard("Valor total glosado (R$)", 0.0, icon="💰", accent="#F97316", format="R$ {:,.2f}")
        self.kpi_glosas_top = KpiCard("1ª causa de glosa", "—", icon="🔍", accent=_PRI, format="{}")
        _kpis_glosas = [self.kpi_glosas_n, self.kpi_glosas_pct, self.kpi_glosas_val, self.kpi_glosas_top]
        for idx, k in enumerate(_kpis_glosas):
            k.setMinimumHeight(90)
            k.setMaximumHeight(140)
            r, c = divmod(idx, 2)
            kpi_grid.addWidget(k, r, c)
        l.addLayout(kpi_grid)

        # Gráficos lado a lado (top)
        self.plot_glosas_causas = self._mk_plot_widget(
            "Distribuição de causas de glosa (Top 15 mais frequentes)"
        )
        self.plot_glosas_serie = self._mk_plot_widget(
            "Série histórica: barras = Nº glosadas | linha = % valor glosado ($)"
        )
        spl_plots = QSplitter(Qt.Horizontal)
        spl_plots.setChildrenCollapsible(False)
        spl_plots.setHandleWidth(4)
        spl_plots.addWidget(self.plot_glosas_causas); spl_plots.addWidget(self.plot_glosas_serie)
        spl_plots.setStretchFactor(0, 1); spl_plots.setStretchFactor(1, 1)
        spl_plots.setSizes([620, 660])
        spl_plots.setCollapsible(0, False); spl_plots.setCollapsible(1, False)
        spl_plots.setMinimumHeight(280)

        # Tabelas lado a lado (inferior)
        self.tbl_glosas_causas = self._mk_table()
        self.tbl_glosas_serie = self._mk_table()
        grp_causas = self._mk_group("📋 Causas de glosa — ocorrências e R$", self.tbl_glosas_causas)
        grp_serie = self._mk_group("📅 Série histórica de glosas", self.tbl_glosas_serie)
        spl_tabs = QSplitter(Qt.Horizontal)
        spl_tabs.setChildrenCollapsible(False)
        spl_tabs.setHandleWidth(4)
        spl_tabs.addWidget(grp_causas); spl_tabs.addWidget(grp_serie)
        spl_tabs.setStretchFactor(0, 1); spl_tabs.setStretchFactor(1, 1)
        spl_tabs.setSizes([620, 660])
        spl_tabs.setCollapsible(0, False); spl_tabs.setCollapsible(1, False)
        spl_tabs.setMinimumHeight(260)

        spl_full = QSplitter(Qt.Vertical)
        spl_full.setChildrenCollapsible(False)
        spl_full.setHandleWidth(4)
        spl_full.addWidget(spl_plots); spl_full.addWidget(spl_tabs)
        spl_full.setSizes([330, 380])
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
        plt.setTitle(title, color=_TXT, size="10pt")
        plt.getAxis("left").setPen(_pen(_TXT_DIM)); plt.getAxis("bottom").setPen(_pen(_TXT_DIM))
        plt.getAxis("left").setTextPen(_TXT_DIM); plt.getAxis("bottom").setTextPen(_TXT_DIM)
        plt.setBackground(_BG)
        plt.showGrid(x=False, y=True, alpha=0.2)
        plt.setMinimumHeight(180)
        return plt

    def _mk_table(self) -> QTableWidget:
        t = QTableWidget(0, 0)
        t.setStyleSheet("""
            QTableWidget{background:#121C1E;alternate-background-color:#172426;color:#E6FFFA;
                         gridline-color:#233539;font-size:13px;border-radius:10px;}
            QTableWidget::item{padding:4px 6px;}
            QHeaderView::section{background:#1B2B2F;color:#8F9EAB;padding:8px;border:none;
                                 border-right:1px solid #233539;border-bottom:2px solid #14B8A6;
                                 font-weight:600;}""")
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
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
        lv = QVBoxLayout(gb); lv.setContentsMargins(16, 24, 16, 16)
        lv.addWidget(inner)
        return gb

    # ------------------------------------------------------------------
    # População (pública API)
    # ------------------------------------------------------------------
    def set_dataset(self, df: pl.DataFrame, *, system: str = "SIA_PA",
                    cnes: list[str] | None = None,
                    compute_huff: bool | None = None) -> None:
        """Dispara o cálculo do dashboard a partir do DataFrame final do pipeline.

        compute_huff: se None, usa o valor do checkbox da UI (chk_huff).
                      Força True/False para sobrescrever em chamadas programáticas.
        """
        # #region debug-point H3_H5: dashboard_set_dataset_ENTRY
        try:
            import json, urllib.request as _ur, pathlib as _pl
            _p = str(_pl.Path('.dbg')/'pipeline-rows-loaded-but-not-showing.env'); _u='http://127.0.0.1:7777/event'; _s='pipeline-rows-loaded-but-not-showing'
            try:
                with open(_p) as _f:
                    _c = _f.read()
                    for _l in _c.split('\n'):
                        if _l.startswith('DEBUG_SERVER_URL='): _u=_l.split('=',1)[1]
                        elif _l.startswith('DEBUG_SESSION_ID='): _s=_l.split('=',1)[1]
            except Exception: pass
            _h = -1; _w=-1; _empty = True
            if df is not None:
                try: _empty = bool(df.is_empty()); _h = int(df.height); _w = int(df.width)
                except Exception: pass
            _n_hosp_uniq=0
            for _col in ("CNES_EXEC","cnes","CO_CNES","CNES"):
                if df is not None and _col in getattr(df, "columns", []):
                    try: _n_hosp_uniq = int(df[_col].n_unique()); break
                    except Exception: pass
            _pld = json.dumps({"sessionId":_s,"runId":"pre","hypothesisId":"H3","location":"dashboard_page.py:set_dataset:L797","msg":"[DEBUG] Dashboard set_dataset ENTRY","data":{"df_is_None":df is None,"df_empty":_empty,"df_height":_h,"df_width":_w,"system":system,"n_hosp_cnes":_n_hosp_uniq,"cnes_list_count":len(cnes or [])}}).encode()
            try: _ur.urlopen(_ur.Request(_u, data=_pld, headers={"Content-Type":"application/json"}), timeout=1).read()
            except Exception: pass
        except Exception: pass
        # #endregion
        self._fonte_dados_camada = "SESSAO_PIPELINE"
        if df is not None and not df.is_empty() and "_fonte_dados" not in df.columns:
            df = df.with_columns(pl.lit("SESSAO_PIPELINE").alias("_fonte_dados"))
        self._last_df = df
        self._last_system = system
        self._last_cnes = cnes
        # === H3a FIX: Sempre volta para 'Sessao Atual' apos receber df do pipeline ===
        self._fonte_atual = "session"
        try:
            self._update_badge_fonte_camada()
        except Exception:
            pass
        try:
            self.cbo_fonte_dados.blockSignals(True)
            self.cbo_fonte_dados.setCurrentIndex(0)
            self.cbo_fonte_dados.blockSignals(False)
            self.cbo_load_runs.setEnabled(False)
        except Exception:
            pass
        # Ajusta combo de sistema
        for i in range(self.cbo_system.count()):
            if self.cbo_system.itemData(i) == system:
                self.cbo_system.setCurrentIndex(i)
                break
        # =============================================================
        # POPULA KPI BÁSICOS IMEDIATAMENTE (antes do worker / cálculo)
        # Se houver df carregado mas algum modulo falhar, usuario ainda
        # vê algo (0 não é o estado verdadeiro do pipeline de carga!)
        # =============================================================
        try:
            if df is not None and not df.is_empty():
                cols = list(df.columns)
                # ---- 1) Estabelecimentos (CNES unique) ----
                n_estabs = 0
                from papahr.dashboard.metrics import _detect_cnes_col
                c_col = _detect_cnes_col(df)
                if c_col:
                    try:
                        n_estabs = int(df[c_col].cast(pl.Utf8, strict=False).n_unique())
                    except Exception:
                        n_estabs = 0
                self.kpi_estabs.set_value(n_estabs)

                # ---- 2) Nº AIHs/APACs (count linhas) ----
                self.kpi_prod.set_value(int(df.height))

                # ---- 3) Valor Total R$ ----
                val_col = None
                for _vc in ("VAL_TOTAL", "VL_TOTAL", "VAL_PAGO", "VAL_APRES",
                            "valor_total", "vl_total_aprovado"):
                    if _vc in cols:
                        val_col = _vc
                        break
                if val_col is None:
                    for _c in cols:
                        _lc = str(_c).lower()
                        if "valor" in _lc and ("total" in _lc or "aprovado" in _lc or "pago" in _lc):
                            val_col = _c
                            break
                try:
                    vtot = 0.0
                    if val_col:
                        vtot = float(
                            df[val_col].cast(pl.Float64, strict=False).fill_null(0.0).sum()
                            or 0.0
                        )
                    self.kpi_valor.set_value(vtot)
                except Exception:
                    pass

                # ---- 4) LOS medio (dias de internacao) ----
                los_col = None
                for _lc in ("QTD_DIARIAS", "QT_DIARIAS", "dias_permanencia",
                            "QT_PERMANENCIA", "los"):
                    if _lc in cols:
                        los_col = _lc
                        break
                if los_col is None:
                    for _c in cols:
                        if ("diaria" in str(_c).lower()) or ("permanencia" in str(_c).lower()):
                            los_col = _c
                            break
                try:
                    if los_col:
                        los_medio = float(
                            df[los_col].cast(pl.Float64, strict=False).fill_null(0.0).mean()
                            or 0.0
                        )
                        self.kpi_los.set_value(round(los_medio, 2))
                except Exception:
                    pass
        except Exception:
            pass  # KPI bruto: se falhar, zeros ainda são mostrados (espera worker)

        # Atualiza info da fonte (para garantir que ao trocar para sessão, mostra linhas novas)
        self._update_fonte_info_label()
        try:
            self._refresh_load_runs()
        except Exception:
            pass
        # #region debug-point H5: dashboard_before_on_calculate_NEW
        try:
            import json, urllib.request as _ur, pathlib as _pl
            _p = str(_pl.Path('.dbg')/'pipeline-rows-loaded-but-not-showing.env'); _u='http://127.0.0.1:7777/event'; _s='pipeline-rows-loaded-but-not-showing'
            try:
                with open(_p) as _f:
                    _c = _f.read()
                    for _l in _c.split('\n'):
                        if _l.startswith('DEBUG_SERVER_URL='): _u=_l.split('=',1)[1]
                        elif _l.startswith('DEBUG_SESSION_ID='): _s=_l.split('=',1)[1]
            except Exception: pass
            _huff_ui = None
            try: _huff_ui = getattr(self.chk_huff, "isChecked", lambda: None)()
            except Exception: pass
            _kpi_raw = {
              "kpi_estabs": getattr(self.kpi_estabs, "_v", -1) if hasattr(self.kpi_estabs,"_v") else None,
              "kpi_prod": getattr(self.kpi_prod, "_v", -1) if hasattr(self.kpi_prod,"_v") else None,
              "kpi_valor": getattr(self.kpi_valor, "_v", -1) if hasattr(self.kpi_valor,"_v") else None,
              "kpi_los": getattr(self.kpi_los, "_v", -1) if hasattr(self.kpi_los,"_v") else None,
            }
            _active = self._current_active_df()
            _act_is_last = _active is self._last_df
            _act_h = -1; _act_empty = True
            if _active is not None:
                try: _act_empty=bool(_active.is_empty()); _act_h=int(_active.height)
                except Exception: pass
            _pld = json.dumps({"sessionId":_s,"runId":"pre","hypothesisId":"H5","location":"dashboard_page.py:set_dataset:L914","msg":"[DEBUG] Dashboard set_dataset → KPI bruto gravado, ANTES _on_calculate (worker DashboardWorker)","data":{"compute_huff_arg":compute_huff,"chk_huff_ui_checked":_huff_ui,"_last_df.height":-1 if self._last_df is None else (int(self._last_df.height) if hasattr(self._last_df,'height') else -1),"_fonte_atual":self._fonte_atual,"_current_active_df is _last_df":_act_is_last,"_current_active_df.height":_act_h,"_current_active_df_empty":_act_empty,"kpi_raw_pos_pupulate":_kpi_raw}}).encode()
            try: _ur.urlopen(_ur.Request(_u, data=_pld, headers={"Content-Type":"application/json"}), timeout=1).read()
            except Exception: pass
        except Exception: pass
        # #endregion
        try:
            self._on_calculate(compute_huff=compute_huff)
            # #region debug-point H5: dashboard_after_on_calculate_ok_NEW
            try:
                import json, urllib.request as _ur, pathlib as _pl
                _p = str(_pl.Path('.dbg')/'pipeline-rows-loaded-but-not-showing.env'); _u='http://127.0.0.1:7777/event'; _s='pipeline-rows-loaded-but-not-showing'
                try:
                    with open(_p) as _f:
                        _c = _f.read()
                        for _l in _c.split('\n'):
                            if _l.startswith('DEBUG_SERVER_URL='): _u=_l.split('=',1)[1]
                            elif _l.startswith('DEBUG_SESSION_ID='): _s=_l.split('=',1)[1]
                except Exception: pass
                _worker_live = self._worker is not None
                _pld = json.dumps({"sessionId":_s,"runId":"pre","hypothesisId":"H5","location":"dashboard_page.py:set_dataset:L946","msg":"[DEBUG] DashboardWorker.start() disparou SEM throw (worker assíncrono; o cálculo final em _on_ready ainda vai acontecer)","data":{"worker_is_not_None":_worker_live}}).encode()
                try: _ur.urlopen(_ur.Request(_u, data=_pld, headers={"Content-Type":"application/json"}), timeout=1).read()
                except Exception: pass
            except Exception: pass
            # #endregion
        except Exception as _e_calc:
            # #region debug-point H5: dashboard_on_calculate_EXCEPTION_NEW
            try:
                import json, urllib.request as _ur, pathlib as _pl
                _p = str(_pl.Path('.dbg')/'pipeline-rows-loaded-but-not-showing.env'); _u='http://127.0.0.1:7777/event'; _s='pipeline-rows-loaded-but-not-showing'
                try:
                    with open(_p) as _f:
                        _c = _f.read()
                        for _l in _c.split('\n'):
                            if _l.startswith('DEBUG_SERVER_URL='): _u=_l.split('=',1)[1]
                            elif _l.startswith('DEBUG_SESSION_ID='): _s=_l.split('=',1)[1]
                except Exception: pass
                import traceback as _tb
                _tb_str = "".join(_tb.format_exception(type(_e_calc), _e_calc, _e_calc.__traceback__)[:8])
                _pld = json.dumps({"sessionId":_s,"runId":"pre","hypothesisId":"H5","location":"dashboard_page.py:set_dataset:L959","msg":"[DEBUG] EXCEPTION lançada em _on_calculate antes do worker.start()","data":{"error_type":type(_e_calc).__name__,"error_msg":str(_e_calc)[:300],"traceback":_tb_str[:2000]}}).encode()
                try: _ur.urlopen(_ur.Request(_u, data=_pld, headers={"Content-Type":"application/json"}), timeout=1).read()
                except Exception: pass
            except Exception: pass
            # #endregion
            raise

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
                cols = runs_df.columns
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
        if is_duck and (self._df_duckdb is None or self._df_duckdb.is_empty()):
            self._load_duckdb_into_df()
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

    def _gold_aggr_has_rows(self) -> bool:
        """Retorna True se gold.aggr_mensal_hospital existir E tiver > 0 rows."""
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

    def _load_duckdb_into_df(self) -> None:
        """Carrega dados persistidos para self._df_duckdb.

        Estratégia Medallion Fallback (P3 R4):
          1. Verifica se gold.aggr_mensal_hospital existe e tem linhas (GOLD).
          2. Se GOLD ok → carrega silver.producao (fonte de detalhe),
             marca como GOLD_OTIMIZADO (camada gold íntegra).
          3. Se GOLD vazio/inexistente → fallback transparente para silver.producao,
             marca como SILVER_FALLBACK (log warning, sem bloqueio para o usuário).
          4. Sempre adiciona a coluna _fonte_dados no resultado.
        """
        try:
            self.lbl_status.set_status("Carregando dados persistidos do DuckDB...", "info")
            QApplication.processEvents()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            db = get_db()
            load_run_id: str | None = self.cbo_load_runs.currentData()

            gold_ok = self._gold_aggr_has_rows()
            if gold_ok:
                self._fonte_dados_camada = "GOLD_OTIMIZADO"
                log.info("[Fallback-P3R4] gold.aggr_mensal_hospital populado → usando GOLD_OTIMIZADO (silver como detalhe).")
            else:
                self._fonte_dados_camada = "SILVER_FALLBACK"
                log.warning(
                    "[Fallback-P3R4] gold.aggr_mensal_hospital VAZIO ou inexistente. "
                    "Fallback automático → silver.producao (transparente para usuário)."
                )

            if load_run_id:
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
                    'uf AS "UF", LPAD(CAST(cnes AS VARCHAR),7,\'0\') AS "CNES_EXEC", no_fantasia AS "NOME_HOSPITAL", '
                    'no_razao_social AS "RAZAO_SOCIAL", LPAD(CAST(municipio_cod AS VARCHAR),7,\'0\') AS "COD_MUNICIPIO_IBGE", '
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
            self._update_badge_fonte_camada()
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
        # #region debug-point H5: dashboard_on_fail_worker_REAL_ERROR
        try:
            import json, urllib.request as _ur, pathlib as _pl
            _p = str(_pl.Path('.dbg')/'pipeline-rows-loaded-but-not-showing.env'); _u='http://127.0.0.1:7777/event'; _s='pipeline-rows-loaded-but-not-showing'
            try:
                with open(_p) as _f:
                    _c = _f.read()
                    for _l in _c.split('\n'):
                        if _l.startswith('DEBUG_SERVER_URL='): _u=_l.split('=',1)[1]
                        elif _l.startswith('DEBUG_SESSION_ID='): _s=_l.split('=',1)[1]
            except Exception: pass
            _pld = json.dumps({"sessionId":_s,"runId":"pre","hypothesisId":"H5","location":"dashboard_page.py:_on_fail:L1190","msg":"[DEBUG] DashboardWorker.failed → _on_fail CHAMADO (esse é o caminho do erro real do cálculo)","data":{"error_msg_full":msg[:3000]}}).encode()
            try: _ur.urlopen(_ur.Request(_u, data=_pld, headers={"Content-Type":"application/json"}), timeout=2).read()
            except Exception: pass
        except Exception: pass
        # #endregion
        self.btn_calc.setEnabled(True)
        self.lbl_status.set_status(f"Erro: {msg[:80]}", "error")
        QMessageBox.critical(self, "Dashboard falhou", msg)

    def _on_ready(self, m: DashboardMetrics) -> None:
        # #region debug-point H5: dashboard_on_ready_worker_FINISHED
        try:
            import json, urllib.request as _ur, pathlib as _pl
            _p = str(_pl.Path('.dbg')/'pipeline-rows-loaded-but-not-showing.env'); _u='http://127.0.0.1:7777/event'; _s='pipeline-rows-loaded-but-not-showing'
            try:
                with open(_p) as _f:
                    _c = _f.read()
                    for _l in _c.split('\n'):
                        if _l.startswith('DEBUG_SERVER_URL='): _u=_l.split('=',1)[1]
                        elif _l.startswith('DEBUG_SESSION_ID='): _s=_l.split('=',1)[1]
            except Exception: pass
            _kp = dict(m.kpis) if getattr(m, 'kpis', None) is not None else {}
            # Corta valores > 80 chars para não infar payload
            for _k, _v in list(_kp.items()):
                if isinstance(_v, str) and len(_v) > 120: _kp[_k] = _v[:120] + "…"
            # Listas/DataFrame shapes
            _df_shapes = {
              "estratificacao_risco": [int(getattr(m.estratificacao_risco,'height',-1)), int(getattr(m.estratificacao_risco,'width',-1))],
              "eficiencia_clinica": [int(getattr(m.eficiencia_clinica,'height',-1)), int(getattr(m.eficiencia_clinica,'width',-1))],
              "territorio_uf_mapa": [int(getattr(m.territorio_uf_mapa,'height',-1)), int(getattr(m.territorio_uf_mapa,'width',-1))],
              "transparencia_prestador": [int(getattr(m.transparencia_prestador,'height',-1)), int(getattr(m.transparencia_prestador,'width',-1))],
              "geral_historico_mensal": [int(getattr(m.geral_historico_mensal,'height',-1)), int(getattr(m.geral_historico_mensal,'width',-1))],
              "benchmarking": [int(getattr(m.benchmarking,'height',-1)), int(getattr(m.benchmarking,'width',-1))],
              "glosas_apac_aih": [int(getattr(m.glosas_apac_aih,'height',-1)), int(getattr(m.glosas_apac_aih,'width',-1))],
            }
            _pld = json.dumps({"sessionId":_s,"runId":"pre","hypothesisId":"H5","location":"dashboard_page.py:_on_ready:L1195","msg":"[DEBUG] DashboardWorker.finished_ok → _on_ready chamado. KPIs e shapes de cada tab para conferir se retornou vazio ou populado","data":{"kpis":_kp,"dfs_shape":_df_shapes}}).encode()
            try: _ur.urlopen(_ur.Request(_u, data=_pld, headers={"Content-Type":"application/json"}), timeout=2).read()
            except Exception: pass
        except Exception: pass
        # #endregion
        self.btn_calc.setEnabled(True)
        self.btn_export.setEnabled(True)
        self._metrics = m
        self._fill_kpis(m)
        self._fill_tab_risco(m)
        self._fill_tab_efic(m)
        self._fill_tab_terr(m)
        self._fill_tab_trans(m)
        self._fill_tab_geral(m)
        self._fill_tab_bench(m)
        self._fill_tab_glosas(m)
        self.lbl_status.set_status("Dashboard atualizado ✓", "ok")

    # ------------------------------------------------------------------
    def _fill_kpis(self, m: DashboardMetrics) -> None:
        k = m.kpis
        self.kpi_estabs.set_value(int(k.get("n_estabelecimentos", 0)))
        self.kpi_prod.set_value(int(k.get("n_aihs_ou_apacs", 0)))
        self.kpi_valor.set_value(float(k.get("valor_total_R$", 0.0)))
        self.kpi_icsap.set_value(float(k.get("icsap_pct", 0.0)))
        self.kpi_los.set_value(float(k.get("dias_permanencia_total", 0.0))
                                / max(1, int(k.get("n_aihs_ou_apacs", 1)))
                                if k.get("n_aihs_ou_apacs", 0) > 0 else 0.0)
        self.kpi_readm.set_value(float(k.get("readmissao_30d_est_pct", 0.0)))

    # ------------------------------------------------------------------
    def _fill_tab_risco(self, m: DashboardMetrics) -> None:
        # Gráfico de estratos (médio de ALTO/MEDIO/BAIXO nos hospitais)
        df = m.estratificacao_risco
        if pg is not None and not df.is_empty():
            self.plot_estratos.clear()
            cat = ["ALTO", "MEDIO", "BAIXO"]
            vals = []
            for c in cat:
                col = f"pct_{c}"
                vals.append(float(df[col].mean()) if col in df.columns else 0.0)
            x = list(range(len(cat)))
            bg = pg.BarGraphItem(x=x, height=vals, width=0.55,
                                 brushes=[_brush(_DANG), _brush(_WARN), _brush(_OK)])
            self.plot_estratos.addItem(bg)
            ax = self.plot_estratos.getAxis("bottom")
            ax.setTicks([(i, c) for i, c in enumerate(cat)])
            self.plot_estratos.setYRange(0, 100)
            self.plot_estratos.setTitle(f"Estratos — ALTO={vals[0]:.1f}%, MÉDIO={vals[1]:.1f}%, BAIXO={vals[2]:.1f}%",
                                        color=_TXT, size="10pt")
        # CMI
        if pg is not None and not m.eficiencia_clinica.is_empty() and "case_mix_index" in m.eficiencia_clinica.columns:
            self.plot_cmi.clear()
            cmi = m.eficiencia_clinica["case_mix_index"].to_numpy()
            cnes = m.eficiencia_clinica["cnes"].to_list()
            y = cmi
            x = list(range(len(y)))
            sp = pg.ScatterPlotItem(x=x, y=list(y), size=8,
                                    pen=_pen(_PRI_DARK), brush=_brush(_PRI))
            self.plot_cmi.addItem(sp)
            if len(x):
                self.plot_cmi.getAxis("bottom").setTicks(
                    [(x[i], (c if len(c) <= 5 else c[-5:]))
                     for i, c in enumerate(cnes) if i % max(1, len(x)//12) == 0])
                self.plot_cmi.setXRange(-0.5, len(x)-0.5, padding=0)
            # média CMI
            avg = float(y.mean()) if len(y) else 0.0
            ln = pg.InfiniteLine(pos=avg, angle=0, pen=_pen(_WARN, 2))
            self.plot_cmi.addItem(ln)
            self.plot_cmi.setTitle(f"Case Mix Index — média={avg:.2f}", color=_TXT, size="10pt")
        # Tabela estratos
        self._plfill(self.tbl_risco, df)

    # ------------------------------------------------------------------
    def _fill_tab_efic(self, m: DashboardMetrics) -> None:
        df = m.eficiencia_clinica
        if pg is not None and not df.is_empty() and "los_o_e" in df.columns:
            self.plot_los.clear()
            labels = df["cnes"].to_list()
            oe = df["los_o_e"].to_numpy().tolist()
            x = list(range(len(oe)))
            colors = [_color_for_pct(50 if v < 1 else 50 + (v-1)*50).name() for v in oe]
            bg = pg.BarGraphItem(x=x, height=oe, width=0.65,
                                 brushes=[QBrush(QColor(c)) for c in colors])
            self.plot_los.addItem(bg)
            self.plot_los.addItem(pg.InfiniteLine(pos=1.0, angle=0, pen=_pen(_WARN, 2)))
            if x:
                self.plot_los.getAxis("bottom").setTicks(
                    [(x[i], (c if len(c) <= 5 else c[-5:]))
                     for i, c in enumerate(labels) if i % max(1, len(x)//12) == 0])
                self.plot_los.setXRange(-0.5, len(x)-0.5, padding=0)
        if pg is not None and not df.is_empty() and "custo_o_e" in df.columns:
            self.plot_custo.clear()
            labels = df["cnes"].to_list() if "cnes" in df.columns else []
            ce = df["custo_o_e"].to_numpy().tolist()
            colors_c = [_color_for_pct(50 if v < 1 else 50 + (v-1)*50).name() for v in ce]
            x = list(range(len(ce)))
            bg = pg.BarGraphItem(x=x, height=ce, width=0.65,
                                 brushes=[QBrush(QColor(c)) for c in colors_c])
            self.plot_custo.addItem(bg)
            self.plot_custo.addItem(pg.InfiniteLine(pos=1.0, angle=0, pen=_pen(_WARN, 2)))
            if x and labels:
                self.plot_custo.getAxis("bottom").setTicks(
                    [(x[i], (c if len(c) <= 5 else c[-5:]))
                     for i, c in enumerate(labels) if i % max(1, len(x)//12) == 0])
                self.plot_custo.setXRange(-0.5, len(x)-0.5, padding=0)
        self._plfill(self.tbl_efic, df)

    # ------------------------------------------------------------------
    def _fill_tab_terr(self, m: DashboardMetrics) -> None:
        # ICSAP por condição
        if pg is not None and not m.icsap_por_condicao.is_empty():
            self.plot_icsap.clear()
            conds = m.icsap_por_condicao["condicao_evitavel"].to_list()
            ns = m.icsap_por_condicao["n_internacoes"].to_numpy().tolist()
            y = list(reversed(ns)); labels = list(reversed(conds))
            brushes = [QBrush(_color_for_pct(50 - 50*(y[i]/(max(y) if max(y) else 1)))) for i in range(len(y))]
            bg = pg.BarGraphItem(x=list(range(len(y))), height=y, width=0.7, brushes=brushes)
            self.plot_icsap.addItem(bg)
            self.plot_icsap.getAxis("bottom").setTicks(
                [(i, c if len(c) < 26 else c[:24] + "…") for i, c in enumerate(labels)])
            ax = self.plot_icsap.getAxis("bottom")
            ax.setStyle(tickTextHeight=110, autoExpandTextSpace=True)
            self.plot_icsap.setXRange(-0.5, len(y)-0.5, padding=0)
        # Tabelas
        self._plfill(self.tbl_terr, m.integracao_territorial)
        if m.huff and not m.huff.hospital_attraction.is_empty():
            self._plfill(self.tbl_huff, m.huff.hospital_attraction)

        # =====================================================
        # Mapa concêntrico + filtros de agrupamento SIGTAP
        # =====================================================
        try:
            from papahr.dashboard.cnes_loader import get_cnes_data
            cnes_data = get_cnes_data().load()
            muni_df = cnes_data.municipios
            estab_df = cnes_data.estabelecimentos
            self._muni_df = muni_df
        except Exception as exc:
            log.warning("Não foi possível carregar CNES para o mapa: %s", exc)
            muni_df = pl.DataFrame()
            estab_df = pl.DataFrame()

        points: list[HospitalMapPoint] = []
        target_cnes = set(self._last_cnes or [])
        if estab_df is not None and not estab_df.is_empty() and "CO_CNES" in estab_df.columns:
            cols = [
                "CO_CNES", "NO_FANTASIA", "NO_RAZAO_SOCIAL",
                "NU_LATITUDE", "NU_LONGITUDE", "NU_LEITOS_SUS",
            ]
            avail = [c for c in cols if c in estab_df.columns]
            if len(avail) >= 5:
                filt = estab_df.select(avail)
                if target_cnes:
                    filt = filt.filter(pl.col("CO_CNES").cast(pl.Utf8).is_in(list(target_cnes)))
                for row in filt.to_dicts():
                    cnes = str(row.get("CO_CNES", ""))
                    nome = str(
                        row.get("NO_FANTASIA") or row.get("NO_RAZAO_SOCIAL") or cnes
                    )
                    try:
                        lat = float(row.get("NU_LATITUDE") or 0.0)
                        lon = float(row.get("NU_LONGITUDE") or 0.0)
                    except (TypeError, ValueError):
                        continue
                    if not lat or not lon:
                        continue
                    try:
                        leitos = float(row.get("NU_LEITOS_SUS") or 50.0)
                    except (TypeError, ValueError):
                        leitos = 50.0
                    points.append(HospitalMapPoint(
                        cnes=cnes, nome=nome, latitude=lat, longitude=lon, leitos=leitos,
                    ))

        # Ajusta o max_rate baseado nos dados
        if points and self._last_df is not None and not self._last_df.is_empty():
            # Estimativa rápida de faixa
            try:
                total_int = self._last_df.height
                if muni_df is not None and "NU_POPULACAO" in muni_df.columns and not muni_df.is_empty():
                    pop_total = float(muni_df["NU_POPULACAO"].cast(pl.Float64, strict=False).fill_null(0).sum() or 1)
                    taxa_geral = 1000.0 * total_int / pop_total
                    self.concentric_map.set_max_rate(max(10.0, taxa_geral * 2.5))
            except Exception:
                pass
        self.concentric_map.set_data(
            points,
            muni_df if (muni_df is not None and not muni_df.is_empty()) else pl.DataFrame(),
            self._last_df if self._last_df is not None else pl.DataFrame(),
        )

        # Popula combos de filtro
        self._rebuild_map_group_options()

        # =====================================================
        # Curvas de decaimento exponencial: popula combo hospital
        # =====================================================
        decay_df = m.distance_decay
        bench_df = m.benchmarking
        self.cbo_decay_hospital.blockSignals(True)
        self.cbo_decay_hospital.clear()
        if decay_df is not None and not decay_df.is_empty():
            # nome do hospital: se bench_df tem hospital_nome usa senão usa CNES
            nome_lookup: dict[str, str] = {}
            if bench_df is not None and not bench_df.is_empty() and "hospital_nome" in bench_df.columns:
                for r in bench_df.select(["hospital_cnes", "hospital_nome"]).unique(
                        subset=["hospital_cnes"], keep="first").iter_rows(named=True):
                    nome_lookup[str(r["hospital_cnes"])] = str(r["hospital_nome"] or "")
            # CNES únicos presentes em decay_df (ordem original)
            cnes_unicos: list[str] = []
            vistos: set[str] = set()
            for c in decay_df["hospital_cnes"].cast(pl.Utf8).to_list():
                if c in vistos:
                    continue
                vistos.add(c); cnes_unicos.append(c)
            for cnes in cnes_unicos:
                nome = nome_lookup.get(cnes, "").strip()
                label = f"{cnes} — {nome}" if nome else f"CNES {cnes}"
                self.cbo_decay_hospital.addItem(label, cnes)
        self.cbo_decay_hospital.blockSignals(False)
        # Dispara render inicial do primeiro hospital (se tiver)
        if self.cbo_decay_hospital.count() > 0:
            self.cbo_decay_hospital.setCurrentIndex(0)
        else:
            self._on_decay_hospital_changed(0)

    # ------------------------------------------------------------------
    def _fill_tab_trans(self, m: DashboardMetrics) -> None:
        df = m.transicao_cuidado
        if pg is not None and not df.is_empty():
            self.plot_trans.clear()
            linhas = df["linha_cuidado"].to_list()
            x = list(range(len(linhas)))
            contato = df["contato_48h_pct"].to_numpy().tolist() if "contato_48h_pct" in df.columns else [0]*len(linhas)
            readm = df["readmissao_30d_pct_est"].to_numpy().tolist() if "readmissao_30d_pct_est" in df.columns else [0]*len(linhas)
            # Dois grupos de barras (offset esquerda/direita)
            w = 0.38
            bg1 = pg.BarGraphItem(x=[i-w for i in x], height=contato, width=w,
                                  brush=_brush(_OK))
            bg2 = pg.BarGraphItem(x=[i+w for i in x], height=readm, width=w,
                                  brush=_brush(_DANG))
            self.plot_trans.addItem(bg1); self.plot_trans.addItem(bg2)
            ax = self.plot_trans.getAxis("bottom")
            ax.setTicks([(i, (c if len(c) < 24 else c[:22] + "…")) for i, c in enumerate(linhas)])
            ax.setStyle(tickTextHeight=110, autoExpandTextSpace=True)
            self.plot_trans.setXRange(-1, len(x), padding=0)
            self.plot_trans.setYRange(0, 100)
            legend = self.plot_trans.addLegend(offset=(20, 20), brush=None, labelTextColor=_TXT)
            # O addLegend só adiciona itens quando há gráficos plotados com nome; workaround:
            # usamos duas linhas pontilhadas "dummy"
            self.plot_trans.plot([], [], pen=_pen(_OK, 6), name="Contato 48h")
            self.plot_trans.plot([], [], pen=_pen(_DANG, 6), name="Readmissão 30d")
            legend = self.plot_trans.getPlotItem().legend
        self._plfill(self.tbl_trans, df)

    # ------------------------------------------------------------------
    def _fill_tab_geral(self, m: DashboardMetrics) -> None:
        ts = m.serie_temporal
        if pg is not None and not ts.is_empty() and ts.width >= 3:
            self.plot_ts.clear()
            cols = ts.columns
            label_x = f"{cols[0]}-{cols[1]}" if len(cols) >= 2 else ""
            n = ts["n_internacoes"].to_numpy().tolist()
            x = list(range(len(n)))
            # Labels bottom (col1-col2 concat)
            try:
                k1, k2 = cols[0], cols[1]
                labels = [f"{int(a)}-{int(b):02d}" for a, b in zip(ts[k1].to_list(), ts[k2].to_list())]
            except (TypeError, ValueError):
                labels = [str(i) for i in x]
            bg = pg.BarGraphItem(x=x, height=n, width=0.7, brush=_brush(_PRI))
            self.plot_ts.addItem(bg)
            if "taxa_icsap_pct" in ts.columns:
                y2 = ts["taxa_icsap_pct"].to_numpy().tolist()
                if y2 and max(y2) > 0:
                    # escala 0-100 em eixo 2
                    # (simples: escalona y2 para intervalo do mesmo gráfico usando eixo 2)
                    try:
                        self.plot_ts.showAxis("right")
                        self.plot_ts.getAxis("right").setPen(_pen(_WARN))
                        self.plot_ts.getAxis("right").setTextPen(_WARN)
                        self.plot_ts.setYRange(0, max(100.0, float(max(n) or 0) * 1.1), padding=0.0)
                        # Segundo plotitem com viewbox secundário? Simples aqui: usar escala normalizada
                        # Simplificação: plotar normalizado em % do max de n
                        mx = max(n) if n else 1
                        y2_sc = [(y / 100.0) * mx for y in y2]
                        self.plot_ts.plot(x, y2_sc, pen=_pen(_WARN, 2), name="Taxa ICSAP (%)")
                    except Exception:
                        pass
            if x and labels:
                self.plot_ts.getAxis("bottom").setTicks(
                    [(x[i], c) for i, c in enumerate(labels) if i % max(1, len(x)//10) == 0])
                self.plot_ts.setXRange(-0.5, len(x)-0.5, padding=0)
        # Alertas
        if m.alerts:
            df_alerts = pl.DataFrame({"#": list(range(1, len(m.alerts)+1)),
                                       "Alerta Operacional": m.alerts})
        else:
            df_alerts = pl.DataFrame({"#": ["1"], "Alerta Operacional": ["Nenhum alerta. Todos os indicadores dentro dos limites recomendados."]})
        self._plfill(self.tbl_alerts, df_alerts)
        # Top 10
        self._plfill(self.tbl_top10_p, m.top10_procedimentos)
        self._plfill(self.tbl_top10_e, m.top10_especialidades)

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
        self._refresh_filter_combo()
        mode = self.cbo_map_group.currentData() or "grupo"
        self.concentric_map.set_group_mode(mode, None)

    def _on_map_filter_changed(self, _idx: int) -> None:
        mode = self.cbo_map_group.currentData() or "grupo"
        try:
            val = self.cbo_map_filter.currentData()
        except Exception:
            val = None
        self.concentric_map.set_group_mode(mode, val)

    def _on_map_hospital_clicked(self, cnes: str) -> None:
        self.hospital_selected.emit(cnes)
        self.lbl_map_hover.set_status(f"🏥 Hospital selecionado — CNES {cnes}", "ok")
        # Seleciona aba Risco e destaca o hospital (se possível)
        self.tabs.setCurrentIndex(0)

    # ------------------------------------------------------------------
    def _plfill(self, table: QTableWidget, df: pl.DataFrame) -> None:
        table.clear()
        if df is None or df.is_empty():
            table.setRowCount(1); table.setColumnCount(1)
            table.setHorizontalHeaderLabels(["Status"])
            item = QTableWidgetItem("Nenhum dado disponível para exibição.")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setForeground(QBrush(QColor("#8F9EAB")))
            font = QFont()
            font.setItalic(True)
            item.setFont(font)
            table.setItem(0, 0, item)
            table.horizontalHeader().setStretchLastSection(True)
            return
        cols = [str(c) for c in df.columns]
        table.setColumnCount(len(cols))
        table.setHorizontalHeaderLabels(cols)
        rows = min(df.height, 500)
        table.setRowCount(rows)
        view = df.head(500)
        for r in range(rows):
            for c, col in enumerate(cols):
                v = view[col][r]
                if v is None:
                    s = ""
                elif isinstance(v, float):
                    if math.isfinite(v):
                        s = (f"{v:,.2f}" if abs(v) >= 1000 else f"{v:.4f}" if abs(v) < 1
                             else f"{v:.2f}").replace(",", "_").replace(".", ",").replace("_", ".")
                    else:
                        s = ""
                else:
                    s = str(v)
                item = QTableWidgetItem(s)
                # colorir colunas percentuais
                if ("pct" in col.lower() or "%" in col) and isinstance(v, (int, float)):
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

    # ------------------------------------------------------------------
    def _on_load_csv_parquet(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Selecionar arquivos CSV ou Parquet",
            str(Path.home()),
            "Dados (*.csv *.parquet *.pq)",
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
                elif ext == ".csv":
                    # FIX GAP-P0-2 MINIMO #1: CSV BR (DATASUS/GSPHosp) usa (; sep) + windows-1252/utf8-bom.
                    # Reutiliza CNESLoader._detect_csv_params (4 sep x 4 enc = 16 combinações).
                    from papahr.dashboard.cnes_loader import _detect_csv_params
                    auto_params = dict(_detect_csv_params(p))
                    # Depois de detectar sep/enc, habilita parse de datas + schema largo
                    auto_params.update(
                        try_parse_dates=True,
                        infer_schema_length=5000,
                        infer_schema=True,
                    )
                    try:
                        df = pl.read_csv(p, **auto_params)
                    except Exception as _e_csv1:
                        # Fallback leve (caso _detect_csv_params falhe):
                        # Tenta detectar sep por contagem bruta 64KB (CNESLoader fallback binario).
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

            # FIX GAP-P0-2 MINIMO #2: AUTO-DETECTA SISTEMA SUS via heurística de colunas.
            # Motivo: _last_system = SIHRD/SIAPA etc é obrigatório no _on_calculate p/ montar
            # coluna ano/mes e pesos clínicos. Sem isso, usa sistema do pipeline anterior (errado).
            cols_norm: set[str] = {c.strip().lower() for c in getattr(df_final, "columns", [])}
            def _has(*candidates: str) -> bool:
                return any(c.strip().lower() in cols_norm for c in candidates)
            if _has("n_aih","aih","cod_aih","no_aih","co_aih","valor_aprovado","val_tot","diag_princ"):
                auto_system = "SIH-RD"
            elif _has("pa","co_pa","apac","no_pa","valor_apresentado","vl_hospitalar","cod_procedimento","co_procedimento"):
                auto_system = "SIA-PA"
            elif _has("cnes_exec","cnes","no_fantasia","co_cnes") and _has("sp_proc","sp_valor","co_ato"):
                auto_system = "SIH-SP"
            elif _has("cnes_exec") and _has("rejeicao","mot_rejeit","co_mot_rejeicao"):
                auto_system = "SIH-RJ"
            else:
                # Fallback seguro: SIH-RD pois é padrão sus default
                auto_system = self._last_system or "SIH-RD"

            # FIX GAP-P0-2 MINIMO #3: USA API PUBLICA set_dataset() (NÃO raw self._last_df = df_final).
            # Motivo: set_dataset popula os 6 KPIs básicos IMEDIATAMENTE, seta cbo_sistema,
            # salva _last_system _last_cnes corretamente, e evita "usuário vê tudo 0" antes do worker.
            # Além disso: _fonte_atual combo 'session' é setado dentro do set_dataset se precisar?
            # → Não: set_dataset NÃO mexe no fonte. Nós ajustamos fonte ANTES:
            self._fonte_atual = "session"
            idx = self.cbo_fonte_dados.findData("session")
            if idx >= 0:
                self.cbo_fonte_dados.blockSignals(True)
                self.cbo_fonte_dados.setCurrentIndex(idx)
                self.cbo_fonte_dados.blockSignals(False)
            self.set_dataset(df_final, system=auto_system, cnes=None, compute_huff=None)
            resumo = f"{len(files)} arquivo(s) · {df_final.height():,.0f} linhas × {df_final.width()} colunas · {auto_system}"
            try:
                self.lbl_fonte_info.setText(f"✅ {resumo}")
            except Exception:
                pass
            self.lbl_status.set_status(f"✅ {resumo} — pronto para calcular", "ok")
            QApplication.processEvents()
            # _on_calculate agora será chamado? → set_dataset NÃO dispara cálculo (design original:
            # usuário confirma manualmente o compute). Mas refs/p3_dashboard README B1 pede "carregar
            # sem pipeline" — UX melhor: cálculo automático.
            self._on_calculate()
        except Exception as exc:
            log.exception("Erro ao importar CSV/Parquet no dashboard")
            self.lbl_status.set_status(f"❌ Erro ao carregar: {exc!r}", "error")
            QMessageBox.warning(
                self,
                "Erro ao carregar arquivos",
                f"Não foi possível carregar os arquivos selecionados.\n\n"
                f"Detalhe: {exc}\n\n"
                f"Dicas: verifique se o arquivo está aberto em outro programa, "
                f"ou se o CSV usa separadores padrão (; ou ,).",
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
                ("huff_catchment_profile.csv",   self._metrics.huff.catchment_profile if self._metrics.huff else pl.DataFrame()),
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
                    bg = pg.BarGraphItem(x=x, height=vals, width=0.72, brushes=brushes_list)
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
                    bg_serie = pg.BarGraphItem(x=xs, height=heights_bar, width=0.62, brush=_brush(_DANG))
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

    # ------------------------------------------------------------------
    # Benchmarking Hospitalar
    # ------------------------------------------------------------------
    _INDICADOR_PIOR_ALTO = {
        "icsap_pct", "los_medio_dias", "readmissao_30d_pct",
        "pct_alto", "custo_oe", "los_o_e", "lace_medio",
    }

    def _fill_tab_bench(self, m: DashboardMetrics) -> None:
        """Popula o combo de hospitais e dispara a renderização inicial."""
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
            self.cbo_bench_hospital.setCurrentIndex(0)
        else:
            self._on_bench_changed(0)

    def _on_bench_changed(self, _idx: int = 0) -> None:
        """Atualiza os gráficos e tabela de benchmarking conforme seleção."""
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
        # ---------- Gráfico de barras
        if pg is not None:
            self.plot_bench_bar.clear()
            vals = df_sorted[ind].cast(pl.Float64, strict=False).fill_nan(None).to_list()
            xs = list(range(len(vals)))
            # Cores de barra
            brushes_list = []
            max_v = max((v for v in vals if v is not None), default=1.0) or 1.0
            for i, v in enumerate(vals):
                if i == alvo_i:
                    brushes_list.append(QBrush(QColor(_OK)))
                    continue
                if v is None:
                    brushes_list.append(QBrush(QColor("#475569")))
                    continue
                t = min(1.0, float(v) / max_v) if max_v > 0 else 0.0
                if ind in self._INDICADOR_PIOR_ALTO:
                    color = _lerp_color(_OK, _DANG, t)
                else:  # CMI etc.: maior = complexo, então azul→roxo
                    color = _lerp_color("#818CF8", "#A855F7", t)
                brushes_list.append(QBrush(color))
            bg = pg.BarGraphItem(x=xs, height=vals, width=0.65, brushes=brushes_list)
            self.plot_bench_bar.addItem(bg)
            mediana = 0.0
            if vals and any(v is not None for v in vals):
                mediana = float(df_sorted[ind].cast(pl.Float64, strict=False).drop_nans().median() or 0.0)
                self.plot_bench_bar.addItem(
                    pg.InfiniteLine(pos=mediana, angle=0, pen=_pen(_WARN, 2),
                                     style=Qt.DashLine))
            # Labels do eixo X: nome CNES ou últimos 7
            def _short(t: str) -> str:
                tt = (t or "").strip()
                return tt if len(tt) <= 7 else tt[-7:]
            tick_eixo = [
                (_i, _short(str(c)))
                for _i, c in enumerate(df_sorted["hospital_cnes"].to_list())
                if _i % max(1, len(xs)//15) == 0
            ]
            self.plot_bench_bar.getAxis("bottom").setTicks([tick_eixo])
            self.plot_bench_bar.setXRange(-0.5, len(xs)-0.5, padding=0)
            self.plot_bench_bar.setTitle(
                f"Benchmark {self.cbo_bench_ind.currentText()} — {n_comp} hospitais (mediana {mediana:.2f} em amarelo)",
                color=_TXT, size="10pt")
            # ---------- Scatter (X=%ICSAP, Y=LOS medio)
            self.plot_bench_scatter.clear()
            col_x = "icsap_pct"
            col_y = "los_medio_dias"
            if col_x in df_sorted.columns and col_y in df_sorted.columns:
                scat = df_sorted.select([col_x, col_y, "hospital_cnes"])
                xs_all = scat[col_x].cast(pl.Float64, strict=False).fill_nan(None).to_list()
                ys_all = scat[col_y].cast(pl.Float64, strict=False).fill_nan(None).to_list()
                alvo_cnes_s = str(alvo_cnes)
                others_x, others_y, target_x, target_y = [], [], None, None
                for _i, _c in enumerate(scat["hospital_cnes"].to_list()):
                    xi = xs_all[_i]; yi = ys_all[_i]
                    if xi is None or yi is None or not math.isfinite(float(xi)) or not math.isfinite(float(yi)):
                        continue
                    if str(_c) == alvo_cnes_s:
                        target_x, target_y = float(xi), float(yi)
                    else:
                        others_x.append(float(xi)); others_y.append(float(yi))
                if others_x:
                    sp_o = pg.ScatterPlotItem(x=others_x, y=others_y, size=8,
                                               pen=_pen("#475569"), brush=_brush(_TXT_DIM))
                    self.plot_bench_scatter.addItem(sp_o)
                if target_x is not None and target_y is not None:
                    sp_t = pg.ScatterPlotItem(x=[target_x], y=[target_y], size=18,
                                               pen=_pen(_OK), brush=_brush(_OK))
                    self.plot_bench_scatter.addItem(sp_t)
                # Medianas tracejadas
                try:
                    med_x = float(df_sorted[col_x].cast(pl.Float64, strict=False).drop_nans().median() or 0.0)
                    med_y = float(df_sorted[col_y].cast(pl.Float64, strict=False).drop_nans().median() or 0.0)
                    self.plot_bench_scatter.addItem(pg.InfiniteLine(pos=med_x, angle=90, pen=_pen(_WARN, 1), style=Qt.DashLine))
                    self.plot_bench_scatter.addItem(pg.InfiniteLine(pos=med_y, angle=0, pen=_pen(_WARN, 1), style=Qt.DashLine))
                except Exception:
                    pass
                self.plot_bench_scatter.setLabel("bottom", "% ICSAP (evitáveis)")
                self.plot_bench_scatter.setLabel("left", "LOS médio (dias)")
        # ---------- Tabela
        colunas_uteis = [
            "hospital_cnes", "hospital_nome", "CO_UF", "CO_MUNICIPIO",
            "n_altas", ind, "icsap_pct", "los_medio_dias", "cmi", "lace_medio",
            "pct_alto", "readmissao_30d_pct", "custo_oe", "los_o_e",
        ]
        cols_existem = [c for c in colunas_uteis if c in df_sorted.columns]
        self._plfill(self.tbl_bench, df_sorted.select(cols_existem) if cols_existem else df_sorted)

    # ------------------------------------------------------------------
    # Curvas de decaimento exponencial
    # ------------------------------------------------------------------
    def _on_decay_hospital_changed(self, _idx: int = 0) -> None:
        m = self._metrics
        if m is None or m.distance_decay.is_empty():
            if pg is not None:
                self.plot_distance_decay.clear()
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
            self.plot_distance_decay.clear()
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
