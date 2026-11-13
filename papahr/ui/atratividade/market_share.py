"""
market_share.py — participação de mercado por faixa de distância (donut + tabelas).

    from papahr.ui.atratividade.market_share import MarketShareWidget
    w = MarketShareWidget()
    w.atualizar(aih, hospital, dist_df, limites, nivel, categoria, hospitais)
    w.hospitalClicado.connect(fn)       # clique numa fatia/linha → CNES

Para a faixa escolhida (coroa em torno do hospital em foco), conta as internações do tipo
selecionado dos residentes da faixa em QUALQUER hospital e mostra a fatia de cada um:
donut com os N maiores + "Outros", percentual nas fatias, nome/CNES/percentual ao passar o
mouse, tabela completa e, embaixo, a participação do hospital em foco em cada faixa (com HHI).
"""
from __future__ import annotations

import math

import polars as pl
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QSizePolicy, QSpinBox, QSplitter, QVBoxLayout, QWidget

from papahr.dashboard import atratividade as A
from papahr.ui.atratividade.estilo import RotuloElidido, botao_pequeno, rotulo_chave
from papahr.ui.atratividade.grafico import CAT_CLARO, CAT_ESCURO, INK, INK2, tema_de
from papahr.ui.atratividade.tabela import TabelaPolars

OUTROS = "#9a9890"
COLS_SHARE = [
    ("cor", "", "cor", 26),
    ("NOME", "Hospital", "txt", 230),
    ("CNES", "CNES", "txt", 66),
    ("NOME_MUN", "Município", "txt", 120),
    ("dist_km", "Dist.", "km", 60),
    ("n", "Intern.", "int", 64),
    ("pct", "%", "pct", 58),
    ("cum_pct", "% acum.", "pct", 64),
    ("pct_sih", "% SUS", "pct", 62),
]
COLS_FAIXAS = [
    ("rotulo", "Faixa", "txt", 86),
    ("n_total", "Intern. todos", "int", 84),
    ("n_foco", "No hospital", "int", 76),
    ("share_foco", "Share", "pct", 56),
    ("n_hospitais", "Hosp.", "int", 48),
    ("lider", "Líder na faixa", "txt", 190),
    ("share_lider", "Share líder", "pct", 72),
    ("hhi", "HHI", "num2", 50),
]


class DonutWidget(QWidget):
    """Donut desenhado com QPainter; fatias com percentual, destaque e leitura ao passar o mouse."""

    fatiaClicada = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(280, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self._fatias: list[dict] = []        # {cnes, nome, n, pct, cor, foco}
        self._centro_txt = ""
        self._hover = -1
        self._geom: list[tuple[float, float]] = []   # (ângulo inicial, extensão) em graus, sentido horário a partir de 12 h

    def definir(self, fatias: list[dict], centro: str) -> None:
        self._fatias = list(fatias); self._centro_txt = centro; self._hover = -1
        ang = 0.0; self._geom = []
        tot = sum(f["n"] for f in self._fatias) or 1
        for f in self._fatias:
            ext = 360.0 * f["n"] / tot
            self._geom.append((ang, ext)); ang += ext
        self.update()

    # ---- geometria -----------------------------------------------------
    def _raio(self) -> tuple[QPointF, float, float]:
        w, h = self.width(), self.height()
        r = max(10.0, min(w, h) / 2 - 18)
        return QPointF(w / 2, h / 2), r, r * 0.56

    def _fatia_em(self, pos: QPointF) -> int:
        c, r, ri = self._raio()
        dx, dy = pos.x() - c.x(), pos.y() - c.y()
        d = math.hypot(dx, dy)
        if d < ri * 0.98 or d > r * 1.08:
            return -1
        ang = (math.degrees(math.atan2(dx, -dy)) + 360.0) % 360.0     # 0 = 12 h, horário
        for i, (a0, ext) in enumerate(self._geom):
            if a0 <= ang < a0 + ext:
                return i
        return -1

    # ---- eventos --------------------------------------------------------
    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        i = self._fatia_em(ev.position())
        if i != self._hover:
            self._hover = i; self.update()

    def leaveEvent(self, _ev) -> None:  # noqa: N802
        self._hover = -1; self.update()

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        i = self._fatia_em(ev.position())
        if i >= 0 and self._fatias[i].get("cnes"):
            self.fatiaClicada.emit(self._fatias[i]["cnes"])

    # ---- desenho --------------------------------------------------------
    def paintEvent(self, _ev) -> None:  # noqa: N802
        tema = tema_de(self)
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fundo = self.palette().color(self.backgroundRole())
        c, r, ri = self._raio()
        if not self._fatias:
            p.setPen(QColor(INK2[tema])); p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Sem internações nesta faixa")
            return
        f_pct = QFont(self.font()); f_pct.setPointSizeF(max(7.5, self.font().pointSizeF() - 0.5)); f_pct.setBold(True)
        for i, (fat, (a0, ext)) in enumerate(zip(self._fatias, self._geom)):
            rr = r * (1.05 if i == self._hover else 1.0)
            path = QPainterPath()
            # Qt: ângulos anti-horários a partir de 3 h → converte (12 h horário)
            qa0 = 90.0 - a0
            path.arcMoveTo(QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr), qa0)
            path.arcTo(QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr), qa0, -ext)
            path.arcTo(QRectF(c.x() - ri, c.y() - ri, 2 * ri, 2 * ri), qa0 - ext, ext)
            path.closeSubpath()
            p.setBrush(QColor(fat["cor"]))
            p.setPen(QPen(fundo, 2.0))
            p.drawPath(path)
            if fat.get("foco"):
                p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(QColor(INK[tema]), 2.0)); p.drawPath(path)
            # percentual dentro da fatia quando cabe
            if fat["pct"] >= 0.035 and ext >= 10:
                am = math.radians(a0 + ext / 2)
                rm = (rr + ri) / 2
                x, y = c.x() + rm * math.sin(am), c.y() - rm * math.cos(am)
                txt = f"{fat['pct'] * 100:.0f}%" if fat["pct"] < 0.995 else "100%"
                p.setFont(f_pct)
                cor_txt = "#ffffff" if _escura(fat["cor"]) else INK["claro"]
                p.setPen(QColor(cor_txt))
                fm = QFontMetrics(f_pct); w = fm.horizontalAdvance(txt)
                p.drawText(QRectF(x - w / 2 - 2, y - fm.height() / 2, w + 4, fm.height()), Qt.AlignmentFlag.AlignCenter, txt)
        # centro: hospital sob o mouse ou resumo
        if 0 <= self._hover < len(self._fatias):
            fat = self._fatias[self._hover]
            linhas = [fat["nome"], (f"CNES {fat['cnes']}" if fat.get("cnes") else ""), f"{fat['pct'] * 100:.1f}% · {fat['n']:,} intern.".replace(",", ".")]
        else:
            linhas = self._centro_txt.split("\n")
        p.setPen(QColor(INK[tema]))
        f_c = QFont(self.font()); f_c.setPointSizeF(max(8.0, self.font().pointSizeF()))
        p.setFont(f_c)
        caixa = QRectF(c.x() - ri * 0.92, c.y() - ri * 0.92, ri * 1.84, ri * 1.84)
        p.drawText(caixa, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, "\n".join(l for l in linhas if l))


def _escura(hexcor: str) -> bool:
    c = QColor(hexcor)
    return (0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()) < 0.55


class MarketShareWidget(QWidget):
    hospitalClicado = Signal(str)
    escolherPedido = Signal()          # "escolher hospital/tipo…" → quem hospeda abre a aba de atratividade

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._ctx: dict | None = None
        self._share = pl.DataFrame(); self._faixas = pl.DataFrame()
        lay = QVBoxLayout(self); lay.setContentsMargins(2, 2, 2, 2); lay.setSpacing(2)

        topo = QHBoxLayout(); topo.setSpacing(4)
        self.cmb_faixa = QComboBox(); self.cmb_faixa.setMinimumContentsLength(9); self.cmb_faixa.setToolTip("Faixa de distância em torno do hospital em foco")
        self.chk_acum = QCheckBox("acumulado"); self.chk_acum.setToolTip("Do hospital até o limite da faixa (soma das faixas anteriores)")
        self.spn_top = QSpinBox(); self.spn_top.setRange(3, 8); self.spn_top.setValue(7); self.spn_top.setPrefix("fatias "); self.spn_top.setMinimumWidth(76); self.spn_top.setToolTip("Hospitais nomeados no donut; o resto vira 'Outros'")
        self.btn_csv = botao_pequeno("CSV", "Exporta a tabela de market share da faixa")
        self.btn_escolher = botao_pequeno("escolher hospital/tipo…", "O hospital em foco, o tipo e as faixas são definidos na aba 'Atratividade por faixas'")
        self.btn_escolher.clicked.connect(self.escolherPedido.emit)
        self.lbl_ctx = RotuloElidido("—"); self.lbl_ctx.setProperty("nota", True)
        self.lbl_ctx.setToolTip("O hospital em foco, o tipo de internação e as faixas são os escolhidos na aba 'Atratividade por faixas'. "
                                "Clique num hospital da tabela ou numa fatia do donut para passar o foco a ele.")
        for w in (rotulo_chave("Faixa"), self.cmb_faixa, self.chk_acum, self.spn_top):
            topo.addWidget(w)
        topo.addWidget(self.lbl_ctx, 1); topo.addWidget(self.btn_escolher); topo.addWidget(self.btn_csv)
        lay.addLayout(topo)

        corpo = QSplitter(Qt.Orientation.Vertical)
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.donut = DonutWidget()
        self.tbl = TabelaPolars(COLS_SHARE, esticar_primeira=False)
        self.tbl.setToolTip("Hospitais que internaram residentes da faixa; clique para passar o foco a esse hospital")
        self.split.addWidget(self.donut); self.split.addWidget(self.tbl)
        self.split.setSizes([300, 420]); self.split.setStretchFactor(1, 1)
        corpo.addWidget(self.split)
        self.tbl_faixas = TabelaPolars(COLS_FAIXAS, esticar_primeira=False); self.tbl_faixas.setSortingEnabled(False)
        self.tbl_faixas.setToolTip("Participação do hospital em foco em cada faixa (todos os concorrentes): share, líder e HHI. Clique numa faixa para mostrá-la no donut.")
        corpo.addWidget(self.tbl_faixas)
        corpo.setSizes([330, 150]); corpo.setStretchFactor(0, 3); corpo.setStretchFactor(1, 1)
        lay.addWidget(corpo, 1)

        self.cmb_faixa.currentIndexChanged.connect(lambda _i: self._recalcular())
        self.chk_acum.toggled.connect(lambda _v: self._recalcular())
        self.spn_top.valueChanged.connect(lambda _v: self._desenhar())
        self.btn_csv.clicked.connect(lambda: self.tbl.exportar_csv(self, "market_share.csv"))
        self.donut.fatiaClicada.connect(self.hospitalClicado)
        self.tbl.linhaSelecionada.connect(lambda l: self.hospitalClicado.emit(str(l.get("CNES") or "")) if l.get("CNES") else None)
        self.tbl_faixas.linhaSelecionada.connect(self._faixa_da_tabela)

    # ------------------------------------------------------------------
    def atualizar(self, aih: pl.DataFrame, hospital: dict, dist_df: pl.DataFrame, limites, nivel: str,
                  categoria: str | None, hospitais: pl.DataFrame) -> None:
        self._ctx = {"aih": aih, "hospital": hospital, "dist": dist_df, "limites": tuple(limites), "nivel": nivel,
                     "categoria": categoria, "hospitais": hospitais}
        rot = A.rotulos_faixas(limites)
        atual = self.cmb_faixa.currentIndex()
        self.cmb_faixa.blockSignals(True); self.cmb_faixa.clear()
        for i, r in enumerate(rot):
            self.cmb_faixa.addItem(r, i)
        self.cmb_faixa.setCurrentIndex(atual if 0 <= atual < len(rot) else 0)
        self.cmb_faixa.blockSignals(False)
        try:
            self._faixas = A.share_por_faixa(aih, hospital, dist_df, limites, nivel=nivel, categoria=categoria)
            nomes = {str(r["CNES"]): str(r["NOME"]) for r in hospitais.select(["CNES", "NOME"]).to_dicts()} if not hospitais.is_empty() else {}
            self._faixas = self._faixas.with_columns(
                pl.col("cnes_lider").map_elements(lambda c: nomes.get(str(c), f"CNES {c}") if c else "—", return_dtype=pl.Utf8).alias("lider"))
        except Exception as exc:  # noqa: BLE001
            self._faixas = pl.DataFrame(); self.lbl_ctx.setTexto(f"Falha: {exc}")
        self.tbl_faixas.definir(self._faixas)
        self._recalcular()

    def _recalcular(self) -> None:
        if not self._ctx:
            return
        c = self._ctx; faixa = int(self.cmb_faixa.currentData() or 0)
        try:
            self._share = A.market_share(c["aih"], c["hospital"], c["dist"], faixa, nivel=c["nivel"], categoria=c["categoria"],
                                         hospitais=c["hospitais"], acumulado=self.chk_acum.isChecked())
        except Exception as exc:  # noqa: BLE001
            self._share = pl.DataFrame(); self.lbl_ctx.setTexto(f"Falha: {exc}"); return
        nomes_mun = {}
        if "COD_MUN" in c["dist"].columns and "NOME" in c["dist"].columns:
            nomes_mun = {str(r["COD_MUN"]): str(r["NOME"]) for r in c["dist"].select(["COD_MUN", "NOME"]).to_dicts()}
        self._share = self._share.with_columns(
            pl.col("COD_MUN").map_elements(lambda m: nomes_mun.get(str(m), str(m) if m else "—"), return_dtype=pl.Utf8).alias("NOME_MUN"))
        self._desenhar()

    def _desenhar(self) -> None:
        if self._ctx is None:
            return
        tema = tema_de(self); pal = CAT_ESCURO if tema == "escuro" else CAT_CLARO
        c = self._ctx; faixa = int(self.cmb_faixa.currentData() or 0)
        rot = self.cmb_faixa.currentText()
        if self.chk_acum.isChecked():
            rot = f"até {c['limites'][faixa]:g} km" if faixa < len(c["limites"]) else "todas as faixas"
        tipo = c["categoria"] if (c["nivel"] != "todos" and c["categoria"]) else "todas as internações"
        df = self._share
        total = int(df["n"].sum() or 0) if not df.is_empty() else 0
        self.lbl_ctx.setTexto(f"hospital em foco: {c['hospital'].get('nome')} ({c['hospital'].get('cnes')}) · tipo: {tipo} · "
                              f"residentes da faixa {rot}: {total:,} internações em {df.height} hospitais".replace(",", "."))
        if df.is_empty() or total == 0:
            self.donut.definir([], "Sem internações\nnesta faixa"); self.tbl.definir(pl.DataFrame()); return
        n_top = int(self.spn_top.value())
        # cores: hospital em foco sempre na 1ª cor; demais por volume; resto em "Outros"
        linhas = df.to_dicts()
        foco = [l for l in linhas if l["foco"]]
        resto = [l for l in linhas if not l["foco"]]
        topo = (foco + resto)[:n_top]
        if foco and foco[0] not in topo:
            topo = foco + topo[: n_top - 1]
        cores: dict[str, str] = {}
        fatias = []
        for i, l in enumerate(topo):
            cor = pal[i % len(pal)]; cores[l["CNES"]] = cor
            fatias.append({"cnes": l["CNES"], "nome": l["NOME"], "n": int(l["n"]), "pct": float(l["pct"] or 0), "cor": cor, "foco": bool(l["foco"])})
        outros = [l for l in linhas if l["CNES"] not in cores]
        if outros:
            n_o = sum(int(l["n"]) for l in outros)
            fatias.append({"cnes": "", "nome": f"Outros ({len(outros)} hospitais)", "n": n_o, "pct": n_o / total, "cor": OUTROS, "foco": False})
        self.donut.definir(fatias, f"{total:,} internações\n{rot}".replace(",", "."))
        tab = df.with_columns(pl.col("CNES").map_elements(lambda k: cores.get(str(k), OUTROS), return_dtype=pl.Utf8).alias("cor"))
        self.tbl.definir(tab, negrito_onde="foco")

    def _faixa_da_tabela(self, linha: dict) -> None:
        f = linha.get("faixa")
        if f is not None:
            i = self.cmb_faixa.findData(int(f))
            if i >= 0:
                self.cmb_faixa.setCurrentIndex(i)
