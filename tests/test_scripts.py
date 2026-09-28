"""The two verifiers CI runs, held to their exit codes.

Each is a CI step whose only signal is the integer it returns. A verifier that
printed its own failure and returned 0 would leave the step green, so both
directions are asserted: 0 on the tree as committed, 1 when the thing it checks
has drifted.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"{name}_under_test", REPO_ROOT / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_sample_run_check_passes_the_committed_file_and_fails_a_stale_one(
    monkeypatch, capsys
):
    check = _load("check_sample_run")
    assert check.main() == 0

    real_blocks = check.blocks

    def stale(text):
        found = real_blocks(text)
        demo = found.index(check.DEMO_COMMAND) + 1
        found[demo] = found[demo].replace("prices as of", "prices as of 1999-01;", 1)
        return found

    monkeypatch.setattr(check, "blocks", stale)
    assert check.main() == 1
    assert "does not match a fresh run" in capsys.readouterr().out


def test_the_claims_table_check_fails_when_a_named_test_is_not_collected(
    monkeypatch, capsys
):
    verify = _load("verify_claims_table")
    everything = verify.collected()
    assert verify.main() == 0

    named = verify.quoted_ids((REPO_ROOT / "README.md").read_text(encoding="utf-8"))
    missing = named[0][0]
    assert missing in everything
    monkeypatch.setattr(verify, "collected", lambda: everything - {missing})
    assert verify.main() == 1


def test_the_sample_run_check_fails_a_hand_written_figure_no_capture_holds(
    monkeypatch, capsys
):
    check = _load("check_sample_run")
    sample, findings, readme = check.documents()
    assert check.figure_problems(sample, findings, readme) == []

    def built(old, new):
        # The same edit in SAMPLE_RUN.md's findings section, not its captures.
        head, heading, tail = sample.partition(check.FINDINGS_HEADING)
        return head + heading + tail.replace(old, new)

    # A README multiplier no capture prints.
    stale = readme.replace("1.93x the\n  spend", "1.94x the\n  spend")
    assert stale != readme
    assert any("README.md: 1.94x" in p
               for p in check.figure_problems(sample, findings, stale))

    # A findings kappa, edited in the source and in the built copy alike, so
    # the stale-build comparison is not the line that fires.
    kappa = findings.replace("0.963", "0.936")
    assert kappa != findings
    problems = check.figure_problems(built("0.963", "0.936"), kappa, readme)
    assert any("SAMPLE_RUN.findings.md: 0.936" in p for p in problems)

    # A derived figure that no longer follows from the two it is computed from.
    ratio = findings.replace("4.2x the price", "4.3x the price")
    assert ratio != findings
    problems = check.figure_problems(
        built("4.2x the price", "4.3x the price"), ratio, readme)
    assert any("SAMPLE_RUN.findings.md: 4.3x" in p for p in problems)

    # A DERIVED entry whose arithmetic does not give the figure it names.
    monkeypatch.setitem(check.DERIVED, "4.2x", ("0.8494", "0.3206", "{:.1f}x"))
    assert any("derived figure 4.2x" in p
               for p in check.figure_problems(sample, findings, readme))
    monkeypatch.undo()

    # A findings edit that was never rebuilt into SAMPLE_RUN.md.
    assert any("rebuild it" in p
               for p in check.figure_problems(sample, kappa, readme))

    # Any of them makes main() exit 1.
    monkeypatch.setattr(check, "documents", lambda: (sample, findings, stale))
    assert check.main() == 1
    assert "README.md: 1.94x" in capsys.readouterr().out
