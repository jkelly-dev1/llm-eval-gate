"""The gate's exit code, and the two separate questions behind it.

CI reads an integer. Every claim in this module is therefore asserted on the exit
code first and on the printed report second, because a gate that prints BLOCK and
exits 0 is not a gate, and this project shipped that bug once already.

The gate answers two questions that are not the same question. Would this
candidate be allowed through, measured against the COMMITTED baseline record; and
is this panel fit to be making that call at all. The exit code follows both, and
`exit_driver` names which condition drove it, so a red build is diagnosable rather
than merely red.

The property that makes the whole project non-vacuous is asserted in
test_two_miscalibrated_judges_capture_the_panel_and_only_calibration_turns_the_build_red:
the gate itself is fooled there, says ALLOW on a genuine regression, and the
calibration layer is the only thing standing between that regression and a green
build. A harness that cannot demonstrate its own failure mode has not earned the
pass. That test gates against the baseline record for the captured panel, because a
team whose panel was captured would have recorded its baseline with that panel, and
because the gate now refuses to decide at all when the baseline it is handed
measured something else. Both halves are asserted: the fooled ALLOW on a valid
comparison, and the refusal on an invalid one.
"""

from __future__ import annotations

from pathlib import Path

from eval_gate.evals.runner import NO_DECISION, RunResult
from eval_gate.models import JudgeVerdict, PanelVerdict

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED_AUDIT_DIR = REPO_ROOT / "audit"
#: The committed baseline records for the two miscalibrated panel modes. A baseline
#: names the panel that measured it, so each panel mode needs its own record or
#: every comparison built on it is apples to oranges.
ONE_MISCALIBRATED_BASELINE = REPO_ROOT / "baseline.one_miscalibrated.json"
TWO_MISCALIBRATED_BASELINE = REPO_ROOT / "baseline.two_miscalibrated.json"


def test_the_honest_panel_gating_its_own_committed_baseline_exits_zero(run_gate):
    """Exit 0 means both questions came back clean, and says so in one line.

    The default invocation gates the version named in the committed baseline
    against the pass rate recorded for it, so the drop is 0.000 and the honest
    panel passes every calibration check. `exit_driver` is asserted because a
    gate that cannot name why it went green cannot be trusted when it goes red.
    """
    run = run_gate()

    assert run.exit_code == 0
    assert run.decision.candidate_version == "sut.v1"
    assert run.decision.regression_detected is False
    assert run.decision.deployment_decision == "ALLOW"
    assert run.decision.panel_healthy is True
    assert run.decision.refused is False
    assert run.decision.panel_failures == []
    assert round(run.decision.candidate_pass_rate, 3) == 0.800
    assert round(run.decision.baseline_pass_rate, 3) == 0.800
    assert run.decision.exit_driver == (
        "none: panel healthy and no regression against the committed baseline"
    )
    assert "EVAL GATE PASSED" in run.output
    assert "  exit_code                          0" in run.output


def test_the_honest_panel_catches_the_sut_v2_regression_and_exits_one(run_gate):
    """The planted regression, caught, blocked, and non-zero on the way out.

    0.533 against the committed 0.800 is a 0.267 drop, which is more than the
    0.150 threshold allows and more than twice the measured 0.100 noise floor,
    so the finding is a regression rather than a rerun.
    """
    run = run_gate("--candidate", "sut.v2")

    assert run.exit_code == 1
    assert run.decision.candidate_version == "sut.v2"
    assert run.decision.regression_detected is True
    assert run.decision.deployment_decision == "BLOCK"
    assert run.decision.panel_healthy is True
    assert round(run.decision.candidate_pass_rate, 3) == 0.533
    assert round(run.decision.baseline_pass_rate, 3) == 0.800
    assert round(run.decision.drop_vs_baseline, 3) == 0.267
    assert run.decision.drop_vs_baseline > run.decision.threshold == 0.15
    assert run.decision.drop_vs_baseline > run.decision.noise_floor
    assert run.decision.exit_driver == "regression detected against the committed baseline"
    assert "EVAL GATE FAILED" in run.output
    assert "REGRESSION: sut.v2 pass rate 0.533" in run.output


def test_one_miscalibrated_judge_is_outvoted_and_the_gate_still_catches_it(run_gate):
    """One blind judge is survivable, and the report still names it.

    The strict and lenient judges outvote the miscalibrated one, so the panel's
    own kappa stays at 0.842 and the pass rate drop (0.867 -> 0.600) is still
    wide enough to trip the threshold. The bad judge is reported as a calibration
    failure in the same run, which is the honest outcome: the merge is blocked for
    the regression AND the panel is flagged as needing a judge replaced.

    The comparison is against the baseline recorded UNDER THIS PANEL, because a
    baseline names the panel that measured it. Gating this run against the honest
    panel's baseline.json is not a stricter test, it is a meaningless one, and the
    gate refuses it: that refusal is asserted in
    test_a_baseline_from_another_panel_produces_no_deployment_decision.
    """
    run = run_gate(
        "--panel", "one_miscalibrated", "--candidate", "sut.v2",
        "--baseline", str(ONE_MISCALIBRATED_BASELINE),
    )

    assert run.exit_code == 1
    assert run.decision.baseline_incomparable is False, "same panel, same prompts, same repeats"
    assert run.decision.regression_detected is True
    assert run.decision.deployment_decision == "BLOCK"
    assert round(run.decision.candidate_pass_rate, 3) == 0.600
    assert round(run.decision.baseline_pass_rate, 3) == 0.867
    assert round(run.decision.drop_vs_baseline, 3) == 0.267
    assert run.report.panel.kappa > 0.80, "two honest judges still carry the vote"
    assert run.decision.panel_healthy is False
    assert any("mock-miscalibrated" in failure for failure in run.decision.panel_failures)
    assert run.decision.exit_drivers[0] == (
        "regression detected against the committed baseline"
    )


def test_two_miscalibrated_judges_capture_the_panel_and_only_calibration_turns_the_build_red(
    run_gate,
):
    """This test asserts a FAILURE of the system, and that is the point of it.

    With two of three judges blind, the gate's merge blocking half is fooled: the
    captured panel passes every case of both versions, so the candidate's measured
    pass rate is 1.000, `regression_detected` is NO and `deployment_decision` is
    ALLOW on a genuine, planted regression. Nothing in the regression comparison
    notices, and nothing can, because the measurement it consumes is worthless.

    THE BASELINE HERE IS COMPARABLE, and that is what makes this the strong form of
    the claim rather than an artifact. A team whose panel was captured would have
    recorded its baseline with that captured panel, so this run gates against
    baseline.two_miscalibrated.json: same panel, same prompt set, same repeats. The
    gate is fooled on its own terms, with a valid comparison in front of it, and it
    is not fooled because it was handed two numbers it should have refused to
    compare. Recorded 1.000 against measured 1.000 is a drop of exactly 0.000: the
    captured panel cannot see the regression it is being asked about.

    The calibration layer is the ONLY reason this build goes red: four checks fail
    (two judges at kappa 0.000, panel false_pass_rate 1.000, and every case in the
    golden set having stopped discriminating), and `exit_driver` reads "panel
    calibration failed (4 checks)". An uncalibrated harness merges this change
    with a green build and a decisive looking ALLOW next to it.
    """
    run = run_gate(
        "--panel", "two_miscalibrated", "--candidate", "sut.v2",
        "--baseline", str(TWO_MISCALIBRATED_BASELINE),
    )

    # The merge blocking half of the gate has been defeated, on a valid comparison.
    assert run.decision.baseline_incomparable is False
    assert run.decision.baseline_warnings == []
    assert run.decision.regression_detected is False
    assert run.decision.deployment_decision == "ALLOW"
    assert round(run.decision.candidate_pass_rate, 3) == 1.000
    assert round(run.decision.baseline_pass_rate, 3) == 1.000
    assert round(run.decision.drop_vs_baseline, 3) == 0.000, (
        "the captured panel measures the regressed candidate as its own baseline"
    )

    # And the harness health half is what saves the build.
    assert run.decision.panel_healthy is False
    assert len(run.decision.panel_failures) == 4
    assert run.decision.exit_driver == "panel calibration failed (4 checks)"
    assert run.exit_code == 1

    # Non-vacuity: strip the calibration checks and this genuine regression ships.
    run.decision.panel_failures = []
    assert run.decision.passed is True, (
        "with the calibration checks removed the gate exits 0 on a regression, "
        "which is exactly what an uncalibrated harness does"
    )

    assert "suspect a CAPTURED PANEL" in run.output
    assert "panel false_pass_rate 1.000" in run.output
    assert "  deployment_decision                ALLOW" in run.output
    assert "  panel_healthy                      NO" in run.output


def test_a_baseline_from_another_panel_produces_no_deployment_decision(
    run_gate, gate_blind_to_baseline_comparability
):
    """A decision computed from a comparison already called meaningless is invented.

    This is the same invocation as the captured panel test above, minus the
    --baseline flag, so it is measured against the committed baseline.json, which
    was recorded under the HONEST panel. The gate printed BASELINE NOT COMPARABLE
    and then computed deployment_decision ALLOW from that very comparison, which is
    the defect the noise floor refusal exists to prevent one layer down. So the
    deployment decision is withdrawn instead, exit_driver names the incomparability
    AND the field that differs, and the exit code is 1.

    The calibration verdict is deliberately still reached and still reported, as an
    "also": whether this panel is fit to gate is a different question, and an
    invalid baseline does not answer it.

    Mutation check, executed in-test: comparability is stubbed out to report no
    problems, and the gate goes straight back to reporting ALLOW on a regressed
    candidate measured by one panel against a baseline recorded by another.
    """
    run = run_gate("--panel", "two_miscalibrated", "--candidate", "sut.v2")

    assert run.exit_code == 1
    assert run.decision.baseline_incomparable is True
    assert run.decision.baseline_differing_fields == ["panel_mode"]
    assert run.decision.deployment_decision == NO_DECISION
    assert run.decision.deployment_decision not in ("ALLOW", "BLOCK")
    assert run.decision.regression_detected is False
    assert run.decision.exit_drivers[0] == (
        "refused: committed baseline is not comparable to this run (panel_mode differs)"
    )
    assert run.decision.exit_driver.endswith("(also: panel calibration failed (4 checks))")
    assert "BASELINE NOT COMPARABLE" in run.output
    assert "REFUSING TO DECIDE" in run.output
    assert "  deployment_decision                NO DECISION (refused)" in run.output

    # The noise floor refusal is NOT what fired here, so the two refusals stay
    # distinguishable in a red build's output.
    assert run.decision.refused is False
    assert "REFUSING TO RUN" not in run.output

    gate_blind_to_baseline_comparability()
    mutated = run_gate("--panel", "two_miscalibrated", "--candidate", "sut.v2")
    assert mutated.decision.baseline_incomparable is False, (
        "the mutation must actually remove the guard"
    )
    assert mutated.decision.deployment_decision == "ALLOW", (
        "without the guard the gate manufactures ALLOW from an incomparable baseline"
    )
    assert round(mutated.decision.baseline_pass_rate, 3) == 0.800, (
        "and the number it used was measured by a different panel than the one that ran"
    )
    assert round(mutated.decision.candidate_pass_rate, 3) == 1.000


def test_a_threshold_inside_the_measured_noise_floor_is_refused(
    run_gate, gate_blind_to_its_own_noise_floor
):
    """A gate tighter than its own judges' variance is not a gate.

    Mutation check, executed in-test: the noise floor guard is bypassed and the
    same 0.050 threshold is then accepted, exit 0, against a still-reported 0.100
    panel flip rate. That is a gate that cannot distinguish a regression from a
    rerun and will produce a verdict anyway, which is the behavior the refusal
    exists to forbid.
    """
    run = run_gate("--threshold", "0.05")

    assert run.exit_code == 1
    assert run.decision.refused is True
    assert run.decision.deployment_decision == NO_DECISION, (
        "a refused run produces no verdict, so it must not print one either"
    )
    assert round(run.decision.threshold, 3) == 0.050
    assert round(run.decision.noise_floor, 3) == 0.100
    assert run.decision.threshold < run.decision.noise_floor
    assert run.decision.exit_driver == (
        "refused: threshold sits inside the measured noise floor"
    )
    assert "REFUSING TO RUN" in run.output
    # The refusal short circuits: no calibration verdict is manufactured either.
    assert run.decision.panel_failures == []

    gate_blind_to_its_own_noise_floor()
    mutated = run_gate("--threshold", "0.05")
    assert mutated.decision.refused is False, "the mutation must actually remove the guard"
    assert mutated.exit_code == 0
    assert round(mutated.report.consistency.panel_flip_rate, 3) == 0.100, (
        "the flip rate is still measured; the mutant just gates below it"
    )
    assert round(mutated.decision.threshold, 3) == 0.050


def test_toggling_shadow_judges_off_leaves_every_gate_outcome_and_exit_code_identical(run_gate):
    """A shadow judge that could move a gate outcome would be a voting judge.

    Asserted for all five verified invocations, on the exit code, the decision,
    every failure string, and every voting side number in the report. Both refusals
    are in the list, because a refusal is an outcome a shadow judge must not be able
    to lift either. The premise is checked too: with shadow judges on, two of them
    are measured and printed, so the comparison is between a run that has them and a
    run that does not rather than between two runs that never had any.
    """
    invocations = (
        (),
        ("--candidate", "sut.v2"),
        (
            "--panel", "two_miscalibrated", "--candidate", "sut.v2",
            "--baseline", str(TWO_MISCALIBRATED_BASELINE),
        ),
        ("--panel", "two_miscalibrated", "--candidate", "sut.v2"),
        ("--threshold", "0.05"),
    )
    for argv in invocations:
        with_shadow = run_gate(*argv)
        without_shadow = run_gate(*argv, "--no-shadow")

        assert with_shadow.exit_code == without_shadow.exit_code, argv
        assert with_shadow.outcome() == without_shadow.outcome(), argv
        assert with_shadow.voting_numbers() == without_shadow.voting_numbers(), argv

        assert len(with_shadow.report.shadow_judges) == 2, argv
        assert without_shadow.report.shadow_judges == [], argv
        assert without_shadow.report.shadow_correlations == [], argv
        assert without_shadow.result.shadow_verdicts == [], argv
        assert "Shadow judges (measured, NON VOTING, never gate)" in with_shadow.output
        assert "Shadow judges (measured, NON VOTING, never gate)" not in without_shadow.output


def test_the_gate_writes_no_audit_records_into_the_committed_audit_directory(run_gate):
    """A CI run that appends to the committed log is how that log stops being real.

    The gate writes its trail to a tempfile.TemporaryDirectory(), so the assertion
    is structural rather than a directory listing that a concurrent process could
    dirty: every AuditLog path the gate opened is outside the repository, and every
    one of them is gone by the time main() returns. The default committed log path
    is compared before and after as well, so an accidental append would be caught
    even if the temp dir story were rewritten.
    """
    committed_log = COMMITTED_AUDIT_DIR / "audit.log.jsonl"
    before = (committed_log.exists(), committed_log.stat().st_size if committed_log.exists() else 0)

    run = run_gate()

    after = (committed_log.exists(), committed_log.stat().st_size if committed_log.exists() else 0)
    assert before == after, "the gate appended to the committed audit log"

    assert run.audit_paths, "the gate must open an audit log at all, or this is vacuous"
    for path in run.audit_paths:
        assert REPO_ROOT not in path.parents, f"{path} is inside the repository"
        assert path.parent != COMMITTED_AUDIT_DIR
        assert not path.exists(), f"{path} outlived the run, so it was not a temp dir"

    # And the records really were written and chained, just not here.
    assert "  audit_records                      62" in run.output
    assert "  audit_chain_intact                 yes" in run.output


def test_cases_that_never_discriminate_is_reported_and_saturates_under_a_captured_panel(run_gate):
    """The vacuous gate detector: a golden set that cannot fail is untested.

    Under the honest panel 15 of 30 cases give the two versions different verdicts,
    so half the set carries signal. Under the captured panel every case returns the
    same verdict for both versions, the count saturates at 30/30, and that is the
    check that turns the capture into a build failure rather than a footnote.
    """
    honest = run_gate()
    assert honest.report.discrimination.cases == 30
    assert honest.report.discrimination.cases_that_never_discriminate == 15
    assert round(honest.report.discrimination.never_discriminate_rate, 3) == 0.500
    assert "cases_that_never_discriminate      15/30 (0.500)" in honest.output

    captured = run_gate("--panel", "two_miscalibrated", "--candidate", "sut.v2")
    assert captured.report.discrimination.cases_that_never_discriminate == 30
    assert captured.report.discrimination.never_discriminate_rate == 1.0
    assert "cases_that_never_discriminate      30/30 (1.000)" in captured.output
    assert any(
        "cases_that_never_discriminate 30/30" in failure
        for failure in captured.decision.panel_failures
    ), "saturation has to be a failure, not just a printed number"


def test_the_run_result_the_gate_is_handed_keeps_shadow_verdicts_off_every_voting_field(run_gate):
    """Structural, so a refactor that pipes shadow verdicts into the vote breaks here.

    The panel half of this property is asserted in tests/test_panel.py against
    PanelVerdict. This is the other half: the RunResult the gate is handed. Shadow
    verdicts live on separately named fields, are flagged on the record itself, and
    are invisible to the accessors the report builder uses, so merging the two
    benches would require deleting one of these assertions rather than forgetting a
    convention.
    """
    run = run_gate()
    result = run.result
    assert isinstance(result, RunResult)

    voting_fields = {"judge_verdicts", "judge_series", "panel_verdicts", "panel_series"}
    shadow_fields = {"shadow_names", "shadow_models", "shadow_series", "shadow_verdicts"}
    assert voting_fields | shadow_fields <= set(f.name for f in RunResult.__dataclass_fields__.values())
    assert not voting_fields & shadow_fields

    assert result.shadow_verdicts, "two shadow judges ran, or this test proves nothing"
    assert all(verdict.shadow is False for verdict in result.judge_verdicts)
    assert all(verdict.shadow is True for verdict in result.shadow_verdicts)
    assert set(result.judge_series).isdisjoint(result.shadow_series)
    assert set(result.judge_names).isdisjoint(result.shadow_names)

    # The accessors the report builder calls cannot reach across the divide.
    for name in result.shadow_names:
        assert result.primary_judge(name) == {}
    for name in result.judge_names:
        assert result.primary_shadow(name) == {}

    # Every aggregated verdict counted exactly the three voting judges.
    panel = result.primary_panel()
    assert len(panel) == 60
    assert {len(verdict.votes) for verdict in panel.values()} == {3}
    assert all(isinstance(verdict, PanelVerdict) for verdict in panel.values())
    assert all(isinstance(verdict, JudgeVerdict) for verdict in result.shadow_verdicts)

    # And the report keeps the two lists apart on the way to the gate.
    assert {judge.name for judge in run.report.judges}.isdisjoint(
        {judge.name for judge in run.report.shadow_judges}
    )
