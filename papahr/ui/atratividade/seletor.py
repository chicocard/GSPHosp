"""
seletor.py — escolha do hospital em cascata: UF → região de saúde → município → hospital,
com busca livre por nome ou CNES.

    from papahr.ui.atratividade.seletor import SeletorHospital
    sel = SeletorHospital()
    sel.carregar(hospitais, municipios, cnes_inicial="0026921")   # hospitais: CNES, NOME, LAT, LON, COD_MUN, n
    sel.hospitalMudou.connect(fn)                                 # CNES (str) ou "" quando "(nenhum)"
    sel.cnes()  /  sel.hospital()  /  sel.selecionar(cnes)  /  sel.uf()  /  sel.regiao()

A região de saúde vem de papahr.dashboard.regioes_saude (CIR do DATASUS; reserva: microrregião IBGE).
"""
from __future__ import annotations

import polars as pl
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QGridLayout, QLabel, QLineEdit, QSizePolicy, QWidget

from papahr.dashboard.atratividade import UF_NOME, UF_SIGLA
from papahr.dashboard.regioes_saude import carregar_regioes_saude, juntar_regiao, municipios_da_regiao

TODOS_MUN = "__todos__"
TODAS_REG = "__todas__"
NENHUM = "__nenhum__"


def _n(v) -> str:
    return f"{int(v):,}".replace(",", ".")


def _rotulo(r: dict) -> str:
    """'Nome  (CNES) · 1.234 AIH' — '≈' quando a coordenada é o centroide do município; 'sem AIH' quando n = 0."""
    aih = f"{_n(r['n'])} AIH" if int(r.get("n") or 0) > 0 else "sem AIH"
    aprox = " ≈" if str(r.get("COORD") or "") == "centroide do município" else ""
    return f"{r['NOME']}  ({r['CNES']}) · {aih}{aprox}"


class SeletorHospital(QWidget):
    hospitalMudou = Signal(str)
    ufMudada = Signal(str)          # código IBGE de 2 dígitos

    def __init__(self, parent: QWidget | None = None, permitir_nenhum: bool = False, horizontal: bool = True) -> None:
        super().__init__(parent)
        self._permitir_nenhum = permitir_nenhum
        self._hosp = pl.DataFrame()          # CNES, NOME, COD_MUN, n, UF, CO_REGSAUD, NO_REGSAUD, NOME_MUN
        self._bloq = False
        self.cmb_uf = QComboBox(); self.cmb_reg = QComboBox(); self.cmb_mun = QComboBox(); self.cmb_hosp = QComboBox()
        for c in (self.cmb_uf, self.cmb_reg, self.cmb_mun, self.cmb_hosp):
            c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.cmb_uf.setMinimumContentsLength(4); self.cmb_reg.setMinimumContentsLength(10)
        self.cmb_mun.setMinimumContentsLength(10); self.cmb_hosp.setMinimumContentsLength(18)
        self.ed_busca = QLineEdit(); self.ed_busca.setPlaceholderText("buscar nome ou CNES (qualquer UF)…"); self.ed_busca.setClearButtonEnabled(True)
        self.cmb_uf.setToolTip("UF do hospital")
        self.cmb_reg.setToolTip("Região de saúde (CIR). '(todas as regiões)' lista todos os hospitais da UF na caixa Hospital.")
        self.cmb_mun.setToolTip("Município do hospital")
        self.cmb_hosp.setToolTip("Hospital em foco. ≈ = sem coordenada no CNES (centroide do município); 'sem AIH' = sem internações no dataset.")
        self.ed_busca.setToolTip("Busca livre por nome ou CNES em qualquer UF; reposiciona a cascata.")
        self.lbl_info = QLabel(" "); self.lbl_info.setProperty("nota", True); self.lbl_info.setWordWrap(True)
        if horizontal:
            # UMA linha de caixas, sem rótulos (os conteúdos são autoexplicativos; a dica diz o campo): 22 px de altura
            g = QGridLayout(self); g.setContentsMargins(0, 0, 0, 0); g.setHorizontalSpacing(4); g.setVerticalSpacing(0)
            for col, (w, st) in enumerate(((self.cmb_uf, 2), (self.cmb_reg, 5), (self.cmb_mun, 4), (self.cmb_hosp, 9), (self.ed_busca, 3))):
                g.addWidget(w, 0, col); g.setColumnStretch(col, st)
            self.lbl_info.hide()
        else:
            form = QFormLayout(self); form.setContentsMargins(0, 0, 0, 0); form.setHorizontalSpacing(8); form.setVerticalSpacing(4)
            form.addRow("UF", self.cmb_uf); form.addRow("Região de saúde", self.cmb_reg)
            form.addRow("Município", self.cmb_mun); form.addRow("Hospital", self.cmb_hosp)
            form.addRow("", self.ed_busca); form.addRow("", self.lbl_info)
        self.cmb_uf.currentIndexChanged.connect(self._uf_mudou)
        self.cmb_reg.currentIndexChanged.connect(self._reg_mudou)
        self.cmb_mun.currentIndexChanged.connect(self._mun_mudou)
        self.cmb_hosp.currentIndexChanged.connect(self._hosp_mudou)
        self.ed_busca.textChanged.connect(self._buscar)

    # ------------------------------------------------------------------
    def carregar(self, hospitais: pl.DataFrame, municipios: pl.DataFrame, cnes_inicial: str | None = None) -> None:
        h = hospitais.with_columns(pl.col("CNES").cast(pl.Utf8).str.zfill(7))
        if "COD_MUN" not in h.columns:
            h = h.with_columns(pl.lit(None, dtype=pl.Utf8).alias("COD_MUN"))
        if "n" not in h.columns:
            h = h.with_columns(pl.lit(0).alias("n"))
        h = h.with_columns(pl.col("COD_MUN").cast(pl.Utf8).str.slice(0, 6))
        nomes = municipios.select(pl.col("COD_MUN").cast(pl.Utf8).str.slice(0, 6), pl.col("NOME").alias("_nome_mun")) if "NOME" in municipios.columns else pl.DataFrame({"COD_MUN": [], "_nome_mun": []}, schema={"COD_MUN": pl.Utf8, "_nome_mun": pl.Utf8})
        h = juntar_regiao(h.join(nomes.unique(subset=["COD_MUN"]), on="COD_MUN", how="left"))
        nomes_reg = carregar_regioes_saude().select(pl.col("COD_MUN"), pl.col("NOME_MUN").alias("_nome_reg"))
        h = h.join(nomes_reg, on="COD_MUN", how="left").with_columns(
            pl.col("COD_MUN").str.slice(0, 2).replace_strict(UF_SIGLA, default="?", return_dtype=pl.Utf8).alias("UF"),
            pl.coalesce([pl.col("_nome_mun"), pl.col("_nome_reg"), pl.col("COD_MUN")]).alias("NOME_MUN"),
            pl.col("CO_REGSAUD").fill_null("?"), pl.col("NO_REGSAUD").fill_null("(sem região)"),
        ).drop(["_nome_mun", "_nome_reg"]).sort("n", descending=True)
        self._hosp = h
        self._bloq = True
        self.cmb_uf.clear()
        for r in h.group_by("UF").agg(pl.len().alias("k")).sort("UF").to_dicts():
            self.cmb_uf.addItem(f"{r['UF']} — {UF_NOME.get(r['UF'], '')}  ({r['k']} hosp.)", r["UF"])
        self._bloq = False
        alvo = str(cnes_inicial).zfill(7) if cnes_inicial else (h["CNES"][0] if h.height else None)
        if alvo:
            self.selecionar(alvo)
        elif self.cmb_uf.count():
            self._uf_mudou(0)
        else:
            self._hosp_mudou(-1)

    # ------------------------------------------------------------------
    def _uf_mudou(self, _i: int) -> None:
        if self._bloq:
            return
        uf = self.cmb_uf.currentData()
        self._bloq = True
        self.cmb_reg.clear()
        sub = self._hosp.filter(pl.col("UF") == uf)
        regs = sub.group_by(["CO_REGSAUD", "NO_REGSAUD"]).agg(pl.len().alias("k")).sort("NO_REGSAUD")
        self.cmb_reg.addItem(f"(todas as {regs.height} regiões · {sub.height} hospitais)", TODAS_REG)
        for r in regs.to_dicts():
            self.cmb_reg.addItem(f"{r['NO_REGSAUD']}  ({r['k']})", r["CO_REGSAUD"])
        self._bloq = False
        if uf:
            self.ufMudada.emit(uf)
        self._reg_mudou(0)

    def _reg_mudou(self, _i: int) -> None:
        if self._bloq:
            return
        uf, reg = self.cmb_uf.currentData(), self.cmb_reg.currentData()
        self._bloq = True
        self.cmb_mun.clear()
        sub = self._hosp.filter(pl.col("UF") == uf)
        if reg and reg != TODAS_REG:
            sub = sub.filter(pl.col("CO_REGSAUD") == reg)
        muns = sub.group_by(["COD_MUN", "NOME_MUN"]).agg(pl.len().alias("k")).sort("NOME_MUN")
        self.cmb_mun.addItem(f"(todos os {muns.height} municípios · {sub.height} hospitais)", TODOS_MUN)
        for r in muns.to_dicts():
            self.cmb_mun.addItem(f"{r['NOME_MUN']}  ({r['k']})", r["COD_MUN"])
        self._bloq = False
        self._mun_mudou(0)

    def _mun_mudou(self, _i: int) -> None:
        if self._bloq:
            return
        uf, reg, mun = self.cmb_uf.currentData(), self.cmb_reg.currentData(), self.cmb_mun.currentData()
        sub = self._hosp.filter(pl.col("UF") == uf)
        if reg and reg != TODAS_REG:
            sub = sub.filter(pl.col("CO_REGSAUD") == reg)
        if mun and mun != TODOS_MUN:
            sub = sub.filter(pl.col("COD_MUN") == mun)
        self._preencher_hosp(sub.sort("n", descending=True))

    def _preencher_hosp(self, sub: pl.DataFrame) -> None:
        self._bloq = True
        self.cmb_hosp.clear()
        if self._permitir_nenhum:
            self.cmb_hosp.addItem("(nenhum)", NENHUM)
        for r in sub.to_dicts():
            self.cmb_hosp.addItem(_rotulo(r), r["CNES"])
        self._bloq = False
        idx = 1 if (self._permitir_nenhum and self.cmb_hosp.count() > 1) else 0
        self.cmb_hosp.setCurrentIndex(idx)
        self._hosp_mudou(idx)

    def _hosp_mudou(self, _i: int) -> None:
        if self._bloq:
            return
        h = self.hospital()
        if h:
            txt = f"{h['NOME_MUN']} · região de saúde {h['NO_REGSAUD']} · {h['UF']}"
            self.lbl_info.setText(txt); self.cmb_hosp.setToolTip(txt)
        else:
            self.lbl_info.setText(" "); self.cmb_hosp.setToolTip("")
        self.hospitalMudou.emit(self.cnes() or "")

    def _buscar(self, txt: str) -> None:
        t = (txt or "").strip().lower()
        if len(t) < 2:
            self._mun_mudou(0)
            return
        sub = self._hosp.filter(pl.col("NOME").str.to_lowercase().str.contains(t, literal=True) | pl.col("CNES").str.contains(t, literal=True))
        self._preencher_hosp(sub.head(60))

    # ------------------------------------------------------------------
    def cnes(self) -> str | None:
        c = self.cmb_hosp.currentData()
        return None if (c in (None, NENHUM)) else str(c)

    def hospital(self) -> dict | None:
        c = self.cnes()
        if not c or self._hosp.is_empty():
            return None
        r = self._hosp.filter(pl.col("CNES") == c)
        return r.row(0, named=True) if not r.is_empty() else None

    def uf(self) -> str | None:
        return self.cmb_uf.currentData()

    def regiao(self) -> tuple[str, str] | None:
        """(CO_REGSAUD, NO_REGSAUD) da região selecionada."""
        reg = self.cmb_reg.currentData()
        if reg == TODAS_REG:                      # "(todas)": vale a região do hospital escolhido
            h = self.hospital()
            if h and h.get("CO_REGSAUD") not in (None, "?"):
                return str(h["CO_REGSAUD"]), str(h["NO_REGSAUD"])
            return None
        if reg in (None, "?"):
            return None
        return reg, self.cmb_reg.currentText().rsplit("  (", 1)[0]

    def municipios_da_regiao(self) -> list[str]:
        r = self.regiao()
        return municipios_da_regiao(r[0])["COD_MUN"].to_list() if r else []

    def selecionar(self, cnes: str) -> None:
        """Posiciona a cascata no hospital pedido (sem emitir sinais intermediários)."""
        c = str(cnes).zfill(7)
        if self._hosp.is_empty() or "CNES" not in self._hosp.columns:   # ainda sem dados (sinal chegou antes do carregar)
            return
        r = self._hosp.filter(pl.col("CNES") == c)
        if r.is_empty():
            return
        h = r.row(0, named=True)
        self._bloq = True
        self.ed_busca.clear()
        i = self.cmb_uf.findData(h["UF"]); self.cmb_uf.setCurrentIndex(max(0, i))
        self._bloq = False
        self._uf_mudou_silencioso(h)

    def _uf_mudou_silencioso(self, h: dict) -> None:
        # repete a cascata com os sinais bloqueados e só emite no fim
        self._bloq = True
        uf = h["UF"]
        self.cmb_reg.clear()
        na_uf = self._hosp.filter(pl.col("UF") == uf)
        regs = na_uf.group_by(["CO_REGSAUD", "NO_REGSAUD"]).agg(pl.len().alias("k")).sort("NO_REGSAUD")
        self.cmb_reg.addItem(f"(todas as {regs.height} regiões · {na_uf.height} hospitais)", TODAS_REG)
        for r in regs.to_dicts():
            self.cmb_reg.addItem(f"{r['NO_REGSAUD']}  ({r['k']})", r["CO_REGSAUD"])
        self.cmb_reg.setCurrentIndex(max(0, self.cmb_reg.findData(h["CO_REGSAUD"])))
        self.cmb_mun.clear()
        sub = na_uf.filter(pl.col("CO_REGSAUD") == h["CO_REGSAUD"])
        muns = sub.group_by(["COD_MUN", "NOME_MUN"]).agg(pl.len().alias("k")).sort("NOME_MUN")
        self.cmb_mun.addItem(f"(todos os {muns.height} municípios · {sub.height} hospitais)", TODOS_MUN)
        for r in muns.to_dicts():
            self.cmb_mun.addItem(f"{r['NOME_MUN']}  ({r['k']})", r["COD_MUN"])
        self.cmb_mun.setCurrentIndex(max(0, self.cmb_mun.findData(h["COD_MUN"])))
        self.cmb_hosp.clear()
        if self._permitir_nenhum:
            self.cmb_hosp.addItem("(nenhum)", NENHUM)
        for r in sub.filter(pl.col("COD_MUN") == h["COD_MUN"]).sort("n", descending=True).to_dicts():
            self.cmb_hosp.addItem(_rotulo(r), r["CNES"])
        self.cmb_hosp.setCurrentIndex(max(0, self.cmb_hosp.findData(h["CNES"])))
        self._bloq = False
        self.ufMudada.emit(uf)
        self._hosp_mudou(self.cmb_hosp.currentIndex())
