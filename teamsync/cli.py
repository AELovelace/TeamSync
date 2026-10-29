"""Command line entry point: ``teamsync <command>``."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from pathlib import Path

from teamsync.config import Settings, load_settings
from teamsync.db import open_database, repo
from teamsync.services import IngestionService
from teamsync.sources import LocalDirectorySource


def _service(settings: Settings) -> IngestionService:
    settings.ensure_dirs()
    conn = open_database(settings.database_path)
    return IngestionService(conn, settings.files_dir)


def cmd_init_db(settings: Settings, _: argparse.Namespace) -> int:
    settings.ensure_dirs()
    conn = open_database(settings.database_path)
    conn.close()
    print(f"database ready at {settings.database_path}")
    return 0


def cmd_ingest_dir(settings: Settings, args: argparse.Namespace) -> int:
    root = Path(args.path)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    svc = _service(settings)
    summary = svc.ingest(LocalDirectorySource(root, source_kind=args.source_kind).iter_files(), "cli")
    for o in summary.outcomes:
        if args.verbose or o.status != "duplicate":
            detail = o.error or (", ".join(o.flags) if o.flags else "")
            print(f"{o.status:9} {o.parse_status or '-':19} {o.name}  {detail}")
    print(
        f"run {summary.run_id}: seen={summary.seen} new={summary.new} duplicate={summary.duplicate} "
        f"parsed={summary.parsed} failed={summary.failed}"
    )
    return 0


def cmd_reparse(settings: Settings, _: argparse.Namespace) -> int:
    svc = _service(settings)
    summary = svc.reparse_all()
    for o in summary.outcomes:
        if o.parse_status != "parsed":
            print(f"{o.parse_status:19} {o.name}  {o.error or ''}")
    print(f"run {summary.run_id}: reparsed={summary.seen} parsed={summary.parsed} failed={summary.failed}")
    return 0


def cmd_stats(settings: Settings, _: argparse.Namespace) -> int:
    svc = _service(settings)
    print(json.dumps(repo.summary(svc.conn), indent=2))
    return 0


def cmd_show(settings: Settings, args: argparse.Namespace) -> int:
    svc = _service(settings)
    entry = repo.get_entry(svc.conn, args.entry_id)
    if entry is None:
        print(f"no entry {args.entry_id}", file=sys.stderr)
        return 1
    head = dict(entry)
    lines = [dict(r) for r in repo.get_lines(svc.conn, args.entry_id)]
    print(json.dumps({"entry": head, "lines": lines}, indent=2))
    return 0


def cmd_list(settings: Settings, args: argparse.Namespace) -> int:
    svc = _service(settings)
    rows = repo.list_entries(
        svc.conn, repo.EntryFilter(flag=args.flag, property_name=args.property, limit=args.limit)
    )
    for r in rows:
        print(
            f"{r['id']:5} {r['tran_date']} {r['property_name_raw'][:32]:32} {r['payroll_id']}.{r['batch_id']} "
            f"lines={r['line_count']:3} debit={r['total_debit']:>12} flags={r['validation_flags']}"
        )
    print(f"{len(rows)} entries")
    return 0


def cmd_serve(settings: Settings, _: argparse.Namespace) -> int:
    from teamsync.web.app import serve

    serve(settings)
    return 0


def cmd_create_user(settings: Settings, args: argparse.Namespace) -> int:
    from teamsync.services import AuthError, AuthService

    svc = _service(settings)
    password = args.password or getpass.getpass("Password: ")
    try:
        AuthService(svc.conn).create_user(args.username, password, args.role)
    except AuthError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"created {args.role} user {args.username}")
    return 0


def cmd_set_password(settings: Settings, args: argparse.Namespace) -> int:
    from teamsync.services import AuthError, AuthService

    svc = _service(settings)
    auth = AuthService(svc.conn)
    user = auth.get_user_by_name(args.username)
    if user is None:
        print(f"no user {args.username}", file=sys.stderr)
        return 1
    password = args.password or getpass.getpass("New password: ")
    try:
        auth.set_password(user["id"], password)
    except AuthError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"password updated for {args.username}; existing sessions signed out")
    return 0


def cmd_create_token(settings: Settings, args: argparse.Namespace) -> int:
    from teamsync.services import AuthError, AuthService

    svc = _service(settings)
    try:
        _id, plain = AuthService(svc.conn).create_device_token(args.name, "cli", args.scope)
    except AuthError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(plain)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="teamsync", description="TeamSync journal-entry ingestion")
    p.add_argument("--data-dir", help="override TEAMSYNC_DATA_DIR")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create or migrate the database").set_defaults(func=cmd_init_db)

    ing = sub.add_parser("ingest-dir", help="ingest every file under a directory")
    ing.add_argument("path")
    ing.add_argument("--source-kind", default="local")
    ing.set_defaults(func=cmd_ingest_dir)

    sub.add_parser("reparse", help="re-run the parser over all stored files").set_defaults(func=cmd_reparse)
    sub.add_parser("stats", help="print counts").set_defaults(func=cmd_stats)

    show = sub.add_parser("show", help="print one entry with its lines as JSON")
    show.add_argument("entry_id", type=int)
    show.set_defaults(func=cmd_show)

    ls = sub.add_parser("list", help="list entries")
    ls.add_argument("--flag")
    ls.add_argument("--property")
    ls.add_argument("--limit", type=int, default=100)
    ls.set_defaults(func=cmd_list)

    sub.add_parser("serve", help="run the web app under uvicorn").set_defaults(func=cmd_serve)

    cu = sub.add_parser("create-user", help="add a web user")
    cu.add_argument("username")
    cu.add_argument("--role", choices=("admin", "viewer"), default="viewer")
    cu.add_argument("--password", help="prompted when omitted")
    cu.set_defaults(func=cmd_create_user)

    sp = sub.add_parser("set-password", help="change a web user's password")
    sp.add_argument("username")
    sp.add_argument("--password", help="prompted when omitted")
    sp.set_defaults(func=cmd_set_password)

    ct = sub.add_parser("create-token", help="mint a device token for the push script; prints it once")
    ct.add_argument("name")
    ct.add_argument(
        "--scope",
        choices=("pusher", "admin"),
        default="pusher",
        help="pusher: hash check and upload only (default); admin: every API route",
    )
    ct.set_defaults(func=cmd_create_token)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    if args.data_dir:
        settings = settings.model_copy(update={"data_dir": Path(args.data_dir)})
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else settings.log_level,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return int(args.func(settings, args))


if __name__ == "__main__":
    raise SystemExit(main())
