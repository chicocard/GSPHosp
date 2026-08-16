r"""
Utilitario de LINHA DE COMANDO para DIAGNOSTICO + RESET do login do GSPHosp.

UTILIZACAO (PowerShell normal - fora do sandbox):
    cd D:/papapapa/PaPAHR-Python
    .venv/Scripts/python.exe Resetar-Admin-GSPHosp.py

O QUE FAZ:
    1. Lista todos os usuarios DAS 2 bases: NOVA (~/.gsphosp) e ANTIGA (~/.papahr).
    2. Mostra se admin/GSPHosp@2026 entra na base NOVA.
    3. (OPCIONAL) Migra usuarios da base antiga PAPAHU para a nova GSPHosp.
    4. (PADRAO) RESETA (ou CRIA se nao existir) admin / GSPHosp@2026 como ADMIN.
    5. Mostra credenciais finais e confirma com teste de login via API.
"""
from __future__ import annotations

import sqlite3
import sys
import datetime as dt
import uuid
from pathlib import Path

SEP = "=" * 76
home = Path.home()
DB_NEW = home / ".gsphosp" / "gsphosp_users.db"
DB_OLD = home / ".papahr"  / "papahr_users.db"

DEFAULT_USER = "admin"
DEFAULT_PWD  = "GSPHosp@2026"
DEFAULT_FULL = "Administrador GSPHosp (Reset via script CLI)"

# ---------------------------------------------------------------------------
# Garante imports do papahr (caso o script seja rodado de fora do projeto)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from papahr.auth.user_store import UserStore, UserRole, _HASHER  # noqa: E402


def print_title(s: str) -> None:
    print()
    print(SEP)
    print(f"  {s}")
    print(SEP)


def inspect_db(path: Path, label: str) -> list[dict]:
    """Retorna lista de usuarios encontrados (sem hash, apenas metadados)."""
    print_title(f"BASE {label}")
    print(f"Caminho : {path}")
    print(f"Existe  : {path.exists()}")
    if not path.exists():
        return []
    try:
        con = sqlite3.connect(str(path))
    except Exception as e:
        print(f"[ERRO] sqlite.connect falhou: {type(e).__name__}: {e}")
        print("      -> Provavelmente o GSPHosp esta ABERTO. Feche o app e rode o script novamente.")
        return []
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        tabs = [r[0] for r in cur.fetchall()]
        print(f"Tabelas : {tabs}")
        if 'users' not in tabs:
            return []
        cur = con.execute(
            "SELECT COUNT(*) AS n, UPPER(COALESCE(role,'USER')) AS r FROM users GROUP BY UPPER(COALESCE(role,'USER')) ORDER BY r ASC;"
        )
        rows_role = cur.fetchall()
        total = 0
        print("Contagem por role:")
        for r in rows_role:
            qtd = int(r["n"])
            total += qtd
            print(f"  role = {r['r']:<8} : {qtd} usuario(s)")
        print(f"  role = TOTAL    : {total} usuario(s)")
        out: list[dict] = []
        cols = ['user_id','username','email','full_name','role','is_active','must_change_pwd','last_login_at','created_at']
        cur = con.execute(
            f"SELECT {','.join(cols)}, "
            "(password_hash IS NOT NULL) AS tem_hash, "
            "LENGTH(password_hash) AS len_hash "
            "FROM users ORDER BY created_at ASC;"
        )
        rows = cur.fetchall()
        print(f"\nUsuarios ({len(rows)} registros):")
        for r in rows:
            d = {k: r[k] for k in cols}
            d['tem_hash'] = bool(r['tem_hash'])
            d['len_hash'] = int(r['len_hash'] or 0)
            out.append(d)
            extras = [
                f"username={d['username']!r}",
                f"role={d['role']}",
                f"ativo={bool(d['is_active'])}",
                f"must_change={bool(d['must_change_pwd'])}",
                f"email={d['email']!r}",
                f"hash_ok={d['tem_hash']}/{d['len_hash']}c",
            ]
            print(f"  * {' | '.join(extras)}")
        return out
    finally:
        con.close()


def try_login(path: Path, username: str, password: str) -> tuple[bool, str]:
    """Tenta logar via UserStore oficial. Retorna (ok, mensagem)."""
    try:
        s = UserStore(path)
    except Exception as e:
        return False, f"UserStore init falhou: {type(e).__name__}: {e}"
    try:
        u = s.login(username, password)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        s.close()
    return True, f"SUCESSO role={u.role.value} must_change={u.must_change_pwd}"


def reset_admin_in_db(path: Path) -> tuple[bool, str]:
    """Garante admin/GSPHosp@2026 como ADMIN ativo em path."""
    print_title(f"RESET admin/{DEFAULT_PWD} em {path.name}")
    # Cria diretorios se nao existirem (lazy ensure_dirs)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"mkdir {path.parent} falhou: {e}"
    # 1) Garante tabela users exista
    try:
        s = UserStore(path)
    except Exception as e:
        return False, f"UserStore init: {type(e).__name__}: {e}"
    try:
        existing = s.find_user_by_identity(DEFAULT_USER)
        if existing:
            u = s.force_reset_password(
                existing.user_id, DEFAULT_PWD,
                must_change_pwd=False, to_role=UserRole.ADMIN
            )
            msg = f"ATUALIZADO usuario existente: {u.username} -> role ADMIN, must_change=False"
        else:
            u = s.create_user(
                username=DEFAULT_USER, password_raw=DEFAULT_PWD,
                full_name=DEFAULT_FULL, email=None, role=UserRole.ADMIN,
                must_change_pwd=False,
            )
            msg = f"CRIADO novo usuario: {u.username} (ADMIN)"
    except Exception as e:
        s.close()
        return False, f"reset create/update falhou: {type(e).__name__}: {e}"
    finally:
        s.close()
    ok, login_msg = try_login(path, DEFAULT_USER, DEFAULT_PWD)
    print(f"  -> {msg}")
    print(f"  -> Verificacao login : [{ok}] {login_msg}")
    return ok, login_msg


def migrate_users_if_user_wants(db_old: Path, db_new: Path, users_old: list[dict],
                                *, auto_yes: bool = False) -> bool:
    if not users_old:
        print("\n(nenhum usuario na base antiga para migrar - pulado)")
        return False
    if auto_yes:
        print("\n>>> [AUTO-MIGRATE] Migrar usuarios base ANTIGA -> NOVA: SIM (flag --auto-migrate)")
        resp = "s"
    else:
        try:
            resp = input(
                f"\n>>> Migrar {len(users_old)} usuario(s) da base ANTIGA PaPAHR -> nova GSPHosp? [s/N]: "
            ).strip().lower()
        except EOFError:
            resp = "n"
    if resp not in {"s", "sim", "y", "yes"}:
        print("(migracao cancelada pelo usuario ou modo auto-off)")
        return False
    print_title("MIGRACAO usuarios base ANTIGA -> base NOVA")
    s = UserStore(db_new)
    imported, skipped = 0, 0
    try:
        for u in users_old:
            if s.find_user_by_identity(u['username'] or ""):
                print(f"  SKIP (ja existe username={u['username']})")
                skipped += 1
                continue
            try:
                s.create_user(
                    username=u['username'] or f"migrated_{uuid.uuid4().hex[:6]}",
                    password_raw=DEFAULT_PWD,
                    full_name=u['full_name'] or "Usuario migrado",
                    email=u['email'],
                    role=UserRole((u['role'] or "USER").upper()),
                    must_change_pwd=True,
                )
                print(f"  OK importado: {u['username']} role={u['role']}")
                imported += 1
            except Exception as e:
                print(f"  ERRO {u['username']}: {type(e).__name__}: {e}")
                skipped += 1
    finally:
        s.close()
    print(f"  Migracao concluida: {imported} importados, {skipped} pulados/erros.")
    print(f"  (senha de todos migrados = {DEFAULT_PWD!r}, must_change=True)")
    return True


def banner() -> None:
    print()
    print("+==========================================================================+")
    print("|   GSPHosp - Utilitario de diagnostico e RESET do login (admin padrao)    |")
    print("+==========================================================================+")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="GSPHosp - Reset admin/GSPHosp@2026 + migrar usuarios base PAPAHU antiga (se existir)"
    )
    parser.add_argument(
        "--auto-migrate", action="store_true", default=False,
        help="Responde SIM automaticamente para migrar usuarios da base antiga PAPAHU para a nova GSPHosp (nao pede input)."
    )
    parser.add_argument(
        "--no-pause", action="store_true", default=False,
        help="Nao espera Enter do usuario no final (para ser rodado em batch file 1-clique)."
    )
    args = parser.parse_args()

    banner()
    print(f"PROJECT_ROOT = {PROJECT_ROOT}")
    print(f"HOME do usuario: {home}")
    print(f"CLI flags: --auto-migrate={args.auto_migrate}  --no-pause={args.no_pause}")

    users_new = inspect_db(DB_NEW, "NOVA  GSPHosp    ~/.gsphosp/gsphosp_users.db")
    users_old = inspect_db(DB_OLD, "ANTIGA PaPAHR    ~/.papahr/papahr_users.db")

    print_title("Teste: admin/GSPHosp@2026 consegue LOGAR na base NOVA (atual)?")
    ok, msg = try_login(DB_NEW, DEFAULT_USER, DEFAULT_PWD)
    print(f"  Resultado: [{ok}] {msg}")
    if not ok:
        print("  -> Esperado: FALSO. O reset a seguir vai corrigir.")

    # Migracao opcional
    migrate_users_if_user_wants(DB_OLD, DB_NEW, users_old, auto_yes=bool(args.auto_migrate))

    # Reset padrao (SEMPRE) no DB_NEW
    ok2, msg2 = reset_admin_in_db(DB_NEW)

    print_title("RESUMO FINAL")
    print(f"  [Base NOVA]   admin / {DEFAULT_PWD} login : [{ok2}] {msg2}")
    print()
    print("  >>> PROXIMO PASSO:")
    print("      1. Abra o GSPHosp (duplo clique em Abrir-GSPHosp.bat)")
    print(f"      2. No login: Username = {DEFAULT_USER!r}   Senha = {DEFAULT_PWD!r}")
    print("      3. Pressione Enter ou clique em Entrar. SUCESSO.")
    print()
    if not ok2:
        print("  [ATENCAO] Se reset ainda falhou:")
        print("   -> FECHE o app GSPHosp (se estiver aberto ele trava o SQLite).")
        print("   -> Rode este script novamente em um PowerShell NOVO.")
        sys.exit(1)
    if not args.no_pause:
        try:
            input("Pressione Enter para fechar... ")
        except EOFError:
            pass
    sys.exit(0)
