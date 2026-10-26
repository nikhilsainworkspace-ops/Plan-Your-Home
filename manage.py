#!/usr/bin/env python3
"""Operations CLI for Plan Your Home."""
import argparse
import getpass
import sys

from pyh.config import Config
from pyh.db import connect, init_db, transaction
from pyh.security import hash_password, validate_email


def main():
    parser = argparse.ArgumentParser(description="Plan Your Home management commands")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply SQLite schema migrations")
    admin = sub.add_parser("create-admin", help="create or promote the first administrator account")
    admin.add_argument("email", help="administrator email")
    admin.add_argument("--name", default="Site Administrator")
    args = parser.parse_args()

    init_db()
    if args.command == "migrate":
        print("Database migrations applied.")
        return 0

    email = args.email.strip().lower()
    if not validate_email(email):
        print("Invalid email address.", file=sys.stderr)
        return 2

    # Promote an existing, owner-controlled account without prompting for or changing its password.
    with transaction(Config.DATABASE_PATH) as con:
        existing = con.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            user_id = existing["id"]
            con.execute("UPDATE users SET is_admin=1, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?", (user_id,))
            con.execute("INSERT INTO audit_log(actor_user_id,action,subject_type,subject_id,details) VALUES(?,?,?,?,?)", (user_id, "admin.provisioned", "user", str(user_id), "Existing account promoted by management CLI"))
            print(f"Promoted existing account {email} (id={user_id}) to administrator. Password unchanged.")
            return 0

    password = getpass.getpass("Administrator password (12+ characters): ")
    if password != getpass.getpass("Confirm password: "):
        print("Passwords did not match.", file=sys.stderr)
        return 2
    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    with transaction(Config.DATABASE_PATH) as con:
        # Recheck under the write lock to avoid a duplicate if another signup raced this command.
        existing = con.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            user_id = existing["id"]
            con.execute("UPDATE users SET is_admin=1, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?", (user_id,))
            message = f"Promoted existing account {email} (id={user_id}) to administrator. Password unchanged."
        else:
            cur = con.execute("INSERT INTO users(email,full_name,password_hash,is_admin,onboarding_complete) VALUES(?,?,?,?,1)", (email, args.name.strip()[:120] or "Site Administrator", password_hash, 1))
            user_id = cur.lastrowid
            message = f"Created administrator account {email} (id={user_id})."
        con.execute("INSERT INTO audit_log(actor_user_id,action,subject_type,subject_id,details) VALUES(?,?,?,?,?)", (user_id, "admin.provisioned", "user", str(user_id), "Administrator provisioned from CLI"))
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
