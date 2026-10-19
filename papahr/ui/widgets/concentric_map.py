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
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

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
        self.setMinimumSize(380, 340)
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
        self._mun_points: list[list[float]] = []   # [lat, lon, pop, n_internacoes]
        self._proc_g: str | None = None
        self._proc_f: str | None = None
        self._proc_p: str | None = None

        self._empty_layout = QVBoxLayout(self)
        self._empty_layout.setContentsMargins(16, 16, 16, 16)
        self._empty_layout.setAlignment(Qt.AlignCenter)
        self._empty_label = QLabel(
            "<h3 style='color:#94A3B8;margin:0;font-weight:600'>📍 Nenhum hospital georreferenciado no dataset</h3>"
            "<br>"
            "<p style='color:#64748B;font-size:13px;margin:0;line-height:1.5'>"
            "O mapa usa as coordenadas do CNES (tbEstabelecimento) dos hospitais presentes na extração. "
            "Rode o pipeline ou carregue um Parquet com a coluna CNES."
            "</p>"
        )
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet(
            "QLabel{background:transparent;font-family:'Segoe UI',Inter,sans-serif;font-size:16px;}"
        )
        self._empty_holder = QWidget()
        holder_l = QHBoxLayout(self._empty_holder)
        holder_l.setContentsMargins(0, 0, 0, 0)
        holder_l.setAlignment(Qt.AlignCenter)
        holder_l.addWidget(self._empty_label)
        self._empty_layout.addWidget(self._empty_holder, 1, Qt.AlignCenter)
        self._empty_holder.hide()
        self._update_empty_visibility()

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

    @staticmethod
    def _key6_expr(col: str) -> pl.Expr:
        """Código IBGE em 6 dígitos: "0120020" (zfill) → "120020"; "1200203" (com DV) → "120020"."""
        c = pl.col(col).cast(pl.Utf8, strict=False).str.strip_chars()
        return (
            pl.when(c.str.starts_with("0") & (c.str.len_chars() == 7))
            .then(c.str.slice(1, 6))
            .otherwise(c.str.slice(0, 6))
        )

    def set_data(self,
                 points: Sequence[HospitalMapPoint],
                 municipios_df: pl.DataFrame,
                 internacoes_df: pl.DataFrame | None = None) -> None:
        """Calcula buckets por anel e hospital (vetorizado em Polars).

        Args:
            points: hospitais (já restritos aos presentes no dataset).
            municipios_df: CO_MUNICIPIO, NU_LATITUDE, NU_LONGITUDE, NU_POPULACAO (CNES tbMunicipio
                           com centroides derivados pelo CNESLoader).
            internacoes_df: dataset analítico com MUNIC_RES/PA_MUNPCN/SP_M_PAC e CNES
                            (+ opcional NO_GRUPO / NO_FORMA_ORGANIZACAO / NO_PROCEDIMENTO, VAL_TOT).
        """
        self._points = list(points)
        self._buckets = {}
        self._mun_points = []
        self._proc_g = self._proc_f = self._proc_p = None
        if not self._points:
            self._reset_view(); self._update_empty_visibility(); self.update()
            return
        radii = list(self._radii_km)
        rmax = max(radii)
        lats = [p.latitude for p in self._points]
        lons = [p.longitude for p in self._points]

        # 1) municípios com coordenadas, só dentro da caixa envolvente (hospitais + raio máximo)
        mun = pl.DataFrame()
        if municipios_df is not None and not municipios_df.is_empty():
            lat_col = self._find_col(municipios_df, ("NU_LATITUDE", "LATITUDE", "lat", "latitude"))
            lon_col = self._find_col(municipios_df, ("NU_LONGITUDE", "LONGITUDE", "lon", "longitude"))
            cod_col = self._find_col(municipios_df, ("CO_MUNICIPIO", "CO_MUNICIPIO_IBGE", "COD_MUNICIPIO", "codigo_municipio"))
            pop_col = self._find_col(municipios_df, ("NU_POPULACAO", "POPULACAO", "populacao"))
            if lat_col and lon_col and cod_col:
                dlat = rmax * 1.35 / 111.0
                clat = sum(lats) / len(lats)
                dlon = rmax * 1.35 / max(1.0, 111.0 * math.cos(math.radians(clat)))
                mun = (
                    municipios_df.select([
                        self._key6_expr(cod_col).alias("key6"),
                        pl.col(lat_col).cast(pl.Float64, strict=False).alias("lat"),
                        pl.col(lon_col).cast(pl.Float64, strict=False).alias("lon"),
                        (pl.col(pop_col).cast(pl.Float64, strict=False).fill_null(0.0)
                         if pop_col else pl.lit(0.0)).alias("pop"),
                    ])
                    .filter(pl.col("lat").is_not_null() & pl.col("lon").is_not_null()
                            & pl.col("lat").is_not_nan() & pl.col("lon").is_not_nan())
                    .filter(pl.col("lat").is_between(min(lats) - dlat, max(lats) + dlat)
                            & pl.col("lon").is_between(min(lons) - dlon, max(lons) + dlon))
                    .unique(subset=["key6"], keep="first")
                )

        # 2) internações agregadas por (município de residência, CNES[, grupo, forma, procedimento])
        agg = pl.DataFrame()
        if internacoes_df is not None and not internacoes_df.is_empty():
            mun_col = self._find_col(internacoes_df, ("MUNIC_RES", "PA_MUNPCN", "SP_M_PAC", "CO_MUNICIPIO",
                                                      "municipio_residencia"))
            cnes_col = self._find_col(internacoes_df, ("CNES", "CO_CNES", "cnes", "ID_HOSPITAL", "_cnes"))
            if mun_col and cnes_col:
                self._proc_g = self._find_col(internacoes_df, ("NO_GRUPO", "Grupo de Procedimentos", "CO_GRUPO", "grupo"))
                self._proc_f = self._find_col(internacoes_df, ("NO_FORMA_ORGANIZACAO", "Forma de organizacao",
                                                               "CO_FORMA_ORGANIZACAO", "forma_organizacao"))
                self._proc_p = self._find_col(internacoes_df, ("NO_PROCEDIMENTO", "Procedimentos realizados",
                                                               "CO_PROCEDIMENTO", "procedimento"))
                extra = [c for c in (self._proc_g, self._proc_f, self._proc_p) if c]
                val_col = next((c for c in ("VAL_TOT", "VAL_TOTAL", "VL_TOTAL", "VALOR_TOTAL", "Valor Total",
                                            "PA_VALAPR", "Valor Aprovado", "SP_VALATO")
                                if c in internacoes_df.columns), None)
                exprs = [pl.len().alias("n"), pl.col("_valor").sum().alias("valor")]
                try:
                    agg = (
                        internacoes_df.select(
                            [self._key6_expr(mun_col).alias("key6"),
                             pl.col(cnes_col).cast(pl.Utf8, strict=False).str.strip_chars().str.zfill(7).alias("cnes"),
                             (pl.col(val_col).cast(pl.Float64, strict=False).fill_null(0.0)
                              if val_col else pl.lit(0.0)).alias("_valor")]
                            + [pl.col(c).cast(pl.Utf8, strict=False).alias(c) for c in extra]
                        )
                        .group_by(["key6", "cnes"] + extra)
                        .agg(exprs)
                    )
                except Exception as exc_agg:  # noqa: BLE001
                    log_warn = getattr(self, "_log_warn", None)
                    agg = pl.DataFrame()
                    print(f"[mapa] falha ao agregar internacoes: {exc_agg}", flush=True)

        # 3) buckets por hospital
        mun_dots: dict[str, list[float]] = {}
        for hp in self._points:
            buckets = [RingBucket(i, r) for i, r in enumerate(radii)]
            self._buckets[hp.cnes] = buckets
            if mun.is_empty():
                continue
            lat1 = math.radians(hp.latitude); lon1 = math.radians(hp.longitude)
            lat2 = pl.col("lat").radians(); lon2 = pl.col("lon").radians()
            a = ((lat2 - lat1) / 2).sin() ** 2 + math.cos(lat1) * lat2.cos() * ((lon2 - lon1) / 2).sin() ** 2
            dist = 2.0 * 6371.0 * a.sqrt().arcsin()
            ring = pl.lit(len(radii) - 1)
            for i in reversed(range(len(radii))):
                ring = pl.when(dist <= radii[i]).then(pl.lit(i)).otherwise(ring)
            md = mun.with_columns(dist.alias("dist_km"), ring.cast(pl.Int64).alias("ring")).filter(pl.col("dist_km") <= rmax)
            if md.is_empty():
                continue
            pop_ring = md.group_by("ring").agg(pl.col("pop").sum().alias("pop"))
            for r_i, p_sum in zip(pop_ring["ring"].to_list(), pop_ring["pop"].to_list()):
                buckets[int(r_i)].populacao_total = float(p_sum or 0.0)
            n_by_key: dict[str, float] = {}
            if not agg.is_empty():
                a_h = agg.filter(pl.col("cnes") == str(hp.cnes).strip().zfill(7)) \
                         .join(md.select(["key6", "ring"]), on="key6", how="inner")
                if not a_h.is_empty():
                    tot = a_h.group_by("ring").agg(pl.col("n").sum().alias("n"), pl.col("valor").sum().alias("valor"))
                    for r_i, n_v, v_v in zip(tot["ring"].to_list(), tot["n"].to_list(), tot["valor"].to_list()):
                        b = buckets[int(r_i)]
                        b.n_internacoes = int(n_v or 0); b.valor_total = float(v_v or 0.0)
                    for col_name, attr in ((self._proc_g, "procedimentos_por_grupo"),
                                           (self._proc_f, "procedimentos_por_forma"),
                                           (self._proc_p, "procedimentos_por_cod")):
                        if not col_name:
                            continue
                        g = a_h.group_by(["ring", col_name]).agg(pl.col("n").sum().alias("n"))
                        for r_i, k, n_v in zip(g["ring"].to_list(), g[col_name].to_list(), g["n"].to_list()):
                            if k is None or str(k).strip() == "":
                                continue
                            d = getattr(buckets[int(r_i)], attr)
                            d[str(k)] = d.get(str(k), 0) + int(n_v or 0)
                    km = a_h.group_by("key6").agg(pl.col("n").sum().alias("n"))
                    n_by_key = {str(k): float(v or 0) for k, v in zip(km["key6"].to_list(), km["n"].to_list())}
            for k, la, lo, po in zip(md["key6"].to_list(), md["lat"].to_list(), md["lon"].to_list(), md["pop"].to_list()):
                n_here = n_by_key.get(str(k), 0.0)
                cur = mun_dots.get(str(k))
                if cur is None:
                    mun_dots[str(k)] = [float(la), float(lo), float(po or 0.0), n_here]
                else:
                    cur[3] += n_here
        self._mun_points = list(mun_dots.values())

        # 4) escala da paleta pelo percentil 90 das taxas > 0 (o máximo isolado achatava tudo em verde)
        taxas = sorted(b.taxa_por_1000 for bs in self._buckets.values() for b in bs if b.taxa_por_1000 > 0)
        if taxas:
            p90 = taxas[min(len(taxas) - 1, int(0.9 * (len(taxas) - 1)))]
            self._max_rate_display = max(p90, 1.0)
        else:
            self._max_rate_display = 1.0
        self._reset_view()
        self._update_empty_visibility()
        self.update()

    def _update_empty_visibility(self) -> None:
        if not self._points:
            self._empty_holder.show()
        else:
            self._empty_holder.hide()

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
        cx_lat = sum(lats) / len(lats); cx_lon = sum(lons) / len(lons)
        km_lat, km_lon = km_per_degree_lat(cx_lat)
        # Extensão em km da caixa que contém TODOS os hospitais + o anel mais externo
        r_out = max(self._radii_km)
        span_x_km = (max(lons) - min(lons)) * km_lon + 2 * r_out
        span_y_km = (max(lats) - min(lats)) * km_lat + 2 * r_out
        w = max(self.width(), 480); h = max(self.height(), 400)
        # pixels por km que fazem a caixa caber com margem de 8%
        zx = (w * 0.86) / max(1.0, span_x_km)
        zy = (h * 0.86) / max(1.0, span_y_km)
        self._zoom = max(0.05, min(zx, zy))
        # Centro da CAIXA (não a média dos hospitais) no centro do widget: projeta com pan=0 e corrige
        self._pan = QPointF(0.0, 0.0)
        bx, by = self._proj((min(lats) + max(lats)) / 2.0, (min(lons) + max(lons)) / 2.0)
        self._pan = QPointF(w / 2.0 - bx, h / 2.0 - by)

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
        w = self.width(); h = self.height()
        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0.0, QColor("#0B1416"))
        bg.setColorAt(1.0, QColor("#132529"))
        p.fillRect(self.rect(), bg)
        p.end()

        if not self._points:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self._draw_grid(p, w, h)
        self._draw_municipios(p, w, h)
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
                if rad_pix < 1.0:
                    continue
                # Intensidade: anel com taxa alta é mais opaco; taxa zero fica quase transparente
                a_fill = int(18 + 70 * t)
                a_edge = int(70 + 150 * t)
                rg = QRadialGradient(QPointF(cx, cy), rad_pix)
                rg.setColorAt(0.0, QColor(0, 0, 0, 0))
                rg.setColorAt(max(0.0, (in_rad_pix / rad_pix) - 1e-3), QColor(color.red(), color.green(), color.blue(), 0))
                rg.setColorAt(min(1.0, (in_rad_pix / rad_pix) + 1e-3), QColor(color.red(), color.green(), color.blue(), a_fill))
                rg.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), max(0, a_fill - 10)))
                rg.setSpread(QGradient.PadSpread)
                p.setBrush(QBrush(rg))
                p.setPen(Qt.NoPen)
                rect = QRectF(cx - rad_pix, cy - rad_pix, 2 * rad_pix, 2 * rad_pix)
                p.drawEllipse(rect)
                # Contorno do anel na cor da taxa
                p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), a_edge), 1.2, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(rect)
                # Rótulo do raio só no hospital de maior volume (evita 125 textos sobrepostos)
                if hp is self._points[0] and rad_pix > 14:
                    p.setPen(QPen(QColor(C_TEXT_DIM)))
                    f = QFont("Segoe UI", 7); f.setBold(True); p.setFont(f)
                    p.drawText(QPointF(cx + rad_pix * 0.7071 + 3, cy - rad_pix * 0.7071 - 2), f"{r_km:.0f} km")
        # 2) Desenha pins de hospitais (topo de tudo); rótulos só onde não colidem
        drawn_labels: list[QRectF] = []
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
            # Rótulo com fundo escuro (legível sobre os anéis)
            f = QFont("Segoe UI", 8); f.setBold(True); p.setFont(f)
            label = hp.nome if len(hp.nome) < 26 else hp.nome[:24] + "…"
            txt = f"{label}"
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(txt); th = fm.height()
            # tenta à direita, depois à esquerda, depois abaixo; se tudo colide, não rotula
            candidatos = [
                QRectF(cx + pin_r + 3, cy - th / 2.0 - 1, tw + 8, th + 2),
                QRectF(cx - pin_r - 11 - tw, cy - th / 2.0 - 1, tw + 8, th + 2),
                QRectF(cx - tw / 2.0 - 4, cy + pin_r + 4, tw + 8, th + 2),
            ]
            rect_lbl = None
            for cand in candidatos:
                if cand.left() < 0 or cand.right() > w or cand.top() < 0 or cand.bottom() > h:
                    continue
                if not any(cand.intersects(r) for r in drawn_labels):
                    rect_lbl = cand
                    break
            if rect_lbl is not None:
                drawn_labels.append(rect_lbl)
                p.setPen(Qt.NoPen); p.setBrush(QBrush(QColor(11, 20, 22, 200)))
                p.drawRoundedRect(rect_lbl, 4, 4)
                p.setPen(QPen(QColor(C_TEXT)))
                p.drawText(QPointF(rect_lbl.left() + 4, rect_lbl.bottom() - 1 - fm.descent()), txt)
        # 3) Legenda taxa
        self._draw_legend(p, w, h)

    # ------------------------------------------------------------------
    def _draw_municipios(self, p: QPainter, w: int, h: int) -> None:
        """Pontos dos municípios: tamanho ∝ √população; teal se enviam pacientes, cinza se não."""
        if not self._mun_points:
            return
        p.setPen(Qt.NoPen)
        for la, lo, po, n in self._mun_points:
            px, py = self._proj(la, lo)
            if px < -20 or py < -20 or px > w + 20 or py > h + 20:
                continue
            r = 2.0 + min(7.0, math.sqrt(max(po, 0.0)) / 90.0)
            if n > 0:
                p.setBrush(QBrush(QColor(45, 212, 191, 210)))
                p.drawEllipse(QPointF(px, py), r, r)
                p.setPen(QPen(QColor(230, 255, 250, 120), 0.8))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(px, py), r + 1.5, r + 1.5)
                p.setPen(Qt.NoPen)
            else:
                p.setBrush(QBrush(QColor(148, 163, 184, 90)))
                p.drawEllipse(QPointF(px, py), r, r)

    def _draw_grid(self, p: QPainter, w: int, h: int) -> None:
        pen = QPen(QColor(C_GRID)); pen.setStyle(Qt.DotLine); p.setPen(pen)
        step = 80
        for x in range(0, w + step, step):
            p.drawLine(x, 0, x, h)
        for y in range(0, h + step, step):
            p.drawLine(0, y, w, y)

    def _draw_placeholder(self, p: QPainter, w: int, h: int) -> None:
        p.setPen(QPen(QColor("#8F9EAB")))
        f = QFont("Inter", 16); f.setBold(False); p.setFont(f)
        rect = QRectF(20, h/2 - 60, w - 40, 120)
        p.drawText(rect, Qt.AlignCenter,
                   "Selecione ao menos 1 hospital e rode o cálculo\n"
                   "para visualizar os anéis concêntricos de taxa.")

    def _draw_legend(self, p: QPainter, w: int, h: int) -> None:
        # Barra vertical no canto superior direito, com fundo
        bh = max(90, min(160, h - 90)); bw = 14
        x = w - 70; y = 28
        p.setPen(Qt.NoPen); p.setBrush(QBrush(QColor(11, 20, 22, 170)))
        p.drawRoundedRect(QRectF(x - 76, y - 22, 140, bh + 34), 6, 6)
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
        p.drawText(QPointF(x - 70, y - 8), "Taxa / 1.000 hab")
        # Legenda dos pontos (canto inferior esquerdo)
        f2 = QFont("Segoe UI", 8); p.setFont(f2)
        ly = h - 16
        p.setPen(Qt.NoPen); p.setBrush(QBrush(QColor(45, 212, 191, 210)))
        p.drawEllipse(QPointF(18, ly - 4), 4, 4)
        p.setPen(QPen(QColor(C_TEXT))); p.drawText(QPointF(28, ly), "município que envia pacientes")
        p.setPen(Qt.NoPen); p.setBrush(QBrush(QColor(148, 163, 184, 120)))
        p.drawEllipse(QPointF(218, ly - 4), 4, 4)
        p.setPen(QPen(QColor(C_TEXT_DIM))); p.drawText(QPointF(228, ly), "demais municípios (tamanho = população)")

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
