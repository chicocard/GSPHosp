"""
grafico.py — gráficos pyqtgraph das telas de atratividade e de taxas municipais.

GraficoDecaimento : taxa × distância por tipo de internação (pontos observados por faixa
                    + curva ajustada), escalas lineares ou log, legenda, leitura ao passar o mouse.
GraficoHistograma : histograma das TIH municipais com média, mediana e município destacado.

Cores: paleta categórica validada (método dataviz) em versão clara e escura; o tema é
fixo em 'claro' (folha compacta), salvo propriedade tema='escuro' no widget; funciona com o QSS
claro (theme.qss) e com temas escuros.
"""
from __future__ import annotations

import math
from typing import Sequence

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

pg.setConfigOptions(antialias=True)

CAT_CLARO = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
CAT_ESCURO = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
SEQ_BARRA = {"claro": "#3987e5", "escuro": "#5598e7"}
INK = {"claro": "#0b0b0b", "escuro": "#f0efec"}
INK2 = {"claro": "#52514e", "escuro": "#c3c2b7"}
GRADE = {"claro": "#e1e0d9", "escuro": "#3a3a38"}
DESTAQUE = "#eb6834"


SURFACE = {"claro": "#fcfcfb", "escuro": "#1e1e1c"}


def tema_de(widget: QWidget) -> str:
    """Tema das cores de texto/traço. As telas usam a folha compacta (sempre clara), por isso o padrão é
    'claro' independentemente da paleta do Windows — a paleta do sistema em modo escuro fazia o texto
    sair branco sobre fundo claro. Um widget pode pedir o tema escuro com setProperty("tema", "escuro")."""
    w = widget
    while w is not None:
        t = w.property("tema")
        if t in ("claro", "escuro"):
            return str(t)
        w = w.parentWidget()
    return "claro"


def cor_categoria(i: int, tema: str) -> str:
    pal = CAT_ESCURO if tema == "escuro" else CAT_CLARO
    return pal[i % len(pal)]


def _estilizar(pw: pg.PlotWidget, tema: str, xlabel: str, ylabel: str) -> None:
    pw.setBackground(SURFACE[tema])
    pi = pw.getPlotItem()
    pi.showGrid(x=True, y=True, alpha=0.18)
    f = QFont(); f.setPointSize(8)
    for nome in ("bottom", "left"):
        ax = pi.getAxis(nome)
        ax.setPen(pg.mkPen(GRADE[tema]))
        ax.setTextPen(pg.mkPen(INK2[tema]))
        ax.setStyle(tickFont=f)
    pi.setLabel("bottom", xlabel, color=INK2[tema], **{"font-size": "8pt"})
    pi.setLabel("left", ylabel, color=INK2[tema], **{"font-size": "8pt"})
    pi.getViewBox().setDefaultPadding(0.04)
    pi.layout.setContentsMargins(2, 2, 6, 2)


class _Base(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.tema = tema_de(self)
        self.pw = pg.PlotWidget(self)
        self.pw.setMinimumHeight(120)
        self.leitura = QLabel(" ")
        self.leitura.setProperty("nota", True)
        self.leitura.setStyleSheet(f"color:{INK2[self.tema]};")
        self.pw.scene().sigMouseMoved.connect(self._mouse)

    def _mouse(self, pos) -> None:  # pragma: no cover - interação
        pass


class GraficoDecaimento(_Base):
    """Taxa por faixa de distância, por tipo de internação."""

    def __init__(self, parent=None, unidade: str = "internações por 10 mil hab./ano") -> None:
        super().__init__(parent)
        self.unidade = unidade
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(4)
        barra = QHBoxLayout(); barra.setContentsMargins(0, 0, 0, 0)
        self.chk_logx = QCheckBox("distância em log"); self.chk_logx.setChecked(True)
        self.chk_logy = QCheckBox("taxa em log"); self.chk_logy.setChecked(True)
        self.chk_ajuste = QCheckBox("mostrar curvas ajustadas"); self.chk_ajuste.setChecked(True)
        for c in (self.chk_logx, self.chk_logy, self.chk_ajuste):
            c.toggled.connect(lambda _v: self._redesenhar()); barra.addWidget(c)
        barra.addStretch(1); barra.addWidget(self.leitura)
        lay.addLayout(barra); lay.addWidget(self.pw, 1)
        # legenda em HTML abaixo do gráfico (dentro do gráfico ela cobria as curvas)
        self.legenda = QLabel(" "); self.legenda.setWordWrap(True); self.legenda.setProperty("nota", True)
        self.legenda.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.legenda)
        _estilizar(self.pw, self.tema, "distância ao hospital (km)", self.unidade)
        self._series: list[dict] = []
        self._pontos: list[tuple[float, float, str, str]] = []

    def desenhar(self, series: Sequence[dict]) -> None:
        """series: [{label, x:[km], y:[taxa], n:[int], rotulos:[str], modelo, T0, beta, lambda, cor, principal}]"""
        self._series = list(series)
        self._redesenhar()

    def _redesenhar(self) -> None:
        pi = self.pw.getPlotItem()
        pi.clear()
        logx, logy = self.chk_logx.isChecked(), self.chk_logy.isChecked()
        pi.setLogMode(x=logx, y=logy)
        self._pontos = []
        ax = pi.getAxis("bottom")
        if logx:
            xmax_all = max((max(s["x"]) for s in self._series if s["x"]), default=500.0)
            vals = [v for v in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000) if v <= xmax_all * 1.3]
            ax.setTicks([[(math.log10(v), f"{v}") for v in vals]])
        else:
            ax.setTicks(None)
        if not self._series:
            self.legenda.setText(" ")
            return
        chips = []
        for s in self._series:
            lab = s["label"] if len(s["label"]) <= 44 else s["label"][:43] + "…"
            if s.get("principal"):
                lab = f"<b>{lab}</b>"
            chips.append(f'<span style="color:{s["cor"]}">&#9679;</span>&nbsp;{lab}')
        self.legenda.setText("&nbsp;&nbsp;&nbsp;".join(chips) + ("&nbsp;&nbsp;&nbsp;<span style='color:" + INK2[self.tema] + "'>— — ajuste</span>" if self.chk_ajuste.isChecked() else ""))
        xmax = max((max(s["x"]) for s in self._series if s["x"]), default=100.0)
        for s in self._series:
            cor = s["cor"]; princ = bool(s.get("principal"))
            xs, ys = [], []
            for x, y, n, rot in zip(s["x"], s["y"], s.get("n", []), s.get("rotulos", [])):
                if y is None or (logy and y <= 0) or x is None or (logx and x <= 0):
                    continue
                xs.append(x); ys.append(y); self._pontos.append((x, y, s["label"], f"{rot}: {n:,} internações".replace(",", ".")))
            if not xs:
                continue
            largura = 3 if princ else 2
            pi.plot(xs, ys, pen=pg.mkPen(cor, width=largura), symbol="o", symbolSize=9 if princ else 8,
                    symbolBrush=pg.mkBrush(cor), symbolPen=pg.mkPen(self.pw.palette().color(self.pw.backgroundRole()), width=1.5))
            if self.chk_ajuste.isChecked() and s.get("modelo") and s.get("T0"):
                xf = _grade_x(max(1.0, min(xs) * 0.6), xmax * 1.05, logx)
                yf = [_modelo(s, x) for x in xf]
                par = [(x, y) for x, y in zip(xf, yf) if y is not None and y > 0]
                if par:
                    pi.plot([p[0] for p in par], [p[1] for p in par], pen=pg.mkPen(cor, width=1.2, style=Qt.PenStyle.DashLine))

    def _mouse(self, pos) -> None:  # pragma: no cover - interação
        if not self._pontos:
            return
        vb = self.pw.getPlotItem().getViewBox()
        if not vb.sceneBoundingRect().contains(pos):
            return
        p = vb.mapSceneToView(pos)
        logx, logy = self.chk_logx.isChecked(), self.chk_logy.isChecked()
        melhor, dist = None, float("inf")
        rx = vb.viewRange()[0]; ry = vb.viewRange()[1]
        sx = (rx[1] - rx[0]) or 1; sy = (ry[1] - ry[0]) or 1
        for x, y, lab, det in self._pontos:
            vx = math.log10(x) if logx else x; vy = math.log10(y) if logy else y
            d = ((vx - p.x()) / sx) ** 2 + ((vy - p.y()) / sy) ** 2
            if d < dist:
                dist, melhor = d, (x, y, lab, det)
        if melhor and dist < 0.0016:
            x, y, lab, det = melhor
            self.leitura.setText(f"{lab} · {det} · {x:,.0f} km → {y:,.2f}".replace(",", "§").replace(".", ",").replace("§", "."))
        else:
            self.leitura.setText(" ")


def _grade_x(a: float, b: float, log: bool, n: int = 80) -> list[float]:
    if log:
        la, lb = math.log10(a), math.log10(b)
        return [10 ** (la + (lb - la) * i / (n - 1)) for i in range(n)]
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def _modelo(s: dict, x: float) -> float | None:
    t0 = s.get("T0"); d0 = s.get("d0", 10.0)
    if not t0:
        return None
    if s.get("modelo") == "potencia" and s.get("beta") is not None:
        return t0 * (max(x, 0.5) / d0) ** (-s["beta"])
    if s.get("modelo") == "exponencial" and s.get("lambda") is not None:
        return t0 * math.exp(-s["lambda"] * (x - d0))
    return None


class GraficoHistograma(_Base):
    """Histograma das taxas municipais."""

    def __init__(self, parent=None, unidade: str = "internações por 1.000 hab./ano") -> None:
        super().__init__(parent)
        self.unidade = unidade
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(4)
        lay.addWidget(self.pw, 1); lay.addWidget(self.leitura)
        _estilizar(self.pw, self.tema, self.unidade, "nº de municípios")
        self._bordas: list[float] = []; self._cont: list[int] = []; self._cods: list[list[str]] = []
        self._nomes: dict[str, str] = {}

    def desenhar(self, hist: dict, *, media: float | None, mediana: float | None, taxa_uf: float | None,
                 destaque: tuple[str, float] | None = None, nomes: dict[str, str] | None = None) -> None:
        pi = self.pw.getPlotItem(); pi.clear()
        self._bordas = list(hist.get("bordas") or []); self._cont = list(hist.get("contagens") or [])
        self._cods = list(hist.get("cod_por_classe") or []); self._nomes = nomes or {}
        if not self._bordas:
            return
        larg = float(hist["largura"])
        centros = [(a + b) / 2 for a, b in zip(self._bordas[:-1], self._bordas[1:])]
        cor = SEQ_BARRA[self.tema]
        pi.addItem(pg.BarGraphItem(x=centros, width=larg * 0.9, height=self._cont, brush=pg.mkBrush(cor), pen=pg.mkPen(None)))
        ink = INK[self.tema]
        fundo = pg.mkBrush(SURFACE[self.tema])

        def linha(v, texto, estilo, cor_l, pos):
            if v is None:
                return
            ln = pg.InfiniteLine(pos=v, angle=90, pen=pg.mkPen(cor_l, width=1.5, style=estilo),
                                 label=texto, labelOpts={"position": pos, "color": cor_l, "fill": fundo, "movable": False})
            pi.addItem(ln)
        # rótulos em alturas diferentes para não se sobreporem quando os valores são próximos
        linha(mediana, f"mediana {mediana:,.1f}".replace(".", ","), Qt.PenStyle.SolidLine, ink, 0.93)
        linha(media, f"média {media:,.1f}".replace(".", ","), Qt.PenStyle.DashLine, INK2[self.tema], 0.80)
        linha(taxa_uf, f"UF {taxa_uf:,.1f}".replace(".", ","), Qt.PenStyle.DotLine, INK2[self.tema], 0.67)
        if destaque and destaque[1] is not None:
            v = float(destaque[1])
            x = min(max(v, self._bordas[0]), self._bordas[-1])
            fora = v > self._bordas[-1]
            i = min(len(self._cont) - 1, max(0, int((x - self._bordas[0]) / larg)))
            y = self._cont[i] + max(1.0, max(self._cont) * 0.06)
            sc = pg.ScatterPlotItem([x], [y], symbol="t", size=14, brush=pg.mkBrush(DESTAQUE), pen=pg.mkPen("#ffffff", width=1.5))
            pi.addItem(sc)
            txt = f"{destaque[0]} ({v:,.1f}{' →' if fora else ''})".replace(".", ",")
            direita = (x - self._bordas[0]) > 0.7 * (self._bordas[-1] - self._bordas[0])
            t = pg.TextItem(txt, color=DESTAQUE, anchor=(1.0 if direita else 0.0, 1.2)); t.setPos(x, y); pi.addItem(t)
        pi.setLogMode(x=False, y=False)
        pi.getViewBox().setLimits(xMin=self._bordas[0] - larg, xMax=self._bordas[-1] + larg, yMin=0)
        pi.getViewBox().autoRange()

    def _mouse(self, pos) -> None:  # pragma: no cover - interação
        if not self._bordas:
            return
        vb = self.pw.getPlotItem().getViewBox()
        if not vb.sceneBoundingRect().contains(pos):
            return
        x = vb.mapSceneToView(pos).x()
        larg = self._bordas[1] - self._bordas[0]
        i = int((x - self._bordas[0]) / larg)
        if 0 <= i < len(self._cont):
            cods = self._cods[i] if i < len(self._cods) else []
            nomes = ", ".join(self._nomes.get(c, c) for c in cods[:6]) + (" …" if len(cods) > 6 else "")
            self.leitura.setText(f"{self._bordas[i]:,.1f} a {self._bordas[i + 1]:,.1f}: {self._cont[i]} municípios · {nomes}".replace(".", ","))
        else:
            self.leitura.setText(" ")

