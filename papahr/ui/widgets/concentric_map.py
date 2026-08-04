"""Widget de mapa em círculos concêntricos (ring-buffer) ao redor de hospitais.

Converte lat/lon (graus) para pixels via projeção equiretangular local, então
desenha:
  - 5 aneis (5, 10, 20, 40, 80 km) ao redor de CADA hospital selecionado.
  - Aneis com gradiente radial VERDE → VERMELHO conforme TAXA DE INTERNAÇÃO
    (por 1000 habitantes) no raio correspondente.
  - No centro, pin do hospital (CNES / nome / leitos).
  - Modo de agrupamento: por Grupo SIGTAP / Forma de Organização / Procedimento.

Usa apenas QPainter (PySide6) — sem pyqtgraph. Suporta pan (mouse drag) e zoom (wheel).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import polars as pl
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QGradient,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QFrame, QSizePolicy, QWidget

# Cores Material Teal Dark ---------------------------------------------------
C_BG = "#0F1A1C"
C_GRID = "#1B2B2F"
C_AXIS = "#2D4A4A"
C_PIN_EDGE = "#E6FFFA"
C_TEXT = "#E6FFFA"
C_TEXT_DIM = "#94A3B8"
C_HOSP = "#14B8A6"

# Paleta para aneis (verde → amarelo → vermelho, por taxa/1000hab)
_PALETTE_RATE = [
    (0.0,  (34,  197,  94, 210)),   # verde OK
    (0.35, (59, 130, 246, 190)),   # azul
    (0.6,  (245, 158,  11, 200)),  # amarelo
    (0.8,  (239,  68,  68, 220)),  # vermelho
    (1.0,  (127,  29,  29, 230)),  # vermelho escuro
]

# Radios padrão em km (aneis concêntricos)
DEFAULT_RADII_KM = (5.0, 10.0, 20.0, 40.0, 80.0)


@dataclass(slots=True)
class HospitalMapPoint:
    cnes: str
    nome: str
    latitude: float
    longitude: float
    leitos: float = 100.0


@dataclass(slots=True)
class RingBucket:
    """Municípios agregados dentro de cada anel (distância [min, max) km) ao redor do hospital."""
    ring_index: int
    radius_km: float
    populacao_total: float = 0.0
    n_internacoes: int = 0
    valor_total: float = 0.0
    procedimentos_por_grupo: dict[str, int] = field(default_factory=dict)
    procedimentos_por_forma:  dict[str, int] = field(default_factory=dict)
    procedimentos_por_cod:   dict[str, int] = field(default_factory=dict)

    @property
    def taxa_por_1000(self) -> float:
        if self.populacao_total <= 0:
            return 0.0
        return 1000.0 * self.n_internacoes / self.populacao_total


# ---------------------------------------------------------------------------
def km_per_degree_lat(lat_deg: float) -> tuple[float, float]:
    """Retorna (km_por_grau_latitude, km_por_grau_longitude) numa latitude."""
    lat_rad = math.radians(lat_deg)
    # 1 grau latitude ~ 111.132954 km (WGS84)
    km_lat = 111.132954 - 0.559822 * math.cos(2 * lat_rad) + 0.001175 * math.cos(4 * lat_rad)
    km_lon = 111.41284 * math.cos(lat_rad) - 0.09350 * math.cos(3 * lat_rad) + 0.00012 * math.cos(5 * lat_rad)
    return km_lat, km_lon


# ---------------------------------------------------------------------------
class ConcentricRingsMap(QWidget):
    """Mapa de anéis concêntricos de oferta de saúde (taxa/1000hab)."""

    hospital_clicked = Signal(str)  # emite CNES

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(520, 480)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setStyleSheet(f"background:{C_BG};border-radius:12px;border:1px solid {C_AXIS};")
        self._points: list[HospitalMapPoint] = []
        self._buckets: dict[str, list[RingBucket]] = {}  # cnes → 5 buckets
        self._radii_km: tuple[float, ...] = DEFAULT_RADII_KM
        self._group_mode: str = "grupo"   # "grupo" | "forma" | "procedimento"
        self._filter_key: str | None = None   # "02 - Atenção Básica" etc.
        self._zoom: float = 1.0
        self._pan = QPointF(0, 0)
        self._drag_pos: QPointF | None = None
        self._max_rate_display: float = 10.0  # por 1000hab (paleta)
        self._tooltip_pixmap = QPixmap()

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def set_radii(self, radii: Sequence[float]) -> None:
        self._radii_km = tuple(radii)
        self.update()

    def set_group_mode(self, mode: str, filter_key: str | None = None) -> None:
        self._group_mode = mode
        self._filter_key = filter_key
        self.update()

    def set_max_rate(self, v: float) -> None:
        self._max_rate_display = max(1.0, float(v))
        self.update()

    def set_data(self,
                 points: Sequence[HospitalMapPoint],
                 municipios_df: pl.DataFrame,
                 internacoes_df: pl.DataFrame | None = None) -> None:
        """Calcula buckets por anel e hospital.

        Args:
            points: hospitais.
            municipios_df: colunas CO_MUNICIPIO, NU_LATITUDE, NU_LONGITUDE, NU_POPULACAO, (opc) NO_MUNICIPIO, SIGLA_ESTADO
            internacoes_df: colunas MUNIC_RES (6 dígitos IBGE, 7→6) e CNES/CO_CNES
                            + (opc) CO_GRUPO, NO_GRUPO, CO_FORMA_ORGANIZACAO, NO_FORMA_ORGANIZACAO,
                                   CO_PROCEDIMENTO, NO_PROCEDIMENTO, QT_PROCED, VAL_TOTAL.
        """
        self._points = list(points)
        # Agrupa internações por (municipio_res, cnes)
        agg_mun = pl.DataFrame()
        if internacoes_df is not None and not internacoes_df.is_empty():
            mun_col = self._find_col(internacoes_df, ("MUNIC_RES", "CO_MUNICIPIO", "municipio_residencia"))
            cnes_col = self._find_col(internacoes_df, ("CNES", "CO_CNES", "cnes", "ID_HOSPITAL", "_cnes"))
            if mun_col and cnes_col:
                g_cols: list[str] = [mun_col, cnes_col]
                agg_exprs = [pl.count().alias("n_internacoes")]
                # Valor total
                for c in ("VAL_TOTAL", "VL_TOTAL", "VALOR_TOTAL", "vlr_total"):
                    if c in internacoes_df.columns:
                        agg_exprs.append(pl.col(c).cast(pl.Float64, strict=False).fill_null(0).sum().alias("valor_total"))
                        break
                # Agrupa também por grupo/forma/procedimento se existir
                self._proc_g = self._find_col(internacoes_df, ("NO_GRUPO", "CO_GRUPO", "grupo"))
                self._proc_f = self._find_col(internacoes_df, ("NO_FORMA_ORGANIZACAO", "CO_FORMA_ORGANIZACAO", "forma_organizacao"))
                self._proc_p = self._find_col(internacoes_df, ("NO_PROCEDIMENTO", "CO_PROCEDIMENTO", "procedimento"))
                for c in (self._proc_g, self._proc_f, self._proc_p):
                    if c and c not in g_cols:
                        g_cols.append(c)
                try:
                    agg_mun = internacoes_df.group_by(g_cols).agg(agg_exprs)
                except Exception:
                    agg_mun = pl.DataFrame()
        # Agrupa por (mun, cnes) - simplifica para buckets
        buckets_per_hospital: dict[str, list[RingBucket]] = {}
        for hp in self._points:
            buckets = [RingBucket(i, r) for i, r in enumerate(self._radii_km)]
            buckets_per_hospital[hp.cnes] = buckets
            if municipios_df.is_empty():
                continue
            # Para cada município, distância para o hospital → bucket
            lats = municipios_df["NU_LATITUDE"].cast(pl.Float64, strict=False).fill_nan(None).fill_null(None)
            lons = municipios_df["NU_LONGITUDE"].cast(pl.Float64, strict=False).fill_nan(None).fill_null(None)
            cods = municipios_df["CO_MUNICIPIO"].cast(pl.Utf8).fill_null("")
            pops = (municipios_df["NU_POPULACAO"].cast(pl.Float64, strict=False)
                    if "NU_POPULACAO" in municipios_df.columns else pl.repeat(0.0, municipios_df.height))
            # Internações deste hospital
            mun_hp_counts: dict[str, dict[str, Any]] = {}
            if not agg_mun.is_empty() and cnes_col is not None and mun_col is not None:
                filt = agg_mun.filter(pl.col(cnes_col) == hp.cnes)
                # Agrupa por município
                mun_keys: dict[str, dict[str, Any]] = {}
                for row in filt.to_dicts():
                    mkey = str(row.get(mun_col, ""))
                    # Trunca municípios 7→6 dígitos
                    if mkey and len(mkey) > 6:
                        mkey = mkey[:6]
                    d = mun_keys.setdefault(mkey, {"n": 0, "valor": 0.0, "grupos": {}, "formas": {}, "procs": {}})
                    d["n"] += int(row.get("n_internacoes", 0))
                    d["valor"] += float(row.get("valor_total", 0.0) or 0.0)
                    if self._proc_g:
                        g = str(row.get(self._proc_g, "") or "")
                        if g:
                            d["grupos"][g] = d["grupos"].get(g, 0) + int(row.get("n_internacoes", 0))
                    if self._proc_f:
                        f = str(row.get(self._proc_f, "") or "")
                        if f:
                            d["formas"][f] = d["formas"].get(f, 0) + int(row.get("n_internacoes", 0))
                    if self._proc_p:
                        p = str(row.get(self._proc_p, "") or "")
                        if p:
                            d["procs"][p] = d["procs"].get(p, 0) + int(row.get("n_internacoes", 0))
                mun_hp_counts = mun_keys

            km_lat_hp, km_lon_hp = km_per_degree_lat(hp.latitude)
            for i in range(municipios_df.height):
                lat = lats[i]; lon = lons[i]; cod = cods[i]
                if lat is None or lon is None:
                    continue
                # Distância
                dlat = float(lat - hp.latitude) * km_lat_hp
                dlon = float(lon - hp.longitude) * km_lon_hp
                dist_km = math.hypot(dlat, dlon)
                # Acha bucket (primeiro radius_km >= dist)
                ridx = len(self._radii_km) - 1  # outermost default
                for b_idx, r in enumerate(self._radii_km):
                    if dist_km <= r:
                        ridx = b_idx
                        break
                bucket = buckets[ridx]
                try:
                    bucket.populacao_total += float(pops[i])
                except Exception:
                    pass
                mkey6 = cod[:6] if cod else ""
                if mkey6 in mun_hp_counts:
                    info = mun_hp_counts[mkey6]
                    n_val = int(info["n"])
                    bucket.n_internacoes += n_val
                    bucket.valor_total += info["valor"]
                    for k, v in info["grupos"].items():
                        bucket.procedimentos_por_grupo[k] = bucket.procedimentos_por_grupo.get(k, 0) + v
                    for k, v in info["formas"].items():
                        bucket.procedimentos_por_forma[k] = bucket.procedimentos_por_forma.get(k, 0) + v
                    for k, v in info["procs"].items():
                        bucket.procedimentos_por_cod[k] = bucket.procedimentos_por_cod.get(k, 0) + v
        self._buckets = buckets_per_hospital
        # Auto escala max_rate
        max_rate = 0.1
        for bs in buckets_per_hospital.values():
            for b in bs:
                if b.taxa_por_1000 > max_rate:
                    max_rate = b.taxa_por_1000
        self._max_rate_display = max(max_rate * 1.2, 1.0)
        self._reset_view()
        self.update()

    # ------------------------------------------------------------------
    @staticmethod
    def _find_col(df: pl.DataFrame, candidates: tuple[str, ...]) -> str | None:
        low = {c.lower().replace(" ", "_"): c for c in df.columns}
        for k in candidates:
            if k.lower() in low:
                return low[k.lower()]
        return None

    # ------------------------------------------------------------------
    def _reset_view(self) -> None:
        if not self._points:
            self._zoom = 1.0; self._pan = QPointF(0, 0); return
        lats = [p.latitude for p in self._points]
        lons = [p.longitude for p in self._points]
        pad_km = 2 * max(self._radii_km)
        cx_lat = sum(lats) / len(lats); cx_lon = sum(lons) / len(lons)
        # ~ 80% widget width = 2*pad_km
        km_lat, km_lon = km_per_degree_lat(cx_lat)
        deg_lon_span = 2.2 * pad_km / max(0.001, km_lon)
        deg_lat_span = 2.2 * pad_km / max(0.001, km_lat)
        # Desenhamos no rect do widget
        w = max(self.width(), 480); h = max(self.height(), 400)
        zx = w / max(0.01, deg_lon_span)
        zy = h / max(0.01, deg_lat_span)
        self._zoom = min(zx, zy)
        self._pan = QPointF(w / 2.0, h / 2.0)

    # ------------------------------------------------------------------
    def _bucket_count_filtered(self, bucket: RingBucket | None) -> int:
        """Retorna contagem de internações no bucket considerando filtros de grupo."""
        if bucket is None:
            return 0
        if not self._filter_key:
            return bucket.n_internacoes
        if self._group_mode == "grupo":
            return int(bucket.procedimentos_por_grupo.get(self._filter_key, 0))
        if self._group_mode == "forma":
            return int(bucket.procedimentos_por_forma.get(self._filter_key, 0))
        if self._group_mode == "procedimento":
            return int(bucket.procedimentos_por_cod.get(self._filter_key, 0))
        return bucket.n_internacoes

    def _bucket_rate_filtered(self, bucket: RingBucket | None) -> float:
        if bucket is None or bucket.populacao_total <= 0:
            return 0.0
        return 1000.0 * self._bucket_count_filtered(bucket) / bucket.populacao_total

    # ------------------------------------------------------------------
    # Eventos de interação
    # ------------------------------------------------------------------
    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reset_view()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        d = event.angleDelta().y()
        factor = 1.25 if d > 0 else 0.8
        self._zoom = max(0.1, min(2000.0, self._zoom * factor))
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.position()
            # Detecta clique em hospital para emitir
            hp = self._pick_hospital(event.position())
            if hp is not None:
                self.hospital_clicked.emit(hp.cnes)
                event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            delta = event.position() - self._drag_pos
            self._pan += delta
            self._drag_pos = event.position()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_pos = None

    def _pick_hospital(self, pos: QPointF) -> HospitalMapPoint | None:
        # 16px raio = hit
        best: HospitalMapPoint | None = None; best_d = 1e9
        for hp in self._points:
            px, py = self._proj(hp.latitude, hp.longitude)
            d = math.hypot(px - pos.x(), py - pos.y())
            if d < 18 and d < best_d:
                best = hp; best_d = d
        return best

    # ------------------------------------------------------------------
    # Projeção e pintura
    # ------------------------------------------------------------------
    def _proj(self, lat: float, lon: float) -> tuple[float, float]:
        """lat/lon graus → pixels (topo do widget = maior latitude)."""
        if not self._points:
            return (self.width() / 2.0, self.height() / 2.0)
        lats = [p.latitude for p in self._points]
        lons = [p.longitude for p in self._points]
        cx_lat = sum(lats) / len(lats); cx_lon = sum(lons) / len(lons)
        km_lat, km_lon = km_per_degree_lat(cx_lat)
        # Conversão graus → km → pixels (crescimento em x = +lon, y = -lat pois topo = lat alta)
        dx_km = (lon - cx_lon) * km_lon
        dy_km = (lat - cx_lat) * km_lat
        px = self._pan.x() + dx_km * self._zoom
        py = self._pan.y() - dy_km * self._zoom
        return (px, py)

    def paintEvent(self, _evt: Any) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # Fundo com gradiente
        w = self.width(); h = self.height()
        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0.0, QColor("#0B1416"))
        bg.setColorAt(1.0, QColor("#132529"))
        p.fillRect(self.rect(), bg)
        # Grid
        self._draw_grid(p, w, h)
        if not self._points:
            self._draw_placeholder(p, w, h)
            return
        # 1) Desenha aneis
        for hp in self._points:
            buckets = self._buckets.get(hp.cnes, [])
            cx, cy = self._proj(hp.latitude, hp.longitude)
            # Radios de fora para dentro (para composição)
            for i in reversed(range(len(self._radii_km))):
                r_km = self._radii_km[i]
                bucket = buckets[i] if i < len(buckets) else None
                # Pixels: quantos pixels por km?
                # Usamos um ponto próximo (1 km a leste) para calcular px_por_km
                km_lat, km_lon = km_per_degree_lat(hp.latitude)
                one_km_lon = 1.0 / max(0.001, km_lon)
                lon1 = hp.longitude + one_km_lon
                _x1, _y1 = self._proj(hp.latitude, lon1)
                px_per_km = abs(_x1 - cx)
                rad_pix = r_km * px_per_km
                # Cor do anel: taxa/1000 (considera filtro de grupo SIGTAP/forma/procedimento)
                rate = self._bucket_rate_filtered(bucket)
                t = min(1.0, rate / max(0.01, self._max_rate_display))
                color = self._palette_color(t)
                # Radial gradiente: transparente fora → cor forte no início do anel
                in_rad_pix = (self._radii_km[i - 1] * px_per_km) if i > 0 else 0.0
                rg = QRadialGradient(QPointF(cx, cy), rad_pix)
                rg.setColorAt(0.0, QColor(0, 0, 0, 0))
                rg.setColorAt(max(0.0, (in_rad_pix / rad_pix) - 1e-3), QColor(color.red(), color.green(), color.blue(), 0))
                rg.setColorAt(min(1.0, (in_rad_pix / rad_pix) + 1e-3), color)
                rg.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), max(0, color.alpha() - 120)))
                rg.setSpread(QGradient.PadSpread)
                p.setBrush(QBrush(rg))
                p.setPen(Qt.NoPen)
                rect = QRectF(cx - rad_pix, cy - rad_pix, 2 * rad_pix, 2 * rad_pix)
                p.drawEllipse(rect)
                # Contorno do anel cinza claro sutil
                p.setPen(QPen(QColor(C_AXIS), 1, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(rect)
                # Texto radius
                p.setPen(QPen(QColor(C_TEXT_DIM)))
                f = QFont("Segoe UI", 7); f.setBold(True); p.setFont(f)
                p.drawText(QPointF(cx + rad_pix + 4, cy - 2), f"{r_km:.0f} km")
        # 2) Desenha pins de hospitais (topo de tudo)
        for hp in self._points:
            cx, cy = self._proj(hp.latitude, hp.longitude)
            pin_r = max(5.0, min(14.0, 2 + math.sqrt(max(hp.leitos, 0.0)) / 3.5))
            # Glow
            glow = QRadialGradient(QPointF(cx, cy), pin_r * 3.2)
            glow.setColorAt(0.0, QColor(20, 184, 166, 180))
            glow.setColorAt(1.0, QColor(20, 184, 166, 0))
            p.setBrush(glow); p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx, cy), pin_r * 3.2, pin_r * 3.2)
            # Pino (círculo + anel branco)
            p.setBrush(QBrush(QColor(C_HOSP)))
            p.setPen(QPen(QColor(C_PIN_EDGE), 2))
            p.drawEllipse(QPointF(cx, cy), pin_r, pin_r)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), pin_r + 4, pin_r + 4)
            # Legenda
            p.setPen(QPen(QColor(C_TEXT)))
            f = QFont("Segoe UI", 8); p.setFont(f)
            label = hp.nome if len(hp.nome) < 22 else hp.nome[:20] + "…"
            p.drawText(QPointF(cx + pin_r + 7, cy + 3), f"{label} ({hp.cnes[-5:]})")
        # 3) Legenda taxa
        self._draw_legend(p, w, h)

    # ------------------------------------------------------------------
    def _draw_grid(self, p: QPainter, w: int, h: int) -> None:
        pen = QPen(QColor(C_GRID)); pen.setStyle(Qt.DotLine); p.setPen(pen)
        step = 80
        for x in range(0, w + step, step):
            p.drawLine(x, 0, x, h)
        for y in range(0, h + step, step):
            p.drawLine(0, y, w, y)

    def _draw_placeholder(self, p: QPainter, w: int, h: int) -> None:
        p.setPen(QPen(QColor(C_TEXT_DIM)))
        f = QFont("Segoe UI", 11); f.setItalic(True); p.setFont(f)
        rect = QRectF(40, h/2 - 40, w - 80, 80)
        p.drawText(rect, Qt.AlignCenter,
                   "Selecione ao menos 1 hospital e rode o pipeline\n"
                   "para visualizar os anéis concêntricos de taxa de internação.")

    def _draw_legend(self, p: QPainter, w: int, h: int) -> None:
        # Barra vertical no canto inferior direito
        x = w - 60; y = h - 230; bw = 22; bh = 200
        p.setPen(QPen(QColor(C_AXIS))); p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(x, y, bw, bh))
        # Gradiente
        for i in range(bh):
            t = 1.0 - i / bh
            color = self._palette_color(t)
            p.fillRect(QRectF(x + 1, y + 1 + i, bw - 2, 2), QBrush(color))
        # Ticks
        p.setPen(QPen(QColor(C_TEXT)))
        f = QFont("Segoe UI", 8); p.setFont(f)
        ticks = [0.0, 0.25, 0.50, 0.75, 1.0]
        for t in ticks:
            yy = y + bh - t * bh
            rate = t * self._max_rate_display
            p.drawText(QPointF(x + bw + 6, yy + 3), f"{rate:,.1f}/k")
            p.setPen(QPen(QColor(C_PIN_EDGE), 1))
            p.drawLine(x - 3, int(yy), x, int(yy))
            p.setPen(QPen(QColor(C_TEXT)))
        # Título legenda
        f = QFont("Segoe UI", 8); f.setBold(True); p.setFont(f)
        p.drawText(QPointF(x - 70, y - 6), "Taxa / 1.000 hab")

    # ------------------------------------------------------------------
    @staticmethod
    def _palette_color(t: float) -> QColor:
        t = min(1.0, max(0.0, t))
        for i in range(len(_PALETTE_RATE) - 1):
            t0, c0 = _PALETTE_RATE[i]
            t1, c1 = _PALETTE_RATE[i + 1]
            if t0 <= t <= t1:
                u = (t - t0) / max(1e-9, t1 - t0)
                r = int(c0[0] + (c1[0] - c0[0]) * u)
                g = int(c0[1] + (c1[1] - c0[1]) * u)
                b = int(c0[2] + (c1[2] - c0[2]) * u)
                a = int(c0[3] + (c1[3] - c0[3]) * u)
                return QColor(r, g, b, a)
        r, g, b, a = _PALETTE_RATE[-1][1]
        return QColor(r, g, b, a)
