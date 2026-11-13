"""
visor.py — QWebEngineView que mostra `assets/mapa_faixas.html` e conversa com o JS.

    from papahr.ui.mapa.visor import VisorMapa
    v = VisorMapa()
    v.chamar("setFaixas", payload)          # window.gsp.setFaixas(payload) quando a página estiver pronta
    v.municipioClicado.connect(fn)          # clique num município no mapa → cod6 (str)

Chamadas feitas antes de a página carregar ficam numa fila e são executadas em ordem.
Importar este módulo ANTES de criar o QApplication (exigência do QtWebEngine).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, Qt, QUrl, Signal, Slot
from PySide6.QtWidgets import QWidget, QVBoxLayout

if QCoreApplication.instance() is None:
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
from PySide6.QtWebChannel import QWebChannel  # noqa: E402
from PySide6.QtWebEngineCore import QWebEngineSettings  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402

ASSETS = Path(__file__).resolve().parent / "assets"
PAGINA_FAIXAS = ASSETS / "mapa_faixas.html"


def limpar_json(obj):
    """NaN/inf → None, para o JSON ser válido no JS."""
    if isinstance(obj, dict):
        return {k: limpar_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [limpar_json(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


class _Ponte(QObject):
    municipio = Signal(str)
    hospital = Signal(str)

    @Slot(str)
    def municipioClicado(self, cod: str) -> None:
        self.municipio.emit(str(cod))

    @Slot(str)
    def hospitalClicado(self, cnes: str) -> None:
        self.hospital.emit(str(cnes))


class VisorMapa(QWidget):
    municipioClicado = Signal(str)
    hospitalClicado = Signal(str)
    pronto = Signal()

    def __init__(self, parent: QWidget | None = None, pagina: Path = PAGINA_FAIXAS) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView(self)
        lay.addWidget(self.view)
        st = self.view.settings()
        st.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        st.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self._ponte = _Ponte(self)
        self._ponte.municipio.connect(self.municipioClicado)
        self._ponte.hospital.connect(self.hospitalClicado)
        self._canal = QWebChannel(self.view.page())
        self._canal.registerObject("ponte", self._ponte)
        self.view.page().setWebChannel(self._canal)
        self._pronto = False
        self._fila: list[str] = []
        self.view.loadFinished.connect(self._carregou)
        self.view.load(QUrl.fromLocalFile(str(pagina)))

    # ------------------------------------------------------------------
    def _carregou(self, ok: bool) -> None:
        self._pronto = bool(ok)
        if ok:
            for js in self._fila:
                self.view.page().runJavaScript(js)
            self._fila.clear()
            self.pronto.emit()

    def executar(self, js: str) -> None:
        if self._pronto:
            self.view.page().runJavaScript(js)
        else:
            self._fila.append(js)

    def chamar(self, funcao: str, *args) -> None:
        """window.gsp.<funcao>(args...) — args serializados em JSON (NaN vira null)."""
        parametros = ", ".join(json.dumps(limpar_json(a), ensure_ascii=False, allow_nan=False) for a in args)
        self.executar(f"window.gsp && window.gsp.{funcao}({parametros});")

    def avaliar(self, js: str, callback) -> None:
        self.view.page().runJavaScript(js, callback)
