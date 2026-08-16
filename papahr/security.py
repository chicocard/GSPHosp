"""Módulo de segurança: validação de entradas, sanitização de paths e gerenciamento de segredos."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, List, Optional

import keyring
from cryptography.fernet import Fernet
from pydantic import BaseModel, Field, field_validator, model_validator

from papahr.config import get_settings
from papahr.logging_config import get_logger

log = get_logger(__name__)

_UF_ABBR = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA",
    "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN",
    "RS", "RO", "RR", "SC", "SP", "SE", "TO",
}

_CNES_ID_RE = re.compile(r"^\d{6,7}$")
_COUNTY_ID_RE = re.compile(r"^\d{6,7}$")
_PROC_RE = re.compile(r"^\d{10}$")
_CID_RE = re.compile(r"^[A-Z]\d{2}\.?\d?$")

_SYS_TYPES = {"SIA-PA", "SIH-RD", "SIH-RJ", "SIH-SP"}
_CNES_TYPES = {"ST", "LT", "HB", "EQ"}
_SIGTAP_DETAILS = {
    "Procedimento", "Grupo", "Subgrupo", "Forma de organização",
    "Financiamento", "Rubrica", "CBO", "CID",
}


class DateRange(BaseModel):
    """Intervalo de datas para processamento."""

    year_start: int = Field(..., ge=2000, le=2100)
    month_start: int = Field(..., ge=1, le=12)
    year_end: int = Field(..., ge=2000, le=2100)
    month_end: int = Field(..., ge=1, le=12)

    @model_validator(mode="after")
    def _check_order(self) -> "DateRange":
        start = self.year_start * 100 + self.month_start
        end = self.year_end * 100 + self.month_end
        if start > end:
            raise ValueError("Data inicial deve ser anterior ou igual à data final")
        return self


class PipelineRequest(BaseModel):
    """Requisição de processamento de pipeline com validação rigorosa."""

    information_system: str = Field(..., description="Sistema: SIA-PA, SIH-RD, SIH-RJ, SIH-SP")
    state_abbr: str | list[str] = Field(..., description="Sigla(s) da UF")
    county_id: Optional[list[str]] = Field(default=None, description="Códigos IBGE de município (6 dígitos)")
    health_establishment_id: Optional[list[str]] = Field(default=None, description="Códigos CNES (7 dígitos)")
    save_csv: bool = True
    save_parquet: bool = True
    dbc_dir_path: Optional[Path] = Field(default=None, description="Diretório local com DBCs")
    archive_dir_path: Optional[Path] = Field(default=None, description="Pasta Windows com arquivos .zip/.rar/.7z de CNES/SIA/AIH compactados")
    date_range: Optional[DateRange] = Field(default=None, description="Intervalo de datas (obrigatório para downloads)")

    # --------------------------------------------------------------------- NOVOS CAMPOS (Fases 3A/3B/3C)
    macro_regions: Optional[list[str]] = Field(default=None, description="Macrorregiões de Saúde selecionadas (cascateado UF → Macrorregião)")
    health_regions: Optional[list[str]] = Field(default=None, description="DRS / Regiões de Saúde selecionadas (cascateado Macrorregião → DRS)")
    specific_months: Optional[list[tuple[int, int]]] = Field(default=None, description="Lista (ano, mês) de meses selecionados no modo SAZONAL, ONEMONTH ou presets. Não é só range — pode ser meses fora de ordem (ex: todos os janeiro e dezembro).")
    selected_files: Optional[list[str]] = Field(default=None, description="Paths absolutos de arquivos DBC/DBF/ZIP/RAR selecionados manualmente pelo usuário no Step 1.")

    @field_validator("information_system")
    @classmethod
    def _check_sys(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in _SYS_TYPES:
            raise ValueError(f"Sistema inválido: {v}. Use um de {sorted(_SYS_TYPES)}")
        return v

    @field_validator("state_abbr")
    @classmethod
    def _check_uf(cls, v: str | list[str]) -> list[str]:
        items = [v] if isinstance(v, str) else list(v)
        items = [s.strip().upper() for s in items]
        if "ALL" in items:
            return sorted(_UF_ABBR)
        invalid = [s for s in items if s not in _UF_ABBR]
        if invalid:
            raise ValueError(f"UF inválida(s): {invalid}")
        return sorted(set(items))

    @field_validator("county_id")
    @classmethod
    def _check_county(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return None
        cleaned: List[str] = []
        for cid in v:
            cid = str(cid).strip()
            if len(cid) == 7:
                cid = cid[:6]
            if not _COUNTY_ID_RE.match(cid):
                raise ValueError(f"county_id inválido: {cid}")
            cleaned.append(cid)
        return sorted(set(cleaned))

    @field_validator("health_establishment_id")
    @classmethod
    def _check_cnes(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return None
        for cnes in v:
            if not _CNES_ID_RE.match(str(cnes).strip()):
                raise ValueError(f"health_establishment_id inválido: {cnes}")
        return sorted(set(str(c).strip() for c in v))

    @field_validator("dbc_dir_path", "archive_dir_path")
    @classmethod
    def _check_path(cls, v: Optional[Path]) -> Optional[Path]:
        if v is None:
            return None
        v = Path(v).resolve()
        if not v.exists():
            raise ValueError(f"Diretório não existe: {v}")
        if not v.is_dir():
            raise ValueError(f"Não é um diretório: {v}")
        return v

    @model_validator(mode="after")
    def _check_dates(self) -> "PipelineRequest":
        if (
            self.dbc_dir_path is None
            and self.archive_dir_path is None
            and self.date_range is None
        ):
            raise ValueError(
                "Informe date_range (FTP) OU dbc_dir_path (local DBC) OU "
                "archive_dir_path (pasta com arquivos compactados)."
            )
        return self


def safe_path(base: Path, user_path: str | os.PathLike[str]) -> Path:
    """Sanitiza um path de usuário evitando path traversal.

    Garante que o resultado fique contido em `base`.
    """
    base_resolved = Path(base).resolve()
    candidate = Path(user_path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (base_resolved / candidate).resolve()
    try:
        resolved.relative_to(base_resolved)
    except ValueError as e:
        raise PermissionError(f"Tentativa de path traversal detectada: {user_path}") from e
    return resolved


def validate_cnes_ids(ids: Iterable[str]) -> list[str]:
    """Valida uma lista de códigos CNES."""
    result = []
    for s in ids:
        s = str(s).strip()
        if not _CNES_ID_RE.match(s):
            raise ValueError(f"CNES inválido: {s}")
        result.append(s)
    return result


def validate_procedure_codes(codes: Iterable[str]) -> list[str]:
    """Valida códigos de procedimento SIGTAP (10 dígitos)."""
    result = []
    for c in codes:
        c = str(c).strip()
        if not _PROC_RE.match(c):
            raise ValueError(f"Código de procedimento inválido: {c}")
        result.append(c)
    return result


class SecretManager:
    """Gerencia segredos do app usando keyring + criptografia opcional."""

    SERVICE_NAME = "papahr"

    @classmethod
    def get(cls, key: str) -> Optional[str]:
        """Recupera um segredo."""
        try:
            return keyring.get_password(cls.SERVICE_NAME, key)
        except Exception:
            log.warning("keyring indisponivel, usando env var fallback", key=key)
            return os.environ.get(key.upper())

    @classmethod
    def set(cls, key: str, value: str) -> None:
        """Armazena um segredo."""
        try:
            keyring.set_password(cls.SERVICE_NAME, key, value)
        except Exception:
            log.warning("keyring indisponivel, segredo nao persistido", key=key)

    @classmethod
    def delete(cls, key: str) -> None:
        """Remove um segredo."""
        try:
            keyring.delete_password(cls.SERVICE_NAME, key)
        except Exception:
            pass

    @staticmethod
    def generate_key() -> bytes:
        """Gera uma chave Fernet para criptografia simétrica."""
        return Fernet.generate_key()
