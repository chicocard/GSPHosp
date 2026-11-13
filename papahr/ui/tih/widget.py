"""
widget.py — tela "Taxas municipais de internação" (TIH) do GSPHosp (PySide6).

    from papahr.ui.tih.widget import TaxasMunicipaisWidget
    w = TaxasMunicipaisWidget()
    w.carregar(aih, hospitais, municipios)     # mesmos insumos da tela de atratividade

TIH do município = internações de residentes (em qualquer hospital do dataset) × (12 / meses)
÷ população × 1.000. Mostra histograma com média/mediana/UF, cartões de estatísticas,
tabela ordenável e mapa coroplético da UF (malha IBGE embarcada).

Atenção: os arquivos SIH-RD são por UF do hospital; residentes internados em outra UF
não aparecem. Para hospitais de fronteira, carregue as UFs vizinhas também.
Importar este módulo ANTES de criar o QApplication (QtWebEngine).
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QMessageBox, QSplitter, QToolButton, QVBoxLayout, QWidget

from papahr.ui.mapa.visor import VisorMapa  # noqa: E402
from papahr.dashboard import atratividade as A
from papahr.ui.atratividade.estilo import RotuloElidido, aplicar_compacto, botao_pequeno, rotulo_chave
from papahr.ui.atratividade.grafico import GraficoHistograma
from papahr.ui.atratividade.seletor import SeletorHospital
from papahr.ui.atratividade.tabela import TabelaPolars

COLS = [
    ("NOME", "Município", "txt", 170),
    ("COD_MUN", "IBGE", "txt", 58),
    ("POP", "População", "int", 80),
    ("n", "Intern.*", "int", 68),
    ("taxa", "TIH", "num2", 60),
    ("razao_uf", "÷ UF", "num2", 50),
    ("percentil", "Perc.", "num0", 50),
    ("n_hosp", "No hosp.", "int", 64),
    ("pct_hosp", "% hosp.", "pct", 62),
]


def _f(v, d=1, suf=""):
    if v is None:
        return "—"
    s = f"{float(v):,.{d}f}".replace(",", "§").replace(".", ",").replace("§", ".")
    return s + suf


class TaxasMunicipaisWidget(QWidget):
    municipioSelecionado = Signal(str)
    resumoMudou = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._aih = pl.DataFrame(); self._hosp_ds = pl.DataFrame(); self._mun = pl.DataFrame()
        self._taxas = pl.DataFrame(); self._meses = 12
        self._construir()

    def _construir(self) -> None:
        aplicar_compacto(self)
        raiz = QVBoxLayout(self); raiz.setContentsMargins(4, 3, 4, 3); raiz.setSpacing(3)

        self.painel_cfg = QWidget(); cfg = QVBoxLayout(self.painel_cfg); cfg.setContentsMargins(0, 0, 0, 0); cfg.setSpacing(3)
        # linha 1: hospital em foco (opcional), em cascata
        self.seletor = SeletorHospital(permitir_nenhum=True, horizontal=True)
        cfg.addWidget(self.seletor)
        # linha 2: recorte da comparação e ações
        l2 = QHBoxLayout(); l2.setSpacing(4)
        self.cmb_uf = QComboBox(); self.cmb_uf.setMinimumContentsLength(10); self.cmb_uf.setToolTip("UF cujos municípios são comparados")
        self.cmb_nivel = QComboBox(); self.cmb_nivel.setMinimumContentsLength(12); self.cmb_nivel.setToolTip("Nível de agregação do tipo de internação")
        self.cmb_cat = QComboBox(); self.cmb_cat.setMinimumContentsLength(16); self.cmb_cat.setToolTip("Tipo de internação (ou todas)")
        self.cmb_cat.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_fonte = QComboBox(); self.cmb_fonte.setMinimumContentsLength(9)
        for rot, val in (("ambas", None), ("SIH (SUS)", "SIH"), ("CIHA (não SUS)", "CIHA")):
            self.cmb_fonte.addItem(rot, val)
        self.cmb_fonte.setToolTip("Fonte das internações: SIH = SUS; CIHA = não SUS. Só aparece quando o dataset tem as duas.")
        self.lbl_fonte = rotulo_chave("Fonte"); self.lbl_fonte.hide(); self.cmb_fonte.hide()
        self.btn_csv = botao_pequeno("CSV", "Exporta a tabela de taxas municipais"); self.btn_enq = botao_pequeno("Enquadrar", "Enquadra o mapa na UF")
        for w in (rotulo_chave("UF comparada"), self.cmb_uf, self.lbl_fonte, self.cmb_fonte, rotulo_chave("Tipo"), self.cmb_nivel, rotulo_chave("Categoria"), self.cmb_cat):
            l2.addWidget(w)
        l2.setStretch(7, 1)
        l2.addWidget(self.btn_csv); l2.addWidget(self.btn_enq)
        cfg.addLayout(l2)
        raiz.addWidget(self.painel_cfg)

        # linha de estatísticas (substitui os cartões): 1 linha, com dica explicando cada número
        topo = QHBoxLayout(); topo.setSpacing(4)
        self.lbl_stats = RotuloElidido("—"); self.lbl_stats.setProperty("resumo", True)
        self.btn_cfg = QToolButton(); self.btn_cfg.setText("▴ configuração"); self.btn_cfg.setCheckable(True); self.btn_cfg.setChecked(True)
        self.btn_cfg.setToolTip("Mostra/oculta as linhas de configuração")
        topo.addWidget(self.lbl_stats, 1); topo.addWidget(self.btn_cfg)
        raiz.addLayout(topo)

        self.split = QSplitter(Qt.Orientation.Horizontal, self); raiz.addWidget(self.split, 1)
        esq = QSplitter(Qt.Orientation.Vertical)
        self.hist = GraficoHistograma(unidade="TIH (internações por 1.000 hab./ano)")
        self.hist.setMinimumHeight(120)
        self.tbl = TabelaPolars(COLS, esticar_primeira=False)
        self.tbl.setToolTip("Municípios da UF: clique para localizar no mapa. Intern. = internações/ano de residentes, anualizadas pelo nº de competências; "
                            "÷ UF = taxa do município ÷ taxa da UF; % hosp. = parte das internações do município feitas no hospital em foco.")
        esq.addWidget(self.hist); esq.addWidget(self.tbl); esq.setSizes([260, 400]); esq.setStretchFactor(0, 2); esq.setStretchFactor(1, 3)
        self.split.addWidget(esq)
        self.mapa = VisorMapa(); self.split.addWidget(self.mapa)
        self.split.setSizes([640, 640]); self.split.setStretchFactor(0, 1); self.split.setStretchFactor(1, 1)

        self.btn_cfg.toggled.connect(self._alternar_cfg)
        self.cmb_uf.currentIndexChanged.connect(lambda _i: self.recalcular())
        self.cmb_fonte.currentIndexChanged.connect(lambda _i: self._fonte_mudou())
        self.seletor.hospitalMudou.connect(lambda _c: self.recalcular())
        self.seletor.ufMudada.connect(self._uf_do_seletor)
        self.cmb_nivel.currentIndexChanged.connect(self._nivel_mudou)
        self.cmb_cat.currentIndexChanged.connect(lambda _i: self.recalcular())
        self.tbl.linhaSelecionada.connect(self._linha)
        self.mapa.municipioClicado.connect(self._clique_mapa)
        self.btn_csv.clicked.connect(self._exportar)
        self.btn_enq.clicked.connect(lambda: self.mapa.chamar("enquadrar"))
        self._resumo = ""

    def _alternar_cfg(self, mostrar: bool) -> None:
        self.painel_cfg.setVisible(mostrar)
        self.btn_cfg.setText("▴ configuração" if mostrar else "▾ configuração")

    def resumo(self) -> str:
        return self._resumo

    # ------------------------------------------------------------------
    def carregar(self, aih: pl.DataFrame, hospitais: pl.DataFrame, municipios: pl.DataFrame,
                 populacao: pl.DataFrame | None = None, uf_inicial: str | None = None, cnes_inicial: str | None = None) -> None:
        self._aih_total = aih
        fontes = A.fontes_presentes(aih)
        self.lbl_fonte.setVisible(len(fontes) > 1); self.cmb_fonte.setVisible(len(fontes) > 1)
        if len(fontes) <= 1:
            self.cmb_fonte.blockSignals(True); self.cmb_fonte.setCurrentIndex(0); self.cmb_fonte.blockSignals(False)
        self._aih = A.filtrar_fonte(aih, self.cmb_fonte.currentData()); self._mun = municipios
        if populacao is not None and not populacao.is_empty():
            from papahr.dashboard.populacao import juntar_populacao
            self._mun = juntar_populacao(self._mun, populacao)
        self._hosp_ds = A.hospitais_do_dataset(aih, hospitais, municipios=self._mun)
        self._meses = A.meses_competencia(aih)
        # UFs presentes na tabela de municípios (com população)
        ufs = sorted({str(c)[:2] for c in self._mun["COD_MUN"].cast(pl.Utf8).to_list()})
        self.cmb_uf.blockSignals(True); self.cmb_uf.clear()
        for u in ufs:
            sig = A.UF_SIGLA.get(u, u)
            self.cmb_uf.addItem(f"{sig} — {A.UF_NOME.get(sig, '')}", u)
        self.cmb_uf.blockSignals(False)
        self.cmb_nivel.blockSignals(True); self.cmb_nivel.clear()
        for k, rot in A.niveis_disponiveis(aih):
            self.cmb_nivel.addItem("Todas as internações" if k == "todos" else rot, k)
        self.cmb_nivel.blockSignals(False)
        self._nivel_mudou(0, recalcular=False)
        # UF inicial: a pedida, senão a do 1º hospital, senão a 1ª da lista
        alvo = None
        if uf_inicial:
            alvo = str(uf_inicial)[:2] if str(uf_inicial).isdigit() else next((k for k, v in A.UF_SIGLA.items() if v == str(uf_inicial).upper()), None)
        if alvo is None and not self._hosp_ds.is_empty() and "COD_MUN" in self._hosp_ds.columns:
            alvo = str(self._hosp_ds["COD_MUN"][0])[:2]
        if alvo:
            i = self.cmb_uf.findData(alvo)
            if i >= 0:
                self.cmb_uf.blockSignals(True); self.cmb_uf.setCurrentIndex(i); self.cmb_uf.blockSignals(False)
        self.seletor.blockSignals(True)
        self.seletor.carregar(self._hosp_ds, self._mun, cnes_inicial=cnes_inicial)
        self.seletor.blockSignals(False)
        self.recalcular()

    def selecionar_hospital(self, cnes: str) -> None:
        if str(cnes).zfill(7) != (self.seletor.cnes() or ""):
            self.seletor.selecionar(cnes)

    def _fonte_mudou(self) -> None:
        if not hasattr(self, "_aih_total") or self._aih_total.is_empty():
            return
        self._aih = A.filtrar_fonte(self._aih_total, self.cmb_fonte.currentData())
        self._nivel_mudou(0, recalcular=False)
        self.recalcular()

    def _uf_do_seletor(self, uf: str) -> None:
        """Hospital de outra UF → a UF comparada acompanha."""
        i = self.cmb_uf.findData(uf)
        if i >= 0 and i != self.cmb_uf.currentIndex():
            self.cmb_uf.setCurrentIndex(i)      # dispara recalcular

    def _nivel_mudou(self, _i: int, recalcular: bool = True) -> None:
        nivel = self.cmb_nivel.currentData() or "todos"
        self.cmb_cat.blockSignals(True); self.cmb_cat.clear(); self.cmb_cat.addItem("(todas)", None)
        if nivel != "todos" and not self._aih.is_empty():
            try:
                cats = A.categorias(A.classificar(self._aih, nivel)).head(80)
                for r in cats.to_dicts():
                    self.cmb_cat.addItem(f"{r['categoria']}  ·  {int(r['n']):,}".replace(",", "."), r["categoria"])
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Taxas municipais", str(exc))
        self.cmb_cat.setEnabled(nivel != "todos")
        self.cmb_cat.blockSignals(False)
        if recalcular:
            self.recalcular()

    def recalcular(self) -> None:
        if self._aih.is_empty() or self.cmb_uf.count() == 0:
            return
        uf = self.cmb_uf.currentData(); cnes = self.seletor.cnes()
        nivel = self.cmb_nivel.currentData() or "todos"; cat = self.cmb_cat.currentData()
        try:
            self._taxas = A.taxas_municipais(self._aih, self._mun, uf=uf, nivel=nivel, categorias_sel=[cat] if cat else None,
                                             meses=self._meses, cnes_destaque=cnes)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Taxas municipais", f"Falha ao calcular: {exc}")
            return
        e = A.estatisticas_taxas(self._taxas)
        titulo_cat = cat or ("todas as internações" if nivel == "todos" else self.cmb_nivel.currentText())
        sig = A.UF_SIGLA.get(uf, uf)
        self.lbl_stats.setTexto(
            f"{sig} · {titulo_cat} · TIH da UF {_f(e.get('taxa_uf'), 2)} · mediana {_f(e.get('mediana'), 2)} · "
            f"média {_f(e.get('media'), 2)} (CV {_f((e.get('cv') or 0) * 100, 0, '%')}) · "
            f"P10–P90 {_f(e.get('p10'), 1)}–{_f(e.get('p90'), 1)} ({_f(e.get('razao_p90_p10'), 1, '×')}) · "
            f"sem internação {e.get('n_zero', 0)} de {e.get('n_mun', 0)} municípios · "
            f"{int(e.get('internacoes', 0)):,} internações/ano".replace(",", "."))
        self.lbl_stats.setToolTip(self.lbl_stats.texto() + "\n\nTIH = internações de residentes por 1.000 hab./ano, anualizadas por "
                                  f"{self._meses} competências (fator {A.fator_anual(self._meses):.2f}). CV = coeficiente de variação entre municípios; "
                                  "P10–P90 = decis extremos e a razão entre eles.")
        self._resumo = self.lbl_stats.texto(); self.resumoMudou.emit(self._resumo)
        # histograma
        h = A.histograma(self._taxas)
        nomes = {str(r["COD_MUN"]): str(r["NOME"]) for r in self._taxas.select(["COD_MUN", "NOME"]).to_dicts()}
        destaque = None
        hosp = None
        if cnes:
            r = self._hosp_ds.filter(pl.col("CNES") == cnes)
            if not r.is_empty():
                hd = A.hospital_dict(r.row(0, named=True)); hosp = hd
                m = self._taxas.filter(pl.col("COD_MUN") == hd["cod_mun"])
                if not m.is_empty():
                    destaque = (str(m["NOME"][0]), m["taxa"][0])
        self.hist.desenhar(h, media=e.get("media"), mediana=e.get("mediana"), taxa_uf=e.get("taxa_uf"), destaque=destaque, nomes=nomes)
        # tabela e mapa
        self.tbl.definir(self._taxas)
        pay = A.payload_coropleto(self._taxas, uf=uf, titulo=f"TIH — {sig} · {titulo_cat}", destaque=(hosp or {}).get("cod_mun"), hospital=hosp)
        reg = self.seletor.regiao() if hosp else None
        if reg:
            pay["regiao"] = {"nome": reg[1], "cods": self.seletor.municipios_da_regiao()}
        self.mapa.chamar("setCoropleto", pay)

    def _linha(self, linha: dict) -> None:
        cod = str(linha.get("COD_MUN") or "")
        if cod:
            self.mapa.chamar("destacar", cod)
            self.municipioSelecionado.emit(cod)

    def _clique_mapa(self, cod: str) -> None:
        self.tbl.selecionar_onde("COD_MUN", cod)

    def _exportar(self) -> None:
        if self._taxas.is_empty():
            return
        caminho, _ = QFileDialog.getSaveFileName(self, "Exportar CSV", f"tih_{self.cmb_uf.currentData()}.csv", "CSV (*.csv)")
        if caminho:
            self._taxas.write_csv(Path(caminho), separator=";")
