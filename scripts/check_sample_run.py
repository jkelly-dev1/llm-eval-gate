"""Check SAMPLE_RUN.md's offline captures against fresh runs of the same commands.

SAMPLE_RUN.md is assembled by build_sample_run.py from capture files, and a
capture taken before a fix keeps showing the old output until someone rebuilds
it. Nothing noticed that happening: the demo ran in CI, but whether its output
still MATCHED the document was never compared. This script compares it.

It re-runs `scripts/run_demo.py` and the five gate invocations, and holds each
output to its block in SAMPLE_RUN.md once timestamps, run ids and hashes are
set aside, which is the determinism boundary tests/test_determinism.py pins.
The two real model sections are captures of paid runs and are not re-run here.

It also holds the hand written figures to the captures. Every figure written in
digits in the findings section, and in the README passages that report run
output (the intro's sentence on the real captures, the five controls, "What it
demonstrates" and "What the real runs actually found"), has to appear in a
capture block of SAMPLE_RUN.md, or be listed in DERIVED with the two captured
figures it is computed from. A figure matches by its digits and sign, so
`$0.2023`, `0.2023` and `+0.2023` are the same figure and `-0.106` is not
`0.106`. The findings section of SAMPLE_RUN.md must also be the current
SAMPLE_RUN.findings.md, so an edit to the source that was never rebuilt fails.

It cannot tell which capture line a figure came from. A figure that
appears anywhere in the captures passes, so a small count such as 4 or 60
passes on its own, and a figure swapped for another captured figure is not
caught. Words are not figures: "a quarter of the price" is not checked.

    python scripts/check_sample_run.py     # exit 1 and a diff on any drift
"""

from __future__ import annotations

import difflib
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_sample_run import GATE_INVOCATIONS, TAIL_MARKER, _normalised  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEMO_COMMAND = "python scripts/run_demo.py"
FINDINGS_HEADING = "## What the real model runs found"

#: The README passages that report run output, as (first line, the text that
#: ends the passage).
README_PASSAGES = [
    ("Both real model\ncaptures", "Unless a number"),
    ("Five controls, all implemented here:", "Every decision, verdict"),
    ("## What it demonstrates", "## Claims backed by tests"),
    ("### What the real runs actually found", "Every model id and price"),
]

#: Figures no run prints, each computed here from two figures a capture does
#: print: (numerator, denominator, format). A denominator of None reads the
#: numerator as a rate and prints it as a percent.
DERIVED = {
    "4.2x": ("0.8494", "0.2023", "{:.1f}x"),  # claude-opus-5 sweep over gpt-5.6-terra's
    "1.55": ("1.6452", "1.0588", "{:.2f}"),   # measured dollars over estimated
    "100%": ("1.000", None, "{:.0f}%"),       # a pass rate of 1.000
}

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# A number standing on its own: not part of a model id (gpt-5.6-terra), a
# version (judge.v2), a case id (gc-001) or a longer number.
_FIGURE = re.compile(
    r"(?<![\w.$])(?<!\w-)[+-]?\$?\d+(?:\.\d+)?(?:x\b|%)?(?![\w])(?!-[A-Za-z])(?!\.\d)"
)


def blocks(text: str) -> list[str]:
    return re.findall(r"^```\n(.*?)\n```$", text, flags=re.S | re.M)


def block_after(found: list[str], command: str) -> str:
    """The capture printed under the block that shows `command`."""
    try:
        return found[found.index(command) + 1]
    except (ValueError, IndexError):
        raise SystemExit(f"SAMPLE_RUN.md has no capture under {command!r}") from None


def run(argv: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True)


def compare(name: str, published: str, fresh: str) -> list[str]:
    return list(difflib.unified_diff(
        _normalised(published), _normalised(fresh),
        f"SAMPLE_RUN.md ({name})", f"fresh run ({name})", n=1, lineterm=""))


def figures(text: str) -> list[str]:
    return _FIGURE.findall(_DATE.sub(" ", text))


def digits(figure: str) -> str:
    """The figure without its unit: `$0.2023`, `+0.2023` and `0.2023x` agree."""
    return figure.lstrip("+").replace("$", "").rstrip("x%")


def passage(text: str, start: str, end: str) -> str:
    try:
        first = text.index(start)
        return text[first:text.index(end, first)]
    except ValueError:
        raise SystemExit(f"README.md has no passage from {start!r} to {end!r}") from None


def figure_problems(sample_run: str, findings: str, readme: str) -> list[str]:
    """Every hand written figure that is neither captured nor derived."""
    heading = sample_run.find(FINDINGS_HEADING)
    if heading == -1:
        return [f"SAMPLE_RUN.md has no {FINDINGS_HEADING!r} section"]
    source = re.sub(r"<!--.*?-->\n*", "", findings, flags=re.S).strip()
    problems = []
    if sample_run[heading:].strip() != source:
        problems.append("SAMPLE_RUN.md's findings section is not the current "
                        "SAMPLE_RUN.findings.md; rebuild it")
    captured = {digits(f) for block in blocks(sample_run[:heading])
                for f in figures(block)}
    for figure, (top, bottom, form) in DERIVED.items():
        missing = [f for f in (top, bottom) if f is not None and f not in captured]
        value = float(top) * 100 if bottom is None else float(top) / float(bottom)
        if missing or form.format(value) != figure:
            problems.append(f"derived figure {figure} is not {form} of "
                            f"{top}/{bottom} from the captures")
    # Section numbers in the findings headings are not figures.
    texts = [("SAMPLE_RUN.findings.md", re.sub(r"(?m)^#+ \d+\.", "", source))]
    texts += [("README.md", passage(readme, start, end))
              for start, end in README_PASSAGES]
    for name, text in texts:
        for figure in figures(text):
            if digits(figure) not in captured and figure not in DERIVED:
                problems.append(f"{name}: {figure} is in no capture block of "
                                "SAMPLE_RUN.md and is not in DERIVED")
    return problems


def documents() -> tuple[str, str, str]:
    return tuple((ROOT / name).read_text(encoding="utf-8") for name in
                 ("SAMPLE_RUN.md", "SAMPLE_RUN.findings.md", "README.md"))


def main() -> int:
    sample_run, findings, readme = documents()
    problems = figure_problems(sample_run, findings, readme)
    found = blocks(sample_run)
    drift: list[str] = []
    checked = 0

    demo = run([sys.executable, "scripts/run_demo.py"])
    drift += compare("demo", block_after(found, DEMO_COMMAND), demo.stdout.rstrip("\n"))
    checked += 1

    for slug, _title, command in GATE_INVOCATIONS:
        shown = f'{command} ; echo "exit=$?"'
        args = shlex.split(command.replace("\\\n", " "))[3:]
        gate = run([sys.executable, "-m", "eval_gate.evals.gate", *args])
        output = gate.stdout + gate.stderr
        index = output.find(TAIL_MARKER)
        tail = output[index:] if index != -1 else output
        fresh = f"{tail.rstrip(chr(10))}\nexit={gate.returncode}"
        drift += compare(slug, block_after(found, shown), fresh)
        checked += 1

    if drift:
        print("\n".join(drift))
        print(f"\nSAMPLE_RUN.md does not match a fresh run of {checked} captures. "
              "Rebuild it: bash scripts/rebuild_sample_run.sh ...")
    if problems:
        print("\n".join(problems))
        print(f"\n{len(problems)} hand written figure problem(s)")
    if drift or problems:
        return 1
    print(f"SAMPLE_RUN.md matches fresh runs of all {checked} offline captures "
          "(demo and five gate invocations), and every hand written figure is "
          "captured or derived")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
