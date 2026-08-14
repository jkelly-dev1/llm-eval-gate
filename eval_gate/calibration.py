"""The measurement layer. This is the reason the project exists.

A gate you have not calibrated is a coin flip wearing a lab coat. Everything
here answers a question the gate cannot answer for itself.

Why raw agreement is not enough

On this golden set the baseline is 24 pass / 6 fail. A judge that returns "pass"
unconditionally therefore agrees with the humans on 80% of cases and looks
respectable in a status report, while being incapable of blocking anything.
Cohen's kappa corrects for the agreement you would get by chance given each
rater's own marginal rates, so the same all-pass judge scores 0.00 rather than
0.80. The skew in the label distribution is deliberate for exactly this reason:
a 50/50 set would make raw agreement look almost as good as kappa and hide the
problem the harness is built to surface.

Why false pass and false fail are reported separately

They are not interchangeable and averaging them destroys the only information a
release manager wants. A false pass ships a regression to production. A false
fail blocks a good change and costs a developer an afternoon. Their denominators
differ too: false_pass_rate is measured against the cases the humans failed
(what fraction of genuinely bad answers did this judge wave through), and
false_fail_rate against the cases the humans passed (what fraction of good
answers did it block).

Why self consistency is measured rather than configured away

Claude Opus 5 and Claude Sonnet 5 do not accept a temperature parameter. Sending
one returns HTTP 400. There is no temperature=0 knob to pin a judge's output
with, so a panel's run to run variance is a property of the system rather than a
setting, and the only honest response is to measure it. The panel flip rate that
falls out of that measurement is the NOISE_FLOOR, expressed in the same units as
the gate's regression threshold (an absolute fraction of cases), so the two are
directly comparable. The gate refuses to run when the threshold is below it.

Why error correlation is reported at all

Majority voting only buys accuracy when the errors being voted on are
independent. Two judges from the same vendor, on the same family of models, are
expected to be wrong on the same cases. If they are, three judges cost three
times as much and behave like slightly more than one, which is a finding rather
than a footnote. The pairwise numbers here compare the observed rate of shared
errors against what independence would predict.

No external stats dependency. The arithmetic is short and writing it out means
the definitions are visible instead of buried in a library's conventions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CLASSES = ("pass", "fail")


# --------------------------------------------------------------------------- #
# Arithmetic
# --------------------------------------------------------------------------- #


def raw_agreement(pairs: list[tuple[str, str]]) -> float:
    """Fraction of scored units where the judge and the human agree."""
    if not pairs:
        return 0.0
    return sum(1 for judged, human in pairs if judged == human) / len(pairs)


def cohen_kappa(pairs: list[tuple[str, str]]) -> float:
    """Chance corrected agreement between one judge and the human labels.

    kappa = (po - pe) / (1 - pe), where po is observed agreement and pe is the
    agreement expected from the two raters' marginal rates alone. Abstentions
    are excluded from the pairs before they get here and counted separately: a
    judge that declines to score is not agreeing or disagreeing, and folding
    abstentions into either bucket would let a broken judge look calibrated.
    """
    if not pairs:
        return 0.0
    total = len(pairs)
    observed = raw_agreement(pairs)
    expected = 0.0
    for label in CLASSES:
        judge_rate = sum(1 for judged, _ in pairs if judged == label) / total
        human_rate = sum(1 for _, human in pairs if human == label) / total
        expected += judge_rate * human_rate
    if 1.0 - expected <= 1e-12:
        # Both raters put everything in one class. Agreement is total but
        # entirely explained by chance, so kappa is undefined; 1.0 when they
        # actually agreed on every unit, 0.0 otherwise.
        return 1.0 if observed >= 1.0 - 1e-12 else 0.0
    return (observed - expected) / (1.0 - expected)


def confusion(pairs: list[tuple[str, str]]) -> dict[str, int]:
    """Counts with "pass" as the positive class, in sorted key order."""
    matrix = {"false_fail": 0, "false_pass": 0, "true_fail": 0, "true_pass": 0}
    for judged, human in pairs:
        if judged == "pass" and human == "pass":
            matrix["true_pass"] += 1
        elif judged == "pass" and human == "fail":
            matrix["false_pass"] += 1
        elif judged == "fail" and human == "fail":
            matrix["true_fail"] += 1
        else:
            matrix["false_fail"] += 1
    return matrix


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


# --------------------------------------------------------------------------- #
# Result records
# --------------------------------------------------------------------------- #


@dataclass
class RaterCalibration:
    """One rater (a judge, or the panel) measured against the human labels."""

    name: str
    model: str
    units: int
    scored: int
    abstained: int
    raw_agreement: float
    kappa: float
    confusion: dict[str, int]
    false_pass_rate: float
    false_fail_rate: float
    abstention_rate: float
    #: Panel pass rate per sut version, so the regression is readable per rater.
    pass_rate: dict[str, float] = field(default_factory=dict)


@dataclass
class PanelCalibration(RaterCalibration):
    unanimity_rate: float = 0.0
    split_rate: float = 0.0
    escalation_rate: float = 0.0
    best_single_judge: str = ""
    best_single_judge_kappa: float = 0.0
    #: The honest test of whether three judges earned their 3x cost. Negative or
    #: zero means they did not, and the report says so in words.
    kappa_vs_best_single_judge: float = 0.0

    @property
    def panel_earned_its_cost(self) -> bool:
        return self.kappa_vs_best_single_judge > 0.0


@dataclass
class ConsistencyReport:
    units: int
    repeats: int
    per_judge_flip_rate: dict[str, float]
    panel_flip_rate: float
    #: Shadow judge flip rates live in their own dict. They are measured for the
    #: same reason the voting ones are, and they are kept apart so nothing that
    #: derives the noise floor can accidentally read one.
    shadow_flip_rate: dict[str, float] = field(default_factory=dict)

    @property
    def noise_floor(self) -> float:
        """The panel flip rate, in the same units as the gate threshold.

        Both are an absolute fraction of the (case, sut version) units in the
        golden set, so a 0.05 threshold and a 0.08 noise floor are directly
        comparable and the comparison means what it looks like it means.
        """
        return self.panel_flip_rate


@dataclass
class PairCorrelation:
    judge_a: str
    judge_b: str
    common_units: int
    error_rate_a: float
    error_rate_b: float
    joint_error_rate: float
    expected_if_independent: float
    ratio: float

    @property
    def interpretation(self) -> str:
        if self.expected_if_independent <= 0.0:
            return "no shared errors to compare"
        if self.ratio >= 1.30:
            return "errors correlate; majority voting buys less than it appears to"
        if self.ratio <= 0.70:
            return "errors are anti correlated; the pair covers for each other"
        return "errors look roughly independent"


@dataclass
class DiscriminationReport:
    """Vacuous gate metrics. A gate that cannot fail is untested, not safe."""

    cases: int
    cases_that_never_discriminate: int
    panel_abstention_rate: float
    unanimity_rate: float
    #: Carried here so `suspicion` can read the panel's accuracy alongside the
    #: discrimination counts. Reading unanimity alone stays quiet in the one
    #: configuration where the panel has been captured, which is the only
    #: configuration where the line needs to say something.
    panel_kappa: float = 0.0
    panel_false_pass_rate: float = 0.0

    @property
    def never_discriminate_rate(self) -> float:
        return _rate(self.cases_that_never_discriminate, self.cases)

    def signals(self) -> list[str]:
        """Every vacuous gate signal that fired, named, in a fixed order.

        Four independent tells, because they do not co-occur. A captured panel
        does NOT necessarily look unanimous: measured on this golden set, the
        panel with two miscalibrated judges scores 0.733 unanimity against the
        honest panel's 0.867, so a heuristic keyed on unanimity is silent exactly
        when it matters. The tells that do fire under capture are the
        discrimination count saturating and the panel's kappa collapsing, which is
        the stronger finding: it is what validates the vacuous gate metric as the
        thing that catches a captured panel.
        """
        fired: list[str] = []
        if self.never_discriminate_rate >= 0.60:
            fired.append(
                f"cases_that_never_discriminate {self.cases_that_never_discriminate}"
                f"/{self.cases} ({self.never_discriminate_rate:.3f}): most cases give "
                f"the two versions the same verdict, so they carry no signal"
            )
        if self.panel_kappa <= 0.05:
            fired.append(
                f"panel kappa {self.panel_kappa:.3f} at or near zero: the panel's "
                f"verdicts carry no information about the human labels"
            )
        if self.panel_false_pass_rate >= 0.90:
            fired.append(
                f"panel false_pass_rate {self.panel_false_pass_rate:.3f} near one: the "
                f"panel passes almost everything the humans failed"
            )
        if self.unanimity_rate >= 0.90:
            fired.append(
                f"unanimity_rate {self.unanimity_rate:.3f} near total: the judges "
                f"almost never disagree"
            )
        return fired

    def suspicion(self) -> str:
        """What the harness suspects, naming the signal that raised it.

        The three diagnoses call for different fixes, so the line says which one
        it means. A captured panel needs different judges. An easy golden set needs
        harder cases. Non independent judges need a different vendor, not a fourth
        judge from the same one.
        """
        fired = self.signals()
        if not fired:
            return "no vacuous gate signal fired"
        if self.panel_kappa <= 0.05 or self.panel_false_pass_rate >= 0.90:
            diagnosis = "suspect a CAPTURED PANEL: these verdicts cannot gate anything"
        elif self.never_discriminate_rate >= 0.60:
            diagnosis = "suspect an easy golden set: add cases that separate the versions"
        else:
            diagnosis = "suspect non independent judges: another judge will not help"
        return "SUSPICIOUS: " + "; ".join(fired) + f". {diagnosis}"


@dataclass
class CalibrationReport:
    run_id: str
    panel_description: list[str]
    panel_size: int
    repeats: int
    degraded: bool
    judges: list[RaterCalibration]
    panel: PanelCalibration
    consistency: ConsistencyReport
    correlations: list[PairCorrelation]
    discrimination: DiscriminationReport
    baseline_version: str
    candidate_version: str
    #: Which panel measured this run, and over which prompt set. Both are needed
    #: to decide whether a committed baseline pass rate is comparable to it.
    panel_mode: str = "honest"
    prompt_manifest_hash: str = ""
    #: Non voting judges, measured identically and kept in a SEPARATE list. The
    #: gate never reads this field, and that is the invariant: a shadow judge
    #: cannot change a gate outcome, or it would be a voting judge with extra
    #: steps. Keeping it off `judges` is what makes the property structural.
    shadow_judges: list[RaterCalibration] = field(default_factory=list)
    #: Pairs involving at least one shadow judge.
    shadow_correlations: list[PairCorrelation] = field(default_factory=list)

    @property
    def pass_rate_drop(self) -> float:
        """Within run delta: first measured version minus second.

        Informative, and NOT what the gate compares. The golden set carries a
        planted regression, so this number is positive on every honest run and a
        gate wired to it would be permanently red. The gate compares the
        candidate's pass rate against the committed baseline record instead.
        """
        return self.panel.pass_rate.get(self.baseline_version, 0.0) - self.panel.pass_rate.get(
            self.candidate_version, 0.0
        )

    def best_judge_kappa(self) -> float:
        return max((judge.kappa for judge in self.judges), default=0.0)

    def all_raters(self) -> list[RaterCalibration]:
        """Voting and shadow judges together, for reporting only.

        Deliberately a method rather than a field, so a caller that wants both
        has to ask for both. The gate asks for `judges`.
        """
        return list(self.judges) + list(self.shadow_judges)


# --------------------------------------------------------------------------- #
# Builders. Input shapes are kept dumb on purpose: dicts keyed by tuples, so
# the runner does not have to know what the calibration layer wants next.
# --------------------------------------------------------------------------- #


def _pairs(
    verdicts: dict[tuple[str, str], str],
    labels: dict[tuple[str, str], str],
) -> list[tuple[str, str]]:
    """(judged, human) for every unit the rater actually voted on, sorted."""
    return [
        (verdicts[unit], labels[unit])
        for unit in sorted(verdicts)
        if verdicts[unit] in CLASSES
    ]


def calibrate_rater(
    name: str,
    model: str,
    verdicts: dict[tuple[str, str], str],
    labels: dict[tuple[str, str], str],
    versions: tuple[str, ...],
) -> RaterCalibration:
    pairs = _pairs(verdicts, labels)
    matrix = confusion(pairs)
    human_fails = sum(1 for _, human in pairs if human == "fail")
    human_passes = sum(1 for _, human in pairs if human == "pass")
    abstained = sum(1 for unit in sorted(verdicts) if verdicts[unit] == "abstain")
    pass_rate = {}
    for version in versions:
        of_version = [unit for unit in sorted(verdicts) if unit[0] == version]
        pass_rate[version] = _rate(
            sum(1 for unit in of_version if verdicts[unit] == "pass"), len(of_version)
        )
    return RaterCalibration(
        name=name,
        model=model,
        units=len(verdicts),
        scored=len(pairs),
        abstained=abstained,
        raw_agreement=raw_agreement(pairs),
        kappa=cohen_kappa(pairs),
        confusion=matrix,
        false_pass_rate=_rate(matrix["false_pass"], human_fails),
        false_fail_rate=_rate(matrix["false_fail"], human_passes),
        abstention_rate=_rate(abstained, len(verdicts)),
        pass_rate=pass_rate,
    )


def calibrate_panel(
    verdicts: dict[tuple[str, str], str],
    labels: dict[tuple[str, str], str],
    versions: tuple[str, ...],
    *,
    unanimous: dict[tuple[str, str], bool],
    split: dict[tuple[str, str], bool],
    escalated: dict[tuple[str, str], bool],
    judges: list[RaterCalibration],
) -> PanelCalibration:
    base = calibrate_rater("panel", "panel", verdicts, labels, versions)
    best = max(judges, key=lambda judge: judge.kappa, default=None)
    total = len(verdicts) or 1
    return PanelCalibration(
        name=base.name,
        model=base.model,
        units=base.units,
        scored=base.scored,
        abstained=base.abstained,
        raw_agreement=base.raw_agreement,
        kappa=base.kappa,
        confusion=base.confusion,
        false_pass_rate=base.false_pass_rate,
        false_fail_rate=base.false_fail_rate,
        abstention_rate=base.abstention_rate,
        pass_rate=base.pass_rate,
        unanimity_rate=sum(1 for unit in sorted(unanimous) if unanimous[unit]) / total,
        split_rate=sum(1 for unit in sorted(split) if split[unit]) / total,
        escalation_rate=sum(1 for unit in sorted(escalated) if escalated[unit]) / total,
        best_single_judge=best.name if best else "",
        best_single_judge_kappa=best.kappa if best else 0.0,
        kappa_vs_best_single_judge=base.kappa - (best.kappa if best else 0.0),
    )


def measure_consistency(
    per_judge: dict[str, dict[tuple[str, str], list[str]]],
    panel: dict[tuple[str, str], list[str]],
    repeats: int,
) -> ConsistencyReport:
    """Flip rate per judge and for the panel, across repeats of the same case."""

    def flip_rate(series: dict[tuple[str, str], list[str]]) -> float:
        if not series:
            return 0.0
        flipped = sum(1 for unit in sorted(series) if len(set(series[unit])) > 1)
        return flipped / len(series)

    return ConsistencyReport(
        units=len(panel),
        repeats=repeats,
        per_judge_flip_rate={name: flip_rate(per_judge[name]) for name in sorted(per_judge)},
        panel_flip_rate=flip_rate(panel),
    )


def measure_correlation(
    per_judge: dict[str, dict[tuple[str, str], str]],
    labels: dict[tuple[str, str], str],
    *,
    only_pairs_touching: set[str] | None = None,
) -> list[PairCorrelation]:
    """Pairwise: how often two judges are wrong on the SAME unit.

    `only_pairs_touching` restricts the output to pairs involving at least one of
    the named judges, which is how the shadow pairs are separated from the voting
    pairs without computing the arithmetic twice.
    """
    names = sorted(per_judge)
    correlations: list[PairCorrelation] = []
    for index, name_a in enumerate(names):
        for name_b in names[index + 1 :]:
            if only_pairs_touching is not None and not (
                {name_a, name_b} & only_pairs_touching
            ):
                continue
            verdicts_a, verdicts_b = per_judge[name_a], per_judge[name_b]
            common = [
                unit
                for unit in sorted(set(verdicts_a) & set(verdicts_b))
                if verdicts_a[unit] in CLASSES and verdicts_b[unit] in CLASSES
            ]
            total = len(common)
            wrong_a = [unit for unit in common if verdicts_a[unit] != labels[unit]]
            wrong_b = [unit for unit in common if verdicts_b[unit] != labels[unit]]
            joint = len(set(wrong_a) & set(wrong_b))
            rate_a = _rate(len(wrong_a), total)
            rate_b = _rate(len(wrong_b), total)
            expected = rate_a * rate_b
            correlations.append(
                PairCorrelation(
                    judge_a=name_a,
                    judge_b=name_b,
                    common_units=total,
                    error_rate_a=rate_a,
                    error_rate_b=rate_b,
                    joint_error_rate=_rate(joint, total),
                    expected_if_independent=expected,
                    ratio=(_rate(joint, total) / expected) if expected > 0 else 0.0,
                )
            )
    return correlations


def measure_discrimination(
    panel_series: dict[tuple[str, str], list[str]],
    case_ids: tuple[str, ...],
    versions: tuple[str, str],
    *,
    abstention_rate: float,
    unanimity_rate: float,
    panel_kappa: float = 0.0,
    panel_false_pass_rate: float = 0.0,
) -> DiscriminationReport:
    """Count the cases that contribute no signal at all.

    A case whose baseline and candidate answers receive identical verdicts on
    every repeat cannot distinguish the two prompt versions. It is not evidence
    that the candidate is fine, it is an absence of evidence, and a golden set
    made mostly of those cases produces a gate that cannot fail.
    """
    baseline, candidate = versions
    never = 0
    for case_id in case_ids:
        left = panel_series.get((baseline, case_id), [])
        right = panel_series.get((candidate, case_id), [])
        if left and right and set(left) == set(right) and len(set(left)) == 1:
            never += 1
    return DiscriminationReport(
        cases=len(case_ids),
        cases_that_never_discriminate=never,
        panel_abstention_rate=abstention_rate,
        unanimity_rate=unanimity_rate,
        panel_kappa=panel_kappa,
        panel_false_pass_rate=panel_false_pass_rate,
    )
