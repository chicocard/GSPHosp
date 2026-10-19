"""Configurações centralizadas do aplicativo usando Pydantic Settings."""
from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações globais do PaPAHR."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "GSPHosp"
    app_version: str = "2.0.0"

    data_dir: Path = Field(default_factory=lambda: Path.home() / ".gsphosp" / "data")
    # Cache de dados auxiliares (SIGTAP, CNES, municípios, DBC baixados). Fica no
    # perfil do usuário: o %TEMP% do Windows é limpo periodicamente e apagava o SIGTAP.
    cache_dir: Path = Field(default_factory=lambda: Path.home() / ".gsphosp" / "cache")
    output_dir: Path = Field(default_factory=lambda: Path.cwd() / "output")
    log_dir: Path = Field(default_factory=lambda: Path.home() / ".gsphosp" / "logs")

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    datasus_ftp_host: str = "ftp.datasus.gov.br"
    datasus_ftp_host2: str = "ftp2.datasus.gov.br"
    ftp_timeout: int = 60
    ftp_max_retries: int = 3
    ftp_retry_delay: float = 2.0

    chunk_memory_weight: float = 0.09
    chunk_default_weight: float = 0.15

    ibge_api_base: str = "https://servicodados.ibge.gov.br/api/v1"
    http_timeout: int = 120
    http_max_retries: int = 5

    llm_model_path: Path | None = None
    llm_n_ctx: int = 4096
    llm_n_threads: int = 4
    llm_n_gpu_layers: int = 0
    llm_temperature: float = 0.1
    llm_max_tokens: int = 1024

    ui_theme: Literal["dark_teal", "light_blue", "dark_purple"] = "dark_teal"
    ui_window_width: int = 1280
    ui_window_height: int = 800

    # Caminhos para persistência de usuário, banco de dados ETL e auditoria
    # Por padrão ficam em ~/.gsphosp/ (perfil do usuário Windows: %USERPROFILE%\.gsphosp\)
    # NÃO ficam junto ao código-fonte para evitar commit de dados sensíveis SUS
    duckdb_path: Path = Field(default_factory=lambda: Path.home() / ".gsphosp" / "gsphosp.duckdb")
    user_db_path: Path = Field(default_factory=lambda: Path.home() / ".gsphosp" / "gsphosp_users.db")
    audit_db_path: Path = Field(default_factory=lambda: Path.home() / ".gsphosp" / "gsphosp_audit.db")
    sessions_dir: Path = Field(default_factory=lambda: Path.home() / ".gsphosp" / "sessions")

    custom_cnes_dir: Path | None = Field(
        default=None,
        description=(
            "Quando informado (normalmente detectado via extração de arquivo compactado "
            "do CNES no Wizard Passo 1), sobrescreve o caminho padrão C:\\DICIONARIO_DADOS\\* "
            "e carrega tbEstabelecimento, tbMunicipio, tbLeito, tbTipoEstabelecimento, "
            "tbEstado e tbNaturezaJuridica da pasta customizada."
        ),
    )
    custom_cnes_version: int | None = Field(
        default=None,
        description="AAAAMM da versão do CNES (ex: 202512). Detectado automaticamente do nome da pasta.",
    )

    @field_validator("data_dir", "cache_dir", "output_dir", "log_dir")
    @classmethod
    def _resolve_paths(cls, v: Path) -> Path:
        """Apenas resolve caminhos absolutos — criação de diretórios é lazy
        (chame `ensure_dirs()` no início do pipeline/cli).
        Isso evita PermissionError ao importar `papahr` em ambientes restritos
        (ex.: sandboxes) onde o HOME não é gravável."""
        try:
            return v.resolve()
        except Exception:
            return v

    def ensure_dirs(self) -> "Settings":
        """Cria os diretórios data, cache, output, logs se não existirem."""
        for attr in ("data_dir", "cache_dir", "output_dir", "log_dir"):
            p: Path = getattr(self, attr)
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                import warnings

                warnings.warn(f"Não foi possível criar diretório {p}: {exc}", stacklevel=2)
        return self

    @field_validator("ftp_timeout", "http_timeout")
    @classmethod
    def _positive_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Timeout deve ser positivo")
        return v

    @field_validator("llm_n_threads")
    @classmethod
    def _min_threads(cls, v: int) -> int:
        return max(1, v)

    def save_json(self, path: Path | str | None = None) -> Path:
        """Serializa todas as configurações para um arquivo JSON (configurações do usuário).

        Por padrão salva em ``data_dir / settings.json``.
        Usado pela página SettingsPage (Manual §3.6) para persistir preferências
        que o usuário escolheu (caminhos de pasta, log level, GGUF, etc.).
        """
        import json
        p = Path(path) if path else (self.data_dir / "settings.json")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        raw = self.model_dump(mode="json")
        # Caminhos Path → string (Path não é serializável diretamente JSON puro)
        clean: dict[str, object] = {}
        for k, v in raw.items():
            if isinstance(v, Path):
                clean[k] = str(v)
            elif isinstance(v, list) and v and isinstance(v[0], Path):
                clean[k] = [str(x) for x in v]
            else:
                clean[k] = v
        p.write_text(
            json.dumps(clean, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return p

    @classmethod
    def load_json(cls, path: Path | str) -> "Settings":
        """Carrega configurações de um arquivo JSON salvo via `save_json()`."""
        import json
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retorna a instância singleton das configurações."""
    return Settings()
