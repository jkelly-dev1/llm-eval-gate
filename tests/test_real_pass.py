"""The paid pass: what it costs, what it survives, and what it cannot touch.

Every test in this module runs offline. That is not a convenience, it is the
whole point: the real model pass is the one path in this project that cannot be
verified by running it, because running it spends money against a live vendor and
a live vendor cannot be asked to time out on case gc-004 so a claim can be
checked. So the vendor is stood in for, and the properties that matter are
asserted against the stand in.

Four properties carry the weight here.

  1. It is SKIPPED offline, and the skip prints the call count the paid run would
     actually make, computed from the same plan. An offline capture that quoted a
     stale figure would be the first thing an operator budgeted against.
  2. The printed call count and dollar estimate are the arithmetic of the pass
     that follows, asserted by counting the calls the judges actually receive
     rather than by re-deriving the formula in the test.
  3. A judge that raises becomes a recorded abstention and the pass carries on.
     Aborting call 401 of 460 would throw away a paid measurement.
  4. NOTHING here can reach a gate decision or an exit code. That one is
     mutation-checked by executing the mutation: the test feeds the real pass's own
     report to evaluate_gate, shows the gate would have gone RED on it, and then
     shows the demo exits 0 anyway.
"""

from __future__ import annotations

import dataclasses

import pytest

from eval_gate.cost import (
    ESTIMATED_OUTPUT_TOKENS,
    PricingSlotMismatch,
    cost_accuracy_table,
    price_for,
)
from eval_gate.evals.golden import BASELINE, CANDIDATE
from eval_gate.evals.runner import GateDecision, evaluate_gate
from eval_gate.llm import MockJudgeStrict, describe_panel_health, honest_mock_panel
from eval_gate.real_pass import (
    NO_GATE_LINE,
    PassPlan,
    describe_cases,
    format_real_pass,
    plan_for,
    preflight,
    run_real_pass,
    select_cases,
    skipped,
)

#: The defaults an operator gets with no flags: the whole golden set once, a
#: subset three times, three voting judges and two shadow judges.
DEFAULT_PLAN = PassPlan(cases=30, repeat_cases=8, repeats=3, voting_judges=3, shadow_judges=2)


def _run(settings, panel, shadow, plan):
    """Run the pass with its output collected instead of printed."""
    lines: list[str] = []
    result = run_real_pass(
        settings, panel=panel, shadow=shadow, plan=plan, echo=lines.append
    )
    return result, "\n".join(lines)


# --------------------------------------------------------------------------- #
# 1. Offline behavior
# --------------------------------------------------------------------------- #


def test_the_real_model_section_is_skipped_offline_and_makes_no_judge_call(demo_run):
    """The offline capture stays complete: a skipped section says so, and why.

    Mutation check: return early without printing and this fails, which is the
    behavior that matters. A section that vanished from the offline capture would
    invite a reader to assume it had run, and the whole argument of this repository
    is about the gap between a printed number and the thing it measured.
    """
    exit_code, output = demo_run()

    assert exit_code == 0
    assert "11. The real model measurement pass (LIVE JUDGES, NEVER GATES)" in output
    assert 'SKIPPED: AGENT_PROVIDER is "mock"' in output
    assert "460 API calls" in output
    # No progress line can appear, because no judge was called.
    assert " r1  anthropic#1" not in output
    assert "real model pass         skipped (provider is mock)" in output


def test_the_skipped_line_quotes_the_call_count_the_paid_run_would_actually_make():
    """The offline capture cannot quote a figure the paid run would not honor.

    Mutation check, executed below: hardcode the figure in `skipped` and the second
    assertion fails, because a differently sized plan still prints 460.
    """
    default_text = skipped(DEFAULT_PLAN)
    assert f"{DEFAULT_PLAN.total_calls} API calls" in default_text
    assert "460 API calls" in default_text

    smaller = PassPlan(cases=4, repeat_cases=2, repeats=2, voting_judges=3, shadow_judges=2)
    smaller_text = skipped(smaller)
    assert f"{smaller.total_calls} API calls" in smaller_text
    assert "460 API calls" not in smaller_text


# --------------------------------------------------------------------------- #
# 2. The call count and the estimate
# --------------------------------------------------------------------------- #


def test_the_call_count_arithmetic_is_the_calls_the_judges_actually_receive(
    real_settings, fake_real_panel
):
    """The printed plan and the run are the same run, asserted by counting.

    60 judgments per judge for the calibration pass (30 cases x 2 versions x 1
    repeat), 32 more for the consistency pass (8 cases x 2 versions x repeats 2 and
    3), 92 per judge across 5 judges = 460. Two independent passes would have cost
    540; repeat 1 of the 16 consistency units is reused rather than re-purchased,
    which is the 80 call difference.

    Mutation check, executed: this asserts the arithmetic against the length of
    each judge's own call log, so changing either the formula or the loop without
    changing the other fails here rather than in a capture nobody re-reads.
    """
    assert DEFAULT_PLAN.calibration_calls_per_judge == 60
    assert DEFAULT_PLAN.extra_repeat_calls_per_judge == 32
    assert DEFAULT_PLAN.calls_per_judge == 92
    assert DEFAULT_PLAN.total_calls == 460
    assert DEFAULT_PLAN.naive_total_calls == 540
    assert DEFAULT_PLAN.calls_saved_by_reuse == 80

    panel, shadow = fake_real_panel()
    plan = PassPlan(cases=6, repeat_cases=3, repeats=3, voting_judges=3, shadow_judges=2)
    result, _output = _run(real_settings, panel, shadow, plan)

    assert plan.total_calls == (6 * 2 + 3 * 2 * 2) * 5
    for judge in panel + shadow:
        assert len(judge.seen) == plan.calls_per_judge
    assert result.calls == plan.total_calls


def test_repeat_one_of_a_consistency_case_is_bought_once_and_read_twice(
    real_settings, fake_real_panel
):
    """Reuse is asserted on the prompts sent, not on a comment saying it happens.

    The consistency cases are a prefix of the calibration cases, so attempt 1 of
    each of them belongs to both passes. Every (case, version) pair must appear at
    attempt 1 exactly once, and the consistency units must still carry a full
    series of `repeats` verdicts.

    Mutation check: drop first_repeat from run_panel and the attempt 1 count for
    the consistency units doubles, which this asserts directly.
    """
    panel, shadow = fake_real_panel()
    plan = PassPlan(cases=5, repeat_cases=2, repeats=3, voting_judges=3, shadow_judges=2)
    result, _output = _run(real_settings, panel, shadow, plan)

    judge = panel[0]
    first_attempts = [call for call in judge.seen if call[2] == 1]
    assert len(first_attempts) == 5 * 2
    assert len(set(first_attempts)) == len(first_attempts)

    consistency_units = [call for call in judge.seen if call[0] in {"gc-001", "gc-002"}]
    assert len(consistency_units) == 2 * 2 * 3
    assert sorted(call[2] for call in consistency_units) == [1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3]
    assert result.consistency.units == 2 * 2


def test_the_plans_case_selection_is_the_cases_the_judges_are_actually_sent(
    real_settings, fake_real_panel
):
    """A selection that only reached the printed heading would be worse than none.

    The reduced OpenAI capture is the one that has to buy discriminating cases, so
    the flag has to move the calls rather than the caption. This reads the case ids
    back off each judge's own call log, and checks the heading names the same set
    rather than a "gc-015..gc-020" range that skips gc-021 through gc-024.

    Mutation check, executed: the same plan with the default prefix selection sends
    gc-001..gc-006 instead, which is the run that measured nothing.
    """
    panel, shadow = fake_real_panel()
    plan = PassPlan(
        cases=6,
        repeat_cases=3,
        repeats=3,
        voting_judges=3,
        shadow_judges=2,
        selection="discriminating",
    )
    result, output = _run(real_settings, panel, shadow, plan)

    seen = sorted({call[0] for call in panel[0].seen})
    assert seen == ["gc-015", "gc-016", "gc-017", "gc-018", "gc-025", "gc-026"]
    assert "discriminating: gc-015, gc-025, gc-016, gc-026, gc-017, gc-018" in output

    # The report's own summary of the subset, which is a second place the ids are
    # printed and was wrong first: it described the same three cases as
    # "gc-015..gc-026, first 3 by case_id", naming ten cases the run never bought.
    text = format_real_pass(result)
    assert "discriminating: gc-015, gc-025, gc-016, gc-026, gc-017, gc-018" in text
    assert "3 (discriminating: gc-015, gc-025, gc-016)," in text
    assert "gc-015..gc-026" not in text
    assert "by case_id" not in text, "a discriminating subset is not ordered by case id"

    prefix_panel, prefix_shadow = fake_real_panel()
    _prefix_result, prefix_output = _run(
        real_settings, prefix_panel, prefix_shadow, dataclasses.replace(plan, selection="prefix")
    )
    assert sorted({call[0] for call in prefix_panel[0].seen}) == [
        f"gc-00{n}" for n in range(1, 7)
    ]
    assert "gc-001..gc-006" in prefix_output


def test_the_dollar_estimate_is_priced_arithmetic_and_never_costs_a_network_call(
    fake_real_panel,
):
    """An operator asking what a run will spend must not be charged to find out.

    count_tokens is itself an API round trip, so the preflight would have made 300
    of them at the default sizes to answer "should I authorize 460". The estimate
    is therefore forced onto the offline approximation, and this test proves it by
    giving every judge a count_tokens that raises.

    Mutation check: drop approximate_only from the preflight call and this test
    fails with the AssertionError below rather than with a wrong number, which is
    the failure mode worth having.
    """

    def explode(**kwargs):
        raise AssertionError("the preflight estimate must not call the vendor")

    panel, shadow = fake_real_panel()
    for judge in panel + shadow:
        judge.count_tokens = explode

    plan = PassPlan(cases=4, repeat_cases=2, repeats=3, voting_judges=3, shadow_judges=2)
    estimate = preflight(plan, panel, shadow)

    opus = estimate.by_slot()["anthropic#1"]
    assert opus.calls == plan.calls_per_judge
    assert opus.output_tokens == ESTIMATED_OUTPUT_TOKENS * plan.calls_per_judge
    price_in, price_out = price_for("claude-opus-5")
    assert opus.usd == pytest.approx(
        opus.input_tokens / 1_000_000 * price_in
        + opus.output_tokens / 1_000_000 * price_out
    )
    assert estimate.total_usd == pytest.approx(estimate.voting_usd + estimate.shadow_usd)


def test_the_measured_tokens_come_from_the_vendors_own_accounting(
    real_settings, fake_real_panel
):
    """Measured means measured: vendor usage, never characters divided by four.

    Mutation check: make GuardedJudge ignore `last_usage` and fall through to the
    approximation, and the equality below breaks, because the stand in deliberately
    reports a figure characters/4 does not produce.
    """
    panel, shadow = fake_real_panel()
    plan = PassPlan(cases=3, repeat_cases=2, repeats=2, voting_judges=3, shadow_judges=2)
    result, _output = _run(real_settings, panel, shadow, plan)

    opus = result.voting_cost.per_judge[0]
    assert opus.approximate is False
    assert result.voting_cost.approximate is False
    assert opus.input_tokens == panel[0].reported_input
    assert opus.output_tokens == panel[0].reported_output
    assert opus.input_tokens > 0
    # The approximation for the same calls, which the report also prints, is a
    # different number. If it were not, the comparison table would prove nothing.
    estimated = result.preflight.by_slot()["anthropic#1"]
    assert opus.input_tokens != estimated.input_tokens


def test_a_slot_that_reports_no_usage_is_counted_by_approximation_and_says_so(
    real_settings, fake_real_panel
):
    """An SDK that stops reporting usage must not silently become a measurement.

    Mutation check: set `exact=True` unconditionally in GuardedJudge._account and
    the report claims measured dollars for a number it guessed, which is the one
    thing cost.py raises UnknownModelPrice to avoid elsewhere.
    """
    panel, shadow = fake_real_panel(reports_usage=False)
    plan = PassPlan(cases=3, repeat_cases=2, repeats=2, voting_judges=3, shadow_judges=2)
    result, _output = _run(real_settings, panel, shadow, plan)

    assert all(item.approximate for item in result.voting_cost.per_judge)
    assert result.voting_cost.approximate is True
    assert "token counts are characters/4, an APPROXIMATION" in format_real_pass(result)


# --------------------------------------------------------------------------- #
# 3. Surviving a bad judge
# --------------------------------------------------------------------------- #


def test_a_judge_that_raises_becomes_a_recorded_abstention_rather_than_an_exception(
    real_settings, fake_real_panel
):
    """One flaky call must not end a run that has already been paid for.

    The third voting slot raises on every call for gc-002. Those calls become
    abstentions with the exception named, the other four judges are unaffected, and
    the pass completes every case it was asked for.

    Mutation check: let the exception propagate out of GuardedJudge.complete and
    this test raises RuntimeError instead of asserting anything, which is precisely
    the outcome a paid run cannot afford.
    """
    panel, shadow = fake_real_panel(raise_on=("gc-002",))
    plan = PassPlan(cases=4, repeat_cases=2, repeats=2, voting_judges=3, shadow_judges=2)
    result, output = _run(real_settings, panel, shadow, plan)

    assert result.calls == plan.total_calls
    flaky = [judge for judge in result.report.judges if judge.name == "openai"][0]
    healthy = [judge for judge in result.report.judges if judge.name == "anthropic#1"][0]
    assert flaky.abstained > 0
    assert healthy.abstained == 0

    kinds = {incident.kind for incident in result.incidents}
    assert kinds == {"RuntimeError"}
    assert {incident.case_id for incident in result.incidents} == {"gc-002"}
    assert all(incident.slot == "openai" for incident in result.incidents)
    assert all(incident.billed is False for incident in result.incidents)

    text = format_real_pass(result)
    assert "Calls that produced no usable verdict: " in text
    assert "RuntimeError: connection reset by peer" in text
    assert "recorded as an ABSTENTION and the pass carried on" in text
    assert "abstain" in output


def test_a_call_that_raised_is_not_billed_for_tokens_it_never_consumed(
    real_settings, fake_real_panel
):
    """A raised call returned no usage, so it contributes no tokens and no dollars.

    Mutation check: count the approximation for a raised call and the failing slot's
    token total rises to match the healthy slots', hiding the failure inside a cost
    report that looks entirely normal.
    """
    panel, shadow = fake_real_panel(raise_on=("gc-001", "gc-002", "gc-003", "gc-004"))
    plan = PassPlan(cases=4, repeat_cases=2, repeats=2, voting_judges=3, shadow_judges=2)
    result, _output = _run(real_settings, panel, shadow, plan)

    flaky = [item for item in result.voting_cost.per_judge if item.judge_name == "openai"][0]
    assert flaky.calls == plan.calls_per_judge
    assert flaky.input_tokens == 0
    assert flaky.output_usd == 0.0
    assert len(result.incidents) == plan.calls_per_judge


def test_asking_for_more_cases_than_the_golden_set_holds_raises_rather_than_measuring_fewer():
    """A pass that measured 30 cases while reporting 50 would misprice a run.

    Mutation check: clamp instead of raising and the report names a case count the
    run never scored, which is the defect ShadowBenchExhausted exists to prevent one
    module over.
    """
    with pytest.raises(ValueError, match="golden set holds 30"):
        select_cases(50)
    with pytest.raises(ValueError):
        select_cases(0)
    assert [case.case_id for case in select_cases(3)] == ["gc-001", "gc-002", "gc-003"]

    # The discriminating selection has its own, smaller, ceiling, and it names the
    # real reason: 12 of the 30 cases are labeled differently on the two versions.
    with pytest.raises(ValueError, match="only 12 of the golden set's 30"):
        select_cases(13, selection="discriminating")
    with pytest.raises(ValueError, match="unknown case selection"):
        select_cases(3, selection="first-three")


def test_a_reduced_sweep_of_discriminating_cases_separates_the_two_sut_versions():
    """The subset a reduced paid run should buy, asserted on what makes it worth buying.

    A reduced prefix is not a smaller version of the full sweep, it is a different
    and useless measurement: gc-001..gc-008 are grounded and pass in BOTH versions,
    so every judge scores kappa 1.000 against them and the two versions are
    indistinguishable. That was measured once, with money. This asserts the
    replacement actually separates the versions, that it holds both directions of
    disagreement rather than eight cases pointing the same way, and that the
    consistency subset is still a strict prefix of the calibration subset so
    repeat 1 stays reusable.

    Mutation check, executed in-test: the prefix selection is run against the same
    assertion and produces exactly the degenerate subset described above.
    """
    reduced = select_cases(8, selection="discriminating")
    ids = [case.case_id for case in reduced]
    assert len(set(ids)) == 8

    v1 = [case.label(BASELINE) for case in reduced]
    v2 = [case.label(CANDIDATE) for case in reduced]
    assert v1.count("pass") / 8 == 0.75
    assert v2.count("pass") / 8 == 0.25
    assert all(a != b for a, b in zip(v1, v2)), "every case must separate the versions"
    # Both directions: cases that regressed AND cases the candidate fixed.
    assert any(a == "pass" and b == "fail" for a, b in zip(v1, v2))
    assert any(a == "fail" and b == "pass" for a, b in zip(v1, v2))

    assert [case.case_id for case in select_cases(4, selection="discriminating")] == ids[:4]
    assert describe_cases(reduced, "discriminating").startswith("discriminating: gc-015, gc-025")
    assert describe_cases(select_cases(8), "prefix") == "gc-001..gc-008"

    # The mutation: the same size, taken as a prefix, is the run that measured nothing.
    prefix = select_cases(8)
    assert [case.case_id for case in prefix] == [f"gc-00{n}" for n in range(1, 9)]
    assert all(case.label(BASELINE) == case.label(CANDIDATE) == "pass" for case in prefix), (
        "the premise of the whole test: a reduced prefix cannot separate the versions"
    )


# --------------------------------------------------------------------------- #
# 4. It cannot gate. This is the property the section exists under.
# --------------------------------------------------------------------------- #


def test_nothing_the_real_pass_measures_can_reach_a_gate_decision(
    real_settings, fake_real_panel, canned_real_judge
):
    """MUTATION EXECUTED: gate on the real pass's own report and the build goes red.

    The panel here is three judges that pass everything, which on this skewed
    golden set scores 0.000 kappa and a 1.000 false pass rate. The mutation is
    performed rather than described: `evaluate_gate` is handed the real pass's
    report, and it fails four calibration checks. So there is nothing vacuous about
    the property being asserted, which is that the real pass produced no decision
    for anything to read, and that its report reaches no exit code.
    """
    passes_everything = [
        canned_real_judge("anthropic", "claude-opus-5", ScriptedPass()),
        canned_real_judge("anthropic", "claude-sonnet-5", ScriptedPass()),
        canned_real_judge("openai", "gpt-5.6-terra", ScriptedPass()),
    ]
    _panel, shadow = fake_real_panel()
    plan = PassPlan(cases=30, repeat_cases=2, repeats=2, voting_judges=3, shadow_judges=2)
    result, _output = _run(real_settings, passes_everything, shadow, plan)

    # THE MUTATION, executed: this is the gate the real pass deliberately does not
    # run. It goes red on these numbers.
    mutant = evaluate_gate(result.report, real_settings, candidate_version="sut.v2")
    assert mutant.passed is False
    assert mutant.panel_failures

    # And the property: the pass itself produced no decision at all.
    assert not any(
        isinstance(value, GateDecision) for value in vars(result).values()
    )
    assert not hasattr(result, "decision")
    text = format_real_pass(result)
    assert "deployment_decision" not in text
    assert "exit_code" not in text
    assert "EVAL GATE" not in text
    assert NO_GATE_LINE in text


def test_the_demo_exit_code_is_the_same_with_the_real_pass_running_as_without_it(
    demo_run, fake_real_panel
):
    """The end to end statement of the property, on the integer CI actually reads.

    The same demo is run twice: once offline, where the section is skipped, and once
    against a live looking panel with a judge that raises on four cases. The exit
    code is 0 both times, and every line of the summary block that the gate produced
    is byte identical.

    Mutation check: make main() return non-zero when the real pass recorded an
    incident, and the exit codes below diverge. The summary comparison is the second
    half: an exit code that happened to match while the gate's own numbers had moved
    would not be the property this claims.
    """
    offline_code, offline_output = demo_run()
    panel, shadow = fake_real_panel(raise_on=("gc-001", "gc-002", "gc-003", "gc-004"))
    live_code, live_output = demo_run(
        "--real-cases",
        "4",
        "--real-repeat-cases",
        "2",
        "--real-repeats",
        "2",
        panel=panel,
        shadow=shadow,
        provider="anthropic",
    )

    assert offline_code == 0
    assert live_code == 0
    assert "SKIPPED" in offline_output
    # Every one of the flaky slot's 12 calls (4 cases x 2 versions, plus 2 cases x
    # 2 versions at repeat 2) raised, and the run finished anyway.
    assert "Calls that produced no usable verdict: 12" in live_output
    assert _summary(offline_output) == _summary(live_output)


def _summary(output: str) -> list[str]:
    """The gate outcome lines of the demo's summary block, real pass line removed.

    The real pass line is expected to differ, since one run skipped the section and
    the other did not. Everything else in the block is a gate outcome and must not
    move.
    """
    lines = output.splitlines()
    start = lines.index("Summary")
    return [
        line
        for line in lines[start:]
        if line.strip().startswith(("active", "candidate", "threshold", "one bad", "two bad", "incomparable", "shadow judges off"))
    ]


# --------------------------------------------------------------------------- #
# 5. Reporting honesty
# --------------------------------------------------------------------------- #


def test_the_panel_degraded_header_distinguishes_all_three_panels():
    """One boolean said "fine" for a real panel and for a fallback panel alike.

    Mutation check: print `panel_degraded(panel)` instead and the first and third
    assertions below become the same string, which is the bug: a reader with only an
    Anthropic key was told their three model panel was healthy.
    """
    real_pair = [
        _StubReal("anthropic", "claude-opus-5"),
        _StubReal("anthropic", "claude-sonnet-5"),
        _StubReal("openai", "gpt-5.6-terra"),
    ]
    fell_back = real_pair[:2] + [MockJudgeStrict()]

    genuine = describe_panel_health(real_pair)
    degraded = describe_panel_health(fell_back)
    offline = describe_panel_health(honest_mock_panel())

    assert genuine.startswith("no (3 of 3 slots real: 3 models, 2 vendors")
    assert degraded.startswith("YES (2 of 3 slots real (anthropic)")
    assert "slot 3 fell back to a deterministic mock" in degraded
    assert offline.startswith("no (3 of 3 slots are offline deterministic mocks")
    assert genuine != offline


def test_the_real_section_names_which_slots_were_real(real_settings, fake_real_panel):
    """A capture has to say what measured it, slot by slot.

    Mutation check: report the panel description alone and a mock fallback slot
    reads as a model id in a list of model ids, which is how a partially real run
    gets quoted as a real one.
    """
    panel, shadow = fake_real_panel()
    panel[2] = MockJudgeStrict()
    plan = PassPlan(cases=3, repeat_cases=2, repeats=2, voting_judges=3, shadow_judges=2)
    result, _output = _run(real_settings, panel, shadow, plan)

    text = format_real_pass(result)
    assert "real (anthropic)" in text
    assert "mock fallback" in text
    assert result.degraded is True
    assert "YES, at least one slot is a deterministic mock" in text


def test_a_measured_cost_table_refuses_to_mix_measured_dollars_with_the_estimate(
    real_settings, fake_real_panel
):
    """A cheapest row derived from a guess would invert what the table says.

    Mutation check: fall back to the estimate for a judge with no measured figure
    and the table silently compares a measurement against an approximation in one
    column, which is the comparison the whole section exists to separate.
    """
    panel, shadow = fake_real_panel()
    plan = PassPlan(cases=3, repeat_cases=2, repeats=2, voting_judges=3, shadow_judges=2)
    result, _output = _run(real_settings, panel, shadow, plan)

    measured = result.sweep_usd_by_judge()
    rows = cost_accuracy_table(result.report, sweep_usd_by_judge=measured)
    assert {row.judge_name for row in rows} == set(measured)
    assert all(row.sweep_usd == measured[row.judge_name] for row in rows)

    del measured["anthropic#1"]
    with pytest.raises(PricingSlotMismatch, match="anthropic#1"):
        cost_accuracy_table(result.report, sweep_usd_by_judge=measured)


def test_the_report_states_how_far_the_offline_approximation_was_off(
    real_settings, fake_real_panel
):
    """The point of measuring is to find out that the estimate was wrong.

    Mutation check: print only the measured figures and the capture loses the one
    comparison that justifies labeling characters/4 an approximation everywhere
    else in the repository.
    """
    panel, shadow = fake_real_panel()
    plan = PassPlan(cases=3, repeat_cases=2, repeats=2, voting_judges=3, shadow_judges=2)
    result, _output = _run(real_settings, panel, shadow, plan)

    text = format_real_pass(result)
    assert "Measured against the offline characters/4 estimate" in text
    assert "dollars are MEASURED" in text
    assert result.preflight.input_tokens != (
        result.voting_cost.input_tokens + result.shadow_cost.input_tokens
    )


def test_the_plan_is_built_from_settings_so_the_demo_flags_reach_it(real_settings):
    """The flags are configuration, not a second set of constants.

    Mutation check: read the module defaults instead of the settings and
    --real-cases stops doing anything, which an operator would only discover from
    the invoice.
    """
    sized = real_settings.model_copy(
        update={"real_pass_cases": 12, "real_pass_repeat_cases": 4, "real_pass_repeats": 5}
    )
    plan = plan_for(sized, voting_judges=3, shadow_judges=2)

    assert (plan.cases, plan.repeat_cases, plan.repeats) == (12, 4, 5)
    assert plan.calls_per_judge == 12 * 2 + 4 * 2 * 4
    assert plan_for(real_settings, voting_judges=3, shadow_judges=2) == DEFAULT_PLAN


# --------------------------------------------------------------------------- #
# Local stand ins. Kept here rather than in conftest because each is used by one
# test and exists to make that one test's premise legible.
# --------------------------------------------------------------------------- #


class ScriptedPass:
    """A judge that passes everything: 0.800 agreement and 0.000 kappa.

    The behavior behind the panel in the "cannot gate" test. It has to be a judge
    the gate would reject, or executing the mutation would prove nothing.
    """

    name = "scripted-pass"
    model = "scripted"

    def complete(self, *, system: str, user: str) -> str:
        return (
            '{"verdict": "pass", "criteria": {"answers_the_question": true, '
            '"grounded_in_source": true, "no_invented_numbers": true, '
            '"well_formed": true}, "reasons": []}'
        )


class _StubReal:
    """A slot with a real vendor name and model id, and no client behind it."""

    def __init__(self, name: str, model: str) -> None:
        self.name = name
        self.model = model

    def complete(self, *, system: str, user: str) -> str:  # pragma: no cover
        raise AssertionError("a test tried to call a real provider")


def test_the_shadow_bench_is_measured_and_still_never_votes(real_settings, fake_real_panel):
    """The structural invariant has to hold on the paid path too.

    Shadow verdicts travel on their own fields and land in their own list, so a
    real run reports them without their ever reaching aggregate(). Mutation check:
    hand the shadow judges to run_panel's voting slot and panel.aggregate raises
    "shadow judges cannot vote", which is the boundary that makes this structural.
    """
    panel, shadow = fake_real_panel()
    plan = PassPlan(cases=3, repeat_cases=2, repeats=2, voting_judges=3, shadow_judges=2)
    result, _output = _run(real_settings, panel, shadow, plan)

    assert [judge.name for judge in result.report.judges] == [
        "anthropic#1",
        "anthropic#2",
        "openai",
    ]
    assert [judge.name for judge in result.report.shadow_judges] == [
        "shadow:openai#1",
        "shadow:openai#2",
    ]
    assert result.report.panel.units == 3 * 2
    assert set(result.consistency.shadow_flip_rate) == {
        "shadow:openai#1",
        "shadow:openai#2",
    }
