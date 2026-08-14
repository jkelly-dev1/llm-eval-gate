"""Cost accounting: a three judge panel is a 3x line item, so say so in dollars.

The property is that the cost report cannot flatter the design. A panel has to
come out more expensive than a single judge, in calls and in dollars, because the
whole calibration argument is about whether that extra spend bought anything. A
report that quietly totaled to the same figure either way would make the question
unaskable.

The second property is coverage of the price table. Every model this project would
actually call, voting or shadow, real or stood in for by a mock, needs a price
entry. A missing entry is not survivable here, so price_for RAISES
UnknownModelPrice rather than returning zero per million tokens: a zero in a cost
report is worse than a missing one, because it reads as "free" rather than as
"unknown" and would silently invert this project's headline finding. The report
builders convert that raise into no price on file plus a report level flag, and
the tests below pin both halves.

The third property is that a price which was SCHEDULED to change, and then did
not, must not be modeled as if it had. claude-sonnet-5's 2.00/10.00 became the
standard price when the increase to 3.00/15.00 was canceled, so nothing here
selects a rate by date. Dollar figures still pass sonnet_intro explicitly, which
now asserts that the flag cannot change the answer.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from eval_gate import cost
from eval_gate.config import Settings
from eval_gate.evals.golden import GOLDEN_SET
from eval_gate.cost import (
    MOCK_STAND_INS,
    NO_PRICE_MARKER,
    PRICES,
    PricingSlotMismatch,
    UnknownModelPrice,
    combined_total_usd,
    cost_accuracy_table,
    estimate_cost,
    format_cost,
    format_cost_accuracy,
    price_for,
    priced_as_real,
    real_panel_slots,
    real_shadow_slots,
    sonnet_intro_active,
    stand_in_model,
)
from eval_gate.llm import (
    MOCK_MODEL,
    build_mock_panel,
    get_panel,
    get_shadow_judges,
    unique_judge_names,
)

#: A short slice of the golden set. The cost property is about the ratio between a
#: panel and one judge, and that ratio does not depend on how many cases are swept.
CASES_SLICE = 4

#: A model id that is deliberately absent from PRICES. It is spelled like a real
#: successor model on purpose: a renamed or newly released judge is exactly how an
#: unpriced model gets into a run, and it must never be reported as free.
UNPRICED_MODEL = "claude-opus-6"


class UnpricedJudge:
    """A judge whose model has no PRICES entry, for the unpriced report paths.

    Local to this module rather than in conftest.py: it makes the no price on file
    claims here non-vacuous and nothing else needs it. It only has to satisfy what
    the cost layer reads, which is a name, a model, and no count_tokens method, so
    the offline characters/4 path is taken.
    """

    name = "judge-with-no-price"

    def __init__(self, model: str = UNPRICED_MODEL) -> None:
        self.model = model


class StubJudgeMetrics:
    """One judge's calibration numbers, as cost_accuracy_table reads them."""

    def __init__(self, name: str, model: str, kappa: float, false_pass_rate: float) -> None:
        self.name = name
        self.model = model
        self.kappa = kappa
        self.false_pass_rate = false_pass_rate


class StubCalibrationReport:
    """The two judge lists cost_accuracy_table needs, and nothing else.

    Local to this module because the claim it serves is about PRICING a report, not
    about producing one. Running a real calibration sweep to get four numbers would
    couple this test to the calibration layer another test already owns.
    """

    def __init__(
        self, judges: list[StubJudgeMetrics], shadow_judges: list[StubJudgeMetrics]
    ) -> None:
        self.judges = judges
        self.shadow_judges = shadow_judges


def assert_every_model_is_priced(models: list[str]) -> None:
    """The premise of the coverage test, factored out so a mutant can be run at it.

    sonnet_intro is pinned so that the day the introductory rate lapses cannot turn
    this helper red for a reason that has nothing to do with coverage.
    """
    for model in sorted(set(models)):
        assert model in cost.PRICES, f"no price entry for {model}"
        input_price, output_price = price_for(model, sonnet_intro=True)
        if model != MOCK_MODEL:
            assert input_price > 0 and output_price > 0, f"{model} is priced at zero"


def configured_models(settings: Settings) -> list[str]:
    """Every model a run of this harness could bill against."""
    models = [
        settings.model_for("anthropic"),
        settings.anthropic_model,
        settings.anthropic_model_secondary,
        settings.openai_model,
        settings.model_for("openai"),
        *settings.shadow_models(),
        *(model for _vendor, model in real_panel_slots()),
        *(model for _vendor, model in real_shadow_slots()),
    ]
    offline_judges = get_panel(settings) + get_shadow_judges(settings) + build_mock_panel(2)
    for judge, name in zip(offline_judges, unique_judge_names(offline_judges)):
        models.append(stand_in_model(name, judge.model))
    return models


def test_the_panel_costs_more_than_a_single_judge(settings: Settings, golden_set, library):
    """Three judges bill three times, and the report must show it in dollars.

    Offline mocks are priced at zero by design, so the dollar comparison is made
    through priced_as_real, which keeps the approximate token counts and prices
    them against the models the slots stand in for. That is the number a reader
    needs to judge whether the panel earned its cost.
    """
    cases = golden_set[:CASES_SLICE]
    panel = get_panel(settings)
    single = get_panel(Settings(**{**settings.model_dump(), "judge_panel_size": 1}))
    assert len(panel) == 3 and len(single) == 1

    panel_report = estimate_cost(panel, cases=cases, library=library)
    single_report = estimate_cost(single, cases=cases, library=library)

    assert panel_report.calls == 3 * single_report.calls
    assert panel_report.input_tokens > single_report.input_tokens

    # sonnet_intro is pinned because the second real slot is claude-sonnet-5, whose
    # rate changes on a date. An unpinned dollar comparison would change meaning on
    # 2026-09-01 for reasons that have nothing to do with panel cost.
    panel_usd = priced_as_real(panel_report, sonnet_intro=True)
    single_usd = priced_as_real(single_report, sonnet_intro=True)
    assert single_usd.total_usd > 0, "a real priced run cannot cost nothing"
    assert panel_usd.total_usd > single_usd.total_usd
    assert panel_usd.panel_multiplier() > 1.0
    assert panel_usd.single_judge_usd() == pytest.approx(single_usd.total_usd)


def test_the_shadow_bench_is_billed_even_though_it_does_not_vote(
    settings: Settings, golden_set, library
):
    """Shadow judges do not vote, but they do bill.

    Reporting the panel total alone understates what the harness costs, which is
    how a shadow bench gets switched off later by someone reading a surprise
    invoice rather than a report.
    """
    cases = golden_set[:CASES_SLICE]
    panel = priced_as_real(
        estimate_cost(get_panel(settings), cases=cases, library=library), sonnet_intro=True
    )
    shadow = priced_as_real(
        estimate_cost(get_shadow_judges(settings), cases=cases, library=library),
        slots=real_shadow_slots(),
        sonnet_intro=True,
    )
    assert shadow.total_usd > 0
    assert combined_total_usd(panel, shadow) > panel.total_usd


def test_the_cost_table_covers_every_configured_judge_model(settings: Settings, monkeypatch):
    """A model with no price entry must not be reported as free.

    This is the coverage half: every model a run could bill against is in PRICES
    today, so no real invocation lands on the unknown model path at all. The
    behavior half, that the unknown path raises rather than returning zero, is
    pinned by test_an_unknown_model_refuses_to_be_priced_at_zero.

    Mutation check, executed in-test: delete gpt-5.6-terra from PRICES, which is
    what adding a model or renaming one looks like from this test's point of view,
    and the premise above stops holding.
    """
    models = configured_models(settings)
    assert "gpt-5.6-terra" in models
    assert_every_model_is_priced(models)

    monkeypatch.delitem(cost.PRICES, "gpt-5.6-terra")
    with pytest.raises(AssertionError):
        assert_every_model_is_priced(models)


def test_every_offline_mock_judge_prices_against_a_real_stand_in_model(settings: Settings):
    """A mock run has to report the dollars the real run would have cost.

    Mocks are free, which makes an unmapped cost table useless for the only
    question anyone has. Slot qualified names (mock-miscalibrated#1) must resolve
    to the same stand in as the bare name, or a captured panel would silently fall
    back to the default price.
    """
    judges = get_panel(settings) + get_shadow_judges(settings) + build_mock_panel(2)
    names = unique_judge_names(judges)
    for judge, name in zip(judges, names):
        assert judge.model == MOCK_MODEL
        bare = name.split("#")[0]
        assert bare in MOCK_STAND_INS, f"{bare} has no stand in model"
        assert stand_in_model(name, judge.model) == MOCK_STAND_INS[bare]
        assert stand_in_model(name, judge.model) in PRICES

    suffixed = [name for name in unique_judge_names(build_mock_panel(2)) if "#" in name]
    assert suffixed, "a captured panel repeats a judge name and must be slot qualified"


def test_an_offline_cost_report_labels_its_token_counts_as_approximate(
    settings: Settings, golden_set, library
):
    """characters/4 is not a tokenizer, and the caveat rides on the number.

    Dropping the caveat would let an approximation be quoted as a token count,
    which is the kind of confident wrong number this project exists to argue
    against.
    """
    report = estimate_cost(get_panel(settings), cases=golden_set[:CASES_SLICE], library=library)
    assert report.approximate is True
    assert all(item.approximate for item in report.per_judge)
    rendered = format_cost(report, "offline panel")
    assert "APPROXIMATION" in rendered
    assert "count_tokens" in rendered
    assert priced_as_real(report).approximate is True


def test_an_unknown_model_refuses_to_be_priced_at_zero():
    """A model with no price entry raises instead of costing nothing.

    price_for used to answer (0.00, 0.00) for anything it did not recognize, so a
    renamed or newly configured judge reported as FREE and the project's headline
    finding, whether a dearer judge buys better agreement, quietly inverted. The
    exception has to name the model and point at the table, because the person who
    sees it is the person who renamed the model.

    Mutation check: restore the PRICES.get(model, (0.00, 0.00)) fallback in
    price_for and this test fails.
    """
    assert UNPRICED_MODEL not in PRICES

    with pytest.raises(UnknownModelPrice) as raised:
        price_for(UNPRICED_MODEL)

    assert raised.value.model == UNPRICED_MODEL
    message = str(raised.value)
    assert UNPRICED_MODEL in message
    assert "PRICES" in message

    # The known models still price, so the raise is discriminating rather than a
    # blanket refusal.
    assert price_for("claude-opus-5") == PRICES["claude-opus-5"]


def test_a_report_row_for_an_unpriced_judge_says_no_price_on_file_not_zero_dollars(
    settings: Settings, golden_set, library, monkeypatch
):
    """The dollars column carries a marker, and the report carries a flag.

    A reader who cannot see that a judge went unpriced will read the total as the
    whole invoice. So the row prints no price on file with no dollar figure at all,
    the report exposes unpriced_models, and the block says in words that its total
    covers the priced judges only and is therefore an understatement.

    Mutation check, executed in-test: give the unknown model a (0.00, 0.00) entry,
    which is what "just default it to zero" looks like, and the marker is replaced
    by a dollar figure that reads as free.
    """
    offline = estimate_cost(
        get_panel(settings)[:2], cases=golden_set[:CASES_SLICE], library=library
    )
    slots = [("anthropic", "claude-opus-5"), ("anthropic", UNPRICED_MODEL)]
    report = priced_as_real(offline, slots=slots, sonnet_intro=True)

    priced_row, unpriced_row = report.per_judge
    assert priced_row.priced is True and priced_row.total_usd > 0
    assert unpriced_row.priced is False
    assert unpriced_row.total_usd is None
    assert unpriced_row.input_usd is None and unpriced_row.output_usd is None
    # The tokens are still real and still reported; it is only the price that is
    # unknown, and the two must not be confused.
    assert unpriced_row.input_tokens > 0

    assert report.has_unpriced is True
    assert report.unpriced_models == [UNPRICED_MODEL]
    assert report.total_usd == pytest.approx(priced_row.total_usd)

    rendered = format_cost(report, "priced as real")
    row_line = [line for line in rendered.splitlines() if UNPRICED_MODEL in line][0]
    assert NO_PRICE_MARKER in row_line
    assert "$" not in row_line, "an unpriced judge must not show a dollar figure at all"
    assert "PRICED JUDGES ONLY" in rendered
    assert "AT LEAST ONE JUDGE COULD NOT BE PRICED" in rendered
    assert "UNDERSTATEMENT" in rendered

    monkeypatch.setitem(cost.PRICES, UNPRICED_MODEL, (0.00, 0.00))
    mutated = format_cost(priced_as_real(offline, slots=slots, sonnet_intro=True), "mutated")
    assert NO_PRICE_MARKER not in mutated, "mutation must actually remove the marker"
    assert "$  0.0000" in mutated, "the mutation is exactly the zero this test forbids"


def test_the_cost_accuracy_table_never_calls_an_unpriced_judge_the_cheapest(monkeypatch):
    """An unpriced judge must not win the comparison the table exists to make.

    This table's headline is cheapest versus dearest kappa. Sorting an unpriced
    judge as 0.0000 would hand that headline to a model nobody has a price for and
    print "the extra spend is not buying agreement" on the strength of it. So the
    unpriced row still appears, with its kappa, sorted last and excluded from the
    comparison, under a line saying it could not be priced.

    Mutation check, executed in-test: price the unknown model at (0.00, 0.00) and
    the unpriced judge becomes the cheapest row, which is the inversion this test
    is here to prevent.
    """
    report = StubCalibrationReport(
        judges=[
            StubJudgeMetrics("mock-strict", "claude-opus-5", kappa=0.62, false_pass_rate=0.05),
            StubJudgeMetrics("mystery-judge", UNPRICED_MODEL, kappa=0.10, false_pass_rate=0.44),
        ],
        shadow_judges=[
            StubJudgeMetrics("mock-verbosity", "gpt-5.6-luna", kappa=0.21, false_pass_rate=0.38)
        ],
    )

    rows = cost_accuracy_table(report, cases=GOLDEN_SET[:CASES_SLICE], sonnet_intro=True)
    by_name = {row.judge_name: row for row in rows}
    assert by_name["mystery-judge"].sweep_usd is None
    assert by_name["mystery-judge"].priced is False
    assert rows[-1].judge_name == "mystery-judge", "an unpriced row sorts last, never first"
    assert rows[0].judge_name == "mock-verbosity", "gpt-5.6-luna is the cheapest priced judge"

    rendered = format_cost_accuracy(rows, noise=0.05)
    assert NO_PRICE_MARKER in rendered
    assert "AT LEAST ONE JUDGE COULD NOT BE PRICED" in rendered
    cheapest_line = [line for line in rendered.splitlines() if line.startswith("  cheapest")][0]
    assert "mystery-judge" not in cheapest_line

    monkeypatch.setitem(cost.PRICES, UNPRICED_MODEL, (0.00, 0.00))
    mutated = cost_accuracy_table(report, cases=GOLDEN_SET[:CASES_SLICE], sonnet_intro=True)
    assert mutated[0].judge_name == "mystery-judge", "mutation must change the ranking"
    mutated_cheapest = [
        line
        for line in format_cost_accuracy(mutated, noise=0.05).splitlines()
        if line.startswith("  cheapest")
    ][0]
    assert "mystery-judge" in mutated_cheapest


def test_the_sonnet_price_is_fixed_because_the_scheduled_increase_was_canceled():
    """A CANCELED price change is not an expiry, and must not be modeled as one.

    claude-sonnet-5's 2.00/10.00 per MTok was announced as an introductory rate
    ending 2026-08-31. The vendor made that figure the standard price and
    canceled the increase to 3.00/15.00. A report that switched rates on
    2026-09-01 would have over-priced every claude-sonnet-5 figure by fifty
    percent, and would have looked like a dated guard working correctly, so it
    needs a test rather than a deletion.

    The date is asserted from BOTH sides so that reintroducing any date
    comparison here fails, whatever direction it points.

    Mutation check, executed against the pricing: make `sonnet_intro_active`
    consult the clock again, or restore PRICES["claude-sonnet-5"] to
    (3.00, 15.00), and this goes red.
    """
    ended = date.fromisoformat(cost.SONNET_INTRO_ENDED)
    for day in (ended - timedelta(days=1), ended, ended + timedelta(days=1),
                ended + timedelta(days=400)):
        assert sonnet_intro_active(day) is True, f"the rate moved on {day}"

    assert PRICES["claude-sonnet-5"] == (2.00, 10.00)
    assert price_for("claude-sonnet-5") == (2.00, 10.00)
    assert price_for("claude-sonnet-5", sonnet_intro=True) == (2.00, 10.00)
    assert price_for("claude-sonnet-5", sonnet_intro=False) == (2.00, 10.00), (
        "the flag still selects a rate, so a caller can still be handed the "
        "canceled price"
    )

    report = estimate_cost(
        [UnpricedJudge(model="claude-sonnet-5")], cases=GOLDEN_SET[:CASES_SLICE]
    )
    priced = priced_as_real(report, slots=[("anthropic", "claude-sonnet-5")])
    priced_forced = priced_as_real(
        report, slots=[("anthropic", "claude-sonnet-5")], sonnet_intro=False
    )
    assert priced.total_usd == priced_forced.total_usd, (
        "a dollar total still depends on the canceled rate"
    )


def test_pricing_more_judges_than_slots_is_an_error_not_a_wraparound(
    settings: Settings, golden_set, library
):
    """A five judge report against three slots must raise, not wrap around.

    priced_as_real indexed slots[index % len(slots)], so judges four and five were
    billed against slots one and two: the wrong models, at the wrong prices, with
    the wrong vendor names printed beside them, and no sign in the output that
    anything had been reused. That is the same class of error as pricing an unknown
    model at zero, so it is named and raised.

    Mutation check: restore the modulo index in priced_as_real and this test fails.
    """
    judges = get_panel(settings) + get_shadow_judges(settings)
    assert len(judges) == 5
    report = estimate_cost(judges, cases=golden_set[:CASES_SLICE], library=library)

    with pytest.raises(PricingSlotMismatch) as raised:
        priced_as_real(report, sonnet_intro=True)
    message = str(raised.value)
    assert "5" in message and "3" in message

    # The fix is a slots list per judge, and then every judge is priced against its
    # OWN slot rather than a recycled one.
    slots = real_panel_slots() + real_shadow_slots()
    repriced = priced_as_real(report, slots=slots, sonnet_intro=True)
    assert [item.model for item in repriced.per_judge] == [model for _vendor, model in slots]
    assert len({item.model for item in repriced.per_judge}) == 5

    # Fewer judges than slots stays legal: that is the single judge baseline.
    single = priced_as_real(
        estimate_cost(judges[:1], cases=golden_set[:CASES_SLICE], library=library),
        sonnet_intro=True,
    )
    assert len(single.per_judge) == 1
