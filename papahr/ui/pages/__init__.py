from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import partial
from pathlib import Path
from typing import Optional
import sys
import traceback
import polars as pl
from PySide6.QtCore import QDate, QLocale, QObject, QPoint, QRect, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from papahr.config import get_settings
from papahr.geo import UF_POR_SIGLA, get_ufs, lista_drs, lista_macrorregioes
from papahr.geo.reference import hospital_bate_geo, resolve_geo_enrichment
from papahr.llm import LLMAnalyst
from papahr.logging_config import get_logger
from papahr.pipelines import PipelineProgress, PipelineRequest, PipelineResult, run_pipeline
from papahr.security import DateRange
from papahr.db import get_db  # ← DuckDB Medallion (Bronze→Silver→Gold)
from papahr.ui.widgets import (
    AnimatedProgress,
    CardWidget,
    ConcentricRingsMap,
    HospitalMapPoint,
    KpiCard,
    StatusBadge,
    StreamViewer,
    ToggleSwitch,
)

log = get_logger(__name__)

def is_hospital_unit(tipo_code_or_str: str, name_str: str) -> bool:
    """Retorna True estritamente para HOSPITAIS, MATERNIDADES, PRONTO-SOCORROS e UNIDADES MISTAS.

    Filtra fora farmácias, academias de saúde, UBS, postos de saúde, consultórios e laboratórios.
    """
    t = str(tipo_code_or_str or "").strip().upper()
    n = str(name_str or "").strip().upper()

    # Se nome contiver explicitamente termos de não-hospital -> rejeita
    non_hosp_words = ("FARMACIA", "FARMA ", "FARMA_", "ACADEMIA DE SAUDE", "ACADEMIA DA SAUDE",
                      "POSTO DE SAUDE", "UNIDADE DE SAUDE DA FAMILIA", "U S F ", "USF ",
                      "CONSULTORIO", "LABORATORIO", "DROGARIA", "ENDEMIAS", "APAE", "CAPS ")
    if any(nh in n for nh in non_hosp_words):
        return False

    # Códigos numéricos oficiais de hospitais no CNES DATASUS (CO_TIPO_ESTABELECIMENTO):
    # 05 = Hospital Geral, 07 = Hospital Especializado, 06 = Pronto Socorro Geral,
    # 15 = Unidade Mista, 20 = Pronto Socorro Especializado, 04 = Policlínica c/ internação
    hosp_codes = {"05", "5", "07", "7", "06", "6", "15", "20", "04", "4"}
    if t in hosp_codes:
        return True

    # Termos no tipo de estabelecimento
    hosp_type_keywords = ("HOSPITAL", "HOSP", "PRONTO SOCORRO", "UNIDADE MISTA", "EMERGENCIA", "MATERNIDADE", "CASA DE SAUDE")
    if any(k in t for k in hosp_type_keywords):
        return True

    # Termos no nome do estabelecimento
    name_keywords = ("HOSPITAL", "HOSP ", " HOSP", "MATERNIDADE", "PRONTO SOCORRO", "SANTA CASA", "UNIDADE MISTA", "CASA DE SAUDE", "UPA 24H", "PRONTO ATENDIMENTO", "PRONTO-SOCORRO")
    if any(k in n for k in name_keywords):
        return True

    return False


def _is_hospital_establishment(tipo_str: str, name_str: str) -> bool:
    """Verifica se o estabelecimento é um HOSPITAL/UNIDADE HOSPITALAR.

    Evita que postos de saúde, UBS, farmácias, laboratórios e consultórios
    apareçam na lista de seleção quando o usuário escolhe uma UF (ex: Acre).
    """
    t = str(tipo_str or "").strip().upper()
    n = str(name_str or "").strip().upper()

    # Códigos numéricos CNES de hospitais/pronto-socorros/unidades mistas
    # 05 = Hospital Geral, 07 = Hospital Especializado, 06 = Pronto Socorro Geral,
    # 15 = Unidade Mista, 20 = Pronto Socorro Especializado, 04 = Policlínica c/ leitos
    hosp_codes = {"05", "5", "07", "7", "06", "6", "15", "20", "04", "4"}
    if t in hosp_codes:
        return True

    # Palavras-chave no tipo de estabelecimento
    hosp_type_keywords = ("HOSPITAL", "HOSP", "PRONTO SOCORRO", "UNIDADE MISTA", "EMERGENCIA", "MATERNIDADE", "CASA DE SAUDE")
    if any(k in t for k in hosp_type_keywords):
        return True

    # Palavras-chave no nome do estabelecimento
    name_keywords = ("HOSPITAL", "HOSP ", " HOSP", "MATERNIDADE", "PRONTO SOCORRO", "SANTA CASA", "UNIDADE MISTA", "CASA DE SAUDE", "UPA 24H", "PRONTO ATENDIMENTO")
    if any(k in n for k in name_keywords):
        return True

    # Se tipo for totalmente desconhecido ou vazio, inclui como segurança
    if not t and not any(non_hosp in n for non_hosp in ("FARMACIA", "POSTO DE SAUDE", "CONSULTORIO", "LABORATORIO", "DROGARIA")):
        return True

    return False


def _wrap_step_scrollable(w: QWidget) -> QScrollArea:
    """Envolve o passo do wizard em um QScrollArea responsivo.

    Garante que NENHUM controle (datas, meses, chips, busca ou botões)
    fique cortado, desalinhado ou escondido em resoluções menores.
    """
    scr = QScrollArea()
    scr.setWidgetResizable(True)
    scr.setFrameShape(QFrame.NoFrame)
    scr.setStyleSheet(
        "QScrollArea { background: transparent; border: none; }"
        "QScrollBar:vertical { background: #0B1416; width: 10px; border-radius: 5px; }"
        "QScrollBar::handle:vertical { background: #233539; border-radius: 5px; min-height: 20px; }"
        "QScrollBar::handle:vertical:hover { background: #4FD1C5; }"
    )
    scr.setWidget(w)
    return scr


_UF_LIST = sorted([
    "AC","AL","AP","AM","BA","CE","DF","ES","GO","MA",
    "MT","MS","MG","PA","PB","PR","PE","PI","RJ","RN",
    "RS","RO","RR","SC","SP","SE","TO",
])

# Mapeia códigos IBGE de 2 dígitos para sigla UF (DATASUS usa CO_ESTADO_GESTOR em CNES)
_UF_CODIBGE_TO_SIGLA: dict[str, str] = {
    "12":"AC","27":"AL","16":"AP","13":"AM","29":"BA","23":"CE","53":"DF",
    "32":"ES","52":"GO","21":"MA","51":"MT","50":"MS","31":"MG","15":"PA",
    "25":"PB","41":"PR","26":"PE","22":"PI","33":"RJ","24":"RN","43":"RS",
    "11":"RO","14":"RR","42":"SC","35":"SP","28":"SE","17":"TO",
}


# ---------------------------------------------------------------------------
# Worker thread para pipelines (não travar a UI)
# ---------------------------------------------------------------------------

class PipelineWorker(QThread):
    progress = Signal(object)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, request: PipelineRequest) -> None:
        super().__init__()
        self._req = request

    def run(self) -> None:
        try:
            def cb(p: PipelineProgress) -> None:
                self.progress.emit(p)
            result = run_pipeline(self._req, progress_cb=cb)
            self.finished_ok.emit(result)
        except Exception as e:  # noqa: BLE001
            log.exception("Pipeline falhou")
            self.failed.emit(str(e))


# ---------------------------------------------------------------------------
# Página 1: Pipeline de processamento
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Widgets auxiliares: Chip + FlowLayout (para interface 100% visual)
# ---------------------------------------------------------------------------

class ChipButton(QPushButton):
    """Chip clicável (toggle style) para seleção múltipla."""

    def __init__(self, label: str, value: Optional[str] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(label, parent)
        self.setCheckable(True)
        self._value = value if value is not None else label
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(34)
        self.setStyleSheet("""
            QPushButton{background:#162023;color:#94A3B8;border:1px solid #233539;
                         border-radius:17px;padding:4px 14px;font-size:12px;}
            QPushButton:hover{border-color:#4FD1C5;color:#E6FFFA;}
            QPushButton:checked{background:linear-gradient(90deg,#0D9488,#0F766E);
                                color:#FFFFFF;border:2px solid #5EEAD4;font-weight:700;}
        """)

    def value(self) -> str:
        return self._value


class HospitalChipButton(ChipButton):
    """Chip compacto e elegante para seleção de hospitais com remoção rápida."""

    def __init__(self, cnes: str, nome: str, uf: str = "", parent: Optional[QWidget] = None) -> None:
        name_clean = (nome or "").strip()
        name_trunc = (name_clean[:45] + "…") if len(name_clean) > 45 else name_clean
        uf_prefix = f"[{uf}] " if uf else ""
        label_text = f"🏥 {uf_prefix}{name_trunc} ({cnes[-4:]})  ×"
        super().__init__(label_text, value=cnes, parent=parent)
        self.setChecked(True)
        self.setMinimumHeight(26)
        self.setMaximumHeight(28)
        self.setToolTip(f"CNES: {cnes}\nHospital: {nome}\nUF: {uf}\n\n(Clique para remover este hospital da seleção)")
        self.setStyleSheet("""
            QPushButton {
                background: #1B2B2F;
                color: #4FD1C5;
                border: 1px solid #14B8A6;
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 10.5px;
                font-weight: 600;
                text-align: left;
            }
            QPushButton:hover {
                background: #991B1B;
                color: #FEE2E2;
                border-color: #EF4444;
            }
            QPushButton:checked {
                background: #0D9488;
                color: #FFFFFF;
                border: 2px solid #5EEAD4;
            }
            QPushButton:checked:hover {
                background: #991B1B;
                color: #FEE2E2;
                border-color: #EF4444;
            }
        """)
        self.clicked.connect(self._on_chip_clicked)

    def _on_chip_clicked(self) -> None:
        if not self.isChecked():
            self.deleteLater()


class FlowLayout(QLayout):
    """Layout horizontal que quebra linha automaticamente (QFlowLayout moderno)."""

    def __init__(self, parent: Optional[QWidget] = None, margin: int = 0, spacing: int = 8) -> None:
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self._spacing = spacing
        self._items: list[QLayoutItem] = []

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:  # noqa: N802
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x(); y = effective.y()
        line_height = 0
        for item in self._items:
            wid = item.widget()
            space_x = self._spacing
            space_y = self._spacing
            if wid is not None:
                style = wid.style()
                space_x += max(0, style.layoutSpacing(QSizePolicy.PushButton, QSizePolicy.PushButton, Qt.Horizontal))
                space_y += max(0, style.layoutSpacing(QSizePolicy.PushButton, QSizePolicy.PushButton, Qt.Vertical))
            next_x = x + item.sizeHint().width() + space_x
            if (next_x - space_x) > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        return y + line_height - rect.y() + m.bottom()


# ---------------------------------------------------------------------------
# Worker thread para pipelines (não travar a UI)
# ---------------------------------------------------------------------------

class PipelineWorker(QThread):
    progress = Signal(object)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, request: PipelineRequest) -> None:
        super().__init__()
        self._req = request

    def run(self) -> None:
        try:
            def cb(p: PipelineProgress) -> None:
                self.progress.emit(p)
            result = run_pipeline(self._req, progress_cb=cb)
            self.finished_ok.emit(result)
        except Exception as e:  # noqa: BLE001
            log.exception("Pipeline falhou")
            self.failed.emit(str(e))


# ---------------------------------------------------------------------------
# Wizard de pipeline em 4 passos (100% visual)
# ---------------------------------------------------------------------------

_STEP_COLOR = ("#14B8A6", "#6366F1", "#F59E0B", "#10B981")
_STEP_LABEL = (
    "1️⃣ Sistema & Fonte",
    "2️⃣ Período",
    "3️⃣ Localização SUS",
    "4️⃣ Extração & Dashboard",
)


class PipelinePage(QWidget):
    """Página principal com wizard passo-a-passo 100% visual."""

    result_ready = Signal(object)  # PipelineResult

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings = get_settings()
        self._worker: Optional[PipelineWorker] = None
        self._last_result: Optional[PipelineResult] = None
        self._build_ui()

    # ================================================================== UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # Container principal com scroll para caber em qualquer resolução sem sobreposição
        main_scroll = QScrollArea()
        main_scroll.setWidgetResizable(True)
        main_scroll.setFrameShape(QFrame.NoFrame)
        main_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: #0B1416; width: 10px; border-radius: 5px; }"
            "QScrollBar::handle:vertical { background: #233539; border-radius: 5px; min-height: 20px; }"
            "QScrollBar::handle:vertical:hover { background: #4FD1C5; }"
        )

        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setContentsMargins(4, 4, 4, 4)
        cl.setSpacing(14)

        # --- WIZARD 4 PASSOS (FULL WIDTH - 100% VISÍVEL, NUNCA ENCOLHE) ---
        wizard_card = CardWidget()
        wizard_card.setMinimumHeight(620)
        fl = wizard_card.layout_()

        title = QLabel("🧭 Assistente de Extração de Dados do SUS")
        title.setStyleSheet("font-size:18px;font-weight:700;color:#4FD1C5;")
        fl.addWidget(title)
        subtitle = QLabel("Siga os 4 passos clicando nos botões/chips. Não há necessidade de digitar nada!")
        subtitle.setStyleSheet("color:#94A3B8;font-size:12px;")
        subtitle.setWordWrap(True)
        fl.addWidget(subtitle)

        # Step Indicator (4 bolinhas coloridas com label)
        self.step_bar = QHBoxLayout()
        self.step_bar.setSpacing(6)
        self.step_labels: list[QLabel] = []
        self.step_dots: list[QLabel] = []
        for i in range(4):
            vbox = QVBoxLayout()
            dot = QLabel()
            dot.setAlignment(Qt.AlignCenter)
            dot.setFixedSize(QSize(28, 28))
            dot.setStyleSheet(self._dot_style(i, active=False, done=False))
            lbl = QLabel(_STEP_LABEL[i])
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("color:#64748B;font-size:10px;font-weight:600;")
            lbl.setWordWrap(True)
            lbl.setMinimumHeight(34)
            vbox.addWidget(dot, 0, Qt.AlignCenter)
            vbox.addWidget(lbl)
            self.step_dots.append(dot); self.step_labels.append(lbl)
            self.step_bar.addLayout(vbox, 1)
            if i < 3:
                sep = QFrame(); sep.setFrameShape(QFrame.HLine); sep.setStyleSheet(f"color:{_STEP_COLOR[i]};");
                sep.setMaximumHeight(3); sep.setMinimumWidth(26)
                self.step_bar.addWidget(sep, 0)
        fl.addLayout(self.step_bar)
        fl.addSpacing(4)

        # Passos (QStackedWidget)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_step1())      # 0: Sistema & Fonte
        self.stack.addWidget(self._build_step3())      # 1: Período
        self.stack.addWidget(self._build_step2())      # 2: Localização SUS
        self.stack.addWidget(self._build_step4())      # 3: Extração & Dashboard
        self.stack.setMinimumHeight(430)
        fl.addWidget(self.stack, 1)
        fl.addSpacing(6)

        # Navegação entre passos
        nav = QHBoxLayout()
        nav.setSpacing(10)
        self.btn_prev = QPushButton("◀ Anterior")
        self.btn_prev.setStyleSheet("padding:10px 16px;border-radius:10px;background:#1B2B2F;color:#E6FFFA;border:1px solid #233539;")
        self.btn_prev.setMinimumHeight(40)
        self.btn_prev.clicked.connect(self._step_prev)
        self.btn_next = QPushButton("Próximo ▶")
        self.btn_next.setStyleSheet("padding:10px 18px;border-radius:10px;background:#6366F1;color:#fff;font-weight:700;border:none;")
        self.btn_next.setMinimumHeight(40)
        self.btn_next.clicked.connect(self._step_next)
        self.btn_run = QPushButton("🚀 EXECUTAR EXTRAÇÃO E ABRIR DASHBOARD")
        self.btn_run.setStyleSheet(
            "QPushButton{padding:12px 22px;border-radius:10px;font-weight:900;font-size:14px;"
            "background:linear-gradient(90deg,#059669,#0D9488);color:#FFFFFF;border:2px solid #34D399;}"
            "QPushButton:hover{background:linear-gradient(90deg,#047857,#0F766E);color:#FEF08A;border:2px solid #6EE7B7;}"
        )
        self.btn_run.setMinimumHeight(44)
        self.btn_run.clicked.connect(self._on_analyze_dashboard_clicked)
        nav.addWidget(self.btn_prev); nav.addStretch(1); nav.addWidget(self.btn_next); nav.addWidget(self.btn_run)
        fl.addLayout(nav)

        # Progresso / status
        self.progress = AnimatedProgress()
        self.status_badge = StatusBadge("Passo 1 de 4", "INFO")
        self.lbl_stage = QLabel("Selecione o sistema de informação e a fonte dos dados.")
        self.lbl_stage.setStyleSheet("color:#94A3B8;")
        self.lbl_stage.setWordWrap(True)
        fl.addWidget(self.status_badge); fl.addWidget(self.progress); fl.addWidget(self.lbl_stage)
        cl.addWidget(wizard_card, 1)

        # --- PAINEL INFERIOR SANFONA (OPCIONAL - Recolhido por padrão) ---
        self.preview_group = QGroupBox("📋 Prévia dos Dados & Resultados da Extração (Opcional - Clique para Expandir)")
        self.preview_group.setCheckable(True)
        self.preview_group.setChecked(False)
        self.preview_group.setStyleSheet("""
            QGroupBox{border:1px solid #233539;border-radius:10px;margin-top:10px;
                      color:#4FD1C5;font-weight:600;padding:8px;}
            QGroupBox::title{subcontrol-origin:margin;left:12px;padding:0 6px 0 6px;}""")
        
        preview_inner = QWidget()
        self._preview_content_widget = preview_inner
        preview_inner.setVisible(False)
        gl = QVBoxLayout(preview_inner)
        gl.setContentsMargins(8, 12, 8, 8); gl.setSpacing(8)

        kpi_row = QHBoxLayout(); kpi_row.setSpacing(8)
        self.kpi_rows  = KpiCard("Linhas", 0, "Total processado")
        self.kpi_cols  = KpiCard("Colunas", 0, "Largura do dataset", accent="#3B82F6")
        self.kpi_time  = KpiCard("Tempo", "0 s", "Tempo total", accent="#8B5CF6")
        self.kpi_valor = KpiCard("Valor", "R$ 0", "Soma de valores", accent="#F59E0B")
        for k in (self.kpi_rows, self.kpi_cols, self.kpi_time, self.kpi_valor):
            k.setMinimumHeight(80)
            kpi_row.addWidget(k)
        gl.addLayout(kpi_row)

        self.table = QTableWidget(0, 0)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(180)
        self.table.setMaximumHeight(260)
        self.table.setStyleSheet(
            "QTableWidget{background:#121C1E;alternate-background-color:#172426;"
            "color:#E6FFFA;gridline-color:#233539;border-radius:10px;}"
            "QHeaderView::section{background:#1B2B2F;color:#4FD1C5;padding:8px;"
            "border:none;border-bottom:2px solid #14B8A6;font-weight:600;}")
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        gl.addWidget(self.table)

        warn_card = CardWidget()
        wl = warn_card.layout_()
        wh = QLabel("⚠ Avisos / Observações"); wh.setStyleSheet("font-weight:700;color:#F59E0B;")
        wl.addWidget(wh)
        self.txt_warnings = QPlainTextEdit(); self.txt_warnings.setReadOnly(True)
        self.txt_warnings.setPlaceholderText("Nenhum aviso ainda.")
        self.txt_warnings.setMaximumHeight(80)
        wl.addWidget(self.txt_warnings)

        btns = QHBoxLayout(); btns.setSpacing(6)
        self.btn_open_folder = QPushButton("📂 Abrir saída"); self.btn_open_folder.clicked.connect(self._open_output_folder)
        self.btn_open_folder.setMinimumHeight(34)
        self.btn_export_excel = QPushButton("📊 Salvar Excel (.xlsx)"); self.btn_export_excel.clicked.connect(self._export_excel)
        self.btn_export_excel.setMinimumHeight(34)
        self.btn_dashboard = QPushButton("📈 Ir para Dashboard")
        self.btn_dashboard.setStyleSheet(
            "QPushButton{background:#0D9488;color:#FFFFFF;font-weight:800;border-radius:10px;padding:8px 14px;border:1px solid #2DD4BF;}"
            "QPushButton:hover{background:#0F766E;color:#FEF08A;}"
        )
        self.btn_dashboard.setMinimumHeight(34)
        self.btn_dashboard.setEnabled(True)
        btns.addWidget(self.btn_open_folder); btns.addWidget(self.btn_export_excel); btns.addStretch(); btns.addWidget(self.btn_dashboard)
        wl.addLayout(btns)
        gl.addWidget(warn_card)

        self._preview_content_widget = preview_inner
        pg_layout = QVBoxLayout(self.preview_group)
        pg_layout.setContentsMargins(0, 0, 0, 0)
        pg_layout.addWidget(preview_inner)
        self.preview_group.toggled.connect(lambda ch: preview_inner.setVisible(ch))

        cl.addWidget(self.preview_group, 0)
        main_scroll.setWidget(container)
        root.addWidget(main_scroll, 1)

        self._auto_go_dashboard_after_run = False
        self._set_step(0)

    # -----------------------------------------------------------
    def _dot_style(self, idx: int, active: bool, done: bool) -> str:
        c = _STEP_COLOR[idx]
        if done:
            return f"border-radius:14px;background:#1B2B2F;color:#E6FFFA;border:2px solid {c};"
        if active:
            return (f"border-radius:14px;background:{c};color:#FFFFFF;border:2px solid {c};"
                    "font-weight:800;font-size:13px;")
        return f"border-radius:14px;background:#162023;color:#64748B;border:1px dashed #2D4A4A;"

    def _on_analyze_dashboard_clicked(self) -> None:
        """Handler do botão '🚀 Analisar & Ver Dashboard'."""
        if self._last_result is None or getattr(self._last_result, "df", None) is None or self._last_result.df.is_empty():
            self._auto_go_dashboard_after_run = True
            self._on_run()
        else:
            if hasattr(self, "btn_dashboard") and self.btn_dashboard is not None:
                self.btn_dashboard.click()

    def _update_step4_summary(self) -> None:
        if not hasattr(self, "lbl_step4_summary") or self.lbl_step4_summary is None:
            return
        sys_code = self._selected_system()
        if "SIH" in sys_code or sys_code == "SIH-RD":
            sys_name = "SIH-RD (AIHs Aprovadas — Internação Hospitalar)"
        elif sys_code == "SIH-RJ":
            sys_name = "SIH-RJ (AIHs Rejeitadas)"
        elif sys_code == "SIH-SP":
            sys_name = "SIH-SP (Serviços Profissionais — AIH)"
        else:
            sys_name = "SIA-PA (Produção Ambulatorial)"

        src_id = self.group_source.checkedId() if hasattr(self, "group_source") else 0
        if src_id == 0:
            source_name = "Download Automático FTP DATASUS (Público)"
        elif src_id == 1:
            source_name = "Pasta com Arquivos DBC/DBF Locais"
        else:
            source_name = "Pasta com Arquivos Compactados (.zip/.rar)"

        ufs = self._selected_ufs()
        uf_txt = ", ".join(ufs) if ufs else "Nenhuma UF selecionada"
        p_txt = getattr(self, "lbl_period_summary", None)
        period_txt = p_txt.text() if p_txt else "Período Configurado"
        
        info = (
            f"• <b>Sistema & Fonte:</b> {sys_name} ({source_name})<br/>"
            f"• <b>Período:</b> {period_txt}<br/>"
            f"• <b>Escopo Territorial:</b> UF(s): <b>{uf_txt}</b> (Carga total de 100% dos hospitais)"
        )
        self.lbl_step4_summary.setText(info)

    def _set_step(self, n: int) -> None:
        for i in range(4):
            self.step_dots[i].setStyleSheet(self._dot_style(i, active=(i == n), done=(i < n)))
            self.step_labels[i].setStyleSheet(
                f"font-size:10px;font-weight:700;"
                f"color:{'#4FD1C5' if i <= n else '#64748B'};"
            )
            self.step_dots[i].setText("✔" if i < n else str(i + 1))
        self.stack.setCurrentIndex(n)
        self.btn_prev.setEnabled(n > 0)
        self.btn_next.setVisible(n < 3)
        self.btn_run.setVisible(True)  # Sempre visível em todos os passos!
        # Stage label
        hints = [
            "Selecione o sistema e a fonte dos dados.",
            "Escolha o período de competência.",
            "Clique em chips para selecionar UF e/ou municípios.",
            "Confirme as configurações e acione a extração.",
        ]
        self.lbl_stage.setText(f"Passo {n+1} de 4 — {hints[n]}")
        self.status_badge.set_status(f"Passo {n+1}/4", "OK" if n == 3 else "INFO")
        if n == 3:
            self._update_step4_summary()

    def _step_next(self) -> None:
        n = self.stack.currentIndex()
        # Validações por step
        if n == 0:
            src = self._current_step1_source()
            if src == "local" and not self._local_dir():
                QMessageBox.information(self, "Informe a pasta DBC",
                    "Selecione uma pasta local com arquivos DBC ou escolha Download FTP DATASUS "
                    "ou Pasta com arquivos compactados.")
                return
            if src == "archive" and not self._archive_dir():
                QMessageBox.information(self, "Informe a pasta compactada",
                    "Selecione uma pasta com arquivos .zip/.rar/.7z de CNES/SIA/AIH "
                    "ou use outra opção de fonte.")
                return
        if n < 3:
            self._set_step(n + 1)

    def _step_prev(self) -> None:
        n = self.stack.currentIndex()
        if n > 0:
            self._set_step(n - 1)

    # ----------------------------------------------------------- STEP 1
    def _build_step1(self) -> QWidget:
        w = QScrollArea(); w.setWidgetResizable(True); w.setFrameShape(QFrame.NoFrame)
        inner = QWidget(); vl = QVBoxLayout(inner); vl.setContentsMargins(10, 6, 10, 6); vl.setSpacing(10)

        # Título
        hl = QLabel("Selecione o Sistema de Informação:"); hl.setStyleSheet("font-weight:700;color:#E6FFFA;")
        vl.addWidget(hl)
        # Chips de sistema (ORDEM = prioridade: 1.AIH-RD (internações PADRÃO), 2.AIH-RJ (rejeitadas),
        # 3.AIH-SP (profissionais), 4.SIA-PA (SÓ AMBULATORIAL - NÃO tem AIHs/internações!))
        self.chips_sys = FlowLayout(spacing=8)
        self.group_sys = QButtonGroup(self); self.group_sys.setExclusive(True)
        for lab, val in [("🛏️ SIH-RD  (AIHs Aprovadas — PADRÃO / Internações Hospitalares)", "SIH-RD"),
                         ("❗ SIH-RJ  (AIHs Rejeitadas)", "SIH-RJ"),
                         ("👨‍⚕️ SIH-SP (Serviços Profissionais — AIH)", "SIH-SP"),
                         ("🏥 SIA-PA  (Produção Ambulatorial — ⚠️ SÓ AMBULATORIAL, NÃO contém AIHs nem internações!)", "SIA-PA")]:
            c = ChipButton(lab, val); self.chips_sys.addWidget(c); self.group_sys.addButton(c)
            if val == "SIH-RD":
                c.setChecked(True)
        self.chips_sys_wrap = QWidget(); self.chips_sys_wrap.setLayout(self.chips_sys)
        vl.addWidget(self.chips_sys_wrap)

        vl.addSpacing(6)
        hl2 = QLabel("Fonte dos dados:"); hl2.setStyleSheet("font-weight:700;color:#E6FFFA;")
        vl.addWidget(hl2)
        self.group_source = QButtonGroup(self); self.group_source.setExclusive(True)

        card_ftp = QFrame(); card_ftp.setStyleSheet(
            "QFrame{background:#162023;border:1px solid #233539;border-radius:12px;padding:14px;}")
        cf = QVBoxLayout(card_ftp)
        rb_ftp = QRadioButton("🌐 Download automático via FTP DATASUS (PADRÃO, SEM CONFIGURAÇÃO)")
        rb_ftp.setStyleSheet("color:#4FD1C5;font-weight:700;font-size:13px;")
        rb_ftp.setChecked(True); self.group_source.addButton(rb_ftp, 0)
        cf.addWidget(rb_ftp)
        ftp_hint = QLabel(
            "✓ Recomendado. NÃO PRECISA DE AUTORIZAÇÃO NEM SENHA EXTRA — "
            "o sistema baixa automaticamente os arquivos DBC do FTP público do Ministério da Saúde (DATASUS) "
            "e guarda em cache local após o 1º download.\n"
            "💡 Basta avançar para os próximos passos (UFs, período, hospitais) e clicar em '🚀 Executar Extração' no Passo 4 — "
            "o download FTP começa sozinho."
        )
        ftp_hint.setStyleSheet("color:#94A3B8;")
        ftp_hint.setWordWrap(True)
        cf.addWidget(ftp_hint)

        card_local = QFrame(); card_local.setStyleSheet(
            "QFrame{background:#162023;border:1px solid #233539;border-radius:12px;padding:14px;}")
        cl = QVBoxLayout(card_local)
        rb_local = QRadioButton("💾 Arquivos DBC/DBF em pasta do meu computador")
        rb_local.setStyleSheet("color:#6366F1;font-weight:700;font-size:13px;")
        self.group_source.addButton(rb_local, 1)
        cl.addWidget(rb_local)
        cl.addWidget(QLabel("✓ Aceita arquivos DBC (compactados DATASUS) **ou** DBF (já descompactados).\n"
                           "  Misture os dois formatos tranquilamente — arquivos duplicados usam o DBF."))
        hl_row = QHBoxLayout()
        self.lbl_dbc = QLabel("(nenhuma pasta selecionada)")
        self.lbl_dbc.setStyleSheet("color:#94A3B8;")
        self.lbl_dbc.setWordWrap(True)
        btn_pick = QPushButton("📁 Selecionar pasta DBC/DBF...")
        btn_pick.setMinimumWidth(210)
        btn_pick.setStyleSheet("background:#1B2B2F;color:#E6FFFA;padding:8px 12px;border-radius:10px;border:1px solid #233539;")
        btn_pick.clicked.connect(self._pick_dbc_dir)
        hl_row.addWidget(self.lbl_dbc, 1); hl_row.addWidget(btn_pick, 0)
        cl.addLayout(hl_row)

        card_archive = QFrame(); card_archive.setStyleSheet(
            "QFrame{background:#162023;border:1px solid #233539;border-radius:12px;padding:14px;}")
        al = QVBoxLayout(card_archive)
        rb_archive = QRadioButton("📦 Pasta com arquivos compactados (.zip/.rar/.7z)")
        rb_archive.setStyleSheet("color:#F59E0B;font-weight:700;font-size:13px;")
        self.group_source.addButton(rb_archive, 2)
        al.addWidget(rb_archive)
        al.addWidget(QLabel("✓ Jogue na pasta arquivos zipados/raros do CNES, SIA ou AIH\n"
                           "  — o sistema extrai tudo automaticamente (RAR precisa de unrar no PATH)."))
        hl_arc = QHBoxLayout()
        self.lbl_archive = QLabel("(nenhuma pasta compactada selecionada)")
        self.lbl_archive.setStyleSheet("color:#94A3B8;")
        self.lbl_archive.setWordWrap(True)
        btn_arc = QPushButton("📦 Selecionar pasta compactada...")
        btn_arc.setMinimumWidth(210)
        btn_arc.setStyleSheet("background:#1B2B2F;color:#E6FFFA;padding:8px 12px;border-radius:10px;border:1px solid #233539;")
        btn_arc.clicked.connect(self._pick_archive_dir)
        hl_arc.addWidget(self.lbl_archive, 1); hl_arc.addWidget(btn_arc, 0)
        al.addLayout(hl_arc)
        self.rb_source_ftp = rb_ftp; self.rb_source_local = rb_local; self.rb_source_archive = rb_archive
        # Layout em 1 coluna (todos os cards empilhados verticalmente = 100% acessível em telas pequenas!)
        vl.addWidget(card_ftp)
        vl.addWidget(card_local)
        vl.addWidget(card_archive)

        # ---------------------------------------------------- NOVO: Seleção manual de arquivos
        gbox_files = QGroupBox("📋 Arquivos encontrados na pasta (selecione os que quer analisar)")
        gbox_files.setStyleSheet(
            "QGroupBox{border:1px solid #233539;border-radius:10px;margin-top:8px;padding:10px;color:#6366F1;font-weight:700;}"
            "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 6px;}")
        gl = QVBoxLayout(gbox_files); gl.setSpacing(8)
        row_btns = QHBoxLayout()
        self.btn_mark_all_files = QPushButton("☑ Marcar todos"); self._style_action_btn(self.btn_mark_all_files, "#6366F1")
        self.btn_mark_none_files = QPushButton("☒ Nenhum"); self._style_action_btn(self.btn_mark_none_files, "#64748B")
        self.btn_refresh_files = QPushButton("🔄 Atualizar lista"); self._style_action_btn(self.btn_refresh_files, "#4FD1C5")
        self.lbl_files_count = QLabel("0 arquivo(s) listados.")
        self.lbl_files_count.setStyleSheet("color:#94A3B8;")
        self.btn_mark_all_files.clicked.connect(self._marcar_todos_arquivos(True))
        self.btn_mark_none_files.clicked.connect(self._marcar_todos_arquivos(False))
        self.btn_refresh_files.clicked.connect(self._refresh_file_table)
        row_btns.addWidget(self.btn_mark_all_files); row_btns.addWidget(self.btn_mark_none_files)
        row_btns.addWidget(self.btn_refresh_files); row_btns.addStretch(1); row_btns.addWidget(self.lbl_files_count)
        gl.addLayout(row_btns)

        self.tbl_files = QTableWidget(0, 5)
        self.tbl_files.setHorizontalHeaderLabels(["", "Arquivo", "Tipo", "Tamanho", "Modificado em"])
        self.tbl_files.verticalHeader().setVisible(False)
        self.tbl_files.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_files.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_files.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_files.setAlternatingRowColors(True)
        self.tbl_files.setStyleSheet("""
            QTableWidget{background:#0F1A1C;color:#E6FFFA;border:1px solid #233539;border-radius:8px;gridline-color:#1B2B2F;}
            QTableWidget::item{padding:4px 6px;}
            QTableWidget::item:selected{background:#14B8A6;color:#0F1A1C;}
            QHeaderView::section{background:#121C1E;color:#94A3B8;border:none;padding:6px;font-weight:700;border-bottom:1px solid #233539;}
            QHeaderView::section:first{color:#6366F1;}
        """)
        hdr = self.tbl_files.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)   # checkbox
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)            # arquivo
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)   # tipo
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)   # tamanho
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)   # data
        self.tbl_files.setMinimumHeight(230)
        self.tbl_files.verticalHeader().setDefaultSectionSize(26)
        gl.addWidget(self.tbl_files, 1)
        vl.addWidget(gbox_files, 1)

        # Salvar formatos
        gbox_save = QGroupBox("Formatos de saída"); gbox_save.setStyleSheet(
            "QGroupBox{border:1px solid #233539;border-radius:10px;margin-top:8px;padding:10px;color:#4FD1C5;font-weight:700;}"
            "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 6px;}")
        gl2 = QVBoxLayout(gbox_save)
        self.chk_csv = QCheckBox("Salvar CSV (planilha aberta)"); self.chk_csv.setChecked(True)
        self.chk_parquet = QCheckBox("Salvar Parquet compactado (★ recomendado, rápido e pouco espaço)")
        self.chk_parquet.setChecked(True)
        for c in (self.chk_csv, self.chk_parquet):
            c.setStyleSheet("color:#E6FFFA;padding:4px 2px;")
            gl2.addWidget(c)
        vl.addWidget(gbox_save)

        vl.addStretch(1)
        w.setWidget(inner)
        return w

    def _pick_dbc_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Selecione a pasta com arquivos DBC ou DBF (já descompactado)")
        if d:
            self.lbl_dbc.setText(d)
            self.lbl_dbc.setStyleSheet("color:#4FD1C5;font-weight:700;")
            self.rb_source_local.setChecked(True)
            self._populate_file_table(Path(d), extensions=(".dbc", ".dbf"))

    def _pick_archive_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self,
            "Selecione pasta com arquivos compactados (.zip/.rar/.7z de CNES/SIA/AIH)")
        if d:
            self.lbl_archive.setText(d)
            self.lbl_archive.setStyleSheet("color:#F59E0B;font-weight:700;")
            self.rb_source_archive.setChecked(True)
            self._populate_file_table(Path(d), extensions=(".zip", ".rar", ".7z", ".dbc", ".dbf"))

    def _refresh_file_table(self) -> None:
        mode = self._current_step1_source()
        if mode == "local":
            d = self._local_dir()
            if not d:
                QMessageBox.information(self, "Selecione a pasta", "Primeiro escolha uma pasta DBC/DBF (fonte LOCAL).")
                return
            self._populate_file_table(Path(d), extensions=(".dbc", ".dbf"))
        elif mode == "archive":
            a = self._archive_dir()
            if not a:
                QMessageBox.information(self, "Selecione a pasta", "Primeiro escolha uma pasta compactada.")
                return
            self._populate_file_table(Path(a), extensions=(".zip", ".rar", ".7z", ".dbc", ".dbf"))
        else:
            QMessageBox.information(self, "Fonte FTP DATASUS",
                "No modo FTP a seleção de arquivos é automática, baseada no período e UFs selecionados.")

    def _populate_file_table(self, folder: Path, extensions: tuple[str, ...]) -> None:
        self.tbl_files.setRowCount(0)
        if not folder or not folder.is_dir():
            self.lbl_files_count.setText("0 arquivo(s) listados.")
            return
        exts_lower = tuple(e.lower() for e in extensions)
        all_files: list[Path] = []
        for p in folder.rglob("*"):
            try:
                if p.is_file() and p.suffix.lower() in exts_lower:
                    all_files.append(p)
            except (OSError, PermissionError):
                continue
        all_files.sort(key=lambda x: x.name.lower())
        for path in all_files:
            row = self.tbl_files.rowCount()
            self.tbl_files.insertRow(row)
            # Coluna 0: checkbox
            w = QWidget(); hbl = QHBoxLayout(w); hbl.setContentsMargins(6, 0, 0, 0); hbl.setAlignment(Qt.AlignCenter)
            cb = QCheckBox(); cb.setChecked(True); cb.setCursor(Qt.PointingHandCursor)
            hbl.addWidget(cb); w.setProperty("cb", cb)
            self.tbl_files.setCellWidget(row, 0, w)
            # Coluna 1: Nome do arquivo
            it1 = QTableWidgetItem(path.name)
            try:
                it1.setToolTip(str(path))
            except Exception:
                pass
            self.tbl_files.setItem(row, 1, it1)
            # Coluna 2: Tipo (extensão)
            ext = (path.suffix or "").lower().replace(".", "").upper()
            it2 = QTableWidgetItem(ext)
            it2.setTextAlignment(Qt.AlignCenter)
            color_map = {"DBC": "#4FD1C5", "DBF": "#6366F1", "ZIP": "#F59E0B",
                         "RAR": "#EF4444", "7Z": "#8B5CF6"}
            if ext in color_map:
                it2.setForeground(QColor(color_map[ext]))
            self.tbl_files.setItem(row, 2, it2)
            # Coluna 3: Tamanho formatado
            try:
                sz = path.stat().st_size
                if sz < 1024: szs = f"{sz} B"
                elif sz < 1024**2: szs = f"{sz/1024:.1f} KB"
                elif sz < 1024**3: szs = f"{sz/1024**2:.1f} MB"
                else: szs = f"{sz/1024**3:.2f} GB"
            except (OSError,): szs = "—"
            it3 = QTableWidgetItem(szs); it3.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.tbl_files.setItem(row, 3, it3)
            # Coluna 4: Data modificação
            try:
                import datetime as _dt
                mt = _dt.datetime.fromtimestamp(path.stat().st_mtime)
                dts = mt.strftime("%d/%m/%Y %H:%M")
            except (OSError,): dts = "—"
            it4 = QTableWidgetItem(dts); it4.setTextAlignment(Qt.AlignCenter)
            self.tbl_files.setItem(row, 4, it4)
            # Armazena o path em data do item 1
            it1.setData(Qt.UserRole, str(path))
        self.lbl_files_count.setText(f"{len(all_files)} arquivo(s) listado(s) (todos marcados por padrão).")

    def _marcar_todos_arquivos(self, state: bool) -> object:
        def _do() -> None:
            for r in range(self.tbl_files.rowCount()):
                w = self.tbl_files.cellWidget(r, 0)
                if w:
                    cb = w.property("cb")
                    if isinstance(cb, QCheckBox):
                        cb.setChecked(bool(state))
        return _do

    def _selected_files(self) -> Optional[list[str]]:
        """Retorna lista de paths absolutos de arquivos marcados (ou None se nenhum listado/marcado)."""
        out: list[str] = []
        for r in range(self.tbl_files.rowCount()):
            w = self.tbl_files.cellWidget(r, 0)
            cb = w.property("cb") if w else None
            if not isinstance(cb, QCheckBox) or not cb.isChecked():
                continue
            it = self.tbl_files.item(r, 1)
            if it is None: continue
            p = it.data(Qt.UserRole)
            if p: out.append(str(p))
        return out or None

    def _selected_system(self) -> str:
        for b in self.group_sys.buttons():
            if b.isChecked():
                return b.value()
        return "SIH-RD"

    def _current_step1_source(self) -> str:
        cid = self.group_source.checkedId()
        if cid == 0:
            return "ftp"
        if cid == 1:
            return "local"
        if cid == 2:
            return "archive"
        return "ftp"

    def _local_dir(self) -> str:
        t = self.lbl_dbc.text()
        return "" if t.startswith("(") and t.endswith(")") else t

    def _archive_dir(self) -> str:
        t = self.lbl_archive.text()
        return "" if t.startswith("(") and t.endswith(")") else t

    # ----------------------------------------------------------- STEP 2
    def _build_step2(self) -> QWidget:
        w = QScrollArea(); w.setWidgetResizable(True); w.setFrameShape(QFrame.NoFrame)
        inner = QWidget(); vl = QVBoxLayout(inner); vl.setContentsMargins(10, 6, 10, 6); vl.setSpacing(12)

        head = QLabel("Selecione a localização SUS:")
        head.setStyleSheet("font-weight:700;color:#E6FFFA;")
        vl.addWidget(head)
        sub = QLabel("Ordem cascateada: UFs → Macrorregiões → DRS (Regiões de Saúde). "
                     "Se nada for marcado em um nível, o sistema considera TODOS daquele nível.")
        sub.setStyleSheet("color:#94A3B8;font-size:11px;")
        sub.setWordWrap(True)
        vl.addWidget(sub)

        # =================================================== NÍVEL 1: UFs
        hl1 = QLabel("🟢 1. Estados (UF):"); hl1.setStyleSheet("color:#4FD1C5;font-weight:700;")
        vl.addWidget(hl1)
        row = QHBoxLayout(); row.setSpacing(6)
        self.btn_all_uf = QPushButton("☑ Todas UFs"); self._style_action_btn(self.btn_all_uf, "#4FD1C5")
        self.btn_none_uf = QPushButton("☒ Limpar"); self._style_action_btn(self.btn_none_uf, "#64748B")
        self.btn_ne = QPushButton("🌎 Nordeste"); self._style_action_btn(self.btn_ne, "#F59E0B")
        self.btn_se = QPushButton("🌎 Sudeste"); self._style_action_btn(self.btn_se, "#8B5CF6")
        self.btn_s = QPushButton("🌎 Sul"); self._style_action_btn(self.btn_s, "#22C55E")
        self.btn_co = QPushButton("🌎 Centro-Oeste"); self._style_action_btn(self.btn_co, "#3B82F6")
        self.btn_no = QPushButton("🌎 Norte"); self._style_action_btn(self.btn_no, "#EF4444")
        for b in (self.btn_all_uf, self.btn_none_uf, self.btn_ne, self.btn_se, self.btn_s, self.btn_co, self.btn_no):
            row.addWidget(b)
        row.addStretch(1)
        self.btn_all_uf.clicked.connect(self._select_all_uf)
        self.btn_none_uf.clicked.connect(self._deselect_all_uf)
        self.btn_ne.clicked.connect(lambda: self._select_region("NE"))
        self.btn_se.clicked.connect(lambda: self._select_region("SE"))
        self.btn_s.clicked.connect(lambda: self._select_region("S"))
        self.btn_co.clicked.connect(lambda: self._select_region("CO"))
        self.btn_no.clicked.connect(lambda: self._select_region("N"))
        vl.addLayout(row)

        self.chips_uf_wrap = QScrollArea(); self.chips_uf_wrap.setWidgetResizable(True); self.chips_uf_wrap.setFrameShape(QFrame.NoFrame)
        uf_inner = QWidget(); uf_flow = FlowLayout(uf_inner, margin=4, spacing=6)
        self.chips_uf: dict[str, ChipButton] = {}
        for uf in get_ufs():
            ch = ChipButton(f"🟢 {uf}", uf)
            ch.toggled.connect(lambda *_: self._refresh_geo_cascate())
            self.chips_uf[uf] = ch
            uf_flow.addWidget(ch)
        self.chips_uf_wrap.setWidget(uf_inner)
        self.chips_uf_wrap.setMinimumHeight(120)
        vl.addWidget(self.chips_uf_wrap)

        # =================================================== NÍVEL 2: MACRORREGIÕES
        splitter_geo = QSplitter(Qt.Vertical); splitter_geo.setHandleWidth(3)
        splitter_geo.setChildrenCollapsible(False)

        macro_frame = QFrame(); macro_frame.setStyleSheet(
            "QFrame{background:#121C1E;border-radius:10px;border:1px solid #233539;}")
        mv = QVBoxLayout(macro_frame); mv.setContentsMargins(10, 8, 10, 8); mv.setSpacing(6)
        row_macro = QHBoxLayout()
        lbl_macro = QLabel("🏛️ 2. Macrorregiões de Saúde:")
        lbl_macro.setStyleSheet("color:#6366F1;font-weight:700;")
        row_macro.addWidget(lbl_macro)
        row_macro.addStretch(1)
        self.btn_all_macro = QPushButton("☑ Todas da(s) UF(s)"); self._style_action_btn(self.btn_all_macro, "#6366F1")
        self.btn_none_macro = QPushButton("☒ Nenhuma (todas)"); self._style_action_btn(self.btn_none_macro, "#64748B")
        self.btn_all_macro.clicked.connect(self._select_all_macro)
        self.btn_none_macro.clicked.connect(self._deselect_all_macro)
        row_macro.addWidget(self.btn_all_macro); row_macro.addWidget(self.btn_none_macro)
        mv.addLayout(row_macro)
        self.lbl_macro_hint = QLabel("⬅ Primeiro selecione as UFs para ver as macrorregiões disponíveis.")
        self.lbl_macro_hint.setStyleSheet("color:#495F5E;font-style:italic;")
        mv.addWidget(self.lbl_macro_hint)
        self.chips_macro_wrap = QScrollArea(); self.chips_macro_wrap.setWidgetResizable(True); self.chips_macro_wrap.setFrameShape(QFrame.NoFrame)
        self._macro_inner = QWidget(); self._macro_flow = FlowLayout(self._macro_inner, margin=4, spacing=6)
        self.chips_macro_wrap.setWidget(self._macro_inner)
        self.chips_macro_wrap.setMinimumHeight(90)
        mv.addWidget(self.chips_macro_wrap, 1)
        self.chips_macro: dict[str, ChipButton] = {}
        splitter_geo.addWidget(macro_frame)

        # =================================================== NÍVEL 3: DRS / REGIÕES DE SAÚDE
        drs_frame = QFrame(); drs_frame.setStyleSheet(
            "QFrame{background:#121C1E;border-radius:10px;border:1px solid #233539;}")
        dv = QVBoxLayout(drs_frame); dv.setContentsMargins(10, 8, 10, 8); dv.setSpacing(6)
        row_drs = QHBoxLayout()
        lbl_drs = QLabel("🏥 3. Regiões de Saúde (DRS):")
        lbl_drs.setStyleSheet("color:#F59E0B;font-weight:700;")
        row_drs.addWidget(lbl_drs)
        row_drs.addStretch(1)
        self.btn_all_drs = QPushButton("☑ Todas DRS"); self._style_action_btn(self.btn_all_drs, "#F59E0B")
        self.btn_none_drs = QPushButton("☒ Nenhuma (todas)"); self._style_action_btn(self.btn_none_drs, "#64748B")
        self.btn_all_drs.clicked.connect(self._select_all_drs)
        self.btn_none_drs.clicked.connect(self._deselect_all_drs)
        row_drs.addWidget(self.btn_all_drs); row_drs.addWidget(self.btn_none_drs)
        dv.addLayout(row_drs)
        self.lbl_drs_hint = QLabel("⬅ Selecione UFs (e opcionalmente macrorregiões) para ver as DRS.")
        self.lbl_drs_hint.setStyleSheet("color:#495F5E;font-style:italic;")
        dv.addWidget(self.lbl_drs_hint)
        self.chips_drs_wrap = QScrollArea(); self.chips_drs_wrap.setWidgetResizable(True); self.chips_drs_wrap.setFrameShape(QFrame.NoFrame)
        self._drs_inner = QWidget(); self._drs_flow = FlowLayout(self._drs_inner, margin=4, spacing=6)
        self.chips_drs_wrap.setWidget(self._drs_inner)
        self.chips_drs_wrap.setMinimumHeight(120)
        dv.addWidget(self.chips_drs_wrap, 1)
        self.chips_drs: dict[str, ChipButton] = {}
        splitter_geo.addWidget(drs_frame)

        splitter_geo.setStretchFactor(0, 1)
        splitter_geo.setStretchFactor(1, 1)
        vl.addWidget(splitter_geo, 1)

        # Municípios (opcional, como era antes)
        splitter = QSplitter(Qt.Horizontal)
        leftp = QFrame(); leftp.setStyleSheet("QFrame{background:#121C1E;border-radius:10px;border:1px solid #233539;}")
        lp = QVBoxLayout(leftp); lp.setContentsMargins(8, 8, 8, 8); lp.setSpacing(6)
        lp.addWidget(QLabel("<b>🏙️  Municípios (opcional)</b>: selecione para filtrar."))
        self.search_muni = QLineEdit(); self.search_muni.setPlaceholderText("🔎 Digite nome do município...")
        self.search_muni.setStyleSheet(
            "padding:8px 10px;border-radius:10px;background:#162023;color:#E6FFFA;border:1px solid #233539;")
        self.search_muni.textChanged.connect(self._filter_muni_list)
        lp.addWidget(self.search_muni)
        self.list_muni = QListWidget(); self.list_muni.setSelectionMode(QAbstractItemView.MultiSelection)
        self.list_muni.setStyleSheet(
            "QListWidget{background:#162023;color:#E6FFFA;border:1px solid #233539;border-radius:10px;}"
            "QListWidget::item{padding:5px 8px;}"
            "QListWidget::item:selected{background:#14B8A6;color:#0F1A1C;font-weight:700;border-radius:6px;}")
        lp.addWidget(self.list_muni, 1)
        rowm = QHBoxLayout()
        btn_add_all_selected = QPushButton("➕ Adicionar selecionados")
        self._style_action_btn(btn_add_all_selected, "#6366F1")
        btn_add_all_selected.clicked.connect(self._add_selected_munis_to_chips)
        btn_clear_muni_chips = QPushButton("🗑 Limpar")
        self._style_action_btn(btn_clear_muni_chips, "#64748B")
        btn_clear_muni_chips.clicked.connect(self._clear_muni_chips)
        rowm.addWidget(btn_add_all_selected); rowm.addWidget(btn_clear_muni_chips); rowm.addStretch()
        lp.addLayout(rowm)
        splitter.addWidget(leftp)
        rightp = QFrame(); rightp.setStyleSheet("QFrame{background:#121C1E;border-radius:10px;border:1px solid #233539;}")
        rp = QVBoxLayout(rightp); rp.setContentsMargins(8, 8, 8, 8)
        rp.addWidget(QLabel("<b>✅ Selecionados:</b>"))
        self.muni_selected_flow = FlowLayout(spacing=4)
        msw = QWidget(); msw.setLayout(self.muni_selected_flow)
        scroll_muni_sel = QScrollArea(); scroll_muni_sel.setWidgetResizable(True); scroll_muni_sel.setFrameShape(QFrame.NoFrame)
        scroll_muni_sel.setWidget(msw)
        rp.addWidget(scroll_muni_sel, 1)
        splitter.addWidget(rightp)
        splitter.setStretchFactor(0, 3); splitter.setStretchFactor(1, 2)
        vl.addWidget(splitter, 2)

        self._muni_codes: list[tuple[str, str, str]] = []
        self._load_muni_from_cnes()
        self._fill_muni_list()

        w.setWidget(inner)
        self._refresh_geo_cascate()
        return w

    # ----------------------------------------------------------- NÍVEIS GEOGRÁFICOS helpers
    def _refresh_geo_cascate(self) -> None:
        try:
            ufs_sel = self._selected_ufs_nonempty()
            print(f"[DEBUG] _refresh_geo_cascate START | UFs marcadas = {sorted(ufs_sel)}", file=sys.stderr, flush=True)
            # --- Nível 2: macrorregiões ---
            while self._macro_flow.count():
                it = self._macro_flow.takeAt(0); wid = it.widget()
                if wid: wid.deleteLater()
            self.chips_macro.clear()
            macros_sorted: list[str] = []
            seen_mac: set[str] = set()
            for uf in ufs_sel:
                for m in lista_macrorregioes(uf):
                    if m not in seen_mac:
                        seen_mac.add(m); macros_sorted.append(m)
            prev_sel = {k for k, ch in self.chips_macro.items() if ch.isChecked()}
            if macros_sorted:
                self.lbl_macro_hint.setText(f"✅ {len(macros_sorted)} macrorregião(ões) disponíveis "
                                            f"para {len(ufs_sel)} UF(s) — marque as de interesse (ou todas).")
                self.lbl_macro_hint.setStyleSheet("color:#6366F1;")
                for m in macros_sorted:
                    ch = ChipButton(f"🏛️ {m}", m)
                    ch.toggled.connect(lambda *_: self._refresh_drs())
                    if m in prev_sel:
                        ch.setChecked(True)
                    self._macro_flow.addWidget(ch)
                    self.chips_macro[m] = ch
            else:
                self.lbl_macro_hint.setText("⬅ Primeiro selecione as UFs para ver as macrorregiões disponíveis.")
                self.lbl_macro_hint.setStyleSheet("color:#495F5E;font-style:italic;")
            # Atualiza DRS
            self._refresh_drs()
            print(f"[DEBUG] _refresh_geo_cascate OK | N Macros={len(macros_sorted)}  N chips_drs={len(self.chips_drs)}", file=sys.stderr, flush=True)
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[DEBUG] _refresh_geo_cascate FALHOU:\n{tb}", file=sys.stderr, flush=True)
            try:
                QMessageBox.warning(self, "Erro no recorte geográfico", f"{e}\n\n{tb[:3500]}")
            except Exception:
                pass

    def _refresh_drs(self) -> None:
        try:
            ufs_sel = self._selected_ufs_nonempty()
            macros_sel = self._selected_macros_nonempty()
            print(f"[DEBUG] _refresh_drs START | UFs={sorted(ufs_sel)}  Macros={sorted(macros_sel)}", file=sys.stderr, flush=True)
            while self._drs_flow.count():
                it = self._drs_flow.takeAt(0); wid = it.widget()
                if wid: wid.deleteLater()
            self.chips_drs.clear()
            drs_sorted: list[str] = []
            seen_d: set[str] = set()
            for uf in ufs_sel:
                if macros_sel:
                    for mac in macros_sel:
                        for d in lista_drs(uf, mac):
                            if d not in seen_d:
                                seen_d.add(d); drs_sorted.append(d)
                else:
                    for d in lista_drs(uf):
                        if d not in seen_d:
                            seen_d.add(d); drs_sorted.append(d)
            if drs_sorted:
                self.lbl_drs_hint.setText(f"✅ {len(drs_sorted)} DRS disponíveis.")
                self.lbl_drs_hint.setStyleSheet("color:#F59E0B;")
                for d in drs_sorted:
                    ch = ChipButton(f"🏥 {d}", d)
                    self._drs_flow.addWidget(ch)
                    self.chips_drs[d] = ch
            else:
                self.lbl_drs_hint.setText("⬅ Selecione UFs (e opcionalmente macrorregiões) para ver as DRS.")
                self.lbl_drs_hint.setStyleSheet("color:#495F5E;font-style:italic;")
            print(f"[DEBUG] _refresh_drs OK | N DRS chips={len(drs_sorted)}", file=sys.stderr, flush=True)
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[DEBUG] _refresh_drs FALHOU:\n{tb}", file=sys.stderr, flush=True)
            try:
                QMessageBox.warning(self, "Erro no recorte geográfico (DRS)", f"{e}\n\n{tb[:3500]}")
            except Exception:
                pass

    def _select_all_macro(self) -> None:
        for ch in self.chips_macro.values():
            ch.setChecked(True)

    def _deselect_all_macro(self) -> None:
        for ch in self.chips_macro.values():
            ch.setChecked(False)

    def _select_all_drs(self) -> None:
        for ch in self.chips_drs.values():
            ch.setChecked(True)

    def _deselect_all_drs(self) -> None:
        for ch in self.chips_drs.values():
            ch.setChecked(False)

    def _selected_ufs_nonempty(self) -> list[str]:
        return [uf for uf, ch in self.chips_uf.items() if ch.isChecked()]

    def _selected_ufs(self) -> list[str]:
        """Retorna UFs marcadas no Passo 3. NUNCA expande para todas as 27 UFs.

        Motivo (BUG R2 corrigido): o fallback que adicionava a lista de todas
        as UFs quando nenhum chip estava marcado causava download de TODAS
        as UFs do Brasil (27) no pipeline FTP SIA/SIH — DATASUS recusava por
        volume e aparecia erro de "Nenhum arquivo encontrado".
        """
        return self._selected_ufs_nonempty()

    def _selected_macros_nonempty(self) -> list[str]:
        return [m for m, ch in self.chips_macro.items() if ch.isChecked()]

    def _selected_macros(self) -> list[str]:
        out = self._selected_macros_nonempty()
        if out:
            return out
        # Se nenhuma marcada, considera TODAS da(s) UF(s) selecionada(s)
        allm: list[str] = []
        seen: set[str] = set()
        for uf in self._selected_ufs():
            for m in lista_macrorregioes(uf):
                if m not in seen:
                    seen.add(m); allm.append(m)
        return allm

    def _selected_drs(self) -> list[str]:
        out = [d for d, ch in self.chips_drs.items() if ch.isChecked()]
        if out:
            return out
        alld: list[str] = []
        seen: set[str] = set()
        for uf in self._selected_ufs():
            for mac in self._selected_macros_nonempty() or lista_macrorregioes(uf):
                for d in lista_drs(uf, mac):
                    if d not in seen:
                        seen.add(d); alld.append(d)
            if not self._selected_macros_nonempty():
                for d in lista_drs(uf):
                    if d not in seen:
                        seen.add(d); alld.append(d)
        return alld

    def _style_action_btn(self, b: QPushButton, color: str) -> None:
        b.setCursor(Qt.PointingHandCursor)
        b.setMinimumHeight(38)
        b.setStyleSheet(
            f"QPushButton{{background:#162023;color:{color};border:1px solid #233539;"
            f"border-radius:10px;padding:6px 12px;font-weight:700;font-size:12px;text-align:center;}}"
            f"QPushButton:hover{{background:#1B2B2F;border-color:{color};color:#FEF08A;}}"
            f"QPushButton:disabled{{color:#64748B;border-color:#1E293B;}}")

    def _select_all_uf(self) -> None:
        for ch in self.chips_uf.values():
            ch.setChecked(True)

    def _deselect_all_uf(self) -> None:
        for ch in self.chips_uf.values():
            ch.setChecked(False)

    _REGIONS = {
        "N":  ["AC","AM","AP","PA","RO","RR","TO"],
        "NE": ["AL","BA","CE","MA","PB","PE","PI","RN","SE"],
        "SE": ["ES","MG","RJ","SP"],
        "S":  ["PR","RS","SC"],
        "CO": ["DF","GO","MS","MT"],
    }

    def _select_region(self, region: str) -> None:
        self._deselect_all_uf()
        for uf in self._REGIONS.get(region, []):
            self.chips_uf[uf].setChecked(True)

    def _load_muni_from_cnes(self) -> None:
        try:
            from papahr.dashboard.cnes_loader import get_cnes_data
            data = get_cnes_data().load()
            md = data.municipios
            if not md.is_empty() and "CO_MUNICIPIO" in md.columns:
                cod = md["CO_MUNICIPIO"].cast(pl.Utf8).to_list()
                nome = md["NO_MUNICIPIO"].cast(pl.Utf8).to_list() if "NO_MUNICIPIO" in md.columns else cod
                uf: list[str] = [""] * md.height
                for col_uf_muni in (
                    "CO_SIGLA_ESTADO", "SIGLA_ESTADO", "SIGLA_UF", "SG_UF",
                    "CO_UF", "CO_ESTADO", "SGL_UF",
                ):
                    if col_uf_muni in md.columns:
                        vals = md[col_uf_muni].cast(pl.Utf8).fill_null(pl.lit("")).to_list()
                        if (
                            col_uf_muni.startswith("CO_")
                            and len(vals) > 0
                            and any(str(v).strip().isdigit() for v in vals if v is not None)
                        ):
                            mapping = _UF_CODIBGE_TO_SIGLA
                            vals = [mapping.get(str(v).strip().zfill(2), str(v).strip()) for v in vals]
                        else:
                            vals = [str(v).strip().upper() for v in vals]
                        uf = vals
                        break
                self._muni_codes = [(str(c), str(n), str(u)) for c, n, u in zip(cod, nome, uf)]
        except Exception:
            pass

    def _fill_muni_list(self, filter_text: str = "") -> None:
        self.list_muni.clear()
        ft = (filter_text or "").lower()
        count = 0
        for cod, nome, uf in self._muni_codes:
            if ft and ft not in f"{nome} {uf} {cod}".lower():
                continue
            label = f"[{uf}] {nome}  — {cod}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, (cod, nome, uf))
            self.list_muni.addItem(item)
            count += 1
            if count >= 4000:
                break

    def _filter_muni_list(self, text: str) -> None:
        self._fill_muni_list(text)

    def _add_selected_munis_to_chips(self) -> None:
        for item in self.list_muni.selectedItems():
            cod, nome, uf = item.data(Qt.UserRole)
            key = cod
            already = {self.muni_selected_flow.itemAt(i).widget().value()
                       for i in range(self.muni_selected_flow.count())
                       if self.muni_selected_flow.itemAt(i) and self.muni_selected_flow.itemAt(i).widget()}
            if key in already:
                continue
            ch = ChipButton(f"[{uf}] {nome} ({cod[-3:]})", key)
            ch.setChecked(True)
            self.muni_selected_flow.addWidget(ch)
        self.list_muni.clearSelection()

    def _clear_muni_chips(self) -> None:
        while self.muni_selected_flow.count():
            item = self.muni_selected_flow.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _selected_munis(self) -> Optional[list[str]]:
        out: list[str] = []
        for i in range(self.muni_selected_flow.count()):
            it = self.muni_selected_flow.itemAt(i)
            if it is None: continue
            w = it.widget()
            if isinstance(w, ChipButton):
                out.append(w.value())
        return out or None

    # ----------------------------------------------------------- STEP 3
    def _build_step3(self) -> QWidget:
        w = QScrollArea(); w.setWidgetResizable(True); w.setFrameShape(QFrame.NoFrame)
        inner = QWidget(); vl = QVBoxLayout(inner); vl.setContentsMargins(10, 6, 10, 6); vl.setSpacing(12)

        head = QLabel("Escolha o período de competência:")
        head.setStyleSheet("font-weight:700;color:#E6FFFA;")
        vl.addWidget(head)
        sub = QLabel("Quatro modos: presets rápidos, intervalo calendário, 1 mês específico de 1 ano "
                     "ou checklist SAZONAL de meses em qualquer ano (férias/janeiro/dezembro etc.).")
        sub.setStyleSheet("color:#94A3B8;font-size:11px;")
        sub.setWordWrap(True)
        vl.addWidget(sub)

        # Modo de período (4 opções)
        self.period_mode_group = QButtonGroup(self)
        modes = [
            ("⏰ Pré-definidos (rápido)",   "PRESET",   "#14B8A6"),
            ("🗓️  Intervalo MM/yyyy",        "RANGE",    "#6366F1"),
            ("📅 1 mês específico",          "ONEMONTH", "#F59E0B"),
            ("🏖️ Sazonal (férias etc.)",     "SAZONAL",  "#EF4444"),
        ]
        mode_bar = QHBoxLayout(); mode_bar.setSpacing(6)
        self.period_mode_btns: dict[str, QRadioButton] = {}
        for i, (label, key, color) in enumerate(modes):
            rb = QRadioButton(label)
            rb.setCursor(Qt.PointingHandCursor)
            rb.setStyleSheet(f"QRadioButton{{color:{color};font-weight:600;padding:6px 10px;border-radius:8px;}}"
                             f"QRadioButton:checked{{background:#1B2B2F;border:1px solid {color};}}")
            self.period_mode_btns[key] = rb
            self.period_mode_group.addButton(rb, i)
            mode_bar.addWidget(rb)
        mode_bar.addStretch(1)
        vl.addLayout(mode_bar)

        self.period_stack = QStackedWidget()
        self.period_stack.setMinimumHeight(320)

        # ---------------------------------------------------------------- 1. PRESET
        preset_page = QWidget(); pl = QVBoxLayout(preset_page); pl.setContentsMargins(4, 8, 4, 4); pl.setSpacing(10)
        hl = QLabel("⏰ Selecione um preset:"); hl.setStyleSheet("color:#4FD1C5;font-weight:700;")
        pl.addWidget(hl)
        presets_list = [
            ("💚 2024 INTEIRO — FTP 100% GARANTIDO",
                ("preset_year_prev_2", 2024)),
            ("✅ Ano ANTERIOR inteiro (" + str(QDate.currentDate().year() - 1) + ") — FTP sempre OK",
                ("preset_year_prev", QDate.currentDate().year() - 1)),
            ("⏪ Último mês",                           ("preset_last_1m",   1)),
            ("⏪ Últimos 3 meses",                      ("preset_last_3m",   3)),
            ("⏪ Últimos 6 meses",                      ("preset_last_6m",   6)),
            ("⏪ Último ano (12m)",                     ("preset_last_12m",  12)),
            ("📅 Ano ATUAL inteiro (Jan→hoje)",        ("preset_year_cur", -1)),
        ]
        flow = FlowLayout(spacing=8)
        for lab, (pk, n) in presets_list:
            b = ChipButton(lab, pk)
            is_destaque = (pk == "preset_year_prev_2")
            if is_destaque:
                b.setStyleSheet(
                    "QPushButton{background:linear-gradient(90deg,#0F766E,#14B8A6);color:white;"
                    "border-radius:16px;padding:12px 20px;font-weight:900;font-size:14px;"
                    "border:3px solid #2DD4BF;}"
                    "QPushButton:hover{background:linear-gradient(90deg,#115E59,#0F766E);"
                    "border:3px solid #5EEAD4;color:#F0FDFA;}"
                )
            if pk.startswith("preset_last_"):
                b.clicked.connect(partial(self._apply_period_preset, months_back=n, _update_mode=True))
            elif pk == "preset_year_cur":
                b.clicked.connect(partial(self._apply_period_year, offset=0, _update_mode=True))
            elif pk == "preset_year_prev_2":
                # Força 2024 fixo (sem depender do relógio)
                b.clicked.connect(partial(self._apply_period_year_fixed, year=2024, _update_mode=True))
            elif pk == "preset_year_prev":
                b.clicked.connect(partial(self._apply_period_year, offset=-1, _update_mode=True))
            self._preset_chip = b
            flow.addWidget(b)
        fw = QWidget(); fw.setLayout(flow); pl.addWidget(fw)
        pl.addStretch(1)
        self.period_stack.addWidget(preset_page)

        # ---------------------------------------------------------------- 2. RANGE calendário MM/yyyy
        range_page = QWidget(); rl = QVBoxLayout(range_page); rl.setContentsMargins(4, 8, 4, 4); rl.setSpacing(10)
        gf = QGroupBox("Selecione o intervalo de competências:")
        gf.setStyleSheet(
            "QGroupBox{border:1px solid #233539;border-radius:10px;margin-top:6px;padding:12px;color:#6366F1;font-weight:700;}"
            "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 6px;}")
        form = QFormLayout(gf); form.setSpacing(10)
        _ptbr = QLocale(QLocale.Portuguese, QLocale.Brazil)
        today = QDate.currentDate(); start_default = today.addMonths(-3)
        self.dt_start = QDateEdit()
        self.dt_start.setCalendarPopup(True); self.dt_start.setLocale(_ptbr); self.dt_start.setDisplayFormat("MM/yyyy")
        self.dt_start.setMinimumDate(QDate(1994, 1, 1)); self.dt_start.setMaximumDate(today)
        self.dt_start.setDate(QDate(start_default.year(), start_default.month(), 1))
        self.dt_start.setStyleSheet(
            "QDateEdit { padding:10px 14px; border-radius:10px; background:#0F1A1C; color:#E6FFFA; border:2px solid #14B8A6; font-size:14px; font-weight:bold; min-height:40px; }"
            "QDateEdit::drop-down { border:none; width:30px; background:#14B8A6; border-top-right-radius:8px; border-bottom-right-radius:8px; }"
            "QDateEdit::down-arrow { image:none; border:none; }")
        cal_s = self.dt_start.calendarWidget()
        if cal_s:
            cal_s.setLocale(_ptbr); cal_s.setGridVisible(True); cal_s.setFirstDayOfWeek(Qt.Monday)
            cal_s.setStyleSheet("""
                QCalendarWidget{background:#0F1A1C;color:#E6FFFA;border:1px solid #14B8A6;border-radius:10px;}
                QCalendarWidget QToolButton{background:#1B2B2F;color:#4FD1C5;border-radius:6px;padding:4px 8px;}
                QCalendarWidget QToolButton:hover{background:#233539;}
                QCalendarWidget QMenu{background:#1B2B2F;color:#E6FFFA;}
                QCalendarWidget QSpinBox{background:#162023;color:#4FD1C5;}
                QCalendarWidget QWidget#qt_calendar_navigationbar{background:#121C1E;border-bottom:1px solid #233539;}
                QCalendarWidget QAbstractItemView:enabled{color:#E6FFFA;selection-background-color:#4FD1C5;selection-color:#0F1A1C;}
                QCalendarWidget QAbstractItemView:disabled{color:#495F5E;}
                #qt_calendar_prevmonth,#qt_calendar_nextmonth{min-width:28px;}""")
        self.dt_end = QDateEdit()
        self.dt_end.setCalendarPopup(True); self.dt_end.setLocale(_ptbr); self.dt_end.setDisplayFormat("MM/yyyy")
        self.dt_end.setMinimumDate(QDate(1994, 1, 1)); self.dt_end.setMaximumDate(today)
        self.dt_end.setDate(QDate(today.year(), today.month(), 1))
        self.dt_end.setStyleSheet(self.dt_start.styleSheet())
        cal_e = self.dt_end.calendarWidget()
        if cal_e:
            cal_e.setLocale(_ptbr); cal_e.setGridVisible(True); cal_e.setFirstDayOfWeek(Qt.Monday)
            cal_e.setStyleSheet(cal_s.styleSheet())
        def _force_day1_start(newd: QDate):
            if newd.day() != 1 or newd > self.dt_end.date():
                fix = QDate(newd.year(), newd.month(), 1)
                if fix > self.dt_end.date():
                    self.dt_end.blockSignals(True); self.dt_end.setDate(QDate(fix.year(), fix.month(), 1)); self.dt_end.blockSignals(False)
                self.dt_start.blockSignals(True); self.dt_start.setDate(fix); self.dt_start.blockSignals(False)
        def _force_day1_end(newd: QDate):
            if newd.day() != 1 or newd < self.dt_start.date():
                fix = QDate(newd.year(), newd.month(), 1)
                if fix < self.dt_start.date():
                    self.dt_start.blockSignals(True); self.dt_start.setDate(fix); self.dt_start.blockSignals(False)
                self.dt_end.blockSignals(True); self.dt_end.setDate(fix); self.dt_end.blockSignals(False)
        self.dt_start.dateChanged.connect(_force_day1_start)
        self.dt_end.dateChanged.connect(_force_day1_end)
        lbl_s = QLabel("📅 Início (Mês/Ano):"); lbl_s.setStyleSheet("color:#4FD1C5;font-weight:bold;font-size:13px;")
        form.addRow(lbl_s, self.dt_start)
        lbl_e = QLabel("📅 Fim (Mês/Ano):"); lbl_e.setStyleSheet("color:#4FD1C5;font-weight:bold;font-size:13px;")
        form.addRow(lbl_e, self.dt_end)
        rl.addWidget(gf)
        rl.addStretch(1)
        self.period_stack.addWidget(range_page)

        # ---------------------------------------------------------------- 3. UM MÊS ESPECÍFICO
        onemonth_page = QWidget(); ol = QVBoxLayout(onemonth_page); ol.setContentsMargins(4, 8, 4, 4); ol.setSpacing(10)
        gf2 = QGroupBox("Selecione 1 mês em 1 ano específico:")
        gf2.setStyleSheet(
            "QGroupBox{border:1px solid #233539;border-radius:10px;margin-top:6px;padding:12px;color:#F59E0B;font-weight:700;}"
            "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 6px;}")
        form2 = QFormLayout(gf2); form2.setSpacing(12)
        self.combo_om_ano = QComboBox(); self.combo_om_ano.setCursor(Qt.PointingHandCursor)
        ycur = QDate.currentDate().year()
        for y in range(ycur, 1993, -1):
            self.combo_om_ano.addItem(str(y), y)
        self.combo_om_ano.setStyleSheet(
            "QComboBox{padding:8px 12px;border-radius:8px;background:#162023;color:#E6FFFA;border:1px solid #233539;}"
            "QComboBox::drop-down{border:none;width:24px;}"
            "QComboBox QAbstractItemView{background:#0F1A1C;color:#E6FFFA;selection-background-color:#F59E0B;selection-color:#0F1A1C;}")
        _MESES_BR = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho","Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]
        self.combo_om_mes = QComboBox(); self.combo_om_mes.setCursor(Qt.PointingHandCursor)
        for i, nm in enumerate(_MESES_BR, 1):
            self.combo_om_mes.addItem(f"{i:02d} — {nm}", i)
        self.combo_om_mes.setStyleSheet(self.combo_om_ano.styleSheet())
        self.combo_om_mes.setCurrentIndex(QDate.currentDate().month() - 1)
        form2.addRow("📅 Ano:", self.combo_om_ano)
        form2.addRow("📅 Mês:", self.combo_om_mes)
        ol.addWidget(gf2)
        ol.addStretch(1)
        self.period_stack.addWidget(onemonth_page)

        # ---------------------------------------------------------------- 4. SAZONAL (checklist meses em múltiplos anos)
        sazonal_page = QWidget(); sl = QVBoxLayout(sazonal_page); sl.setContentsMargins(4, 8, 4, 4); sl.setSpacing(8)
        # Barra anos
        hl_s = QLabel("🏖️ Selecione os ANOS e os MESES que deseja analisar (pode selecionar meses fora de ordem):")
        hl_s.setStyleSheet("color:#EF4444;font-weight:700;"); hl_s.setWordWrap(True)
        sl.addWidget(hl_s)
        row_sel = QHBoxLayout(); row_sel.setSpacing(6)
        btn_all_years = QPushButton("📅 Anos recentes (5 anos)"); self._style_action_btn(btn_all_years, "#EF4444")
        btn_all_months = QPushButton("🗓️ Todos meses"); self._style_action_btn(btn_all_months, "#F59E0B")
        btn_clear_saz = QPushButton("☒ Limpar tudo"); self._style_action_btn(btn_clear_saz, "#64748B")
        btn_ferias = QPushButton("🏖️ Férias (Jan, Jul, Dez)"); self._style_action_btn(btn_ferias, "#14B8A6")
        btn_carnaval = QPushButton("🎭 Carnaval / Fevereiro"); self._style_action_btn(btn_carnaval, "#8B5CF6")
        row_sel.addWidget(btn_all_years); row_sel.addWidget(btn_all_months); row_sel.addWidget(btn_ferias); row_sel.addWidget(btn_carnaval); row_sel.addWidget(btn_clear_saz); row_sel.addStretch(1)
        sl.addLayout(row_sel)

        scroll_saz = QScrollArea(); scroll_saz.setWidgetResizable(True); scroll_saz.setFrameShape(QFrame.NoFrame)
        saz_inner = QWidget(); saz_outer = QVBoxLayout(saz_inner); saz_outer.setContentsMargins(4, 4, 4, 4); saz_outer.setSpacing(10)

        header_row = QHBoxLayout(); header_row.setSpacing(6)
        header_row.addSpacing(72)
        for mi in range(12):
            lbl = QLabel(f"{mi+1:02d}")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setFixedWidth(44)
            lbl.setStyleSheet("color:#94A3B8;font-size:11px;font-weight:700;")
            header_row.addWidget(lbl)
        header_row.addStretch(1)
        saz_outer.addLayout(header_row)

        self.saz_checkboxes: dict[tuple[int,int], QCheckBox] = {}
        y_cur = QDate.currentDate().year()
        for y in range(y_cur, 2019, -1):   # últimos ~6 anos por padrão, botão adicionará mais
            row_y = QHBoxLayout(); row_y.setSpacing(6)
            year_btn = QPushButton(str(y)); year_btn.setCheckable(True); year_btn.setCursor(Qt.PointingHandCursor)
            year_btn.setFixedWidth(72); year_btn.setFixedHeight(30)
            year_btn.setStyleSheet("""QPushButton{background:#162023;color:#E6FFFA;border:1px solid #233539;border-radius:8px;font-weight:700;}
                                      QPushButton:checked{background:#EF4444;color:#FFF;border-color:#EF4444;}
                                      QPushButton:hover{border-color:#4FD1C5;}""")
            row_y.addWidget(year_btn)
            for mi in range(12):
                key = (y, mi+1)
                cb = QCheckBox(); cb.setCursor(Qt.PointingHandCursor)
                cb.setToolTip(f"{_MESES_BR[mi]} de {y}")
                cb.setFixedSize(QSize(40, 28))
                cb.setStyleSheet("""
                    QCheckBox{spacing:0;}
                    QCheckBox::indicator{width:36px;height:24px;border-radius:6px;border:1px solid #233539;background:#162023;}
                    QCheckBox::indicator:hover{border-color:#4FD1C5;}
                    QCheckBox::indicator:checked{background:linear-gradient(90deg,#EF4444,#F97316);border:1px solid #EF4444;}""")
                year_btn.toggled.connect(lambda c, yy=y: (self._set_saz_by_year(yy, c),))
                cb.stateChanged.connect(lambda *_: None)
                self.saz_checkboxes[key] = cb
                row_y.addWidget(cb)
            row_y.addStretch(1)
            saz_outer.addLayout(row_y)
        saz_outer.addStretch(1)
        scroll_saz.setWidget(saz_inner)
        sl.addWidget(scroll_saz, 1)

        # Helpers do Sazonal
        def _marca_ferias():
            for (y, m), cb in self.saz_checkboxes.items():
                cb.setChecked(m in (1, 7, 12))
        def _marca_carnaval():
            for (y, m), cb in self.saz_checkboxes.items():
                cb.setChecked(m == 2)
        def _marca_todos_meses():
            for cb in self.saz_checkboxes.values():
                cb.setChecked(True)
        def _anos_recentes():
            for (y, _), cb in self.saz_checkboxes.items():
                if y >= (QDate.currentDate().year() - 4):
                    for mi in range(1, 13):
                        self.saz_checkboxes[(y, mi)].setChecked(True)
        def _limpa_saz():
            for cb in self.saz_checkboxes.values():
                cb.setChecked(False)
        btn_ferias.clicked.connect(_marca_ferias)
        btn_carnaval.clicked.connect(_marca_carnaval)
        btn_all_months.clicked.connect(_marca_todos_meses)
        btn_all_years.clicked.connect(_anos_recentes)
        btn_clear_saz.clicked.connect(_limpa_saz)

        self.period_stack.addWidget(sazonal_page)

        self.period_mode_btns["PRESET"].setChecked(True)
        self.period_stack.setCurrentIndex(0)
        def _change_mode(_btn=None):
            mapping = {"PRESET": 0, "RANGE": 1, "ONEMONTH": 2, "SAZONAL": 3}
            for k, rb in self.period_mode_btns.items():
                if rb.isChecked():
                    self.period_stack.setCurrentIndex(mapping[k])
                    break
        for rb in self.period_mode_btns.values():
            rb.toggled.connect(_change_mode)
        vl.addWidget(self.period_stack, 1)

        # Resumo seleção
        self.lbl_period_summary = QLabel("")
        self.lbl_period_summary.setStyleSheet(
            "background:#121C1E;border:1px dashed #233539;border-radius:10px;padding:10px;color:#4FD1C5;font-weight:700;")
        self.lbl_period_summary.setWordWrap(True)
        self.lbl_period_summary.setMinimumHeight(44)
        vl.addWidget(self.lbl_period_summary)
        # Atualiza resumo quando usuário mexe em qualquer componente
        for k, rb in self.period_mode_btns.items():
            rb.toggled.connect(lambda *_: self._update_period_summary())
        self.dt_start.dateChanged.connect(lambda *_: self._update_period_summary())
        self.dt_end.dateChanged.connect(lambda *_: self._update_period_summary())
        self.combo_om_ano.currentIndexChanged.connect(lambda *_: self._update_period_summary())
        self.combo_om_mes.currentIndexChanged.connect(lambda *_: self._update_period_summary())
        for cb in self.saz_checkboxes.values():
            cb.stateChanged.connect(lambda *_: self._update_period_summary())

        w.setWidget(inner)
        self._update_period_summary()
        return w

    def _set_saz_by_year(self, year: int, checked: bool) -> None:
        for (y, m), cb in self.saz_checkboxes.items():
            if y == year:
                cb.setChecked(bool(checked))

    def _apply_period_preset(self, months_back: int, _update_mode: bool = False) -> None:
        if _update_mode:
            self.period_mode_btns["RANGE"].setChecked(True)
        qe = QDate.currentDate()
        qs = qe.addMonths(-max(1, int(months_back)))
        self.dt_start.blockSignals(True); self.dt_end.blockSignals(True)
        self.dt_start.setDate(QDate(qs.year(), qs.month(), 1))
        self.dt_end.setDate(QDate(qe.year(), qe.month(), 1))
        cs, ce = self.dt_start.calendarWidget(), self.dt_end.calendarWidget()
        if cs: cs.setSelectedDate(self.dt_start.date())
        if ce: ce.setSelectedDate(self.dt_end.date())
        self.dt_start.blockSignals(False); self.dt_end.blockSignals(False)
        self._update_period_summary()

    def _apply_period_year_fixed(self, year: int = 2024, _update_mode: bool = False) -> None:
        """Aplica um ano FIXO (ex: 2024) sem depender do relógio nem offset."""
        if _update_mode:
            self.period_mode_btns["RANGE"].setChecked(True)
        ds, de = QDate(int(year), 1, 1), QDate(int(year), 12, 1)
        self.dt_start.blockSignals(True); self.dt_end.blockSignals(True)
        cap = QDate.currentDate()
        if de > cap: de = QDate(cap.year(), cap.month(), 1)
        if ds > de:  ds = de
        self.dt_start.setDate(ds); self.dt_end.setDate(de)
        cs, ce = self.dt_start.calendarWidget(), self.dt_end.calendarWidget()
        if cs: cs.setSelectedDate(self.dt_start.date())
        if ce: ce.setSelectedDate(self.dt_end.date())
        self.dt_start.blockSignals(False); self.dt_end.blockSignals(False)
        self._update_period_summary()

    def _apply_period_year(self, offset: int = 0, _update_mode: bool = False) -> None:
        if _update_mode:
            self.period_mode_btns["RANGE"].setChecked(True)
        y = QDate.currentDate().year() + int(offset)
        if offset == 0:
            # Ano ATUAL: Janeiro 1 → mês corrente
            today = QDate.currentDate()
            ds, de = QDate(y, 1, 1), QDate(y, today.month(), 1)
        else:
            # Ano PASSADO (e qualquer outro offset): JANEIRO 1 → DEZEMBRO 1 (ano INTEIRO)
            ds, de = QDate(y, 1, 1), QDate(y, 12, 1)
        self.dt_start.blockSignals(True); self.dt_end.blockSignals(True)
        # Garante que datas não ultrapassem o máximo de dt_start/dt_end (max = today)
        cap = QDate.currentDate()
        if de > cap: de = QDate(cap.year(), cap.month(), 1)
        if ds > de:  ds = de
        self.dt_start.setDate(ds); self.dt_end.setDate(de)
        cs, ce = self.dt_start.calendarWidget(), self.dt_end.calendarWidget()
        if cs: cs.setSelectedDate(self.dt_start.date())
        if ce: ce.setSelectedDate(self.dt_end.date())
        self.dt_start.blockSignals(False); self.dt_end.blockSignals(False)
        self._update_period_summary()

    def _current_period_mode(self) -> str:
        for k, rb in self.period_mode_btns.items():
            if rb.isChecked():
                return k
        return "RANGE"

    def _selected_months(self) -> list[tuple[int, int]]:
        """Retorna lista (ano, mês) de todos os meses selecionados, em qualquer modo."""
        months: list[tuple[int, int]] = []
        mode = self._current_period_mode()
        if mode == "RANGE":
            qs, qe = self.dt_start.date(), self.dt_end.date()
            if qe < qs:
                qs, qe = qe, qs
            ys, ms = qs.year(), qs.month()
            ye, me = qe.year(), qe.month()
            y, m = ys, ms
            while (y, m) <= (ye, me):
                months.append((y, m))
                m += 1
                if m > 12:
                    m = 1; y += 1
        elif mode == "ONEMONTH":
            y = int(self.combo_om_ano.currentData())
            m = int(self.combo_om_mes.currentData())
            months.append((y, m))
        elif mode == "SAZONAL":
            months = sorted([(y, m) for (y, m), cb in self.saz_checkboxes.items() if cb.isChecked()])
        else:  # PRESET: usa range (último preset clicado atualiza o RANGE)
            qs, qe = self.dt_start.date(), self.dt_end.date()
            if qe < qs:
                qs, qe = qe, qs
            ys, ms = qs.year(), qs.month()
            ye, me = qe.year(), qe.month()
            y, m = ys, ms
            while (y, m) <= (ye, me):
                months.append((y, m))
                m += 1
                if m > 12:
                    m = 1; y += 1
        return sorted(set(months))

    def _selected_date_range(self) -> DateRange:
        months = self._selected_months()
        if not months:
            today = QDate.currentDate()
            return DateRange(year_start=today.year(), month_start=today.month(),
                             year_end=today.year(),   month_end=today.month())
        y0, m0 = months[0]
        y1, m1 = months[-1]
        return DateRange(year_start=y0, month_start=m0,
                         year_end=y1,   month_end=m1)

    def _update_period_summary(self) -> None:
        months = self._selected_months()
        if not months:
            self.lbl_period_summary.setText("⚠️ Nenhum mês selecionado — por favor selecione um período para continuar.")
            self.lbl_period_summary.setStyleSheet(
                "background:#121C1E;border:1px dashed #EF4444;border-radius:10px;padding:10px;color:#EF4444;font-weight:700;")
            return
        self.lbl_period_summary.setStyleSheet(
            "background:#121C1E;border:1px dashed #14B8A6;border-radius:10px;padding:10px;color:#4FD1C5;font-weight:700;")
        _MESES_BR = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]
        # Converte para string compacta: agrupa intervalos contínuos
        def fmt_month(y, m): return f"{_MESES_BR[m-1]}/{y}"
        # Agrupar
        groups: list[str] = []
        run_start = months[0]
        prev = months[0]
        for cur in months[1:]:
            py, pm = prev
            expected_m = pm + 1; expected_y = py
            if expected_m > 12: expected_m = 1; expected_y += 1
            if cur == (expected_y, expected_m):
                prev = cur
                continue
            # Fecha grupo
            if run_start == prev:
                groups.append(fmt_month(*run_start))
            else:
                groups.append(f"{fmt_month(*run_start)} → {fmt_month(*prev)}")
            run_start = cur
            prev = cur
        # Fecha último
        if run_start == prev:
            groups.append(fmt_month(*run_start))
        else:
            groups.append(f"{fmt_month(*run_start)} → {fmt_month(*prev)}")
        txt = (f"✅ {len(months)} mês(es) selecionado(s): " + " • ".join(groups))
        if len(txt) > 260:
            txt = txt[:257] + "..."
        self.lbl_period_summary.setText(txt)

    # ----------------------------------------------------------- STEP 4
    def _build_step4(self) -> QWidget:
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(14, 14, 14, 14)
        vl.setSpacing(14)

        hl = QLabel("4️⃣ Confirmar Configuração & Executar Extração")
        hl.setStyleSheet("font-size:17px;font-weight:700;color:#4FD1C5;")
        vl.addWidget(hl)

        hint = QLabel(
            "💡 Todos os hospitais das UFs e regiões selecionadas no Passo 3 serão extraídos integralmente. "
            "A seleção de hospitais para benchmarking e comparação é feita diretamente nas abas de gráficos do Dashboard."
        )
        hint.setStyleSheet(
            "background:#1B2B2F;color:#4FD1C5;border:1px solid #233539;border-radius:10px;padding:12px;font-size:12px;"
        )
        hint.setWordWrap(True)
        vl.addWidget(hint)

        # Card de Resumo dos Parâmetros
        summary_card = CardWidget()
        sl = summary_card.layout_()
        sl.addWidget(QLabel("<b>📋 Resumo da Extração Configurada:</b>"))
        
        self.lbl_step4_summary = QLabel("Aguardando confirmação dos passos anteriores...")
        self.lbl_step4_summary.setStyleSheet("color:#E6FFFA;font-size:13px;padding:8px;")
        self.lbl_step4_summary.setWordWrap(True)
        sl.addWidget(self.lbl_step4_summary)
        vl.addWidget(summary_card)
        vl.addStretch()

        return _wrap_step_scrollable(w)

    def _load_all_cnes(self) -> None:
        """Handler botão verde '🔄 Carregar hospitais do CNES'.

        Carrega TODOS os hospitais da(s) UF(s) selecionada(s) no Step3.
        NÃO aplica filtro de Macro / DRS aqui — usuário vê tamanho TOTAL
        do estado primeiro. Para aplicar recorte territorial (Macro+DRS+Munic),
        usar botão roxo '📍 Aplicar filtros geográficos'.
        """
        self._reload_hospitals_from_cnes(apply_geo_filter=False)

    def _apply_geo_filter(self) -> None:
        """Handler botão roxo '📍 Aplicar filtros geográficos'.

        ORDEM CORRETA (conforme usuário perguntou):
          1. PRIMEIRO carrega TODOS os hospitais da(s) UF(s) selecionada(s)
             (Bronze/CNES CSV completo, evita perda de hospitais de fronteira).
          2. DEPOIS aplica recorte em MEMÓRIA restringindo a:
               - MACRORREGIÃO SUS selecionada
               - DRS (Região de Saúde) selecionada
               - Município se disponível
        """
        self._reload_hospitals_from_cnes(apply_geo_filter=True)

    def _reload_hospitals_from_cnes(self, apply_geo_filter: bool = True) -> None:
        # Feedback IMEDIATO para UI não parecer "não responde" (congelada)
        if hasattr(self, "lbl_hosp_status") and self.lbl_hosp_status is not None:
            msg0 = (
                "⏳ Carregando CNES (1º hospitais das UF depois filtro territorial)..."
                if apply_geo_filter else
                "⏳ Carregando TODOS os hospitais das UF selecionadas..."
            )
            self.lbl_hosp_status.set_status(msg0, "INFO")
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
        except Exception:
            pass

        self._hospitals_data: list[tuple[str, str, str, str]] = []
        sel_ufs: set[str] = set(self._selected_ufs_nonempty() or [])
        sel_macros_pre: set[str] = set(self._selected_macros_nonempty() or [])
        sel_drs_pre: set[str] = set(d for d, ch in self.chips_drs.items() if ch.isChecked())
        if apply_geo_filter:
            sel_macros: set[str] = set(sel_macros_pre)
            sel_drs: set[str] = set(sel_drs_pre)
        else:
            sel_macros: set[str] = set()
            sel_drs: set[str] = set()
        errors: list[str] = []
        warnings: list[str] = []
        using_source: str = "CNES local"
        print(f"[DEBUG] _reload_hospitals_from_cnes START | apply_geo_filter={apply_geo_filter} sel_ufs={sorted(sel_ufs)} sel_macros={sorted(sel_macros_pre)} sel_drs={sorted(sel_drs_pre)}", file=sys.stderr, flush=True)

        # ╔══════════════════════════════════════════════════════════════════╗
        # ║  🚨 CORREÇÃO FINAL: Try/Catch SEPARADO POR HEURÍSTICA.          ║
        # ║  Antes: get_db() = DuckDB LOCKADO (PID 26336) → EXCEPT GERAL    ║
        # ║         captura TUDO → pula Heurística 2 (CNES CSV local).      ║
        # ║  Agora: Cada heurística tem SEU PRÓPRIO try/except. Falhou 1?   ║
        # ║         Tenta 2, 3, 4. Atras que CSV do CNES está 100% OK.      ║
        # ╚══════════════════════════════════════════════════════════════════╝

        # -------------------------------------------------------------- H1: DuckDB silver.dim_hospitais (se aberto)
        used_dim_hospitais = False
        try:
            db = get_db()
        except Exception as exc_ldb:  # pragma: no cover - DuckDB lockado/rota corrompida
            tb = traceback.format_exc()
            errors.append(f"(DuckDB não abriu: {exc_ldb})")
            print(f"[DEBUG] Heuristica1 FALHOU em get_db():\n{tb}", file=sys.stderr, flush=True)
            db = None
        if db is not None:
            try:
                if db.has_persisted_data():
                    q = """
                    SELECT DISTINCT
                        cnes,
                        COALESCE(no_fantasia, nome_hospital, no_razao_social, '') AS nome,
                        COALESCE(uf, '') AS uf,
                        COALESCE(no_macrorregiao, '') AS no_macrorregiao,
                        COALESCE(no_regiao_saude, '') AS no_regiao_saude,
                        COALESCE(tipo_estabelecimento, '') AS tipo
                    FROM silver.dim_hospitais
                    WHERE 1=1
                    """
                    params: list[object] = []
                    if sel_ufs:
                        q += " AND uf IN (" + ",".join(["?"] * len(sel_ufs)) + ")"
                        params.extend(list(sel_ufs))
                    if sel_macros:
                        q += " AND no_macrorregiao IN (" + ",".join(["?"] * len(sel_macros)) + ")"
                        params.extend(list(sel_macros))
                    if sel_drs:
                        q += " AND no_regiao_saude IN (" + ",".join(["?"] * len(sel_drs)) + ")"
                        params.extend(list(sel_drs))
                    q += " ORDER BY uf, nome LIMIT 15000"
                    dim_df = db.sql_to_polars(q, params=params if params else None)
                    if not dim_df.is_empty():
                        added = 0
                        for row in dim_df.iter_rows(named=True):
                            c = str(row.get("cnes") or "").strip()
                            if not c: continue
                            n = str(row.get("nome") or c).strip()
                            u = str(row.get("uf") or "").strip()
                            t = str(row.get("tipo") or "").strip()
                            self._hospitals_data.append((c, n, u, t))
                            added += 1
                        used_dim_hospitais = True
                        using_source = f"DuckDB silver.dim_hospitais ({added:,} registros)"
                        if sel_macros or sel_drs:
                            macro_ok = bool(sel_macros); drs_ok = bool(sel_drs)
                            warnings.append(
                                f"✅ Filtro por {'Macro ' if macro_ok else ''}"
                                f"{' + DRS' if (macro_ok and drs_ok) else ('DRS' if drs_ok and not macro_ok else '')}"
                                f" aplicado via DuckDB."
                            )
            except Exception as exc_dim:  # pragma: no cover - erro de schema ou query
                tb_dim = traceback.format_exc()
                errors.append(f"(DuckDB dim_hospitais indisponível: {exc_dim})")
                print(f"[DEBUG] Heuristica1 (query dim) FALHOU:\n{tb_dim}", file=sys.stderr, flush=True)

        # ---------------------------------------------------------- H2: CNES CSV local tbEstabelecimento (fallback PRINCIPAL)
        if not used_dim_hospitais:
            try:
                from papahr.dashboard.cnes_loader import get_cnes_data
                cnes = get_cnes_data()
                cnes.load()
                edf = cnes.hospitais
                try:
                    fu = getattr(cnes, "fonts_used", {}) or {}
                    parts = []
                    for _k, _v in list(fu.items())[:5]:
                        try:
                            parts.append(f"{str(_k).split('_',1)[0]}={Path(str(_v)).name}")
                        except Exception:
                            parts.append(str(_k))
                    meta_info = (
                        f"{len(edf):,} registros"
                        + ((" (" + ", ".join(parts) + ")") if parts else "")
                    )
                except Exception:
                    meta_info = f"{len(edf):,} registros" if edf is not None else ""
                using_source = f"CNES local {meta_info}".strip()
                if edf is None or edf.is_empty():
                    errors.append("Tabela `tbEstabelecimento*` vazia.")
                    edf = None
            except Exception as exc_h2:
                tb = traceback.format_exc()
                errors.append(f"(CNES CSV indisponível: {exc_h2})")
                print(f"[DEBUG] Heuristica2 (CNES CSV) FALHOU:\n{tb}", file=sys.stderr, flush=True)
                edf = None
            if edf is not None and not edf.is_empty():
                try:
                    # Nome: Fantasia / Razão Social
                    nome = None
                    for col in ("NO_FANTASIA", "NO_RAZAO_SOCIAL", "NO_FANTAS", "NO_RAZAO",
                                "NOME_FANTASIA", "RAZAO_SOCIAL", "NOME"):
                        if col in edf.columns:
                            nome = edf[col].cast(pl.Utf8).fill_null(pl.lit("")).to_list()
                            break
                    cod = None
                    for col in ("CO_CNES", "CNES", "COD_CNES"):
                        if col in edf.columns:
                            cod = edf[col].cast(pl.Utf8).fill_null(pl.lit("")).str.strip_chars().to_list()
                            break
                    uf: Optional[list[str]] = None
                    for col in ("SIGLA_UF",
                                "SIGLA_ESTADO_GESTOR", "SIGLA_UF_GESTOR",
                                "UF", "SG_UF", "SG_UF_GESTOR", "SG_ESTADO",
                                "CO_ESTADO_GESTOR", "CO_UF_GESTOR", "CO_UF"):
                        if col in edf.columns:
                            vals = edf[col].cast(pl.Utf8).fill_null(pl.lit("")).to_list()
                            if col.startswith("CO_"):
                                mapping = _UF_CODIBGE_TO_SIGLA
                                vals = [mapping.get(str(v).zfill(2), str(v)) for v in vals]
                            else:
                                vals = [str(v).strip().upper() for v in vals]
                            uf = vals
                            break
                    tipo: Optional[list[str]] = None
                    for col in ("DS_TIPO_ESTABELECIMENTO", "NO_TIPO", "TIPO_ESTABELECIMENTO",
                                "CO_TIPO_ESTABELECIMENTO"):
                        if col in edf.columns:
                            tipo = edf[col].cast(pl.Utf8).fill_null(pl.lit("")).to_list()
                            break

                    cod_mun_gestor: Optional[list[str]] = None
                    for col in ("CO_MUNICIPIO_GESTOR", "CO_MUNICIPIO", "MUNICIPIO_GESTOR"):
                        if col in edf.columns:
                            cod_mun_gestor = edf[col].cast(pl.Utf8).fill_null(pl.lit("")).to_list()
                            break
                    cod_reg_saude_cnes: Optional[list[str]] = None
                    for col in ("CO_REGIAO_SAUDE", "REGIAO_SAUDE", "CO_DRS"):
                        if col in edf.columns:
                            cod_reg_saude_cnes = edf[col].cast(pl.Utf8).fill_null(pl.lit("")).to_list()
                            break

                    if cod is None:
                        errors.append("Coluna CNES não encontrada (procurei CO_CNES/CNES/COD_CNES).")
                    else:
                        if nome is None:
                            nome = [str(c) for c in cod]
                        if uf is None:
                            uf = [""] * len(cod)
                        if tipo is None:
                            tipo = [""] * len(cod)
                        N = len(cod)
                        nome = list(nome) + [""] * max(0, N - len(nome))
                        uf   = list(uf)   + [""] * max(0, N - len(uf))
                        tipo = list(tipo) + [""] * max(0, N - len(tipo))
                        if cod_mun_gestor is not None:
                            cod_mun_gestor = list(cod_mun_gestor) + [""] * max(0, N - len(cod_mun_gestor))
                        else:
                            cod_mun_gestor = [""] * N
                        if cod_reg_saude_cnes is not None:
                            cod_reg_saude_cnes = list(cod_reg_saude_cnes) + [""] * max(0, N - len(cod_reg_saude_cnes))
                        else:
                            cod_reg_saude_cnes = [""] * N
                        added = 0
                        skipped_por_limite = 0
                        sel_ufs_l = list(sel_ufs)
                        sel_macros_l = list(sel_macros)
                        sel_drs_l = list(sel_drs)
                        # PRÉ-FILTRO vetorizado por SIGLA_UF (evita 598k chamadas hospital_bate_geo se só tem MG):
                        queremos_todas_ufs = not sel_ufs_l and not sel_macros_l and not sel_drs_l
                        indices_validos_por_uf: list[int]
                        if queremos_todas_ufs:
                            indices_validos_por_uf = list(range(N))
                        else:
                            ufs_set_pre = None
                            if sel_ufs_l:
                                ufs_set_pre = {str(u).strip().upper() for u in sel_ufs_l if u and str(u).strip()}
                            if ufs_set_pre and (sel_macros_l or sel_drs_l):
                                ufs_set_pre = None  # macros/drs pode precisar expandir
                            if ufs_set_pre:
                                indices_validos_por_uf = [i for i in range(N) if (uf[i] or "").strip().upper() in ufs_set_pre]
                            else:
                                indices_validos_por_uf = list(range(N))
                        for i in indices_validos_por_uf:
                            c = (cod[i] or "").strip()
                            if not c:
                                continue
                            n = (nome[i] or c).strip()
                            u = (uf[i] or "").strip()
                            t = (tipo[i] or "").strip()
                            n = (nome[i] or c).strip()
                            if not _is_hospital_establishment(t, n):
                                continue
                            mun_h = (cod_mun_gestor[i] or "").strip()
                            rs_h = (cod_reg_saude_cnes[i] or "").strip()
                            if not queremos_todas_ufs and not hospital_bate_geo(
                                uf_hospital=u,
                                co_municipio_hospital=mun_h or None,
                                co_regiao_saude_hospital=rs_h or None,
                                sel_ufs=sel_ufs_l,
                                sel_macros=sel_macros_l,
                                sel_drs=sel_drs_l,
                            ):
                                continue
                            # LIMITE: mesmo comportamento da Heuristica1 DuckDB (LIMIT 15000).
                            #   Sem isso, _hospitals_data armazena 598k tuplas e o filtro de busca
                            #   do _filter_hosp_list demora 2-3s POR letra (O(N) em 600k) → UI trava.
                            if added >= 15000:
                                skipped_por_limite += 1
                                continue
                            self._hospitals_data.append((c, n, u, t))
                            added += 1
                        if skipped_por_limite > 0:
                            warnings.append(
                                f"⚠ {skipped_por_limite:,} estabelecimentos omitidos do preview "
                                f"(limite 15.000 p/ não travar a UI). Use o filtro geográfico "
                                f"UF/Macro/DRS para refinar: todos continuam disponíveis no pipeline."
                            )
                        using_source = (
                            f"CNES local tbEstabelecimento* ({added:,} registros"
                            f" de {len(indices_validos_por_uf):,} elegíveis)"
                        )
                        if sel_macros or sel_drs:
                            avisos: list[str] = []
                            if sel_macros:
                                avisos.append(f"{len(sel_macros)} Macro(s)")
                            if sel_drs:
                                avisos.append(f"{len(sel_drs)} DRS")
                            warnings.append(
                                f"✅ Filtro cascata {' + '.join(avisos)} aplicado via CNES local "
                                f"(em cima do mapeamento Município ↔ Macro/DRS do projeto)."
                            )
                        if not sel_ufs and not sel_macros and not sel_drs:
                            warnings.append(
                                f"Nenhuma UF/Macro/DRS selecionada → mostrando TODOS os {added:,} hospitais."
                            )
                except Exception as exc_h2_iter:  # pragma: no cover
                    tb = traceback.format_exc()
                    errors.append(f"(CNES CSV iteração: {exc_h2_iter})")
                    print(f"[DEBUG] Heuristica2 (iteração CSV CNES) FALHOU:\n{tb}", file=sys.stderr, flush=True)

        # ---------------------------------------------------------- H3: Datasources CNES FTP/Local complementar
        try:
            from papahr.datasources.cnes import CNESRepository
            repo = CNESRepository()
            sel_ufs_l = list(sel_ufs)
            estabs = repo.load_health_establishments(newer=True, state_abbr=sel_ufs_l or None)
            if estabs.df is not None and not estabs.df.is_empty() and "CNES" in estabs.df.columns:
                # Pega colunas disponíveis
                col_cnes = "CNES"
                col_nome = "FANTASIA" if "FANTASIA" in estabs.df.columns else ("NO_ESTABELECIMENTO" if "NO_ESTABELECIMENTO" in estabs.df.columns else None)
                col_tipo = "TIPO" if "TIPO" in estabs.df.columns else None
                col_uf = "UF" if "UF" in estabs.df.columns else None
                ja_tem = {str(c or "").strip().zfill(7) for c, *_ in self._hospitals_data}
                added_extra = 0
                for row in estabs.df.select([c for c in (col_cnes, col_nome, col_uf, col_tipo) if c]).iter_rows():
                    if len(row) < 1: continue
                    cc = str(row[0] or "").strip()
                    if not cc or cc.zfill(7) in ja_tem:
                        continue
                    nn = (row[1] if len(row)>=2 else None) or cc
                    uu = (row[2] if len(row)>=3 else None) or ""
                    tt = (row[3] if len(row)>=4 else None) or ""
                    if not is_hospital_unit(str(tt), str(nn)):
                        continue
                    if sel_ufs_l:
                        sel_ufs_set = {str(u).strip().upper() for u in sel_ufs_l if u}
                        if sel_ufs_set and str(uu).strip().upper() not in sel_ufs_set:
                            continue
                    self._hospitals_data.append((str(cc), str(nn), str(uu), str(tt)))
                    ja_tem.add(cc.zfill(7))
                    added_extra += 1
                if added_extra:
                    using_source += f" + Datasources extra ({added_extra:,})"
        except Exception as exc_h3:  # pragma: no cover
            warnings.append(f"(fonte auxiliar CNES não usada: {exc_h3})")
            print(f"[DEBUG] Heuristica3 (datasources) FALHOU: {exc_h3}", file=sys.stderr, flush=True)

        # ---------------------------------------------------------- H4: Dedup por CNES (zfill 7)
        try:
            seen: set[str] = set()
            uniq: list[tuple[str, str, str, str]] = []
            for c, n, u, t in self._hospitals_data:
                k = str(c or "").strip().zfill(7)
                if not k or k in seen:
                    continue
                seen.add(k)
                uniq.append((str(c or ""), str(n or ""), str(u or ""), str(t or "")))
            self._hospitals_data = uniq
            print(f"[DEBUG] _reload_hospitals_from_cnes RESULTADO: {len(self._hospitals_data)} registros (fonte={using_source})", file=sys.stderr, flush=True)
        except Exception as exc_h4:
            tb = traceback.format_exc()
            errors.append(f"(Dedup por CNES: {exc_h4})")
            print(f"[DEBUG] Heuristica4 (dedup CNES) FALHOU:\n{tb}", file=sys.stderr, flush=True)

        # ---------------------------------------------------------- H5: PÓS-FILTRO GEOGRÁFICO EM MEMÓRIA
        #    (apply_geo_filter=True = botão roxo Aplicar filtros geográficos)
        #    ORDEM CORRETA que o usuário pediu:
        #    1) CARREGA TUDO da(s) UF(s) primeiro → 2) DEPOIS filtra Macro / DRS / Munic.
        geo_reduziu = False
        if apply_geo_filter and (sel_macros or sel_drs):
            try:
                from papahr.dashboard.cnes_loader import get_cnes_data
                total_antes = len(self._hospitals_data)
                # Busca metadados geográficos (Macro/DRS) a partir de CNES/Município do CNESLoader local
                dict_cnes_to_geo: dict[str, tuple[str, str]] = {}  # cnes_zf7 -> (macro, drs)
                try:
                    _cnes = get_cnes_data()
                    _cnes.load()
                    if _cnes.estabelecimentos is not None and not _cnes.estabelecimentos.is_empty():
                        cols = _cnes.estabelecimentos.columns
                        col_c = "CO_CNES" if "CO_CNES" in cols else ("CNES" if "CNES" in cols else None)
                        col_uf = "SIGLA_UF" if "SIGLA_UF" in cols else ("UF" if "UF" in cols else None)
                        col_codmun = ("CO_MUNICIPIO_GESTOR" if "CO_MUNICIPIO_GESTOR" in cols
                                      else ("CO_MUNICIPIO" if "CO_MUNICIPIO" in cols else None))
                        col_codreg = ("CO_REGIAO_SAUDE" if "CO_REGIAO_SAUDE" in cols
                                      else ("CO_DRS" if "CO_DRS" in cols else None))
                        if col_c:
                            df_sel = _cnes.estabelecimentos
                            ufs_ok = {str(u).strip().upper() for u in (sel_ufs or []) if str(u).strip()}
                            if ufs_ok and col_uf:
                                mask = df_sel[col_uf].cast(pl.Utf8).fill_null("").str.to_uppercase().is_in(list(ufs_ok))
                                df_sel = df_sel.filter(mask)
                            rows = df_sel.select(
                                [c for c in (col_c, col_uf, col_codmun, col_codreg) if c]
                            ).iter_rows() if df_sel.height > 0 else iter([])
                            for r in rows:
                                cc = str(r[0] or "").strip().zfill(7)
                                if not cc or not cc.isdigit() or len(cc) != 7:
                                    continue
                                uu = str(r[1] or "").strip().upper() if len(r) >= 2 else ""
                                cmun = str(r[2] or "") if len(r) >= 3 else ""
                                creg = str(r[3] or "") if len(r) >= 4 else ""
                                macro_res = ""
                                drs_res = ""
                                if uu:
                                    try:
                                        _uf, _mun, co_macro, no_macro, co_drs, no_drs = resolve_geo_enrichment(
                                            uf_sigla=uu,
                                            co_municipio_6dig=cmun or None,
                                            co_regiao_saude_cnes_cod=creg or None,
                                        )
                                        macro_res = no_macro or ""
                                        drs_res = no_drs or ""
                                    except Exception:
                                        pass
                                if macro_res or drs_res:
                                    dict_cnes_to_geo[cc] = (macro_res, drs_res)
                except Exception as exc_dict:
                    print(f"[DEBUG] H5 geo_dict falhou: {exc_dict}", file=sys.stderr, flush=True)

                # Mantém só os hospitais cujo (macro está na sel_macros) E/OU (drs está na sel_drs)
                macros_upper = {str(m).strip() for m in sel_macros if str(m).strip()}
                drs_upper = {str(d).strip() for d in sel_drs if str(d).strip()}
                mantem: list[tuple[str, str, str, str]] = []
                for c, n, u, t in self._hospitals_data:
                    cc7 = str(c or "").strip().zfill(7)
                    mmac = ""
                    ddrs = ""
                    if cc7 in dict_cnes_to_geo:
                        mmac, ddrs = dict_cnes_to_geo[cc7]
                    # Regras cascata rígidas (mesma lógica de hospital_bate_geo geo.reference L646):
                    # Macro selecionada? mmac deve match (igualdade + prefixo 8 chars)
                    passa_macro = True
                    if macros_upper:
                        mm_s = (mmac or "").strip()
                        bate = False
                        for sm in macros_upper:
                            sm_s = sm.strip()
                            if sm_s == mm_s:
                                bate = True; break
                            if len(mm_s) >= 8 and sm_s.startswith(mm_s[:8]):
                                bate = True; break
                            if len(sm_s) >= 8 and mm_s.startswith(sm_s[:8]):
                                bate = True; break
                        passa_macro = bate
                    passa_drs = True
                    if drs_upper:
                        dd_s = (ddrs or "").strip()
                        bate = False
                        for sd in drs_upper:
                            sd_s = sd.strip()
                            if sd_s == dd_s:
                                bate = True; break
                            if len(dd_s) >= 10 and sd_s.startswith(dd_s[:10]):
                                bate = True; break
                            if len(sd_s) >= 10 and dd_s.startswith(sd_s[:10]):
                                bate = True; break
                        passa_drs = bate
                    if passa_macro and passa_drs:
                        mantem.append((c, n, u, t))
                self._hospitals_data = mantem
                removidos = total_antes - len(mantem)
                if removidos > 0:
                    geo_reduziu = True
                    using_source += f" | Geo filtro removeu {removidos:,} (fora Macro/DRS)"
                    warnings.append(
                        f"📍 Recorte geográfico aplicado (Macro/DRS): "
                        f"{total_antes:,} → {len(mantem):,} hospitais mantidos."
                    )
                print(f"[DEBUG] H5 Pós-filtro geo OK. Antes={total_antes} Depois={len(self._hospitals_data)} | apply_geo_filter={apply_geo_filter}", file=sys.stderr, flush=True)
            except Exception as exc_h5:
                tb = traceback.format_exc()
                errors.append(f"(Pós-filtro geográfico: {exc_h5})")
                print(f"[DEBUG] H5 Pós-filtro geo FALHOU:\n{tb}", file=sys.stderr, flush=True)

        # Feedback UI
        if not self._hospitals_data:
            # Diagnóstico preciso: NÃO usar a mensagem genérica 'pasta compactada'
            # se o CNESLoader já carregou a base com sucesso.
            tem_cnes_local = False
            try:
                from papahr.dashboard.cnes_loader import get_cnes_data
                _cc = get_cnes_data()
                try:
                    _cc.load()
                    if _cc.estabelecimentos is not None and _cc.estabelecimentos.height > 0:
                        tem_cnes_local = True
                except Exception:  # noqa: BLE001
                    tem_cnes_local = False
            except Exception:  # noqa: BLE001
                tem_cnes_local = False

            filtros_ativados: list[str] = []
            if sel_ufs:
                filtros_ativados.append(f"{len(sel_ufs)} UF(s): {', '.join(sorted(sel_ufs)[:8])}"
                                         + ("..." if len(sel_ufs) > 8 else ""))
            if sel_macros:
                filtros_ativados.append(f"{len(sel_macros)} Macro(s)")
            if sel_drs:
                filtros_ativados.append(f"{len(sel_drs)} DRS")

            if tem_cnes_local:
                base_msg = (
                    "⚠️ Carregamos a base CNES local com sucesso, mas NENHUM hospital "
                    "passou no seu filtro.\n"
                    "   Motivo mais comum: você marcou UF/Macro/DRS incompatíveis entre si "
                    "(ex: só MG marcada + só Macro do RJ).\n"
                )
                if filtros_ativados:
                    base_msg += "\n📌 Filtros ATIVOS agora: " + " | ".join(filtros_ativados)
                    base_msg += (
                        "\n\n👉 Como resolver:\n"
                        "   1. Volte para o Passo 3 (Localização SUS).\n"
                        "   2. Marque pelo menos 1 UF, depois marque SOMENTE Macros/DRS DAQUELA UF.\n"
                        "   3. Ou clique em Limpar tudo no Passo3 para mostrar todos os hospitais BR.\n"
                        "   4. Depois volte aqui e clique no botão roxo '📍 Aplicar filtros geográficos'."
                    )
            else:
                base_msg = (
                    "⚠️ Nenhuma fonte de dados CNES encontrada.\n\n"
                    "   Temos 2 opções para carregar:\n"
                    "   A) Clique no botão verde '🔄 Carregar hospitais do CNES' acima —\n"
                    "      ele lê automaticamente arquivos 'tbEstabelecimento*.csv' que\n"
                    "      estiverem em C:\\DICIONARIO_DADOS\\BASE_DE_DADOS_CNES_*\\.\n"
                    "   B) No Passo 1 (Sistema & Fonte) use fonte 'Pasta compactada' e aponte\n"
                    "      para um .zip/.rar baixado do portal DATASUS com tbEstabelecimento.\n"
                )
            msg = base_msg
            if errors:
                msg += "\n\n🔴 Detalhes técnicos: " + " ; ".join(errors)
            if warnings:
                msg += "\n\nℹ " + "  ".join(warnings)
            if hasattr(self, "lbl_hosp_status") and self.lbl_hosp_status is not None:
                self.lbl_hosp_status.set_status(msg, "WARN")
            # ✅ BOTÃO NÃO FUNCIONAVA = NÃO HAVIA DIÁLOGO AO USUÁRIO (só WARN badge).
            #   Agora exibe QMessageBox se houver ERRO para o usuário SABER que algo
            #   aconteceu (ao invés de "cliquei e não apareceu nada").
            try:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self,
                    "⚠ Nenhum hospital listado",
                    msg[:3500],
                )
            except Exception:
                pass
        else:
            filtros: list[str] = []
            if sel_ufs:
                filtros.append(f"{len(sel_ufs)} UF(s)")
            if sel_macros:
                filtros.append(f"{len(sel_macros)} Macro(s)")
            if sel_drs:
                filtros.append(f"{len(sel_drs)} DRS")
            prefix = (f"Filtrando por {' + '.join(filtros)} → " if filtros else "Mostrando todos → ")
            body = (
                f"{prefix}{len(self._hospitals_data):,} hospital(is) listado(s)  "
                f"(fonte: {using_source}). "
                "Dê duplo clique ou selecione vários + '➕ Adicionar'."
            )
            if warnings:
                body += "\nℹ " + ("  ".join(warnings))
            if hasattr(self, "lbl_hosp_status") and self.lbl_hosp_status is not None:
                self.lbl_hosp_status.set_status(body, "OK")
            print(f"[DEBUG] _reload_hospitals_from_cnes OK | {len(self._hospitals_data)} hospitais carregados | source={using_source}", file=sys.stderr, flush=True)
        self._filter_hosp_list(self.search_hosp.text() if hasattr(self, "search_hosp") else "")

    def _filter_hosp_list(self, text: str = "") -> None:
        if not hasattr(self, "list_hosp") or self.list_hosp is None:
            return
        self.list_hosp.clear()
        ft = (text or "").lower().strip()
        tokens = ft.split() if ft else []
        n = 0
        raw = getattr(self, "_hospitals_data", []) or []
        total_avail = len(raw)

        # --- BUG #18 FIX: REORDENAÇÃO PRIORIDADE --------------------------
        # Coloca HOSPITAIS DAS UF(s) SELECIONADAS PRIMEIRO na lista visual.
        # (Antes: ordem = ordem do CSV CNES, que começa com RO/AC/AL... SP/MG no fim.)
        # Resultado: se limite 15000 cortar, UF selecionada NUNCA fica de fora.
        sel_ufs_now: set[str] = set()
        for s in (getattr(self, "_selected_ufs_nonempty", None) or (lambda: []))():
            if s and str(s).strip():
                sel_ufs_now.add(str(s).strip().upper())
        if sel_ufs_now and total_avail > 0:
            matching_uf: list[tuple[str, str, str, str]] = []
            others: list[tuple[str, str, str, str]] = []
            for row in raw:
                uu = (str(row[2] or "").strip().upper()) if len(row) >= 3 else ""
                if uu in sel_ufs_now:
                    matching_uf.append(row)
                else:
                    others.append(row)
            if matching_uf:
                raw = matching_uf + others
                print(f"[DEBUG] _filter_hosp_list reordenado. UF(s) selecionada(s) {sorted(sel_ufs_now)}: {len(matching_uf)} hospitais PRIMEIRO. Outros {len(others)} depois.", file=sys.stderr, flush=True)
        # -----------------------------------------------------------------

        for cnes, nome, uf, tipo in raw:
            # CNES 7 dígitos OFICIAL: zfill preserva zero à esquerda no display / busca
            def _z(c):
                s = str(c).strip()
                if not s or not s.isdigit(): return s
                return s.zfill(7)
            cnes_z = _z(cnes)
            haystack = f"{nome} {cnes_z} {uf} {tipo}".lower()
            passa = True
            for t in tokens:
                if len(t) >= 5 and t.isdigit():
                    t_z = t.zfill(7)
                    if t_z not in cnes_z.lower():
                        passa = False; break
                    continue
                if t not in haystack:
                    passa = False; break
            if not passa:
                continue
            label = f"[{uf or '—'}] {nome or '(sem nome)'}"
            if tipo:
                label += f"  ·  {tipo}"
            label += f"  ({cnes_z})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, (cnes_z, nome, uf, tipo))
            item.setToolTip(f"CNES: {cnes_z}\nNome: {nome}\nUF: {uf or '—'}\nTipo: {tipo or '—'}")
            self.list_hosp.addItem(item)
            n += 1
            if n >= 15000:
                break
        # Feedback: quantos aparecem vs quantos existem
        if hasattr(self, "search_hosp") and text:
            self.search_hosp.setPlaceholderText(
                f"{n:,} resultado(s) / {total_avail:,} total  (digite para filtrar mais...)"
            )
        elif hasattr(self, "search_hosp"):
            self.search_hosp.setPlaceholderText("Buscar hospital por nome ou CNES...")

    def _existing_hospital_chip_values(self) -> set[str]:
        """Códigos CNES que já estão como chip selecionado (evita duplicatas)."""
        seen: set[str] = set()
        for i in range(self.hosp_flow.count()):
            it = self.hosp_flow.itemAt(i)
            if it and it.widget() and isinstance(it.widget(), ChipButton):
                v = str(it.widget().value()).strip()
                if v and v.isdigit():
                    v = v.zfill(7)
                seen.add(v)
        return seen

    def _add_cnes_codes_from_text(self, text: str) -> tuple[int, list[str], int, list[str]]:
        """Adiciona hospitais a partir de um texto com códigos CNES separados por vírgula/espaco/linha.

        Validações (ordem):
          1) Parse códigos (regex \\d+ para extrair só numeros)
          2) Existe na base _hospitals_data (que JÁ foi filtrada por UF/Macro/DRS)?
          3) (FALLBACK NOVO BUG R1) — se NÃO existir em _hospitals_data, busca DIRETAMENTE
             na base CNES local tbEstabelecimento e valida contra hospital_bate_geo com a
             seleção ATUAL de UF/Macro/DRS do usuário. Isso permite que o usuário cole
             CNES antes de clicar em "Aplicar filtros geográficos" (o erro da imagem 1).
          4) Já está como chip? → skip (sem duplicata)
          5) Adiciona chip verde com NOME DO HOSPITAL.

        Retorna: (added:int, invalid_codes:list[str], skipped:int, added_with_names:list[str])
        """
        import re

        raw_codes = [c for c in re.findall(r"\d+", text or "") if c]
        if not raw_codes:
            return 0, [], 0, []

        # CNES 7 dígitos OFICIAL zfill(7): padroniza lookup, input, CO_CNES, already_chips
        # — evita "123456" vs "0123456" criar duplicatas / falso não-encontrado.
        def _z(c):
            s = str(c).strip()
            if not s or not s.isdigit():
                return s
            return s.zfill(7)

        lookup: dict[str, tuple[str, str, str]] = {}
        for cnes, nome, uf, tipo in getattr(self, "_hospitals_data", []) or []:
            lookup[_z(cnes)] = (nome, uf, tipo)

        already_chips = {_z(x) for x in self._existing_hospital_chip_values()}
        added = 0
        skipped = 0
        invalid: list[str] = []
        added_labels: list[str] = []

        # Parametros de selecao GEOGRAFICA ATUAL do usuario (para fallback)
        sel_ufs_cur = list(self._selected_ufs_nonempty() or [])
        sel_macros_cur = list(self._selected_macros_nonempty() or [])
        sel_drs_cur = list(d for d, ch in self.chips_drs.items() if ch.isChecked())

        for raw in raw_codes:
            c = _z(raw)
            if not c or not c.isdigit():
                continue
            if c in already_chips:
                skipped += 1
                continue
            info = lookup.get(c)

            # ===================================================== FALLBACK BUG R1:
            # Nao encontrou na lista carregada → busca direto na base CNES
            if info is None:
                try:
                    from papahr.dashboard.cnes_loader import get_cnes_data
                    from papahr.geo.reference import (
                        hospital_bate_geo, UF_CODIBGE_TO_SIGLA,
                    )
                    cnes_db = get_cnes_data()
                    cnes_db.load()
                    edf = cnes_db.estabelecimentos
                    match = (edf
                             .with_columns(pl.col("CO_CNES").cast(pl.Utf8, strict=False).str.strip_chars().str.zfill(7).alias("CO_CNES"))
                             .filter(pl.col("CO_CNES") == c)
                             .head(1))
                    if not match.is_empty():
                        row = match.row(0, named=True)
                        # Extrai NOME
                        nome_h = ""
                        for col in ("NO_FANTASIA", "NO_RAZAO_SOCIAL", "NO_FANTAS",
                                    "NO_RAZAO", "NOME_FANTASIA", "RAZAO_SOCIAL"):
                            val = row.get(col)
                            if val is not None and str(val).strip():
                                nome_h = str(val).strip()
                                break
                        if not nome_h:
                            nome_h = f"CNES {c}"
                        # Extrai UF com o mesmo fallback cascata do CNESLoader
                        uf_h = ""
                        for col in ("SIGLA_UF", "SIGLA_ESTADO_GESTOR", "SIGLA_UF_GESTOR",
                                    "UF", "SG_UF", "SG_UF_GESTOR", "SG_ESTADO",
                                    "CO_ESTADO_GESTOR", "CO_UF_GESTOR", "CO_UF"):
                            val = row.get(col)
                            if val is None:
                                continue
                            s = str(val).strip()
                            if col.startswith("CO_"):
                                s = (UF_CODIBGE_TO_SIGLA.get(s.zfill(2), s)
                                     if s else "")
                            s = s.upper()
                            if s:
                                uf_h = s
                                break
                        # Extrai municipio / regiao saude
                        mun_h = str(row.get("CO_MUNICIPIO_GESTOR", "") or "").strip() or None
                        rs_h = str(row.get("CO_REGIAO_SAUDE", "") or "").strip() or None
                        # Extrai tipo
                        tipo_h = str(row.get("CO_TIPO_ESTABELECIMENTO", "") or "").strip()
                        bate = hospital_bate_geo(
                            uf_hospital=uf_h,
                            co_municipio_hospital=mun_h,
                            co_regiao_saude_hospital=rs_h,
                            sel_ufs=sel_ufs_cur,
                            sel_macros=sel_macros_cur,
                            sel_drs=sel_drs_cur,
                        )
                        if bate:
                            info = (nome_h, uf_h, tipo_h)
                            # Adiciona no lookup (para duplicatas) e tb na lista _hospitals_data
                            lookup[c] = info
                            hd = getattr(self, "_hospitals_data", None)
                            if hd is None:
                                hd = []
                                setattr(self, "_hospitals_data", hd)
                            if is_hospital_unit(tipo_h, nome_h):
                                hd.append((c, nome_h, uf_h, tipo_h))
                        else:
                            invalid.append(f"{c} (fora da area selecionada: "
                                           f"UF={uf_h or '?'} mun={mun_h or '?'} "
                                           f"reg={rs_h or '?'})")
                            continue
                except Exception as _exc_cnes:  # noqa: BLE001
                    invalid.append(f"{c} (erro ao validar: {_exc_cnes})")
                    continue

            if info is None:
                invalid.append(c)
                continue
            nome, uf, tipo = info
            already_chips.add(c)
            ch = HospitalChipButton(cnes=c, nome=nome or "", uf=uf or "")
            self.hosp_flow.addWidget(ch)
            added_labels.append(f"🏥 {c}={nome[:50]}")
            added += 1

        return added, invalid, skipped, added_labels

    def _feedback_cnes_add(self, *, added: int, invalid: list[str], skipped: int,
                            added_labels: list[str]) -> None:
        """Exibe resumo amigável no StatusBadge do Step 4 (e opcionalmente QMessageBox se há inválidos)."""
        parts: list[str] = []
        if added:
            parts.append(f"✅ {added} CNES adicionado(s).")
        if skipped:
            parts.append(f"⏭ {skipped} já estava(m) selecionado(s).")
        if invalid:
            parts.append(f"⚠ {len(invalid)} código(s) NÃO encontrados na filtragem geográfica atual: "
                         f"{', '.join(invalid[:8])}{'…' if len(invalid) > 8 else ''}")

        if hasattr(self, "lbl_hosp_status") and self.lbl_hosp_status is not None:
            sel_ufs = self._selected_ufs_nonempty()
            sel_macros = self._selected_macros_nonempty()
            sel_drs = [d for d, ch in self.chips_drs.items() if ch.isChecked()]
            filter_note = ""
            if sel_ufs or sel_macros or sel_drs:
                geo_parts: list[str] = []
                if sel_ufs: geo_parts.append(f"{len(sel_ufs)} UF(s)")
                if sel_macros: geo_parts.append(f"{len(sel_macros)} Macro(s)")
                if sel_drs: geo_parts.append(f"{len(sel_drs)} DRS")
                filter_note = (
                    "  (Filtro geográfico ativo: " + " + ".join(geo_parts) +
                    ". Hospital inválido = CNES não existe OU não está na área selecionada)."
                )
            msg = " ".join(parts) if parts else "Nenhum código válido informado."
            if invalid:
                self.lbl_hosp_status.set_status(msg + filter_note, "WARN")
                try:
                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self,
                        "CNES não correspondem à área selecionada",
                        f"{len(invalid)} código(s) de CNES não foram encontrados na lista "
                        f"filtrada de hospitais atuais.\n\n"
                        f"Causas prováveis:\n"
                        f"  • O CNES não existe na base local do CNES.\n"
                        f"  • O CNES pertence a outra UF/Macro/DRS que a selecionada no Passo 2.\n\n"
                        f"Códigos rejeitados: {', '.join(invalid[:12])}"
                        f"{'…' if len(invalid) > 12 else ''}\n\n"
                        f"Sugestão: revise a seleção geográfica no Passo 2 e clique em "
                        f"'📍 Aplicar filtros geográficos' para recarregar a lista, "
                        f"ou use '🔄 Carregar hospitais do CNES' para listar TODOS (sem filtro).",
                    )
                except Exception:  # noqa: BLE001
                    pass
            elif added:
                self.lbl_hosp_status.set_status(msg, "OK")
            elif skipped:
                self.lbl_hosp_status.set_status(msg, "INFO")
            else:
                self.lbl_hosp_status.set_status(msg, "WARN")

    def _on_add_cnes_from_field(self) -> None:
        """Handler do campo permanente 'Digite CNES...' (Enter ou botão ➕ Adicionar CNES)."""
        if not hasattr(self, "ed_add_cnes_manual") or self.ed_add_cnes_manual is None:
            return
        text = self.ed_add_cnes_manual.text() or ""
        added, invalid, skipped, labels = self._add_cnes_codes_from_text(text)
        self._feedback_cnes_add(
            added=added, invalid=invalid, skipped=skipped, added_labels=labels,
        )
        if added or skipped:
            self.ed_add_cnes_manual.clear()

    def _add_selected_hospitals(self) -> None:
        selected = self.list_hosp.selectedItems()
        if not selected:
            return
        max_batch = 30
        added = 0
        already = set()
        for i in range(self.hosp_flow.count()):
            it = self.hosp_flow.itemAt(i)
            if it and it.widget() and isinstance(it.widget(), ChipButton):
                already.add(it.widget().value())
        for item in selected:
            cnes, nome, uf, _tipo = item.data(Qt.UserRole)
            if cnes in already:
                continue
            if added >= max_batch:
                break
            ch = HospitalChipButton(cnes=cnes, nome=nome or "", uf=uf or "")
            self.hosp_flow.addWidget(ch)
            already.add(cnes)
            added += 1
        self.list_hosp.clearSelection()
        if len(selected) > max_batch:
            if hasattr(self, "lbl_hosp_status") and self.lbl_hosp_status is not None:
                self.lbl_hosp_status.set_status(
                    f"Adicionados {max_batch} hospitais em lote para manter a visualização leve. "
                    f"Para incluir todos os hospitais na análise, deixe a lista limpa (padrão).",
                    "INFO"
                )

    def _clear_hosp_chips(self) -> None:
        while self.hosp_flow.count():
            item = self.hosp_flow.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _paste_cnes_dialog(self) -> None:
        dlg = QDialog(self); dlg.setWindowTitle("Colar códigos CNES")
        dlg.resize(560, 260)
        v = QVBoxLayout(dlg)
        hint = QLabel(
            "Cole abaixo os códigos CNES, separados por vírgula, espaço, tabulação ou "
            "uma linha por código:\n"
            "  (Exemplos: 2561492,2481286  ou  2561492 2481286  ou  2561492\\n2481286)"
        )
        hint.setWordWrap(True); v.addWidget(hint)
        te = QPlainTextEdit(); te.setPlaceholderText("2561492, 2481286, ...")
        v.addWidget(te, 1)
        warn = QLabel(
            "⚠ Apenas códigos CNES que batem com a UF/Macro/DRS selecionada(s) no Passo 2 "
            "serão aceitos — os demais serão rejeitados com explicação."
        )
        warn.setStyleSheet("color:#F59E0B;font-size:12px;"); warn.setWordWrap(True); v.addWidget(warn)
        bx = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bx.button(QDialogButtonBox.Ok).setText("✅ Validar e adicionar")
        bx.button(QDialogButtonBox.Cancel).setText("Cancelar")
        bx.accepted.connect(dlg.accept); bx.rejected.connect(dlg.reject)
        v.addWidget(bx)
        if dlg.exec() == QDialog.Accepted:
            text = te.toPlainText() or ""
            added, invalid, skipped, _labels = self._add_cnes_codes_from_text(text)
            self._feedback_cnes_add(
                added=added, invalid=invalid, skipped=skipped, added_labels=_labels,
            )

    def _selected_hospitals(self) -> Optional[list[str]]:
        """Retorna None para extrair 100% de todos os hospitais do estado/região selecionada (sem filtrar na carga)."""
        return None

    # ----------------------------------------------------------- Lógica
    def _on_run(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        try:
            system = self._selected_system()
            # Usa novo método genérico que resolve os 4 modos (preset/range/onemonth/sazonal)
            dr = self._selected_date_range()
            months_sel = self._selected_months()
            ufs = self._selected_ufs()
            macros = self._selected_macros()
            drs = self._selected_drs()
            counties = self._selected_munis()
            estabs = self._selected_hospitals()
            # ========================================================= BUG R3 FIX:
            # Hospital ESPECÍFICO selecionado no Step4 = não baixar 23 UFs à toa!
            # Se temos CNES selecionados, descobrimos a UF de CADA um (lookup rápido
            # na lista _hospitals_data ou fallback CNESLoader) e usamos APENAS estas
            # UFs no pipeline. Também zeramos macros/drs pois o usuário escolheu por
            # CNES, não por geografia. Isso resolve o caso do print: 1 hospital CE
            # baixava 23 UFs inteiras e demorava/fracassava no FTP.
            ufs_from_estabs: set[str] = set()
            estabs_com_uf_desconhecida: list[str] = []
            # Normaliza estabs para 7 dígitos zfill ANTES de qualquer lookup
            def _z7(c):
                s = str(c).strip()
                if s and s.isdigit(): return s.zfill(7)
                return s
            estabs_z = [_z7(x) for x in (estabs or [])]
            if estabs_z:
                hd = getattr(self, "_hospitals_data", None) or []
                lookup_uf: dict[str, str] = {}
                for _c, _n, _u, _t in hd:
                    lookup_uf[_z7(_c)] = (str(_u or "").upper() or "")
                try:
                    from papahr.dashboard.cnes_loader import get_cnes_data
                    from papahr.geo.reference import UF_CODIBGE_TO_SIGLA
                    import polars as pl
                    cnes_db = None
                    for cnes_cod in estabs_z:
                        cc = cnes_cod
                        uf_l = lookup_uf.get(cc, "")
                        if uf_l:
                            ufs_from_estabs.add(uf_l)
                            continue
                        if cnes_db is None:
                            try:
                                _t = get_cnes_data(); _t.load(); cnes_db = _t.estabelecimentos
                            except Exception:
                                cnes_db = None
                        uf_resolve = ""
                        if cnes_db is not None:
                            m = (cnes_db
                                 .with_columns(pl.col("CO_CNES").cast(pl.Utf8, strict=False).str.strip_chars().str.zfill(7).alias("CO_CNES"))
                                 .filter(pl.col("CO_CNES") == cc)
                                 .head(1))
                            if not m.is_empty():
                                row = m.row(0, named=True)
                                for col in ("SIGLA_UF", "SIGLA_ESTADO_GESTOR",
                                            "SIGLA_UF_GESTOR", "UF", "SG_UF",
                                            "SG_UF_GESTOR", "SG_ESTADO",
                                            "CO_ESTADO_GESTOR", "CO_UF_GESTOR", "CO_UF"):
                                    v = row.get(col)
                                    if v is None:
                                        continue
                                    sv = str(v).strip()
                                    if col.startswith("CO_"):
                                        sv = (UF_CODIBGE_TO_SIGLA.get(sv.zfill(2), sv)
                                              if sv else "")
                                    sv = sv.upper()
                                    if sv:
                                        uf_resolve = sv; break
                        if uf_resolve:
                            ufs_from_estabs.add(uf_resolve)
                        else:
                            estabs_com_uf_desconhecida.append(cc)
                except Exception:  # noqa: BLE001
                    # Em ambiente sem CNES, usa o que o usuário marcou no Passo 3
                    ufs_from_estabs = set()
            # Substitui ufs do Passo3 apenas se conseguimos UFs a partir dos estabs
            if estabs and ufs_from_estabs:
                if len(ufs) != len(ufs_from_estabs) or set(ufs) != ufs_from_estabs:
                    log.info("[_on_run] UFs do pipeline reduzidas %s -> %s "
                             "pois %s hospital(is) especifico(s) selecionado(s) no Step4.",
                             ufs, sorted(ufs_from_estabs), len(estabs))
                ufs = sorted(ufs_from_estabs)
                macros = []; drs = []  # filtro geográfico sobrescrito por CNES direto
            # ========================================================= FIM BUG R3 FIX
            # ========================================================= BLOQUEIO (BUG R2):
            # Nunca permite rodar pipeline SEM UFs selecionadas (antes rodava 27 UFs).
            # Nunca permite datas sem período.
            if not ufs:
                QMessageBox.warning(
                    self,
                    "Selecione pelo menos 1 UF",
                    "Nenhuma Unidade Federativa (UF) foi selecionada no Passo 3.\n\n"
                    "👉 Volte ao Passo 3 e marque ao menos 1 chip de UF (ex: MG).\n"
                    "   Não é permitido baixar dados de TODAS as 27 UFs de uma vez "
                    "(o DATASUS recusa por volume).",
                )
                return
            if not dr:
                QMessageBox.warning(
                    self, "Selecione o período",
                    "Nenhum mês/ano foi selecionado no Passo 2. Volte e marque "
                    "ao menos 1 mês.",
                )
                return
            # ========================================================= FTP WARN AHEAD FTP
            # Pré-validação: meses do período que são FUTUROS ou MUITO RECENTES
            # (hoje - 3 meses). DATASUS publica competência fechada ~3 meses depois.
            # EXTRA: se PERÍODO TODO >= (ano_atual - 1), também aciona — evita erro
            # quando relógio está avançado (ex: 08/2026 e usuário pede 2025 inteiro).
            if months_sel:
                from datetime import date as _dt_date
            req_args = dict(
                information_system=system,
                state_abbr=ufs,
                county_id=counties,
                health_establishment_id=estabs,
                save_csv=self.chk_csv.isChecked(),
                save_parquet=self.chk_parquet.isChecked(),
                date_range=dr,
                # NOVOS campos da carga SUS (Fases 3A e 3B) — serão consumidos na Fase 4 (ETL persistente)
                macro_regions=macros,
                health_regions=drs,
                specific_months=months_sel,
                # NOVO: Fase 3C — arquivos manualmente selecionados
                selected_files=self._selected_files(),
            )
            if self._current_step1_source() == "local":
                d = self._local_dir()
                if not d:
                    QMessageBox.warning(self, "Selecione a pasta DBC",
                        "Escolha uma pasta com arquivos DBC ou use Download FTP.")
                    return
                req_args["dbc_dir_path"] = Path(d)
            elif self._current_step1_source() == "archive":
                a = self._archive_dir()
                if not a:
                    QMessageBox.warning(self, "Selecione a pasta compactada",
                        "Escolha uma pasta Windows onde você colocou os arquivos .zip/.rar/.7z.")
                    return
                req_args["archive_dir_path"] = Path(a)
            req = PipelineRequest(**req_args)
            self._last_request = req
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Parâmetros inválidos", str(e))
            return
        self.btn_run.setEnabled(False); self.btn_next.setEnabled(False); self.btn_prev.setEnabled(False)
        self.progress.set_value_smooth(0)
        self.status_badge.set_status("Extraindo dados...", "LOADING")
        self.lbl_stage.setText(f"Iniciando processamento de {system}...")
        self._worker = PipelineWorker(req)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_result)
        self._worker.failed.connect(self._on_failure)
        self._worker.start()

    def _on_progress(self, p: PipelineProgress) -> None:
        self.lbl_stage.setText(f"[{p.stage}] {p.message}".strip())
        if p.total:
            pct = int(100 * p.current / max(1, p.total))
            self.progress.set_value_smooth(pct)
        if p.stage == "Concluido":
            self.status_badge.set_status("Concluído", "OK")

    def _on_result(self, result: PipelineResult) -> None:
        self._last_result = result

        # ================================================================= DuckDB Medallion ETL
        #   Grava o resultado em Bronze (raw Polars) → Silver (producao normalizada)
        #   → Gold (agregados pré-calculados). Roda em try/except 100% safe:
        #   SE der ERRO em QUALQUER camada, não trava a UI, grava no audit_log
        #   e o usuário continua usando dashboard em memória normalmente.
        load_run_id: str | None = None
        try:
            db = get_db()
            try:
                from papahr.auth import current_user
                u = current_user()
                if u:
                    db.audit_log("UI_PIPELINE_SUCCESS", "UI", user=u.username,
                                 details={"system": result.system, "rows": result.rows,
                                          "seconds": result.elapsed_seconds})
            except Exception:
                pass

            # --- UFs usadas neste pipeline (pega da última requisição se existir)
            ufs: list[str] = ["BR"]
            if self._last_request and isinstance(self._last_request, PipelineRequest):
                sab = self._last_request.state_abbr
                ufs = [sab] if isinstance(sab, str) else list(sab) if isinstance(sab, list) else ["BR"]

            # --- Chama persistência Bronze→Silver→Gold
            load_run_id = db.persist_result_to_duckdb(
                system=result.system,
                df=result.df,
                ufs=ufs,
                output_parquet=result.output_parquet,
                n_files=0,
                n_rows=result.rows,
            )
        except Exception as e:
            try:
                log.error("Erro ao gravar DuckDB Medallion: %s: %s", type(e).__name__, e)
            except Exception:
                pass

        self.btn_run.setEnabled(True); self.btn_next.setEnabled(True); self.btn_prev.setEnabled(True)
        self.progress.set_value_smooth(100)
        self.status_badge.set_status("Sucesso", "OK")
        self.btn_dashboard.setEnabled(True)
        self._show_preview_auto()   # ← abre automaticamente o painel de prévia APENAS quando tiver resultado
        msg_extra = f" • Load Run: {load_run_id[:12]}…" if load_run_id else ""
        self.lbl_stage.setText(
            f"Concluído: {result.rows:,} linhas em {result.elapsed_seconds:.1f}s{msg_extra}"
        )
        self.kpi_rows.update_values(f"{result.rows:,}")
        cols = result.df.width if not result.df.is_empty() else 0
        self.kpi_cols.update_values(f"{cols}")
        self.kpi_time.update_values(f"{result.elapsed_seconds:.1f} s")
        valor = 0.0
        for c in ["Valor Aprovado", "Valor Total", "VAL_TOTAL", "VAL_PAGO", "VAL_APRES", "vl_total_aprovado", "vl_total_apresentado", "VAL_TOT", "vlr_total"]:
            if c in result.df.columns:
                try:
                    valor = float(result.df[c].cast(pl.Float64, strict=False).sum() or 0.0)
                    if valor > 0:
                        break
                except Exception:
                    pass
        self.kpi_valor.update_values(f"R$ {valor:,.2f}")
        self._fill_preview_table(result.df)
        warns = list(result.warnings)
        if result.revoked_procedures_main:
            warns.append("Procedimentos revogados (principal): " +
                         ", ".join(result.revoked_procedures_main[:12]))
        if result.revoked_procedures_sec:
            warns.append("Procedimentos revogados (secundário): " +
                         ", ".join(result.revoked_procedures_sec[:12]))
        if warns:
            self.txt_warnings.setPlainText("\n\n".join(f"⚠ {w}" for w in warns))
            self.status_badge.set_status("Concluído com avisos", "WARN")
        else:
            self.txt_warnings.setPlainText("✅ Tudo certo! Nenhum aviso encontrado.")
        self.result_ready.emit(result)
        if getattr(self, "_auto_go_dashboard_after_run", False):
            self._auto_go_dashboard_after_run = False
            if hasattr(self, "btn_dashboard") and self.btn_dashboard is not None:
                self.btn_dashboard.click()

    def _on_failure(self, err: str) -> None:
        self.btn_run.setEnabled(True); self.btn_next.setEnabled(True); self.btn_prev.setEnabled(True)
        self.status_badge.set_status("Falha", "ERR")
        self.lbl_stage.setText("Erro durante processamento.")
        QMessageBox.critical(self, "Erro no pipeline", err)

    # -----------------------------------------------------------
    def _toggle_preview(self, checked: bool) -> None:
        self._preview_visible = bool(checked)
        if hasattr(self, "preview_group") and self.preview_group is not None:
            self.preview_group.setChecked(self._preview_visible)
        if hasattr(self, "_preview_content_widget") and self._preview_content_widget is not None:
            self._preview_content_widget.setVisible(self._preview_visible)

    def _show_preview_auto(self) -> None:
        """Quando o resultado chega: abre preview automaticamente."""
        self._toggle_preview(True)

    def _fill_preview_table(self, df: pl.DataFrame, max_rows: int = 50) -> None:
        if df.is_empty():
            self.table.setRowCount(0); self.table.setColumnCount(0); return
        preview = df.head(max_rows); cols = list(preview.columns)
        self.table.setColumnCount(len(cols)); self.table.setHorizontalHeaderLabels(cols)
        self.table.setRowCount(preview.height)
        for r in range(preview.height):
            for c, col in enumerate(cols):
                val = preview.item(r, col)
                txt = "" if val is None else str(val)
                item = QTableWidgetItem(txt)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()

    def _open_output_folder(self) -> None:
        out = self.settings.output_dir
        if out.exists():
            import os
            os.startfile(out)  # type: ignore[attr-defined]
        else:
            QMessageBox.information(self, "Info", "Diretório de saída ainda não criado.")

    def _export_excel(self) -> None:
        if not self._last_result or self._last_result.df.is_empty():
            QMessageBox.warning(self, "Aviso", "Sem dados para exportar.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Salvar como Excel", f"output_{self._last_result.system}.xlsx", "Excel (*.xlsx)"
        )
        if not path:
            return
        try:
            self._last_result.df.write_excel(path)
            QMessageBox.information(self, "Sucesso", f"Exportado para:\n{path}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Erro", str(e))


# ---------------------------------------------------------------------------
# Página 2: Análise LLM
# ---------------------------------------------------------------------------

class LLMPage(QWidget):
    """Página de análise com LLM local."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._df: Optional[pl.DataFrame] = None
        self._analyst = LLMAnalyst()
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("Assistente de Análise (LLM Local)")
        title.setProperty("class", "sectionTitle")
        subtitle = QLabel(
            "Phi-3 Mini ou TinyLlama (configurável). Analise seus dados do SUS sem enviar informações para a nuvem."
        )
        subtitle.setStyleSheet("color:#8FA6A3;")
        root.addWidget(title)
        root.addWidget(subtitle)

        splitter = QSplitter(Qt.Horizontal)

        # --- Esquerda: Dataset info + perguntas pré-definidas ---
        left_card = CardWidget()
        ll = left_card.layout_()

        status_header = QLabel("Status do Modelo")
        status_header.setProperty("class", "sectionTitle")
        ll.addWidget(status_header)
        status_row = QHBoxLayout()
        self.llm_status = StatusBadge(
            "Carregando..." if self._analyst.available else "Não configurado",
            "OK" if self._analyst.available else "WARN",
        )
        self.btn_reload_llm = QPushButton("🔄 Verificar LLM")
        self.btn_reload_llm.clicked.connect(self._check_llm)
        status_row.addWidget(self.llm_status, 1)
        status_row.addWidget(self.btn_reload_llm, 0)
        sw = QWidget(); sw.setLayout(status_row); ll.addWidget(sw)

        if not self._analyst.available:
            hint = QPlainTextEdit()
            hint.setReadOnly(True)
            hint.setPlainText(self._analyst.install_hint())
            hint.setFixedHeight(120)
            ll.addWidget(hint)

        info_header = QLabel("Dataset carregado")
        info_header.setProperty("class", "sectionTitle")
        ll.addWidget(info_header)
        self.lbl_data_info = QLabel("Nenhum dataset carregado.")
        self.lbl_data_info.setStyleSheet("color:#8FA6A3;")
        ll.addWidget(self.lbl_data_info)

        btn_load = QPushButton("📂 Carregar CSV/Parquet")
        btn_load.clicked.connect(self._load_file)
        ll.addWidget(btn_load)

        presets_header = QLabel("Perguntas pré-definidas")
        presets_header.setProperty("class", "sectionTitle")
        ll.addWidget(presets_header)

        self.preset_btns = []
        presets = [
            "📊 Resumo executivo geral",
            "🏥 Top 10 procedimentos por frequência",
            "💰 Análise de valores por financiamento",
            "📈 Tendência mensal de produção",
            "🗺️ Distribuição geográfica (municípios)",
            "⚠️ Análise de procedimentos revogados",
        ]
        for p in presets:
            b = QPushButton(p)
            b.clicked.connect(lambda _=False, txt=p: self._ask(txt))
            ll.addWidget(b)
            self.preset_btns.append(b)

        splitter.addWidget(left_card)

        # --- Direita: Chat + pergunta livre ---
        right_card = CardWidget()
        rl = right_card.layout_()

        q_header = QLabel("Pergunta à Assistente")
        q_header.setProperty("class", "sectionTitle")
        rl.addWidget(q_header)

        self.txt_question = QPlainTextEdit()
        self.txt_question.setPlaceholderText("Faça uma pergunta sobre os dados. Ex.: Qual procedimento gerou maior valor?")
        self.txt_question.setFixedHeight(100)
        rl.addWidget(self.txt_question)

        btn_row = QHBoxLayout()
        self.btn_ask = QPushButton("💡 Perguntar ao LLM")
        self.btn_ask.setObjectName("primaryButton")
        self.btn_ask.clicked.connect(lambda: self._ask(self.txt_question.toPlainText()))
        self.btn_stop = QPushButton("⏹ Parar")
        self.btn_stop.setObjectName("dangerButton")
        self.btn_stop.setEnabled(False)
        btn_row.addWidget(self.btn_ask)
        btn_row.addWidget(self.btn_stop)
        rl.addLayout(btn_row)

        ans_header = QLabel("Resposta")
        ans_header.setProperty("class", "sectionTitle")
        rl.addWidget(ans_header)
        self.viewer = StreamViewer()
        rl.addWidget(self.viewer, 1)

        self.llm_prog = AnimatedProgress()
        rl.addWidget(self.llm_prog)

        splitter.addWidget(right_card)
        splitter.setSizes([380, 700])

        root.addWidget(splitter, 1)
        self._check_llm()

    def _check_llm(self) -> None:
        if self._analyst.available:
            self.llm_status.set_status("LLM disponível", "OK")
        else:
            self.llm_status.set_status("Configurar LLM", "WARN")

    def set_dataset(self, df: pl.DataFrame) -> None:
        self._df = df
        self.lbl_data_info.setText(
            f"{df.height:,} linhas × {df.width} colunas"
            + ("" if df.is_empty() else f"\nPrimeira coluna: {df.columns[0]}")
        )

    def _load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Carregar dados", "", "Dados (*.parquet *.csv);;Todos (*)"
        )
        if not path:
            return
        try:
            p = Path(path)
            if p.suffix == ".parquet":
                df = pl.read_parquet(p)
            else:
                df = pl.read_csv(p, separator=";", infer_schema_length=10_000)
            self.set_dataset(df)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Erro", str(e))

    def _ask(self, question: str) -> None:
        if not self._analyst.available:
            QMessageBox.warning(
                self, "LLM indisponível",
                "Instale e configure o LLM local. Veja as instruções no painel esquerdo."
            )
            return
        if self._df is None or self._df.is_empty():
            QMessageBox.warning(self, "Sem dados", "Carregue ou processe um dataset primeiro.")
            return
        q = question.strip()
        if not q:
            return
        self.viewer.reset()
        self.llm_prog.set_value_smooth(5)
        self.btn_ask.setEnabled(False)
        self.btn_stop.setEnabled(True)

        # Executa em thread para não travar UI
        import threading
        def _worker() -> None:
            try:
                # Mapeamento dos presets
                text = q
                if "Resumo executivo geral" in q:
                    resp = self._analyst.summarize_dataset(self._df)
                    text_resp = resp.text if hasattr(resp, "text") else ""
                elif "Top 10 procedimentos" in q and "Procedimentos realizados" in self._df.columns and "Frequencia" in self._df.columns:
                    top = (
                        self._df.group_by("Procedimentos realizados")
                        .agg(pl.col("Frequencia").sum().alias("Frequencia"))
                        .sort("Frequencia", descending=True)
                        .head(10)
                    )
                    resp = self._analyst.answer_question(top, "Liste e comente os 10 procedimentos mais frequentes.")
                    text_resp = resp.text if hasattr(resp, "text") else ""
                elif "Tendência mensal" in q:
                    date_col = next((c for c in self._df.columns if "Mes/Ano" in c or "Ano/Mes" in c), None)
                    val_col = next((c for c in ["Frequencia"] if c in self._df.columns), None)
                    if not date_col or not val_col:
                        text_resp = "Colunas de data ou frequência não encontradas."
                    else:
                        resp = self._analyst.analyze_trend(self._df, date_col, val_col)
                        text_resp = resp.text if hasattr(resp, "text") else ""
                else:
                    resp = self._analyst.answer_question(self._df, text)
                    text_resp = resp.text if hasattr(resp, "text") else str(resp)

                # Simula streaming em chunks para UX
                import time
                chunk_size = 4
                for i in range(0, len(text_resp), chunk_size):
                    self.viewer.append_chunk(text_resp[i:i + chunk_size])
                    self.llm_prog.set_value_smooth(int(5 + 95 * (i / max(1, len(text_resp)))))
                    time.sleep(0.01)
                self.llm_prog.set_value_smooth(100)
            except Exception as e:  # noqa: BLE001
                self.viewer.append_chunk(f"\n\n[Erro: {e}]")
            finally:
                self.btn_ask.setEnabled(True)
                self.btn_stop.setEnabled(False)

        threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------------------------
# Página 3: Sobre / Configurações
# ---------------------------------------------------------------------------

class SettingsPage(QWidget):
    """Configurações e informações do app (conforme Manual §3.6)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings = get_settings()
        self._build_ui()
        self._load_from_settings()

    def _build_ui(self) -> None:
        from PySide6.QtWidgets import QFileDialog, QComboBox

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        t = QLabel("Configurações")
        t.setProperty("class", "sectionTitle")
        root.addWidget(t)

        cards = QHBoxLayout()
        cards.setSpacing(14)

        # ------------------------------------------------------------------
        # Card 1: Diretórios (4 botões Selecionar Pasta — Manual exige)
        # ------------------------------------------------------------------
        c1 = CardWidget(); cl = c1.layout_()
        th = QLabel("Diretórios"); th.setProperty("class", "sectionTitle"); cl.addWidget(th)
        info_dirs = QFormLayout()
        info_dirs.setHorizontalSpacing(10)
        info_dirs.setVerticalSpacing(10)

        def _mk_dir_row(label: str) -> tuple[QLineEdit, QPushButton]:
            row = QHBoxLayout(); row.setSpacing(6)
            le = QLineEdit(); le.setStyleSheet(
                "padding:7px 10px;border-radius:8px;background:#121C1E;"
                "color:#E6FFFA;border:1px solid #233539;")
            btn = QPushButton("📁 Selecionar Pasta")
            self._style_action_btn(btn, "#14B8A6")
            row.addWidget(le, 1); row.addWidget(btn, 0)
            wrap = QWidget(); wrap.setLayout(row)
            info_dirs.addRow(label + ":", wrap)
            return le, btn

        self.txt_data_dir, self.btn_data_dir = _mk_dir_row("Dados auxiliares")
        self.txt_cache_dir, self.btn_cache_dir = _mk_dir_row("Cache temporário")
        self.txt_output_dir, self.btn_output_dir = _mk_dir_row("Saída padrão")
        self.txt_log_dir, self.btn_log_dir = _mk_dir_row("Logs")

        def _pick_dir(widget: QLineEdit) -> None:
            cur = widget.text().strip() or str(Path.cwd())
            chosen = QFileDialog.getExistingDirectory(self, "Selecionar diretório", cur)
            if chosen:
                widget.setText(chosen)

        self.btn_data_dir.clicked.connect(lambda: _pick_dir(self.txt_data_dir))
        self.btn_cache_dir.clicked.connect(lambda: _pick_dir(self.txt_cache_dir))
        self.btn_output_dir.clicked.connect(lambda: _pick_dir(self.txt_output_dir))
        self.btn_log_dir.clicked.connect(lambda: _pick_dir(self.txt_log_dir))

        wrap_dirs = QWidget(); wrap_dirs.setLayout(info_dirs); cl.addWidget(wrap_dirs)

        row_extra = QHBoxLayout(); row_extra.setSpacing(8)
        btn_open_out = QPushButton("📂 Abrir diretório de saída")
        btn_open_out.clicked.connect(lambda: os.startfile(self.txt_output_dir.text() or str(self.settings.output_dir))
                                     if hasattr(os, "startfile") else None)
        btn_reset_defaults = QPushButton("↺ Restaurar padrões")
        self._style_action_btn(btn_reset_defaults, "#64748B")
        btn_reset_defaults.clicked.connect(self._restore_defaults)
        row_extra.addWidget(btn_open_out); row_extra.addStretch(1); row_extra.addWidget(btn_reset_defaults)
        cl.addLayout(row_extra)

        cards.addWidget(c1, 1)

        # ------------------------------------------------------------------
        # Card 2: LLM Local + LogLevel (combo — Manual exige)
        # ------------------------------------------------------------------
        c2 = CardWidget(); cl2 = c2.layout_()
        th2 = QLabel("LLM Local · Auditoria"); th2.setProperty("class", "sectionTitle"); cl2.addWidget(th2)
        form2 = QFormLayout()
        form2.setHorizontalSpacing(10)
        form2.setVerticalSpacing(10)

        # GGUF: FileDialog botão (Manual §3.6: caminho arquivo GGUF picker)
        row_gguf = QHBoxLayout(); row_gguf.setSpacing(6)
        self.txt_model_path = QLineEdit()
        self.txt_model_path.setPlaceholderText("Caminho para .gguf (ex.: Phi-3-mini-4k-instruct-q4.gguf)")
        self.txt_model_path.setStyleSheet(
            "padding:7px 10px;border-radius:8px;background:#121C1E;"
            "color:#E6FFFA;border:1px solid #233539;")
        self.btn_pick_gguf = QPushButton("🧠 Arquivo GGUF…")
        self._style_action_btn(self.btn_pick_gguf, "#6366F1")
        def _pick_gguf() -> None:
            cur = self.txt_model_path.text().strip()
            start_dir = str(Path(cur).parent) if cur and Path(cur).parent.exists() else str(Path.home())
            chosen, _ = QFileDialog.getOpenFileName(
                self, "Selecionar modelo GGUF", start_dir,
                "Modelos GGUF (*.gguf);;Todos os arquivos (*.*)")
            if chosen:
                self.txt_model_path.setText(chosen)
        self.btn_pick_gguf.clicked.connect(_pick_gguf)
        row_gguf.addWidget(self.txt_model_path, 1); row_gguf.addWidget(self.btn_pick_gguf, 0)
        wrap_gguf = QWidget(); wrap_gguf.setLayout(row_gguf)
        form2.addRow("Modelo (GGUF):", wrap_gguf)

        self.spin_ctx = QSpinBox(); self.spin_ctx.setRange(512, 32768); self.spin_ctx.setSingleStep(512)
        form2.addRow("Contexto (tokens):", self.spin_ctx)
        self.spin_threads = QSpinBox(); self.spin_threads.setRange(1, 16)
        form2.addRow("Threads CPU:", self.spin_threads)
        self.spin_gpu = QSpinBox(); self.spin_gpu.setRange(0, 99)
        form2.addRow("Camadas GPU:", self.spin_gpu)

        # Combo LogLevel (Manual §3.6)
        self.cbo_log_level = QComboBox()
        self.cbo_log_level.addItem("INFO  — Padrão, só mensagens importantes", "INFO")
        self.cbo_log_level.addItem("DEBUG — Tudo, para depuração",        "DEBUG")
        self.cbo_log_level.addItem("WARNING — Apenas avisos e erros",     "WARNING")
        self.cbo_log_level.addItem("ERROR — Críticos somente",            "ERROR")
        self.cbo_log_level.setStyleSheet(
            "QComboBox{padding:7px 10px;border-radius:8px;background:#121C1E;"
            "color:#E6FFFA;border:1px solid #233539;}"
            "QComboBox::drop-down{border:none;width:22px;}"
            "QComboBox QAbstractItemView{background:#0F1A1C;color:#E6FFFA;"
            "selection-background-color:#14B8A6;selection-color:#0F1A1C;}")
        form2.addRow("Nível de Log:", self.cbo_log_level)

        w2 = QWidget(); w2.setLayout(form2); cl2.addWidget(w2)
        btn_apply = QPushButton("💾 Salvar preferências")
        btn_apply.clicked.connect(self._save)
        cl2.addWidget(btn_apply)
        cards.addWidget(c2, 1)

        root.addLayout(cards)

        # Sobre
        about = CardWidget()
        al = about.layout_()
        at = QLabel("Sobre o GSPHosp 2.0"); at.setProperty("class", "sectionTitle"); al.addWidget(at)
        body = QLabel(
            "<p><b>GSPHosp</b> — Gestão em Saúde Populacional Hospitalar, versão Python 2.0.</p>"
            "<p>Sistema de análise de produção ambulatorial e hospitalar do SUS via dados DATASUS, "
            "com UI moderna (Qt6/Material Design 3), motor de processamento em Polars, integração "
            "opcional de LLM local (Phi-3/TinyLlama em llama.cpp) e melhores práticas de segurança "
            "(validação Pydantic, proteção contra path traversal, auditoria completa).</p>"
            f"<p style='color:#8FA6A3;'>Versão: {self.settings.app_version} | Cache: {self.settings.cache_dir}</p>"
        )
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        al.addWidget(body)
        root.addWidget(about, 1)

    # Helpers internos ------------------------------------------------
    def _style_action_btn(self, b: QPushButton, color: str) -> None:
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(
            f"QPushButton{{background:#162023;color:{color};border:1px solid #233539;"
            f"border-radius:10px;padding:7px 12px;font-weight:700;}}"
            f"QPushButton:hover{{background:#1B2B2F;border-color:{color};}}")

    def _load_from_settings(self) -> None:
        s = self.settings
        self.txt_data_dir.setText(str(s.data_dir))
        self.txt_cache_dir.setText(str(s.cache_dir))
        self.txt_output_dir.setText(str(s.output_dir))
        self.txt_log_dir.setText(str(s.log_dir))
        self.txt_model_path.setText(str(s.llm_model_path or ""))
        self.spin_ctx.setValue(s.llm_n_ctx)
        self.spin_threads.setValue(s.llm_n_threads)
        self.spin_gpu.setValue(s.llm_n_gpu_layers)
        # LogLevel combo: busca pelo código da sigla
        level = s.log_level
        for i in range(self.cbo_log_level.count()):
            if self.cbo_log_level.itemData(i) == level:
                self.cbo_log_level.setCurrentIndex(i); break

    def _restore_defaults(self) -> None:
        from pathlib import Path as _P
        import tempfile as _tf
        self.txt_data_dir.setText(str(_P.home() / ".gsphosp" / "data"))
        self.txt_cache_dir.setText(str(_P(_tf.gettempdir()) / "gsphosp"))
        self.txt_output_dir.setText(str(_P.cwd() / "output"))
        self.txt_log_dir.setText(str(_P.home() / ".gsphosp" / "logs"))
        self.cbo_log_level.setCurrentIndex(0)  # INFO

    def _save(self) -> None:
        """Salva preferências: (a) JSON via Settings.save_json() e (b) .env LLM para compatibilidade."""
        from pathlib import Path
        s = self.settings
        try:
            # Atualiza o objeto Settings a partir da UI
            s.data_dir = Path(self.txt_data_dir.text().strip() or s.data_dir)
            s.cache_dir = Path(self.txt_cache_dir.text().strip() or s.cache_dir)
            s.output_dir = Path(self.txt_output_dir.text().strip() or s.output_dir)
            s.log_dir = Path(self.txt_log_dir.text().strip() or s.log_dir)
            mp = self.txt_model_path.text().strip()
            s.llm_model_path = Path(mp) if mp else None
            s.llm_n_ctx = int(self.spin_ctx.value())
            s.llm_n_threads = int(self.spin_threads.value())
            s.llm_n_gpu_layers = int(self.spin_gpu.value())
            try:
                s.log_level = str(self.cbo_log_level.currentData() or "INFO")
            except Exception:
                s.log_level = "INFO"
            s.ensure_dirs()
            # Persiste JSON (conforme Manual §3.6: save_json())
            json_path = s.save_json()
        except Exception as exc_json:  # noqa: BLE001
            QMessageBox.warning(self, "Aviso ao salvar JSON",
                                f"Não foi possível salvar settings.json:\n{exc_json}\n"
                                f"Tentando salvar no .env mesmo assim…")

        # Backward-compat: também salva LLM em .env como antes
        env_path = Path.cwd() / ".env"
        lines = []
        try:
            if env_path.exists():
                lines = env_path.read_text(encoding="utf-8").splitlines()
        except Exception:  # noqa: PERF203
            pass
        updates = {
            "PAPAHHR_LLM_MODEL_PATH": self.txt_model_path.text().strip(),
            "PAPAHHR_LLM_N_CTX": str(self.spin_ctx.value()),
            "PAPAHHR_LLM_N_THREADS": str(self.spin_threads.value()),
            "PAPAHHR_LLM_N_GPU_LAYERS": str(self.spin_gpu.value()),
            "GSPHOSP_LOG_LEVEL": str(self.cbo_log_level.currentData() or "INFO"),
        }
        new_lines = []
        seen = set()
        for ln in lines:
            if "=" in ln and not ln.strip().startswith("#"):
                k = ln.split("=", 1)[0].strip().upper()
                if k in updates:
                    new_lines.append(f"{k}={updates[k]}")
                    seen.add(k)
                    continue
            new_lines.append(ln)
        for k, v in updates.items():
            if k not in seen:
                new_lines.append(f"{k}={v}")
        try:
            from papahr.ui.pages.dashboard_page import DashboardPage
            from papahr.ui.pages.pipeline_page import PipelinePage
            from papahr.ui.pages.settings_page import SettingsPage
            from papahr.ui.pages.llm_page import LlmPage

            __all__ = ["DashboardPage", "PipelinePage", "SettingsPage", "LlmPage"]
            env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Aviso ao salvar .env", str(e))

        # Reseta o cache do get_settings() para recarregar com valores novos
        try:
            from papahr.config import get_settings as _gs
            _gs.cache_clear()  # type: ignore[attr-defined]
        except Exception:
            pass

        try:
            QMessageBox.information(self, "Sucesso",
                                    f"Preferências salvas em:\n"
                                    f"• {json_path}\n"
                                    f"• {env_path}\n\n"
                                    f"Feche e reabra o app para aplicar novo LogLevel e caminhos.")
        except Exception:  # noqa: BLE001
            QMessageBox.information(self, "Sucesso",
                                    f"Preferências salvas em:\n{env_path}\n\n"
                                    "Feche e reabra o app para aplicar novo LogLevel e caminhos.")


# ---------------------------------------------------------------------------
# Dashboard (Saúde Populacional) — import tardiamente para evitar
# ciclos / peso no startup.
# ---------------------------------------------------------------------------
from papahr.ui.pages.dashboard_page import DashboardPage  # noqa: E402

__all__ = [
    "PipelinePage",
    "PipelineWorker",
    "LLMPage",
    "SettingsPage",
    "DashboardPage",
    "ChipButton",
    "FlowLayout",
]