"""Module entry point: python -m vdebt_verify runs the verification gate.

Subcommand-style checks: all (the default), dash, imports, and fixtures.
Every check prints its report and exits 0 on pass, 1 on failure. The all check
is vdebt_verify.gate.run_all(), which is also what ``python -m vdebt verify``
invokes through the engine CLI shell.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from vdebt_verify import gate


def build_parser() -> argparse.ArgumentParser:
    """Build the parser with all as the default check."""
    parser = argparse.ArgumentParser(
        prog="vdebt_verify",
        description="Independent verification gate checks.",
    )
    parser.add_argument(
        "check",
        nargs="?",
        default="all",
        choices=("all", "dash", "imports", "fixtures"),
        help="which check to run (default: all)",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="repository root for the dash scan (default: resolved from the package location)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse argv (defaults to sys.argv) and dispatch one check."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = Path(args.root) if args.root else None
    if args.check == "dash":
        return gate.run_dash_check(root)
    if args.check == "imports":
        return gate.run_imports_check()
    if args.check == "fixtures":
        return gate.run_fixtures_check()
    return gate.run_all(root)


if __name__ == "__main__":
    sys.exit(main())
