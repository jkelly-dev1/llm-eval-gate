"""The measurement layer: why each number is reported, and what it refuses to hide.

A gate you have not calibrated is a coin flip wearing a lab coat, and every claim
here is about a specific way an uncalibrated harness lies to you.

Raw agreement flatters a judge that cannot fail anything, on this deliberately
skewed golden set, by exactly 0.800. Cohen's kappa scores the same judge 0.000.
A single accuracy number hides whether a judge ships regressions or blocks good
changes, so false pass and false fail are reported separately and against
different denominators. A flip rate of zero and a flip rate of 0.250 are the
difference between a threshold that means something and one that measures its own
noise. Correlated errors mean three judges cost 3x and behave like slightly more
than one. And the suspicion line has to fire under total capture, name which
signal fired, and stay quiet when nothing is wrong, because a reassuring
diagnostic is worse than no diagnostic.

The arithmetic is asserted against hand computed fixtures whose numbers are worked
out in the fixture docstrings, not against the implementation's own output. Two
rules are mutation checked in-test: the substitution of raw agreement for kappa,
and the original unanimity-only suspicion heuristic.
"""

from __future__ import annotations

import dataclasses

from eval_gate.calibration import (
    calibrate_rater,
    cohen_kappa,
    measure_consistency,
    measure_correlation,
    raw_agreement,
)
from eval_gate.evals.runner import evaluate_gate, format_metrics


def test_cohens_kappa_is_zero_for_a_judge_that_agrees_only_by_chance(chance_level_pairs):
    """Chance level agreement is worth nothing, and kappa is what says so.

    The judge agrees on half the units, which is precisely what its own marginal
    rates predict, so raw agreement reads 0.500 and kappa reads 0.000. Reporting
    the first number as a judge's quality is how a coin flip gets promoted.
    """
    assert raw_agreement(chance_level_pairs) == 0.5
    assert cohen_kappa(chance_level_pairs) == 0.0


def test_cohens_kappa_is_one_for_a_perfect_judge(perfect_pairs):
    """Total agreement on a mixed label set is kappa 1.000, with no scaling artifact."""
    assert raw_agreement(perfect_pairs) == 1.0
    assert cohen_kappa(perfect_pairs) == 1.0


def test_raw_agreement_flatters_a_judge_that_always_answers_pass(
    skewed_human_labels, constant_pass_verdicts, rater_health_rules
):
    """The argument for reporting kappa at all, made on the real golden set.

    24 of the 30 baseline cases are labeled pass, so a judge that answers "pass"
    unconditionally agrees with the humans 80% of the time while being structurally
    incapable of blocking anything.

    Mutation check, executed in-test: the panel health rule is swapped from kappa
    to raw agreement against the same 0.200 floor, and the constant-pass judge
    stops looking bad. It is admitted to the panel with a score of 0.800, which is
    how a judge that has never failed a case ends up gating a release.
    """
    rater = calibrate_rater(
        "constant-pass", "mock", constant_pass_verdicts, skewed_human_labels, ("sut.v1",)
    )
    assert rater.raw_agreement == 0.8
    assert rater.kappa == 0.0
    assert rater.false_pass_rate == 1.0, "it waves through every case the humans failed"
    assert rater.pass_rate["sut.v1"] == 1.0

    honest_rule, reading_raw_agreement = rater_health_rules
    assert honest_rule(rater) is False
    assert reading_raw_agreement(rater) is True, (
        "the mutation must actually admit the judge, or the check is decorative"
    )

    # And the mutation is not merely permissive: it cannot separate this judge from
    # a good one, because both score 0.800 or better on raw agreement.
    assert reading_raw_agreement(rater) == reading_raw_agreement(
        dataclasses.replace(rater, kappa=0.857)
    )


def test_false_pass_and_false_fail_are_reported_separately(opposite_error_profiles):
    """One accuracy number hides which failure mode you have.

    Both judges score 0.800 raw agreement on the same ten units. One passes
    everything, so it waves through 2 of 2 genuinely bad answers (false_pass
    1.000) and blocks nothing. The other blocks 2 of 8 genuinely good answers
    (false_fail 0.250) and waves through nothing. The denominators differ on
    purpose: the question is what fraction of bad answers got through, and what
    fraction of good answers got stopped.
    """
    labels, lenient_verdicts, harsh_verdicts = opposite_error_profiles
    lenient = calibrate_rater("lenient", "mock", lenient_verdicts, labels, ("sut.v1",))
    harsh = calibrate_rater("harsh", "mock", harsh_verdicts, labels, ("sut.v1",))

    assert lenient.raw_agreement == harsh.raw_agreement == 0.8

    assert lenient.false_pass_rate == 1.0
    assert lenient.false_fail_rate == 0.0
    assert lenient.confusion == {
        "false_fail": 0,
        "false_pass": 2,
        "true_fail": 0,
        "true_pass": 8,
    }

    assert harsh.false_pass_rate == 0.0
    assert harsh.false_fail_rate == 0.25
    assert harsh.confusion == {
        "false_fail": 2,
        "false_pass": 0,
        "true_fail": 2,
        "true_pass": 6,
    }

    # Equal accuracy, unequal risk, and kappa is the only reported scalar that sees
    # the difference at all.
    assert lenient.kappa == 0.0
    assert round(harsh.kappa, 3) == 0.545


def test_flip_rate_is_zero_for_a_deterministic_judge(deterministic_and_mind_changing_series):
    deterministic, _changes_its_mind = deterministic_and_mind_changing_series
    consistency = measure_consistency({"steady": deterministic}, deterministic, repeats=3)
    assert consistency.per_judge_flip_rate["steady"] == 0.0
    assert consistency.panel_flip_rate == 0.0
    assert consistency.noise_floor == 0.0


def test_flip_rate_rises_for_a_judge_that_changes_its_mind(deterministic_and_mind_changing_series):
    """One unit out of four returning different verdicts across repeats is 0.250.

    The unit of the flip rate is the same as the unit of the gate's threshold: an
    absolute fraction of (case, sut version) units. That is what makes the noise
    floor comparison mean what it looks like it means.
    """
    deterministic, changes_its_mind = deterministic_and_mind_changing_series
    consistency = measure_consistency(
        {"steady": deterministic, "wavering": changes_its_mind},
        changes_its_mind,
        repeats=3,
    )
    assert consistency.per_judge_flip_rate["steady"] == 0.0
    assert consistency.per_judge_flip_rate["wavering"] == 0.25
    assert consistency.panel_flip_rate == 0.25
    assert consistency.noise_floor == consistency.panel_flip_rate


def test_error_correlation_is_high_for_two_judges_with_the_same_bias(
    shared_and_independent_biases, offline_run
):
    """Majority voting buys nothing when the voters are wrong on the same cases.

    Hand computed: both judges are wrong on the same 4 of 20 units, so the joint
    error rate is 0.200 while independence would predict 0.040. The ratio is 5.00
    and the interpretation has to say so in words, because a bare ratio in a table
    gets read as decoration.
    """
    labels, shared, _independent = shared_and_independent_biases
    pair = measure_correlation(shared, labels)[0]
    assert pair.error_rate_a == pair.error_rate_b == 0.2
    assert pair.joint_error_rate == 0.2
    assert round(pair.expected_if_independent, 3) == 0.04
    assert round(pair.ratio, 6) == 5.0
    assert pair.interpretation == (
        "errors correlate; majority voting buys less than it appears to"
    )

    # The same finding on the real captured panel: two judges built from one blind
    # behavior are wrong together three times more often than independence allows.
    _result, report = offline_run("two_miscalibrated")
    twins = [
        item
        for item in report.correlations
        if item.judge_a.startswith("mock-miscalibrated")
        and item.judge_b.startswith("mock-miscalibrated")
    ]
    assert len(twins) == 1
    assert round(twins[0].ratio, 2) == 3.00
    assert "errors correlate" in twins[0].interpretation


def test_error_correlation_is_near_independence_for_unrelated_biases(
    shared_and_independent_biases,
):
    """The number has to be able to say "independent" too, or it says nothing.

    Hand computed: error rates 0.400 and 0.500 (pairs are emitted in sorted judge
    name order, so the sliding window judge is judge_a) with a joint rate of 0.200,
    which is exactly what independence predicts. The ratio is 1.00 and the
    interpretation reads as roughly independent rather than as a shared bias.
    """
    labels, _shared, independent = shared_and_independent_biases
    pair = measure_correlation(independent, labels)[0]
    assert (pair.judge_a, pair.judge_b) == (
        "wrong-on-a-sliding-window",
        "wrong-on-the-first-half",
    )
    assert pair.error_rate_a == 0.4
    assert pair.error_rate_b == 0.5
    assert pair.joint_error_rate == 0.2
    assert pair.expected_if_independent == 0.2
    assert pair.ratio == 1.0
    assert pair.interpretation == "errors look roughly independent"


def test_the_report_says_so_when_the_panel_does_not_beat_its_best_member(
    offline_run, settings
):
    """The honest finding has to be impossible to overlook, not merely computable.

    On this golden set the three judge panel scores kappa 0.925, which is exactly
    the balanced judge's own 0.925: the majority vote lands on the same verdicts its
    best member already had, so the 3x cost bought nothing at all. That is asserted
    on the flag and on the emitted line, because a +0.000 in a table is easy to skim
    past and a sentence in capitals is not.

    A tie is the strict reading of the claim rather than a weak one. "The panel did
    not beat its best member" has to fire at a gap of zero, not only at a negative
    gap, or a panel that costs 3x for an identical answer would print nothing.
    """
    _result, report = offline_run("honest")
    panel = report.panel

    assert panel.best_single_judge == "mock-balanced"
    assert round(panel.best_single_judge_kappa, 3) == 0.925
    assert round(panel.kappa, 3) == 0.925
    assert panel.kappa_vs_best_single_judge == 0.0
    assert panel.panel_earned_its_cost is False

    decision = evaluate_gate(report, settings)
    text = format_metrics(report, decision)
    assert "THE PANEL DID NOT BEAT ITS BEST MEMBER" in text
    assert "three judges cost 3x and bought nothing measurable here" in text

    # And the line is conditional rather than always printed, so it still means
    # something on the day a panel does earn its cost.
    earned = dataclasses.replace(panel, kappa_vs_best_single_judge=0.05)
    assert earned.panel_earned_its_cost is True
    assert "THE PANEL DID NOT BEAT ITS BEST MEMBER" not in format_metrics(
        dataclasses.replace(report, panel=earned), decision
    )


def test_the_suspicion_line_names_which_vacuous_gate_signal_fired(
    offline_run, suspicion_reading_unanimity_only
):
    """Both sides pinned: it fires under capture, and it stays quiet when it should.

    Under the captured panel three independent signals fire and the line names each
    one (cases_that_never_discriminate 30/30, panel kappa 0.000, panel
    false_pass_rate 1.000) before giving the diagnosis, because the three
    diagnoses call for different fixes. Under the honest panel it prints "no
    vacuous gate signal fired", which is what makes the captured case informative
    rather than boilerplate.

    Mutation check, executed in-test: the heuristic is reverted to its original
    unanimity-only form and goes SILENT under total capture, because the captured
    panel measures 0.733 unanimity against the honest panel's 0.867. A suspicion
    line that reassures you while the panel is captured is worse than none.
    """
    _honest_result, honest = offline_run("honest")
    assert honest.discrimination.suspicion() == "no vacuous gate signal fired"
    assert honest.discrimination.signals() == []

    _captured_result, captured = offline_run("two_miscalibrated")
    discrimination = captured.discrimination
    fired = discrimination.signals()
    assert len(fired) == 3
    assert "cases_that_never_discriminate 30/30" in fired[0]
    assert "panel kappa 0.000" in fired[1]
    assert "panel false_pass_rate 1.000" in fired[2]

    line = discrimination.suspicion()
    assert line.startswith("SUSPICIOUS: ")
    assert line.endswith("suspect a CAPTURED PANEL: these verdicts cannot gate anything")

    # The tell that does NOT fire is the one the original heuristic relied on.
    assert round(discrimination.unanimity_rate, 3) == 0.733
    assert not any("unanimity_rate" in signal for signal in fired)

    assert suspicion_reading_unanimity_only(discrimination) == "no vacuous gate signal fired", (
        "the original heuristic is silent exactly when it matters"
    )
    assert suspicion_reading_unanimity_only(honest.discrimination) == (
        "no vacuous gate signal fired"
    )


def test_an_abstention_is_excluded_from_kappa_and_counted_on_its_own(skewed_human_labels):
    """A judge that declines is not agreeing, and not disagreeing either.

    Folding abstentions into either bucket would let a judge that abstains on every
    hard case report a clean kappa. They are dropped from the scored pairs and
    surface as abstention_rate instead, which is a gate threshold in its own right.
    """
    verdicts = dict.fromkeys(sorted(skewed_human_labels), "pass")
    for unit in sorted(skewed_human_labels)[:6]:
        verdicts[unit] = "abstain"

    rater = calibrate_rater("cautious", "mock", verdicts, skewed_human_labels, ("sut.v1",))
    assert rater.units == 30
    assert rater.scored == 24
    assert rater.abstained == 6
    assert rater.abstention_rate == 0.2
    assert rater.scored + rater.abstained == rater.units
