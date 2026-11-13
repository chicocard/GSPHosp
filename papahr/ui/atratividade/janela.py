"""Janela da análise, com QUATRO abas sempre visíveis:

    Dados · Atratividade por faixas · Market share · Taxas municipais (TIH)

"Dados" escolhe pasta, UF, período e fontes (e baixa do DATASUS o que faltar). As três abas de análise
mostram um aviso ("carregue os dados na aba Dados") até os dados chegarem; depois ficam preenchidas.
O market share é alimentado pela aba de atratividade (hospital em foco, tipo e faixas), mas tem aba própria.
Ao abrir, a última seleção usada é recarregada sozinha (opção na aba Dados), sem trocar de aba.
Carga e download rodam em QThreads; o progresso vai para o registro da aba Dados e para a barra de status.
"""
from __future__ import annotations

import platform
import traceback

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QLabel, QMainWindow, QMessageBox, QProgressBar, QStackedWidget, QStatusBar,
                               QTabWidget, QVBoxLayout, QWidget)

from papahr.dashboard.importar_rd import Carga, Selecao, baixar_datasus, carregar_fonte
from papahr.ui.atratividade.dados import PaginaDados
from papahr.ui.atratividade.estilo import RotuloElidido, aplicar_compacto
from papahr.ui.atratividade.widget import AtratividadeFaixasWidget
from papahr.ui.tih.widget import TaxasMunicipaisWidget

AVISO_SEM_DADOS = ("Sem dados carregados.\n\nNa aba Dados: escolha a pasta, marque a UF, defina o período e as fontes "
                   "e clique em \"Carregar e analisar\" (ou \"Demonstração\" para conhecer as telas).")


def _versoes() -> str:
    import numpy
    import polars
    import PySide6
    return f"Python {platform.python_version()} · polars {polars.__version__} · numpy {numpy.__version__} · PySide6 {PySide6.__version__} · {platform.system()} {platform.release()}"


class CargaWorker(QThread):
    progresso = Signal(str)
    etapa = Signal(int, int)
    pronto = Signal(object)
    erro = Signal(str)

    def __init__(self, caminho: str, selecao: Selecao | None, sem_ibge: bool, uf: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.caminho, self.selecao, self.sem_ibge, self.uf = caminho, selecao, sem_ibge, uf
        self._cancelar = False

    def cancelar(self) -> None:
        self._cancelar = True

    def run(self) -> None:
        try:
            carga = carregar_fonte(self.caminho, uf=self.uf, sem_ibge=self.sem_ibge, progresso=self.progresso.emit,
                                   etapa=self.etapa.emit, cancelado=lambda: self._cancelar, selecao=self.selecao)
            self.pronto.emit(carga)
        except InterruptedError:
            self.erro.emit("cancelada")
        except Exception as e:  # noqa: BLE001
            self.erro.emit(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")


class DownloadWorker(QThread):
    progresso = Signal(str)
    etapa = Signal(int, int)
    pronto = Signal(object)        # (baixados, erros)
    erro = Signal(str)

    def __init__(self, pasta: str, faltantes: list, parent: QWidget | None = None):
        super().__init__(parent)
        self.pasta, self.faltantes = pasta, faltantes
        self._cancelar = False

    def cancelar(self) -> None:
        self._cancelar = True

    def run(self) -> None:
        try:
            r = baixar_datasus(self.faltantes, self.pasta, progresso=self.progresso.emit, etapa=self.etapa.emit,
                               cancelado=lambda: self._cancelar)
            self.pronto.emit(r)
        except InterruptedError:
            self.erro.emit("cancelado")
        except Exception as e:  # noqa: BLE001
            self.erro.emit(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")


class _Aba(QStackedWidget):
    """Página de aba: aviso 'sem dados' (0) ou o widget de análise (1)."""

    def __init__(self, widget: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        aviso = QWidget(); lv = QVBoxLayout(aviso); lv.setContentsMargins(40, 40, 40, 40)
        lab = QLabel(AVISO_SEM_DADOS); lab.setProperty("nota", True); lab.setWordWrap(True); lab.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        lv.addWidget(lab); lv.addStretch(1)
        self.addWidget(aviso); self.addWidget(widget)

    def com_dados(self, sim: bool) -> None:
        self.setCurrentIndex(1 if sim else 0)


class JanelaAtratividade(QMainWindow):
    ABA_DADOS, ABA_ATRAT, ABA_MARKET, ABA_TIH = 0, 1, 2, 3

    def __init__(self, *, cnes_inicial: str | None = None, carregar_ultima: bool | None = None):
        super().__init__()
        aplicar_compacto(self)
        self._cnes_inicial = cnes_inicial
        self._worker: CargaWorker | None = None
        self._dl: DownloadWorker | None = None
        self._settings = QSettings("GSPHosp", "atratividade")
        self.setWindowTitle("GSPHosp — atratividade, market share e taxas municipais")

        self.dados = PaginaDados()
        self.w1 = AtratividadeFaixasWidget(); self.w2 = TaxasMunicipaisWidget()
        self.market = self.w1.destacar_market()                     # aba própria, alimentada pela aba de atratividade
        self.abas = QTabWidget(); self.abas.setDocumentMode(True)
        self.pag_atrat = _Aba(self.w1); self.pag_market = _Aba(self.market); self.pag_tih = _Aba(self.w2)
        self.abas.addTab(self.dados, "Dados")
        self.abas.addTab(self.pag_atrat, "Atratividade por faixas")
        self.abas.addTab(self.pag_market, "Market share")
        self.abas.addTab(self.pag_tih, "Taxas municipais (TIH)")
        self.abas.setTabToolTip(self.ABA_DADOS, "Pasta, UF, período e fontes da análise; download do DATASUS")
        self.abas.setTabToolTip(self.ABA_ATRAT, "Taxas de internação por faixa de distância do hospital em foco, por tipo; modelos de atratividade")
        self.abas.setTabToolTip(self.ABA_MARKET, "Participação dos hospitais nas internações dos residentes de cada faixa de distância do hospital em foco")
        self.abas.setTabToolTip(self.ABA_TIH, "Taxas de internação por município da UF, histograma, tabela e mapa")
        self.w1.hospitalSelecionado.connect(self.w2.selecionar_hospital)
        self.market.escolherPedido.connect(lambda: self.abas.setCurrentIndex(self.ABA_ATRAT))
        self.setCentralWidget(self.abas)

        sb = QStatusBar(); sb.setSizeGripEnabled(True); self.setStatusBar(sb)
        self.lbl_origem = RotuloElidido("nenhum dado carregado"); sb.addWidget(self.lbl_origem, 3)
        self.lbl_status = RotuloElidido(""); sb.addWidget(self.lbl_status, 4)
        self.barra = QProgressBar(); self.barra.setRange(0, 0); self.barra.setMaximumWidth(140); self.barra.hide()
        sb.addPermanentWidget(self.barra)
        self.w1.resumoMudou.connect(self._resumo)

        self.dados.carregarPedido.connect(lambda pasta, sel, sem: self.carregar_selecao(pasta, sel, sem, ir_para_analise=True))
        self.dados.baixarPedido.connect(self.baixar)
        self.dados.demoPedido.connect(self.carregar_demo)
        self.dados.cancelarPedido.connect(self.cancelar)
        self.dados.registrar("-- " + _versoes())

        auto = carregar_ultima if carregar_ultima is not None else self.dados.carregar_ultima()
        if auto and self.dados.pasta() and self.dados.btn_carregar.isEnabled():
            QTimer.singleShot(300, self._carregar_ultima)

    # ------------------------------------------------------------------ ações
    def _carregar_ultima(self) -> None:
        self.dados.registrar("-- recarregando a última seleção (desligue em 'carregar a última seleção ao abrir' se não quiser)…")
        self.carregar_selecao(self.dados.pasta(), self.dados.selecao(), self.dados.sem_ibge(), ir_para_analise=False)

    def carregar_selecao(self, pasta: str, selecao: Selecao | None, sem_ibge: bool, ir_para_analise: bool = True) -> None:
        """Lê os arquivos da seleção (ou o parquet/csv da pasta/arquivo, se selecao for None)."""
        if self._ocupado_agora():
            return
        self._ir = ir_para_analise
        self._ocupado(True, f"carregando {selecao.rotulo() if selecao else pasta}…")
        self.dados.ocupado(True, f"-- carga: {pasta}" + (f" · {selecao.rotulo()}" if selecao else ""))
        self._worker = CargaWorker(pasta, selecao, sem_ibge, parent=self)
        self._worker.progresso.connect(self._progresso)
        self._worker.etapa.connect(self.dados.etapa)
        self._worker.pronto.connect(self._pronto)
        self._worker.erro.connect(self._erro)
        self._worker.start()

    def carregar_caminho(self, caminho: str, uf: str | None = None, sem_ibge: bool = False) -> None:
        """Linha de comando: um arquivo (parquet/csv/dbc) ou pasta sem seleção explícita."""
        if self._ocupado_agora():
            return
        self._ir = True
        self._ocupado(True, f"carregando {caminho}…")
        self.dados.ocupado(True, f"-- carga: {caminho}")
        self._worker = CargaWorker(caminho, None, sem_ibge, uf=uf, parent=self)
        self._worker.progresso.connect(self._progresso)
        self._worker.etapa.connect(self.dados.etapa)
        self._worker.pronto.connect(self._pronto)
        self._worker.erro.connect(self._erro)
        self._worker.start()

    def baixar(self, pasta: str, faltantes: list) -> None:
        if self._ocupado_agora():
            return
        self._ocupado(True, f"baixando {len(faltantes)} arquivo(s) do DATASUS…")
        self.dados.ocupado(True, f"-- download de {len(faltantes)} arquivo(s) para {pasta}")
        self._dl = DownloadWorker(pasta, faltantes, parent=self)
        self._dl.progresso.connect(self._progresso)
        self._dl.etapa.connect(self.dados.etapa)
        self._dl.pronto.connect(self._baixou)
        self._dl.erro.connect(self._erro)
        self._dl.start()

    def cancelar(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancelar(); self.dados.registrar("-- cancelando ao fim do arquivo em leitura…")
        if self._dl and self._dl.isRunning():
            self._dl.cancelar(); self.dados.registrar("-- cancelando ao fim do arquivo em download…")
        self.dados.btn_cancelar.setEnabled(False)

    def carregar_demo(self) -> None:
        from papahr.ui.atratividade.demo_dados import gerar
        aih, hosp, mun = gerar()
        self._ir = True
        self._aplicar(aih, hosp, mun, "DEMONSTRAÇÃO: hospitais fictícios e AIH sintéticas de MG", [])
        self.setWindowTitle("GSPHosp — DEMONSTRAÇÃO com dados sintéticos")

    # ------------------------------------------------------------------ slots
    def _ocupado_agora(self) -> bool:
        if (self._worker and self._worker.isRunning()) or (self._dl and self._dl.isRunning()):
            QMessageBox.information(self, "Aguarde", "Já há uma carga ou um download em andamento (ou cancele na aba Dados).")
            return True
        return False

    def _progresso(self, msg: str) -> None:
        print(msg, flush=True)
        self.lbl_status.setTexto(msg.strip().lstrip("-").strip())
        self.dados.registrar(msg)

    def _pronto(self, carga: Carga) -> None:
        curto = f"{carga.origem} · {carga.descricao.split(';')[0]}"      # "… · UF MG: 1213 hospitais na base do CNES"
        self._aplicar(carga.aih, carga.hospitais, carga.municipios, curto, carga.avisos, dica=carga.descricao)
        self.setWindowTitle(f"GSPHosp — atratividade, market share e taxas municipais · {carga.origem}")
        self._cnes_inicial = None

    def _baixou(self, r) -> None:
        baixados, erros = r
        self._ocupado(False, f"download: {len(baixados)} arquivo(s) ok" + (f", {len(erros)} com erro" if erros else ""))
        self.dados.ocupado(False)
        self.dados.atualizar_inventario()
        if erros:
            self.dados.registrar("-- não baixados:\n   " + "\n   ".join(erros))
            QMessageBox.warning(self, "Download do DATASUS", f"{len(baixados)} arquivo(s) baixado(s); {len(erros)} não:\n\n" + "\n".join(erros[:12])
                                + ("\n…" if len(erros) > 12 else "") + "\n\nCompetências recentes podem ainda não estar publicadas (o DATASUS fecha ~3 meses depois).")
        else:
            self.dados.registrar(f"-- {len(baixados)} arquivo(s) na pasta; pode carregar.")

    def _erro(self, msg: str) -> None:
        cancel = msg in ("cancelada", "cancelado")
        self._ocupado(False, "operação cancelada" if cancel else "falhou")
        self.dados.ocupado(False)
        if cancel:
            self.dados.registrar("-- cancelado. Os arquivos já lidos/baixados ficam; a próxima vez continua de onde parou.")
            return
        self.dados.registrar("ERRO: " + msg)
        self.abas.setCurrentIndex(self.ABA_DADOS)
        QMessageBox.critical(self, "Não consegui concluir", msg[:3000])

    def _aplicar(self, aih, hosp, mun, origem: str, avisos: list[str], dica: str | None = None) -> None:
        self.lbl_status.setTexto("montando as telas…"); self.dados.registrar("-- montando as telas (cálculo inicial)…")
        QApplication.processEvents()          # pinta a mensagem antes do cálculo inicial (que roda nesta thread)
        try:
            self.w1.carregar(aih, hosp, mun, cnes_inicial=self._cnes_inicial)
            self.w2.carregar(aih, hosp, mun, cnes_inicial=self._cnes_inicial)
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"
            self._erro("falha ao montar as telas com os dados carregados:\n" + msg)
            return
        for pg in (self.pag_atrat, self.pag_market, self.pag_tih):
            pg.com_dados(True)
        self.lbl_origem.setTexto(origem); self.lbl_origem.setToolTip(dica or origem)
        if getattr(self, "_ir", True):
            self.abas.setCurrentIndex(self.ABA_ATRAT)
        self._ocupado(False, self.w1.resumo() + (" · " + " | ".join(avisos) if avisos else ""))
        self.dados.ocupado(False)
        self.dados.registrar("-- pronto: " + self.w1.resumo())
        if avisos:
            QMessageBox.information(self, "Avisos da carga", "\n\n".join(avisos))

    def _resumo(self, txt: str) -> None:
        if txt and not (self._worker and self._worker.isRunning()):
            self.lbl_status.setTexto(txt)

    def _ocupado(self, sim: bool, msg: str) -> None:
        self.barra.setVisible(sim)
        self.lbl_status.setTexto(msg)
