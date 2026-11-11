"""Command line interface for the venture debt engine.

The verify subcommand runs the independent gate: it imports
vdebt_verify.gate, calls gate.run_all(), and exits with that return code.
The import direction is one way: vdebt may import vdebt_verify, and
vdebt_verify never imports vdebt (architecture section 10).

The build subcommand runs vdebt.build.run_build and exits with its status:
0 on a clean canonical payload, 1 on any hard failure (rate manifest or
label mismatch, zero stratum count, solver non-convergence, or schema
validation errors, architecture P2.6). Both command modules import lazily
inside their handlers so a broken one cannot take the other down.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """Build the parser with verify as the default subcommand and build beside it."""
    parser = argparse.ArgumentParser(
        prog="vdebt",
        description="Venture debt sizing and scheduling engine.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "verify",
        help="run the independent verification gate (default)",
    )
    build_command = subparsers.add_parser(
        "build",
        help="build payloads/<asof>/book.json and payloads/build_meta.json",
    )
    build_command.add_argument(
        "--output-root",
        type=Path,
        default=None,
        metavar="DIR",
        help="directory that receives the payloads tree (default: the repository payloads directory)",
    )
    build_command.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="scenario.toml path (default: pipeline/config/scenario.toml)",
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


def run_build_command(
    output_root: Path | None, cfg_path: Path | None
) -> int:
    """Run vdebt.build.run_build and map any failure to exit status 1.

    The import happens here like run_verify, so verify, help, and parser
    errors keep working even when the build module is unavailable. Every
    hard failure the build raises (BuildError, RateError, SolverError,
    ValueError from config or schedule guards) is reported on stderr and
    becomes a non-zero exit status instead of a silent pass.
    """
    try:
        from vdebt.build import run_build
    except ImportError as exc:
        print(f"build unavailable: cannot import vdebt.build ({exc})", file=sys.stderr)
        return 1
    try:
        return int(run_build(output_root=output_root, cfg_path=cfg_path))
    except Exception as exc:  # noqa: BLE001
        print(f"build failed: {exc}", file=sys.stderr)
        return 1


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
    if command == "build":
        return run_build_command(args.output_root, args.config)
    parser.error(f"unknown command: {command}")
    return 2
