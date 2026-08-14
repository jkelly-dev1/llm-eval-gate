"""One judge call: render, invoke, parse tolerantly, abstain rather than raise.

The tolerant parser is not a nicety. A real model wraps JSON in code fences or
prose no matter what the system prompt says, and a judge that raises on that
takes the whole eval run down with it. Worse, a judge that silently coerces
unparseable output into "fail" corrupts the measurement: the calibration layer
would then attribute a parse failure to the system under test.

So an unparseable response becomes verdict="abstain" with raw_ok=False. An
abstention does not vote, and the abstention rate is reported separately from
agreement, which means a judge that is quietly broken shows up as a rising
abstention rate rather than as a plausible looking accuracy number.
"""

from __future__ import annotations

import json
import re

from eval_gate.evals.golden import GoldenCase
from eval_gate.llm import LLMProvider
from eval_gate.models import RUBRIC_CRITERIA, JudgeVerdict
from eval_gate.prompts import PromptLibrary, render_judge_user_prompt

_VALID_VERDICTS = ("pass", "fail", "abstain")


def parse_judge_response(raw: str) -> tuple[str, dict[str, bool], list[str], bool]:
    """Tolerantly parse a judge response into (verdict, criteria, reasons, raw_ok).

    Strips code fences and any prose surrounding the JSON object. A response that
    cannot be parsed, or that parses but carries a verdict outside the contract,
    is treated as an abstention rather than an exception.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            return "abstain", {}, ["unparseable judge response"], False
        text = text[start : end + 1]
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return "abstain", {}, ["unparseable judge response"], False
    if not isinstance(payload, dict):
        return "abstain", {}, ["judge response was not an object"], False

    verdict = str(payload.get("verdict", "")).strip().lower()
    if verdict not in _VALID_VERDICTS:
        return "abstain", {}, [f"unrecognized verdict {verdict!r}"], False

    criteria_raw = payload.get("criteria")
    criteria: dict[str, bool] = {}
    if isinstance(criteria_raw, dict):
        for name in RUBRIC_CRITERIA:
            if name in criteria_raw:
                criteria[name] = bool(criteria_raw[name])

    reasons_raw = payload.get("reasons")
    reasons = [str(item) for item in reasons_raw] if isinstance(reasons_raw, list) else []
    return verdict, criteria, reasons, True


def judge_case(
    judge: LLMProvider,
    case: GoldenCase,
    sut_version: str,
    *,
    repeat: int = 1,
    library: PromptLibrary | None = None,
    judge_prompt_version: str = "v1",
    judge_name: str | None = None,
    shadow: bool = False,
) -> JudgeVerdict:
    """Score one candidate answer with one judge.

    `judge_name` overrides the provider's own name with a slot qualified one, so
    a panel holding two judges of the same kind reports two judges rather than
    merging them into one.

    `shadow` marks the verdict as non voting. It is stamped on the verdict rather
    than tracked by the caller, so panel.aggregate can refuse it outright instead
    of relying on every call site to have filtered correctly.
    """
    library = library or PromptLibrary()
    system = library.get("judge", judge_prompt_version).body
    user = render_judge_user_prompt(
        case_id=case.case_id,
        sut_version=sut_version,
        question=case.question,
        source=case.source,
        answer=case.answer(sut_version),
        attempt=repeat,
    )
    raw = judge.complete(system=system, user=user)
    verdict, criteria, reasons, raw_ok = parse_judge_response(raw)
    return JudgeVerdict(
        case_id=case.case_id,
        sut_version=sut_version,
        judge_name=judge_name or judge.name,
        judge_model=judge.model,
        verdict=verdict,
        criteria=criteria,
        reasons=reasons,
        raw_ok=raw_ok,
        repeat=repeat,
        shadow=shadow,
    )
