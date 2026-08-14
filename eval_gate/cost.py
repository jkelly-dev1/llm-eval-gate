"""Token and dollar accounting per run, so SAMPLE_RUN.md can state the real
multiplier for a three judge panel instead of estimating it.

Two counting paths, and the difference between them is labeled everywhere it
appears:

  - Offline: characters divided by four. This is an APPROXIMATION and is marked
    as one in every report it reaches. It is not a tokenizer and must not be
    quoted as a token count.
  - Real Anthropic: client.messages.count_tokens, which is the API's own count.
    Never tiktoken. tiktoken is OpenAI's tokenizer and undercounts Claude input
    by a wide and content dependent margin, so using it here would produce a
    confident number that is simply wrong.

The panel's cost is the thing worth reporting, because a three judge panel is a
3x line item and the calibration layer's job is to say whether it bought
anything. Single judge and panel totals are printed side by side for that
reason.

TWO RULES THIS MODULE ENFORCES RATHER THAN DOCUMENTS

  - An unpriced model is never worth $0.00. price_for raises UnknownModelPrice,
    and the report builders turn that into a None that renders as
    NO PRICE ON FILE plus a report level warning. A model that was renamed or
    newly configured therefore shows as unknown, not as free, because a free
    judge would silently invert the finding this project publishes.
  - A dated price expires by date, not by hope. claude-sonnet-5's introductory
    rate is selected by comparing today against SONNET_INTRO_ENDS rather than by
    a default argument that would stay true forever.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from eval_gate.evals.golden import GOLDEN_SET, GoldenCase
from eval_gate.llm import MOCK_MODEL, LLMProvider
from eval_gate.prompts import PromptLibrary, render_judge_user_prompt

#: USD per million tokens, (input, output).
#:
#: PRICES ARE VERSION SENSITIVE AND ARE AS OF 2026-07. Anthropic figures from
#: Anthropic's pricing page, OpenAI figures from developers.openai.com/api/docs
#: /models. They are checked into the repo so an offline run can report dollars at
#: all, which means they go stale silently. Anything load bearing should re-read
#: the vendor's page rather than trusting this table.
#:
#: claude-sonnet-5 carries an introductory rate of 2.00/10.00 per MTok through
#: 2026-08-31, after which it reverts to 3.00/15.00. Both are recorded so the
#: report can show which one it used.
#:
#: gpt-5 is listed FOR COMPARISON ONLY. It is cheaper per token than the third
#: voting slot and is deliberately not used: OpenAI's own docs mark it superseded
#: and point at the GPT-5.6 line. A gate should not be built on a model the vendor
#: has moved off, and the price difference is not worth the migration it defers.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-5-intro": (2.00, 10.00),
    "gpt-5.6-sol": (5.00, 30.00),
    "gpt-5.6-terra": (2.50, 15.00),
    "gpt-5.6-luna": (1.00, 6.00),
    "gpt-5": (1.25, 10.00),  # superseded; comparison only
    "gpt-4o": (2.50, 10.00),
    MOCK_MODEL: (0.00, 0.00),
}

#: Which real model each offline mock judge stands in for, so a mock run can
#: report a meaningful price. Mocks cost nothing, which makes an unmapped cost
#: table useless for the question anyone actually has.
MOCK_STAND_INS: dict[str, str] = {
    "mock-strict": "claude-opus-5",
    "mock-lenient": "claude-sonnet-5",
    "mock-balanced": "gpt-5.6-terra",
    "mock-miscalibrated": "gpt-5.6-terra",
    "mock-verbosity": "gpt-5.6-luna",
    "mock-literalist": "gpt-4o",
    # Shadow slots 3 and 4, used only when SHADOW_JUDGE_MODELS configures that
    # many. Mapped here so a wider bench prices against real models instead of
    # falling through to the default stand in.
    "mock-numeric": "gpt-5.6-luna",
    "mock-formatter": "gpt-4o",
}


def stand_in_model(judge_name: str, model: str) -> str:
    """The model to price a judge at: itself, or what the mock stands in for."""
    if model != MOCK_MODEL:
        return model
    bare = judge_name.split(":")[-1].split("#")[0]
    return MOCK_STAND_INS.get(bare, "claude-opus-5")


SONNET_INTRO_ENDS = "2026-08-31"


class UnknownModelPrice(LookupError):
    """Raised when a model has no entry in PRICES.

    WHY THIS IS AN EXCEPTION AND NOT A ZERO: price_for used to return
    (0.00, 0.00) for a model it did not know, so renaming a model or configuring a
    new one made it report as FREE. That silently corrupts the one finding this
    project exists to publish, which is whether a dearer judge buys better
    agreement. A zero in a cost report is worse than a missing one, because a
    reader has no way to tell it apart from a genuinely free slot.
    """

    def __init__(self, model: str) -> None:
        super().__init__(
            f"no price on file for model {model!r}: add it to eval_gate.cost.PRICES "
            "as (input, output) USD per million tokens. It is deliberately NOT "
            "defaulted to zero, because an unpriced model reported at $0.00 reads "
            "as free rather than as unknown."
        )
        self.model = model


#: What a report prints in a dollars column it could not fill. Never a number, and
#: never 0.00, so a reader cannot mistake an unpriced judge for a cheap one.
NO_PRICE_MARKER = "NO PRICE ON FILE"


class PricingSlotMismatch(ValueError):
    """Raised when a report holds more judges than there are pricing slots.

    priced_as_real used to index `slots[index % len(slots)]`, so a four judge
    report priced against three slots quietly billed the fourth judge at the first
    slot's model. Silent modulo wraparound in a cost report is exactly the class of
    error this project argues against, so the mismatch is named and raised.
    """


def sonnet_intro_active(today: date | None = None) -> bool:
    """Whether claude-sonnet-5's introductory rate still applies on `today`.

    WHY THIS IS DATE DRIVEN: the intro rate was a hardcoded default of True, so
    after SONNET_INTRO_ENDS every report would have under-priced claude-sonnet-5 by
    a third ($2/$10 instead of $3/$15) with nothing in the output admitting it. The
    end date was already recorded; this is the comparison that was missing.
    """
    return (today or date.today()) <= date.fromisoformat(SONNET_INTRO_ENDS)


#: Characters per token in the offline approximation. Crude on purpose: a
#: precise looking constant would invite someone to quote the result as a token
#: count.
CHARS_PER_TOKEN = 4

#: A judge's JSON verdict is short and near constant in length, so the offline
#: output estimate is a flat figure rather than a measurement of nothing.
ESTIMATED_OUTPUT_TOKENS = 60


def approximate_tokens(text: str) -> int:
    """Characters divided by four. An approximation, labeled as such."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def price_for(model: str, *, sonnet_intro: bool | None = None) -> tuple[float, float]:
    """(input, output) USD per million tokens, or raise UnknownModelPrice.

    `sonnet_intro=None` means "decide by date", comparing today against
    SONNET_INTRO_ENDS. An explicit True or False forces the rate, which is what a
    test asserting a dollar figure must pass so that the suite does not start
    failing the day the intro rate lapses, and what reproducing an old capture
    needs.
    """
    if sonnet_intro is None:
        sonnet_intro = sonnet_intro_active()
    if model == "claude-sonnet-5" and sonnet_intro:
        return PRICES["claude-sonnet-5-intro"]
    try:
        return PRICES[model]
    except KeyError as exc:
        raise UnknownModelPrice(model) from exc


def price_for_report(
    model: str, *, sonnet_intro: bool | None = None
) -> tuple[float, float] | None:
    """price_for for report builders: None instead of a raise, never a zero.

    This is the ONE place UnknownModelPrice is turned into a renderable absence,
    so the conversion is a deliberate decision at a single site rather than a
    swallowed exception scattered through the callers. Every caller of this
    function has to carry the None into its output as NO_PRICE_MARKER, because
    None cannot be formatted as a number by accident the way 0.00 could.
    """
    try:
        return price_for(model, sonnet_intro=sonnet_intro)
    except UnknownModelPrice:
        return None


@dataclass
class JudgeCost:
    judge_name: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    #: None when the model has no entry in PRICES. None rather than 0.0 so that an
    #: unpriced judge cannot be summed, averaged, or printed as a dollar figure
    #: without something raising or rendering NO_PRICE_MARKER.
    input_usd: float | None
    output_usd: float | None
    #: True when the token figures came from characters/4 rather than a
    #: tokenizer. Carried on the record so a report cannot lose the caveat.
    approximate: bool

    @property
    def priced(self) -> bool:
        return self.input_usd is not None and self.output_usd is not None

    @property
    def total_usd(self) -> float | None:
        if not self.priced:
            return None
        return (self.input_usd or 0.0) + (self.output_usd or 0.0)


@dataclass
class CostReport:
    repeats: int
    cases: int
    versions: int
    per_judge: list[JudgeCost] = field(default_factory=list)
    approximate: bool = True

    @property
    def calls(self) -> int:
        return sum(item.calls for item in self.per_judge)

    @property
    def input_tokens(self) -> int:
        return sum(item.input_tokens for item in self.per_judge)

    @property
    def output_tokens(self) -> int:
        return sum(item.output_tokens for item in self.per_judge)

    @property
    def total_usd(self) -> float:
        """Sum over the judges that COULD be priced.

        Read with `unpriced_models`: a total that silently omitted an unpriced
        judge would understate the invoice, so the two are reported together and
        format_cost refuses to print this figure without the warning line.
        """
        return sum(item.total_usd or 0.0 for item in self.per_judge if item.priced)

    @property
    def unpriced_models(self) -> list[str]:
        """The models in this report with no PRICES entry, sorted and deduplicated."""
        return sorted({item.model for item in self.per_judge if not item.priced})

    @property
    def has_unpriced(self) -> bool:
        """The report level flag: at least one judge could not be priced at all."""
        return bool(self.unpriced_models)

    def single_judge_usd(self) -> float | None:
        """Cost of the first slot alone: the single judge comparison baseline.

        None when the first slot has no price on file, so the caller renders
        NO_PRICE_MARKER instead of a $0.00 that would read as a free judge.
        """
        if not self.per_judge:
            return 0.0
        return self.per_judge[0].total_usd

    def panel_multiplier(self) -> float:
        """Panel cost divided by single judge cost.

        It is not exactly the number of judges: the slots run different models at
        different prices, so a panel of three can cost anywhere from a little
        over 1x to well over 3x its first slot.
        """
        single = self.single_judge_usd()
        if single is None or single <= 0:
            return 0.0
        return self.total_usd / single


def _count_tokens(
    judge: LLMProvider, system: str, user: str, *, approximate_only: bool = False
) -> tuple[int, bool]:
    """Exact count where the provider offers one, approximation otherwise.

    `approximate_only` forces the approximation even on a provider that could
    answer exactly. It exists for the preflight estimate the real model pass
    prints BEFORE it spends anything: count_tokens is itself an API round trip, so
    asking a live panel what a run will cost would make 300 network calls to answer
    "should I authorize 460". An operator deciding whether to spend must not have
    to spend to find out.
    """
    counter = None if approximate_only else getattr(judge, "count_tokens", None)
    if callable(counter):  # pragma: no cover - needs a live key
        return int(counter(system=system, user=user)), False
    return approximate_tokens(system) + approximate_tokens(user), True


def estimate_cost(
    panel: list[LLMProvider],
    *,
    cases: tuple[GoldenCase, ...] = GOLDEN_SET,
    versions: tuple[str, ...] = ("sut.v1", "sut.v2"),
    repeats: int = 1,
    library: PromptLibrary | None = None,
    judge_prompt_version: str = "v1",
    sonnet_intro: bool | None = None,
    approximate_only: bool = False,
) -> CostReport:
    """Token and dollar cost of running `panel` over `cases` for `repeats`.

    `approximate_only=True` refuses to use a provider's exact counter even when it
    has one. See `_count_tokens`: the preflight estimate has to be free.
    """
    library = library or PromptLibrary()
    system = library.get("judge", judge_prompt_version).body
    report = CostReport(repeats=repeats, cases=len(cases), versions=len(versions))
    approximate_anywhere = False

    for judge in panel:
        input_tokens = 0
        calls = 0
        approximate = False
        for case in cases:
            for version in versions:
                user = render_judge_user_prompt(
                    case_id=case.case_id,
                    sut_version=version,
                    question=case.question,
                    source=case.source,
                    answer=case.answer(version),
                    attempt=1,
                )
                per_call, is_approximate = _count_tokens(
                    judge, system, user, approximate_only=approximate_only
                )
                approximate = approximate or is_approximate
                input_tokens += per_call * repeats
                calls += repeats
        output_tokens = ESTIMATED_OUTPUT_TOKENS * calls
        # An unpriced model yields None dollars, which format_cost renders as
        # NO_PRICE_MARKER. The token counts are still real and still reported.
        price = price_for_report(judge.model, sonnet_intro=sonnet_intro)
        report.per_judge.append(
            JudgeCost(
                judge_name=judge.name,
                model=judge.model,
                calls=calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                input_usd=None if price is None else input_tokens / 1_000_000 * price[0],
                output_usd=None if price is None else output_tokens / 1_000_000 * price[1],
                approximate=approximate,
            )
        )
        approximate_anywhere = approximate_anywhere or approximate

    report.approximate = approximate_anywhere
    return report


@dataclass(frozen=True)
class MeasuredUsage:
    """One judge's ACTUAL consumption over a completed run, already summed.

    Distinct from models.TokenUsage, which is one call as the vendor reported it.
    This is the per judge total a real pass accumulates from those, and it carries
    `exact` forward: a slot that fell back to a deterministic mock has no vendor
    accounting behind it, so its figures are the characters/4 approximation and the
    report has to keep saying so rather than presenting one measured column with a
    guess hidden in it.
    """

    judge_name: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    exact: bool


def cost_from_usage(
    usages: list[MeasuredUsage],
    *,
    repeats: int,
    cases: int,
    versions: int,
    sonnet_intro: bool | None = None,
) -> CostReport:
    """Price MEASURED token counts, the same way estimate_cost prices guessed ones.

    Same JudgeCost records, same PRICES table, same UnknownModelPrice to None
    conversion, so a measured report and an estimated one are directly comparable
    and format_cost renders both. The only difference is where the tokens came
    from, which is exactly the difference the real model section exists to show.
    """
    report = CostReport(repeats=repeats, cases=cases, versions=versions)
    for usage in usages:
        price = price_for_report(usage.model, sonnet_intro=sonnet_intro)
        report.per_judge.append(
            JudgeCost(
                judge_name=usage.judge_name,
                model=usage.model,
                calls=usage.calls,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                input_usd=(
                    None if price is None else usage.input_tokens / 1_000_000 * price[0]
                ),
                output_usd=(
                    None if price is None else usage.output_tokens / 1_000_000 * price[1]
                ),
                approximate=not usage.exact,
            )
        )
    # The whole report is approximate if ANY judge in it is, because the totals
    # line sums them together and a total that mixes a measurement with a guess is
    # a guess.
    report.approximate = any(item.approximate for item in report.per_judge)
    return report


@dataclass
class CostAccuracyRow:
    judge_name: str
    model: str
    voting: bool
    kappa: float
    false_pass_rate: float
    #: None when the judge's model has no PRICES entry. The row still appears, with
    #: its kappa, so an unpriced judge is visible rather than dropped; it just
    #: cannot claim a dollar figure.
    sweep_usd: float | None

    @property
    def priced(self) -> bool:
        return self.sweep_usd is not None


def cost_accuracy_table(
    report,
    *,
    repeats: int = 1,
    cases: tuple[GoldenCase, ...] = GOLDEN_SET,
    versions: tuple[str, ...] = ("sut.v1", "sut.v2"),
    library: PromptLibrary | None = None,
    sonnet_intro: bool | None = None,
    sweep_usd_by_judge: dict[str, float | None] | None = None,
) -> list[CostAccuracyRow]:
    """Does judge cost buy judge accuracy: one row per judge, sorted by dollars.

    `report` is a calibration report. Voting and shadow judges appear together
    because the comparison is the whole point: if the cheapest judge's kappa is
    within the run's own measured noise of the most expensive judge's, then the
    expensive judge is not buying agreement with humans, it is buying a bigger
    invoice. Sorting by dollars puts that question in front of the reader instead
    of leaving it to be derived.

    `sweep_usd_by_judge` replaces the estimated dollars with figures a real run
    actually measured, which is the only way this table answers its own question
    on real models. It must name EVERY judge in the report: a table that quietly
    fell back to the approximation for one judge would be comparing a measurement
    against a guess in the same column, and the row that ends up cheapest decides
    what the table says.
    """
    library = library or PromptLibrary()
    system = library.get("judge", "v1").body
    per_call = 0
    for case in cases:
        for version in versions:
            user = render_judge_user_prompt(
                case_id=case.case_id,
                sut_version=version,
                question=case.question,
                source=case.source,
                answer=case.answer(version),
                attempt=1,
            )
            per_call += approximate_tokens(system) + approximate_tokens(user)
    input_tokens = per_call * repeats
    calls = len(cases) * len(versions) * repeats
    output_tokens = ESTIMATED_OUTPUT_TOKENS * calls

    raters = [(item, True) for item in report.judges] + [
        (item, False) for item in report.shadow_judges
    ]
    if sweep_usd_by_judge is not None:
        missing = sorted(
            judge.name for judge, _voting in raters if judge.name not in sweep_usd_by_judge
        )
        if missing:
            raise PricingSlotMismatch(
                f"measured dollars were supplied for "
                f"{sorted(sweep_usd_by_judge)} but the report also holds "
                f"{missing}. Every judge needs a measured figure, or the table would "
                "mix measured dollars with the characters/4 estimate in one column."
            )

    rows: list[CostAccuracyRow] = []
    for judge, voting in raters:
        model = stand_in_model(judge.name, judge.model)
        price = price_for_report(model, sonnet_intro=sonnet_intro)
        sweep_usd = (
            None
            if price is None
            else input_tokens / 1_000_000 * price[0] + output_tokens / 1_000_000 * price[1]
        )
        if sweep_usd_by_judge is not None:
            sweep_usd = sweep_usd_by_judge[judge.name]
        rows.append(
            CostAccuracyRow(
                judge_name=judge.name,
                model=model,
                voting=voting,
                kappa=judge.kappa,
                false_pass_rate=judge.false_pass_rate,
                sweep_usd=sweep_usd,
            )
        )
    # Unpriced rows sort LAST and are excluded from the cheapest versus dearest
    # comparison below. Sorting them as 0.0 would make an unpriced judge the
    # "cheapest" and hand the report's headline to a model nobody has a price for.
    rows.sort(key=lambda row: (not row.priced, row.sweep_usd or 0.0, row.judge_name))
    return rows


def unpriced_rows(rows: list[CostAccuracyRow]) -> list[CostAccuracyRow]:
    """The rows with no price on file: the table's own unpriced flag."""
    return [row for row in rows if not row.priced]


def format_cost_accuracy(
    rows: list[CostAccuracyRow], *, noise: float, measured: bool = False
) -> str:
    """Print the table, and state plainly when price is buying nothing.

    `measured=True` says the dollars came from vendor reported usage on a real run
    rather than from characters/4. It only changes the footer, and it is a required
    argument of the caller's intent rather than something inferred, because a
    footer that claimed measured dollars over estimated ones would be the exact
    class of quiet mislabeling this module raises UnknownModelPrice to prevent.

    `noise` is the measured flip rate to compare a kappa gap against. It is the
    same variance figure the gate uses for its noise floor, so "within noise" here
    means the same thing it means there rather than a second, softer standard.

    A judge whose model has no price entry prints NO PRICE ON FILE in the dollars
    column and is called out under the table. It is never rendered as 0.0000 and
    never enters the cheapest versus dearest comparison, because an unpriced judge
    presented as the cheapest one would invert the finding this table exists for.
    """
    lines = ["Does judge cost buy judge accuracy (sorted by dollars per sweep)"]
    lines.append(
        f"  {'judge':<24} {'model':<18} {'votes':>5} {'kappa':>7} "
        f"{'falsePass':>10} {'$/sweep':>9}"
    )
    for row in rows:
        dollars = NO_PRICE_MARKER if row.sweep_usd is None else f"{row.sweep_usd:9.4f}"
        lines.append(
            f"  {row.judge_name:<24} {row.model:<18} "
            f"{'yes' if row.voting else 'no':>5} {row.kappa:>7.3f} "
            f"{row.false_pass_rate:>10.3f} {dollars:>9}"
        )
    unpriced = unpriced_rows(rows)
    if unpriced:
        lines.append(
            "  AT LEAST ONE JUDGE COULD NOT BE PRICED: "
            + ", ".join(f"{row.judge_name} ({row.model})" for row in unpriced)
            + f". {NO_PRICE_MARKER} means unknown, NOT free, and these rows are "
            "excluded from the comparison below. Add the model to "
            "eval_gate.cost.PRICES before quoting any figure here."
        )
    rows = [row for row in rows if row.priced]
    if len(rows) >= 2:
        cheapest, dearest = rows[0], rows[-1]
        gap = dearest.kappa - cheapest.kappa
        lines.append(
            f"  cheapest {cheapest.judge_name} ({cheapest.model}, ${cheapest.sweep_usd:.4f}) "
            f"kappa {cheapest.kappa:.3f}"
        )
        lines.append(
            f"  dearest  {dearest.judge_name} ({dearest.model}, ${dearest.sweep_usd:.4f}) "
            f"kappa {dearest.kappa:.3f}"
        )
        if abs(gap) <= max(noise, 1e-9):
            lines.append(
                f"  THE CHEAPEST JUDGE'S KAPPA IS WITHIN NOISE OF THE MOST EXPENSIVE: "
                f"gap {gap:+.3f}, measured noise {noise:.3f}. On this golden set the "
                f"extra spend is not buying agreement with humans."
            )
        else:
            lines.append(
                f"  the kappa gap is {gap:+.3f} against measured noise {noise:.3f}, so the "
                f"price difference is buying a real difference in agreement"
            )
        # The sharper question is not cheapest versus dearest, it is whether ANY
        # cheaper judge already matches the dearest. That is the row that decides
        # whether the expensive slot is worth keeping.
        matched = [
            row
            for row in rows[:-1]
            if abs(dearest.kappa - row.kappa) <= max(noise, 1e-9)
        ]
        if matched:
            best_value = matched[0]
            lines.append(
                f"  CHEAPER JUDGE WITHIN NOISE OF THE DEAREST: {best_value.judge_name} "
                f"({best_value.model}, ${best_value.sweep_usd:.4f}) kappa "
                f"{best_value.kappa:.3f} vs {dearest.kappa:.3f}, gap "
                f"{dearest.kappa - best_value.kappa:+.3f}. The extra "
                f"${dearest.sweep_usd - best_value.sweep_usd:.4f} per sweep is not "
                f"buying measurable agreement."
            )
    if measured:
        lines.append(
            "  dollars are MEASURED: vendor reported token usage for this run, priced "
            "against the models that actually ran; prices as of 2026-07"
        )
    else:
        lines.append(
            "  dollars are the offline characters/4 approximation priced against the models "
            "each judge stands in for; prices as of 2026-07"
        )
    return "\n".join(lines)


def real_panel_slots() -> list[tuple[str, str]]:
    """The models a real three judge panel would use, for a priced comparison.

    Offline runs cost nothing, which makes the mock cost report useless for
    answering "what would this have cost". This is the same arithmetic against
    the real price table so the demo can state the figure without a key.
    """
    return [
        ("anthropic", "claude-opus-5"),
        ("anthropic", "claude-sonnet-5"),
        ("openai", "gpt-5.6-terra"),
    ]


def real_shadow_slots() -> list[tuple[str, str]]:
    """The models the non voting shadow bench would use."""
    return [("openai", "gpt-5.6-luna"), ("openai", "gpt-4o")]


def priced_as_real(
    report: CostReport,
    *,
    slots: list[tuple[str, str]] | None = None,
    sonnet_intro: bool | None = None,
) -> CostReport:
    """Re-price an offline report against the real models, tokens unchanged.

    The token counts stay the offline approximation, which is why the returned
    report keeps `approximate=True`. It answers "roughly what would this run have
    cost against real models" and nothing more precise than that.

    More judges than slots is an error, not a wraparound. Taking the index
    modulo the slot count prices the extra judges against the wrong models
    without saying so.
    """
    slots = slots or real_panel_slots()
    if len(report.per_judge) > len(slots):
        raise PricingSlotMismatch(
            f"cannot price {len(report.per_judge)} judges against {len(slots)} "
            f"pricing slots ({', '.join(model for _vendor, model in slots)}). Pass a "
            "slots list with one entry per judge; reusing slots would bill some "
            "judges against a model they never ran."
        )
    repriced = CostReport(
        repeats=report.repeats,
        cases=report.cases,
        versions=report.versions,
        approximate=True,
    )
    for index, item in enumerate(report.per_judge):
        name, model = slots[index]
        price = price_for_report(model, sonnet_intro=sonnet_intro)
        repriced.per_judge.append(
            JudgeCost(
                judge_name=name,
                model=model,
                calls=item.calls,
                input_tokens=item.input_tokens,
                output_tokens=item.output_tokens,
                input_usd=None if price is None else item.input_tokens / 1_000_000 * price[0],
                output_usd=None if price is None else item.output_tokens / 1_000_000 * price[1],
                approximate=True,
            )
        )
    return repriced


def combined_total_usd(*reports: CostReport) -> float:
    """Voting panel plus shadow bench, so the invoice line is the honest one.

    Shadow judges do not vote, but they do bill. Reporting the panel total alone
    would understate what running this harness costs, which is the sort of
    omission that gets a shadow bench quietly switched off later by someone
    looking at a surprise invoice.
    """
    return sum(report.total_usd for report in reports)


def format_cost(report: CostReport, title: str) -> str:
    """An aligned block, with the approximation caveat attached to the number.

    An unpriced judge shows NO PRICE ON FILE where its dollars would go, and the
    block carries a report level warning saying the total is partial. Printing
    $0.0000 for that judge would understate the invoice AND read as free, which is
    the failure mode this formatter is written to make impossible.
    """
    lines = [title]
    for item in report.per_judge:
        dollars = NO_PRICE_MARKER if item.total_usd is None else f"${item.total_usd:>8.4f}"
        lines.append(
            f"  {item.judge_name:<20} {item.model:<22} "
            f"{item.calls:>5} calls  {item.input_tokens:>8} in  "
            f"{item.output_tokens:>6} out  {dollars}"
        )
    total_note = " (PRICED JUDGES ONLY)" if report.has_unpriced else ""
    lines.append(
        f"  {'TOTAL':<20} {'':<22} {report.calls:>5} calls  "
        f"{report.input_tokens:>8} in  {report.output_tokens:>6} out  "
        f"${report.total_usd:>8.4f}{total_note}"
    )
    single = report.single_judge_usd()
    single_rendered = NO_PRICE_MARKER if single is None else f"${single:.4f}"
    lines.append(
        f"  single judge (first slot) {single_rendered}  "
        f"panel ${report.total_usd:.4f}  multiplier {report.panel_multiplier():.2f}x"
    )
    if report.has_unpriced:
        lines.append(
            "  AT LEAST ONE JUDGE COULD NOT BE PRICED: "
            + ", ".join(report.unpriced_models)
            + f". {NO_PRICE_MARKER} means unknown, NOT free; the total above covers "
            "the priced judges only and is therefore an UNDERSTATEMENT. Add the "
            "model to eval_gate.cost.PRICES."
        )
    if report.approximate:
        lines.append(
            "  token counts are characters/4, an APPROXIMATION, not a tokenizer; "
            "real runs use client.messages.count_tokens"
        )
    intro_state = "in effect today" if sonnet_intro_active() else "EXPIRED as of today"
    lines.append(
        f"  prices as of 2026-07; claude-sonnet-5 intro rate runs through "
        f"{SONNET_INTRO_ENDS} and is {intro_state}"
    )
    return "\n".join(lines)
