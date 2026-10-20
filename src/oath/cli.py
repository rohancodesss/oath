"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from oath import __version__
from oath.devices import android_devices, check_tools, ios_devices
from oath.model import ReadResult


def doctor() -> int:
    """Print tool and phone status. Exit 1 if a required tool is missing."""
    tools = check_tools()
    for tool in tools:
        print(f"{'ok     ' if tool.found else 'MISSING'} {tool.name}  {tool.path or ''}".rstrip())
    print(f"android devices: {android_devices() or 'none'}")
    print(f"ios devices:     {ios_devices() or 'none'}")
    return 0 if all(t.found for t in tools) else 1


def _summary(label: str, result: ReadResult) -> None:
    print(f"{label}: {len(result.chats)} chats, {result.message_count} messages, {result.media_count} media")
    for chat in result.chats:
        name = chat.subject or chat.jid
        print(f"  - {name}: {len(chat.messages)} messages")
    for warning in result.warnings:
        print(f"  ! {warning}")


def emulate(dest: Path, key_hex: str) -> int:
    from oath.emulate.android import build_android_fixture
    from oath.emulate.ios import build_ios_fixture

    wa_root = build_android_fixture(dest / "android", key_hex)
    build_ios_fixture(dest / "ios-backup")
    print(f"android WhatsApp dir: {wa_root}")
    print(f"iPhone backup:        {dest / 'ios-backup'}")
    print(f"64-digit key:         {key_hex}")
    return 0


def inspect(args: argparse.Namespace) -> int:
    if args.platform == "android":
        from oath.android.reader import load_android

        _summary("android", load_android(args.path, args.key))
    else:
        from oath.ios.backup import IOSBackup
        from oath.ios.reader import load_ios

        _summary("ios", load_ios(IOSBackup(args.path)))
    return 0


def schema(args: argparse.Namespace) -> int:
    from oath.schema import android_schema, ios_schema

    text = android_schema(args.path, args.key) if args.platform == "android" else ios_schema(args.path)
    if args.out:
        args.out.write_text(text + "\n")
        print(f"wrote {args.out} ({len(text.splitlines())} lines, no message content)")
    else:
        print(text)
    return 0


def convert(args: argparse.Namespace) -> int:
    from oath.convert import android_to_ios

    result = android_to_ios(args.android, args.key, args.ios_backup, args.out, args.dry_run)
    _summary("source", result.source)
    if args.dry_run:
        print("dry run: nothing written")
        return 0
    w = result.write
    print(f"written to {result.output}: {w.chats_added} chats added, {w.chats_merged} merged, "
          f"{w.messages_added} messages, {w.media_files} media files")
    for warning in w.warnings:
        print(f"  ! {warning}")
    for problem in result.problems:
        print(f"  PROBLEM: {problem}")
    return 0 if result.ok else 1


def search_build(args: argparse.Namespace) -> int:
    from oath.android.reader import load_android
    from oath.ios.backup import IOSBackup
    from oath.ios.reader import load_ios
    from oath.search import build_search_db

    result = load_android(args.android, args.key) if args.android else load_ios(IOSBackup(args.ios_backup))
    n = build_search_db(result, args.out)
    print(f"indexed {n} messages across {len(result.chats)} chats into {args.out}")
    return 0


def search_query(args: argparse.Namespace) -> int:
    from oath.search import search

    hits = search(args.db, " ".join(args.query), chat=args.chat)
    for h in hits:
        print(f"[{h.date}] {h.chat} | {h.sender}: {h.text}")
    print(f"\n{len(hits)} match(es)")
    return 0


def search_gui(args: argparse.Namespace) -> int:
    from oath.gui import serve

    serve(args.db, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oath", description="OATH Allows Total History-transfer.")
    parser.add_argument("--version", action="version", version=f"oath {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("doctor", help="check required tools and connected phones")

    p = sub.add_parser("emulate", help="generate synthetic Android and iPhone data for testing")
    p.add_argument("dest", type=Path)
    p.add_argument("--key", default=None, help="64-hex key (default: fixed test key)")

    p = sub.add_parser("inspect", help="summarise an Android WhatsApp dir or an iPhone backup")
    p.add_argument("platform", choices=["android", "ios"])
    p.add_argument("path", type=Path)
    p.add_argument("--key", help="64-digit key (android only)")

    p = sub.add_parser("schema", help="content-free structure report, safe to share")
    p.add_argument("platform", choices=["android", "ios"])
    p.add_argument("path", type=Path)
    p.add_argument("--key", help="64-digit key (android only)")
    p.add_argument("--out", type=Path)

    p = sub.add_parser("convert", help="Android -> iPhone; writes to a copy of the iPhone backup")
    p.add_argument("--android", type=Path, required=True, help="the WhatsApp dir containing Databases/ and Media/")
    p.add_argument("--key", required=True, help="64-digit backup key")
    p.add_argument("--ios-backup", type=Path, required=True)
    p.add_argument("--out", type=Path, help="new backup directory (must not exist)")
    p.add_argument("--dry-run", action="store_true", help="read and report only")

    p = sub.add_parser("search-build", help="build a local full-text search database from your chat history")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--android", type=Path, help="the WhatsApp dir containing Databases/ and Media/")
    src.add_argument("--ios-backup", type=Path, help="an unencrypted iPhone backup")
    p.add_argument("--key", help="64-digit backup key (android only)")
    p.add_argument("--out", type=Path, required=True, help="search database path to create")

    p = sub.add_parser("search", help="full-text search a database built with search-build")
    p.add_argument("db", type=Path)
    p.add_argument("query", nargs="+")
    p.add_argument("--chat", help="limit to one chat name")

    p = sub.add_parser("search-gui", help="local-only web GUI over a search database")
    p.add_argument("db", type=Path)
    p.add_argument("--port", type=int, default=8765)

    args = parser.parse_args(argv)

    if args.command == "doctor":
        return doctor()
    if args.command == "emulate":
        from oath.emulate.sample import DEFAULT_KEY_HEX

        return emulate(args.dest, args.key or DEFAULT_KEY_HEX)
    if args.command == "inspect":
        if args.platform == "android" and not args.key:
            parser.error("inspect android requires --key")
        return inspect(args)
    if args.command == "schema":
        if args.platform == "android" and not args.key:
            parser.error("schema android requires --key")
        return schema(args)
    if args.command == "convert":
        if not args.dry_run and args.out is None:
            parser.error("convert requires --out unless --dry-run is set")
        return convert(args)
    if args.command == "search-build":
        if args.android and not args.key:
            parser.error("search-build --android requires --key")
        return search_build(args)
    if args.command == "search":
        return search_query(args)
    if args.command == "search-gui":
        return search_gui(args)

    from oath.app import OathApp

    OathApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
