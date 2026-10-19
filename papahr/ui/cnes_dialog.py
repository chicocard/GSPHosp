"""
cnes_dialog.py — diálogo "Baixar base do CNES" (PySide6).

    from papahr.ui.cnes_dialog import CnesDownloadDialog
    dlg = CnesDownloadDialog(parent)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        pasta = dlg.pasta_resultado   # Path de BASE_DE_DADOS_CNES_AAAAMM pronta para o loader

Lista as competências do FTP numa thread, baixa e extrai noutra, com barra de
progresso e botão Cancelar. Nunca bloqueia a interface.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget)

from papahr.dashboard import cnes_download as cd


class _Listador(QObject):
    pronto = Signal(list)
    erro = Signal(str)

    @Slot()
    def rodar(self):
        try:
            self.pronto.emit(cd.listar_competencias())
        except Exception as e:  # noqa: BLE001
            self.erro.emit(f"{type(e).__name__}: {e}")


class _Baixador(QObject):
    progresso = Signal(int, int, str)
    concluido = Signal(str)
    erro = Signal(str)

    def __init__(self, competencia: str, destino: Path, todas: bool):
        super().__init__()
        self.competencia, self.destino, self.todas = competencia, destino, todas
        self._cancelar = False

    def cancelar(self):
        self._cancelar = True

    @Slot()
    def rodar(self):
        try:
            pasta = cd.baixar_e_extrair(
                self.competencia, self.destino,
                tabelas=None if self.todas else cd.TABELAS_GSPHOSP,
                progresso=lambda a, b, m: self.progresso.emit(int(a), int(b), m),
                cancelado=lambda: self._cancelar,
            )
            self.concluido.emit(str(pasta))
        except InterruptedError:
            self.erro.emit("Download cancelado.")
        except Exception as e:  # noqa: BLE001
            self.erro.emit(f"{type(e).__name__}: {e}")


class CnesDownloadDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, destino: Path | None = None):
        super().__init__(parent)
        self.setWindowTitle("Baixar base do CNES")
        self.setMinimumWidth(520)
        self.pasta_resultado: Path | None = None
        self._thread: QThread | None = None
        self._worker: QObject | None = None

        v = QVBoxLayout(self); v.setSpacing(8)
        intro = QLabel("O GSPHosp precisa da Base de Dados do CNES do DATASUS (nomes, leitos e coordenadas dos "
                       "estabelecimentos). O download tem centenas de MB e é feito uma vez por competência.")
        intro.setWordWrap(True); v.addWidget(intro)

        form = QFormLayout(); form.setSpacing(6)
        self.cmb = QComboBox(); self.cmb.addItem("Consultando o FTP do DATASUS…"); self.cmb.setEnabled(False)
        form.addRow("Competência", self.cmb)
        hl = QHBoxLayout(); hl.setSpacing(6)
        self.ed_destino = QLineEdit(str(destino or cd.pasta_cnes_padrao()))
        self.btn_pasta = QPushButton("Escolher…"); self.btn_pasta.clicked.connect(self._escolher_pasta)
        hl.addWidget(self.ed_destino, 1); hl.addWidget(self.btn_pasta)
        form.addRow("Salvar em", hl)
        self.chk_todas = QCheckBox("Extrair todas as tabelas do CNES (ocupa vários GB)")
        form.addRow("", self.chk_todas)
        v.addLayout(form)

        self.barra = QProgressBar(); self.barra.setRange(0, 100); self.barra.setValue(0); self.barra.setTextVisible(True)
        v.addWidget(self.barra)
        self.lbl = QLabel(""); self.lbl.setWordWrap(True); v.addWidget(self.lbl)

        self.botoes = QDialogButtonBox()
        self.btn_ok = self.botoes.addButton("Baixar", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_cancel = self.botoes.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_ok.setEnabled(False)
        self.botoes.accepted.connect(self._iniciar)
        self.botoes.rejected.connect(self._cancelar)
        v.addWidget(self.botoes)

        self._listar()

    # ---- listagem -------------------------------------------------------------
    def _listar(self):
        self._thread = QThread(self); self._worker = _Listador(); self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.rodar)
        self._worker.pronto.connect(self._listou); self._worker.erro.connect(self._falhou_listar)
        self._worker.pronto.connect(self._thread.quit); self._worker.erro.connect(self._thread.quit)
        self._thread.start()

    @Slot(list)
    def _listou(self, comps: list):
        self.cmb.clear()
        for c in comps:
            self.cmb.addItem(f"{c[4:]}/{c[:4]}  ({c})", c)
        self.cmb.setEnabled(True); self.btn_ok.setEnabled(bool(comps))
        self._marcar_existentes()
        self.cmb.currentIndexChanged.connect(self._marcar_existentes)

    @Slot(str)
    def _falhou_listar(self, msg: str):
        self.cmb.clear(); self.cmb.addItem("FTP indisponível")
        self.lbl.setText(f"Não foi possível listar as competências: {msg}\n"
                         "Verifique a internet ou prepare a pasta do CNES manualmente (ver manual).")

    def _marcar_existentes(self):
        c = self.cmb.currentData()
        if c and cd.base_ja_existe(Path(self.ed_destino.text()), c):
            self.lbl.setText(f"A competência {c} já está em {cd.pasta_destino(Path(self.ed_destino.text()), c)}. "
                             "Clique em Baixar para usá-la sem baixar de novo.")
        else:
            self.lbl.setText("")

    # ---- download -------------------------------------------------------------
    def _escolher_pasta(self):
        p = QFileDialog.getExistingDirectory(self, "Pasta para a base do CNES", self.ed_destino.text())
        if p:
            self.ed_destino.setText(p); self._marcar_existentes()

    def _iniciar(self):
        c = self.cmb.currentData()
        if not c:
            return
        self.btn_ok.setEnabled(False); self.cmb.setEnabled(False); self.btn_pasta.setEnabled(False)
        self._thread = QThread(self)
        self._worker = _Baixador(c, Path(self.ed_destino.text()), self.chk_todas.isChecked())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.rodar)
        self._worker.progresso.connect(self._progresso)
        self._worker.concluido.connect(self._concluiu); self._worker.erro.connect(self._falhou)
        self._worker.concluido.connect(self._thread.quit); self._worker.erro.connect(self._thread.quit)
        self._thread.start()

    @Slot(int, int, str)
    def _progresso(self, feito: int, total: int, msg: str):
        if total > 0:
            self.barra.setRange(0, 100); self.barra.setValue(int(feito * 100 / total))
            if total > 10_000:
                msg = f"{msg} — {feito / 1e6:,.0f} de {total / 1e6:,.0f} MB".replace(",", ".")
        else:
            self.barra.setRange(0, 0)
        self.lbl.setText(msg)

    @Slot(str)
    def _concluiu(self, pasta: str):
        self.pasta_resultado = Path(pasta)
        self.barra.setRange(0, 100); self.barra.setValue(100)
        self.lbl.setText(f"Pronto: {pasta}")
        self.accept()

    @Slot(str)
    def _falhou(self, msg: str):
        self.barra.setRange(0, 100); self.barra.setValue(0)
        self.lbl.setText(msg)
        self.btn_ok.setEnabled(True); self.cmb.setEnabled(True); self.btn_pasta.setEnabled(True)

    def _cancelar(self):
        if isinstance(self._worker, _Baixador) and self._thread and self._thread.isRunning():
            self._worker.cancelar(); self.lbl.setText("Cancelando…")
            return
        self.reject()

    def closeEvent(self, ev):
        if self._thread and self._thread.isRunning():
            if isinstance(self._worker, _Baixador):
                self._worker.cancelar()
            self._thread.quit(); self._thread.wait(5000)
        super().closeEvent(ev)
