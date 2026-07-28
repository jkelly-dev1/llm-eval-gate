"""Panel aggregation: what the vote counts, and what it refuses to count.

A gate is only as honest as its aggregation rule. Three failure modes are cheap to
write and expensive to ship, and the rules here exist to make each one impossible
rather than unlikely.

An abstention that votes lets a broken judge veto or wave through every case, so
abstentions do not vote and are counted on the record instead. A tie resolved
toward pass is how a gate ships a regression while looking decisive, so a tie
abstains and escalates to a human. A shadow judge that reaches the vote is a
voting judge with extra steps, so aggregation refuses a shadow verdict outright
and shadow results travel on a different field of a different type from the one
the gate reads.

The tie rule is the one worth mutating rather than merely asserting, and the
mutation is executed here rather than described.
"""

from __future__ import annotations

import pytest

from eval_gate.llm import honest_mock_panel, shadow_mock_judges
from eval_gate.models import JudgeVerdict, PanelVerdict
from eval_gate.panel import aggregate


def assert_the_tie_was_escalated(result: PanelVerdict) -> None:
    """The premise of the tie test, factored out so a mutant can be run against it."""
    assert result.verdict == "abstain"
    assert result.escalated is True


def test_two_of_three_votes_carries_the_panel(make_verdict):
    result = aggregate(
        [
            make_verdict("pass", judge_name="mock-strict"),
            make_verdict("pass", judge_name="mock-lenient"),
            make_verdict("fail", judge_name="mock-balanced"),
        ]
    )
    assert result.verdict == "pass"
    assert result.escalated is False
    assert result.unanimous is False

    flipped = aggregate(
        [
            make_verdict("fail", judge_name="mock-strict"),
            make_verdict("fail", judge_name="mock-lenient"),
            make_verdict("pass", judge_name="mock-balanced"),
        ]
    )
    assert flipped.verdict == "fail"


def test_an_abstaining_judge_does_not_vote(make_verdict):
    """An abstention is neither a pass nor a fail, and the record keeps it visible.

    The discriminating case is the two judge one: counting the abstention as a
    vote would turn it into a confident pass, and counting it as a fail would turn
    it into a confident fail. It has to be neither.
    """
    result = aggregate(
        [
            make_verdict("pass", judge_name="mock-strict"),
            make_verdict("pass", judge_name="mock-lenient"),
            make_verdict("abstain", judge_name="mock-balanced", raw_ok=False),
        ]
    )
    assert result.verdict == "pass"
    assert result.abstentions == 1
    assert result.unanimous is False, "an abstention is not agreement"

    two_judges_one_abstaining = aggregate(
        [
            make_verdict("pass", judge_name="mock-strict"),
            make_verdict("abstain", judge_name="mock-lenient"),
        ]
    )
    assert two_judges_one_abstaining.verdict == "abstain"
    assert two_judges_one_abstaining.escalated is True


def test_fewer_than_two_voting_judges_abstains_the_panel(make_verdict):
    """One opinion is not a panel, and reporting it as one hides a degraded run.

    A deliberately configured single judge is exempt, which panel.py states and
    explains: single judge mode exists so the harness can price the panel against
    it. The rule bites when a panel of three has been reduced to one voter by
    abstentions, which is the case that would otherwise be reported as a verdict.
    """
    result = aggregate(
        [
            make_verdict("fail", judge_name="mock-strict"),
            make_verdict("abstain", judge_name="mock-lenient", raw_ok=False),
            make_verdict("abstain", judge_name="mock-balanced", raw_ok=False),
        ]
    )
    assert result.verdict == "abstain"
    assert result.escalated is True
    assert result.abstentions == 2

    single_judge_mode = aggregate([make_verdict("fail", judge_name="mock-strict")])
    assert single_judge_mode.verdict == "fail"


def test_a_tie_abstains_and_flags_the_case_for_human_escalation(
    tied_verdicts, aggregate_with_ties_resolved_toward_pass
):
    """A tie is a routing decision, not a verdict the gate may take for itself.

    Mutation check, executed in-test: the tie rule is resolved toward pass and this
    test's own premise, assert_the_tie_was_escalated, then raises. A tie silently
    resolved toward pass is how a gate ships a regression while looking decisive,
    and the escalation flag is what puts the ambiguous case in front of a human
    instead.
    """
    result = aggregate(tied_verdicts)
    assert_the_tie_was_escalated(result)
    assert result.split is True
    assert result.votes == ("abstain", "fail", "pass")

    mutated = aggregate_with_ties_resolved_toward_pass(tied_verdicts)
    assert mutated.verdict == "pass", "the mutation must actually change the outcome"
    with pytest.raises(AssertionError):
        assert_the_tie_was_escalated(mutated)


def test_a_shadow_judge_never_appears_in_the_panel_vote(make_verdict):
    """Aggregation refuses a shadow verdict rather than quietly dropping it.

    Dropping it silently would be worse than counting it: the panel would report a
    two judge vote as a three judge one and no one would know a slot had gone
    missing. The error names the judge so the misconfiguration is findable.
    """
    verdicts = [
        make_verdict("fail", judge_name="mock-strict"),
        make_verdict("fail", judge_name="mock-lenient"),
        make_verdict("pass", judge_name="mock-verbosity", shadow=True),
    ]
    with pytest.raises(ValueError) as excinfo:
        aggregate(verdicts)
    assert "mock-verbosity" in str(excinfo.value)
    assert "shadow" in str(excinfo.value)

    # And the same verdict with the shadow flag cleared is accepted, so the refusal
    # is about the flag rather than about the judge's name.
    voting = verdicts[:2] + [verdicts[2].model_copy(update={"shadow": False})]
    assert aggregate(voting).verdict == "fail"


def test_shadow_results_travel_on_a_field_the_gate_never_reads(shadow_bench):
    """Structural, not conventional, so a future refactor breaks here.

    The gate reads PanelVerdict. If someone later pipes shadow verdicts into the
    vote, they have to either add a shadow carrying field to PanelVerdict or hand
    JudgeVerdicts to the gate, and both are asserted against here. The two benches
    are also built by separate factories, so the lists are disjoint from the moment
    they are constructed rather than filtered apart later.
    """
    panel_fields = set(PanelVerdict.model_fields)
    assert "shadow" not in panel_fields
    assert not [name for name in panel_fields if "shadow" in name]

    # Nothing the gate is handed can hold a JudgeVerdict at all.
    for name, field in PanelVerdict.model_fields.items():
        annotation = repr(field.annotation)
        assert "JudgeVerdict" not in annotation, f"{name} can carry per judge verdicts"

    # The shadow flag lives on the per judge record instead, which is the type
    # aggregate refuses.
    assert "shadow" in JudgeVerdict.model_fields

    voting_names = {judge.name for judge in honest_mock_panel()}
    shadow_names = {judge.name for judge in shadow_bench}
    assert voting_names.isdisjoint(shadow_names)
    assert shadow_names == {judge.name for judge in shadow_mock_judges()}


def test_the_panel_record_keeps_the_disagreement_it_resolved(make_verdict):
    """A resolved 2-1 must not be reported as agreement.

    split and abstentions stay on the record rather than being recomputed later,
    because the reason a case was escalated or narrowly decided is exactly what an
    audit trail is for.
    """
    result = aggregate(
        [
            make_verdict("pass", judge_name="mock-strict"),
            make_verdict("pass", judge_name="mock-lenient"),
            make_verdict("fail", judge_name="mock-balanced"),
        ]
    )
    assert result.split is True
    assert result.unanimous is False
    assert result.votes == ("fail", "pass", "pass")

    unanimous = aggregate(
        [
            make_verdict("pass", judge_name="mock-strict"),
            make_verdict("pass", judge_name="mock-lenient"),
            make_verdict("pass", judge_name="mock-balanced"),
        ]
    )
    assert unanimous.unanimous is True
    assert unanimous.split is False


def test_an_empty_panel_is_an_error_rather_than_a_verdict():
    """No judges ran is not the same fact as the judges could not decide."""
    with pytest.raises(ValueError):
        aggregate([])


def test_the_panel_record_carries_the_case_it_judged(make_verdict):
    verdicts = [
        make_verdict("pass", case_id="gc-013", sut_version="sut.v2", repeat=3),
        make_verdict("pass", case_id="gc-013", sut_version="sut.v2", repeat=3),
        make_verdict("fail", case_id="gc-013", sut_version="sut.v2", repeat=3),
    ]
    result = aggregate(verdicts)
    assert result.case_id == "gc-013"
    assert result.sut_version == "sut.v2"
    assert result.repeat == 3
