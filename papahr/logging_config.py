"""Configuração de logging estruturado com structlog."""
from __future__ import annotations

import io
import logging
import sys
from pathlib import Path
from typing import Optional

import structlog

from papahr.config import get_settings


# Guarda referência dos file handlers abertos para não serem fechados pelo GC.
_OPEN_FILES: list[io.TextIOWrapper] = []


def configure_logging() -> None:
    """Configura o sistema de logging estruturado com structlog.

    - Cria diretórios do Settings se ainda não existirem (ensure_dirs lazy).
    - Escreve em sys.stdout (console, opcionalmente colorido via ConsoleRenderer)
      E em paralelo no arquivo ~/.gsphosp/logs/gsphosp.log (UTF-8, append).
    - WriteLoggerFactory de structlog espera um file-like, não um Path.
    """
    settings = get_settings()
    # Garante que as pastas existam sem depender de side-effect no import do settings.
    try:
        settings.ensure_dirs()
    except Exception:
        # Falha de criação de pasta não deve travar a app (ex: sandbox sem permissão).
        pass

    log_level = getattr(logging, settings.log_level, logging.INFO)
    log_file_path: Optional[Path] = None
    try:
        # Usa nome de arquivo ATUALIZADO (GSPHosp, nao PAPAHU!):
        log_file_path = Path(settings.log_dir) / "gsphosp.log"
        # Mantem o nome antigo papahr.log por compatibilidade temporaria (cria hardlink se SO suportar)
    except Exception:
        log_file_path = None

    processors_console: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        structlog.processors.StackInfoRenderer(),
    ]
    # Renderer para console/stderr/stdout (TTY)
    if sys.stdout.isatty():
        processors_console.append(structlog.dev.ConsoleRenderer())
    else:
        processors_console.append(structlog.processors.format_exc_info)
        processors_console.append(structlog.dev.ConsoleRenderer(colors=False, exception_formatter=structlog.dev.plain_traceback))

    structlog.configure(
        processors=processors_console,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        # Escreve primariamente no stdout (funciona em qualquer ambiente Windows).
        # Obs: WriteLoggerFactory espera file-like, não Path.
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # ------------------------------------------------------------------
    # Logger stdlib (root) — usado por libs externas, exibido no console.
    # ------------------------------------------------------------------
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    # Evita handlers duplicados se configure_logging for chamada 2x.
    if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(logging.Formatter("%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"))
        root_logger.addHandler(stream)

    # ------------------------------------------------------------------
    # Handler de arquivo UTF-8 (append, diário) — para debug pós-execução.
    # Falha de escrita é tolerada (sandboxes/permissões) — não trava a UI.
    # ------------------------------------------------------------------
    if log_file_path is not None:
        try:
            log_file_path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(log_file_path, mode="a", encoding="utf-8", buffering=1)
            _OPEN_FILES.append(fh)
            file_handler = logging.StreamHandler(fh)
            file_handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)-8s] %(name)s %(threadName)s: %(message)s"
            ))
            root_logger.addHandler(file_handler)
        except Exception:
            # Falha em escrever log em disco não é fatal em nenhum cenário.
            pass


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Retorna um logger estruturado para o módulo solicitado.

    Garante que configure_logging já foi rodado pelo menos uma vez.
    """
    if not structlog.is_configured():
        configure_logging()
    return structlog.get_logger(name)

