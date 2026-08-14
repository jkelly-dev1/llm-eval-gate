"""The real model measurement pass: live judges, measured, and never a gate.

Why this module exists at all

Every other path in this project runs on the deterministic mock, and that is
correct: a regression gate has to be reproducible, the golden set pins which
judge disagrees with which human on which case, and a live model moves those
around between runs. But a harness whose entire claim is that its numbers are
measured cannot ship without ever having measured a real judge. The demo used to
build a live panel, print its description, and then run every number on mocks,
which meant a capture headed "real model run" would have been a fabrication in
the one repository that argues against exactly that.

So this is the one section that spends money, and the one section that cannot
change an exit code. Nothing here is read by evaluate_gate, no GateDecision is
constructed, and no threshold in config.py is compared against any number this
module produces. That is enforced by the shape of the code rather than by a
comment: run_real_pass returns a RealPassResult that has no decision field to
read.

The same seam, not a second code path

The calibration and consistency sweeps go through run_panel and judge_case, the
same functions the offline sections use. The provider seam is the project's
claim; a real-models-only measurement path would quietly make it false, because
then the offline run would no longer be exercising the code the real run uses.
All this module adds around that seam is GuardedJudge, and a wrapper that
proxies name and model is not a second path, it is the same path with the
failure and accounting behavior a paid run needs.

Two passes, sized for two different questions

  - The CALIBRATION pass sweeps the whole golden set once, both sut versions.
    Kappa over 60 judgments per judge is a number worth quoting. Kappa over ten
    cases is a number that moves when one case flips, and this project's whole
    subject is the difference between those two things.
  - The CONSISTENCY pass re-scores a small subset at several repeats. A flip rate
    needs repeats, and repeats are what multiply the invoice, so the subset is
    small and deliberate. Repeat 1 of every consistency case was already bought by
    the calibration pass and is reused rather than re-purchased.

What a failing judge does

It abstains, with the reason recorded, and the pass carries on. Aborting 400
calls into a paid run because call 401 timed out would throw away the whole
measurement, and an abstention is already the project's answer to a judge that
cannot be read: it does not vote, it is counted separately from agreement, and a
judge that is quietly broken therefore shows up as a rising abstention rate
rather than as a plausible looking accuracy number.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from eval_gate.calibration import CalibrationReport, ConsistencyReport, measure_consistency
from eval_gate.config import Settings
from eval_gate.cost import (
    ESTIMATED_OUTPUT_TOKENS,
    NO_PRICE_MARKER,
    CostReport,
    MeasuredUsage,
    approximate_tokens,
    combined_total_usd,
    cost_accuracy_table,
    cost_from_usage,
    estimate_cost,
    format_cost,
    format_cost_accuracy,
)
from eval_gate.evals.golden import BASELINE, CANDIDATE, GOLDEN_SET, GoldenCase
from eval_gate.evals.runner import build_report, run_panel
from eval_gate.judge import parse_judge_response
from eval_gate.llm import LLMProvider, is_mock, slot_labels, unique_judge_names
from eval_gate.models import RUBRIC_CRITERIA, TokenUsage
from eval_gate.prompts import PromptLibrary

#: Printed in the section itself, not only in this docstring. An operator reading
#: a capture should not have to take the repository's word for it.
NO_GATE_LINE = (
    "NOTHING IN THIS SECTION GATES. No exit code, no deployment decision and no "
    "threshold reads any number above it. The gate ran on the deterministic mock "
    "panel in the sections before this one, because a regression gate has to be "
    "reproducible and a live model moves its own numbers between runs."
)

_CASE_ID = re.compile(r"^CASE_ID:\s*(\S+)\s*$", re.MULTILINE)
_SUT_VERSION = re.compile(r"^SUT_VERSION:\s*(\S+)\s*$", re.MULTILINE)
_ATTEMPT = re.compile(r"^ATTEMPT:\s*(\d+)\s*$", re.MULTILINE)


def prompt_context(user: str) -> tuple[str, str, int]:
    """(case_id, sut_version, attempt) read back off the rendered judge prompt.

    The wrapper is handed a prompt string, not a case object, because that is what
    the provider Protocol passes. Recovering the three identifiers from the prompt
    keeps the wrapper on the same seam as the judges it wraps rather than needing
    its own channel from the runner.
    """
    case = _CASE_ID.search(user)
    version = _SUT_VERSION.search(user)
    attempt = _ATTEMPT.search(user)
    return (
        case.group(1) if case else "?",
        version.group(1) if version else "?",
        int(attempt.group(1)) if attempt else 0,
    )


def abstention_json(reason: str) -> str:
    """The judge response a failed call is replaced with.

    Deliberately the same shape a live judge returns for a refusal, so it travels
    through parse_judge_response and lands in the calibration layer as an
    abstention with a reason rather than as a special case anything downstream has
    to know about.
    """
    return json.dumps(
        {
            "verdict": "abstain",
            "criteria": {name: False for name in RUBRIC_CRITERIA},
            "reasons": [reason],
        }
    )


# --------------------------------------------------------------------------- #
# The plan, and what it will cost, both printed before anything is spent.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PassPlan:
    """How big the paid pass is, in the units an invoice is denominated in.

    Every figure here is derived rather than typed, so the printed call count and
    the number of calls the run actually makes cannot drift apart.
    """

    cases: int
    repeat_cases: int
    repeats: int
    voting_judges: int
    shadow_judges: int
    versions: int = 2
    #: Which cases a reduced pass buys. See select_cases and discriminating_cases.
    selection: str = "prefix"

    @property
    def judges(self) -> int:
        return self.voting_judges + self.shadow_judges

    @property
    def calibration_calls_per_judge(self) -> int:
        return self.cases * self.versions

    @property
    def extra_repeat_calls_per_judge(self) -> int:
        """Repeats 2..N only. Repeat 1 is reused from the calibration pass."""
        return self.repeat_cases * self.versions * max(0, self.repeats - 1)

    @property
    def calls_per_judge(self) -> int:
        return self.calibration_calls_per_judge + self.extra_repeat_calls_per_judge

    @property
    def total_calls(self) -> int:
        return self.calls_per_judge * self.judges

    @property
    def calls_saved_by_reuse(self) -> int:
        """What running the consistency pass from repeat 1 would have added."""
        return self.repeat_cases * self.versions * self.judges if self.repeats else 0

    @property
    def naive_total_calls(self) -> int:
        return self.total_calls + self.calls_saved_by_reuse

    @property
    def measures_consistency(self) -> bool:
        return self.repeats > 1 and self.repeat_cases > 0


def plan_for(settings: Settings, *, voting_judges: int, shadow_judges: int) -> PassPlan:
    """Build the plan from settings, which the demo's flags have already overridden."""
    return PassPlan(
        cases=settings.real_pass_cases,
        repeat_cases=settings.real_pass_repeat_cases,
        repeats=settings.real_pass_repeats,
        voting_judges=voting_judges,
        shadow_judges=shadow_judges,
        selection=settings.real_pass_case_selection,
    )


#: How a reduced pass picks its cases. "prefix" is the whole-set default; see
#: discriminating_cases for why a reduced pass should not use it.
CASE_SELECTIONS = ("prefix", "discriminating")


def discriminating_cases(
    cases: tuple[GoldenCase, ...] = GOLDEN_SET,
) -> tuple[GoldenCase, ...]:
    """Cases the two sut versions are labeled differently on, both directions first.

    A reduced pass picked by prefix measures nothing, and this exists because it
    happened. An 8 case run over gc-001..gc-008 returned kappa 1.000 for every
    judge and a 100 percent pass rate on both sut versions, because those eight
    cases are grounded and pass in both versions: the subset contained no case the
    two versions disagree about, so there was nothing for a judge to be right or
    wrong about in a way that separates the versions. That is this project's own
    cases_that_never_discriminate warning, arriving as a paid capture.

    The order alternates between the two directions, each direction sorted by case
    id: a regressed case (v1 pass, v2 fail), then a fixed one (v1 fail, v2 pass),
    and so on until one side runs out. Taking the regressed cases in case id order
    alone would give a subset every version disagreement points the same way, where
    a judge that answers "fail" to everything scores well for the wrong reason. A
    subset holding both directions cannot be gamed that way, and a consistency
    subset taken from the front of it inherits the property.
    """
    regressed = sorted(
        (case for case in cases if case.label(BASELINE) == "pass" and case.label(CANDIDATE) == "fail"),
        key=lambda case: case.case_id,
    )
    fixed = sorted(
        (case for case in cases if case.label(BASELINE) == "fail" and case.label(CANDIDATE) == "pass"),
        key=lambda case: case.case_id,
    )
    interleaved: list[GoldenCase] = []
    for index in range(max(len(regressed), len(fixed))):
        if index < len(regressed):
            interleaved.append(regressed[index])
        if index < len(fixed):
            interleaved.append(fixed[index])
    return tuple(interleaved)


def describe_ids(case_ids: list[str] | tuple[str, ...], selection: str) -> str:
    """How the capture names the subset it bought.

    A prefix is a contiguous run of case ids and reads correctly as "first..last".
    A discriminating subset is not contiguous, so the same shorthand would name
    cases the run never touched: an 8 case discriminating sweep written as
    "gc-015..gc-020" claims six cases and omits gc-025 and gc-026 entirely. Those
    are listed in full instead. A capture that misnames its own sample is worse
    than one that is verbose about it, and this project is about that gap.
    """
    if not case_ids:
        return "none"
    if selection == "prefix":
        return f"{case_ids[0]}..{case_ids[-1]}"
    return f"{selection}: " + ", ".join(case_ids)


def describe_cases(cases: tuple[GoldenCase, ...], selection: str) -> str:
    """describe_ids for callers holding cases rather than ids."""
    return describe_ids([case.case_id for case in cases], selection)


def select_cases(
    count: int,
    *,
    cases: tuple[GoldenCase, ...] = GOLDEN_SET,
    selection: str = "prefix",
) -> tuple[GoldenCase, ...]:
    """The first `count` cases of the named selection.

    "prefix" is every case sorted by case_id ascending, which is the right subset
    when `count` is the whole golden set: sorted by case_id rather than taken in
    declaration order, so the subset is a property of the data and not of where
    someone happened to insert a case.

    "discriminating" is for a REDUCED pass, and see discriminating_cases for what
    a reduced prefix measures instead. Both selections are deterministic and both
    are taken from the front, which is what keeps the consistency subset a strict
    prefix of the calibration subset and therefore reusable.

    Asking for more cases than the selection holds raises instead of clamping. A
    run that silently measured 30 cases when 50 were requested would print a case
    count nobody configured, which is the same defect ShadowBenchExhausted exists
    to prevent one module over.
    """
    if count < 1:
        raise ValueError(f"a pass needs at least one case, got {count}")
    if selection not in CASE_SELECTIONS:
        raise ValueError(
            f"unknown case selection {selection!r}; expected one of "
            f"{', '.join(CASE_SELECTIONS)}"
        )
    if selection == "discriminating":
        ordered = discriminating_cases(cases)
        available = (
            f"{count} cases were requested but only {len(ordered)} of the golden "
            f"set's {len(cases)} are labeled differently on the two sut versions. "
            "Lower --real-cases, or run the whole set with the default prefix "
            "selection, rather than measuring a smaller set than the one the "
            "report will name."
        )
    else:
        ordered = tuple(sorted(cases, key=lambda case: case.case_id))
        available = (
            f"{count} cases were requested but the golden set holds {len(ordered)}. "
            "Lower --real-cases or --real-repeat-cases rather than measuring a "
            "smaller set than the one the report will name."
        )
    if count > len(ordered):
        raise ValueError(available)
    return ordered[:count]


@dataclass(frozen=True)
class SlotEstimate:
    slot: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    #: None when the model has no PRICES entry. Never 0.0: see cost.py.
    usd: float | None


@dataclass
class Preflight:
    """What the pass will do and what it should cost, before it does any of it."""

    plan: PassPlan
    voting: list[SlotEstimate] = field(default_factory=list)
    shadow: list[SlotEstimate] = field(default_factory=list)

    @property
    def slots(self) -> list[SlotEstimate]:
        return list(self.voting) + list(self.shadow)

    @property
    def voting_usd(self) -> float:
        return sum(slot.usd or 0.0 for slot in self.voting)

    @property
    def shadow_usd(self) -> float:
        return sum(slot.usd or 0.0 for slot in self.shadow)

    @property
    def total_usd(self) -> float:
        return self.voting_usd + self.shadow_usd

    @property
    def input_tokens(self) -> int:
        return sum(slot.input_tokens for slot in self.slots)

    def by_slot(self) -> dict[str, SlotEstimate]:
        return {slot.slot: slot for slot in self.slots}


def _estimate_slots(
    panel: list[LLMProvider],
    names: list[str],
    plan: PassPlan,
    *,
    library: PromptLibrary,
    sonnet_intro: bool | None,
) -> list[SlotEstimate]:
    """Per slot preflight figures, from the existing estimate_cost arithmetic.

    Two calls to estimate_cost rather than one, because the two passes have
    different case counts and different repeat counts, and folding them into one
    average would produce a figure that describes neither.
    """
    calibration = estimate_cost(
        panel,
        cases=select_cases(plan.cases, selection=plan.selection),
        repeats=1,
        library=library,
        sonnet_intro=sonnet_intro,
        approximate_only=True,
    )
    extra = (
        estimate_cost(
            panel,
            cases=select_cases(plan.repeat_cases, selection=plan.selection),
            repeats=plan.repeats - 1,
            library=library,
            sonnet_intro=sonnet_intro,
            approximate_only=True,
        )
        if plan.measures_consistency
        else None
    )
    slots: list[SlotEstimate] = []
    for index, name in enumerate(names):
        first = calibration.per_judge[index]
        second = extra.per_judge[index] if extra else None
        usd = first.total_usd
        if usd is not None and second is not None:
            usd = usd + (second.total_usd or 0.0)
        slots.append(
            SlotEstimate(
                slot=name,
                model=first.model,
                calls=first.calls + (second.calls if second else 0),
                input_tokens=first.input_tokens + (second.input_tokens if second else 0),
                output_tokens=first.output_tokens
                + (second.output_tokens if second else 0),
                usd=usd,
            )
        )
    return slots


def preflight(
    plan: PassPlan,
    panel: list[LLMProvider],
    shadow: list[LLMProvider],
    *,
    library: PromptLibrary | None = None,
    sonnet_intro: bool | None = None,
) -> Preflight:
    """Cost the plan without making a single network call. See _count_tokens."""
    library = library or PromptLibrary()
    return Preflight(
        plan=plan,
        voting=_estimate_slots(
            panel,
            unique_judge_names(panel),
            plan,
            library=library,
            sonnet_intro=sonnet_intro,
        ),
        shadow=_estimate_slots(
            shadow,
            shadow_slot_names(shadow),
            plan,
            library=library,
            sonnet_intro=sonnet_intro,
        )
        if shadow
        else [],
    )


def format_preflight(pre: Preflight) -> str:
    """The block an operator reads before authorizing the spend."""
    plan = pre.plan
    lines = ["Before any call is made: the plan, the call count, and the estimate"]
    lines.append(
        f"  calibration pass      {plan.cases} cases x {plan.versions} versions x 1 "
        f"repeat = {plan.calibration_calls_per_judge} judgments per judge"
    )
    if plan.measures_consistency:
        lines.append(
            f"  consistency pass      {plan.repeat_cases} cases x {plan.versions} "
            f"versions x repeats 2..{plan.repeats} = "
            f"{plan.extra_repeat_calls_per_judge} more per judge"
        )
    else:
        lines.append(
            f"  consistency pass      SKIPPED at repeats={plan.repeats}: a flip rate "
            f"needs at least two repeats of the same case"
        )
    lines.append(
        f"  judges                {plan.voting_judges} voting + {plan.shadow_judges} "
        f"shadow = {plan.judges}"
    )
    lines.append(
        f"  TOTAL API CALLS       {plan.total_calls}  "
        f"({plan.calls_per_judge} per judge)"
    )
    if plan.calls_saved_by_reuse:
        lines.append(
            f"                        repeat 1 of every consistency case is reused from "
            f"the calibration pass, so this is {plan.calls_saved_by_reuse} fewer than "
            f"the {plan.naive_total_calls} two independent passes would cost"
        )
    lines.append(
        f"  {'slot':<24} {'model':<18} {'calls':>6} {'est in':>9} {'est out':>8} "
        f"{'est $':>9}"
    )
    for slot in pre.slots:
        dollars = NO_PRICE_MARKER if slot.usd is None else f"{slot.usd:9.4f}"
        lines.append(
            f"  {slot.slot:<24} {slot.model:<18} {slot.calls:>6} "
            f"{slot.input_tokens:>9} {slot.output_tokens:>8} {dollars:>9}"
        )
    lines.append(
        f"  ESTIMATED COST        voting ${pre.voting_usd:.4f}  +  shadow "
        f"${pre.shadow_usd:.4f}  =  ${pre.total_usd:.4f}"
    )
    lines.append(
        "  the estimate is the offline characters/4 approximation, priced against the "
        "models in the slots. It is NOT a token count. The measured figures below "
        "replace it, and the gap between the two is reported."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The guard: one flaky call must not end a paid run.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class JudgeIncident:
    """One call that produced no usable verdict, and why.

    Kept per call rather than summed per judge, because "which cases did this
    judge refuse" is the question an operator has after a paid run, and a count
    cannot answer it.
    """

    slot: str
    case_id: str
    sut_version: str
    repeat: int
    kind: str
    detail: str
    #: False when the call raised, so nothing was returned to count tokens from.
    billed: bool = True


class GuardedJudge:
    """One live judge, wrapped so a single bad call cannot end a paid run.

    It proxies `name` and `model` so unique_judge_names, panel_degraded and
    describe_panel see exactly what they would see without it, which is what keeps
    the slot names in the report identical to the slot names in the cost table.

    Three things happen here that cannot happen inside judge_case. The exception
    from a live SDK becomes a recorded abstention instead of ending the run. The
    vendor's token accounting is picked up off the provider before the next call
    overwrites it. And progress is printed per call, because 460 calls is several
    minutes of silence otherwise and a paid run that cannot be watched cannot be
    debugged.
    """

    def __init__(self, judge: LLMProvider, slot: str, *, echo=print) -> None:
        self._judge = judge
        self._echo = echo
        self.slot = slot
        self.name = judge.name
        self.model = judge.model
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        #: False as soon as one call had to be counted by approximation.
        self.exact = True
        self.incidents: list[JudgeIncident] = []

    def usage(self) -> MeasuredUsage:
        return MeasuredUsage(
            judge_name=self.slot,
            model=self.model,
            calls=self.calls,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            exact=self.exact,
        )

    def complete(self, *, system: str, user: str) -> str:
        case_id, version, repeat = prompt_context(user)
        try:
            raw = self._judge.complete(system=system, user=user)
        except Exception as exc:  # deliberately broad: see the class docstring
            reason = f"judge error: {type(exc).__name__}: {exc}"
            self.calls += 1
            self.incidents.append(
                JudgeIncident(
                    slot=self.slot,
                    case_id=case_id,
                    sut_version=version,
                    repeat=repeat,
                    kind=type(exc).__name__,
                    detail=str(exc)[:160],
                    billed=False,
                )
            )
            self._progress(case_id, version, repeat, "abstain", f"ERROR {type(exc).__name__}")
            return abstention_json(reason)

        self.calls += 1
        self._account(system, user)
        verdict, _criteria, reasons, raw_ok = parse_judge_response(raw)
        note = ""
        if verdict == "abstain":
            kind = "unparseable" if not raw_ok else "abstained"
            detail = "; ".join(reasons)[:160] or "no reason given"
            self.incidents.append(
                JudgeIncident(
                    slot=self.slot,
                    case_id=case_id,
                    sut_version=version,
                    repeat=repeat,
                    kind=kind,
                    detail=detail,
                )
            )
            note = f"{kind}: {detail}"
        self._progress(case_id, version, repeat, verdict, note)
        return raw

    def _account(self, system: str, user: str) -> None:
        """Vendor reported usage where there is any, the approximation otherwise.

        A slot that fell back to a deterministic mock has no usage to report, and
        neither does an SDK version that stops returning one. Either way the
        approximation is used AND `exact` goes false, so the cost report keeps
        saying the total is not a measurement.
        """
        usage: TokenUsage | None = getattr(self._judge, "last_usage", None)
        if usage is None:
            self.exact = False
            self.input_tokens += approximate_tokens(system) + approximate_tokens(user)
            self.output_tokens += ESTIMATED_OUTPUT_TOKENS
            return
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.exact = self.exact and usage.exact

    def _progress(
        self, case_id: str, version: str, repeat: int, verdict: str, note: str
    ) -> None:
        self._echo(
            f"    {case_id:<7} {version:<7} r{repeat}  {self.slot:<24} "
            f"{verdict:<8} {note}".rstrip()
        )


def shadow_slot_names(shadow: list[LLMProvider]) -> list[str]:
    """The names runner.py gives shadow judges, computed the same way.

    Duplicated deliberately rather than imported, because run_panel builds these
    names inline and the cost table has to key on the identical strings. If the two
    ever disagree, cost_accuracy_table raises PricingSlotMismatch naming the judges
    that did not line up, which is a loud failure rather than a mispriced table.
    """
    return [f"shadow:{name}" for name in unique_judge_names(shadow)]


def guard(panel: list[LLMProvider], names: list[str], *, echo=print) -> list[GuardedJudge]:
    return [
        GuardedJudge(judge, slot, echo=echo) for judge, slot in zip(panel, names)
    ]


# --------------------------------------------------------------------------- #
# The pass itself.
# --------------------------------------------------------------------------- #


@dataclass
class RealPassResult:
    """Everything the paid pass measured. There is no decision field, on purpose.

    A reader looking for the gate outcome of a real model run will not find one
    here, because there is not one. The offline sections decided the exit code
    before this pass started.
    """

    plan: PassPlan
    preflight: Preflight
    calibration_case_ids: tuple[str, ...]
    consistency_case_ids: tuple[str, ...]
    slots: list[tuple[str, str, str]]
    degraded: bool
    report: CalibrationReport
    consistency: ConsistencyReport
    voting_cost: CostReport
    shadow_cost: CostReport
    incidents: list[JudgeIncident] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return self.voting_cost.calls + self.shadow_cost.calls

    @property
    def measured_usd(self) -> float:
        return combined_total_usd(self.voting_cost, self.shadow_cost)

    def sweep_usd_by_judge(self) -> dict[str, float | None]:
        """Measured dollars keyed by the names the calibration report uses."""
        return {
            item.judge_name: item.total_usd
            for item in list(self.voting_cost.per_judge) + list(self.shadow_cost.per_judge)
        }


def run_real_pass(
    settings: Settings,
    *,
    panel: list[LLMProvider],
    shadow: list[LLMProvider],
    plan: PassPlan,
    library: PromptLibrary | None = None,
    echo=print,
    sonnet_intro: bool | None = None,
) -> RealPassResult:
    """Run both passes through the ordinary run_panel seam and measure the result.

    The settings copies below change only the repeat count. The panel, the shadow
    bench, the prompts, the aggregation rule and the calibration arithmetic are the
    ones every offline section uses: if this needed its own measurement code, the
    offline path would no longer be exercising the code a real run takes.
    """
    library = library or PromptLibrary()
    calibration_cases = select_cases(plan.cases, selection=plan.selection)
    consistency_cases = select_cases(plan.repeat_cases, selection=plan.selection)

    voting_names = unique_judge_names(panel)
    shadow_names = shadow_slot_names(shadow)
    pre = preflight(plan, panel, shadow, library=library, sonnet_intro=sonnet_intro)
    echo(format_preflight(pre))
    echo("")

    guarded_panel = guard(panel, voting_names, echo=echo)
    guarded_shadow = guard(shadow, shadow_names, echo=echo)

    echo(
        f"  calibration pass: {len(calibration_cases)} cases "
        f"({describe_cases(calibration_cases, plan.selection)}), both sut "
        f"versions, 1 repeat"
    )
    calibration = run_panel(
        settings.model_copy(update={"judge_repeats": 1}),
        panel=guarded_panel,
        shadow=guarded_shadow,
        cases=calibration_cases,
        library=library,
    )
    report = build_report(calibration)

    consistency_result = None
    if plan.measures_consistency:
        echo("")
        echo(
            f"  consistency pass: {len(consistency_cases)} cases "
            f"({describe_cases(consistency_cases, plan.selection)}), both "
            f"sut versions, repeats 2..{plan.repeats} (repeat 1 reused from above)"
        )
        consistency_result = run_panel(
            settings.model_copy(update={"judge_repeats": plan.repeats}),
            panel=guarded_panel,
            shadow=guarded_shadow,
            cases=consistency_cases,
            library=library,
            first_repeat=2,
        )

    consistency = merge_consistency(
        calibration, consistency_result, consistency_cases, plan.repeats
    )

    voting_cost = cost_from_usage(
        [judge.usage() for judge in guarded_panel],
        repeats=plan.repeats,
        cases=plan.cases,
        versions=plan.versions,
        sonnet_intro=sonnet_intro,
    )
    shadow_cost = cost_from_usage(
        [judge.usage() for judge in guarded_shadow],
        repeats=plan.repeats,
        cases=plan.cases,
        versions=plan.versions,
        sonnet_intro=sonnet_intro,
    )

    incidents = [
        incident
        for judge in list(guarded_panel) + list(guarded_shadow)
        for incident in judge.incidents
    ]

    return RealPassResult(
        plan=plan,
        preflight=pre,
        calibration_case_ids=tuple(case.case_id for case in calibration_cases),
        consistency_case_ids=tuple(case.case_id for case in consistency_cases),
        slots=[
            (slot, judge.model, label)
            for slot, judge, label in zip(
                voting_names + shadow_names,
                list(panel) + list(shadow),
                slot_labels(list(panel) + list(shadow)),
            )
        ],
        degraded=any(is_mock(judge) for judge in list(panel) + list(shadow)),
        report=report,
        consistency=consistency,
        voting_cost=voting_cost,
        shadow_cost=shadow_cost,
        incidents=incidents,
    )


def merge_consistency(
    calibration,
    consistency_result,
    consistency_cases: tuple[GoldenCase, ...],
    repeats: int,
) -> ConsistencyReport:
    """Repeat 1 from the calibration pass, repeats 2..N from the consistency pass.

    Merged at the series level and handed to the SAME measure_consistency the
    offline path uses, so the flip rate means what it means everywhere else.
    Restricted to the consistency units, because a unit scored once has a flip rate
    of zero by construction and letting those into the denominator would divide a
    real flip count by the whole golden set and report a noise floor several times
    smaller than the measured one.
    """
    wanted = {case.case_id for case in consistency_cases}

    def units(series: dict) -> list:
        return [unit for unit in sorted(series) if unit[1] in wanted]

    if consistency_result is None:
        per_judge = {
            name: {unit: list(calibration.judge_series[name][unit]) for unit in units(calibration.judge_series[name])}
            for name in sorted(calibration.judge_series)
        }
        panel = {unit: list(calibration.panel_series[unit]) for unit in units(calibration.panel_series)}
        report = measure_consistency(per_judge, panel, repeats)
        if calibration.shadow_series:
            shadow = {
                name: {unit: list(calibration.shadow_series[name][unit]) for unit in units(calibration.shadow_series[name])}
                for name in sorted(calibration.shadow_series)
            }
            report.shadow_flip_rate = measure_consistency(shadow, panel, repeats).per_judge_flip_rate
        return report

    def merged(first: dict, second: dict) -> dict:
        return {
            unit: list(first.get(unit, [])) + list(second.get(unit, []))
            for unit in sorted(second)
        }

    per_judge = {
        name: merged(calibration.judge_series[name], consistency_result.judge_series[name])
        for name in sorted(consistency_result.judge_series)
    }
    panel = merged(calibration.panel_series, consistency_result.panel_series)
    report = measure_consistency(per_judge, panel, repeats)
    if consistency_result.shadow_series:
        shadow = {
            name: merged(
                calibration.shadow_series[name], consistency_result.shadow_series[name]
            )
            for name in sorted(consistency_result.shadow_series)
        }
        report.shadow_flip_rate = measure_consistency(
            shadow, panel, repeats
        ).per_judge_flip_rate
    return report


# --------------------------------------------------------------------------- #
# Reporting.
# --------------------------------------------------------------------------- #


def skipped(plan: PassPlan) -> str:
    """What the section prints offline, so the offline capture stays complete.

    It prints the call count the real pass WOULD make at the configured sizes,
    computed from the same PassPlan the real run uses rather than from a number
    written into this string, so the offline capture cannot quote a figure the
    paid run would not honor.
    """
    return "\n".join(
        [
            "  SKIPPED: AGENT_PROVIDER is \"mock\", so there is no live judge to measure.",
            "  This is the only section that calls a real model, and it is the only",
            "  section whose numbers can never reach an exit code. Everything above ran",
            "  on the deterministic offline panel, which is what makes the gate",
            "  reproducible and what makes this section necessary: five hand written mock",
            "  judges demonstrate that the harness measures whatever judges it is given,",
            "  and demonstrate nothing whatsoever about how a real judge behaves.",
            "",
            "  To run it:",
            "    ENV_FILE=~/.secrets/ai.env AGENT_PROVIDER=anthropic python scripts/run_demo.py",
            "",
            f"  At the configured sizes that is {plan.total_calls} API calls: "
            f"{plan.cases} cases x {plan.versions} versions x 1 repeat for the",
            f"  calibration pass, plus {plan.repeat_cases} cases x {plan.versions} "
            f"versions x repeats 2..{plan.repeats} for the consistency",
            f"  pass, across {plan.voting_judges} voting and {plan.shadow_judges} shadow "
            f"judges. Size it with --real-cases,",
            "  --real-repeat-cases and --real-repeats. The run prints its own call count",
            "  and dollar estimate before it makes the first call.",
        ]
    )


def _rater_table(raters, header: str) -> list[str]:
    lines = [header]
    lines.append(
        f"  {'judge':<24} {'agree':>6} {'kappa':>7} {'falsePass':>10} "
        f"{'falseFail':>10} {'abstain':>8}"
    )
    for rater in raters:
        lines.append(
            f"  {rater.name:<24} {rater.raw_agreement:>6.3f} {rater.kappa:>7.3f} "
            f"{rater.false_pass_rate:>10.3f} {rater.false_fail_rate:>10.3f} "
            f"{rater.abstention_rate:>8.3f}"
        )
    return lines


def _correlation_table(pairs) -> list[str]:
    lines = [
        f"  {'pair':<46} {'joint':>7} {'indep':>7} {'ratio':>7}  interpretation"
    ]
    for pair in pairs:
        label = f"{pair.judge_a} + {pair.judge_b}"
        lines.append(
            f"  {label:<46} {pair.joint_error_rate:>7.3f} "
            f"{pair.expected_if_independent:>7.3f} {pair.ratio:>7.2f}  "
            f"{pair.interpretation}"
        )
    return lines


def format_real_pass(result: RealPassResult) -> str:
    """The whole report for the paid pass. No exit code, and it says so."""
    report = result.report
    lines: list[str] = []

    def row(label: str, value: str) -> None:
        lines.append(f"  {label:<32} {value}")

    lines.append("")
    lines.append("Which slots were real")
    for slot, model, label in result.slots:
        lines.append(f"  {slot:<24} {model:<18} {label}")
    row(
        "degraded",
        "YES, at least one slot is a deterministic mock"
        if result.degraded
        else "no, every slot above is a live model",
    )
    # Both lines name the subset through describe_cases rather than as a case id
    # range: a discriminating subset is not contiguous, so "gc-015..gc-026" would
    # claim ten cases the run never bought. A capture that misdescribes its own
    # sample is the failure this whole project is about.
    row(
        "calibration cases",
        f"{len(result.calibration_case_ids)} "
        f"({describe_ids(result.calibration_case_ids, result.plan.selection)}), "
        f"both sut versions, 1 repeat",
    )
    # "first N by case_id" describes how a PREFIX subset was chosen and is the
    # useful half of that row. It is false of a discriminating subset, which is
    # ordered to alternate the two directions, so it is omitted rather than
    # reworded: describe_ids has already named those cases individually.
    ordering = (
        f"first {len(result.consistency_case_ids)} by case_id, "
        if result.plan.selection == "prefix"
        else ""
    )
    row(
        "consistency cases",
        f"{len(result.consistency_case_ids)} "
        f"({describe_ids(result.consistency_case_ids, result.plan.selection)}), "
        f"{ordering}{result.plan.repeats} repeats",
    )
    row("calls made", str(result.calls))

    lines.append("")
    lines.extend(_rater_table(report.judges, "Per judge, against human labels (REAL MODELS)"))
    panel = report.panel
    lines.append(
        f"  {'PANEL':<24} {panel.raw_agreement:>6.3f} {panel.kappa:>7.3f} "
        f"{panel.false_pass_rate:>10.3f} {panel.false_fail_rate:>10.3f} "
        f"{panel.abstention_rate:>8.3f}"
    )
    lines.append(
        "  raw agreement is misleading on this skewed label set; kappa is the number to read"
    )

    if report.shadow_judges:
        lines.append("")
        lines.extend(
            _rater_table(
                report.shadow_judges, "Shadow judges (measured, NON VOTING, never gate)"
            )
        )

    lines.append("")
    lines.append("Panel")
    row("unanimity_rate", f"{panel.unanimity_rate:.3f}")
    row("split_rate", f"{panel.split_rate:.3f}")
    row("escalation_rate", f"{panel.escalation_rate:.3f}")
    row(
        "best_single_judge",
        f"{panel.best_single_judge} (kappa {panel.best_single_judge_kappa:.3f})",
    )
    row(
        "panel_kappa_vs_best_single_judge",
        f"{panel.kappa_vs_best_single_judge:+.3f}",
    )
    if not panel.panel_earned_its_cost:
        lines.append(
            "  THE PANEL DID NOT BEAT ITS BEST MEMBER on real models: three judges cost "
            "3x and bought nothing measurable here"
        )
    for name in (report.baseline_version, report.candidate_version):
        row(f"pass_rate[{name}]", f"{panel.pass_rate.get(name, 0.0):.3f}")

    lines.append("")
    lines.append("Pairwise error correlation, every pair of judges that ran")
    lines.extend(_correlation_table(list(report.correlations) + list(report.shadow_correlations)))

    lines.append("")
    lines.append(
        f"Self consistency, measured on {len(result.consistency_case_ids)} cases x 2 "
        f"versions x {result.plan.repeats} repeats"
    )
    for name in sorted(result.consistency.per_judge_flip_rate):
        row(f"flip_rate[{name}]", f"{result.consistency.per_judge_flip_rate[name]:.3f}")
    for name in sorted(result.consistency.shadow_flip_rate):
        row(f"flip_rate[{name}]", f"{result.consistency.shadow_flip_rate[name]:.3f}")
    row("panel_flip_rate", f"{result.consistency.panel_flip_rate:.3f}")
    row("REAL NOISE FLOOR", f"{result.consistency.noise_floor:.3f}")
    lines.append(
        "  temperature, top_p and top_k are removed on claude-opus-5 and "
        "claude-sonnet-5 (HTTP 400), so this is measured rather than configured away. "
        "It is NOT compared against any gate threshold: this pass does not gate."
    )

    lines.append("")
    lines.append(format_cost(result.voting_cost, "MEASURED cost, voting panel"))
    if result.shadow_cost.per_judge:
        lines.append("")
        lines.append(
            format_cost(result.shadow_cost, "MEASURED cost, shadow bench (NON VOTING)")
        )
    lines.append("")
    lines.append(
        f"  voting ${result.voting_cost.total_usd:.4f}  +  shadow "
        f"${result.shadow_cost.total_usd:.4f}  =  combined ${result.measured_usd:.4f}"
    )

    lines.append("")
    lines.append("Measured against the offline characters/4 estimate")
    lines.append(
        f"  {'slot':<24} {'est in':>9} {'real in':>9} {'ratio':>7} "
        f"{'est $':>9} {'real $':>9}"
    )
    estimates = result.preflight.by_slot()
    for item in list(result.voting_cost.per_judge) + list(result.shadow_cost.per_judge):
        estimate = estimates.get(item.judge_name)
        if estimate is None:  # pragma: no cover - slot names come from one source
            continue
        ratio = item.input_tokens / estimate.input_tokens if estimate.input_tokens else 0.0
        estimated_usd = NO_PRICE_MARKER if estimate.usd is None else f"{estimate.usd:9.4f}"
        measured_usd = (
            NO_PRICE_MARKER if item.total_usd is None else f"{item.total_usd:9.4f}"
        )
        lines.append(
            f"  {item.judge_name:<24} {estimate.input_tokens:>9} "
            f"{item.input_tokens:>9} {ratio:>7.2f} {estimated_usd:>9} {measured_usd:>9}"
        )
    measured_input = result.voting_cost.input_tokens + result.shadow_cost.input_tokens
    estimated_input = result.preflight.input_tokens
    lines.append(
        f"  {'TOTAL':<24} {estimated_input:>9} {measured_input:>9} "
        f"{(measured_input / estimated_input if estimated_input else 0.0):>7.2f} "
        f"{result.preflight.total_usd:>9.4f} {result.measured_usd:>9.4f}"
    )
    lines.append(
        "  ratio above 1.00 means characters/4 UNDERCOUNTS the real prompt. That is the "
        "reason the offline number is labeled an approximation everywhere it appears "
        "and is never quoted as a token count."
    )

    lines.append("")
    lines.append(
        format_cost_accuracy(
            cost_accuracy_table(
                report,
                repeats=result.plan.repeats,
                sweep_usd_by_judge=result.sweep_usd_by_judge(),
            ),
            noise=result.consistency.noise_floor,
            measured=True,
        )
    )

    lines.append("")
    lines.append(f"Calls that produced no usable verdict: {len(result.incidents)}")
    if not result.incidents:
        lines.append(
            "  none. Every judge returned a readable verdict on every call, which is a "
            "good run and is not the claim: the abstention path is exercised offline by "
            "tests/test_real_pass.py precisely because no live run can be relied on to "
            "exercise it."
        )
    else:
        for incident in result.incidents:
            billed = "" if incident.billed else "  (raised; no usage returned, tokens not counted)"
            lines.append(
                f"  {incident.slot:<24} {incident.case_id} {incident.sut_version} "
                f"r{incident.repeat}  {incident.kind}: {incident.detail}{billed}"
            )
        lines.append(
            "  each of these was recorded as an ABSTENTION and the pass carried on. An "
            "abstention does not vote and is counted apart from agreement, so a judge "
            "that is quietly broken shows up as a rising abstention rate rather than as "
            "a plausible looking accuracy number."
        )

    lines.append("")
    lines.append(f"  {NO_GATE_LINE}")
    return "\n".join(lines)
