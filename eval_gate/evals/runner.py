"""Run the panel over the golden set, K times, and hand the result to calibration.

Two decisions worth stating, because both could reasonably have gone the other
way:

REPEAT 1 IS THE REPORTED RUN. The headline metrics come from the first repeat
rather than from a mode or an average across repeats. That is what a single CI
run would actually see, and reporting a smoothed figure the pipeline never
observes would understate the risk. The other repeats exist to measure how much
that first run could have differed, which is the noise floor the gate then
compares its threshold against. Averaging would fold that measurement into the
number it is supposed to qualify.

SHADOW JUDGES TRAVEL SEPARATELY. Voting verdicts land on `judge_verdicts` and
`judge_series`; shadow verdicts land on `shadow_verdicts` and `shadow_series`, and
`build_report` puts them in a separate list on the report. The gate reads the
voting fields and the panel verdicts, never the shadow ones. Two lists rather than
one list with a flag is the point: it makes "a shadow judge cannot change a gate
outcome" a property of the data flow instead of a rule someone has to remember.

BOTH SUT VERSIONS RUN IN THE SAME PASS. Baseline and candidate are scored by the
same panel in the same run, so a drop between them cannot be an artifact of two
panels or two days. That is also why the unit of measurement throughout this
package is (sut_version, case_id) rather than case_id: the pair is the thing that
gets a verdict.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from eval_gate.audit import AuditLog
from eval_gate.baseline import BaselineRecord
from eval_gate.calibration import (
    CalibrationReport,
    calibrate_panel,
    calibrate_rater,
    measure_consistency,
    measure_correlation,
    measure_discrimination,
)
from eval_gate.config import Settings, get_settings
from eval_gate.evals.golden import GOLDEN_SET, GoldenCase
from eval_gate.judge import judge_case
from eval_gate.llm import (
    LLMProvider,
    describe_panel,
    get_panel,
    get_shadow_judges,
    panel_degraded,
    unique_judge_names,
)
from eval_gate.models import JudgeVerdict, PanelVerdict
from eval_gate.panel import aggregate
from eval_gate.prompts import PromptLibrary

Unit = tuple[str, str]  # (sut_version, case_id)


@dataclass
class RunResult:
    run_id: str
    panel_description: list[str]
    panel_size: int
    repeats: int
    degraded: bool
    panel_mode: str
    baseline_version: str
    candidate_version: str
    case_ids: tuple[str, ...]
    judge_names: tuple[str, ...]
    judge_models: dict[str, str]
    human_labels: dict[Unit, str] = field(default_factory=dict)
    #: judge name -> unit -> repeat index -> verdict
    judge_series: dict[str, dict[Unit, list[str]]] = field(default_factory=dict)
    panel_series: dict[Unit, list[str]] = field(default_factory=dict)
    judge_verdicts: list[JudgeVerdict] = field(default_factory=list)
    panel_verdicts: list[PanelVerdict] = field(default_factory=list)
    prompt_manifest_hash: str = ""
    #: Non voting judges. Separate fields, never merged into the voting ones.
    shadow_names: tuple[str, ...] = ()
    shadow_models: dict[str, str] = field(default_factory=dict)
    shadow_series: dict[str, dict[Unit, list[str]]] = field(default_factory=dict)
    shadow_verdicts: list[JudgeVerdict] = field(default_factory=list)

    def primary_panel(self) -> dict[Unit, PanelVerdict]:
        return {
            (item.sut_version, item.case_id): item
            for item in self.panel_verdicts
            if item.repeat == 1
        }

    def primary_judge(self, name: str) -> dict[Unit, str]:
        return {
            (item.sut_version, item.case_id): item.verdict
            for item in self.judge_verdicts
            if item.repeat == 1 and item.judge_name == name
        }

    def primary_shadow(self, name: str) -> dict[Unit, str]:
        return {
            (item.sut_version, item.case_id): item.verdict
            for item in self.shadow_verdicts
            if item.repeat == 1 and item.judge_name == name
        }


def run_id_for(panel: list[LLMProvider], repeats: int, manifest_hash: str) -> str:
    """A run id derived from what the run actually is, not from a clock.

    Two runs of the same panel over the same prompt set get the same id, which
    makes the audit trail comparable across runs. The timestamp inside each audit
    record still varies, and that is the documented boundary of the determinism
    claim.
    """
    seed = "|".join(
        [
            manifest_hash,
            str(repeats),
            *[
                f"{name}:{judge.model}"
                for name, judge in zip(unique_judge_names(panel), panel)
            ],
        ]
    )
    return "run-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def run_panel(
    settings: Settings | None = None,
    *,
    panel: list[LLMProvider] | None = None,
    shadow: list[LLMProvider] | None = None,
    cases: tuple[GoldenCase, ...] = GOLDEN_SET,
    library: PromptLibrary | None = None,
    audit: AuditLog | None = None,
    first_repeat: int = 1,
) -> RunResult:
    """Score every case, for both sut versions, `judge_repeats` times.

    `first_repeat` skips the leading repeats instead of re-scoring them. Offline it
    is never needed and never used, and it defaults to 1 so nothing the gate does
    changes. It exists because on a paid panel repeat 1 of a case is a real
    invoice line: the real model pass measures a wide calibration sweep at one
    repeat and then a narrow consistency sweep at three, and the consistency cases
    are a subset of the calibration cases, so repeat 1 of each of them has already
    been bought. Paying for it twice would be 80 calls at the default sizes, spent
    to learn something already on file. The prompt is identical either way, because
    ATTEMPT is rendered from the repeat index rather than from a counter, so a
    reused repeat 1 is the same measurement and not a near enough one.
    """
    settings = settings or get_settings()
    if first_repeat < 1:
        raise ValueError(f"first_repeat must be at least 1, got {first_repeat}")
    panel = panel if panel is not None else get_panel(settings)
    shadow = shadow if shadow is not None else get_shadow_judges(settings)
    library = library or PromptLibrary()
    repeats = max(1, settings.judge_repeats)
    degraded = panel_degraded(panel)
    manifest = library.manifest_hash()
    run_id = run_id_for(panel, repeats, manifest)
    versions = (settings.baseline_sut_version, settings.candidate_sut_version)
    names = unique_judge_names(panel)
    shadow_names = [f"shadow:{name}" for name in unique_judge_names(shadow)]

    result = RunResult(
        run_id=run_id,
        panel_description=describe_panel(panel),
        panel_size=len(panel),
        repeats=repeats,
        degraded=degraded,
        panel_mode=settings.judge_panel_mode,
        baseline_version=versions[0],
        candidate_version=versions[1],
        case_ids=tuple(case.case_id for case in cases),
        judge_names=tuple(names),
        judge_models={name: judge.model for name, judge in zip(names, panel)},
        prompt_manifest_hash=manifest,
        shadow_names=tuple(shadow_names),
        shadow_models={
            name: judge.model for name, judge in zip(shadow_names, shadow)
        },
    )
    for name in names:
        result.judge_series[name] = {}
    for name in shadow_names:
        result.shadow_series[name] = {}

    if audit:
        audit.append(
            "run_started",
            run_id,
            {
                "panel": result.panel_description,
                "panel_size": len(panel),
                "degraded": degraded,
                "repeats": repeats,
                "cases": len(cases),
                "prompt_manifest_hash": manifest,
                "shadow_judges": shadow_names,
                "sut_versions": list(versions),
            },
        )

    for case in cases:
        for version in versions:
            unit: Unit = (version, case.case_id)
            result.human_labels[unit] = case.label(version)
            result.panel_series[unit] = []
            for name in names:
                result.judge_series[name][unit] = []
            for name in shadow_names:
                result.shadow_series[name][unit] = []
            for repeat in range(first_repeat, repeats + 1):
                verdicts = [
                    judge_case(
                        judge,
                        case,
                        version,
                        repeat=repeat,
                        library=library,
                        judge_name=name,
                    )
                    for name, judge in zip(names, panel)
                ]
                for verdict in verdicts:
                    result.judge_verdicts.append(verdict)
                    result.judge_series[verdict.judge_name][unit].append(verdict.verdict)
                # Shadow judges are scored in their own loop, stamped shadow=True,
                # and never handed to aggregate(). aggregate() also refuses a
                # shadow verdict, so the separation holds even if this loop is
                # later rewritten by someone who has not read panel.py.
                for name, judge in zip(shadow_names, shadow):
                    shadow_verdict = judge_case(
                        judge,
                        case,
                        version,
                        repeat=repeat,
                        library=library,
                        judge_name=name,
                        shadow=True,
                    )
                    result.shadow_verdicts.append(shadow_verdict)
                    result.shadow_series[name][unit].append(shadow_verdict.verdict)
                panel_verdict = aggregate(verdicts, degraded=degraded)
                result.panel_verdicts.append(panel_verdict)
                result.panel_series[unit].append(panel_verdict.verdict)
                if audit and repeat == 1:
                    audit.append(
                        "case_scored",
                        run_id,
                        {
                            "case_id": case.case_id,
                            "sut_version": version,
                            "human_label": case.label(version),
                            "panel_verdict": panel_verdict.verdict,
                            "votes": list(panel_verdict.votes),
                            "unanimous": panel_verdict.unanimous,
                            "split": panel_verdict.split,
                            "abstentions": panel_verdict.abstentions,
                            "escalated": panel_verdict.escalated,
                            "degraded": panel_verdict.degraded,
                            "tags": list(case.tags),
                        },
                    )

    return result


def build_report(result: RunResult) -> CalibrationReport:
    """Turn a run into the measurement that decides whether it can gate."""
    versions = (result.baseline_version, result.candidate_version)

    judges = [
        calibrate_rater(
            name,
            result.judge_models[name],
            result.primary_judge(name),
            result.human_labels,
            versions,
        )
        for name in sorted(result.judge_names)
    ]

    primary = result.primary_panel()
    panel_verdicts = {unit: primary[unit].verdict for unit in sorted(primary)}
    panel = calibrate_panel(
        panel_verdicts,
        result.human_labels,
        versions,
        unanimous={unit: primary[unit].unanimous for unit in sorted(primary)},
        split={unit: primary[unit].split for unit in sorted(primary)},
        escalated={unit: primary[unit].escalated for unit in sorted(primary)},
        judges=judges,
    )

    shadow_judges = [
        calibrate_rater(
            name,
            result.shadow_models[name],
            result.primary_shadow(name),
            result.human_labels,
            versions,
        )
        for name in sorted(result.shadow_names)
    ]

    consistency = measure_consistency(
        {name: result.judge_series[name] for name in sorted(result.judge_series)},
        result.panel_series,
        result.repeats,
    )
    if result.shadow_series:
        shadow_consistency = measure_consistency(
            {name: result.shadow_series[name] for name in sorted(result.shadow_series)},
            result.panel_series,
            result.repeats,
        )
        consistency.shadow_flip_rate = shadow_consistency.per_judge_flip_rate

    voting_verdicts = {
        name: result.primary_judge(name) for name in sorted(result.judge_names)
    }
    correlations = measure_correlation(voting_verdicts, result.human_labels)
    everyone = dict(voting_verdicts)
    everyone.update(
        {name: result.primary_shadow(name) for name in sorted(result.shadow_names)}
    )
    shadow_correlations = measure_correlation(
        everyone,
        result.human_labels,
        only_pairs_touching=set(result.shadow_names),
    )
    discrimination = measure_discrimination(
        result.panel_series,
        result.case_ids,
        versions,
        abstention_rate=panel.abstention_rate,
        unanimity_rate=panel.unanimity_rate,
        panel_kappa=panel.kappa,
        panel_false_pass_rate=panel.false_pass_rate,
    )

    return CalibrationReport(
        run_id=result.run_id,
        panel_description=result.panel_description,
        panel_size=result.panel_size,
        repeats=result.repeats,
        degraded=result.degraded,
        judges=judges,
        panel=panel,
        consistency=consistency,
        correlations=correlations,
        discrimination=discrimination,
        baseline_version=versions[0],
        candidate_version=versions[1],
        panel_mode=result.panel_mode,
        prompt_manifest_hash=result.prompt_manifest_hash,
        shadow_judges=shadow_judges,
        shadow_correlations=shadow_correlations,
    )


# --------------------------------------------------------------------------- #
# Gate evaluation. Kept here rather than in gate.py so the CLI and the demo can
# report the same failures the gate would without shelling out to it.
# --------------------------------------------------------------------------- #


#: What `deployment_decision` reads when the gate refuses to produce one. It is
#: deliberately not "ALLOW" and not "BLOCK": a refusal is not a quiet pass, and it
#: is not a merge block either. Something an operator cannot mistake for a verdict
#: is the whole point of the string.
NO_DECISION = "NO DECISION (refused)"


@dataclass
class GateDecision:
    """What the gate concluded, and which condition drove the exit code.

    `deployment_decision` is the merge blocking answer: would this candidate be
    allowed through, measured against the COMMITTED baseline record. The panel
    health checks are the second question: is this panel fit to be making that
    call at all.

    The exit code is non-zero when ANY of four conditions holds, and
    `exit_driver` names which one. Returning exit 0 while printing
    `deployment_decision BLOCK` would make this project's central claim false:
    the CI step goes green while the harness says block.

    THERE ARE TWO WAYS TO PRODUCE NO DECISION, and both are refusals rather than
    verdicts. A threshold inside the measured noise floor means the comparison
    cannot distinguish a regression from a rerun. A baseline that is not
    comparable to this run means the two pass rates are not measurements of the
    same thing. In both cases `deployment_decision` reads NO_DECISION, because
    printing BASELINE NOT COMPARABLE and then computing ALLOW from that very
    comparison is the same defect the noise floor refusal exists to prevent: a
    number the harness has already called meaningless, dressed up as a
    verdict.
    """

    run_id: str
    candidate_version: str
    threshold: float
    noise_floor: float
    baseline_pass_rate: float
    candidate_pass_rate: float
    drop_vs_baseline: float
    regression_detected: bool
    deployment_decision: str  # "BLOCK" | "ALLOW" | NO_DECISION
    refused: bool
    #: Calibration failures: the panel is not fit to gate.
    panel_failures: list[str] = field(default_factory=list)
    #: Reasons the committed baseline is not comparable to this run, in prose.
    baseline_warnings: list[str] = field(default_factory=list)
    #: The same reasons as bare field names, for the exit_driver line.
    baseline_differing_fields: list[str] = field(default_factory=list)
    #: Set when there is no committed baseline at all, which is fatal: there is
    #: nothing to compare against, and inventing a comparison would be worse.
    baseline_missing: bool = False

    @property
    def panel_healthy(self) -> bool:
        return not self.panel_failures

    @property
    def baseline_incomparable(self) -> bool:
        """Whether the committed baseline measures the same thing this run did.

        Derived from `baseline_warnings` rather than stored, so there is no way to
        print a NOT COMPARABLE warning and still hold a comparable flag, which is
        the inconsistency that let the old gate decide anyway.
        """
        return bool(self.baseline_warnings)

    @property
    def failures(self) -> list[str]:
        """Everything to print as a bullet, in exit precedence order."""
        rows: list[str] = []
        if self.refused:
            rows.append(
                f"REFUSING TO RUN: regression threshold {self.threshold:.3f} is inside "
                f"the measured noise floor {self.noise_floor:.3f} (panel flip rate). A "
                f"gate tighter than its own judges' variance is not a gate. Raise the "
                f"threshold above {self.noise_floor:.3f} or make the judges more "
                f"consistent."
            )
            return rows
        if self.baseline_missing:
            rows.append(
                "no committed baseline record to compare against; record one with "
                "--record-baseline before wiring this gate into CI"
            )
        if self.baseline_incomparable:
            rows.append(
                f"REFUSING TO DECIDE: the committed baseline does not measure the same "
                f"thing this run did ({', '.join(self.baseline_differing_fields)} "
                f"differ{'s' if len(self.baseline_differing_fields) == 1 else ''}), so "
                f"no deployment decision was computed. Comparing these two pass rates "
                f"would produce a number the harness has already called meaningless. "
                f"Re-record the baseline for this configuration, or run the "
                f"configuration the baseline was recorded under."
            )
        if self.regression_detected:
            rows.append(
                f"REGRESSION: {self.candidate_version} pass rate "
                f"{self.candidate_pass_rate:.3f} is {self.drop_vs_baseline:.3f} below "
                f"the committed baseline {self.baseline_pass_rate:.3f}, more than the "
                f"{self.threshold:.3f} threshold allows"
            )
        rows.extend(self.panel_failures)
        return rows

    @property
    def exit_drivers(self) -> list[str]:
        """Which conditions force a non-zero exit, in precedence order."""
        drivers: list[str] = []
        if self.refused:
            drivers.append("refused: threshold sits inside the measured noise floor")
        if self.baseline_missing:
            drivers.append("no committed baseline record to compare against")
        if self.baseline_incomparable:
            drivers.append(
                f"refused: committed baseline is not comparable to this run "
                f"({', '.join(self.baseline_differing_fields)} "
                f"differ{'s' if len(self.baseline_differing_fields) == 1 else ''})"
            )
        if self.regression_detected:
            drivers.append("regression detected against the committed baseline")
        if self.panel_failures:
            drivers.append(
                f"panel calibration failed ({len(self.panel_failures)} check"
                f"{'' if len(self.panel_failures) == 1 else 's'})"
            )
        return drivers

    @property
    def exit_driver(self) -> str:
        """One line naming what drove the exit code."""
        drivers = self.exit_drivers
        if not drivers:
            return (
                "none: panel healthy and no regression against the committed baseline"
            )
        head, rest = drivers[0], drivers[1:]
        return head + (f" (also: {'; '.join(rest)})" if rest else "")

    @property
    def passed(self) -> bool:
        """Exit 0 only when the panel is healthy AND nothing regressed."""
        return not self.exit_drivers


def evaluate_gate(
    report: CalibrationReport,
    settings: Settings | None = None,
    *,
    baseline: BaselineRecord | None = None,
    candidate_version: str | None = None,
) -> GateDecision:
    """Apply every threshold. `decision.passed` is the exit 0 condition.

    This function reads `report.judges`, `report.panel`, `report.consistency`
    (panel flip rate only) and `report.discrimination`. It does NOT read
    `report.shadow_judges`, `report.shadow_correlations`, or
    `consistency.shadow_flip_rate`, and that omission is the invariant rather
    than an oversight: a shadow judge that could change a gate outcome would be a
    voting judge with extra steps. Turning shadow judges off must leave every
    number below untouched.

    The regression comparison is against the COMMITTED baseline record, not
    against the other version measured in this run. The within run delta is still
    reported, because it is informative, but it is not what gates: it would report
    a regression on every default invocation, since the golden set carries a
    planted one.
    """
    settings = settings or get_settings()
    threshold = settings.gate_max_pass_rate_drop
    noise_floor = report.consistency.noise_floor
    candidate = candidate_version or report.candidate_version
    candidate_pass_rate = report.panel.pass_rate.get(candidate, 0.0)
    baseline_rate = baseline.measured_pass_rate if baseline else 0.0
    drop = baseline_rate - candidate_pass_rate if baseline else 0.0
    regression_detected = bool(baseline) and drop > threshold

    decision = GateDecision(
        run_id=report.run_id,
        candidate_version=candidate,
        threshold=threshold,
        noise_floor=noise_floor,
        baseline_pass_rate=baseline_rate,
        candidate_pass_rate=candidate_pass_rate,
        drop_vs_baseline=drop,
        regression_detected=regression_detected,
        deployment_decision="BLOCK" if regression_detected else "ALLOW",
        refused=False,
        baseline_missing=baseline is None,
    )
    if baseline:
        comparison = dict(
            prompt_version_hash=report.prompt_manifest_hash,
            panel_mode=report.panel_mode,
            repeats=report.repeats,
        )
        decision.baseline_warnings = baseline.comparable_with(**comparison)
        decision.baseline_differing_fields = baseline.differing_fields(**comparison)

    # THE COMPARABILITY REFUSAL. A baseline recorded under a different panel, a
    # different repeat count or a different prompt set is not a measurement of what
    # this run measured, and a decision derived from it is manufactured. So the
    # deployment decision is withdrawn rather than computed, on the same grounds as
    # the noise floor refusal below: the gate does not produce numbers it has
    # already declared meaningless. `regression_detected` is cleared too, because a
    # regression finding IS that comparison.
    #
    # This does NOT return early. The calibration checks still run, because whether
    # this panel is fit to gate is a separate question that an invalid baseline does
    # not answer, and the report is more useful with both halves in it.
    if decision.baseline_incomparable:
        decision.regression_detected = False
        decision.deployment_decision = NO_DECISION

    # THE SIGNATURE CHECK, and it comes first because nothing after it means
    # anything if it fails. A threshold inside the panel's own variance cannot
    # distinguish a regression from a rerun, so the gate produces no verdict at
    # all rather than a manufactured one.
    if threshold < noise_floor:
        decision.refused = True
        # No verdict, so no regression finding and no deployment decision either.
        # `failures` already short circuits to the refusal alone; these two lines
        # stop the same refused run from claiming a regression in `exit_driver`.
        decision.regression_detected = False
        decision.deployment_decision = NO_DECISION
        return decision

    for judge in report.judges:
        if judge.kappa < settings.gate_min_judge_kappa:
            decision.panel_failures.append(
                f"judge {judge.name} kappa {judge.kappa:.3f} < "
                f"{settings.gate_min_judge_kappa:.3f}"
            )

    if report.panel.false_pass_rate > settings.gate_max_panel_false_pass_rate:
        decision.panel_failures.append(
            f"panel false_pass_rate {report.panel.false_pass_rate:.3f} > "
            f"{settings.gate_max_panel_false_pass_rate:.3f} (a false pass ships a regression)"
        )
    if report.panel.false_fail_rate > settings.gate_max_panel_false_fail_rate:
        decision.panel_failures.append(
            f"panel false_fail_rate {report.panel.false_fail_rate:.3f} > "
            f"{settings.gate_max_panel_false_fail_rate:.3f} (a false fail blocks a good change)"
        )
    if report.panel.abstention_rate > settings.gate_max_panel_abstention_rate:
        decision.panel_failures.append(
            f"panel abstention_rate {report.panel.abstention_rate:.3f} > "
            f"{settings.gate_max_panel_abstention_rate:.3f}"
        )

    discrimination = report.discrimination
    if discrimination.cases < settings.gate_min_cases:
        decision.panel_failures.append(
            f"golden set has {discrimination.cases} cases, fewer than the "
            f"{settings.gate_min_cases} the gate requires"
        )
    if discrimination.never_discriminate_rate > settings.gate_max_never_discriminate_rate:
        decision.panel_failures.append(
            f"cases_that_never_discriminate {discrimination.cases_that_never_discriminate}"
            f"/{discrimination.cases} = {discrimination.never_discriminate_rate:.3f} > "
            f"{settings.gate_max_never_discriminate_rate:.3f}; {discrimination.suspicion()}"
        )
    if discrimination.unanimity_rate > settings.gate_max_unanimity_rate:
        decision.panel_failures.append(
            f"unanimity_rate {discrimination.unanimity_rate:.3f} > "
            f"{settings.gate_max_unanimity_rate:.3f}; {discrimination.suspicion()}"
        )

    return decision


def format_metrics(
    report: CalibrationReport,
    decision: GateDecision,
    *,
    baseline: BaselineRecord | None = None,
) -> str:
    """The aligned metrics block. Same text in the gate, the CLI, and the demo."""
    width = 34
    lines: list[str] = []

    def row(label: str, value: str) -> None:
        lines.append(f"  {label:<{width}} {value}")

    lines.append("Run")
    row("run_id", report.run_id)
    row("panel", ", ".join(report.panel_description))
    row("panel_size", str(report.panel_size))
    row("repeats", str(report.repeats))
    row("panel_mode", report.panel_mode)
    row("panel_degraded", "YES" if report.degraded else "no")
    row("prompt_manifest_hash", report.prompt_manifest_hash[:16])

    lines.append("")
    lines.append("Per judge, against human labels")
    header = (
        f"  {'judge':<20} {'agree':>6} {'kappa':>7} {'falsePass':>10} "
        f"{'falseFail':>10} {'abstain':>8}"
    )
    lines.append(header)
    for judge in report.judges:
        lines.append(
            f"  {judge.name:<20} {judge.raw_agreement:>6.3f} {judge.kappa:>7.3f} "
            f"{judge.false_pass_rate:>10.3f} {judge.false_fail_rate:>10.3f} "
            f"{judge.abstention_rate:>8.3f}"
        )
    panel = report.panel
    lines.append(
        f"  {'PANEL':<20} {panel.raw_agreement:>6.3f} {panel.kappa:>7.3f} "
        f"{panel.false_pass_rate:>10.3f} {panel.false_fail_rate:>10.3f} "
        f"{panel.abstention_rate:>8.3f}"
    )
    lines.append(
        "  raw agreement is misleading on this skewed label set; kappa is the number to read"
    )

    if report.shadow_judges:
        lines.append("")
        lines.append("Shadow judges (measured, NON VOTING, never gate)")
        lines.append(header)
        for judge in report.shadow_judges:
            lines.append(
                f"  {judge.name:<20} {judge.raw_agreement:>6.3f} {judge.kappa:>7.3f} "
                f"{judge.false_pass_rate:>10.3f} {judge.false_fail_rate:>10.3f} "
                f"{judge.abstention_rate:>8.3f}"
            )
        lines.append(
            "  excluded from the vote, from unanimity, from split detection, and from "
            "every gate threshold"
        )

    lines.append("")
    lines.append("Panel")
    row("unanimity_rate", f"{panel.unanimity_rate:.3f}")
    row("split_rate", f"{panel.split_rate:.3f}")
    row("escalation_rate", f"{panel.escalation_rate:.3f}")
    row("best_single_judge", f"{panel.best_single_judge} (kappa {panel.best_single_judge_kappa:.3f})")
    row("panel_kappa_vs_best_single_judge", f"{panel.kappa_vs_best_single_judge:+.3f}")
    if not panel.panel_earned_its_cost:
        lines.append(
            "  THE PANEL DID NOT BEAT ITS BEST MEMBER: three judges cost 3x and bought "
            "nothing measurable here"
        )
    for name in (report.baseline_version, report.candidate_version):
        row(f"pass_rate[{name}]", f"{panel.pass_rate.get(name, 0.0):.3f}")
    row(
        "within_run_delta",
        f"{report.pass_rate_drop:+.3f} (informative only; the gate compares the "
        f"committed baseline)",
    )

    lines.append("")
    lines.append("Self consistency across repeats")
    for name in sorted(report.consistency.per_judge_flip_rate):
        row(f"flip_rate[{name}]", f"{report.consistency.per_judge_flip_rate[name]:.3f}")
    row("panel_flip_rate", f"{report.consistency.panel_flip_rate:.3f}")
    row("NOISE_FLOOR", f"{report.consistency.noise_floor:.3f}")
    row("gate threshold", f"{decision.threshold:.3f}")
    lines.append(
        "  temperature is not available on claude-opus-5 or claude-sonnet-5 (HTTP 400), "
        "so this variance is measured rather than configured away"
    )

    lines.append("")
    lines.append("Pairwise error correlation")
    lines.append(
        f"  {'pair':<40} {'joint':>7} {'indep':>7} {'ratio':>7}  interpretation"
    )
    for pair in report.correlations:
        label = f"{pair.judge_a} + {pair.judge_b}"
        lines.append(
            f"  {label:<40} {pair.joint_error_rate:>7.3f} "
            f"{pair.expected_if_independent:>7.3f} {pair.ratio:>7.2f}  {pair.interpretation}"
        )

    if report.shadow_correlations:
        lines.append("")
        lines.append("Pairwise error correlation, pairs touching a shadow judge")
        for pair in report.shadow_correlations:
            label = f"{pair.judge_a} + {pair.judge_b}"
            lines.append(
                f"  {label:<40} {pair.joint_error_rate:>7.3f} "
                f"{pair.expected_if_independent:>7.3f} {pair.ratio:>7.2f}  "
                f"{pair.interpretation}"
            )

    lines.append("")
    lines.append("Vacuous gate metrics")
    discrimination = report.discrimination
    row(
        "cases_that_never_discriminate",
        f"{discrimination.cases_that_never_discriminate}/{discrimination.cases} "
        f"({discrimination.never_discriminate_rate:.3f})",
    )
    row("panel_abstention_rate", f"{discrimination.panel_abstention_rate:.3f}")
    row("unanimity_rate", f"{discrimination.unanimity_rate:.3f}")
    row("suspicion", discrimination.suspicion())

    lines.append("")
    lines.append("Regression, against the committed baseline record")
    from eval_gate.baseline import describe as describe_baseline

    row("baseline record", describe_baseline(baseline))
    row("candidate", decision.candidate_version)
    row("baseline_pass_rate", f"{decision.baseline_pass_rate:.3f}")
    row("candidate_pass_rate", f"{decision.candidate_pass_rate:.3f}")
    row("drop_vs_baseline", f"{decision.drop_vs_baseline:+.3f}")
    row("threshold", f"{decision.threshold:.3f}")
    for warning in decision.baseline_warnings:
        lines.append(f"  BASELINE NOT COMPARABLE: {warning}")
    if decision.baseline_incomparable:
        lines.append(
            "  the drop above is REPORTED AND NOT DECIDED ON: no deployment decision is "
            "computed from a comparison the harness has called meaningless"
        )

    lines.append("")
    lines.append("Decision")
    row("regression_detected", "YES" if decision.regression_detected else "NO")
    row("deployment_decision", decision.deployment_decision)
    row("panel_healthy", "yes" if decision.panel_healthy else "NO")
    row("exit_code", "0" if decision.passed else "1")
    row("exit_driver", decision.exit_driver)
    return "\n".join(lines)
