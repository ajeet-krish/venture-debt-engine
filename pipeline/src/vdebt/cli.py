"""Command line interface for the venture debt engine.

The verify subcommand runs the independent gate: it imports
vdebt_verify.gate, calls gate.run_all(), and exits with that return code.
The import direction is one way: vdebt may import vdebt_verify, and
vdebt_verify never imports vdebt (architecture section 10).
"""

import argparse
import sys
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Build the parser with verify as the default subcommand."""
    parser = argparse.ArgumentParser(
        prog="vdebt",
        description="Venture debt sizing and scheduling engine.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "verify",
        help="run the independent verification gate (default)",
    )
    return parser


def run_verify() -> int:
    """Run vdebt_verify.gate.run_all() and return its exit code.

    The import happens here, not at module import time, so help output and
    other subcommands work even when the gate module is not yet installed.
    """
    try:
        from vdebt_verify import gate
    except ImportError as exc:
        print(f"verify unavailable: cannot import vdebt_verify.gate ({exc})", file=sys.stderr)
        return 1
    return int(gate.run_all())


def main(argv: Sequence[str] | None = None) -> int:
    """Parse argv (defaults to sys.argv) and dispatch to a subcommand.

    Running with no subcommand runs verify, which keeps `python -m vdebt`
    equivalent to `python -m vdebt verify`.
    """
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    command = args.command or "verify"
    if command == "verify":
        return run_verify()
    parser.error(f"unknown command: {command}")
    return 2
