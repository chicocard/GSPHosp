"""Estilo compacto das telas (compacto.qss) e pequenos widgets de apoio ao layout.

    from papahr.ui.atratividade.estilo import aplicar_compacto, RotuloElidido, botao_pequeno
    aplicar_compacto(widget_raiz)      # a folha vale para o widget e todos os filhos
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QWidget

_QSS = Path(__file__).with_name("compacto.qss")


@lru_cache(maxsize=1)
def qss_compacto() -> str:
    try:
        # {{DIR}} → pasta desta folha (imagens do QSS precisam de caminho; o Qt não aceita data: URI)
        return _QSS.read_text(encoding="utf-8").replace("{{DIR}}", _QSS.parent.as_posix())
    except OSError:
        return ""


def aplicar_compacto(w: QWidget) -> None:
    """Aplica a folha compacta ao widget (e filhos). Vale dentro do app, cuja folha global é diferente."""
    q = qss_compacto()
    if q:
        w.setStyleSheet(q)


class RotuloElidido(QLabel):
    """QLabel de UMA linha que corta com '…' o que não cabe; o texto inteiro vai para a dica."""

    def __init__(self, texto: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._txt = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setTexto(texto)

    def setTexto(self, txt: str) -> None:
        self._txt = txt or ""
        self.setToolTip(self._txt)
        self._reelidir()

    def texto(self) -> str:
        return self._txt

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._reelidir()

    def _reelidir(self) -> None:
        largura = max(20, self.width() - 6)
        super().setText(self.fontMetrics().elidedText(self._txt, Qt.TextElideMode.ElideRight, largura))


def botao_pequeno(texto: str, dica: str = "", primario: bool = False, marcavel: bool = False) -> QPushButton:
    b = QPushButton(texto)
    if dica:
        b.setToolTip(dica)
    if primario:
        b.setProperty("primario", True)
    if marcavel:
        b.setCheckable(True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def rotulo_chave(texto: str) -> QLabel:
    lab = QLabel(texto); lab.setProperty("chave", True)
    return lab
