"""Every pytest node id quoted in README.md, resolved against the real suite.

A claims table means nothing unless a reviewer can paste any row's node id
into pytest and watch it run, so this resolves all of them, including the
`::shorthand` rows that inherit their file from the node id before them.

    .venv/bin/python scripts/verify_claims_table.py

Exits 1 and names every id that does not resolve.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FULL = re.compile(r"`(tests/[\w./]+\.py::[\w:]+)`")
SHORT = re.compile(r"`(::[\w]+)`")


def collected() -> set[str]:
    proc = subprocess.run(
        [".venv/bin/python", "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return {line.strip() for line in proc.stdout.splitlines() if "::" in line}


def quoted_ids(text: str) -> list[tuple[str, bool]]:
    """Every node id in document order, with each `::shorthand` expanded.

    A shorthand row names a second test in the file most recently named in full,
    which is how the table stays readable; resolving it needs that context rather
    than the string alone.
    """
    ids: list[tuple[str, bool]] = []
    current_file = ""
    for token in re.finditer(r"`(tests/[\w./]+\.py::[\w:]+|::[\w]+)`", text):
        raw = token.group(1)
        if raw.startswith("::"):
            ids.append((f"{current_file}{raw}" if current_file else raw, True))
        else:
            current_file = raw.split("::", 1)[0]
            ids.append((raw, False))
    return ids


def main() -> int:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    ids = quoted_ids(text)
    have = collected()
    missing = [node for node, _short in ids if node not in have]

    short = sum(1 for _node, is_short in ids if is_short)
    print(
        f"README node ids resolved: {len(ids) - len(missing)}/{len(ids)} "
        f"({len(ids) - short} written in full, {short} in ::shorthand)"
    )
    if missing:
        print("\nDO NOT RESOLVE:")
        for node in missing:
            print(f"  {node}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
