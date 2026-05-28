"""Command line interface for OpsMitra."""

from __future__ import annotations

import argparse

from opsmitra import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opsmitra", description="Private AI-assisted log anomaly detector.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Run one detector pass.")
    run_parser.add_argument("--source", choices=("local", "athena"), default="local")
    run_parser.add_argument("--window-minutes", type=int, default=60)
    run_parser.add_argument("--dry-run", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    return 0
