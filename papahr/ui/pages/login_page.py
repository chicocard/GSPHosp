"""Página de Login e Cadastro Inicial do PaPAHR.

Tela obrigatória antes de qualquer acesso ao Wizard e Dashboard.
- Se NÃO existir nenhum usuário cadastrado: força o modo "Cadastrar 1° Administrador".
- Se já existir: modo Login normal.
"""
from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from papahr.auth import AppUser, UserRole, get_user_store, is_authenticated
from papahr.db import get_db
from papahr.logging_config import get_logger

log = get_logger(__name__)

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}$")

_PRI_TEAL = "#4FD1C5"
_PRI_DARK = "#14B8A6"
_BG = "#0F1A1C"
_SURFACE = "#121C1E"
_SURFACE_2 = "#1B2B2F"
_TEXT = "#E6FFFA"
_TEXT_MUTED = "#64748B"
_DANG = "#EF4444"
_WARN = "#F59E0B"


def _mk_shadow() -> QGraphicsDropShadowEffect:
    s = QGraphicsDropShadowEffect()
    s.setBlurRadius(40)
    s.setXOffset(0)
    s.setYOffset(8)
    s.setColor(Qt.GlobalColor.black)
    return s


class LoginPage(QWidget):
    """Tela pública (sem navegação lateral). Emite `login_sucesso(user)` ao entrar."""

    login_sucesso = Signal(object)  # AppUser

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("LoginPage")
        self.setStyleSheet(f"QWidget#LoginPage {{ background: {_BG}; }}")
        self._falhas_login = 0  # contador falhas: mostra 'esqueci senha' depois de 3
        self._pending_authenticated_user: Optional[AppUser] = None  # usado pelo must_change_pwd
        store = get_user_store()
        n_users = store.count_users()

        # ---------------------- BUG FIX: Login sem criar admin ----------------------
        # Se 0 usuarios cadastrados -> cria AUTOMATICAMENTE admin padrao.
        # Se 1+ usuarios mas NENHUM eh ADMIN ativo com hash valido -> reseta admin forcadamente.
        # Usuario nao fica mais travado na tela de cadastro (UX bug reportado).
        DEFAULT_USER = "admin"
        DEFAULT_PWD  = "GSPHosp@2026"
        admins = [u for u in store.list_users() if u.role == UserRole.ADMIN and u.is_active]
        if (n_users == 0) or (not admins):
            motivo = "primeira vez" if n_users == 0 else "nenhum ADMIN ativo (base corrompida/incompleta)"
            try:
                existing = store.find_user_by_identity(DEFAULT_USER)
                if existing:
                    # Existe usuario 'admin' mas nao eh admin ativo? Promove + reseta senha
                    store.force_reset_password(
                        existing.user_id, DEFAULT_PWD,
                        must_change_pwd=False, to_role=UserRole.ADMIN
                    )
                else:
                    store.create_user(
                        username=DEFAULT_USER,
                        password_raw=DEFAULT_PWD,
                        full_name="Administrador GSPHosp",
                        email=None,
                        role=UserRole.ADMIN,
                        must_change_pwd=False,
                    )
                get_db().audit_log("ADMIN_USER_CREATE", "AUTH", status="OK",
                                   details={"username": DEFAULT_USER,
                                            "role": "ADMIN",
                                            "motivo": motivo,
                                            "auto_created": True})
                QMessageBox.information(
                    self,
                    "👑 Conta Administrador Criada",
                    f"<h3>🔑 Credenciais padrão do GSPHosp</h3>"
                    f"<p>Motivo: <b>{motivo}</b>. Conta criada/resetada automaticamente.</p>"
                    f"<table cellpadding='6' cellspacing='0' style='font-size:14px;margin:8px 0;'>"
                    f"<tr><td align='right'><b>Username: </b></td><td><code style='color:{_PRI_TEAL};background:#121C1E;padding:2px 8px;border-radius:6px;'>{DEFAULT_USER}</code></td></tr>"
                    f"<tr><td align='right'><b>Senha:    </b></td><td><code style='color:{_PRI_TEAL};background:#121C1E;padding:2px 8px;border-radius:6px;'>{DEFAULT_PWD}</code></td></tr>"
                    f"</table>"
                    f"<p>💡 Recomendamos <b>trocar a senha</b> após o primeiro login "
                    f"(menu <b>⚙️ Configurações</b> → <b>Segurança</b>).</p>",
                )
                n_users = max(n_users, 1)
            except Exception as exc:  # pragma: no cover
                log.warning("Falha ao criar/admin resetar admin padrao: %s", exc, exc_info=True)
                n_users = store.count_users()
        # ---------------------------------------------------------------------------

        self._modo_cadastro_admin = n_users == 0
        self._build_ui()
        self._apply_mode()

    # ----------------------------- UI -----------------------------
    def _build_ui(self) -> None:
        # Raiz: duas colunas (esquerda = branding, direita = card de login)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --------------------- LEFT (branding SUS) ---------------------
        left = QFrame()
        left.setStyleSheet(f"""
            QFrame {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #0B1E1C, stop:0.45 #0F2D2A, stop:1 #14B8A6);
                color: {_TEXT};
            }}
        """)
        left.setMinimumWidth(420)
        left.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(48, 48, 48, 48)
        ll.setSpacing(18)

        title = QLabel("🏥 GSPHosp")
        title.setStyleSheet("font-size:34px;font-weight:900;color:#4FD1C5;letter-spacing:-0.03em;")
        sub = QLabel("Gestão em Saúde Populacional Hospitalar")
        sub.setStyleSheet("font-size:18px;font-weight:600;color:#E6FFFA;")
        tag = QLabel("DATASUS · SIA/SIH · CNES · SIGTAP")
        tag.setStyleSheet(
            "font-size:13px;color:#4FD1C5;background:#121C1E;padding:6px 10px;border-radius:999px;"
            "border:1px solid #233539;"
        )
        ll.addWidget(title, 0, Qt.AlignmentFlag.AlignBottom)
        ll.addWidget(sub)
        ll.addSpacing(6)
        ll.addWidget(tag)
        ll.addStretch(1)

        bullets = [
            ("💾", "Dados persistidos em DuckDB (arquitetura Bronze/Silver/Gold)"),
            ("🔒", "Senhas criptografadas Argon2id + auditoria de todas as ações"),
            ("🗺️", "Filtro por UF, Região de Saúde e Macrorregião"),
            ("📅", "Períodos sazonais (ex: todos os Janeiros) e meses específicos"),
            ("📊", "7 abas: Risco, Território, Eficácia, Transição, Benchmarking, Glosas"),
        ]
        for emoji, txt in bullets:
            row = QLabel(f'<span style="font-size:18px;">{emoji}</span>&nbsp;&nbsp;'
                         f'<span style="color:#CCFBF1;">{txt}</span>')
            row.setStyleSheet("font-size:14px;")
            row.setWordWrap(True)
            ll.addWidget(row)
        ll.addStretch(2)
        foot = QLabel("v2.0 — Pasta de dados segura no perfil do usuário.")
        foot.setStyleSheet(f"color:{_TEXT_MUTED};font-size:12px;")
        ll.addWidget(foot)
        root.addWidget(left, 2)

        # --------------------- RIGHT (card login/cadastro) ---------------------
        right_wrap = QWidget()
        right_wrap.setStyleSheet(f"QWidget {{ background: {_BG}; }}")
        rwl = QHBoxLayout(right_wrap)
        rwl.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setGraphicsEffect(_mk_shadow())
        card.setStyleSheet(f"""
            QFrame {{
                background: {_SURFACE};
                border:1px solid #233539;
                border-radius: 18px;
            }}
        """)
        card.setMinimumWidth(430)
        card.setMaximumWidth(520)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(36, 34, 36, 34)
        cl.setSpacing(12)

        # Título alterna Login vs Cadastro
        self.lbl_titulo = QLabel("Entrar no GSPHosp")
        self.lbl_titulo.setStyleSheet(f"font-size:24px;font-weight:800;color:{_TEXT};")
        self.lbl_subtitulo = QLabel("Informe suas credenciais para acessar.")
        self.lbl_subtitulo.setStyleSheet(f"color:{_TEXT_MUTED};font-size:13px;")
        self.lbl_subtitulo.setWordWrap(True)
        cl.addWidget(self.lbl_titulo)
        cl.addWidget(self.lbl_subtitulo)
        cl.addSpacing(6)

        # Badge de status ADMIN primeiro cadastro
        self.lbl_badge_admin = QLabel("👑 Nenhum usuário cadastrado — crie a conta de Administrador")
        self.lbl_badge_admin.setStyleSheet(
            "background:#1E2A2B;color:#F59E0B;padding:8px 12px;border:1px solid #5B3A0E;border-radius:10px;font-size:12px;"
        )
        self.lbl_badge_admin.setWordWrap(True)
        cl.addWidget(self.lbl_badge_admin)
        cl.addSpacing(8)

        # ------------------- Stack -------------------
        self.stack = QStackedLayout()

        # ======== Tela LOGIN ========
        w_login = QWidget()
        w_login_l = QVBoxLayout(w_login); w_login_l.setContentsMargins(0, 0, 0, 0); w_login_l.setSpacing(10)
        self.ed_login_user = self._mk_field("👤 Username ou e-mail")
        self.ed_login_pwd = self._mk_field("🔒 Senha", is_password=True)
        self.chk_lembrar = QCheckBox("Lembrar-me neste computador")
        self.chk_lembrar.setStyleSheet(f"QCheckBox {{ color: {_TEXT_MUTED}; font-size: 13px; }}")
        self.btn_login = self._mk_primary("✅ Entrar")
        self.btn_login.clicked.connect(self._do_login)
        self.btn_login.setAutoDefault(True)
        self.btn_login.setDefault(True)
        self.ed_login_user.returnPressed.connect(self.btn_login.click)
        self.ed_login_pwd.returnPressed.connect(self.btn_login.click)
        self.lbl_erro_login = QLabel("")
        self.lbl_erro_login.setStyleSheet(f"color:{_DANG};font-size:13px;font-weight:600;")
        self.lbl_erro_login.setWordWrap(True)
        self.lbl_erro_login.setMinimumHeight(26)
        self.lbl_erro_login.setTextFormat(Qt.TextFormat.RichText)
        # w_login_l.setObjectName("vl_login_card")  # (se existir parent dele pra shake
        self.btn_forgot = self._mk_ghost("🔑 Esqueci minha senha / Recuperar admin")
        self.btn_forgot.clicked.connect(self._do_forgot_password)
        self.btn_forgot.hide()
        self.btn_force_reset = self._mk_ghost("🛠️ Resetar admin forçado (padrão admin / GSPHosp@2026)")
        self.btn_force_reset.clicked.connect(lambda: self._do_forgot_password(_force_direct=True))
        self.btn_toggle_cad = self._mk_ghost("Novo por aqui? Criar conta")
        self.btn_toggle_cad.clicked.connect(self._to_cadastro)

        w_login_l.addWidget(QLabel("<b>Username ou e-mail</b>"))
        w_login_l.addWidget(self.ed_login_user)
        w_login_l.addWidget(QLabel("<b>Senha</b>"))
        w_login_l.addWidget(self.ed_login_pwd)
        w_login_l.addSpacing(2)
        w_login_l.addWidget(self.chk_lembrar)
        w_login_l.addSpacing(4)
        w_login_l.addWidget(self.btn_login)
        w_login_l.addWidget(self.lbl_erro_login)
        w_login_l.addWidget(self.btn_forgot, 0, Qt.AlignmentFlag.AlignCenter)
        w_login_l.addWidget(self.btn_force_reset, 0, Qt.AlignmentFlag.AlignCenter)
        w_login_l.addStretch(1)
        w_login_l.addWidget(self.btn_toggle_cad, 0, Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(w_login)

        # ======== Tela CADASTRO ========
        w_cad = QWidget()
        wl = QVBoxLayout(w_cad); wl.setContentsMargins(0, 0, 0, 0); wl.setSpacing(8)
        self.ed_cad_nome = self._mk_field("Nome completo")
        self.ed_cad_user = self._mk_field("Username (3+ letras/números)")
        self.ed_cad_email = self._mk_field("📧 E-mail (opcional)")
        self.ed_cad_pwd = self._mk_field("🔒 Senha (6+ caracteres)", is_password=True)
        self.ed_cad_pwd2 = self._mk_field("🔐 Repetir senha", is_password=True)
        self.btn_cad = self._mk_primary("✅ Criar conta e entrar")
        self.btn_cad.clicked.connect(self._do_cadastro)
        self.btn_cad.setAutoDefault(True)
        self.btn_cad.setDefault(True)
        self.ed_cad_nome.returnPressed.connect(lambda: self.ed_cad_user.setFocus())
        self.ed_cad_user.returnPressed.connect(lambda: self.ed_cad_email.setFocus())
        self.ed_cad_email.returnPressed.connect(lambda: self.ed_cad_pwd.setFocus())
        self.ed_cad_pwd.returnPressed.connect(lambda: self.ed_cad_pwd2.setFocus())
        self.ed_cad_pwd2.returnPressed.connect(self.btn_cad.click)
        self.lbl_erro_cad = QLabel("")
        self.lbl_erro_cad.setStyleSheet(f"color:{_DANG};font-size:13px;font-weight:600;")
        self.lbl_erro_cad.setWordWrap(True)
        self.lbl_erro_cad.setMinimumHeight(26)
        self.btn_toggle_log = self._mk_ghost("Já tenho conta — Entrar")
        self.btn_toggle_log.clicked.connect(self._to_login)

        wl.addWidget(QLabel("<b>Nome completo</b>"))
        wl.addWidget(self.ed_cad_nome)
        wl.addWidget(QLabel("<b>Username</b>"))
        wl.addWidget(self.ed_cad_user)
        wl.addWidget(QLabel("<b>E-mail</b>"))
        wl.addWidget(self.ed_cad_email)
        wl.addWidget(QLabel("<b>Senha</b>"))
        wl.addWidget(self.ed_cad_pwd)
        wl.addWidget(QLabel("<b>Repetir senha</b>"))
        wl.addWidget(self.ed_cad_pwd2)
        wl.addSpacing(4)
        wl.addWidget(self.btn_cad)
        wl.addWidget(self.lbl_erro_cad)
        wl.addStretch(1)
        wl.addWidget(self.btn_toggle_log, 0, Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(w_cad)

        # ======== Tela MUDANCA OBRIGATORIA DE SENHA (must_change_pwd=True) ========
        w_chg = QWidget()
        cl3 = QVBoxLayout(w_chg); cl3.setContentsMargins(0, 0, 0, 0); cl3.setSpacing(8)
        self.ed_chg_pwd1 = self._mk_field("🔒 Nova senha (6+ caracteres)", is_password=True)
        self.ed_chg_pwd2 = self._mk_field("🔐 Confirmar nova senha", is_password=True)
        self.lbl_erro_chg = QLabel("")
        self.lbl_erro_chg.setStyleSheet(f"color:{_DANG};font-size:12px;")
        self.lbl_erro_chg.setWordWrap(True)
        self.btn_chg = self._mk_primary("🔐 Trocar senha e entrar no GSPHosp")
        self.btn_chg.clicked.connect(self._do_change_pwd_flow)
        self.btn_chg.setAutoDefault(True)
        self.btn_chg.setDefault(True)
        self.ed_chg_pwd1.returnPressed.connect(lambda: self.ed_chg_pwd2.setFocus())
        self.ed_chg_pwd2.returnPressed.connect(self.btn_chg.click)
        self.lbl_chg_hint = QLabel(
            "ℹ️ Um administrador redefiniu sua senha recentemente, "
            "ou você usou o botão recuperar senha. Por segurança, você deve "
            "definir uma senha pessoal ANTES de acessar o sistema."
        )
        self.lbl_chg_hint.setStyleSheet(f"color:{_WARN};font-size:12px;background:#2A1414;padding:10px;border-radius:10px;border:1px solid #5B3A0E;")
        self.lbl_chg_hint.setWordWrap(True)
        cl3.addWidget(self.lbl_chg_hint)
        cl3.addSpacing(6)
        cl3.addWidget(QLabel("<b>Nova senha</b>"))
        cl3.addWidget(self.ed_chg_pwd1)
        cl3.addWidget(QLabel("<b>Confirmar nova senha</b>"))
        cl3.addWidget(self.ed_chg_pwd2)
        cl3.addSpacing(4)
        cl3.addWidget(self.btn_chg)
        cl3.addWidget(self.lbl_erro_chg)
        cl3.addStretch(1)
        self.stack.addWidget(w_chg)

        cl.addLayout(self.stack, 1)

        rwl.addStretch(1); rwl.addWidget(card); rwl.addStretch(1)
        root.addWidget(right_wrap, 3)

    # ----------------------------- helpers -----------------------------
    _FIELD_PAD_RIGHT = "46px"
    _FIELD_BASE_QSS = f"""
        QLineEdit {{
            background: {_SURFACE_2};
            color: {_TEXT};
            padding: 10px 14px;
            border: 1px solid #233539;
            border-radius: 10px;
            font-size: 14px;
        }}
        QLineEdit:focus {{ border: 1px solid {_PRI_TEAL}; background: #112123; }}
        QLineEdit[withEyeBtn="true"] {{
            padding-right: {_FIELD_PAD_RIGHT};
        }}
    """
    _FIELD_ERROR_QSS = f"""
        QLineEdit {{
            background: #2A1414;
            color: {_TEXT};
            padding: 10px 14px;
            border: 2px solid {_DANG};
            border-radius: 10px;
            font-size: 14px;
        }}
        QLineEdit:focus {{ border: 2px solid #F87171; background: #3A1818; }}
        QLineEdit[withEyeBtn="true"] {{
            padding-right: {_FIELD_PAD_RIGHT};
        }}
    """

    _EYE_SHOW_SVG = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="#5EEAD4" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>'
        '<circle cx="12" cy="12" r="3"/></svg>'
    )
    _EYE_HIDE_SVG = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="#94A3B8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A10.94 10.94 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>'
        '<line x1="1" y1="1" x2="23" y2="23"/></svg>'
    )

    @staticmethod
    def _svg_to_icon(svg_text: str, size_px: int = 20) -> "QIcon":
        from PySide6.QtCore import QByteArray, Qt
        from PySide6.QtGui import QIcon, QPixmap
        from PySide6.QtSvg import QSvgRenderer
        from PySide6.QtGui import QImage, QPainter
        img = QImage(size_px, size_px, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
            renderer.render(painter)
        finally:
            painter.end()
        pm = QPixmap.fromImage(img)
        return QIcon(pm)

    def _install_password_eye(self, ed: QLineEdit) -> None:
        """Adiciona botão 👁️ Material Design 3 sobreposto ao QLineEdit.

        Abordagem QToolButton (em vez de QAction) = funciona 100% no Windows com
        QSS customizado e em qualquer DPI. Abre espaço no QLineEdit via
        propriedade `withEyeBtn="true"` + padding-right no stylesheet.
        """
        from PySide6.QtCore import Qt, QSize, QEvent, QObject
        from PySide6.QtWidgets import QToolButton

        ed.setProperty("withEyeBtn", "true")
        ed.style().unpolish(ed)
        ed.style().polish(ed)

        try:
            ico_show = self._svg_to_icon(self._EYE_SHOW_SVG, size_px=22)
            ico_hide = self._svg_to_icon(self._EYE_HIDE_SVG, size_px=22)
        except Exception:
            from PySide6.QtGui import QIcon
            ico_show = QIcon()
            ico_hide = QIcon()

        btn = QToolButton(ed)
        btn.setObjectName("pwd_eye_btn")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setIconSize(QSize(22, 22))
        btn.setFixedSize(QSize(34, 34))
        btn.setAutoRaise(True)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        btn.setStyleSheet("""
            QToolButton {
                background: transparent;
                border: none;
                border-radius: 8px;
                padding: 0px;
                margin: 0px;
            }
            QToolButton:hover {
                background: rgba(94, 234, 212, 0.12);
            }
            QToolButton:pressed {
                background: rgba(94, 234, 212, 0.22);
            }
        """)
        btn.setToolTip("Mostrar senha (clique para visualizar os caracteres digitados)")

        if not ico_show.isNull():
            btn.setIcon(ico_show)
        else:
            btn.setText("👁")

        state = {"is_hidden": True}

        def _toggle():
            state["is_hidden"] = not state["is_hidden"]
            if state["is_hidden"]:
                ed.setEchoMode(QLineEdit.EchoMode.Password)
                if not ico_show.isNull():
                    btn.setIcon(ico_show)
                    btn.setText("")
                else:
                    btn.setText("👁")
                btn.setToolTip("Mostrar senha (clique para visualizar os caracteres digitados)")
            else:
                ed.setEchoMode(QLineEdit.EchoMode.Normal)
                if not ico_hide.isNull():
                    btn.setIcon(ico_hide)
                    btn.setText("")
                else:
                    btn.setText("🙈")
                btn.setToolTip("Ocultar senha (clique para voltar aos pontinhos)")
            ed.setFocus()

        btn.clicked.connect(_toggle)

        def _reposicionar():
            fw = ed.width()
            fh = ed.height()
            btn_w = btn.width()
            btn_h = btn.height()
            x = fw - btn_w - 6
            y = (fh - btn_h) // 2
            btn.move(x, max(1, y))

        _reposicionar()

        class _Filter(QObject):
            def eventFilter(_self, obj, ev):
                if ev.type() in (QEvent.Type.Resize, QEvent.Type.Show):
                    _reposicionar()
                return super().eventFilter(obj, ev)

        ed.installEventFilter(_Filter(ed))
        btn.show()
        btn.raise_()

    def _mk_field(self, placeholder: str, is_password: bool = False) -> QLineEdit:
        ed = QLineEdit()
        ed.setPlaceholderText(placeholder)
        ed.setMinimumHeight(48)
        ed.setStyleSheet(self._FIELD_BASE_QSS)
        if is_password:
            ed.setEchoMode(QLineEdit.EchoMode.Password)
            self._install_password_eye(ed)
        ed.textEdited.connect(lambda *_: self._clear_field_error(ed))
        return ed

    def _mark_field_error(self, *fields: QLineEdit) -> None:
        for ed in fields:
            ed.setStyleSheet(self._FIELD_ERROR_QSS)
        # Shake animation: pega o parent do primeiro campo (o WIDGET do card), animacao horizontal
        target = fields[0].parent()
        if target is None:
            return
        anim = QPropertyAnimation(target, b"pos", self)
        orig = target.pos()
        frames = [
            orig,
            orig + (-10, 0),
            orig + (+10, 0),
            orig + (-8, 0),
            orig + (+8, 0),
            orig + (-4, 0),
            orig + (+4, 0),
            orig,
        ]
        anim.setDuration(420)
        anim.setEasingCurve(QEasingCurve.Type.OutElastic)
        for i, p in enumerate(frames):
            anim.setKeyValueAt(i / max(1, len(frames) - 1), p)
        anim.start()
        self._shake_anim = anim  # manter referência pra não ser GC'ed

    @staticmethod
    def _clear_field_error(ed: QLineEdit) -> None:
        ed.setStyleSheet(LoginPage._FIELD_BASE_QSS)

    # ---------------------- helpers SEGUROS ----------------------
    # DUCKDB crasha (ex: WAL corrompido)? Nós NAO queremos que o botão de login
    # ou cadastro morra silenciosamente. A auditoria é NÃO CRÍTICA — se falhar,
    # só log warning e continua com o fluxo normal.
    def _safe_get_db(self):
        """Retorna DuckDB (get_db) ou None se der QUALQUER erro (WAL, permissões).

        Nunca crasha a UI do botão Entrar/Criar conta."""
        try:
            return get_db()
        except Exception as exc:
            log.warning(
                "[_safe_get_db] Auditoria DUCKDB indisponivel (fluxo LOGIN/CADASTRO CONTINUA!): %s",
                type(exc).__name__ + ": " + str(exc)[:160],
            )
            return None

    def _safe_audit_log(self, db, event: str, category: str, *,
                        status: str = "OK", details: Optional[dict] = None) -> None:
        """Auditoria SEGURA — duckdb.crash não crasha mais o botão.

        db=None? -> ignora silenciosamente. db.audit_log crasha? -> log warning só."""
        if db is None:
            return
        try:
            db.audit_log(event, category, status=status, details=details or {})
        except Exception as exc:
            log.warning(
                "[_safe_audit_log] Ignorado erro na auditoria event=%s status=%s: %s",
                event, status, type(exc).__name__ + ": " + str(exc)[:160],
            )

    def _mk_primary(self, text: str) -> QPushButton:
        b = QPushButton(text)
        b.setMinimumHeight(44)
        b.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #0D9488, stop:1 #0F766E);
                color: #FFFFFF;
                font-weight: 800;
                border: 1px solid #2DD4BF;
                border-radius: 10px;
                padding: 8px 20px;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #0F766E, stop:1 #115E59);
                color: #FEF08A;
            }}
            QPushButton:disabled {{ background: #233539; color: #495F5E; }}
        """)
        return b

    def _mk_ghost(self, text: str) -> QPushButton:
        b = QPushButton(text)
        b.setMinimumHeight(38)
        b.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {_PRI_TEAL};
                font-weight: 700;
                border: none;
                font-size: 13px;
            }}
            QPushButton:hover {{ color: #5EE8D9; text-decoration: underline; }}
        """)
        return b

    # ----------------------------- modos -----------------------------
    def _apply_mode(self) -> None:
        if self._modo_cadastro_admin:
            self.stack.setCurrentIndex(1)
            self.lbl_titulo.setText("👑 Criar Administrador")
            self.lbl_subtitulo.setText(
                "Essa é a primeira vez que o GSPHosp abre nesta máquina. "
                "Crie a conta de ADMINISTRADOR para iniciar. Dados são armazenados no seu perfil de usuário."
            )
            self.lbl_badge_admin.show()
            self.btn_toggle_log.hide()
            self.btn_toggle_cad.hide()
            self.btn_login.setDefault(False)
            self.btn_cad.setDefault(True)
            self.ed_cad_nome.setFocus()
        else:
            self.stack.setCurrentIndex(0)
            self.lbl_titulo.setText("Entrar no GSPHosp")
            self.lbl_subtitulo.setText("Informe suas credenciais para acessar o Wizard e o Dashboard.")
            self.lbl_badge_admin.hide()
            self.btn_toggle_log.show()
            self.btn_toggle_cad.show()
            self.btn_cad.setDefault(False)
            self.btn_login.setDefault(True)
            self.ed_login_user.setFocus()

    def _to_cadastro(self) -> None:
        self.stack.setCurrentIndex(1)
        self.lbl_titulo.setText("Criar nova conta")
        self.lbl_subtitulo.setText("Preencha os dados abaixo para criar seu usuário.")
        self.lbl_erro_cad.setText("")
        self.btn_login.setDefault(False)
        self.btn_cad.setDefault(True)
        self.ed_cad_nome.setFocus()

    def _to_login(self) -> None:
        self.stack.setCurrentIndex(0)
        self.lbl_titulo.setText("Entrar no GSPHosp")
        self.lbl_subtitulo.setText("Informe suas credenciais para acessar.")
        self.lbl_erro_login.setText("")
        self.btn_cad.setDefault(False)
        self.btn_chg.setDefault(False)
        self.btn_login.setDefault(True)
        self.ed_login_user.setFocus()

    def _to_must_change_pwd(self, authenticated_user: AppUser) -> None:
        """Mostra tela de mudança OBRIGATÓRIA de senha (must_change_pwd=True)."""
        self._pending_authenticated_user = authenticated_user  # espera a troca para emitir login_sucesso
        self.stack.setCurrentIndex(2)
        self.lbl_titulo.setText("🔐 Troca de senha obrigatória")
        self.lbl_subtitulo.setText(
            f"Olá, <b>{authenticated_user.display_name}</b>. "
            "Por favor escolha uma senha nova para continuar."
        )
        self.btn_cad.setDefault(False)
        self.btn_login.setDefault(False)
        self.btn_chg.setDefault(True)
        self.ed_chg_pwd1.clear()
        self.ed_chg_pwd2.clear()
        self.lbl_erro_chg.setText("")
        self.ed_chg_pwd1.setFocus()

    # ----------------------------- ações -----------------------------
    def _do_login(self) -> None:
        # 1) Limpa labels e erros antigos SEMPRE que o usuario clica em Entrar (mesmo com campos preenchidos!)
        self.lbl_erro_login.setStyleSheet(f"color:{_DANG};font-size:13px;font-weight:600;")
        self.lbl_erro_login.setText("")
        self._clear_field_error(self.ed_login_user)
        self._clear_field_error(self.ed_login_pwd)
        QApplication.processEvents()  # <- GARANTE ATUALIZACAO VISUAL ANTES DE TUDO!

        user_txt = self.ed_login_user.text().strip()
        pwd = self.ed_login_pwd.text().rstrip("\r\n\t ")
        if pwd != self.ed_login_pwd.text():
            self.ed_login_pwd.setText(pwd)
        log.info(
            "[DEBUG _do_login] clique='Entrar' | identity=%r len=%d | pwd_len=%d | "
            "usuarios_existentes=%d | antes_hint=%s",
            user_txt, len(user_txt), len(pwd),
            (get_user_store().count_users() if get_user_store() else -1),
            (("None" if get_user_store() else "store-NAO-criado"))
        )
        # Auto-strip visual na tela de login: se usuario digitou espacos/tab/enter no final da senha, tira para nao bater diferente!
        QApplication.processEvents()

        if not user_txt or not pwd:
            self.lbl_erro_login.setText(
                "❌ Preencha ambos os campos: <b>Username ou e-mail</b> e <b>Senha</b>."
            )
            if not user_txt and not pwd:
                self._mark_field_error(self.ed_login_user, self.ed_login_pwd)
                self.ed_login_user.setFocus()
            elif not user_txt:
                self._mark_field_error(self.ed_login_user)
                self.ed_login_user.setFocus()
            else:
                self._mark_field_error(self.ed_login_pwd)
                self.ed_login_pwd.setFocus()
            self._falhas_login += 1
            if self._falhas_login >= 3: self.btn_forgot.show()
            QApplication.processEvents()
            return

        # Usa _safe_get_db pois auditoria é NAO CRITICA (DuckDB crash -> login continua!)
        db = self._safe_get_db()
        store = get_user_store()

        # Sugere usuários encontrados no banco se o que o usuário digitou NÃO existe
        existe = store.find_user_by_identity(user_txt)
        if existe is None:
            todos = store.list_users()
            sugestoes = []
            for u in todos:
                if u.username:
                    sugestoes.append(f"• <b>{u.username}</b>")
                if u.email and (u.email not in [s for s in sugestoes]):
                    sugestoes.append(f"• 📧 {u.email}")
            if not todos:
                hint_msg = ("<br>⚠️ NÃO HÁ NENHUM USUÁRIO CADASTRADO NA BASE NOVA!<br>"
                            "Clique no botão verde abaixo para <b>Criar nova conta</b>, "
                            "ou clique no botão cinza <b>🛠️ Resetar admin forçado</b>.")
            else:
                hint_msg = (
                    "<br>⚠️ Username/e-mail <b>NÃO ENCONTRADO NA BASE NOVA GSPHosp</b>.<br>"
                    "Você apagou a base antiga PAPAHU (onde estava o chicocard2026!). "
                    "<b>Usuários válidos na base NOVA são:</b><br>"
                    + "<br>".join(sugestoes)
                    + "<br><span style='color:{w}'>💡 Clique em '🛠️ Resetar admin forçado' abaixo para criar admin/GSPHosp@2026 AGORA!</span>"
                ).replace("{w}", _WARN)
            self._falhas_login += 1
            self._safe_audit_log(
                db, "LOGIN_FAIL", "AUTH", status="FAIL",
                details={"username": user_txt, "attempt": self._falhas_login,
                         "error": "Usuario/email nao existe na base NOVA"}
            )
            self.lbl_erro_login.setText(
                f"❌ Usuário/e-mail <code style='color:#FCA5A5;'>{user_txt}</code> não cadastrado. "
                f"(Tentativa #{self._falhas_login})" + hint_msg
            )
            self._mark_field_error(self.ed_login_user)
            if self._falhas_login >= 3:
                self.btn_forgot.show()
            self.ed_login_user.setFocus()
            self.ed_login_user.selectAll()
            QApplication.processEvents()
            return

        # Username/email EXISTE: tenta autenticar
        try:
            user = store.login(user_txt, pwd)
        except Exception as exc:
            self._falhas_login += 1
            self._safe_audit_log(
                db, "LOGIN_FAIL", "AUTH", status="FAIL",
                details={"username": user_txt, "error": str(exc),
                         "attempt": self._falhas_login}
            )
            msg = str(exc)
            self.lbl_erro_login.setText(
                f"❌ Falha no login: {msg}<br>"
                f"<span style='color:{_WARN};'>Tentativa #{self._falhas_login}. "
                f"(dica: o username admin com senha 'GSPHosp@2026' SEMPRE funciona se usar o reset forçado abaixo!)</span>"
            )
            self._mark_field_error(self.ed_login_pwd, self.ed_login_user)
            self.ed_login_pwd.setFocus()
            self.ed_login_pwd.selectAll()
            # 3+ falhas: mostra botao recuperar senha + lista usuarios disponiveis
            if self._falhas_login >= 3:
                self.btn_forgot.show()
                extras = []
                for u in store.list_users():
                    extras.append(
                        f"• <b>{u.username}</b> ({u.role.value}{' 👑' if u.role == UserRole.ADMIN else ''} — {u.full_name}"
                        + (f" · 📧 {u.email}" if u.email else "") + ")"
                    )
                lista = "<br>".join(extras) if extras else "<i>(nenhum usuário cadastrado — bug raro)</i>"
                self.lbl_erro_login.setText(
                    self.lbl_erro_login.text() +
                    f"<br><br>👉 Temos os seguintes usuários cadastrados hoje na base nova:<br>{lista}"
                    f"<br><span style='color:{_WARN};'>"
                    f"Clique no botão abaixo: <b>🛠️ Resetar admin forçado (admin/GSPHosp@2026)</b> — "
                    f"ele cria/redefine admin e entra automaticamente em 1 segundo!</span>"
                )
            QApplication.processEvents()
            return

        # --- LOGIN BEM-SUCEDIDO ---
        self._falhas_login = 0
        self.btn_forgot.hide()
        self._safe_audit_log(
            db, "LOGIN_OK", "AUTH", status="OK",
            details={"username": user.username, "role": user.role.value,
                     "must_change_pwd": user.must_change_pwd}
        )
        # Feedback VERDE visivel antes de entrar (usuario NAO tem mais duvida se funcionou)
        self.lbl_erro_login.setStyleSheet(f"color:#10B981;font-size:13px;font-weight:700;")
        self.lbl_erro_login.setText(
            f"✅ Login BEM-SUCEDIDO! <b>{user.full_name or user.username}</b> "
            f"(role {user.role.value}). Entrando no GSPHosp..."
        )
        self.btn_login.setEnabled(False)
        QApplication.processEvents()
        # Forca must_change (senha temporaria?)
        if user.must_change_pwd:
            self._to_must_change_pwd(user)
            return
        # 700ms para usuario ler a mensagem VERDE e depois EMITE O LOGIN
        QTimer.singleShot(700, lambda: self.login_sucesso.emit(user))

    def _do_forgot_password(self, _force_direct: bool = False) -> None:
        store = get_user_store()
        users = store.list_users()
        lines = [f"• {u.username}  [{u.role.value}]  {u.full_name}" for u in users]
        users_txt = "\n".join(lines) if lines else "(nenhum)"
        DEFAULT_USER = "admin"
        DEFAULT_PWD = "GSPHosp@2026"

        # MODO DIRETO: botao "Resetar admin forcado" clica direto, nao pergunta!
        if _force_direct:
            db = self._safe_get_db()
            existing = store.find_user_by_identity(DEFAULT_USER)
            try:
                if existing:
                    store.force_reset_password(
                        existing.user_id, DEFAULT_PWD,
                        must_change_pwd=False, to_role=UserRole.ADMIN
                    )
                    self._safe_audit_log(
                        db, "ADMIN_PASSWORD_RESET", "AUTH", status="OK",
                        details={"username": DEFAULT_USER, "force_direct": True,
                                 "must_change": False}
                    )
                else:
                    store.create_user(
                        username=DEFAULT_USER, password_raw=DEFAULT_PWD,
                        full_name="Administrador Padrao (forcado UI)", email=None,
                        role=UserRole.ADMIN, must_change_pwd=False,
                    )
                    self._safe_audit_log(
                        db, "ADMIN_USER_CREATE", "AUTH", status="OK",
                        details={"username": DEFAULT_USER, "role": "ADMIN",
                                 "force_direct": True}
                    )
            except Exception as exc:
                log.error("Falha force reset admin: %s", exc, exc_info=True)
                QMessageBox.critical(self, "Erro", f"Nao foi possivel resetar: {exc}")
                return
            self._falhas_login = 0
            self.btn_forgot.hide()
            self._to_login()
            self.ed_login_user.setText(DEFAULT_USER)
            self.ed_login_pwd.setText(DEFAULT_PWD)
            self.lbl_erro_login.setText(
                f"✅ Admin <b>{DEFAULT_USER}</b> resetado (senha <b>{DEFAULT_PWD}</b>). "
                f"Entrando automaticamente em 1 segundo..."
            )
            # Auto-clica no botao Entrar apos 900ms (QTimer singleShot para nao travar)
            QTimer.singleShot(900, self.btn_login.click)
            return

        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Warning)
        msg_box.setWindowTitle("🔑 Recuperar Acesso")
        msg_box.setText(
            f"<h3>Recuperar senha / Redefinir admin</h3>"
            f"<p>Usuarios cadastrados atualmente:</p>"
            f"<pre style='background:#121C1E;color:{_TEXT};padding:10px;border-radius:8px;'>{users_txt}</pre>"
            f"<p>Voce pode <b>(A)</b> criar uma <b>nova conta ADMIN de emergencia</b> agora:"
            f"<ul><li>👤 Username: <code style='color:{_PRI_TEAL};'>{DEFAULT_USER}</code></li>"
            f"<li>🔑 Senha:    <code style='color:{_PRI_TEAL};'>{DEFAULT_PWD}</code></li></ul></p>"
            f"<p>Ou <b>(B)</b>: cancelar e tentar novamente com outra combinacao.</p>"
        )
        btn_reset = msg_box.addButton(f"✅ Criar/Redefinir admin ({DEFAULT_USER}/{DEFAULT_PWD})",
                                      QMessageBox.ButtonRole.AcceptRole)
        msg_box.addButton("❌ Cancelar", QMessageBox.ButtonRole.RejectRole)
        msg_box.exec()
        if msg_box.clickedButton() is btn_reset:
            db = self._safe_get_db()
            existing = store.find_user_by_identity(DEFAULT_USER)
            try:
                if existing:
                    # Usa API pública nova (não mexe mais em _conn protegido)
                    store.force_reset_password(
                        existing.user_id, DEFAULT_PWD,
                        must_change_pwd=True, to_role=UserRole.ADMIN
                    )
                    self._safe_audit_log(
                        db, "ADMIN_PASSWORD_RESET", "AUTH", status="OK",
                        details={"username": DEFAULT_USER, "reset_to_default": True}
                    )
                    QMessageBox.information(self, "✅ Senha Redefinida",
                        f"<p>👑 Conta admin <b>{DEFAULT_USER}</b> foi redefinida com sucesso!<br>"
                        f"Use as credenciais abaixo no login:</p>"
                        f"<ul><li>Username: <b>{DEFAULT_USER}</b></li>"
                        f"<li>Senha:    <b>{DEFAULT_PWD}</b> (será pedido para trocar no próximo login — "
                        f"tela obrigatória de troca aparecerá automaticamente após você entrar)</li></ul>")
                else:
                    u = store.create_user(
                        username=DEFAULT_USER, password_raw=DEFAULT_PWD,
                        full_name="Administrador Padrão (Recuperação)", email=None,
                        role=UserRole.ADMIN, must_change_pwd=True)
                    self._safe_audit_log(
                        db, "ADMIN_USER_CREATE", "AUTH", status="OK",
                        details={"username": DEFAULT_USER, "role": "ADMIN",
                                 "recovery_created": True}
                    )
                    QMessageBox.information(self, "✅ Admin Criado",
                        f"<p>👑 Conta <b>admin</b> criada em modo recuperação.</p>"
                        f"<ul><li>Username: <b>{DEFAULT_USER}</b></li>"
                        f"<li>Senha:    <b>{DEFAULT_PWD}</b> (tela de troca obrigatória aparecerá após o login)</li></ul>")
                self._falhas_login = 0
                self.btn_forgot.hide()
                self._to_login()
                self.ed_login_user.setText(DEFAULT_USER)
                self.ed_login_pwd.setText(DEFAULT_PWD)
                # Foca no botão "Entrar" e seleciona tudo para o usuário ver e submeter com Enter
                self.ed_login_pwd.setFocus()
                self.ed_login_pwd.selectAll()
                self.lbl_erro_login.setText(
                    f"✅ Admin pronto! Clique em <b>Entrar</b> (ou pressione Enter) — "
                    f"será pedido para trocar a senha depois."
                )
            except Exception as exc:
                log.error("Falha reset admin: %s", exc, exc_info=True)
                QMessageBox.critical(self, "❌ Erro", f"Não foi possível redefinir: {exc}")

    def _do_change_pwd_flow(self) -> None:
        """Usuário validou as 2 senhas novas e clicou em Trocar / Enter (must_change_pwd)."""
        self.lbl_erro_chg.setText("")
        p1 = self.ed_chg_pwd1.text()
        p2 = self.ed_chg_pwd2.text()
        if len(p1) < 6:
            self.lbl_erro_chg.setText("Nova senha deve ter pelo menos 6 caracteres.")
            self._mark_field_error(self.ed_chg_pwd1)
            self.ed_chg_pwd1.setFocus()
            return
        if p1 != p2:
            self.lbl_erro_chg.setText("As senhas não conferem.")
            self._mark_field_error(self.ed_chg_pwd1, self.ed_chg_pwd2)
            self.ed_chg_pwd2.setFocus()
            return
        user = getattr(self, "_pending_authenticated_user", None)
        if user is None:
            self.lbl_erro_chg.setText("Sessão inválida — volte e faça login novamente.")
            self._to_login()
            return
        store = get_user_store()
        try:
            # Troca a senha e reseta must_change_pwd (API já faz isso automaticamente)
            store.change_password(user.user_id, old_raw=None, new_raw=p1)
            # Re-autentica agora com a senha nova para garantir session em dia
            user2 = store.login(user.username, p1)
        except Exception as exc:
            self.lbl_erro_chg.setText(str(exc))
            self._mark_field_error(self.ed_chg_pwd1, self.ed_chg_pwd2)
            return
        # Auditoria
        try:
            get_db().audit_log(
                "PASSWORD_MANDATORY_CHANGE", "AUTH", status="OK",
                details={"username": user2.username, "must_change_prev": True}
            )
        except Exception:
            pass
        # Finaliza: emite o login_sucesso para MainWindow abrir o app
        self._pending_authenticated_user = None
        self.login_sucesso.emit(user2)

    def _do_cadastro(self) -> None:
        self.lbl_erro_cad.setStyleSheet(f"color:{_DANG};font-size:13px;font-weight:600;")
        self.lbl_erro_cad.setText("")
        self._clear_field_error(self.ed_cad_nome); self._clear_field_error(self.ed_cad_user)
        self._clear_field_error(self.ed_cad_email); self._clear_field_error(self.ed_cad_pwd)
        self._clear_field_error(self.ed_cad_pwd2)
        QApplication.processEvents()

        nome  = self.ed_cad_nome.text().strip()
        user  = self.ed_cad_user.text().strip()
        email = self.ed_cad_email.text().strip() or None
        # 🔴 CRITICAL FIX: Senhas sem .strip() davam "senhas diferentes" se o usuário colasse com espaço/tab/enter no final!
        pwd   = self.ed_cad_pwd.text().rstrip("\r\n\t ")
        pwd2  = self.ed_cad_pwd2.text().rstrip("\r\n\t ")
        log.info(
            "[DEBUG _do_cadastro] clique='Criar conta e entrar' | "
            "user=%r len=%d | nome=%r len=%d | email=%r | pwd_len=%d pwd2_len=%d match=%s",
            user, len(user), nome, len(nome), email, len(pwd), len(pwd2), pwd == pwd2
        )
        # Se apos o strip as senhas ficaram diferentes, ATUALIZA os campos TEXTUAIS para o usuario ver o que foi comparado!
        if pwd != self.ed_cad_pwd.text() or pwd2 != self.ed_cad_pwd2.text():
            # Auto-trim (se o usuario colou com \n ou espacos, tira para nao confundir)
            if pwd != self.ed_cad_pwd.text():
                self.ed_cad_pwd.setText(pwd)
            if pwd2 != self.ed_cad_pwd2.text():
                self.ed_cad_pwd2.setText(pwd2)

        if len(nome) < 3:
            self.lbl_erro_cad.setText("❌ Nome completo muito curto — informe pelo menos 3 letras.")
            self._mark_field_error(self.ed_cad_nome)
            self.ed_cad_nome.setFocus()
            self.ed_cad_nome.selectAll()
            return
        if len(user) < 3 or not user.replace("_", "").isalnum():
            self.lbl_erro_cad.setText(
                "❌ Username inválido — use 3+ letras/números (apenas underscores e alfanuméricos)."
            )
            self._mark_field_error(self.ed_cad_user)
            self.ed_cad_user.setFocus()
            self.ed_cad_user.selectAll()
            return
        if email is not None and not _EMAIL_RE.match(email):
            self.lbl_erro_cad.setText("❌ E-mail inválido — deixe em branco se não souber, ou corrija.")
            self._mark_field_error(self.ed_cad_email)
            self.ed_cad_email.setFocus()
            self.ed_cad_email.selectAll()
            return
        if len(pwd) < 6:
            self.lbl_erro_cad.setText("❌ Senha muito curta — mínimo 6 caracteres.")
            self._mark_field_error(self.ed_cad_pwd)
            self.ed_cad_pwd.setFocus()
            self.ed_cad_pwd.selectAll()
            return
        if pwd != pwd2:
            self.lbl_erro_cad.setText("❌ Senhas não conferem — digite-as novamente com cuidado.")
            self._mark_field_error(self.ed_cad_pwd, self.ed_cad_pwd2)
            self.ed_cad_pwd2.setFocus()
            self.ed_cad_pwd2.selectAll()
            return

        store = get_user_store()
        db = self._safe_get_db()

        # Checks rápidos ANTES de tentar create_user (feedback imediato por campo)
        if store.get_by_username(user):
            self.lbl_erro_cad.setText(
                f"❌ Username '{user}' JÁ EXISTE — escolha outro diferente. "
                "Ou volte e clique em Entrar usando o username/senha dele."
            )
            self._mark_field_error(self.ed_cad_user)
            self.ed_cad_user.setFocus()
            self.ed_cad_user.selectAll()
            return
        if email is not None and store.get_by_email(email) is not None:
            self.lbl_erro_cad.setText(
                f"❌ E-mail '{email}' JÁ ESTÁ CADASTRADO — use outro, "
                "ou faça login com o username da conta existente."
            )
            self._mark_field_error(self.ed_cad_email)
            self.ed_cad_email.setFocus()
            self.ed_cad_email.selectAll()
            return

        primeiro_admin = self._modo_cadastro_admin or not store.has_any_admin()
        role_desejada = UserRole.ADMIN if primeiro_admin else UserRole.USER
        try:
            u = store.create_user(
                username=user, password_raw=pwd, full_name=nome,
                email=email, role=role_desejada, must_change_pwd=False,
            )
        except Exception as exc:
            log.error("Falha create_user tela cadastro: %s", exc, exc_info=True)
            self._safe_audit_log(
                db, "USER_CREATE", "AUTH", status="FAIL",
                details={"username": user, "email": email, "error": str(exc)}
            )
            self.lbl_erro_cad.setText(f"❌ Erro ao criar usuário: {exc}")
            self._mark_field_error(
                self.ed_cad_nome, self.ed_cad_user,
                self.ed_cad_email, self.ed_cad_pwd, self.ed_cad_pwd2,
            )
            return

        self._safe_audit_log(
            db, "USER_CREATE", "AUTH", status="OK",
            details={"username": u.username, "role": u.role.value,
                     "first_admin": primeiro_admin}
        )
        # Reautentica GARANTIDO (nao depende de try/except fallback!)
        try:
            u2 = store.login(user, pwd)
        except Exception as exc_log:
            # Cadastrou mas nao logou? nao emite falha vazia — reseta senha forcado para a mesma
            log.warning("Cadastro OK mas login pos-cadastro falhou '%s': %s -> forçando reset", user, exc_log)
            try:
                store.force_reset_password(u.user_id, pwd, must_change_pwd=False, to_role=role_desejada)
                u2 = store.login(user, pwd)
            except Exception as exc2:
                self._safe_audit_log(
                    db, "USER_CREATE_REAUTH_FAIL", "AUTH", status="FAIL",
                    details={"username": user, "error": str(exc2)}
                )
                self.lbl_erro_cad.setText(
                    "❌ Usuário foi criado, mas login automático falhou. "
                    "Volte e digite manualmente username/senha na tela Entrar."
                )
                self._mark_field_error(self.ed_cad_pwd, self.ed_cad_pwd2)
                QTimer.singleShot(1500, self._to_login)
                return
        # Tudo certo: mostra feedback verde 1 segundo e depois EMITE O LOGIN_SUCESSO (não volta mais!)
        self.lbl_erro_cad.setStyleSheet(f"color:#10B981;font-size:13px;font-weight:700;")
        self.lbl_erro_cad.setText(
            f"✅ Conta criada com sucesso! Role: <b>{u2.role.value}</b>. "
            "Entrando no GSPHosp em 1 segundo..."
        )
        self.btn_cad.setEnabled(False)
        QTimer.singleShot(900, lambda: self.login_sucesso.emit(u2))

    # ----------------------------- public (MainWindow) -----------------------------
    def reset_for_logout(self) -> None:
        """Chamado quando usuário faz logout."""
        self.ed_login_user.clear(); self.ed_login_pwd.clear()
        self.ed_cad_nome.clear(); self.ed_cad_user.clear()
        self.ed_cad_email.clear(); self.ed_cad_pwd.clear(); self.ed_cad_pwd2.clear()
        self.ed_chg_pwd1.clear(); self.ed_chg_pwd2.clear()
        self.lbl_erro_login.setText(""); self.lbl_erro_cad.setText(""); self.lbl_erro_chg.setText("")
        self._pending_authenticated_user = None
        store = get_user_store()
        self._modo_cadastro_admin = store.count_users() == 0
        self._apply_mode()
