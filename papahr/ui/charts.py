"""Camada de gráficos do Dashboard (pyqtgraph) — um só estilo para todas as abas.

Regras que corrigem os problemas históricos ("barras larguíssimas", "nada se lê"):

* Largura de barra fixa em 0,62 da unidade categórica. Com poucas categorias o
  eixo é ALARGADO (padding) em vez de a barra crescer até ocupar o gráfico.
* Mais de 8 categorias, ou rótulos longos (nome de hospital, condição ICSAP),
  viram barras HORIZONTAIS com o rótulo completo no eixo esquerdo.
* Toda barra recebe o valor escrito na ponta.
* Faixas de eixo travadas (sem autoRange que colapsa barras de altura zero) e
  mouse desabilitado: um dashboard não deve dar zoom por acidente.
* Linhas de referência (benchmark, média, mediana) sempre tracejadas e rotuladas.
"""
from __future__ import annotations

import math
from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPen

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover
    pg = None  # type: ignore[assignment]

# Paleta (idêntica ao QSS material_dark_teal)
BG = "#162023"
PANEL = "#121C1E"
TXT = "#E6FFFA"
DIM = "#94A3B8"
GRID = "#233539"
PRI = "#14B8A6"
PRI_DARK = "#0D9488"
WARN = "#F59E0B"
DANG = "#EF4444"
OK = "#22C55E"
INFO = "#60A5FA"
PURPLE = "#A78BFA"
NEUTRAL = "#475569"

_FONT_TICK = QFont("Segoe UI", 9)
_FONT_VALUE = QFont("Segoe UI", 8)
_FONT_VALUE.setBold(True)


def pen(color: str, width: float = 1.0, style=Qt.SolidLine) -> QPen:
    p = QPen(QColor(color))
    p.setWidthF(width)
    p.setStyle(style)
    p.setCosmetic(True)
    return p


def brush(color: str | QColor) -> QBrush:
    return QBrush(QColor(color) if isinstance(color, str) else color)


def lerp(a: str, b: str, t: float) -> QColor:
    ca, cb = QColor(a), QColor(b)
    t = max(0.0, min(1.0, t))
    return QColor(
        int(ca.red() + (cb.red() - ca.red()) * t),
        int(ca.green() + (cb.green() - ca.green()) * t),
        int(ca.blue() + (cb.blue() - ca.blue()) * t),
    )


def color_for_pct(pct: float) -> QColor:
    """Semáforo contínuo (ICSAP, readmissão): verde → teal → amarelo → vermelho."""
    pct = min(100.0, max(0.0, float(pct)))
    if pct < 15:
        return lerp(OK, PRI, pct / 15.0)
    if pct < 30:
        return lerp(PRI, WARN, (pct - 15) / 15.0)
    return lerp(WARN, DANG, min(1.0, (pct - 30) / 30.0))


def color_for_ratio(v: float) -> QColor:
    """Para razões O/E: 1,0 = benchmark. Abaixo = verde, acima = vermelho."""
    if v is None or not math.isfinite(float(v)):
        return QColor(NEUTRAL)
    v = float(v)
    if v <= 1.0:
        return lerp(OK, PRI, max(0.0, v))
    return lerp(WARN, DANG, min(1.0, (v - 1.0) / 1.0))


def short(label: object, n: int = 30) -> str:
    s = str(label if label is not None else "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _finite(values: Sequence[object]) -> list[float]:
    out: list[float] = []
    for v in values:
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            f = 0.0
        out.append(f if math.isfinite(f) else 0.0)
    return out



def set_title(plt, html: str | None) -> None:
    """Título que quebra linha e não força a largura mínima do gráfico.

    O LabelItem do pyqtgraph fixa minimumWidth = largura do texto; um título longo
    fazia o PlotItem ficar mais largo que o widget e o gráfico aparecia cortado.
    """
    pi = plt.getPlotItem()
    pi.setTitle(html if html is not None else "")
    relayout(plt)


def relayout(plt) -> None:
    """Reajusta título (quebra de linha) e cena ao tamanho ATUAL do widget."""
    try:
        from PySide6.QtCore import QRectF
        pi = plt.getPlotItem()
        lbl = pi.titleLabel
        # O título ocupa a COLUNA do ViewBox: sua largura tem de caber ao lado do eixo esquerdo
        try:
            left_w = float(pi.getAxis("left").width() or 0.0)
        except Exception:  # noqa: BLE001
            left_w = 0.0
        w = max(120.0, plt.width() - left_w - 48.0)
        lbl.item.setTextWidth(w)
        h = float(lbl.item.boundingRect().height())
        # LabelItem.updateMin() reimpõe minimumWidth = largura do texto a cada resize/setText.
        # Substituímos por um no-op neste rótulo e declaramos nós os sizeHints:
        # mínimo 0 de largura (pode encolher), altura exata do texto quebrado.
        lbl.updateMin = lambda *a, **k: None  # type: ignore[method-assign]
        try:
            from PySide6.QtCore import Qt as _Qt
            lbl._sizeHint = {
                _Qt.MinimumSize: (0.0, h),
                _Qt.PreferredSize: (w, h),
                _Qt.MaximumSize: (-1.0, h + 2),
                _Qt.MinimumDescent: (0.0, 0.0),
            }
        except Exception:  # noqa: BLE001
            pass
        lbl.setMinimumWidth(0)
        lbl.setMaximumWidth(16777215)
        lbl.setMinimumHeight(h); lbl.setMaximumHeight(h + 2)
        lbl.updateGeometry()
        if plt.width() > 10 and plt.height() > 10:
            # 1) invalida o mínimo em cache do PlotItem (o título antigo deixava 800+ px)
            pi.layout.invalidate()
            pi.updateGeometry()
            # 2) só então aplica a geometria do widget à cena (setGeometry respeita o mínimo)
            rect = QRectF(0, 0, plt.width(), plt.height())
            plt.setRange(rect, padding=0, disableAutoPixel=False)
            try:
                plt.centralWidget.setGeometry(rect)
                pi.layout.activate()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass

# ---------------------------------------------------------------------------
# Estilo base
# ---------------------------------------------------------------------------
def style_plot(plt, title: str | None = None, *, xlabel: str = "", ylabel: str = "",
               grid_x: bool = False, grid_y: bool = True) -> None:
    if pg is None:
        return
    pi = plt.getPlotItem()
    plt.setBackground(BG)
    for name in ("left", "bottom", "right", "top"):
        ax = pi.getAxis(name)
        ax.setPen(pen(GRID))
        ax.setTextPen(DIM)
        ax.setTickFont(_FONT_TICK)
    pi.showGrid(x=grid_x, y=grid_y, alpha=0.18)
    if title is not None:
        set_title(plt, f'<span style="color:{TXT}; font-size:10pt; font-weight:600">{title}</span>')
    pi.setLabel("bottom", xlabel, color=DIM)
    pi.setLabel("left", ylabel, color=DIM)
    pi.hideButtons()
    pi.setMenuEnabled(False)
    vb = pi.getViewBox()
    vb.setMouseEnabled(x=False, y=False)
    vb.setBackgroundColor(BG)
    pi.layout.setContentsMargins(6, 6, 14, 6)


def clear(plt) -> None:
    """Limpa itens, legenda, eixo direito e inversões — deixa o plot neutro."""
    if pg is None:
        return
    pi = plt.getPlotItem()
    # ViewBox secundário (eixo direito) criado por bars_with_line
    vb2 = getattr(plt, "_vb2", None)
    if vb2 is not None:
        try:
            pi.vb.sigResized.disconnect(plt._vb2_upd)
        except Exception:  # noqa: BLE001
            pass
        try:
            pi.scene().removeItem(vb2)
        except Exception:  # noqa: BLE001
            pass
        plt._vb2 = None
    plt.clear()
    if pi.legend is not None:
        try:
            pi.legend.scene().removeItem(pi.legend)
        except Exception:  # noqa: BLE001
            pass
        pi.legend = None
    pi.hideAxis("right")
    pi.getAxis("left").setTicks(None)
    pi.getAxis("bottom").setTicks(None)
    pi.getAxis("left").setWidth(None)
    pi.getAxis("bottom").setHeight(None)
    vb = pi.getViewBox()
    vb.invertY(False)
    vb.setLimits(xMin=None, xMax=None, yMin=None, yMax=None)
    vb.enableAutoRange(enable=False)
    try:
        plt.setMinimumHeight(220)
    except Exception:  # noqa: BLE001
        pass


def empty_message(plt, text: str = "Sem dados para este indicador") -> None:
    if pg is None:
        return
    clear(plt)
    style_plot(plt, "")
    pi = plt.getPlotItem()
    pi.hideAxis("left"); pi.hideAxis("bottom")
    t = pg.TextItem(text, color=DIM, anchor=(0.5, 0.5))
    t.setFont(QFont("Segoe UI", 10))
    plt.addItem(t)
    t.setPos(0.5, 0.5)
    pi.getViewBox().setRange(xRange=(0, 1), yRange=(0, 1), padding=0)


def legend_html(entries: Sequence[tuple[str, str]]) -> str:
    """Legenda como linha de texto colorida (vai no título, nunca sobre as barras)."""
    return " &nbsp;&nbsp; ".join(
        f'<span style="color:{color}; font-size:11pt">&#9632;</span> '
        f'<span style="color:{DIM}; font-size:8pt">{name}</span>' for name, color in entries
    )


def title_with_legend(title: str | None, entries: Sequence[tuple[str, str]]) -> str:
    base = f'<span style="color:{TXT}; font-size:10pt; font-weight:600">{title or ""}</span>'
    return base + "<br>" + legend_html(entries)


def add_legend(plt, entries: Sequence[tuple[str, str]]) -> None:
    if pg is None:
        return
    pi = plt.getPlotItem()
    legend = pi.addLegend(offset=(-10, 6), labelTextColor=TXT, brush=brush("#121C1ECC"), pen=pen(GRID))
    legend.setLabelTextSize("8pt")
    for name, color in entries:
        legend.addItem(pg.PlotDataItem(pen=pen(color, 8)), name)


def _fit_height(plt, n_items: int, px_per_item: int = 22, base: int = 90, floor: int = 220) -> None:
    """Gráfico horizontal cresce com o nº de categorias (a aba rola); vertical mantém o piso."""
    try:
        plt.setMinimumHeight(max(floor, base + px_per_item * n_items) if n_items else floor)
    except Exception:  # noqa: BLE001
        pass


def _value_text(text: str, anchor: tuple[float, float], x: float, y: float, plt, color: str = TXT) -> None:
    t = pg.TextItem(text, color=color, anchor=anchor)
    t.setFont(_FONT_VALUE)
    t.setPos(x, y)
    plt.addItem(t)


def _wrap(label: str, width: int = 14, max_lines: int = 3) -> str:
    words = str(label).split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) <= width:
            cur = f"{cur} {w}".strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = short(lines[-1], max(3, width - 1))
    return "\n".join(lines) if lines else str(label)


# ---------------------------------------------------------------------------
# Barras categóricas (vertical ou horizontal, decisão automática)
# ---------------------------------------------------------------------------
def bar_categorical(
    plt,
    labels: Sequence[object],
    values: Sequence[object],
    *,
    colors: Sequence[str | QColor] | None = None,
    title: str | None = None,
    value_fmt: str = "{:.1f}",
    unit: str = "",
    axis_label: str = "",
    ref: float | None = None,
    ref_label: str = "",
    horizontal: bool | None = None,
    max_items: int = 40,
    sort_desc: bool | None = None,
    highlight: int | None = None,
    clip_at: float | None = None,
) -> None:
    """Desenha barras categóricas legíveis.

    clip_at: limite do eixo; barras acima são cortadas nele e o rótulo mostra o valor
    real com "›" (um outlier de 300× não pode esmagar os outros 24 hospitais em 2 px).

    horizontal=None decide sozinho: >8 categorias ou rótulos >12 caracteres → horizontal.
    sort_desc=True ordena do maior para o menor (mantendo cores/labels alinhados).
    highlight = índice (após ordenação) da barra em destaque (contorno branco).
    max_items corta o excesso (mantém os maiores quando ordenado).
    """
    if pg is None:
        return
    clear(plt)
    style_plot(plt, title)
    labs = [str(x) for x in labels]
    vals = _finite(values)
    n = len(vals)
    if n == 0 or len(labs) != n:
        empty_message(plt)
        return
    cols: list[QColor] = [QColor(c) if isinstance(c, str) else c for c in (colors or [PRI] * n)]
    if len(cols) != n:
        cols = (cols * (n // max(1, len(cols)) + 1))[:n]
    order = list(range(n))
    if sort_desc is not None:
        order.sort(key=lambda i: vals[i], reverse=bool(sort_desc))
    if max_items and n > max_items:
        order = order[:max_items]
    labs = [labs[i] for i in order]
    vals = [vals[i] for i in order]
    cols = [cols[i] for i in order]
    if highlight is not None and highlight in order:
        highlight = order.index(highlight)
    n = len(vals)
    if horizontal is None:
        horizontal = n > 8 or max(len(x) for x in labs) > 12

    pi = plt.getPlotItem()
    vb = pi.getViewBox()
    reais = list(vals)
    clipped = [False] * n
    if clip_at is not None and clip_at > 0:
        clipped = [v > clip_at for v in vals]
        vals = [min(v, clip_at) for v in vals]
    vmax = max(vals + [0.0])
    vmin = min(vals + [0.0])
    if ref is not None and math.isfinite(ref):
        vmax = max(vmax, float(ref))
        vmin = min(vmin, float(ref))
    span = max(vmax - vmin, 1e-9)
    brushes = [brush(c) for c in cols]
    pens = [pen("#FFFFFF", 1.6) if (highlight is not None and i == highlight) else pen(BG, 0.8)
            for i in range(n)]

    _fit_height(plt, n if horizontal else 0)
    if horizontal:
        ys = list(range(n))
        item = pg.BarGraphItem(x0=[0.0] * n, y=ys, height=0.62, width=vals, brushes=brushes, pens=pens)
        plt.addItem(item)
        axl = pi.getAxis("left")
        axl.setTicks([[(i, short(l, 34)) for i, l in enumerate(labs)]])
        axl.setStyle(autoExpandTextSpace=True)
        vb.invertY(True)
        x_hi = vmax + span * 0.22
        x_lo = min(0.0, vmin - span * 0.05)
        vb.setRange(xRange=(x_lo, x_hi), yRange=(-0.7, n - 0.3), padding=0)
        vb.setLimits(xMin=x_lo, xMax=x_hi, yMin=-0.7, yMax=n - 0.3)
        for i, v in enumerate(vals):
            txt = ("› " if clipped[i] else "") + value_fmt.format(reais[i]) + unit
            _value_text(txt, (0.0, 0.5), max(v, 0.0) + span * 0.012, i, plt,
                        color=WARN if clipped[i] else TXT)
        if ref is not None and math.isfinite(ref):
            line = pg.InfiniteLine(pos=float(ref), angle=90, pen=pen(WARN, 1.5, Qt.DashLine),
                                   label=ref_label or None,
                                   labelOpts={"color": WARN, "position": 0.04, "fill": brush("#121C1EAA")})
            plt.addItem(line)
        pi.setLabel("bottom", axis_label, color=DIM)
        pi.showGrid(x=True, y=False, alpha=0.18)
    else:
        xs = list(range(n))
        item = pg.BarGraphItem(x=xs, height=vals, width=0.62, brushes=brushes, pens=pens)
        plt.addItem(item)
        axb = pi.getAxis("bottom")
        axb.setTicks([[(i, _wrap(l, 14)) for i, l in enumerate(labs)]])
        axb.setStyle(autoExpandTextSpace=True)
        # Poucas categorias: alarga o eixo para a barra não engolir o gráfico
        pad = 0.6 if n >= 6 else (6 - n) / 2.0 + 0.6
        y_hi = vmax + span * 0.22
        y_lo = min(0.0, vmin - span * 0.05)
        vb.setRange(xRange=(-pad, n - 1 + pad), yRange=(y_lo, y_hi), padding=0)
        vb.setLimits(xMin=-pad, xMax=n - 1 + pad, yMin=y_lo, yMax=y_hi)
        for i, v in enumerate(vals):
            txt = ("› " if clipped[i] else "") + value_fmt.format(reais[i]) + unit
            _value_text(txt, (0.5, 1.0), i, max(v, 0.0) + span * 0.02, plt,
                        color=WARN if clipped[i] else TXT)
        if ref is not None and math.isfinite(ref):
            line = pg.InfiniteLine(pos=float(ref), angle=0, pen=pen(WARN, 1.5, Qt.DashLine),
                                   label=ref_label or None,
                                   labelOpts={"color": WARN, "position": 0.96, "fill": brush("#121C1EAA")})
            plt.addItem(line)
        pi.setLabel("left", axis_label, color=DIM)


def stacked_bars_h(
    plt,
    labels: Sequence[object],
    series: Sequence[tuple[str, Sequence[object], str]],
    *,
    title: str | None = None,
    total: float | None = 100.0,
    value_fmt: str = "{:.0f}",
    unit: str = "%",
    max_items: int = 40,
    min_label_share: float = 0.08,
) -> None:
    """Barras horizontais empilhadas (ex.: % ALTO / MÉDIO / BAIXO por hospital).

    `series` = [(nome, valores, cor), ...] na ordem de empilhamento (esquerda → direita).
    `total` fixa o eixo (100 para percentuais); None usa a soma máxima observada.
    Segmentos com participação >= min_label_share recebem o valor escrito.
    """
    if pg is None:
        return
    clear(plt)
    style_plot(plt, title)
    labs = [str(x) for x in labels][:max_items]
    n = len(labs)
    if n == 0 or not series:
        empty_message(plt)
        return
    pi = plt.getPlotItem()
    vb = pi.getViewBox()
    _fit_height(plt, n)
    ys = list(range(n))
    left = [0.0] * n
    tot_obs = 0.0
    for name, values, color in series:
        vals = _finite(values)[:n]
        vals += [0.0] * (n - len(vals))
        item = pg.BarGraphItem(x0=left, y=ys, height=0.66, width=vals,
                               brush=brush(color), pen=pen(BG, 0.8))
        plt.addItem(item)
        base = float(total) if total else max(1e-9, max(l + v for l, v in zip(left, vals)))
        for i, v in enumerate(vals):
            if v / base >= min_label_share:
                _value_text(value_fmt.format(v) + unit, (0.5, 0.5), left[i] + v / 2.0, i, plt,
                            color="#0B1416")
        left = [l + v for l, v in zip(left, vals)]
        tot_obs = max(tot_obs, max(left) if left else 0.0)
    x_hi = float(total) if total else tot_obs * 1.05
    axl = pi.getAxis("left")
    axl.setTicks([[(i, short(l, 34)) for i, l in enumerate(labs)]])
    axl.setStyle(autoExpandTextSpace=True)
    vb.invertY(True)
    vb.setRange(xRange=(0, x_hi), yRange=(-0.7, n - 0.3), padding=0)
    vb.setLimits(xMin=0, xMax=x_hi, yMin=-0.7, yMax=n - 0.3)
    pi.showGrid(x=True, y=False, alpha=0.18)
    pi.setLabel("bottom", "% dos casos" if total == 100.0 else "", color=DIM)
    set_title(plt, title_with_legend(title, [(name, color) for name, _v, color in series]))


def grouped_bars(
    plt,
    labels: Sequence[object],
    series: Sequence[tuple[str, Sequence[object], str]],
    *,
    title: str | None = None,
    value_fmt: str = "{:.1f}",
    unit: str = "",
    axis_label: str = "",
    horizontal: bool | None = None,
    y_max: float | None = None,
    max_items: int = 30,
) -> None:
    """Barras agrupadas (2 a 4 séries por categoria), vertical ou horizontal."""
    if pg is None:
        return
    clear(plt)
    style_plot(plt, title)
    labs = [str(x) for x in labels][:max_items]
    n = len(labs)
    k = len(series)
    if n == 0 or k == 0:
        empty_message(plt)
        return
    if horizontal is None:
        horizontal = n > 6 or max(len(x) for x in labs) > 14
    if max((max(_finite(v) + [0.0]) for _n, v, _c in series), default=0.0) <= 0:
        empty_message(plt, f"{title or 'Indicador'}: todos os valores são zero neste dataset")
        return
    pi = plt.getPlotItem()
    vb = pi.getViewBox()
    _fit_height(plt, n * max(1, k) if horizontal else 0, px_per_item=14)
    group_w = 0.74
    bw = group_w / k
    all_vals: list[float] = []
    for j, (name, values, color) in enumerate(series):
        vals = _finite(values)[:n]
        vals += [0.0] * (n - len(vals))
        all_vals += vals
        offs = [i - group_w / 2 + bw * (j + 0.5) for i in range(n)]
        if horizontal:
            item = pg.BarGraphItem(x0=[0.0] * n, y=offs, height=bw * 0.92, width=vals,
                                   brush=brush(color), pen=pen(BG, 0.6))
        else:
            item = pg.BarGraphItem(x=offs, height=vals, width=bw * 0.92,
                                   brush=brush(color), pen=pen(BG, 0.6))
        plt.addItem(item)
        vmax_s = max(all_vals + [0.0])
        for i, v in enumerate(vals):
            if horizontal:
                _value_text(value_fmt.format(v) + unit, (0.0, 0.5), v + vmax_s * 0.01, offs[i], plt, color=DIM)
            else:
                _value_text(value_fmt.format(v) + unit, (0.5, 1.0), offs[i], v + vmax_s * 0.015, plt, color=DIM)
    vmax = y_max if y_max is not None else max(all_vals + [0.0]) * 1.22
    vmax = max(vmax, 1e-9)
    if horizontal:
        axl = pi.getAxis("left")
        axl.setTicks([[(i, short(l, 34)) for i, l in enumerate(labs)]])
        axl.setStyle(autoExpandTextSpace=True)
        vb.invertY(True)
        vb.setRange(xRange=(0, vmax), yRange=(-0.7, n - 0.3), padding=0)
        vb.setLimits(xMin=0, xMax=vmax, yMin=-0.7, yMax=n - 0.3)
        pi.showGrid(x=True, y=False, alpha=0.18)
        pi.setLabel("bottom", axis_label, color=DIM)
    else:
        axb = pi.getAxis("bottom")
        axb.setTicks([[(i, _wrap(l, 14)) for i, l in enumerate(labs)]])
        pad = 0.6 if n >= 6 else (6 - n) / 2.0 + 0.6
        vb.setRange(xRange=(-pad, n - 1 + pad), yRange=(0, vmax), padding=0)
        vb.setLimits(xMin=-pad, xMax=n - 1 + pad, yMin=0, yMax=vmax)
        pi.setLabel("left", axis_label, color=DIM)
    set_title(plt, title_with_legend(title, [(name, color) for name, _v, color in series]))


def bars_with_line(
    plt,
    labels: Sequence[object],
    bar_values: Sequence[object],
    line_values: Sequence[object] | None,
    *,
    title: str | None = None,
    bar_name: str = "Volume",
    line_name: str = "Taxa (%)",
    bar_color: str = PRI,
    line_color: str = WARN,
    line_max: float | None = 100.0,
    bar_fmt: str = "{:,.0f}",
) -> None:
    """Série temporal: barras (eixo esquerdo) + linha em eixo DIREITO independente."""
    if pg is None:
        return
    clear(plt)
    style_plot(plt, title)
    labs = [str(x) for x in labels]
    bars = _finite(bar_values)
    n = len(bars)
    if n == 0:
        empty_message(plt)
        return
    pi = plt.getPlotItem()
    vb = pi.getViewBox()
    xs = list(range(n))
    width = 0.62 if n <= 24 else 0.8
    plt.addItem(pg.BarGraphItem(x=xs, height=bars, width=width, brush=brush(bar_color), pen=pen(BG, 0.6)))
    bmax = max(bars + [0.0]) * 1.22 or 1.0
    pad = 0.6 if n >= 6 else (6 - n) / 2.0 + 0.6
    vb.setRange(xRange=(-pad, n - 1 + pad), yRange=(0, bmax), padding=0)
    vb.setLimits(xMin=-pad, xMax=n - 1 + pad, yMin=0, yMax=bmax)
    if n <= 18:
        for i, v in enumerate(bars):
            _value_text(bar_fmt.format(v), (0.5, 1.0), i, v + bmax * 0.012, plt, color=DIM)
    step = max(1, n // 12)
    pi.getAxis("bottom").setTicks([[(i, labs[i]) for i in range(0, n, step)]])
    pi.setLabel("left", bar_name, color=DIM)
    entries = [(bar_name, bar_color)]
    if line_values is not None:
        line = _finite(line_values)[:n]
        if any(v > 0 for v in line):
            pi.showAxis("right")
            axr = pi.getAxis("right")
            axr.setPen(pen(line_color)); axr.setTextPen(line_color)
            axr.setLabel(line_name, color=line_color)
            vb2 = pg.ViewBox()
            vb2.setMouseEnabled(x=False, y=False)
            vb2.setZValue(10)  # acima do ViewBox principal, senão a linha fica escondida atrás das barras
            pi.scene().addItem(vb2)
            axr.linkToView(vb2)
            vb2.setXLink(vb)
            lmax = float(line_max) if line_max else (max(line) * 1.25 or 1.0)
            vb2.setRange(yRange=(0, lmax), padding=0)
            curve = pg.PlotCurveItem(xs, line, pen=pen(line_color, 2.2))
            pts = pg.ScatterPlotItem(xs, line, size=7, pen=pen(line_color), brush=brush(line_color))
            vb2.addItem(curve); vb2.addItem(pts)

            def _upd(_vb=vb, _vb2=vb2):
                _vb2.setGeometry(_vb.sceneBoundingRect())
                _vb2.linkedViewChanged(_vb, _vb2.XAxis)

            vb.sigResized.connect(_upd)
            _upd()
            plt._vb2 = vb2
            plt._vb2_upd = _upd
            entries.append((line_name, line_color))
    set_title(plt, title_with_legend(title, entries))


def scatter_xy(
    plt,
    xs: Sequence[object],
    ys: Sequence[object],
    *,
    title: str | None = None,
    xlabel: str = "",
    ylabel: str = "",
    highlight: int | None = None,
    highlight_label: str = "",
    medians: bool = True,
    point_color: str = DIM,
    highlight_color: str = OK,
) -> None:
    """Dispersão com medianas tracejadas e um ponto em destaque (hospital alvo)."""
    if pg is None:
        return
    clear(plt)
    style_plot(plt, title, xlabel=xlabel, ylabel=ylabel, grid_x=True)
    px: list[float] = []
    py: list[float] = []
    hx = hy = None
    for i, (a, b) in enumerate(zip(xs, ys)):
        try:
            fa, fb = float(a), float(b)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(fa) and math.isfinite(fb)):
            continue
        if highlight is not None and i == highlight:
            hx, hy = fa, fb
        else:
            px.append(fa); py.append(fb)
    if not px and hx is None:
        empty_message(plt)
        return
    pi = plt.getPlotItem()
    vb = pi.getViewBox()
    if px:
        plt.addItem(pg.ScatterPlotItem(px, py, size=9, pen=pen(BG), brush=brush(point_color + "CC")))
    allx = px + ([hx] if hx is not None else [])
    ally = py + ([hy] if hy is not None else [])
    if medians and len(allx) >= 3:
        sx = sorted(allx); sy = sorted(ally)
        mx = sx[len(sx) // 2]; my = sy[len(sy) // 2]
        plt.addItem(pg.InfiniteLine(pos=mx, angle=90, pen=pen(WARN, 1.2, Qt.DashLine)))
        plt.addItem(pg.InfiniteLine(pos=my, angle=0, pen=pen(WARN, 1.2, Qt.DashLine)))
    if hx is not None:
        plt.addItem(pg.ScatterPlotItem([hx], [hy], size=18, pen=pen("#FFFFFF", 1.5), brush=brush(highlight_color)))
        if highlight_label:
            _value_text(short(highlight_label, 28), (0.0, 1.2), hx, hy, plt, color=highlight_color)
    x_lo, x_hi = min(allx + [0.0]), max(allx)
    y_lo, y_hi = min(ally + [0.0]), max(ally)
    xs_ = max(x_hi - x_lo, 1e-9); ys_ = max(y_hi - y_lo, 1e-9)
    vb.setRange(xRange=(x_lo - xs_ * 0.05, x_hi + xs_ * 0.12), yRange=(y_lo - ys_ * 0.05, y_hi + ys_ * 0.15), padding=0)
    entries = [("Demais hospitais", point_color)]
    if hx is not None:
        entries.append(("Hospital selecionado", highlight_color))
    if medians:
        entries.append(("Medianas", WARN))
    set_title(plt, title_with_legend(title, entries))


__all__ = [
    "set_title", "relayout",
    "BG", "PANEL", "TXT", "DIM", "GRID", "PRI", "PRI_DARK", "WARN", "DANG", "OK", "INFO", "PURPLE", "NEUTRAL",
    "pen", "brush", "lerp", "color_for_pct", "color_for_ratio", "short",
    "style_plot", "clear", "empty_message", "add_legend",
    "bar_categorical", "stacked_bars_h", "grouped_bars", "bars_with_line", "scatter_xy",
]
