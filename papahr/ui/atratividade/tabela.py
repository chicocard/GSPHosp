"""
tabela.py — QTableView sobre um DataFrame Polars, com formatação por coluna e ordenação.

    tv = TabelaPolars([("categoria", "Tipo", "txt", 260), ("n", "Internações", "int", 80), ...])
    tv.definir(df)                 # DataFrame Polars
    tv.linhaSelecionada.connect(fn)   # dict da linha (dados originais)

Formatos: txt, int, num1, num2, num3, pct, km, sci (3 significativos), cor (quadrado colorido) — nulos viram "—".
"""
from __future__ import annotations

import csv
from typing import Any, Sequence

import polars as pl
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QAbstractItemView, QFileDialog, QHeaderView, QTableView

ColSpec = tuple[str, str, str, int]   # (chave, cabeçalho, formato, largura px)


def formatar(v: Any, fmt: str) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    try:
        if fmt == "cor":
            return ""
        if fmt == "txt":
            return str(v)
        if fmt == "int":
            return f"{int(round(float(v))):,}".replace(",", ".")
        if fmt == "pct":
            return f"{float(v) * 100:.1f}%".replace(".", ",")
        if fmt == "km":
            return f"{float(v):,.0f} km".replace(",", ".")
        if fmt == "sci":
            return f"{float(v):.3g}".replace(".", ",")
        if fmt.startswith("num"):
            d = int(fmt[3:] or 1)
            s = f"{float(v):,.{d}f}"
            return s.replace(",", "§").replace(".", ",").replace("§", ".")
    except (TypeError, ValueError):
        return str(v)
    return str(v)


class _Modelo(QAbstractTableModel):
    def __init__(self, cols: Sequence[ColSpec]) -> None:
        super().__init__()
        self.cols = list(cols)
        self.linhas: list[dict] = []
        self.negrito: set[int] = set()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.linhas)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.cols)

    def data(self, idx: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not idx.isValid():
            return None
        chave, _, fmt, _ = self.cols[idx.column()]
        v = self.linhas[idx.row()].get(chave)
        if role == Qt.ItemDataRole.DisplayRole:
            return formatar(v, fmt)
        if role == Qt.ItemDataRole.UserRole:        # ordenação numérica
            if v is None:
                return float("-inf") if fmt != "txt" else ""
            return v if fmt == "txt" else float(v)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter) if fmt == "txt" else int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.FontRole and idx.row() in self.negrito:
            f = QFont(); f.setBold(True); return f
        if role == Qt.ItemDataRole.DecorationRole and fmt == "cor" and v:
            return QColor(str(v))
        if role == Qt.ItemDataRole.ToolTipRole and fmt == "txt":
            return str(v) if v is not None else None
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.cols[section][1]
        return None

    def definir(self, linhas: list[dict], negrito: set[int] | None = None) -> None:
        self.beginResetModel()
        self.linhas = list(linhas)
        self.negrito = set(negrito or ())
        self.endResetModel()


class TabelaPolars(QTableView):
    linhaSelecionada = Signal(dict)

    def __init__(self, cols: Sequence[ColSpec], parent=None, esticar_primeira: bool = True) -> None:
        super().__init__(parent)
        self.modelo = _Modelo(cols)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.modelo)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.setModel(self.proxy)
        self.setSortingEnabled(True)
        # sem ordenação inicial: mantém a ordem do DataFrame até o usuário clicar num cabeçalho
        self.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.proxy.sort(-1)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(max(17, self.fontMetrics().height() + 4))   # linhas compactas
        self.verticalHeader().setMinimumSectionSize(16)
        hh = self.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setHighlightSections(False)
        for i, (_, _, _, w) in enumerate(cols):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self.setColumnWidth(i, w)
        if cols and esticar_primeira:
            hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._df = pl.DataFrame()
        self.selectionModel().currentRowChanged.connect(self._mudou)

    def definir(self, df: pl.DataFrame, negrito_onde: str | None = None) -> None:
        self._df = df
        linhas = df.to_dicts() if not df.is_empty() else []
        negrito = {i for i, l in enumerate(linhas) if negrito_onde and l.get(negrito_onde)}
        self.modelo.definir(linhas, negrito)
        if not self.isSortingEnabled():
            self.proxy.sort(-1)          # ordem original do DataFrame

    def dataframe(self) -> pl.DataFrame:
        return self._df

    def linha_atual(self) -> dict | None:
        idx = self.currentIndex()
        if not idx.isValid():
            return None
        return self.modelo.linhas[self.proxy.mapToSource(idx).row()]

    def selecionar_onde(self, chave: str, valor) -> None:
        for i, l in enumerate(self.modelo.linhas):
            if l.get(chave) == valor:
                src = self.modelo.index(i, 0)
                p = self.proxy.mapFromSource(src)
                self.setCurrentIndex(p)
                self.scrollTo(p)
                return

    def _mudou(self, atual: QModelIndex, _ant: QModelIndex) -> None:
        if atual.isValid():
            self.linhaSelecionada.emit(self.modelo.linhas[self.proxy.mapToSource(atual).row()])

    def exportar_csv(self, parent=None, sugestao: str = "tabela.csv") -> str | None:
        caminho, _ = QFileDialog.getSaveFileName(parent or self, "Exportar CSV", sugestao, "CSV (*.csv)")
        if not caminho:
            return None
        with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([c[1] for c in self.modelo.cols])
            for l in self.modelo.linhas:
                w.writerow([formatar(l.get(c[0]), c[2]) for c in self.modelo.cols])
        return caminho
