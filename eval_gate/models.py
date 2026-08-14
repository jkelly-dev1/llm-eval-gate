"""The typed contracts: one judge's verdict, one panel's verdict, one audit row.

These are pydantic v2 models rather than dicts because the judge output is the
one place a real model touches this system, and it is the place where things go
wrong quietly. A judge that returns prose instead of JSON, or JSON with a
verdict spelled "PASS", should degrade to an abstention that the calibration
layer can count, not to a KeyError three functions later.

`raw_ok` exists for that reason. It records whether the judge's raw output
parsed at all, separately from what the verdict was, because "the judge said
fail" and "the judge said something we could not read" are different facts and
a harness that conflates them will report a confident number it has not earned.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["pass", "fail", "abstain"]

#: The rubric criteria, in sorted order. Sorted because the criteria dict is
#: hashed into the audit trail and iterated for reporting, and an unstable key
#: order would make byte identical runs look different.
RUBRIC_CRITERIA: tuple[str, ...] = (
    "answers_the_question",
    "grounded_in_source",
    "no_invented_numbers",
    "well_formed",
)


class JudgeVerdict(BaseModel):
    """One judge's scored opinion of one candidate answer."""

    case_id: str
    sut_version: str
    judge_name: str
    judge_model: str
    verdict: Verdict
    criteria: dict[str, bool] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    #: False when the judge's raw output could not be parsed as the contract.
    raw_ok: bool = True
    #: Which repeat produced this verdict. Repeat 1 is the reported run; the
    #: rest exist only to measure how much that first run could have differed.
    repeat: int = 1
    #: True for a non voting shadow judge. Carried on the verdict itself so the
    #: aggregation step can REFUSE a shadow verdict rather than trusting the
    #: caller to have filtered it out. See panel.aggregate.
    shadow: bool = False

    def sorted_criteria(self) -> list[tuple[str, bool]]:
        return sorted(self.criteria.items())


class TokenUsage(BaseModel):
    """What one real call actually consumed, as the vendor reported it.

    This lives here rather than in cost.py because it is a contract about a
    provider response, and cost.py imports llm.py: putting it in the cost module
    would make the provider seam depend on the pricing layer.

    `exact` is the field that matters. A vendor reported usage figure is a
    measurement; characters divided by four is an approximation, and this project
    has one rule about those two that it enforces rather than documents, which is
    that they are never added together without the result being labeled
    approximate. See eval_gate.cost.cost_from_usage.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    #: True only when the numbers came from the API's own accounting.
    exact: bool = True


class PanelVerdict(BaseModel):
    """The panel's aggregated opinion, plus the disagreement it hid.

    `split` and `abstentions` are kept on the record rather than recomputed
    because a tie is a routing decision (escalate to a human), and the reason a
    case was escalated should survive in the audit trail.
    """

    case_id: str
    sut_version: str
    verdict: Verdict
    #: Per judge verdicts, sorted, so the tuple is stable for hashing.
    votes: tuple[str, ...] = ()
    unanimous: bool = False
    split: bool = False
    abstentions: int = 0
    #: True when the panel was not the panel it claims to be: a mix of real and
    #: mock judges, or a mock stand in for a slot with no credential. A
    #: partially real panel must never be reported as a real three model panel.
    degraded: bool = False
    #: Set when a tie sent the case to a human rather than to a verdict.
    escalated: bool = False
    repeat: int = 1


class AuditRecord(BaseModel):
    """One line of the hash chained log.

    Timestamps are NOT frozen. They are excluded from the determinism claim
    rather than faked, and the chain is made reproducible by canonical JSON
    instead. See audit.py.
    """

    event: str
    run_id: str
    timestamp: str
    payload: dict = Field(default_factory=dict)
    prev_hash: str = ""
    record_hash: str = ""

    def payload_for_hash(self) -> dict:
        """Everything except the hash itself, including prev_hash.

        prev_hash is inside the hashed payload on purpose: that is what makes
        the chain a chain. Drop it and each record still verifies against its
        own content while reordering the file goes undetected.
        """
        return {
            "event": self.event,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
        }
