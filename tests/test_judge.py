"""The tolerant parser, and why an unreadable judge is an abstention.

A real model wraps its JSON in code fences or apologizes before it, no matter what
the system prompt says. A judge that raises on that takes the whole eval run down
with it, so the parser is deliberately tolerant. The subtler failure is the
opposite one: a judge that coerces unreadable output into "fail" would attribute
its own parse failure to the system under test, and the gate would then block a
release on evidence that does not exist.

So the property is two sided. Unreadable output becomes verdict="abstain" with
raw_ok=False, and a refusal becomes an abstention too rather than a crash. An
abstention does not vote, and raw_ok keeps "the judge declined" distinguishable
from "we could not read the judge", which are different facts that a single
abstention count would silently merge.
"""

from __future__ import annotations

import json

import pytest

from eval_gate.judge import judge_case, parse_judge_response


def test_a_verdict_wrapped_in_code_fences_is_still_parsed(scripted_verdict_json):
    raw = f"```json\n{scripted_verdict_json('fail')}\n```"
    verdict, criteria, reasons, raw_ok = parse_judge_response(raw)
    assert verdict == "fail"
    assert raw_ok is True
    assert criteria["grounded_in_source"] is False


def test_a_verdict_with_prose_before_the_json_is_still_parsed(scripted_verdict_json):
    raw = (
        "Certainly. Here is my assessment of the candidate answer:\n"
        f"{scripted_verdict_json('pass')}\n"
        "Let me know if you would like the rubric applied more strictly."
    )
    verdict, _criteria, _reasons, raw_ok = parse_judge_response(raw)
    assert verdict == "pass"
    assert raw_ok is True


def test_unparseable_output_becomes_an_abstention_rather_than_an_exception(
    grounded_case, library, scripted_judge, monkeypatch
):
    """A judge that cannot be read must not take the run down with it.

    Mutation check, executed in-test: swap the tolerant parser for a strict
    json.loads and the same judge call raises instead of abstaining, which is the
    behavior this test exists to forbid.
    """
    judge = scripted_judge("I am afraid I cannot score that answer.")
    verdict, criteria, reasons, raw_ok = parse_judge_response(judge.raw)
    assert verdict == "abstain"
    assert raw_ok is False
    assert criteria == {}
    assert reasons == ["unparseable judge response"]

    result = judge_case(judge, grounded_case, "sut.v1", library=library)
    assert result.verdict == "abstain"
    assert result.raw_ok is False

    def strict_parser(raw: str):
        payload = json.loads(raw)
        return payload["verdict"], {}, [], True

    monkeypatch.setattr("eval_gate.judge.parse_judge_response", strict_parser)
    # json.JSONDecodeError, caught as its ValueError base so the assertion is about
    # the run dying rather than about which decoder raised.
    with pytest.raises(ValueError):
        judge_case(judge, grounded_case, "sut.v1", library=library)


def test_a_verdict_outside_the_contract_becomes_an_abstention():
    """Parsed JSON is not the same fact as a usable verdict.

    "affirmative" is readable and still not a verdict this system defines, so it
    abstains with raw_ok=False rather than being coerced toward pass or fail.
    """
    verdict, _criteria, reasons, raw_ok = parse_judge_response(
        json.dumps({"verdict": "affirmative", "criteria": {}, "reasons": []})
    )
    assert verdict == "abstain"
    assert raw_ok is False
    assert "affirmative" in reasons[0]


def test_a_refusal_stop_reason_becomes_an_abstention(refusing_anthropic_judge):
    """For a judge, a refusal is an abstention, not a crash.

    stop_reason is checked before response.content because a refusal carries no
    text block, so reading content[0] first would raise. Asserted against a fake
    response object rather than a live call.
    """
    raw = refusing_anthropic_judge.complete(system="rubric", user="case")
    verdict, _criteria, reasons, raw_ok = parse_judge_response(raw)
    assert verdict == "abstain"
    assert reasons == ["model refused"]
    # The judge declined deliberately and said so in the contract's own shape, so
    # the response parsed: this abstention is NOT a parse failure.
    assert raw_ok is True


def test_a_content_filtered_openai_response_becomes_an_abstention(content_filtered_openai_judge):
    """The same fact in the other vendor's spelling: finish_reason content_filter.

    The message body is None on a filtered completion, so the finish_reason check
    has to come first here too.
    """
    raw = content_filtered_openai_judge.complete(system="rubric", user="case")
    verdict, _criteria, reasons, raw_ok = parse_judge_response(raw)
    assert verdict == "abstain"
    assert reasons == ["content filtered"]
    assert raw_ok is True


def test_an_abstention_from_a_broken_parse_carries_raw_ok_false(
    grounded_case, library, scripted_judge
):
    """A broken judge and a cautious judge must not look the same in the report.

    Both abstain. Only one of them means the harness has lost a measurement, and
    raw_ok is what separates a rising abstention rate caused by a quietly broken
    judge from one caused by judges declining on genuinely ambiguous cases.
    """
    broken = judge_case(
        scripted_judge("<html>502 Bad Gateway</html>"), grounded_case, "sut.v1", library=library
    )
    declined = json.dumps({"verdict": "abstain", "criteria": {}, "reasons": ["too ambiguous"]})
    deliberate = judge_case(
        scripted_judge(declined),
        grounded_case,
        "sut.v1",
        library=library,
    )
    assert broken.verdict == deliberate.verdict == "abstain"
    assert broken.raw_ok is False
    assert deliberate.raw_ok is True
    assert deliberate.reasons == ["too ambiguous"]


def test_a_judge_verdict_records_the_slot_it_was_asked_for(
    grounded_case, library, scripted_judge
):
    """Two judges of the same kind must not merge into one row in the report."""
    result = judge_case(
        scripted_judge('{"verdict": "pass", "criteria": {}, "reasons": []}'),
        grounded_case,
        "sut.v2",
        library=library,
        judge_name="mock-miscalibrated#3",
        repeat=2,
    )
    assert result.judge_name == "mock-miscalibrated#3"
    assert result.sut_version == "sut.v2"
    assert result.repeat == 2
    assert result.shadow is False


def test_every_unreadable_shape_abstains_rather_than_being_coerced():
    """All four unreadable shapes, not just the two that have a test each.

    The claim is that a judge output which cannot be read becomes an abstention
    and is never coerced toward a verdict. Four distinct paths reach that
    decision, and coercing either of the two below toward "pass" or "fail" is
    invisible to a suite that only exercises the other two, which matters
    because "pass" is the direction that ships a regression.

    The `not isinstance(payload, dict)` branch in the parser is NOT exercised
    here, and no input can exercise it: the only text that reaches `json.loads`
    either starts with `{` or was sliced to start with `{`, and valid JSON
    beginning with `{` is always an object. It is defensive, and it is
    unreachable; a mutation of it changes nothing observable, so no test can
    hold it.

    Mutation check, executed against the parser: return "pass" from the
    JSONDecodeError branch, or coerce the off-contract verdict toward "pass",
    and this goes red.
    """
    shapes = {
        "no JSON at all": "I am afraid I cannot score that answer.",
        "prose with no closing brace": "here you go: {verdict: pass",
        "braces around invalid JSON": "{not valid json at all}",
        "fenced block around invalid JSON": "```json\n{nope}\n```",
        "valid JSON object, verdict off-contract": json.dumps({"verdict": "affirmative"}),
        "valid JSON object, verdict missing entirely": json.dumps({"reasons": []}),
    }
    for label, raw in shapes.items():
        verdict, criteria, reasons, raw_ok = parse_judge_response(raw)
        assert verdict == "abstain", f"{label}: coerced to {verdict!r}"
        assert raw_ok is False, f"{label}: reported as readable"
        assert criteria == {}, f"{label}: invented criteria"
        assert reasons, f"{label}: abstained without saying why"
