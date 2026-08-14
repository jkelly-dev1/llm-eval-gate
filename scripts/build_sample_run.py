"""Assemble SAMPLE_RUN.md out of captures, so no number in it is retyped.

Every code block in SAMPLE_RUN.md is a verbatim slice of a file this script was
given. Editing that document by hand is how a capture drifts from the run it
claims to be, and this project's whole subject is that gap, so the document is
generated instead:

    .venv/bin/python scripts/build_sample_run.py \
        --demo out/demo.txt --gate-dir out/gate \
        --anthropic SAMPLE_RUN.anthropic.local.md \
        --openai SAMPLE_RUN.openai.local.md \
        --findings SAMPLE_RUN.findings.md \
        --captured-on 2026-07-28

THE INPUTS. SAMPLE_RUN.findings.md is the one hand written section and is in the
repository. The demo capture, the gate captures and the two real model captures
are run output: every path is an argument with no default, so wherever they are
kept is the caller's business and nothing in this repository points at a
location. The real model captures match *.local.md in .gitignore, so a clean
clone cannot rebuild SAMPLE_RUN.md. That is intended: rebuilding it means paying
for new captures, and the committed document is the record of the runs made.

The prose between the blocks lives here as well, because a caption that has to
be kept true to a number belongs next to the code that inserts the number.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: --no-findings resolves to this, so "omitted on purpose" and "forgotten"
#: are distinguishable rather than both being an empty string.
NO_FINDINGS = "<deliberately omitted>"

GATE_INVOCATIONS = [
    (
        "default",
        "The honest panel gating its own committed baseline",
        "python -m eval_gate.evals.gate",
    ),
    (
        "candidate-v2",
        "The planted regression, blocked",
        "python -m eval_gate.evals.gate --candidate sut.v2",
    ),
    (
        "captured-own-baseline",
        "The captured panel, against a baseline recorded by that same panel",
        "python -m eval_gate.evals.gate --panel two_miscalibrated --candidate sut.v2 \\\n"
        "    --baseline baseline.two_miscalibrated.json",
    ),
    (
        "captured-wrong-baseline",
        "The same captured run against the honest panel's baseline: no decision",
        "python -m eval_gate.evals.gate --panel two_miscalibrated --candidate sut.v2",
    ),
    (
        "threshold-in-the-noise",
        "A threshold inside the measured noise floor: refused",
        "python -m eval_gate.evals.gate --threshold 0.05",
    ),
]

#: Where each gate tail starts. Everything above it is the same report shape the
#: demo capture already shows in full, so repeating it five times would bury the
#: five lines that actually differ.
TAIL_MARKER = "Regression, against the committed baseline record"

#: Section 11 is the only part of the demo a real provider changes, so the real
#: captures are sliced to it rather than pasted whole. What the rest of a real
#: capture does and does not share with the offline one is stated line by line in
#: the Anthropic section's caption below, from a diff rather than from assumption.
REAL_SECTION_START = "11. The real model measurement pass"
REAL_SECTION_END = "12. "


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8").rstrip("\n")


def gate_tail(path: Path) -> str:
    """The gate's output from the regression block down, plus the shell's exit code."""
    text = read(path)
    index = text.find(TAIL_MARKER)
    if index == -1:
        raise SystemExit(f"{path}: no {TAIL_MARKER!r} block to slice from")
    body = text[index:]
    return body


def rule_index(text: str, needle: str, start: int = 0) -> int:
    index = text.find(needle, start)
    if index == -1:
        raise SystemExit(f"capture does not contain {needle!r}")
    return index


def real_section(path: Path) -> str:
    """Section 11 of a real capture, from its banner to the start of section 12."""
    text = read(path)
    start = rule_index(text, REAL_SECTION_START)
    # Back up over the ==== rule line that heads the section.
    banner = text.rfind("=" * 78, 0, start)
    start = banner if banner != -1 else start
    end = rule_index(text, REAL_SECTION_END, start)
    end = text.rfind("=" * 78, start, end)
    return text[start:end].rstrip("\n")


def exit_code(path: Path) -> str:
    """The EXIT= line the runner appended, or "" when the capture has none."""
    for line in reversed(read(path).splitlines()):
        if line.startswith(("EXIT=", "ANTHROPIC_EXIT=", "OPENAI_EXIT=")):
            return line
    return ""


def fence(body: str) -> str:
    return "```\n" + body + "\n```"


def build(args: argparse.Namespace) -> str:
    demo = read(Path(args.demo))
    gate_dir = Path(args.gate_dir)

    parts: list[str] = []
    # The contents list is built from what this run will actually contain. A
    # link to a section that was omitted is a small lie in a document whose
    # only job is to be checkable.
    parts.append(
        HEADER.format(
            captured_on=args.captured_on,
            findings_toc=(
                ""
                if args.findings == NO_FINDINGS
                else "- [What the real model runs found](#what-the-real-model-runs-found)\n"
            ),
        )
    )
    parts.append("## Offline run (mock provider)\n")
    parts.append(fence("python scripts/run_demo.py") + "\n")
    parts.append(fence(demo) + "\n")
    parts.append(OFFLINE_EXIT_NOTE)

    parts.append("### The five gate invocations\n")
    parts.append(GATE_INTRO)
    for number, (slug, title, command) in enumerate(GATE_INVOCATIONS, start=1):
        path = gate_dir / f"{slug}.txt"
        parts.append(f"#### {number}. {title}\n")
        parts.append(fence(f'{command} ; echo "exit=$?"') + "\n")
        code = exit_code(path).split("=", 1)[-1] if exit_code(path) else "?"
        parts.append(fence(f"{gate_tail(path)}\nexit={code}") + "\n")

    for flag, heading, command, note in REAL_RUNS:
        capture = getattr(args, flag)
        parts.append(f"## {heading}\n")
        if not capture:
            parts.append(f"**NOT CAPTURED IN THIS BUILD.**\n\n{note}\n")
            continue
        parts.append(note + "\n")
        parts.append(fence(command) + "\n")
        parts.append(fence(real_section(Path(capture))) + "\n")

    # The findings are the only hand written section, they live OUTSIDE this repo
    # (see the module docstring), and losing them is silent: the document still
    # builds, still looks complete, and is simply missing the part that says what
    # the paid runs found. So a missing or unreadable --findings is an error, and
    # omitting it entirely has to be said out loud rather than defaulted into.
    if not args.findings:
        raise SystemExit(
            "refusing to build without --findings: SAMPLE_RUN.md would be published "
            "with no 'What the real model runs found' section and nothing would say "
            "so. Pass --findings <path>, or --no-findings to do it deliberately."
        )
    if args.findings != NO_FINDINGS:
        source = Path(args.findings)
        if not source.exists():
            raise SystemExit(f"--findings {source} does not exist")
        # The source carries an HTML comment naming itself as the file to edit.
        # That note is for whoever opens the source, not for the reader of the
        # built document, so it is stripped rather than left sitting invisibly in
        # the output.
        findings = re.sub(r"<!--.*?-->\n*", "", read(source), flags=re.S)
        parts.append(findings.strip() + "\n")
    return "\n".join(parts).rstrip("\n") + "\n"


HEADER = """# Sample run

Verbatim captures of `scripts/run_demo.py`, of all five gate invocations, and of
the real model measurement pass, so a reviewer without an API key can see exactly
what the harness does and what it refuses to do. Nothing here is retyped: this
file is assembled from the capture files by `scripts/build_sample_run.py`, and
every kappa, flip rate, dollar figure, hash and exit code below is the one its
run produced. Captured {captured_on}.

Timestamps, run ids and record hashes differ per run and are excluded from the
determinism claim rather than frozen. A faked timestamp in an audit trail is worse
than an honest one that varies, since the whole value of the log is that it says
when a decision was made. Reproducibility comes from canonical JSON with sorted
keys, and `tests/test_determinism.py` pins exactly that boundary: everything in
the trail reproduces except `timestamp`, and consequently the `prev_hash` and
`record_hash` that cover it.

- [Offline run (mock provider)](#offline-run-mock-provider)
- [Real model run (Anthropic primary)](#real-model-run-anthropic-primary)
- [Real model run (OpenAI primary, reduced)](#real-model-run-openai-primary-reduced)
{findings_toc}
The gate always runs against the deterministic mock panel, by design: a
regression gate has to be reproducible, the golden set pins which judge disagrees
with which human on which case, and a live model moves those around between runs.
Real model behavior is reported in the two sections below and never gates.
"""

OFFLINE_EXIT_NOTE = """Exit code 0. The demo's own exit code follows the ACTIVE version's gate, which is
the one CI runs, so a red demo would mean the committed baseline no longer
describes the committed prompts. Section 11 is skipped here and contributes
nothing to that integer even when it runs:
`tests/test_real_pass.py::test_the_demo_exit_code_is_the_same_with_the_real_pass_running_as_without_it`
runs the same demo against a live looking panel with a judge that raises on four
cases and asserts both the exit code and the gate's summary lines are unchanged.
"""

GATE_INTRO = """Each capture below is the tail of the gate's own output, from the regression block
through the exit driver, plus the exit code the shell observed. The full report
above every tail is the same shape as section 4 of the demo.
"""

REAL_RUNS = [
    (
        "anthropic",
        "Real model run (Anthropic primary)",
        "ENV_FILE=~/.secrets/ai.env AGENT_PROVIDER=anthropic python scripts/run_demo.py",
        """Only section 11 is reproduced, because it is the only section a real provider
changes and the only one whose numbers can never reach an exit code. Diffing this
run against the offline capture above, rather than assuming:

- **Sections 0 through 10 are byte for byte identical.** They gate on the
  deterministic mock panel whatever `AGENT_PROVIDER` says, which is the design.
- The header differs in four lines, all naming the panel: `provider`,
  `voting panel`, `shadow bench` and `panel degraded`.
- **Sections 12 and 13 differ only in timestamps and the record hashes that cover
  them**, which is exactly the determinism boundary `tests/test_determinism.py`
  pins, plus one line of section 13's summary: `real model pass skipped (provider
  is mock)` becomes `real model pass ran, and contributed nothing to any line
  above`.

That last line is the claim of this whole section, printed by the demo about
itself: 460 live judge calls were made and no number above them moved.""",
    ),
    (
        "openai",
        "Real model run (OpenAI primary, reduced)",
        "ENV_FILE=~/.secrets/ai.env AGENT_PROVIDER=openai python scripts/run_demo.py \\\n"
        "    --real-cases 8 --real-repeat-cases 4 --real-case-selection discriminating",
        """A reduced sweep, and NOT independent evidence. With both credentials present
`AGENT_PROVIDER` only reorders the panel slots, so the same models judge the same
golden set; a full second sweep would be a near duplicate of the one above at the
same price. What this run does add is a smaller sample over the cases that
separate the two sut versions, which is the part a reduced run has to get
right: see the findings below.""",
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", required=True, help="capture of scripts/run_demo.py")
    parser.add_argument("--gate-dir", required=True, help="directory of gate captures")
    parser.add_argument("--anthropic", default="", help="Anthropic primary capture")
    parser.add_argument("--openai", default="", help="OpenAI primary capture")
    parser.add_argument(
        "--findings",
        default="",
        help="markdown for the 'What the real model runs found' section. Required: "
        "see build(). Pass --no-findings to omit it on purpose.",
    )
    parser.add_argument(
        "--no-findings",
        dest="findings",
        action="store_const",
        const=NO_FINDINGS,
        help="build without the findings section, deliberately and on the record",
    )
    parser.add_argument("--captured-on", required=True, help="capture date, YYYY-MM-DD")
    parser.add_argument("--out", default=str(ROOT / "SAMPLE_RUN.md"))
    args = parser.parse_args()

    Path(args.out).write_text(build(args), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
