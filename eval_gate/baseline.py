"""The committed baseline record: the number a candidate is measured against.

WHY A COMMITTED FILE RATHER THAN A COMPARISON INSIDE THE RUN

An earlier version of this gate compared sut.v1 against sut.v2 inside a single
run. That is a fine measurement and a useless gate, for two reasons. The default
invocation permanently reported a regression, because the golden set carries a
planted one, so wiring the exit code to it would have made CI permanently red.
And comparing the active version against itself is not a fix either: a comparison
that can never fail is exactly the vacuous gate this project exists to warn about.

So the baseline lives on disk, in version control, next to the code. It records
what the active prompt version actually measured, under a named panel, over a
named prompt set. A candidate is a regression when its pass rate falls more than
the threshold below that recorded number. Approving a new prompt version is the
act of rewriting this file, which is a reviewable diff rather than a decision
someone made in a terminal.

WHY THERE ARE THREE COMMITTED RECORDS IN THIS REPOSITORY

baseline.json was recorded under the honest panel, and it is the one CI uses. There
is one record per panel mode because a baseline names the panel that measured it,
and a pass rate measured by a different panel is not comparable to it: the two
miscalibrated modes therefore need their own records or the demonstrations built on
them compare apples to oranges.

baseline.two_miscalibrated.json was recorded under the captured panel, and it
exists so the project's headline demonstration can be made honestly. The claim
being demonstrated is that a captured panel says ALLOW on a genuine regression and
only the calibration layer catches it. Making that claim against baseline.json
would require comparing a captured panel's pass rate to an honest panel's, which
`comparable_with` correctly rejects, and the gate now refuses to decide on. A team
whose panel was captured would have recorded ITS baseline with that captured panel,
so the second file is what that team's repository would hold. The demonstration is
stronger for it: the gate is fooled on its own terms, with a comparable baseline
and a valid comparison, and the calibration layer is still the only thing that
turns the build red. baseline.one_miscalibrated.json is the same argument one judge
weaker: with one blind judge the surviving two still outvote it, so the regression
is still caught against that panel's own recorded number.

Both miscalibrated records were written by hand rather than with --record-baseline,
because --record-baseline refuses a panel that failed its own calibration checks,
which is the correct behavior and is tested. That is not a loophole: it is the
statement that a repository holding one of these files is a repository whose
reference number came from a panel this harness would not have trusted.

WHAT IS RECORDED, AND WHY EACH FIELD IS THERE

  prompt_version_hash  sha256 over the whole prompt set. A pass rate measured
                       against different prompts is not comparable to this one,
                       and without the hash the gate would silently compare two
                       unrelated numbers.
  sut_version          which version this baseline describes.
  panel_mode           which panel measured it. A pass rate from a captured panel
                       is not comparable to one from an honest panel.
  repeats              how many repeats produced it, because repeat 1 is the
                       reported run and the noise floor depends on this.
  measured_pass_rate   the number the comparison actually uses.
  recorded_at          when. Not frozen, and not part of the comparison.
  run_id               which run produced it, so the audit trail and the registry
                       binding can be joined back to this file.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_BASELINE_PATH = "baseline.json"


@dataclass(frozen=True)
class BaselineRecord:
    prompt_version_hash: str
    sut_version: str
    panel_mode: str
    repeats: int
    measured_pass_rate: float
    recorded_at: str
    run_id: str

    def _differences(
        self, *, prompt_version_hash: str, panel_mode: str, repeats: int
    ) -> list[tuple[str, str]]:
        """Each field that differs, paired with the explanation of why it matters.

        One place computes the differences and two accessors present them, so the
        field names that drive the exit code and the prose printed in the report
        can never disagree about whether a comparison is valid.
        """
        problems: list[tuple[str, str]] = []
        if prompt_version_hash != self.prompt_version_hash:
            problems.append((
                "prompt_version_hash",
                f"prompt set changed since the baseline was recorded "
                f"({self.prompt_version_hash[:12]} -> {prompt_version_hash[:12]}); "
                f"the recorded pass rate no longer describes these prompts",
            ))
        if panel_mode != self.panel_mode:
            problems.append((
                "panel_mode",
                f"baseline was measured by the {self.panel_mode!r} panel, this run "
                f"used {panel_mode!r}; pass rates from different panels are not "
                f"comparable",
            ))
        if repeats != self.repeats:
            problems.append((
                "repeats",
                f"baseline used {self.repeats} repeats, this run used {repeats}",
            ))
        return problems

    def comparable_with(
        self, *, prompt_version_hash: str, panel_mode: str, repeats: int
    ) -> list[str]:
        """Reasons this baseline is not comparable to the run in hand.

        An empty list means the comparison is apples to apples. Anything else is
        printed AND refused, because a gate that compares incomparable numbers is
        worse than a gate that fails: the number looks authoritative. The refusal
        lives in evaluate_gate, next to the noise floor refusal it copies.
        """
        return [prose for _field, prose in self._differences(
            prompt_version_hash=prompt_version_hash,
            panel_mode=panel_mode,
            repeats=repeats,
        )]

    def differing_fields(
        self, *, prompt_version_hash: str, panel_mode: str, repeats: int
    ) -> list[str]:
        """Which recorded fields differ, by name.

        `comparable_with` explains the difference to a human reading the report;
        this names the fields for the single line that has to diagnose a red build.
        "Not comparable" without a field name is a complaint rather than a
        diagnosis, and the operator's next move depends on which field it was: a
        changed prompt set needs a new baseline, a changed panel needs the honest
        panel back.
        """
        return [field_name for field_name, _prose in self._differences(
            prompt_version_hash=prompt_version_hash,
            panel_mode=panel_mode,
            repeats=repeats,
        )]


def load_baseline(path: str | Path) -> BaselineRecord | None:
    """Read the committed baseline, or None when there is not one yet.

    None is a real answer. A repository with no baseline has nothing to compare a
    candidate against, and the gate says so and fails rather than inventing a
    comparison or quietly passing.
    """
    location = Path(path)
    if not location.exists():
        return None
    payload = json.loads(location.read_text(encoding="utf-8"))
    return BaselineRecord(
        prompt_version_hash=str(payload["prompt_version_hash"]),
        sut_version=str(payload["sut_version"]),
        panel_mode=str(payload["panel_mode"]),
        repeats=int(payload["repeats"]),
        measured_pass_rate=float(payload["measured_pass_rate"]),
        recorded_at=str(payload["recorded_at"]),
        run_id=str(payload["run_id"]),
    )


def write_baseline(path: str | Path, record: BaselineRecord) -> Path:
    """Write the baseline as canonical, sorted JSON so the diff is readable."""
    location = Path(path)
    location.parent.mkdir(parents=True, exist_ok=True)
    location.write_text(
        json.dumps(asdict(record), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return location


def describe(record: BaselineRecord | None) -> str:
    if record is None:
        return "none recorded"
    return (
        f"{record.sut_version} pass_rate {record.measured_pass_rate:.3f} "
        f"({record.panel_mode} panel, {record.repeats} repeats, "
        f"prompts {record.prompt_version_hash[:12]}, {record.run_id})"
    )
