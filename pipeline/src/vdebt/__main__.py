"""Module entry point: `python -m vdebt` runs the engine CLI."""

import sys

from vdebt.cli import main

if __name__ == "__main__":
    sys.exit(main())
