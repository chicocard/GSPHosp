"""Módulo de autenticação local do PaPAHR.

Arquitetura:
- SQLCipher-safe: criptografia de senhas com Argon2id (OWASP 2026).
- Armazenamento em sqlite3 (gsphosp_users.db, na pasta do perfil do usuário, NÃO no workspace).
- Sessão em memória (AppSession singleton).
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHash

from papahr.config import get_settings
from papahr.logging_config import get_logger

log = get_logger(__name__)


# ------------------------------
# Argon2id: parâmetros OWASP 2026 (memória 64 MB, 3 iterações, 4 threads)
# ------------------------------
_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

_USER_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    user_id           TEXT PRIMARY KEY,
    username          TEXT UNIQUE NOT NULL,
    email             TEXT,
    full_name         TEXT NOT NULL,
    password_hash     TEXT NOT NULL,
    role              TEXT NOT NULL DEFAULT 'USER',
    is_active         INTEGER NOT NULL DEFAULT 1,
    must_change_pwd   INTEGER NOT NULL DEFAULT 0,
    last_login_at     TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users (username);
CREATE INDEX IF NOT EXISTS idx_users_role     ON users (role);
"""


class UserRole(str, Enum):
    ADMIN = "ADMIN"   # 1º usuário criado = ADMIN
    USER = "USER"     # usuário comum
    AUDITOR = "AUDITOR"  # só lê auditoria e dashboards (sem modificar)


@dataclass(slots=True)
class AppUser:
    user_id: str
    username: str
    full_name: str
    email: Optional[str]
    role: UserRole
    is_active: bool
    must_change_pwd: bool
    last_login_at: Optional[str]
    created_at: str

    @property
    def display_name(self) -> str:
        return self.full_name or self.username


@dataclass(slots=True)
class AppSession:
    """Singleton: sessão ativa do app (vazia = usuário deslogado)."""
    current_user: Optional[AppUser] = None
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat())
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


_SESSION = AppSession()


def get_current_session() -> AppSession:
    return _SESSION


def is_authenticated() -> bool:
    with _SESSION._lock:
        return _SESSION.current_user is not None and _SESSION.current_user.is_active


def current_user() -> Optional[AppUser]:
    with _SESSION._lock:
        return _SESSION.current_user


class UserStore:
    """SQLite-backed store de usuários. Pasta segura no perfil do usuário."""

    def __init__(self, path: Optional[Path] = None):
        settings = get_settings().ensure_dirs()
        self._path = Path(path) if path else settings.user_db_path
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.execute("PRAGMA journal_mode = WAL;")
        self._conn.executescript(_USER_TABLE)
        self._conn.commit()

    @property
    def path(self) -> Path:
        return self._path

    # -------------------------- internal --------------------------
    def _row_to_user(self, row: sqlite3.Row) -> AppUser:
        return AppUser(
            user_id=row["user_id"],
            username=row["username"].strip().lower(),
            full_name=row["full_name"],
            email=(row["email"] or None),
            role=UserRole((row["role"] or "USER").upper()),
            is_active=bool(row["is_active"]),
            must_change_pwd=bool(row["must_change_pwd"]),
            last_login_at=(row["last_login_at"] or None),
            created_at=row["created_at"],
        )

    @staticmethod
    def _hash_password(raw: str) -> str:
        if len(raw) < 6:
            raise ValueError("Senha deve ter pelo menos 6 caracteres.")
        return _HASHER.hash(raw)

    @staticmethod
    def _verify_password(raw: str, hashed: str) -> bool:
        try:
            return bool(_HASHER.verify(hashed, raw))
        except (VerifyMismatchError, VerificationError, InvalidHash):
            return False

    # -------------------------- api --------------------------
    def count_users(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM users;")
        return int(cur.fetchone()[0])

    def has_any_admin(self) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM users WHERE UPPER(role) = 'ADMIN' AND is_active = 1 LIMIT 1;"
        )
        return cur.fetchone() is not None

    def create_user(
        self,
        username: str,
        password_raw: str,
        full_name: str,
        email: Optional[str] = None,
        role: UserRole = UserRole.USER,
        must_change_pwd: bool = False,
        is_active: bool = True,
        force_unique: bool = True,
    ) -> AppUser:
        username = (username or "").strip().lower()
        if len(username) < 3:
            raise ValueError("Username deve ter pelo menos 3 caracteres.")
        if not full_name:
            raise ValueError("Nome completo é obrigatório.")
        if force_unique:
            ex = self.get_by_username(username)
            if ex is not None:
                raise ValueError(f"Username '{username}' já existe (escolha outro).")
            if email:
                e_normalize = str(email).strip().lower()
                ex2 = self.get_by_email(e_normalize)
                if ex2 is not None:
                    raise ValueError(f"E-mail '{e_normalize}' já está cadastrado para outro usuário.")
        # 1° usuário = ADMIN
        if self.count_users() == 0:
            role = UserRole.ADMIN
            must_change_pwd = False
        pwd = self._hash_password(password_raw)
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        uid = uuid.uuid4().hex
        self._conn.execute(
            """
            INSERT INTO users (
                user_id, username, email, full_name, password_hash, role,
                is_active, must_change_pwd, last_login_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                uid, username,
                (email or "").strip().lower() or None,
                full_name.strip(),
                pwd, role.value,
                1 if is_active else 0,
                1 if must_change_pwd else 0,
                now, now,
            ),
        )
        self._conn.commit()
        log.info("Novo usuário criado: username=%s role=%s", username, role.value)
        return self.get_by_id(uid)

    def get_by_id(self, user_id: str) -> Optional[AppUser]:
        cur = self._conn.execute("SELECT * FROM users WHERE user_id = ?;", (user_id,))
        row = cur.fetchone()
        return self._row_to_user(row) if row else None

    def get_by_username(self, username: str) -> Optional[AppUser]:
        uname = (username or "").strip().lower()
        cur = self._conn.execute("SELECT * FROM users WHERE username = ?;", (uname,))
        row = cur.fetchone()
        return self._row_to_user(row) if row else None

    def get_by_email(self, email: str) -> Optional[AppUser]:
        eml = (email or "").strip().lower() or None
        if eml is None:
            return None
        cur = self._conn.execute("SELECT * FROM users WHERE LOWER(COALESCE(email,'')) = ? LIMIT 1;", (eml,))
        row = cur.fetchone()
        return self._row_to_user(row) if row else None

    def find_user_by_identity(self, identity: str) -> Optional[AppUser]:
        """Busca usuário por username (primeiro) OU e-mail (fallback)."""
        ident = (identity or "").strip()
        if not ident:
            return None
        u = self.get_by_username(ident)
        if u is not None:
            return u
        if "@" in ident:
            return self.get_by_email(ident)
        return None

    def _rehash_if_needed(self, user_id: str, raw_password: str, stored_hash: str) -> None:
        """Se os parâmetros do Argon2 mudaram desde a criação do hash,
        atualiza-o silenciosamente para o novo formato (sempre mantendo a mesma senha)."""
        try:
            if _HASHER.check_needs_rehash(stored_hash):
                new_hash = _HASHER.hash(raw_password)
                now = dt.datetime.now(dt.timezone.utc).isoformat()
                self._conn.execute(
                    "UPDATE users SET password_hash = ?, updated_at = ? WHERE user_id = ?;",
                    (new_hash, now, user_id),
                )
                self._conn.commit()
                log.info("Hash argon2 atualizado (rehash) para user_id=%s", user_id[:8])
        except Exception:
            # nunca quebre o login por falha de rehash; só loga em debug
            log.debug("check_needs_rehash skipado (biblioteca antiga?)", exc_info=True)

    def force_reset_password(self, username_or_user_id: str, new_password_raw: str,
                             must_change_pwd: bool = False, to_role: Optional[UserRole] = None,
                             create_if_missing: bool = False,
                             full_name: Optional[str] = None,
                             email: Optional[str] = None) -> AppUser:
        """API pública para resetar senha (usado por botão recuperar/script CLI).

        Args:
            username_or_user_id: username ou user_id (uuid 8+ chars)
            new_password_raw: senha texto claro (a ser hasheada Argon2id)
            must_change_pwd: obriga usuario trocar senha proximo login (tela obrigatória)
            to_role: se fornecido, forca o usuario a ter esse role e ativo=1
            create_if_missing: se usuario nao existe, cria um USER (se for o primeiro no DB, vira ADMIN!)
            full_name/email: APENAS para create_if_missing=true (se for criar usuario novo)
        """
        raw_id = (username_or_user_id or "").strip()
        user = (self.get_by_id(raw_id) or self.find_user_by_identity(raw_id))
        if user is None:
            if not create_if_missing:
                raise ValueError(f"Usuário não encontrado: {raw_id}")
            # Cria usuario NOVO (create_if_missing=true)
            log.warning("[force_reset_password create_if_missing=true] Criando user=%r role=%r must_change=%s email=%r name=%r (primeiro? count=%s)",
                        username_or_user_id, (to_role.value if to_role else None),
                        must_change_pwd, email, full_name, self.count_users())
            try:
                # Se PRIMEIRO usuario (count==0 ou nenhum admin = SEMPRE ADMIN por seguranca)
                eh_admin_force = (self.count_users() == 0) or (not self.has_any_admin())
                role_efetivo = to_role or (UserRole.ADMIN if eh_admin_force else UserRole.USER)
                return self.create_user(
                    username=raw_id,
                    password_raw=new_password_raw,
                    full_name=(full_name or raw_id),
                    email=email,
                    role=role_efetivo,
                    must_change_pwd=must_change_pwd,
                    is_active=True,
                    force_unique=True,
                )
            except Exception as exc:
                # Se o usuario existe mas nao encontramos por find_user (ex: email duplicado?)
                log.error("create_if_missing falhou: %s. Tentando get_by_username direto.", exc)
                user = self.get_by_username(raw_id)
                if user is None:
                    raise
        new_hash = self._hash_password(new_password_raw)
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        updates = {"password_hash": new_hash, "updated_at": now,
                   "must_change_pwd": 1 if must_change_pwd else 0}
        if to_role is not None:
            updates["role"] = to_role.value
            updates["is_active"] = 1
        if full_name:
            updates["full_name"] = str(full_name).strip()
        if email:
            updates["email"] = (str(email).strip() or None)
        sets = ", ".join(f"{k} = ?" for k in updates.keys())
        vals = list(updates.values()) + [user.user_id]
        self._conn.execute(f"UPDATE users SET {sets} WHERE user_id = ?;", vals)
        self._conn.commit()
        log.info("Senha resetada: user=%s force_must_change=%s new_role=%s",
                 user.username, must_change_pwd, (to_role.value if to_role else None))
        return self.get_by_id(user.user_id)

    def login(self, username: str, password_raw: str) -> AppUser:
        user = self.find_user_by_identity(username)
        if not user:
            raise ValueError("Usuário ou senha inválidos.")
        if not user.is_active:
            raise ValueError("Usuário inativo — contate o administrador.")
        # busca hash para comparar
        cur = self._conn.execute(
            "SELECT password_hash FROM users WHERE user_id = ?;", (user.user_id,)
        )
        row = cur.fetchone()
        if not self._verify_password(password_raw, row["password_hash"]):
            raise ValueError("Usuário ou senha inválidos.")
        # Atualiza hash se parâmetros Argon2 mudaram (sempre mantendo mesma senha)
        self._rehash_if_needed(user.user_id, password_raw, row["password_hash"])
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE users SET last_login_at = ?, updated_at = ? WHERE user_id = ?;",
            (now, now, user.user_id),
        )
        self._conn.commit()
        with _SESSION._lock:
            _SESSION.current_user = AppUser(
                user_id=user.user_id, username=user.username,
                full_name=user.full_name, email=user.email,
                role=user.role, is_active=True,
                must_change_pwd=user.must_change_pwd,
                last_login_at=now, created_at=user.created_at,
            )
            _SESSION.session_id = uuid.uuid4().hex
            _SESSION.started_at = now
        log.info("Login OK: user=%s session=%s", user.username, _SESSION.session_id)
        return _SESSION.current_user

    def logout(self) -> None:
        with _SESSION._lock:
            u = _SESSION.current_user
            _SESSION.current_user = None
            _SESSION.session_id = uuid.uuid4().hex
        if u is not None:
            log.info("Logout: user=%s", u.username)

    def change_password(self, user_id: str, old_raw: Optional[str], new_raw: str) -> None:
        user = self.get_by_id(user_id)
        if not user:
            raise ValueError("Usuário não encontrado.")
        cur = self._conn.execute(
            "SELECT password_hash FROM users WHERE user_id = ?;", (user_id,)
        )
        row = cur.fetchone()
        # ADMIN pode resetar sem senha antiga? Aqui exigimos (exceto 1° admin ou flag)
        if user.must_change_pwd is False and old_raw is not None:
            if not self._verify_password(old_raw, row["password_hash"]):
                raise ValueError("Senha atual incorreta.")
        elif old_raw is not None and not self._verify_password(old_raw, row["password_hash"]):
            # Permite mudança sem senha apenas se must_change_pwd=True (1º login forçado)
            if not user.must_change_pwd:
                raise ValueError("Senha atual incorreta.")
        new_hash = self._hash_password(new_raw)
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE users SET password_hash = ?, must_change_pwd = 0, updated_at = ? WHERE user_id = ?;",
            (new_hash, now, user_id),
        )
        self._conn.commit()

    def list_users(self) -> list[AppUser]:
        cur = self._conn.execute("SELECT * FROM users ORDER BY created_at ASC;")
        return [self._row_to_user(r) for r in cur.fetchall()]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


_STORE_LOCK = threading.RLock()
_STORE: Optional[UserStore] = None


def get_user_store() -> UserStore:
    """Retorna singleton do UserStore."""
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = UserStore()
    return _STORE
