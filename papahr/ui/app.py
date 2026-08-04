"""Janela principal da UI desktop do PaPAHR."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# Força codificação UTF-8 em stdout/stderr para evitar UnicodeEncodeError com
# acentos em loggers (structlog) quando o terminal Windows usar cp1252/cp850.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - falha de encoding é não-fatal
        pass
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from papahr.config import get_settings
from papahr.logging_config import configure_logging, get_logger
from papahr.ui.pages import DashboardPage, LLMPage, PipelinePage, SettingsPage
from papahr.ui.pages.login_page import LoginPage
from papahr.auth import (
    AppUser,
    UserRole,
    current_user,
    get_user_store,
    is_authenticated,
)
from papahr.db import get_db

log = get_logger(__name__)

PAGE_ORDER = [
    ("▶️  Wizard Pipeline", "4 passos · 100% visual"),
    ("📊  Dashboard Saúde", "4 pilares + Território + Mapa"),
    ("🧠  Assistente LLM", "Análise inteligente IA local"),
    ("⚙️   Configurações", "Preferências e sobre"),
]

PAGE_PIPELINE = 0
PAGE_DASHBOARD = 1
PAGE_LLM = 2
PAGE_SETTINGS = 3

# Índices da QStackedWidget RAIZ
_ROOT_LOGIN = 0
_ROOT_MAIN_APP = 1


class MainWindow(QMainWindow):
    """Janela principal. Stack: Login (0) → App completo (sidebar + páginas) (1)."""

    def __init__(self) -> None:
        super().__init__()
        self.settings = get_settings().ensure_dirs()
        self.setWindowTitle(f"GSPHosp v{self.settings.app_version} — Gestão em Saúde Populacional Hospitalar")
        self.resize(self.settings.ui_window_width, self.settings.ui_window_height)
        self.setMinimumSize(QSize(1024, 680))

        # DB DuckDB + UserStore inicializados ANTES de qualquer UI (garante schemas e pastas)
        try:
            get_db()
        except Exception as exc:
            log.error("Falha ao inicializar DuckDB: %s", exc, exc_info=True)
        try:
            get_user_store()
        except Exception as exc:
            log.error("Falha ao inicializar UserStore: %s", exc, exc_info=True)

        self._setup_style()
        self._build_toolbar()
        self._build_root_stack()
        self._build_statusbar()
        log.info("UI inicializada")

    # ------------------------------------------------------------------
    def _setup_style(self) -> None:
        qss_path = Path(__file__).resolve().parent / "styles" / "material_dark_teal.qss"
        if qss_path.exists():
            self.setStyleSheet(qss_path.read_text(encoding="utf-8"))
        font = QFont("Segoe UI", 10)
        self.setFont(font)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Principal")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        tb.setStyleSheet(
            "QToolBar{background:#0B1416; border-bottom:1px solid #1F3A3D; padding:4px; spacing:6px;}"
            "QToolBar QToolButton{color:#C4D4D2; padding:6px 12px; border-radius:8px; font-weight:600;}"
            "QToolBar QToolButton:hover{background:#1A3A3C; color:#4FD1C5;}"
        )
        self.addToolBar(tb)
        self.tb_label = QLabel(f"  <b style='color:#4FD1C5;'>GSPHosp</b> "
                               f"<span style='color:#8FA6A3;'>· Gestão em Saúde Populacional Hospitalar</span>")
        self.tb_label.setTextFormat(Qt.RichText)
        self.tb_label.setStyleSheet("padding: 0 16px;")
        tb.addWidget(self.tb_label)
        tb.addSeparator()

        self.action_new_run = QAction("▶ Wizard", self)
        self.action_new_run.triggered.connect(lambda: self._go_page(PAGE_PIPELINE))
        tb.addAction(self.action_new_run)

        self.action_dashboard = QAction("📊 Dashboard", self)
        self.action_dashboard.triggered.connect(lambda: self._go_page(PAGE_DASHBOARD))
        tb.addAction(self.action_dashboard)

        self.action_llm = QAction("🧠 Análise LLM", self)
        self.action_llm.triggered.connect(lambda: self._go_page(PAGE_LLM))
        tb.addAction(self.action_llm)

        self.action_docs = QAction("ℹ Sobre", self)
        self.action_docs.triggered.connect(self._show_about)
        tb.addAction(self.action_docs)

        # Espaço para empurrar actions de usuário para a direita
        sep = QWidget()
        sep.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(sep)
        tb.addSeparator()

        # Badge com dados do usuário logado (aparece apenas após login)
        self.lbl_user_badge = QLabel("")
        self.lbl_user_badge.setStyleSheet(
            "QLabel { padding: 5px 12px; border: 1px solid #233539; border-radius: 999px; "
            "background:#121C1E; color:#4FD1C5; font-weight:700; font-size:12px; }"
        )
        self.lbl_user_badge.hide()
        tb.addWidget(self.lbl_user_badge)

        self.action_logout = QAction("🚪 Sair", self)
        self.action_logout.triggered.connect(self._on_logout)
        self.action_logout.setVisible(False)
        tb.addAction(self.action_logout)

    def _build_root_stack(self) -> None:
        """ROOT_STACK: 0=Login, 1=App completo (sidebar + páginas)."""
        self._root_stack = QStackedWidget()
        self._root_stack.setContentsMargins(0, 0, 0, 0)

        # Página 0 = Login (sempre a primeira ao abrir o app)
        self._login_page = LoginPage()
        self._login_page.login_sucesso.connect(self._on_login_success)
        self._root_stack.addWidget(self._login_page)

        # Página 1 = App completo (main app widget)
        self._main_app_widget = self._build_main_app_widget()
        self._root_stack.addWidget(self._main_app_widget)
        self.setCentralWidget(self._root_stack)

        # Mostra apenas a tela de login até que o usuário entre
        self._root_stack.setCurrentIndex(_ROOT_LOGIN)

    def _build_main_app_widget(self) -> QWidget:
        """Constrói o App completo (sidebar + stack de páginas). Igual ao _build_central original."""
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        spl_main = QSplitter(Qt.Horizontal)
        spl_main.setChildrenCollapsible(False)
        spl_main.setHandleWidth(8)

        sidebar = QFrame()
        sidebar.setMinimumWidth(220)
        sidebar.setMaximumWidth(360)
        sidebar.setStyleSheet("background:#0B1416; border-right:1px solid #1F3A3D;")
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(14, 22, 14, 14)

        logo_title = QLabel("GSPHosp")
        logo_title.setObjectName("appTitle")
        logo_sub = QLabel("DATASUS · SUS")
        logo_sub.setObjectName("appSubtitle")
        sb_layout.addWidget(logo_title)
        sb_layout.addWidget(logo_sub)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine); sep.setStyleSheet("color:#1F3A3D;");
        sb_layout.addSpacing(6); sb_layout.addWidget(sep); sb_layout.addSpacing(10)

        nav_label = QLabel("NAVEGAÇÃO")
        nav_label.setStyleSheet("color:#495F5E; font-size:9pt; font-weight:700; letter-spacing:1px; padding:6px 8px;")
        sb_layout.addWidget(nav_label)

        self.list_nav = QListWidget()
        self.list_nav.setObjectName("sidebar")
        self._nav_item_widgets: list[QLabel] = []
        for title, sub in PAGE_ORDER:
            it = QListWidgetItem()
            it.setSizeHint(QSize(0, 70))
            self.list_nav.addItem(it)
            lbl = QLabel(
                f"<div style='padding:6px 2px;line-height:1.15em;'>"
                f"<b>{title}</b><br>"
                f"<span style='color:#6B8280; font-size:9pt;'>{sub}</span>"
                f"</div>"
            )
            lbl.setTextFormat(Qt.RichText)
            lbl.setStyleSheet("color:#E6FFFB; background: transparent;")
            lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.list_nav.setItemWidget(it, lbl)
            self._nav_item_widgets.append(lbl)
        self.list_nav.currentRowChanged.connect(self._go_page)
        sb_layout.addWidget(self.list_nav, 1)

        bottom_info = QLabel(
            "<div style='color:#495F5E; font-size:8.5pt; padding:8px;'>"
            "⚡ Polars · PySide6<br>🦙 llama.cpp (opcional)<br>"
            f"v{self.settings.app_version}</div>"
        )
        bottom_info.setTextFormat(Qt.RichText)
        sb_layout.addWidget(bottom_info)

        self.stack = QStackedWidget()
        self.stack.setObjectName("pageContainer")
        self.stack.setContentsMargins(8, 8, 8, 8)
        self.stack.setMinimumWidth(820)
        self.page_pipeline = PipelinePage()
        self.page_dashboard = DashboardPage()
        self.page_llm = LLMPage()
        self.page_settings = SettingsPage()
        self.stack.addWidget(self.page_pipeline)      # PAGE_PIPELINE = 0
        self.stack.addWidget(self.page_dashboard)     # PAGE_DASHBOARD = 1
        self.stack.addWidget(self.page_llm)           # PAGE_LLM = 2
        self.stack.addWidget(self.page_settings)      # PAGE_SETTINGS = 3

        self.page_pipeline.result_ready.connect(self._on_result_ready)
        try:
            self.page_pipeline.btn_dashboard.clicked.connect(
                lambda: self._go_page(PAGE_DASHBOARD)
            )
        except AttributeError:
            log.warning("btn_dashboard não encontrado em PipelinePage")
        self.page_dashboard.hospital_selected.connect(self._on_hospital_selected)

        spl_main.addWidget(sidebar)
        spl_main.addWidget(self.stack)
        spl_main.setStretchFactor(0, 0)
        spl_main.setStretchFactor(1, 1)
        spl_main.setSizes([280, max(1200, self.width() - 300)])
        spl_main.setCollapsible(0, True)
        spl_main.setCollapsible(1, False)
        spl_main.setObjectName("rootMainSplitter")
        spl_main.setHandleWidth(4)

        root.addWidget(spl_main, 1)
        self._root_splitter = spl_main
        self.list_nav.setCurrentRow(PAGE_PIPELINE)
        return central

    def _build_statusbar(self) -> None:
        sb = QStatusBar(self)
        sb.setSizeGripEnabled(True)
        sb.showMessage("Aguardando login...")
        self.setStatusBar(sb)

    # ------------------------------------------------------------------
    def _go_page(self, index: int) -> None:
        if 0 <= index < self.stack.count():
            self.list_nav.setCurrentRow(index)
            self.stack.setCurrentIndex(index)

    def _on_result_ready(self, result) -> None:
        self.statusBar().showMessage(
            f"Pipeline concluído: {result.rows:,} linhas em {result.elapsed_seconds:.1f}s"
        )
        if not result.df.is_empty():
            self.page_llm.set_dataset(result.df)
            cnes_sel: list[str] | None = None
            try:
                fn = getattr(self.page_pipeline, "_selected_hospitals", None)
                if callable(fn):
                    cnes_sel = fn()
            except Exception:
                cnes_sel = None
            system = getattr(result, "system", "SIA_PA") or "SIA_PA"
            self.page_dashboard.set_dataset(result.df, system=system, cnes=cnes_sel)
            # Sugere ao usuário ir para o dashboard
            self.statusBar().showMessage(
                f"✅ Pronto! Clique em '📈 Ir para Dashboard' na página do Wizard para visualizar.",
                8000,
            )

    def _on_hospital_selected(self, cnes: str) -> None:
        self.statusBar().showMessage(f"🏥 Hospital selecionado: CNES {cnes}", 5000)

    def _on_login_success(self, user: AppUser) -> None:
        """Disparado pela LoginPage quando o usuário loga corretamente."""
        role_txt = {
            UserRole.ADMIN: "👑 ADMIN",
            UserRole.AUDITOR: "🕵️ AUDITOR",
            UserRole.USER: "👤 Usuário",
        }.get(user.role, "👤 Usuário")
        self.lbl_user_badge.setText(f"{role_txt} · {user.display_name}")
        self.lbl_user_badge.show()
        self.action_logout.setVisible(True)
        self.statusBar().showMessage(
            f"✅ Olá, {user.display_name}! Login OK. Papel: {role_txt}.",
            8000,
        )
        self._root_stack.setCurrentIndex(_ROOT_MAIN_APP)

        # Auto-carregamento dos dados persistidos do DuckDB se não tiver sessão
        def _post_login_autoload() -> None:
            try:
                self.page_dashboard.try_auto_persisted_fallback()
            except Exception as exc:  # pragma: no cover
                log.warning("Autoload DuckDB após login falhou: %s", exc)
        QTimer.singleShot(550, _post_login_autoload)

    def _on_logout(self) -> None:
        """Sai do usuário e volta para a tela de Login."""
        u = current_user()
        get_user_store().logout()
        get_db().audit_log("LOGOUT", "AUTH", "OK",
                           details={"username": u.username if u else None})
        # Reseta e mostra login
        self._root_stack.setCurrentIndex(_ROOT_LOGIN)
        self._login_page.reset_for_logout()
        self.lbl_user_badge.hide()
        self.action_logout.setVisible(False)
        self.statusBar().showMessage("Sessão encerrada.")

    def _show_about(self) -> None:
        QMessageBox.information(
            self, "Sobre o GSPHosp",
            f"<h2 style='color:#4FD1C5;'>GSPHosp v{self.settings.app_version}</h2>"
            "<p><b>G</b>estão em <b>S</b>aúde <b>P</b>opulacional <b>Hosp</b>elar — "
            "sistema de análise de produção ambulatorial e hospitalar do SUS via dados DATASUS.</p>"
            "<p>Processamento em Polars, validação Pydantic, download FTP resiliente e "
            "assistente de análise com LLM privado local (Phi-3 Mini / TinyLlama).</p>"
            "<p style='color:#8FA6A3; font-size:9pt;'>Dados: DATASUS (SIA/SIH/CNES/SIGTAP + IBGE)</p>"
        )


def main() -> int:
    """Ponto de entrada da GUI desktop."""
    configure_logging()
    settings = get_settings()
    settings.ensure_dirs()
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("GSPHosp")
    app.setOrganizationName("GSPHosp")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
