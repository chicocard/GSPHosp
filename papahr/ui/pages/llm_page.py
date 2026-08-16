"""Aba do Assistente LLM."""
from __future__ import annotations

import polars as pl
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTextEdit, QVBoxLayout, QWidget
)

from papahr.llm import LLMAnalyst

class LLMWorker(QThread):
    finished_ok = Signal(str)
    failed = Signal(str)
    
    def __init__(self, analyst: LLMAnalyst, df: pl.DataFrame, question: str):
        super().__init__()
        self.analyst = analyst
        self.df = df
        self.question = question
        
    def run(self) -> None:
        try:
            res = self.analyst.answer_question(self.df, self.question)
            self.finished_ok.emit(res.text)
        except Exception as e:
            self.failed.emit(str(e))

class LlmPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.analyst = LLMAnalyst()
        self.df = pl.DataFrame()
        self._build_ui()
        
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)
        
        title = QLabel("🧠 Assistente LLM Local")
        title.setStyleSheet("font-size:18px;font-weight:700;color:#4FD1C5;")
        root.addWidget(title)
        
        if not self.analyst.available:
            warn = QLabel("⚠️ LLM não configurado.\n\n" + self.analyst.install_hint())
            warn.setStyleSheet("color:#F59E0B;")
            warn.setWordWrap(True)
            root.addWidget(warn)
            root.addStretch()
            return
            
        self.chat_history = QTextEdit()
        self.chat_history.setReadOnly(True)
        self.chat_history.setStyleSheet(
            "background:#121C1E; color:#E6FFFA; border:1px solid #233539; border-radius:8px; padding:8px;"
            "font-size: 14px;"
        )
        root.addWidget(self.chat_history, 1)
        
        input_layout = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Pergunte algo sobre os dados da extração atual...")
        self.input_field.setStyleSheet("padding:10px; border-radius:8px; border:1px solid #4FD1C5; background:#0B1416; color:#FFFFFF;")
        self.input_field.returnPressed.connect(self._send_msg)
        
        self.btn_send = QPushButton("Enviar")
        self.btn_send.setStyleSheet(
            "QPushButton{padding:10px 20px; border-radius:8px; background:#0D9488; color:#FFFFFF; font-weight:700;}"
            "QPushButton:hover{background:#0F766E; color:#FEF08A;}"
        )
        self.btn_send.clicked.connect(self._send_msg)
        
        input_layout.addWidget(self.input_field, 1)
        input_layout.addWidget(self.btn_send, 0)
        root.addLayout(input_layout)
        
    def set_dataset(self, df: pl.DataFrame) -> None:
        self.df = df
        if self.analyst.available:
            self.chat_history.append("<b style='color:#4FD1C5;'>GSPHosp-LLM:</b> Novos dados carregados! O que gostaria de saber sobre eles?<br>")
        
    def _send_msg(self) -> None:
        if not self.analyst.available:
            return
        msg = self.input_field.text().strip()
        if not msg:
            return
            
        self.chat_history.append(f"<b style='color:#6366F1;'>Você:</b> {msg}<br>")
        self.input_field.clear()
        
        if self.df.is_empty():
            self.chat_history.append("<b style='color:#F59E0B;'>LLM:</b> Nenhum dado carregado. Por favor, execute o Wizard Pipeline primeiro.<br>")
            return
            
        self.btn_send.setEnabled(False)
        
        self.worker = LLMWorker(self.analyst, self.df, msg)
        self.worker.finished_ok.connect(self._on_llm_ok)
        self.worker.failed.connect(self._on_llm_fail)
        self.worker.start()
        
    def _on_llm_ok(self, ans: str) -> None:
        ans_html = ans.replace('\n', '<br>')
        self.chat_history.append(f"<br><b style='color:#4FD1C5;'>GSPHosp-LLM:</b> {ans_html}<br><br>")
        self.btn_send.setEnabled(True)
        
    def _on_llm_fail(self, err: str) -> None:
        self.chat_history.append(f"<b style='color:#EF4444;'>Erro LLM:</b> {err}<br>")
        self.btn_send.setEnabled(True)
