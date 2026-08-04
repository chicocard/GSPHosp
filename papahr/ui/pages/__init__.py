from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import partial
from pathlib import Path
from typing import Optional

import polars as pl
from PySide6.QtCore import QDate, QLocale, QObject, QPoint, QRect, QSize, Qt, QThread, Signal
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
from papahr.geo.reference import hospital_bate_geo
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
            QPushButton:checked{background:linear-gradient(90deg,#4FD1C5,#14B8A6);
                                color:#0F1A1C;border-color:#4FD1C5;font-weight:600;}
        """)

    def value(self) -> str:
        return self._value


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

_STEP_COLOR = ("#14B8A6", "#6366F1", "#F59E0B", "#8B5CF6")
_STEP_LABEL = (
    "1️⃣ Sistema & Fonte",
    "2️⃣ Localização SUS",
    "3️⃣ Período",
    "4️⃣ Hospitais (CNES)",
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
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # --- LADO ESQUERDO: Wizard 4 passos ---
        wizard_card = CardWidget()
        wizard_card.setMinimumWidth(640)       # ← mínimo confortável (antes 560, muito pequeno)
        wizard_card.setMinimumHeight(620)      # ← altura mínima para evitar sobreposição
        fl = wizard_card.layout_()

        title = QLabel("🧭 Assistente de Extração de Dados do SUS")
        title.setStyleSheet("font-size:17px;font-weight:700;color:#4FD1C5;")
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
            lbl.setMinimumHeight(34)             # ← altura mínima para não encostar
            vbox.addWidget(dot, 0, Qt.AlignCenter)
            vbox.addWidget(lbl)
            self.step_dots.append(dot); self.step_labels.append(lbl)
            self.step_bar.addLayout(vbox, 1)
            if i < 3:
                sep = QFrame(); sep.setFrameShape(QFrame.HLine); sep.setStyleSheet(f"color:{_STEP_COLOR[i]};");
                sep.setMaximumHeight(3); sep.setMinimumWidth(26)   # ← mais largo (era 18)
                self.step_bar.addWidget(sep, 0)
        fl.addLayout(self.step_bar)
        fl.addSpacing(4)

        # Passos (QStackedWidget) — MINIMO e ESPAÇAMENTO para não sobrepor
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_step1())
        self.stack.addWidget(self._build_step2())
        self.stack.addWidget(self._build_step3())
        self.stack.addWidget(self._build_step4())
        self.stack.setMinimumHeight(430)        # ← altura mínima (Stack = maior conteúdo)
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
        self.btn_run = QPushButton("🚀 Executar Extração")
        self.btn_run.setStyleSheet(
            "padding:12px 22px;border-radius:12px;font-weight:700;font-size:13px;"
            "background:linear-gradient(90deg,#4FD1C5,#14B8A6);color:#0F1A1C;border:none;")
        self.btn_run.setMinimumHeight(44)
        self.btn_run.clicked.connect(self._on_run)
        nav.addWidget(self.btn_prev); nav.addStretch(1); nav.addWidget(self.btn_next); nav.addWidget(self.btn_run)
        fl.addLayout(nav)

        # Progresso / status
        self.progress = AnimatedProgress()
        self.status_badge = StatusBadge("Passo 1 de 4", "INFO")
        self.lbl_stage = QLabel("Selecione o sistema de informação e a fonte dos dados.")
        self.lbl_stage.setStyleSheet("color:#94A3B8;")
        self.lbl_stage.setWordWrap(True)
        fl.addWidget(self.status_badge); fl.addWidget(self.progress); fl.addWidget(self.lbl_stage)
        # wizard_card já contém todo o conteúdo do lado esquerdo (via layout `fl` interno).

        # --- LADO DIREITO: KPIs + Preview (recolhível) + Avisos ---
        # Botão toggle (colapsa/expande preview)
        self.btn_toggle_preview = QPushButton("📋 Mostrar prévia de dados ▼")
        self.btn_toggle_preview.setCheckable(True); self.btn_toggle_preview.setChecked(False)
        self.btn_toggle_preview.setStyleSheet(
            "QPushButton{padding:7px 12px;border-radius:8px;"
            "background:#1B2B2F;color:#4FD1C5;font-weight:700;border:1px solid #233539;}"
            "QPushButton:checked{color:#64748B;}"
        )
        self.btn_toggle_preview.setMinimumHeight(36)
        self._preview_visible = False  # por padrão recolhido (NÃO TAMPA O WIZARD)

        right_wrap = QWidget()
        right = QVBoxLayout(right_wrap); right.setContentsMargins(6, 2, 6, 2); right.setSpacing(10)
        right_wrap.setMinimumWidth(420)        # ← mínimo confortável painel direito
        right_wrap.setMaximumWidth(680)        # ← máximo (antes 640, pouco flex)

        self.btn_toggle_preview.clicked.connect(lambda ch: self._toggle_preview(ch))
        right.addWidget(self.btn_toggle_preview)

        kpi_row = QHBoxLayout(); kpi_row.setSpacing(8)
        self.kpi_rows  = KpiCard("Linhas", 0, "Total processado")
        self.kpi_cols  = KpiCard("Colunas", 0, "Largura do dataset", accent="#3B82F6")
        self.kpi_time  = KpiCard("Tempo", "0 s", "Tempo total", accent="#8B5CF6")
        self.kpi_valor = KpiCard("Valor", "R$ 0", "Soma de valores", accent="#F59E0B")
        for k in (self.kpi_rows, self.kpi_cols, self.kpi_time, self.kpi_valor):
            k.setMinimumHeight(88)
            k.setMaximumHeight(104)
            kpi_row.addWidget(k)
        right.addLayout(kpi_row)

        preview_card = CardWidget()
        self._preview_card = preview_card
        pl_layout = preview_card.layout_()
        prev_header = QLabel("Prévia dos dados (50 primeiras linhas)")
        prev_header.setStyleSheet("font-size:13px;font-weight:700;color:#4FD1C5;")
        prev_header.setWordWrap(True)
        pl_layout.addWidget(prev_header)
        self.table = QTableWidget(0, 0)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(200)
        self.table.setMaximumHeight(320)
        self.table.setStyleSheet(
            "QTableWidget{background:#121C1E;alternate-background-color:#172426;"
            "color:#E6FFFA;gridline-color:#233539;border-radius:10px;}"
            "QHeaderView::section{background:#1B2B2F;color:#4FD1C5;padding:8px;"
            "border:none;border-bottom:2px solid #14B8A6;font-weight:600;}")
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        pl_layout.addWidget(self.table, 1)
        preview_card.setVisible(self._preview_visible)  # ← por padrão INVISÍVEL até o usuário expandir
        right.addWidget(preview_card, 1)

        warn_card = CardWidget()
        wl = warn_card.layout_()
        wh = QLabel("⚠ Avisos / Observações"); wh.setStyleSheet("font-weight:700;color:#F59E0B;")
        wl.addWidget(wh)
        self.txt_warnings = QPlainTextEdit(); self.txt_warnings.setReadOnly(True)
        self.txt_warnings.setPlaceholderText("Nenhum aviso ainda.")
        self.txt_warnings.setMinimumHeight(70)
        self.txt_warnings.setMaximumHeight(110)
        wl.addWidget(self.txt_warnings)

        btns = QHBoxLayout(); btns.setSpacing(6)
        self.btn_open_folder = QPushButton("📂 Abrir saída"); self.btn_open_folder.clicked.connect(self._open_output_folder)
        self.btn_open_folder.setMinimumHeight(36)
        self.btn_export_excel = QPushButton("📊 Salvar Excel (.xlsx)"); self.btn_export_excel.clicked.connect(self._export_excel)
        self.btn_export_excel.setMinimumHeight(36)
        self.btn_dashboard = QPushButton("📈 Ir para Dashboard")
        self.btn_dashboard.setStyleSheet("background:#14B8A6;color:#0F1A1C;font-weight:700;border-radius:10px;padding:8px 14px;border:none;")
        self.btn_dashboard.setMinimumHeight(36)
        self.btn_dashboard.setEnabled(False)
        btns.addWidget(self.btn_open_folder); btns.addWidget(self.btn_export_excel); btns.addStretch(); btns.addWidget(self.btn_dashboard)
        wl.addLayout(btns)
        right.addWidget(warn_card, 0)

        # Splitter entre Wizard (esquerda, principal → ~70%) e Painel KPIs (direita → ~30%)
        spl = QSplitter(Qt.Horizontal)
        spl.setChildrenCollapsible(False)    # ← EVITA SOBREPOSIÇÃO: filhos NÃO colapsam via handle
        spl.setObjectName("wizardMainSplitter")  # ← QSS customizado: TRANSPARENTE no repouso!
        spl.addWidget(wizard_card); spl.addWidget(right_wrap)
        spl.setStretchFactor(0, 3); spl.setStretchFactor(1, 2)
        spl.setSizes([1200, 560])            # ← tamanhos iniciais reais
        spl.setCollapsible(0, False)         # Wizard nunca colapsa
        spl.setCollapsible(1, True)          # Painel direito PODE ser recolhido
        spl.setHandleWidth(5)
        root.addWidget(spl, 1)
        self._main_splitter = spl
        self._set_step(0)

    # -----------------------------------------------------------
    def _dot_style(self, idx: int, active: bool, done: bool) -> str:
        c = _STEP_COLOR[idx]
        if done:
            return f"border-radius:14px;background:#1B2B2F;color:#E6FFFA;border:2px solid {c};"
        if active:
            return (f"border-radius:14px;background:{c};color:#0F1A1C;border:2px solid {c};"
                    "font-weight:800;font-size:13px;")
        return f"border-radius:14px;background:#162023;color:#64748B;border:1px dashed #2D4A4A;"

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
        self.btn_run.setVisible(n == 3)
        # Stage label
        hints = [
            "Selecione o sistema e a fonte dos dados.",
            "Escolha o período de competência.",
            "Clique em chips para selecionar UF e/ou municípios.",
            "Selecione (se quiser) hospitais específicos e execute.",
        ]
        self.lbl_stage.setText(f"Passo {n+1} de 4 — {hints[n]}")
        self.status_badge.set_status(f"Passo {n+1}/4", "OK" if n == 3 else "INFO")
        if n == 3:
            try:
                from PySide6.QtCore import QTimer
                QTimer.singleShot(40, self._reload_hospitals_from_cnes)
            except Exception:
                try:
                    self._reload_hospitals_from_cnes()
                except Exception:
                    pass

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
        # Chips de sistema
        self.chips_sys = FlowLayout(spacing=8)
        self.group_sys = QButtonGroup(self); self.group_sys.setExclusive(True)
        for lab, val in [("🏥 SIA-PA (Ambulatorial)", "SIA-PA"),
                         ("🛏️ SIH-RD (AIHs Aprovadas)", "SIH-RD"),
                         ("❗ SIH-RJ (AIHs Rejeitadas)", "SIH-RJ"),
                         ("👨‍⚕️ SIH-SP (Serviços Profissionais)", "SIH-SP")]:
            c = ChipButton(lab, val); self.chips_sys.addWidget(c); self.group_sys.addButton(c)
            if val == "SIA-PA":
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
        return "SIA-PA"

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
        ufs_sel = self._selected_ufs_nonempty()
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

    def _refresh_drs(self) -> None:
        ufs_sel = self._selected_ufs_nonempty()
        macros_sel = self._selected_macros_nonempty()
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
        return self._selected_ufs_nonempty() or list(get_ufs())

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
        b.setStyleSheet(
            f"QPushButton{{background:#162023;color:{color};border:1px solid #233539;"
            f"border-radius:10px;padding:6px 12px;font-weight:700;}}"
            f"QPushButton:hover{{background:#1B2B2F;border-color:{color};}}")

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

        # ---------------------------------------------------------------- 1. PRESET
        preset_page = QWidget(); pl = QVBoxLayout(preset_page); pl.setContentsMargins(4, 8, 4, 4); pl.setSpacing(10)
        hl = QLabel("⏰ Selecione um preset:"); hl.setStyleSheet("color:#4FD1C5;font-weight:700;")
        pl.addWidget(hl)
        presets_list = [
            ("⏪ Último mês",                           ("preset_last_1m",   1)),
            ("⏪ Últimos 3 meses",                      ("preset_last_3m",   3)),
            ("⏪ Últimos 6 meses",                      ("preset_last_6m",   6)),
            ("⏪ Último ano (12m)",                     ("preset_last_12m",  12)),
            ("📅 Ano ATUAL inteiro (Jan→hoje)",        ("preset_year_cur", -1)),
            ("📅 Ano PASSADO inteiro (Jan→Dez 2025)",  ("preset_year_prev", -2)),
        ]
        flow = FlowLayout(spacing=8)
        for lab, (pk, n) in presets_list:
            b = ChipButton(lab, pk)
            if pk.startswith("preset_last_"):
                b.clicked.connect(partial(self._apply_period_preset, months_back=n, _update_mode=True))
            elif pk == "preset_year_cur":
                b.clicked.connect(partial(self._apply_period_year, offset=0, _update_mode=True))
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
            "padding:8px 10px;border-radius:8px;background:#162023;color:#E6FFFA;border:1px solid #233539;")
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
        form.addRow("🗓️ Início:", self.dt_start)
        form.addRow("🗓️ Fim:   ", self.dt_end)
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
        w = QWidget(); vl = QVBoxLayout(w); vl.setContentsMargins(10, 6, 10, 6); vl.setSpacing(10)

        hl = QLabel("Opcional: selecione hospitais específicos (CNES):")
        hl.setStyleSheet("font-weight:700;color:#E6FFFA;")
        vl.addWidget(hl)

        hint = QLabel("💡 Se nenhum hospital for selecionado, todos os disponíveis serão usados.")
        hint.setStyleSheet("color:#64748B;")
        hint.setWordWrap(True)
        vl.addWidget(hint)

        # Status de disponibilidade CNES
        self.lbl_hosp_status = StatusBadge("Carregando lista de hospitais...", status="info")
        vl.addWidget(self.lbl_hosp_status)

        # Busca + listagem de hospitais
        top_row = QHBoxLayout()
        self.search_hosp = QLineEdit(); self.search_hosp.setPlaceholderText("🔎 Buscar hospital por nome ou CNES...")
        self.search_hosp.setStyleSheet(
            "padding:8px 10px;border-radius:10px;background:#162023;color:#E6FFFA;border:1px solid #233539;")
        self.search_hosp.setClearButtonEnabled(True)
        self.search_hosp.textChanged.connect(self._filter_hosp_list)
        btn_load_from_cnes = QPushButton("🔄 Carregar hospitais do CNES")
        self._style_action_btn(btn_load_from_cnes, "#4FD1C5")
        btn_load_from_cnes.clicked.connect(self._reload_hospitals_from_cnes)
        btn_refresh_geo = QPushButton("📍 Aplicar filtros geográficos")
        self._style_action_btn(btn_refresh_geo, "#8B5CF6")
        btn_refresh_geo.clicked.connect(self._reload_hospitals_from_cnes)
        top_row.addWidget(self.search_hosp, 1); top_row.addWidget(btn_refresh_geo, 0); top_row.addWidget(btn_load_from_cnes, 0)
        vl.addLayout(top_row)

        # 2a LINHA: Campo PERMANENTE p/ usuario digitar CNES diretamente (sem abrir dialog)
        cnes_row = QHBoxLayout()
        self.ed_add_cnes_manual = QLineEdit()
        self.ed_add_cnes_manual.setObjectName("ManualCNESField")
        self.ed_add_cnes_manual.setPlaceholderText(
            "🔢 Digite CNES (ex: 2561492) ou varios separados por virgula: 2561492,2481286  (ENTER = adicionar)"
        )
        self.ed_add_cnes_manual.setMinimumHeight(40)
        self.ed_add_cnes_manual.setStyleSheet(
            "QLineEdit#ManualCNESField { padding:8px 10px;border-radius:10px;background:#1A1F2B;"
            "color:#E6FFFA;border:1px dashed #F59E0B; }"
            "QLineEdit#ManualCNESField:focus { border:2px solid #F59E0B; background:#232A3D; }"
        )
        self.ed_add_cnes_manual.setClearButtonEnabled(True)
        self.ed_add_cnes_manual.returnPressed.connect(self._on_add_cnes_from_field)
        btn_manual_add = QPushButton("➕ Adicionar CNES")
        self._style_action_btn(btn_manual_add, "#F59E0B")
        btn_manual_add.setToolTip(
            "Adiciona os códigos CNES digitados (verifica se pertencem à UF/Macro selecionada; "
            "rejeita inválidos com explicação)."
        )
        btn_manual_add.clicked.connect(self._on_add_cnes_from_field)
        cnes_row.addWidget(self.ed_add_cnes_manual, 1)
        cnes_row.addWidget(btn_manual_add, 0)
        vl.addLayout(cnes_row)

        spl = QSplitter(Qt.Horizontal)
        spl.setChildrenCollapsible(False)
        spl.setHandleWidth(4)
        # Lista
        self.list_hosp = QListWidget(); self.list_hosp.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_hosp.setStyleSheet(
            "QListWidget{background:#162023;color:#E6FFFA;border:1px solid #233539;border-radius:10px;}"
            "QListWidget::item{padding:6px 8px;border-bottom:1px solid #172426;}"
            "QListWidget::item:hover{background:#1B2B2F;color:#4FD1C5;}"
            "QListWidget::item:selected{background:linear-gradient(90deg,#4FD1C5,#14B8A6);color:#0F1A1C;font-weight:700;border-radius:6px;}")
        self.list_hosp.itemDoubleClicked.connect(lambda _it: self._add_selected_hospitals())
        spl.addWidget(self.list_hosp)
        # Selecionados (chips)
        right = QFrame(); right.setStyleSheet("QFrame{background:#121C1E;border-radius:10px;border:1px solid #233539;}")
        rl = QVBoxLayout(right); rl.setContentsMargins(8, 8, 8, 8)
        rl.addWidget(QLabel("<b>✅ Hospitais selecionados:</b>"))
        self.hosp_flow = FlowLayout(spacing=4)
        hfw = QWidget(); hfw.setLayout(self.hosp_flow)
        scr = QScrollArea(); scr.setWidgetResizable(True); scr.setFrameShape(QFrame.NoFrame); scr.setWidget(hfw)
        rl.addWidget(scr, 1)
        bts = QHBoxLayout()
        ba = QPushButton("➕ Adicionar selecionados"); self._style_action_btn(ba, "#6366F1")
        ba.clicked.connect(self._add_selected_hospitals)
        bc = QPushButton("🗑 Limpar"); self._style_action_btn(bc, "#64748B")
        bc.clicked.connect(self._clear_hosp_chips)
        bl = QPushButton("📋 Colar CNES (separado por vírgula)"); self._style_action_btn(bl, "#F59E0B")
        bl.clicked.connect(self._paste_cnes_dialog)
        bts.addWidget(ba); bts.addWidget(bc); bts.addWidget(bl); bts.addStretch()
        rl.addLayout(bts)
        spl.addWidget(right); spl.setStretchFactor(0, 3); spl.setStretchFactor(1, 2)
        vl.addWidget(spl, 1)

        # Carga inicial (NÃO BLOQUEIA: usa eventos de carregamento assíncrono padrão)
        try:
            QTimer.singleShot(50, self._reload_hospitals_from_cnes)
        except Exception:
            self._reload_hospitals_from_cnes()
        return w

    def _reload_hospitals_from_cnes(self) -> None:
        self._hospitals_data: list[tuple[str, str, str, str]] = []
        sel_ufs: set[str] = set(self._selected_ufs_nonempty() or [])
        sel_macros: set[str] = set(self._selected_macros_nonempty() or [])
        sel_drs: set[str] = set(d for d, ch in self.chips_drs.items() if ch.isChecked())
        errors: list[str] = []
        warnings: list[str] = []
        using_source: str = "CNES local"
        try:
            # -------------------------------------------------------------- Heurística 1:
            # Se já temos dados persistidos no DuckDB → usar silver.dim_hospitais
            # (contém no_macrorregiao / no_regiao_saude para FILTRAR cascateado!)
            db = get_db()
            used_dim_hospitais = False
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
                            if not c:
                                continue
                            n = str(row.get("nome") or c).strip()
                            u = str(row.get("uf") or "").strip()
                            t = str(row.get("tipo") or "").strip()
                            self._hospitals_data.append((c, n, u, t))
                            added += 1
                        used_dim_hospitais = True
                        using_source = f"DuckDB silver.dim_hospitais ({added:,} registros)"
                        if sel_macros or sel_drs:
                            macro_ok = bool(sel_macros)
                            drs_ok = bool(sel_drs)
                            warnings.append(
                                f"✅ Filtro por {'Macro ' if macro_ok else ''}"
                                f"{' + DRS' if (macro_ok and drs_ok) else ('DRS' if drs_ok and not macro_ok else '')}"
                                f" aplicado via DuckDB."
                            )
            except Exception as exc_dim:  # pragma: no cover - erro db
                errors.append(f"(DuckDB dim_hospitais indisponível: {exc_dim})")

            # ---------------------------------------------------------- Heurística 2:
            # CNES local (fallback). Aqui só temos UF garantido.
            if not used_dim_hospitais:
                from papahr.dashboard.cnes_loader import get_cnes_data
                cnes = get_cnes_data()
                cnes.load()
                edf = cnes.estabelecimentos
                if edf is None or edf.is_empty():
                    errors.append("Tabela `tbEstabelecimento*` vazia.")
                else:
                    # Nome: Nome Fantasia OU Razão Social
                    nome = None
                    for col in ("NO_FANTASIA", "NO_RAZAO_SOCIAL", "NO_FANTAS", "NO_RAZAO",
                                "NOME_FANTASIA", "RAZAO_SOCIAL", "NOME"):
                        if col in edf.columns:
                            nome = edf[col].cast(pl.Utf8).fill_null(pl.lit("")).to_list()
                            break
                    # Código CNES
                    cod = None
                    for col in ("CO_CNES", "CNES", "COD_CNES"):
                        if col in edf.columns:
                            cod = edf[col].cast(pl.Utf8).fill_null(pl.lit("")).str.strip_chars().to_list()
                            break
                    # UF
                    uf: Optional[list[str]] = None
                    for col in ("SIGLA_ESTADO_GESTOR", "SIGLA_UF_GESTOR", "SIGLA_UF",
                                "UF", "SG_UF", "SG_UF_GESTOR", "SG_ESTADO",
                                "CO_ESTADO_GESTOR", "CO_UF_GESTOR", "CO_UF"):
                        if col in edf.columns:
                            vals = edf[col].cast(pl.Utf8).fill_null(pl.lit("")).to_list()
                            if col.startswith("CO_"):
                                mapping = _UF_CODIBGE_TO_SIGLA
                                vals = [mapping.get(str(v).zfill(2), v) for v in vals]
                            uf = vals
                            break
                    # Tipo estabelecimento
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
                        sel_ufs_l = list(sel_ufs)
                        sel_macros_l = list(sel_macros)
                        sel_drs_l = list(sel_drs)
                        for i in range(N):
                            c = (cod[i] or "").strip()
                            if not c:
                                continue
                            n = (nome[i] or c).strip()
                            u = (uf[i] or "").strip()
                            t = (tipo[i] or "").strip()
                            mun_h = (cod_mun_gestor[i] or "").strip()
                            rs_h = (cod_reg_saude_cnes[i] or "").strip()
                            if not hospital_bate_geo(
                                uf_hospital=u,
                                co_municipio_hospital=mun_h or None,
                                co_regiao_saude_hospital=rs_h or None,
                                sel_ufs=sel_ufs_l,
                                sel_macros=sel_macros_l,
                                sel_drs=sel_drs_l,
                            ):
                                continue
                            self._hospitals_data.append((c, n, u, t))
                            added += 1
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
        except Exception as exc:  # pragma: no cover
            errors.append(f"Erro ao carregar CNES: {exc}")

        # Feedback UI
        if not self._hospitals_data:
            base = ("Não há hospitais do CNES carregados. "
                    "Na tela inicial, use fonte 'Pasta compactada' com arquivos do CNES "
                    "(tbEstabelecimento*.csv + tbMunicipio*.csv) — ou rode o pipeline primeiro.")
            msg = base + ("\n\n📌 Detalhes: " + " ; ".join(errors) if errors else "")
            if warnings:
                msg += "\n\n" + " ; ".join(warnings)
            self.lbl_hosp_status.set_status(msg, "WARN")
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
            self.lbl_hosp_status.set_status(body, "OK")
        self._filter_hosp_list(self.search_hosp.text() if hasattr(self, "search_hosp") else "")

    def _filter_hosp_list(self, text: str = "") -> None:
        if not hasattr(self, "list_hosp") or self.list_hosp is None:
            return
        self.list_hosp.clear()
        ft = (text or "").lower().strip()
        tokens = ft.split() if ft else []
        n = 0
        total_avail = len(getattr(self, "_hospitals_data", []) or [])
        for cnes, nome, uf, tipo in (self._hospitals_data if hasattr(self, "_hospitals_data") else []):
            haystack = f"{nome} {cnes} {uf} {tipo}".lower()
            if tokens and not all(t in haystack for t in tokens):
                continue
            label = f"🏥 [{uf or '—'}] {nome or '(sem nome)'}"
            if tipo:
                label += f"  ·  {tipo}"
            label += f"  ({cnes})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, (cnes, nome, uf, tipo))
            item.setToolTip(f"CNES: {cnes}\nNome: {nome}\nUF: {uf or '—'}\nTipo: {tipo or '—'}")
            self.list_hosp.addItem(item)
            n += 1
            if n > 5000:
                break
        # Feedback: quantos aparecem vs quantos existem
        if hasattr(self, "search_hosp") and text:
            self.search_hosp.setPlaceholderText(
                f"🔎 {n:,} resultado(s) / {total_avail:,} total  (digite para filtrar mais...)"
            )
        elif hasattr(self, "search_hosp"):
            self.search_hosp.setPlaceholderText("🔎 Buscar hospital por nome ou CNES...")

    def _existing_hospital_chip_values(self) -> set[str]:
        """Códigos CNES que já estão como chip selecionado (evita duplicatas)."""
        seen: set[str] = set()
        for i in range(self.hosp_flow.count()):
            it = self.hosp_flow.itemAt(i)
            if it and it.widget() and isinstance(it.widget(), ChipButton):
                seen.add(str(it.widget().value()).strip())
        return seen

    def _add_cnes_codes_from_text(self, text: str) -> tuple[int, list[str], int, list[str]]:
        """Adiciona hospitais a partir de um texto com códigos CNES separados por vírgula/espaco/linha.

        Validações (ordem):
          1) Parse códigos (regex \\d+ para extrair só numeros)
          2) Existe na base _hospitals_data (que JÁ foi filtrada por UF/Macro/DRS)?
             - Se NÃO existir na base → invalido (hospital não bate area geografica OU nao existe)
          3) Já está como chip? → skip (sem duplicata)
          4) Adiciona chip verde com NOME DO HOSPITAL (igual _add_selected_hospitals)

        Retorna: (added:int, invalid_codes:list[str], skipped:int, added_with_names:list[str])
          added_with_names = lista formatada para feedback humano.
        """
        import re

        raw_codes = [c for c in re.findall(r"\d+", text or "") if c]
        if not raw_codes:
            return 0, [], 0, []

        lookup: dict[str, tuple[str, str, str]] = {}
        for cnes, nome, uf, tipo in getattr(self, "_hospitals_data", []) or []:
            lookup[str(cnes).strip()] = (nome, uf, tipo)

        already_chips = self._existing_hospital_chip_values()
        added = 0
        skipped = 0
        invalid: list[str] = []
        added_labels: list[str] = []

        for raw in raw_codes:
            c = str(raw).strip()
            if not c:
                continue
            if c in already_chips:
                skipped += 1
                continue
            info = lookup.get(c)
            if info is None:
                invalid.append(c)
                continue
            nome, uf, tipo = info
            already_chips.add(c)
            name_trunc = (nome[:28] + "…") if len(nome or "") > 28 else (nome or "")
            lab = f"[{uf or '—'}] {name_trunc} ({c[-4:]})"
            ch = ChipButton(lab, c); ch.setChecked(True)
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
        for item in self.list_hosp.selectedItems():
            cnes, nome, uf, _tipo = item.data(Qt.UserRole)
            already = set()
            for i in range(self.hosp_flow.count()):
                it = self.hosp_flow.itemAt(i)
                if it and it.widget() and isinstance(it.widget(), ChipButton):
                    already.add(it.widget().value())
            if cnes in already:
                continue
            lab = f"[{uf}] {nome[:28]}{'…' if len(nome) > 28 else ''} ({cnes[-4:]})"
            ch = ChipButton(lab, cnes); ch.setChecked(True)
            self.hosp_flow.addWidget(ch)
        self.list_hosp.clearSelection()

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
        out: list[str] = []
        for i in range(self.hosp_flow.count()):
            it = self.hosp_flow.itemAt(i)
            if it and it.widget() and isinstance(it.widget(), ChipButton):
                out.append(it.widget().value())
        return out or None

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
        for c in ["Valor Aprovado", "Valor Total", "VAL_TOTAL"]:
            if c in result.df.columns and result.df[c].dtype.is_numeric():
                valor = float(result.df[c].sum() or 0.0)
                break
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

    def _on_failure(self, err: str) -> None:
        self.btn_run.setEnabled(True); self.btn_next.setEnabled(True); self.btn_prev.setEnabled(True)
        self.status_badge.set_status("Falha", "ERR")
        self.lbl_stage.setText("Erro durante processamento.")
        QMessageBox.critical(self, "Erro no pipeline", err)

    # -----------------------------------------------------------
    def _toggle_preview(self, checked: bool) -> None:
        self._preview_visible = bool(checked)
        self._preview_card.setVisible(self._preview_visible)
        self.btn_toggle_preview.setText(
            "📋 Ocultar prévia de dados ▲" if self._preview_visible
            else "📋 Mostrar prévia de dados ▼"
        )

    def _show_preview_auto(self) -> None:
        """Quando o resultado chega: abre preview automaticamente (uma única vez).
        Força a atualização chamando o _toggle_preview diretamente (em vez de só setChecked),
        para garantir o sincronismo flag/visibilidade/caption do botão."""
        if not self._preview_visible:
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
    """Configurações e informações do app."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings = get_settings()
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        t = QLabel("Configurações")
        t.setProperty("class", "sectionTitle")
        root.addWidget(t)

        cards = QHBoxLayout()
        cards.setSpacing(14)

        # Dirs
        c1 = CardWidget(); cl = c1.layout_()
        th = QLabel("Diretórios"); th.setProperty("class", "sectionTitle"); cl.addWidget(th)
        info_dirs = QFormLayout()
        info_dirs.addRow("Dados auxiliares:", self._lbl(str(self.settings.data_dir)))
        info_dirs.addRow("Cache temporário:", self._lbl(str(self.settings.cache_dir)))
        info_dirs.addRow("Saída padrão:", self._lbl(str(self.settings.output_dir)))
        info_dirs.addRow("Logs:", self._lbl(str(self.settings.log_dir)))
        wrap = QWidget(); wrap.setLayout(info_dirs); cl.addWidget(wrap)

        btn_open = QPushButton("📂 Abrir diretório de saída")
        btn_open.clicked.connect(lambda: os.startfile(self.settings.output_dir) if hasattr(os, "startfile") else None)
        cl.addWidget(btn_open)

        cards.addWidget(c1, 1)

        # LLM
        c2 = CardWidget(); cl2 = c2.layout_()
        th2 = QLabel("LLM Local"); th2.setProperty("class", "sectionTitle"); cl2.addWidget(th2)
        form2 = QFormLayout()
        self.txt_model_path = QLineEdit(str(self.settings.llm_model_path or ""))
        self.txt_model_path.setPlaceholderText("Caminho para .gguf (ex.: Phi-3-mini-4k-instruct-q4.gguf)")
        form2.addRow("Modelo (GGUF):", self.txt_model_path)
        self.spin_ctx = QSpinBox(); self.spin_ctx.setRange(512, 32768); self.spin_ctx.setValue(self.settings.llm_n_ctx); self.spin_ctx.setSingleStep(512)
        form2.addRow("Contexto (tokens):", self.spin_ctx)
        self.spin_threads = QSpinBox(); self.spin_threads.setRange(1, 16); self.spin_threads.setValue(self.settings.llm_n_threads)
        form2.addRow("Threads CPU:", self.spin_threads)
        self.spin_gpu = QSpinBox(); self.spin_gpu.setRange(0, 99); self.spin_gpu.setValue(self.settings.llm_n_gpu_layers)
        form2.addRow("Camadas GPU:", self.spin_gpu)
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

        import os

    def _lbl(self, text: str) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet("background:#0F1A1C; padding:6px 10px; border-radius:6px; color:#C4D4D2;")
        l.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return l

    def _save(self) -> None:
        # Salva preferências em .env simples para o usuário
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
            env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            QMessageBox.information(self, "Sucesso", f"Preferências salvas em:\n{env_path}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Erro ao salvar", str(e))


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
