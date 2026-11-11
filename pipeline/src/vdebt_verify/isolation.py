"""Import isolation check for the verify package (architecture section 10).

Static half: every Python file directly under pipeline/src/vdebt_verify/ is
scanned for engine import statements. The pattern is assembled from string
fragments at runtime so this module's own source cannot match it. The pattern
only matches a line whose first non-space token is ``from`` or ``import``
followed by the engine package name and a boundary, so ``vdebt_verify`` imports
do not match.

Runtime half: a snapshot delta check over sys.modules. The first check in a
process acts as the gate entry: it records every key equal to the engine
package name or starting with it followed by a dot as the launcher baseline,
because the canonical command ``python -m vdebt verify`` loads vdebt and
vdebt.cli before the gate starts. Every later check (gate end, or any other
check point) fails only when a NEW key appears that is not in the baseline,
meaning the gate run itself loaded engine code. A violation is returned as a
failure by run_isolation_check and raised as IsolationError by assert_isolation.

Standard library only; this module never imports the engine.
"""

import re
import sys
from pathlib import Path

_ENGINE_PACKAGE = "vdebt"
_IMPORT_KEYWORDS = ("from", "import")
# Assembled from fragments at runtime, per section 10, so the scanner cannot
# match its own source.
_STATIC_PATTERN_SRC = (
    r"^\s*(?:" + "|".join(_IMPORT_KEYWORDS) + r")\s+" + _ENGINE_PACKAGE + r"(?:\s|\.|$)"
)
_STATIC_PATTERN = re.compile(_STATIC_PATTERN_SRC)

# Launcher baseline for the runtime delta check: engine package keys present at
# gate entry (None until the first check in the process records them).
_launcher_baseline: set[str] | None = None


class IsolationError(RuntimeError):
    """Raised when the engine import isolation check fails."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__("; ".join(messages))
        self.messages = messages


def package_dir() -> Path:
    """Directory holding the verify package sources."""
    return Path(__file__).resolve().parent


def _static_findings() -> list[str]:
    findings: list[str] = []
    for path in sorted(package_dir().glob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            findings.append(f"vdebt_verify/{path.name}: unreadable for static scan ({exc})")
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _STATIC_PATTERN.match(line):
                findings.append(f"vdebt_verify/{path.name}:{lineno}: engine import: {line.strip()}")
    return findings


def _engine_keys() -> set[str]:
    """Engine package keys currently present in sys.modules."""
    return {
        key
        for key in sys.modules
        if key == _ENGINE_PACKAGE or key.startswith(_ENGINE_PACKAGE + ".")
    }


def record_baseline() -> set[str]:
    """Record the current engine keys as the launcher baseline.

    Called at gate entry: the canonical command ``python -m vdebt verify``
    loads vdebt and vdebt.cli before the gate starts, so those keys are
    expected and trusted as pre-existing. Returns the recorded baseline.
    """
    global _launcher_baseline
    _launcher_baseline = _engine_keys()
    return set(_launcher_baseline)


def _runtime_findings() -> list[str]:
    """Delta check: engine keys loaded since the launcher baseline.

    The first check in a process records the baseline (gate entry) and
    reports nothing. Every later check reports only keys that are new since
    that snapshot, that is, engine modules loaded during the gate run.
    """
    if _launcher_baseline is None:
        record_baseline()
        return []
    new_keys = sorted(_engine_keys() - _launcher_baseline)
    if not new_keys:
        return []
    return ["engine package keys loaded during the gate run: " + ", ".join(new_keys)]


def run_isolation_check() -> tuple[bool, list[str]]:
    """Run both halves; return pass/fail plus detail messages.

    The first call in a process acts as gate entry and records the launcher
    baseline; later calls fail only on engine keys new since that snapshot.
    """
    messages = _static_findings() + _runtime_findings()
    return (not messages), messages


def assert_isolation() -> None:
    """Strict form: raise IsolationError when either half fails.

    Uses the same snapshot delta semantics as run_isolation_check.
    """
    passed, messages = run_isolation_check()
    if not passed:
        raise IsolationError(messages)
