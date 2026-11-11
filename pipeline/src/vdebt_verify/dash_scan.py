"""Repository dash scan implementing architecture section 9.

Banned everywhere with no exemptions: every Unicode category Pd character plus
U+2212, checked per character with unicodedata.category. U+002D HYPHEN-MINUS is
itself category Pd, but section 9 enumerates the non-ASCII Pd codepoints as the
banned set and pins the ASCII double-hyphen rules separately, so U+002D is
handled only by those rules and is never reported as unicode_dash.

ASCII double-hyphen exemptions, exact patterns from section 9:
- prose files (*.md, *.rst, *.txt, LICENSE, CHANGELOG, extensionless): thematic
  break lines and full-pipe table separator rows;
- source and machine config files (*.py, *.ts, *.tsx, *.js, *.json, *.toml,
  *.yml, *.yaml, *.sh, *.html, *.css, *.svg, *.lock, .gitignore, .python-version):
  command-line flag tokens only.

Excluded paths: .git, node_modules, __pycache__, .venv, dist, .pytest_cache,
.ruff_cache, .mypy_cache, and the binary extensions. Files that fail UTF-8
decode are counted and reported as skipped. Any other file extension is a scan
failure (unclassified). Pattern literals are written with \\u002d escapes so
this module passes its own scan. Exit is non-zero on any violation.
"""

import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

PROSE_EXTENSIONS = frozenset({".md", ".rst", ".txt"})
PROSE_NAMES = frozenset({"LICENSE", "CHANGELOG"})
SOURCE_EXTENSIONS = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".json",
        ".toml",
        ".yml",
        ".yaml",
        ".sh",
        ".html",
        ".css",
        ".svg",
        ".lock",
    }
)
SOURCE_NAMES = frozenset({".gitignore", ".python-version"})
EXCLUDED_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "dist",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
    }
)
BINARY_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".pdf", ".zip"}
)

THEMATIC_BREAK_RE = re.compile(r"^\s*-{3,}\s*$")
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$")
# Pinned flag token pattern from section 9. The \\u002d escapes keep the
# literal double hyphen out of this source file so the scanner passes its own
# scan.
FLAG_TOKEN_RE = re.compile("(?<![A-Za-z0-9\u002d])\u002d\u002d[A-Za-z][A-Za-z0-9\u002d]*")
DOUBLE_HYPHEN_RE = re.compile("\u002d\u002d")

RULE_UNICODE_DASH = "unicode_dash"
RULE_DOUBLE_HYPHEN_PROSE = "double_hyphen_prose"
RULE_DOUBLE_HYPHEN_SOURCE = "double_hyphen_source"


@dataclass(frozen=True)
class Violation:
    """One dash scan violation: path, line, column, codepoint, rule id."""

    path: str
    line: int
    column: int
    codepoint: str
    rule_id: str

    def format(self) -> str:
        return f"{self.path}:{self.line}:{self.column}: {self.rule_id}: {self.codepoint}"


@dataclass
class DashReport:
    """Result of one dash scan run over a repository root."""

    root: str
    files_scanned: int = 0
    violations: list[Violation] = field(default_factory=list)
    unclassified: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.violations and not self.unclassified

    def summary(self) -> str:
        return (
            f"{self.files_scanned} files scanned, {len(self.violations)} violations, "
            f"{len(self.unclassified)} unclassified, {len(self.skipped)} skipped"
        )

    def detail_lines(self) -> list[str]:
        lines = [violation.format() for violation in self.violations]
        lines.extend(self.unclassified)
        lines.extend(self.skipped)
        return lines


def default_root() -> Path:
    """Repository root resolved relative to this package's location."""
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "pipeline").is_dir() and (candidate / ".git").exists():
            return candidate
    return here.parents[3]


def classify(path: Path) -> str | None:
    """Return 'prose' or 'source' for a file, or None when unclassified."""
    name = path.name
    ext = path.suffix.lower()
    if name in SOURCE_NAMES:
        return "source"
    if name in PROSE_NAMES:
        return "prose"
    if ext in SOURCE_EXTENSIONS:
        return "source"
    if ext in PROSE_EXTENSIONS or ext == "":
        return "prose"
    return None


def _iter_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in EXCLUDED_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix.lower() in BINARY_EXTENSIONS:
                continue
            found.append(path)
    return found


def _scan_text(text: str, rel: str, kind: str) -> list[Violation]:
    violations: list[Violation] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for column, char in enumerate(line, start=1):
            if char != "-" and (
                unicodedata.category(char) == "Pd" or char == "\u2212"
            ):
                codepoint = f"U+{ord(char):04X}"
                violations.append(
                    Violation(rel, lineno, column, codepoint, RULE_UNICODE_DASH)
                )
        if not DOUBLE_HYPHEN_RE.search(line):
            continue
        if kind == "prose":
            if THEMATIC_BREAK_RE.match(line) or TABLE_SEPARATOR_RE.match(line):
                continue
            for match in DOUBLE_HYPHEN_RE.finditer(line):
                violations.append(
                    Violation(
                        rel, lineno, match.start() + 1, "U+002D", RULE_DOUBLE_HYPHEN_PROSE
                    )
                )
        else:
            exempt = {match.start() for match in FLAG_TOKEN_RE.finditer(line)}
            for match in DOUBLE_HYPHEN_RE.finditer(line):
                if match.start() in exempt:
                    continue
                violations.append(
                    Violation(
                        rel,
                        lineno,
                        match.start() + 1,
                        "U+002D",
                        RULE_DOUBLE_HYPHEN_SOURCE,
                    )
                )
    return violations


def run_dash_scan(root: Path | None = None) -> DashReport:
    """Scan every in-scope file under root (default: repository root)."""
    scan_root = (root if root is not None else default_root()).resolve()
    report = DashReport(root=str(scan_root))
    for path in _iter_files(scan_root):
        rel = path.relative_to(scan_root).as_posix()
        kind = classify(path)
        if kind is None:
            report.unclassified.append(
                f"{rel}: scan failure: unclassified extension '{path.suffix}'"
            )
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            report.skipped.append(f"{rel}: skipped, unreadable ({exc})")
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            report.skipped.append(f"{rel}: skipped, not valid UTF-8")
            continue
        report.files_scanned += 1
        report.violations.extend(_scan_text(text, rel, kind))
    report.violations.sort(key=lambda item: (item.path, item.line, item.column))
    return report
