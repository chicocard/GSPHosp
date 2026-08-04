"""Widgets customizados para UI do PaPAHR."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class CardWidget(QFrame):
    """Card com sombra e cantos arredondados."""

    def __init__(self, parent: Optional[QWidget] = None, *, elevated: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("card" if not elevated else "kpiCard")
        self.setProperty("class", "card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(8)
        if elevated:
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(24)
            shadow.setOffset(0, 4)
            shadow.setColor(QColor(0, 0, 0, 120))
            self.setGraphicsEffect(shadow)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    def add_widget(self, widget: QWidget) -> None:
        self._layout.addWidget(widget)

    def layout_(self) -> QVBoxLayout:
        return self._layout


class KpiCard(QFrame):
    """Card de KPI com valor, label e delta opcional."""

    def __init__(self,
                 label: str,
                 value: str | int | float = "",
                 delta: str = "",
                 *,
                 accent: str = "#4FD1C5",
                 icon: str | None = None,
                 format: str | None = None,  # noqa: A002 - keep public API friendly
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("kpiCard")
        self._accent = accent
        self._fmt = format  # Python f-string-style format, e.g. "{:.1f} %"
        self._icon = icon
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(4)

        label_text = (f"{icon}  " if icon else "") + label.upper()
        self.lbl_label = QLabel(label_text)
        self.lbl_label.setObjectName("kpiLabel")
        self.lbl_label.setStyleSheet(f"color: {accent}99;")

        self.lbl_value = QLabel(self._format(value))
        self.lbl_value.setObjectName("kpiValue")

        self.lbl_delta = QLabel(delta)
        self.lbl_delta.setStyleSheet("color:#8FA6A3; font-size:9pt;")

        layout.addWidget(self.lbl_label)
        layout.addWidget(self.lbl_value)
        if delta:
            layout.addWidget(self.lbl_delta)

        self.setMinimumHeight(110)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(20, 184, 166, 30))
        self.setGraphicsEffect(shadow)

    def _format(self, v: object) -> str:
        if v is None:
            return "—"
        if isinstance(v, (int, float)):
            if self._fmt:
                try:
                    return self._fmt.format(v)
                except Exception:
                    pass
            if isinstance(v, float):
                return f"{v:,.2f}".replace(",", " ").replace(".", ",")
            return f"{v:,}".replace(",", ".")
        return str(v)

    def update_values(self, value: str | int | float, delta: str = "") -> None:
        self._animate_value(self.lbl_value.text(), self._format(value))
        if delta:
            self.lbl_delta.setText(delta)

    def set_value(self, value: str | int | float) -> None:
        self.lbl_value.setText(self._format(value))

    def _animate_value(self, old: str, new: str) -> None:
        self.lbl_value.setText(new)


class ToggleSwitch(QPushButton):
    """Toggle estilo Material."""

    toggled_state = Signal(bool)

    def __init__(self, checked: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(56, 30)
        self.setCursor(Qt.PointingHandCursor)
        self._checked = checked
        self.clicked.connect(self._toggle)

    def _toggle(self) -> None:
        self._checked = not self._checked
        self.toggled_state.emit(self._checked)
        self.update()

    def is_checked(self) -> bool:
        return self._checked

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bg = QColor("#14B8A6") if self._checked else QColor("#2A4649")
        painter.setBrush(bg)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(0, 0, self.width(), self.height(), 15, 15)
        painter.setBrush(QColor("#FFFFFF"))
        offset = self.width() - 26 if self._checked else 2
        painter.drawEllipse(offset, 2, 26, 26)
        painter.end()


class AnimatedProgress(QProgressBar):
    """Barra de progresso com animação suave."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setRange(0, 100)
        self.setValue(0)
        self.setTextVisible(True)
        self.setFormat("%p%")
        self._anim = QPropertyAnimation(self, b"value")
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.setDuration(350)

    def set_value_smooth(self, value: int) -> None:
        value = max(0, min(100, value))
        self._anim.stop()
        self._anim.setStartValue(self.value())
        self._anim.setEndValue(value)
        self._anim.start()


class StatusBadge(QLabel):
    """Badge de status com cor."""

    STATUS_COLORS = {
        "OK": "#10B981", "INFO": "#3B82F6",
        "WARN": "#F59E0B", "ERR": "#EF4444", "LOADING": "#8B5CF6",
    }

    def __init__(self, text: str = "OK", status: str = "OK",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.set_status(text, status)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(24)

    def set_status(self, text: str, status: str = "INFO") -> None:
        color = self.STATUS_COLORS.get(status.upper(), "#3B82F6")
        self.setText(text)
        self.setStyleSheet(
            f"background: {color}26; color: {color}; "
            "padding: 4px 12px; border-radius: 12px; font-weight: 600;"
        )


class StreamViewer(QTextEdit):
    """Exibe stream de texto do LLM (streaming)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Respostas do assistente aparecerão aqui...")
        self.setStyleSheet(
            "background: #0F1A1C; border: 1px solid #1F3A3D; "
            "border-radius: 10px; padding: 12px; color: #E6F1EF; "
            "font-family: 'Segoe UI', Inter, sans-serif; line-height: 1.6;"
        )
        self._buffer = ""

    def reset(self) -> None:
        self._buffer = ""
        self.clear()


from papahr.ui.widgets.concentric_map import ConcentricRingsMap, HospitalMapPoint, RingBucket

__all__ = [
    "AnimatedProgress",
    "CardWidget",
    "ConcentricRingsMap",
    "HospitalMapPoint",
    "KpiCard",
    "RingBucket",
    "StatusBadge",
    "StreamViewer",
    "ToggleSwitch",
]
