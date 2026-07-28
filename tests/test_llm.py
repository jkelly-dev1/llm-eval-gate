"""Provider selection, and whether the offline panel can disagree at all.

Provider selection is a safety property, not a convenience. A stray environment
variable must not be able to send a demo run to a paid API, and a key alone must
not select a provider whose name was never asked for. The same rule applies per
panel slot: when one slot has no credential it falls back to a mock, and the run
has to say so, because a partially real panel reported as a real three model
panel is a lie about the evidence a release decision rests on.

The second property here is the one everything downstream leans on. The three
offline judges must genuinely disagree somewhere. If they agreed everywhere, every
split, unanimity, correlation and noise floor number this project reports would be
an artifact of a fixture rather than a measurement, and the calibration argument
would be vacuous while still printing confident figures.

The third property is that the offline shadow bench is the size its configuration
says. SHADOW_JUDGE_MODELS decides how many shadow judges a run builds, offline as
well as online, and each one is a distinct behavior. A bench that quietly built two
judges when none or four were configured would report shadow metrics for a bench
nobody asked for, and shadow correlation numbers built from repeated behaviors
would be a property of the fixture rather than of the judges.
"""

from __future__ import annotations

import pytest

from eval_gate.config import Settings
from eval_gate.judge import judge_case
from eval_gate.llm import (
    MOCK_MODEL,
    SHADOW_MOCK_BENCH,
    MockJudgeStrict,
    ShadowBenchExhausted,
    get_panel,
    get_provider,
    get_shadow_judges,
    honest_mock_panel,
    is_mock,
    panel_degraded,
    shadow_mock_judges,
)
from eval_gate.panel import aggregate


def test_the_default_provider_is_the_offline_mock(settings: Settings):
    provider = get_provider(settings)
    assert is_mock(provider)
    assert isinstance(provider, MockJudgeStrict)


def test_the_offline_settings_carry_no_api_keys(settings: Settings):
    """The precondition for every other claim in the suite.

    A leaked key would not make these tests fail, it would make them meaningless,
    so the absence is asserted rather than assumed.
    """
    assert settings.anthropic_api_key is None
    assert settings.openai_api_key is None
    assert Settings().anthropic_api_key is None
    assert Settings().openai_api_key is None


def test_provider_name_without_a_key_falls_back_to_mock():
    settings = Settings(agent_provider="anthropic", anthropic_api_key=None)
    assert is_mock(get_provider(settings))
    assert get_provider(settings).model == MOCK_MODEL


def test_a_key_without_the_matching_provider_name_stays_on_mock():
    settings = Settings(agent_provider="mock", anthropic_api_key="sk-ant-test")
    assert is_mock(get_provider(settings))


def test_keys_do_not_cross_match_between_providers():
    settings = Settings(agent_provider="openai", anthropic_api_key="sk-ant-test")
    assert is_mock(get_provider(settings))
    settings = Settings(agent_provider="anthropic", openai_api_key="sk-openai-test")
    assert is_mock(get_provider(settings))


def test_an_offline_panel_holds_three_distinct_judges_rather_than_three_clones(settings: Settings):
    panel = get_panel(settings)
    assert len(panel) == 3
    assert sorted(judge.name for judge in panel) == [
        "mock-balanced",
        "mock-lenient",
        "mock-strict",
    ]
    assert all(is_mock(judge) for judge in panel)


def test_a_partially_real_panel_is_recorded_as_degraded(
    stub_real_providers, make_verdict, monkeypatch
):
    """A mixed panel must be flagged, never reported as a real three model panel.

    Mutation check, executed in-test: make is_mock always answer False, which is
    what "treat the fallback slot as if it were real" looks like in code, and the
    degraded flag this test just asserted goes away.
    """
    settings = Settings(
        agent_provider="anthropic",
        anthropic_api_key="sk-ant-test",
        openai_api_key=None,
    )
    panel = get_panel(settings)

    assert len(panel) == 3
    assert not is_mock(panel[0])
    assert not is_mock(panel[1])
    assert is_mock(panel[2]), "the third slot needs the other vendor's key"
    assert panel_degraded(panel) is True

    # The flag has to reach the record the gate and the audit trail read, not
    # just the helper that computed it.
    verdicts = [make_verdict("pass"), make_verdict("pass"), make_verdict("fail")]
    result = aggregate(verdicts, degraded=panel_degraded(panel))
    assert result.verdict == "pass"
    assert result.degraded is True

    monkeypatch.setattr("eval_gate.llm.is_mock", lambda provider: False)
    assert panel_degraded(panel) is False, "mutation must actually break the premise"


def test_a_panel_with_both_credentials_is_not_flagged_as_degraded(stub_real_providers):
    """The discriminating counterpart: degraded must not simply always be True."""
    settings = Settings(
        agent_provider="anthropic",
        anthropic_api_key="sk-ant-test",
        openai_api_key="sk-openai-test",
    )
    panel = get_panel(settings)
    assert not any(is_mock(judge) for judge in panel)
    assert panel_degraded(panel) is False


def test_the_shadow_bench_falls_back_to_mocks_without_a_credential(settings: Settings):
    shadow = get_shadow_judges(settings)
    assert [judge.name for judge in shadow] == ["mock-verbosity", "mock-literalist"]
    assert all(is_mock(judge) for judge in shadow)


def test_the_three_offline_judges_do_not_all_agree_on_every_case(
    honest_panel, golden_set, library
):
    """Offline disagreement has to be real, or every panel metric measures nothing.

    This is what makes the calibration numbers mean something: the split rate, the
    unanimity rate, the pairwise error correlations and the measured noise floor
    are all derived from these three judges differing, so the difference is
    asserted here rather than assumed everywhere else.
    """
    names = [judge.name for judge in honest_panel]
    verdicts: dict[str, dict[tuple[str, str], str]] = {name: {} for name in names}
    for case in golden_set:
        for version in ("sut.v1", "sut.v2"):
            for judge in honest_panel:
                result = judge_case(judge, case, version, library=library)
                verdicts[judge.name][(case.case_id, version)] = result.verdict

    keys = sorted(verdicts[names[0]])
    disagreements = [
        key for key in keys if len({verdicts[name][key] for name in names}) > 1
    ]
    assert disagreements, "the offline panel is unanimous on every case"

    for left in range(len(names)):
        for right in range(left + 1, len(names)):
            first, second = names[left], names[right]
            differing = [key for key in keys if verdicts[first][key] != verdicts[second][key]]
            assert differing, f"{first} and {second} never disagree"


def test_each_offline_judge_both_passes_and_fails_somewhere(honest_panel, golden_set, library):
    """A judge with one constant answer would make its own agreement number a fake.

    A panel can disagree while one member is a rubber stamp, so the per judge
    spread is checked too rather than inferred from the panel level split.
    """
    for judge in honest_panel:
        seen = {
            judge_case(judge, case, version, library=library).verdict
            for case in golden_set
            for version in ("sut.v1", "sut.v2")
        }
        assert {"pass", "fail"} <= seen, f"{judge.name} never changes its answer"


def verdict_series(judge, cases, library) -> list[str]:
    """One judge's verdict for every case and version, in a stable order.

    Local to this module: it exists to make the shadow bench's distinctness claim a
    measurement over the golden set rather than an assertion about class names.
    """
    return [
        judge_case(judge, case, version, library=library).verdict
        for case in cases
        for version in ("sut.v1", "sut.v2")
    ]


def assert_shadow_behaviors_are_distinct(judges, cases, library) -> None:
    """The premise of the bench size claim, factored out so a mutant can be run at it.

    N shadow slots are only worth filling if the N behaviors differ. If two of them
    agreed everywhere, the shadow bench's pairwise correlation numbers would be a
    property of the fixture and the cost versus accuracy table would compare a judge
    against itself at a different price.
    """
    series = {judge.name: verdict_series(judge, cases, library) for judge in judges}
    assert len(series) == len(judges), "two shadow judges share a name"
    names = sorted(series)
    for left in range(len(names)):
        assert {"pass", "fail"} <= set(series[names[left]]), f"{names[left]} never changes answer"
        for right in range(left + 1, len(names)):
            first, second = names[left], names[right]
            differing = [
                index
                for index, (one, other) in enumerate(zip(series[first], series[second]))
                if one != other
            ]
            assert differing, f"{first} and {second} never disagree"


def test_the_offline_shadow_bench_is_the_size_its_configuration_asks_for(settings: Settings):
    """SHADOW_JUDGE_MODELS decides the bench size offline too, or it decides nothing.

    The old fallback was `shadow_mock_judges()[: len(models)] or shadow_mock_judges()`,
    so an empty SHADOW_JUDGE_MODELS still produced two shadow judges and four
    configured models still produced two. Both directions are pinned here, because
    both were wrong in the same way: the run reported a bench size nobody configured,
    and every shadow metric in the report was then about a bench that did not exist.

    Mutation check: restore the `or shadow_mock_judges()` fallback, or slice the bench
    to a fixed two, and this test fails.
    """
    base = settings.model_dump()

    empty = Settings(**{**base, "shadow_judge_models": ""})
    assert empty.shadow_models() == []
    assert get_shadow_judges(empty) == [], "no models configured means no shadow bench"

    one = Settings(**{**base, "shadow_judge_models": "gpt-5.6-luna"})
    assert len(get_shadow_judges(one)) == 1

    assert len(get_shadow_judges(settings)) == len(settings.shadow_models()) == 2

    four = Settings(
        **{**base, "shadow_judge_models": "gpt-5.6-luna,gpt-4o,gpt-5.6-terra,gpt-5.6-sol"}
    )
    wide = get_shadow_judges(four)
    assert len(wide) == 4
    assert len({judge.name for judge in wide}) == 4, "four slots need four behaviors"
    assert all(is_mock(judge) for judge in wide)

    # The invariant the separate factory exists for survives a wider bench: a shadow
    # judge is never one of the voting judges.
    voting = {judge.name for judge in honest_mock_panel()}
    assert voting.isdisjoint({judge.name for judge in wide})

    # More slots than distinct behaviors is refused by name, rather than silently
    # returning a shorter bench or padding it with a repeat.
    five = Settings(
        **{
            **base,
            "shadow_judge_models": "gpt-5.6-luna,gpt-4o,gpt-5.6-terra,gpt-5.6-sol,gpt-5",
        }
    )
    with pytest.raises(ShadowBenchExhausted) as raised:
        get_shadow_judges(five)
    message = str(raised.value)
    assert "5" in message and str(len(SHADOW_MOCK_BENCH)) in message

    # Turning the bench off entirely still wins over any model list.
    off = Settings(**{**base, "shadow_judges": False})
    assert get_shadow_judges(off) == []


def test_every_offline_shadow_judge_is_a_genuinely_distinct_behavior(
    golden_set, library, monkeypatch
):
    """A wider bench has to mean more behaviors, not more instances of two.

    This is what makes the previous test's bench size claim worth anything: filling
    four slots is only honest if the four judges actually differ, so the difference is
    measured over the golden set rather than inferred from four class names.

    Mutation check, executed in-test: replace the third bench entry with a copy of the
    first, which is what padding a bench with a repeat looks like, and the distinctness
    premise stops holding.
    """
    bench = shadow_mock_judges(len(SHADOW_MOCK_BENCH))
    assert len(bench) == 4
    assert_shadow_behaviors_are_distinct(bench, golden_set, library)

    padded = (SHADOW_MOCK_BENCH[0], SHADOW_MOCK_BENCH[1], SHADOW_MOCK_BENCH[0])
    monkeypatch.setattr("eval_gate.llm.SHADOW_MOCK_BENCH", padded)
    with pytest.raises(AssertionError):
        assert_shadow_behaviors_are_distinct(shadow_mock_judges(3), golden_set, library)
