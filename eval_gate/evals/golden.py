"""The golden set: 30 cases, with the system under test's answers baked in.

ALL CONTENT HERE IS SYNTHETIC. The questions, sources, and answers are written
to look like the internal policy and reporting material this design is for, and
no real company data is in this repository. The fictional organization is Acme,
and every address uses an .example TLD.

WHY THE SUT OUTPUTS ARE FIXTURES RATHER THAN GENERATED AT EVAL TIME

Each case carries the candidate answer for both prompt versions as data. That
makes the offline path fully deterministic, and it keeps the harness's job
clearly scoped: this project judges and gates, it does not generate. If the
answers were produced at eval time by the same mock that later judges them, a
regression in the mock would move both sides at once and the gate would sit
there measuring its own reflection.

THE LABEL DISTRIBUTION, AND WHY IT IS SKEWED ON PURPOSE

  sut.v1 (baseline):   24 pass / 6 fail
  sut.v2 (candidate):  16 pass / 14 fail

sut.v2 is GENUINELY WORSE by human label. That gap is the regression the gate
exists to catch, and it is planted rather than hoped for, so a gate that has
stopped catching it is provably broken rather than merely quiet.

The distribution is deliberately NOT 50/50. On the baseline, 80% of cases pass,
so a judge that passes everything scores 80% raw agreement and looks respectable
while being useless. That is precisely what makes raw agreement misleading and
Cohen's kappa necessary, and the skew is here so the project can demonstrate the
difference instead of asserting it.

THE AMBIGUOUS CASES

Four cases are tagged "ambiguous" (gc-013, gc-014, gc-029, gc-030). In each, the
answer restates the source almost verbatim but rounds, converts, or derives a
figure. Reasonable humans disagree about whether that is grounded: rounding
412,600,000 to "about 412.6 million" preserves the fact, while rounding 3.42% to
"roughly 3%" hides a 76 basis point move, and computing a growth figure the
source never states is arguably helpful and arguably invention. The human labels
here reflect that judgment call rather than a rule, which is why these are the
cases the panel splits on and the cases that produce the measured noise floor.

WHY EACH v2 FAILURE FAILS

  gc-015..gc-022  figures the source does not contain (the v2 prompt's
                  "prefer round, memorable figures" and "add nearby context"
                  clauses, doing exactly what they say)
  gc-023, gc-024  deferral instead of an answer (the "never say you cannot
                  answer" clause)
  gc-027, gc-028  wrong in both versions; these keep the gate from mistaking a
                  uniformly bad case for a regression
  gc-029, gc-030  ambiguous, labeled fail because the rounding is material
"""

from __future__ import annotations

from dataclasses import dataclass, field

BASELINE = "sut.v1"
CANDIDATE = "sut.v2"


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    question: str
    source: str
    #: sut version -> the candidate answer text.
    outputs: dict[str, str]
    #: sut version -> "pass" | "fail". The ground truth.
    human_label: dict[str, str]
    #: sut version -> one sentence on why.
    human_rationale: dict[str, str]
    tags: tuple[str, ...] = field(default_factory=tuple)

    def answer(self, sut_version: str) -> str:
        return self.outputs[sut_version]

    def label(self, sut_version: str) -> str:
        return self.human_label[sut_version]


def _case(
    case_id: str,
    question: str,
    source: str,
    v1: str,
    v2: str,
    label_v1: str,
    label_v2: str,
    why_v1: str,
    why_v2: str,
    tags: tuple[str, ...] = (),
) -> GoldenCase:
    return GoldenCase(
        case_id=case_id,
        question=question,
        source=source,
        outputs={BASELINE: v1, CANDIDATE: v2},
        human_label={BASELINE: label_v1, CANDIDATE: label_v2},
        human_rationale={BASELINE: why_v1, CANDIDATE: why_v2},
        tags=tuple(sorted(tags)),
    )


GOLDEN_SET: tuple[GoldenCase, ...] = (
    _case(
        "gc-001",
        "Who approves an expense report above 5,000 USD?",
        "Expense reports above 5,000 USD require approval from a director. "
        "Reports at or below 5,000 USD are approved by the employee's line manager.",
        "Expense reports above 5,000 USD require approval from a director, and reports "
        "at or below 5,000 USD are approved by the line manager.",
        "A director approves any expense report above 5,000 USD, and a line manager "
        "approves reports at or below 5,000 USD.",
        "pass",
        "pass",
        "Both thresholds quoted exactly as the source states them.",
        "Rephrased but every figure and role still matches the source.",
        ("grounded",),
    ),
    _case(
        "gc-002",
        "What is the deadline for submitting an expense report?",
        "Employees must submit expense reports within 30 days of the transaction date. "
        "Reports submitted after 60 days are rejected.",
        "Expense reports must be submitted within 30 days of the transaction date, and "
        "reports submitted after 60 days are rejected.",
        "Submit expense reports within 30 days of the transaction date, and after "
        "60 days the report is rejected.",
        "pass",
        "pass",
        "Both deadlines quoted exactly.",
        "Shorter wording, same two figures.",
        ("grounded",),
    ),
    _case(
        "gc-003",
        "How long are trade confirmations retained?",
        "Trade confirmations and order records are retained for 7 years from the date "
        "of the transaction.",
        "Trade confirmations and order records are retained for 7 years from the date "
        "of the transaction.",
        "Acme retains trade confirmations and order records for 7 years from the "
        "transaction date.",
        "pass",
        "pass",
        "Verbatim from the source.",
        "Reworded, retention period unchanged.",
        ("grounded",),
    ),
    _case(
        "gc-004",
        "How many business days does vendor onboarding review take?",
        "Vendor onboarding review completes within 15 business days once the supplier "
        "questionnaire is returned.",
        "Vendor onboarding review completes within 15 business days once the supplier "
        "questionnaire is returned.",
        "Vendor onboarding review takes up to 15 business days after the supplier "
        "questionnaire comes back.",
        "pass",
        "pass",
        "Verbatim from the source.",
        "Reworded, the 15 business day figure is intact.",
        ("grounded",),
    ),
    _case(
        "gc-005",
        "How often must service account credentials be rotated?",
        "Service account credentials are rotated every 90 days, and privileged account "
        "credentials every 30 days.",
        "Service account credentials are rotated every 90 days, and privileged account "
        "credentials every 30 days.",
        "Service account credentials rotate every 90 days, and privileged account "
        "credentials rotate every 30 days.",
        "pass",
        "pass",
        "Both rotation periods quoted exactly.",
        "Both rotation periods still correct.",
        ("grounded",),
    ),
    _case(
        "gc-006",
        "How quickly must a suspected data breach be reported?",
        "A suspected data breach must be reported to the security team within 4 hours "
        "of discovery, and to the regulator within 72 hours if it is confirmed.",
        "A suspected data breach must be reported to the security team within 4 hours "
        "of discovery, and to the regulator within 72 hours if confirmed.",
        "Report a suspected data breach to the security team within 4 hours of "
        "discovery, and to the regulator within 72 hours once confirmed.",
        "pass",
        "pass",
        "Both notification windows quoted exactly.",
        "Both notification windows still correct.",
        ("grounded",),
    ),
    _case(
        "gc-007",
        "What is the maximum refund an agent can issue without escalation?",
        "A support agent may issue refunds up to 250 USD per case without escalation. "
        "Refunds above that amount require a supervisor.",
        "A support agent may issue refunds up to 250 USD per case without escalation, "
        "and refunds above that amount require a supervisor.",
        "A support agent can refund up to 250 USD per case without escalation, and a "
        "supervisor is required above that amount.",
        "pass",
        "pass",
        "Ceiling and escalation rule both quoted exactly.",
        "Ceiling and escalation rule both still correct.",
        ("grounded",),
    ),
    _case(
        "gc-008",
        "How long are nightly database backups kept?",
        "Nightly database backups are kept for 35 days, and monthly snapshots are kept "
        "for 18 months.",
        "Nightly database backups are kept for 35 days, and monthly snapshots are kept "
        "for 18 months.",
        "Nightly database backups are kept 35 days, and monthly snapshots 18 months.",
        "pass",
        "pass",
        "Both retention periods quoted exactly.",
        "Terser, both retention periods intact.",
        ("grounded",),
    ),
    _case(
        "gc-009",
        "How often is privileged access reviewed?",
        "Privileged access is reviewed quarterly, and the review must be completed "
        "within 10 business days of the quarter close.",
        "Privileged access is reviewed quarterly, and the review must be completed "
        "within 10 business days of the quarter close.",
        "Privileged access is reviewed quarterly, and the review closes within "
        "10 business days of the quarter close.",
        "pass",
        "pass",
        "Cadence and completion window both quoted exactly.",
        "Cadence and completion window both still correct.",
        ("grounded",),
    ),
    _case(
        "gc-010",
        "What encryption is required for data at rest?",
        "Data at rest is encrypted with AES-256, and data in transit uses TLS 1.3.",
        "Data at rest is encrypted with AES-256, and data in transit uses TLS 1.3.",
        "Data at rest uses AES-256 encryption, and data in transit uses TLS 1.3.",
        "pass",
        "pass",
        "Both standards quoted exactly.",
        "Both standards still correct.",
        ("grounded",),
    ),
    _case(
        "gc-011",
        "When must a contractor return an issued laptop?",
        "Contractors return issued laptops within 5 business days of their last "
        "working day.",
        "Contractors return issued laptops within 5 business days of their last "
        "working day.",
        "A contractor returns an issued laptop within 5 business days of the last "
        "working day.",
        "pass",
        "pass",
        "Verbatim from the source.",
        "Reworded, the 5 business day window is intact.",
        ("grounded",),
    ),
    _case(
        "gc-012",
        "When does the year end change freeze run?",
        "The year end change freeze runs from 18 December to 3 January, and only "
        "severity 1 fixes are deployed during it.",
        "The year end change freeze runs from 18 December to 3 January, and only "
        "severity 1 fixes are deployed during it.",
        "The year end change freeze runs 18 December to 3 January, and only severity 1 "
        "fixes deploy during the freeze.",
        "pass",
        "pass",
        "Dates and the severity 1 exception both quoted exactly.",
        "Dates and the severity 1 exception both still correct.",
        ("grounded",),
    ),
    _case(
        "gc-013",
        "What was fourth quarter revenue for the retail division?",
        "The retail division reported revenue of 412,600,000 USD in the fourth "
        "quarter, up from 388,100,000 USD a year earlier.",
        "The retail division reported revenue of about 412.6 million USD in the fourth "
        "quarter, up from roughly 388.1 million USD a year earlier.",
        "Retail division fourth quarter revenue was around 412.6 million USD, compared "
        "with about 388.1 million USD a year earlier.",
        "pass",
        "pass",
        "Rounding to millions preserves both figures exactly; a reader loses nothing.",
        "Same rounding, same two figures, still faithful.",
        ("ambiguous", "rounded-number"),
    ),
    _case(
        "gc-014",
        "How much did the logistics automation program cost?",
        "The logistics automation program cost 2,450,000 USD over two years.",
        "The logistics automation program cost about 2.45 million USD over two years.",
        "The logistics automation program came to roughly 2.45 million USD across "
        "two years.",
        "pass",
        "pass",
        "Expressing 2,450,000 as 2.45 million is the same number in other units.",
        "Same conversion, still the same number.",
        ("ambiguous", "rounded-number"),
    ),
    _case(
        "gc-015",
        "What is the weekly overtime cap?",
        "Weekly overtime is capped at 12 hours, and any additional hours require "
        "written approval from human resources.",
        "Weekly overtime is capped at 12 hours, and additional hours require written "
        "approval from human resources.",
        "Weekly overtime is capped at 20 hours, and staff may bank up to 45 hours of "
        "overtime toward future leave.",
        "pass",
        "fail",
        "Cap and approval rule both quoted exactly.",
        "The 20 hour cap contradicts the source, and the banked leave rule is invented.",
        ("hallucinated-number",),
    ),
    _case(
        "gc-016",
        "How long is the probation period for a new employee?",
        "New employees serve a probation period of 6 months, extendable once by "
        "3 months.",
        "New employees serve a probation period of 6 months, extendable once by "
        "3 months.",
        "New employees serve a probation period of 90 days, and a second review "
        "follows 45 days later.",
        "pass",
        "fail",
        "Both periods quoted exactly.",
        "Neither the 90 day probation nor the 45 day review appears in the source.",
        ("hallucinated-number",),
    ),
    _case(
        "gc-017",
        "How quickly is a data subject access request answered?",
        "A data subject access request is answered within 30 calendar days, extendable "
        "by 60 days for complex requests.",
        "A data subject access request is answered within 30 calendar days, extendable "
        "by 60 days for complex requests.",
        "A data subject access request is answered within 7 calendar days, and Acme "
        "charges a 25 USD handling fee.",
        "pass",
        "fail",
        "Both windows quoted exactly.",
        "The 7 day window is wrong and the handling fee does not exist.",
        ("hallucinated-number",),
    ),
    _case(
        "gc-018",
        "What are the standard supplier payment terms?",
        "Standard supplier payment terms are net 45 days, with a 2 percent discount "
        "for payment within 10 days.",
        "Standard supplier payment terms are net 45 days, with a 2 percent discount "
        "for payment within 10 days.",
        "Standard supplier payment terms are net 90 days, with an 8 percent discount "
        "for settlement within 5 days.",
        "pass",
        "fail",
        "Terms and early payment discount both quoted exactly.",
        "All three figures are invented and materially favorable to the supplier.",
        ("hallucinated-number",),
    ),
    _case(
        "gc-019",
        "How long are CCTV recordings from distribution centers retained?",
        "CCTV recordings from distribution centers are retained for 31 days.",
        "CCTV recordings from distribution centers are retained for 31 days.",
        "CCTV recordings from distribution centers are retained for 180 days and "
        "copied to an offsite vault every 14 days.",
        "pass",
        "fail",
        "Verbatim from the source.",
        "The retention period is wrong and the offsite copy schedule is invented.",
        ("hallucinated-number",),
    ),
    _case(
        "gc-020",
        "How quickly must staff complete annual security awareness training?",
        "All staff complete annual security awareness training within 21 days of "
        "assignment.",
        "All staff complete annual security awareness training within 21 days of "
        "assignment.",
        "All staff complete annual security awareness training within 60 days of "
        "assignment, and managers within 75 days.",
        "pass",
        "fail",
        "Verbatim from the source.",
        "Both figures are invented and the manager deadline does not exist.",
        ("hallucinated-number",),
    ),
    _case(
        "gc-021",
        "What is the warehouse pick accuracy target?",
        "The warehouse pick accuracy target is 99.4 percent, measured monthly.",
        "The warehouse pick accuracy target is 99.4 percent, measured monthly.",
        "The warehouse pick accuracy target is 87.5 percent, measured every 3 weeks.",
        "pass",
        "fail",
        "Target and measurement cadence both quoted exactly.",
        "Both the target and the cadence are invented, and the target is far lower.",
        ("hallucinated-number",),
    ),
    _case(
        "gc-022",
        "When do loyalty points expire?",
        "Loyalty points expire 24 months after they are earned.",
        "Loyalty points expire 24 months after they are earned.",
        "Loyalty points expire 6 months after they are earned, and 500 points are "
        "forfeited when an account closes.",
        "pass",
        "fail",
        "Verbatim from the source.",
        "The expiry period is wrong and the forfeiture rule is invented.",
        ("hallucinated-number",),
    ),
    _case(
        "gc-023",
        "Who signs off a change to store opening hours?",
        "A change to store opening hours is signed off by the regional operations "
        "manager.",
        "A change to store opening hours is signed off by the regional operations "
        "manager.",
        "This information is maintained by the relevant internal team and may change "
        "from time to time.",
        "pass",
        "fail",
        "Verbatim from the source.",
        "A deferral, not an answer; the source states the approver plainly.",
        ("non-answer",),
    ),
    _case(
        "gc-024",
        "Which team owns unredeemed gift card liability?",
        "Unredeemed gift card liability is owned by the finance controller's team.",
        "Unredeemed gift card liability is owned by the finance controller's team.",
        "Please refer to the applicable policy documentation for guidance on this "
        "topic.",
        "pass",
        "fail",
        "Verbatim from the source.",
        "A deferral, not an answer; the owning team is stated in the source.",
        ("non-answer",),
    ),
    _case(
        "gc-025",
        "What is the annual travel budget for the chief executive?",
        "The chief executive's travel budget is 180,000 USD per year, reviewed by the "
        "audit committee.",
        "The chief executive's travel budget is 250,000 USD per year, with a "
        "40,000 USD discretionary allowance on top.",
        "The chief executive's travel budget is 180,000 USD per year and is reviewed "
        "by the audit committee.",
        "fail",
        "pass",
        "Neither the 250,000 USD budget nor the discretionary allowance is in the source.",
        "Budget and reviewing committee both quoted exactly.",
        ("hallucinated-number", "regression-fixed"),
    ),
    _case(
        "gc-026",
        "Who chairs the risk committee?",
        "The risk committee is chaired by the chief risk officer, who reports to the "
        "board quarterly.",
        "That detail is held elsewhere and is not summarized here.",
        "The risk committee is chaired by the chief risk officer, who reports to the "
        "board quarterly.",
        "fail",
        "pass",
        "A deferral, not an answer; the chair is named in the source.",
        "Chair and reporting cadence both quoted exactly.",
        ("non-answer", "regression-fixed"),
    ),
    _case(
        "gc-027",
        "How many distribution centers does Acme operate?",
        "Acme operates 14 distribution centers across three regions.",
        "Acme operates 26 distribution centers across seven regions.",
        "Acme operates 9 distribution centers and plans to add 5 more within "
        "18 months.",
        "fail",
        "fail",
        "The count and the region count both contradict the source.",
        "The count is wrong and the expansion plan is invented.",
        ("hallucinated-number", "wrong-in-both"),
    ),
    _case(
        "gc-028",
        "What is the target for on time delivery?",
        "The on time delivery target is 96 percent, measured weekly against the "
        "promised delivery date.",
        "The on time delivery target is 82 percent, measured every 4 weeks against the "
        "dispatch date.",
        "The on time delivery target is 75 percent and is reviewed twice a year "
        "against a 30 day window.",
        "fail",
        "fail",
        "Target, cadence, and baseline date are all wrong.",
        "Target and review cadence are invented and the 30 day window does not exist.",
        ("hallucinated-number", "wrong-in-both"),
    ),
    _case(
        "gc-029",
        "What was the group operating margin?",
        "Group operating margin was 3.42 percent for the year, down from 4.18 percent "
        "a year earlier.",
        "Group operating margin was roughly 3 percent for the year, down from about "
        "4 percent a year earlier.",
        "Group operating margin was around 3 percent, compared with 4 percent "
        "previously.",
        "fail",
        "fail",
        "Rounding to whole percent hides a 76 basis point move, which is the story.",
        "Same rounding, and the comparison is now too vague to be checkable.",
        ("ambiguous", "rounded-number"),
    ),
    _case(
        "gc-030",
        "What was the year on year change in retail revenue?",
        "Retail revenue was 412,600,000 USD this year and 388,100,000 USD last year.",
        "Retail revenue grew from about 388.1 million USD to about 412.6 million USD, "
        "an increase of roughly 6 percent.",
        "Retail revenue rose from roughly 388.1 million USD to around 412.6 million "
        "USD, an increase of about 6 percent.",
        "fail",
        "fail",
        "The source states no growth figure; both the delta and the percentage are computed.",
        "Same computed figures, rounded further, still absent from the source.",
        ("ambiguous", "derived-number"),
    ),
)


def label_distribution() -> dict[str, dict[str, int]]:
    """Human label counts per sut version, for the module's own claim to be checkable."""
    counts: dict[str, dict[str, int]] = {}
    for version in (BASELINE, CANDIDATE):
        counts[version] = {
            "pass": sum(1 for case in GOLDEN_SET if case.label(version) == "pass"),
            "fail": sum(1 for case in GOLDEN_SET if case.label(version) == "fail"),
        }
    return counts


def cases_by_tag(tag: str) -> tuple[GoldenCase, ...]:
    return tuple(case for case in GOLDEN_SET if tag in case.tags)


def all_tags() -> list[str]:
    return sorted({tag for case in GOLDEN_SET for tag in case.tags})
