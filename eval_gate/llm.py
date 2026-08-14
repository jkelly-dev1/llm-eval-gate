"""Provider seam: one Protocol, three implementations, plus a panel factory.

- MockJudgeStrict / MockJudgeLenient / MockJudgeBalanced: three DISTINCT
  deterministic judges. They are separate behaviors rather than one function
  with three names, because a panel exists to disagree and a panel of clones
  cannot. Offline disagreement has to be real or every panel metric in
  calibration.py is measuring nothing.
- MockJudgeMiscalibrated: a judge that passes almost everything. It is a fixture
  for the two demonstrations the project exists to make, not a bug.
- SHADOW_MOCK_BENCH: four further distinct behaviors for the non voting bench.
  The number of shadow judges an offline run builds follows SHADOW_JUDGE_MODELS,
  because a bench whose size does not match its configuration reports metrics for
  a bench nobody asked for. Asking for more slots than the bench holds raises.
- AnthropicProvider / OpenAIProvider: real paths, SDK imported lazily and only
  when both the provider name and its API key are present.

Determinism, all four mechanisms from the house convention, applied from the
start rather than retrofitted:

  1. Extractive. Every signal a mock judge uses is derived from the prompt text
     it was handed. Nothing is invented.
  2. sorted() on every dict, set, and filesystem iteration.
  3. sha256 in place of randomness, via `_stable_score`. There is no seeded RNG
     anywhere in this package, because nothing is random.
  4. Scripted behaviors keyed off the input text, so each rubric criterion
     fires deterministically and no test is vacuous.

API constraints that shaped the real paths, current as of 2026-07:

  - Do NOT send temperature, top_p or top_k. They are removed on claude-opus-5
    and claude-sonnet-5 and return HTTP 400. There is no determinism knob, so
    this project measures variance instead of configuring it away.
  - Do NOT use assistant message prefill to force JSON; it returns 400 on Opus 5
    and Sonnet 5. Structured output via output_config is used instead, wrapped
    in try/except TypeError so an older SDK falls back to instruction only.
  - Check stop_reason == "refusal" BEFORE reading response.content. Reading
    content[0] unconditionally breaks on a refusal, and for a judge a refusal is
    an abstention rather than a crash.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Protocol, runtime_checkable

from eval_gate.config import Settings, get_settings
from eval_gate.models import RUBRIC_CRITERIA, TokenUsage
from eval_gate.prompts import JUDGE_SCHEMA

MOCK_MODEL = "mock-deterministic-v1"

_SECTION = re.compile(
    r"^(QUESTION|SOURCE|CANDIDATE ANSWER):\s*$(?P<body>.*?)(?=^\w[\w ]*:\s*$|\Z)",
    re.MULTILINE | re.DOTALL,
)
_ATTEMPT = re.compile(r"^ATTEMPT:\s*(\d+)\s*$", re.MULTILINE)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORD = re.compile(r"[a-z][a-z']+")

#: Words that carry no topical signal. Kept as a frozenset and always iterated
#: through sorted() where order could leak into output.
_STOPWORDS = frozenset(
    {
        "and",
        "any",
        "are",
        "the",
        "for",
        "from",
        "does",
        "how",
        "many",
        "much",
        "must",
        "not",
        "per",
        "that",
        "this",
        "was",
        "were",
        "what",
        "when",
        "which",
        "who",
        "with",
        "within",
        "will",
        "long",
        "have",
        "has",
        "its",
        "their",
    }
)

#: Phrases a deferral uses instead of answering. Scripted, so the
#: answers_the_question criterion fires deterministically on the non answers in
#: the golden set rather than by accident of word overlap.
_DEFERRAL_MARKERS = (
    "refer to the applicable",
    "consult the",
    "held elsewhere",
    "not summarized here",
    "may change from time to time",
    "relevant internal team",
    "internal documentation",
    "i cannot",
    "i can't",
    "unable to",
)


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, *, system: str, user: str) -> str:
        """Return the judge's raw text response for one rendered prompt."""
        ...


def _stable_score(text: str) -> float:
    """A deterministic pseudo-score in [0,1] derived from text (no randomness)."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


# --------------------------------------------------------------------------- #
# Extractive signals. Everything a mock judge believes comes from these.
# --------------------------------------------------------------------------- #


def parse_judge_prompt(user: str) -> dict[str, str]:
    """Read the rendered judge prompt back apart into its blocks.

    Mock judges receive the same prompt string a real model would, so they
    have to recover the fields from it. Passing the golden case object straight
    to the mock lets the mock see fields the real judge never gets, and the
    prompt can then rot without any test noticing.
    """
    fields = {"question": "", "source": "", "answer": "", "attempt": "1"}
    for match in _SECTION.finditer(user):
        label = match.group(1)
        body = match.group("body").strip()
        if label == "QUESTION":
            fields["question"] = body
        elif label == "SOURCE":
            fields["source"] = body
        else:
            fields["answer"] = body
    attempt = _ATTEMPT.search(user)
    if attempt:
        fields["attempt"] = attempt.group(1)
    return fields


def _numbers(text: str) -> set[str]:
    """Numeric tokens, comma stripped, so 412,600,000 and 412600000 match."""
    return {match.group(0).replace(",", "").rstrip(".") for match in _NUMBER.finditer(text)}


def _stem(word: str) -> str:
    """A crude suffix stripper, deliberately not a real stemmer.

    It exists because comparing raw words marks a good answer off topic: the
    question says "submitting an expense report" and the answer says "expense
    reports must be submitted", which share exactly one token. Plural and participle endings are the only cases that mattered on
    this corpus, so those are the only ones handled. Anything cleverer would be
    another dependency and another thing to be wrong.
    """
    stem = word.strip("'")
    if stem.endswith("'s"):
        stem = stem[:-2]
    if stem.endswith("ies") and len(stem) > 4:
        stem = stem[:-3] + "y"
    elif stem.endswith("s") and not stem.endswith("ss"):
        stem = stem[:-1]
    if stem.endswith("ing") and len(stem) > 5:
        stem = stem[:-3]
    elif stem.endswith("ed") and len(stem) > 4:
        stem = stem[:-2]
    return stem


_STOPWORD_STEMS = frozenset(_stem(word) for word in _STOPWORDS)


def _tokens(text: str) -> set[str]:
    stems = {_stem(word) for word in _WORD.findall(text.lower()) if len(word) > 2}
    return stems - _STOPWORD_STEMS


#: Unit scales an answer might restate a source figure in: as written, and in
#: thousands, millions, or billions. Nothing else, because a scale set that is
#: too generous starts matching unrelated numbers by coincidence.
_SCALES = (1, 10**3, 10**6, 10**9)


def _significant_digits(token: str) -> int:
    digits = token.replace(".", "").lstrip("0")
    return len(digits.rstrip("0")) or 1


def _round_significant(value: float, digits: int) -> float:
    if value == 0:
        return 0.0
    exponent = math.floor(math.log10(abs(value)))
    factor = 10.0 ** (digits - 1 - exponent)
    return round(value * factor) / factor


def _approximately_matches(answer_token: str, source_token: str) -> bool:
    """True when the answer's figure is the source's figure, rounded or rescaled.

    "about 412.6 million" matches 412,600,000 and "roughly 3 percent" matches
    3.42 percent, because rounding the source figure to the number of
    significant digits the answer actually stated reproduces it. An invented
    figure does not survive that test, so this signal separates a rounded
    answer from a hallucinated one rather than a tolerance band that blurs
    the two together.
    """
    try:
        answer_value = float(answer_token)
        source_value = float(source_token)
    except ValueError:  # pragma: no cover - the regex only yields numerals
        return False
    if answer_value == 0 or source_value == 0:
        return answer_value == source_value
    digits = _significant_digits(answer_token)
    target = _round_significant(answer_value, digits)
    for scale in _SCALES:
        if math.isclose(
            _round_significant(source_value / scale, digits), target, rel_tol=1e-9
        ):
            return True
    return False


def approximate_grounding(answer: str, source: str) -> float:
    """Fraction of the answer's figures that round or rescale to a source figure.

    The lenient judge uses this where the strict judge uses exact `grounding`.
    That single difference is what makes the ambiguous cases in the golden set
    split the panel: both judges are applying a defensible reading of the same
    rubric line, and the harness measures which reading agrees with the humans
    rather than declaring one of them correct.
    """
    answer_numbers = _numbers(answer)
    if not answer_numbers:
        return 1.0
    source_numbers = sorted(_numbers(source))
    hits = 0
    for number in sorted(answer_numbers):
        if any(_approximately_matches(number, candidate) for candidate in source_numbers):
            hits += 1
    return hits / len(answer_numbers)


def grounding(answer: str, source: str) -> float:
    """Fraction of the answer's figures that appear literally in the source.

    Scores 1.0 when the answer states no figures at all: an answer with no
    numbers cannot invent one. A rounded, converted, or derived figure counts as
    absent, which is the whole reason the ambiguous cases in the golden set
    split the panel rather than merely being hard.
    """
    answer_numbers = _numbers(answer)
    if not answer_numbers:
        return 1.0
    source_numbers = _numbers(source)
    hits = sum(1 for number in sorted(answer_numbers) if number in source_numbers)
    return hits / len(answer_numbers)


def topicality(answer: str, question: str) -> float:
    """Fraction of the question's content words the answer addresses."""
    question_tokens = _tokens(question)
    if not question_tokens:
        return 1.0
    answer_tokens = _tokens(answer)
    return len(question_tokens & answer_tokens) / len(question_tokens)


def source_overlap(answer: str, source: str) -> float:
    """Fraction of the answer's content words that came from the source."""
    answer_tokens = _tokens(answer)
    if not answer_tokens:
        return 0.0
    return len(answer_tokens & _tokens(source)) / len(answer_tokens)


def is_deferral(answer: str) -> bool:
    lowered = answer.lower()
    return any(marker in lowered for marker in _DEFERRAL_MARKERS)


def well_formed(answer: str) -> bool:
    stripped = answer.strip()
    return len(stripped) >= 40 and stripped.endswith(".") and not is_deferral(stripped)


def _verdict_json(
    verdict: str,
    criteria: dict[str, bool],
    reasons: list[str],
) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "criteria": {key: criteria[key] for key in sorted(criteria)},
            "reasons": reasons,
        }
    )


def _reasons(criteria: dict[str, bool]) -> list[str]:
    """One clause per failed criterion, in sorted criterion order."""
    return [f"{name} failed" for name in sorted(criteria) if not criteria[name]]


def _abstain(reason: str) -> str:
    """An abstention with every criterion unmet, for an unscoreable input."""
    return _verdict_json("abstain", {name: False for name in RUBRIC_CRITERIA}, [reason])


# --------------------------------------------------------------------------- #
# Three distinct deterministic judges.
# --------------------------------------------------------------------------- #


class MockJudgeStrict:
    """Grounding focused. Fails any answer whose figures are not in the source.

    This is the judge that catches the sut.v2 regression, and it is also the
    judge that flags the ambiguous cases where a human would shrug. Its false
    fail rate is the price of its low false pass rate, so calibration.py reports
    the two separately instead of averaging them into one number that hides the
    tradeoff.
    """

    name = "mock-strict"

    def __init__(self, model: str = MOCK_MODEL) -> None:
        self.model = model

    def complete(self, *, system: str, user: str) -> str:
        fields = parse_judge_prompt(user)
        answer, source, question = fields["answer"], fields["source"], fields["question"]
        if not answer or not source:
            return _abstain("nothing to score")
        criteria = {
            "answers_the_question": topicality(answer, question) >= 0.40
            and not is_deferral(answer),
            "grounded_in_source": grounding(answer, source) >= 1.0
            and source_overlap(answer, source) >= 0.35,
            "no_invented_numbers": grounding(answer, source) >= 1.0,
            "well_formed": well_formed(answer),
        }
        verdict = "pass" if all(criteria.values()) else "fail"
        return _verdict_json(verdict, criteria, _reasons(criteria))


class MockJudgeLenient:
    """Passes anything on topic and well formed.

    It only fails an answer that is a deferral, malformed, or wholly ungrounded
    (every figure invented, or almost no words shared with the source). That is
    a realistic judge, not a broken one: plenty of production judges are this
    permissive, and the point of measuring is to find out.
    """

    name = "mock-lenient"

    def __init__(self, model: str = MOCK_MODEL) -> None:
        self.model = model

    def complete(self, *, system: str, user: str) -> str:
        fields = parse_judge_prompt(user)
        answer, source, question = fields["answer"], fields["source"], fields["question"]
        if not answer or not source:
            return _abstain("nothing to score")
        overlap = source_overlap(answer, source)
        criteria = {
            "answers_the_question": topicality(answer, question) >= 0.15
            and not is_deferral(answer),
            "grounded_in_source": overlap >= 0.10,
            # Accepts a figure that rounds or rescales to one in the source, and
            # fails only when most of the answer's figures match nothing at all.
            # This is the one rubric line where it differs from the strict judge,
            # and it is where the panel's real disagreement comes from.
            "no_invented_numbers": approximate_grounding(answer, source) >= 0.5,
            "well_formed": well_formed(answer),
        }
        verdict = "pass" if all(criteria.values()) else "fail"
        return _verdict_json(verdict, criteria, _reasons(criteria))


#: Weights for the balanced judge's composite score, and the threshold it is
#: compared against. Tuned so that a clearly grounded answer lands well above,
#: a wholly invented answer lands well below, and a rounded or derived figure
#: lands inside one jitter width of the line. That band is where the sha256
#: pseudo-score decides, and supplies the only offline flip rate.
BALANCED_WEIGHTS = {
    "grounding": 0.45,
    "topicality": 0.20,
    "source_overlap": 0.20,
    "well_formed": 0.15,
}
BALANCED_THRESHOLD = 0.70
BALANCED_JITTER = 0.09


class MockJudgeBalanced:
    """The sha256 pseudo-score form, thresholded.

    A composite of the same extractive signals, plus a deterministic jitter
    derived from sha256 of the answer and the attempt number. Cases whose
    composite sits more than one jitter width from the threshold are decided the
    same way on every repeat; cases inside that band flip. Those borderline
    cases are exactly the ones the strict and lenient judges disagree on, so the
    balanced judge is the panel's swing vote and its flip rate becomes the
    panel's flip rate. That is the measured noise floor the gate compares its
    threshold against.

    The jitter is not a simulation of temperature. Opus 5 and Sonnet 5 do not
    accept a temperature parameter at all. It is a stand in for the variance a
    real judge has anyway, put here so the self consistency layer is exercised
    offline without ever becoming non reproducible.
    """

    name = "mock-balanced"

    def __init__(self, model: str = MOCK_MODEL) -> None:
        self.model = model

    def score(self, *, answer: str, source: str, question: str) -> float:
        # Half credit for a figure that only rounds or rescales to the source.
        # That is what puts the ambiguous cases within one jitter width of the
        # threshold while leaving invented figures well below it.
        ground = 0.5 * grounding(answer, source) + 0.5 * approximate_grounding(
            answer, source
        )
        return (
            BALANCED_WEIGHTS["grounding"] * ground
            + BALANCED_WEIGHTS["topicality"] * min(topicality(answer, question) / 0.5, 1.0)
            + BALANCED_WEIGHTS["source_overlap"] * source_overlap(answer, source)
            + BALANCED_WEIGHTS["well_formed"] * (1.0 if well_formed(answer) else 0.0)
        )

    def complete(self, *, system: str, user: str) -> str:
        fields = parse_judge_prompt(user)
        answer, source, question = fields["answer"], fields["source"], fields["question"]
        if not answer or not source:
            return _abstain("nothing to score")
        base = self.score(answer=answer, source=source, question=question)
        jitter = (_stable_score(f"{answer}|attempt={fields['attempt']}") - 0.5) * (
            2 * BALANCED_JITTER
        )
        passed = (base + jitter) >= BALANCED_THRESHOLD
        criteria = {
            "answers_the_question": topicality(answer, question) >= 0.25
            and not is_deferral(answer),
            "grounded_in_source": source_overlap(answer, source) >= 0.30,
            "no_invented_numbers": grounding(answer, source) >= 1.0,
            "well_formed": well_formed(answer),
        }
        if passed:
            # A pass is a pass: the composite outweighed the individual misses,
            # and reporting criteria that contradict the verdict would make the
            # audit trail lie about what the judge decided.
            criteria = {key: True for key in criteria}
            reasons = [f"composite {base:+.3f} at or above threshold {BALANCED_THRESHOLD:.2f}"]
        else:
            reasons = _reasons(criteria) or [
                f"composite {base:+.3f} below threshold {BALANCED_THRESHOLD:.2f}"
            ]
        return _verdict_json("pass" if passed else "fail", criteria, reasons)


class MockJudgeMiscalibrated:
    """Passes almost everything. Blind, by construction, to the v2 regression.

    Exported as a fixture rather than hidden in a test, because the two
    demonstrations that carry this project need it to be reproducible: with ONE
    of these on the panel the other two outvote it and the gate still catches
    the regression; with TWO the panel is captured, reports high unanimity, and
    waves a genuine regression through. A harness that cannot show its own
    failure mode has not earned the pass.
    """

    name = "mock-miscalibrated"

    def __init__(self, model: str = MOCK_MODEL) -> None:
        self.model = model

    def complete(self, *, system: str, user: str) -> str:
        fields = parse_judge_prompt(user)
        answer = fields["answer"]
        if not answer:
            return _abstain("nothing to score")
        passed = len(answer.strip()) >= 20
        criteria = {name: passed for name in RUBRIC_CRITERIA}
        reasons = [] if passed else ["answer too short to score"]
        return _verdict_json("pass" if passed else "fail", criteria, reasons)


class MockJudgeVerbosityBiased:
    """Shadow judge with a length bias: rewards fluent, substantial answers.

    This is a documented real failure mode of LLM judges rather than an invented
    one. It checks that the answer is on topic and long enough to look like an
    answer, and never checks a figure at all. On this golden set that produces a
    high false pass rate, because every hallucinated candidate is fluent and on
    topic; hallucination is what fluency looks like when it is wrong.

    It is a shadow judge, so it is measured and it does not vote.
    """

    name = "mock-verbosity"

    def __init__(self, model: str = MOCK_MODEL) -> None:
        self.model = model

    def complete(self, *, system: str, user: str) -> str:
        fields = parse_judge_prompt(user)
        answer, source, question = fields["answer"], fields["source"], fields["question"]
        if not answer or not source:
            return _abstain("nothing to score")
        substantial = len(answer.strip()) >= 80
        on_topic = topicality(answer, question) >= 0.20 and not is_deferral(answer)
        criteria = {
            "answers_the_question": on_topic,
            "grounded_in_source": substantial,
            # Never inspects the figures. That is the bias, stated plainly.
            "no_invented_numbers": True,
            "well_formed": well_formed(answer),
        }
        verdict = "pass" if all(criteria.values()) else "fail"
        return _verdict_json(verdict, criteria, _reasons(criteria))


class MockJudgeLiteralist:
    """Shadow judge with the opposite bias: it punishes paraphrase.

    It demands that the answer be near verbatim from the source, so a correct
    answer in the model's own words fails. The strict voting judge tolerates
    rewording (source overlap 0.35); this one wants 0.85. On this golden set that
    produces a high false fail rate, which is the mirror image of the verbosity
    judge and the reason both are worth measuring: a panel built from two judges
    that fail in opposite directions is not the same as a panel built from two
    that fail the same way, and only the correlation numbers show the difference.
    """

    name = "mock-literalist"

    def __init__(self, model: str = MOCK_MODEL) -> None:
        self.model = model

    def complete(self, *, system: str, user: str) -> str:
        fields = parse_judge_prompt(user)
        answer, source, question = fields["answer"], fields["source"], fields["question"]
        if not answer or not source:
            return _abstain("nothing to score")
        overlap = source_overlap(answer, source)
        criteria = {
            "answers_the_question": topicality(answer, question) >= 0.40
            and not is_deferral(answer),
            "grounded_in_source": overlap >= 0.85,
            "no_invented_numbers": grounding(answer, source) >= 1.0,
            "well_formed": well_formed(answer),
        }
        verdict = "pass" if all(criteria.values()) else "fail"
        return _verdict_json(verdict, criteria, _reasons(criteria))


class MockJudgeNumbersOnly:
    """Shadow judge that grades the figures and nothing else.

    A documented judge failure mode: a rubric collapsed to its one checkable line.
    It passes any answer whose figures all appear literally in the source, and
    because an answer with no figures cannot invent one, it rubber-stamps an
    "I cannot answer that" deferral. That is what makes it distinct from the
    verbosity judge, which fails a deferral on length and topic while never
    looking at a number at all.

    It is a shadow judge, so it is measured and it does not vote.
    """

    name = "mock-numeric"

    def __init__(self, model: str = MOCK_MODEL) -> None:
        self.model = model

    def complete(self, *, system: str, user: str) -> str:
        fields = parse_judge_prompt(user)
        answer, source = fields["answer"], fields["source"]
        if not answer or not source:
            return _abstain("nothing to score")
        figures_hold = grounding(answer, source) >= 1.0
        criteria = {name: True for name in RUBRIC_CRITERIA}
        criteria["grounded_in_source"] = figures_hold
        criteria["no_invented_numbers"] = figures_hold
        verdict = "pass" if figures_hold else "fail"
        return _verdict_json(verdict, criteria, _reasons(criteria))


class MockJudgeFormatBiased:
    """Shadow judge that rewards the APPEARANCE of rigor: shape plus a figure.

    It checks presentation only. A well formed answer that states any number at
    all passes, whatever the number is, so every invented figure in sut.v2 sails
    through; an entirely correct answer that happens to quote no figure fails.
    Spurious specificity is a documented judge bias, and it is distinct from the
    other three shadow behaviors: the verbosity judge never inspects figures, the
    literalist demands near verbatim wording, and the numbers only judge checks
    whether the figures are actually in the source.

    It is a shadow judge, so it is measured and it does not vote.
    """

    name = "mock-formatter"

    def __init__(self, model: str = MOCK_MODEL) -> None:
        self.model = model

    def complete(self, *, system: str, user: str) -> str:
        fields = parse_judge_prompt(user)
        answer, source = fields["answer"], fields["source"]
        if not answer or not source:
            return _abstain("nothing to score")
        looks_rigorous = well_formed(answer) and bool(_numbers(answer))
        criteria = {name: looks_rigorous for name in RUBRIC_CRITERIA}
        # Never checks whether the figure is the RIGHT figure. That is the bias,
        # stated plainly.
        criteria["no_invented_numbers"] = True
        verdict = "pass" if looks_rigorous else "fail"
        return _verdict_json(verdict, criteria, _reasons(criteria))


#: The offline shadow bench, in slot order: four GENUINELY DISTINCT deterministic
#: behaviors, each a documented judge failure mode. The bench is a list of
#: behaviors rather than a fixed pair because the number of shadow slots is
#: configuration (SHADOW_JUDGE_MODELS), and the offline bench has to follow it:
#: an offline run that quietly benched two judges when four were configured would
#: report shadow metrics for a bench nobody asked for.
SHADOW_MOCK_BENCH: tuple[type, ...] = (
    MockJudgeVerbosityBiased,
    MockJudgeLiteralist,
    MockJudgeNumbersOnly,
    MockJudgeFormatBiased,
)

#: How many shadow slots the default SHADOW_JUDGE_MODELS configures. Named so the
#: zero argument call below is tied to the configuration default rather than to a
#: number someone once typed.
DEFAULT_SHADOW_SLOTS = 2


class ShadowBenchExhausted(ValueError):
    """Raised when more shadow slots are configured than there are behaviors.

    The alternative was to return a shorter bench, which is the bug this replaces:
    SHADOW_JUDGE_MODELS with four entries silently produced two offline shadow
    judges, so the run reported a bench of a size nobody configured. Padding with a
    repeat would be worse still, because two clones make every shadow correlation
    number an artifact of the fixture.
    """


def shadow_mock_judges(count: int = DEFAULT_SHADOW_SLOTS) -> list[LLMProvider]:
    """`count` offline shadow judges, each a DISTINCT behavior, never an alias.

    Reusing one of the voting judges here would make every shadow metric a copy
    of a number already in the report, and the cost versus accuracy table would
    compare a judge against itself at a different price. For the same reason the
    bench is never padded by repeating a behavior: asking for more slots than the
    bench holds raises ShadowBenchExhausted rather than returning a different
    number of judges than the caller asked for.
    """
    if count < 0:
        raise ShadowBenchExhausted(f"cannot build {count} shadow judges")
    if count > len(SHADOW_MOCK_BENCH):
        raise ShadowBenchExhausted(
            f"{count} shadow slots were configured but the offline shadow bench holds "
            f"{len(SHADOW_MOCK_BENCH)} distinct behaviors "
            f"({', '.join(judge.name for judge in SHADOW_MOCK_BENCH)}). Add another "
            "distinct mock judge to SHADOW_MOCK_BENCH or configure fewer models in "
            "SHADOW_JUDGE_MODELS. Repeating a behavior would make the shadow "
            "correlation numbers an artifact of the fixture."
        )
    return [judge() for judge in SHADOW_MOCK_BENCH[:count]]


#: The order in which honest judges get replaced by miscalibrated ones.
#: Balanced goes first because its verdicts look arbitrary from the outside, so
#: it is the judge a team "fixes" first. Strict goes second because it is the
#: one that blocks merges, so it is the judge a team removes under delivery
#: pressure. That ordering is what makes the one bad case survivable (strict and
#: lenient still outvote) and the two bad case fatal (only the permissive judge
#: is left).
MISCALIBRATION_ORDER: tuple[str, ...] = ("mock-balanced", "mock-strict")


def honest_mock_panel() -> list[LLMProvider]:
    """The three distinct deterministic judges, in slot order."""
    return [MockJudgeStrict(), MockJudgeLenient(), MockJudgeBalanced()]


def build_mock_panel(miscalibrated: int = 0) -> list[LLMProvider]:
    """An offline panel with `miscalibrated` honest judges swapped out.

    0 -> the honest panel. 1 -> the bad judge is outvoted. 2 -> the panel is
    captured. Slot positions are preserved so the panel is always reported in a
    stable order.
    """
    if miscalibrated < 0 or miscalibrated > 3:
        raise ValueError("miscalibrated must be between 0 and 3")
    panel = honest_mock_panel()
    replace = set(MISCALIBRATION_ORDER[:miscalibrated])
    return [MockJudgeMiscalibrated() if judge.name in replace else judge for judge in panel]


def panel_mode_to_count(mode: str) -> int:
    """Map the JUDGE_PANEL_MODE setting onto a miscalibrated judge count."""
    return {
        "honest": 0,
        "one_miscalibrated": 1,
        "two_miscalibrated": 2,
    }.get(mode, 0)


def unique_judge_names(panel: list[LLMProvider]) -> list[str]:
    """Slot qualified names, so two judges cannot collide in the metrics.

    A panel can legitimately hold two judges with the same `name`: two Anthropic
    slots, or two miscalibrated mocks. Both were silently sharing one key in the
    calibration dicts, which merged their verdict series and reported one judge
    twice. Duplicated names get a 1 based slot suffix; unique ones are left
    alone, so the common case still reads as "mock-strict".
    """
    counts: dict[str, int] = {}
    for provider in panel:
        counts[provider.name] = counts.get(provider.name, 0) + 1
    return [
        f"{provider.name}#{slot}" if counts[provider.name] > 1 else provider.name
        for slot, provider in enumerate(panel, start=1)
    ]


def is_mock(provider: LLMProvider) -> bool:
    return getattr(provider, "model", "") == MOCK_MODEL


def panel_degraded(panel: list[LLMProvider]) -> bool:
    """True when the panel mixes real and mock judges.

    A run that fell back to a mock for one slot is not a real three model panel
    and must never be reported as one. The flag rides on every PanelVerdict and
    into the audit trail rather than being printed once and forgotten.
    """
    kinds = {is_mock(provider) for provider in panel}
    return len(kinds) > 1


def describe_panel(panel: list[LLMProvider]) -> list[str]:
    return [
        f"{name} ({provider.model})"
        for name, provider in zip(unique_judge_names(panel), panel)
    ]


def slot_labels(panel: list[LLMProvider]) -> list[str]:
    """Per slot: real and which vendor, or a mock fallback. Ordered by slot.

    The real model section prints this so a reader can tell a genuine three model
    panel from one that quietly filled a slot with a deterministic mock. Naming
    the slots individually matters because `panel_degraded` collapses the whole
    question to one boolean, and a boolean cannot say WHICH slot fell back.
    """
    return [
        "mock fallback" if is_mock(provider) else f"real ({provider.name})"
        for provider in panel
    ]


def describe_panel_health(panel: list[LLMProvider]) -> str:
    """The `panel degraded` header line, in words rather than as a bare boolean.

    `panel_degraded` returns False for two completely different situations: a
    panel that is entirely real, and a panel that is entirely mock. Printing that
    boolean alone therefore told a reader with one vendor key that their panel was
    fine, and told a reader with no key the same thing, which is how a partially
    real panel gets quoted as a three model result. This line distinguishes all
    three cases and names the slots that fell back.
    """
    real = [provider for provider in panel if not is_mock(provider)]
    vendors = sorted({provider.name for provider in real})
    models = sorted({provider.model for provider in real})
    if not real:
        return (
            f"no ({len(panel)} of {len(panel)} slots are offline deterministic mocks, "
            f"so there is nothing real to degrade)"
        )
    plural = "s" if len(vendors) != 1 else ""
    if len(real) == len(panel):
        return (
            f"no ({len(panel)} of {len(panel)} slots real: {len(models)} model"
            f"{'s' if len(models) != 1 else ''}, {len(vendors)} vendor{plural} "
            f"({', '.join(vendors)}))"
        )
    fallback = [
        str(slot) for slot, provider in enumerate(panel, start=1) if is_mock(provider)
    ]
    return (
        f"YES ({len(real)} of {len(panel)} slots real ({', '.join(vendors)}); slot"
        f"{'s' if len(fallback) != 1 else ''} {', '.join(fallback)} fell back to a "
        f"deterministic mock, so this is NOT the panel it names)"
    )


# --------------------------------------------------------------------------- #
# Real providers.
# --------------------------------------------------------------------------- #


def anthropic_usage(message) -> TokenUsage | None:
    """Read Anthropic's own token accounting off a response, or None.

    Kept out of `complete` so it is reachable offline: `complete` needs a live key
    and is marked no-cover, and a token figure that reaches a cost report has to be
    testable. Returns None rather than zeros when the SDK reports no usage block,
    because a zero would be summed into a dollar total as though the call were
    free, and the caller has to be able to tell "nothing reported" from "nothing
    consumed".
    """
    usage = getattr(message, "usage", None)
    if usage is None:
        return None
    return TokenUsage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        exact=True,
    )


def openai_usage(response) -> TokenUsage | None:
    """The same, for OpenAI's prompt_tokens / completion_tokens spelling."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    return TokenUsage(
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        exact=True,
    )


class AnthropicProvider:
    """Real Anthropic path. The SDK is imported lazily, only when selected.

    `last_usage` carries what the vendor said the previous call consumed. It is an
    attribute rather than a second return value from `complete` because
    `complete` is the provider Protocol, and the whole argument this project makes
    rests on there being exactly ONE judge seam that mock and real judges both go
    through. Widening that signature for a figure only real judges have would
    fork the seam, so the token accounting rides alongside it and a reader who
    wants it uses getattr.
    """

    name = "anthropic"

    def __init__(self, api_key: str, model: str, max_tokens: int) -> None:
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "AGENT_PROVIDER=anthropic but the 'anthropic' package is not "
                "installed. Run: pip install anthropic"
            ) from exc
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.last_usage: TokenUsage | None = None

    def complete(self, *, system: str, user: str) -> str:  # pragma: no cover - needs a live key
        self.last_usage = None
        kwargs = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        # No temperature, top_p or top_k: removed on claude-opus-5 and
        # claude-sonnet-5, HTTP 400 if sent. Prefer schema constrained output;
        # fall back to instruction only on an SDK that does not accept
        # output_config. Assistant prefill is not an option here either, it also
        # returns 400 on these models.
        try:
            message = self._client.messages.create(
                **kwargs,
                output_config={"format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
            )
        except TypeError:
            message = self._client.messages.create(**kwargs)
        # Usage is read BEFORE the refusal check, because a refusal still bills
        # for the input it read. Recording it only on the success path would make
        # a run of refusals look free.
        self.last_usage = anthropic_usage(message)
        # stop_reason is checked BEFORE content. A refusal has no text block to
        # read, and for a judge it is an abstention rather than an error.
        if getattr(message, "stop_reason", None) == "refusal":
            return json.dumps(
                {"verdict": "abstain", "criteria": {}, "reasons": ["model refused"]}
            )
        return "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )

    def count_tokens(self, *, system: str, user: str) -> int:  # pragma: no cover - needs a live key
        """Exact input token count via the API, never tiktoken."""
        counted = self._client.messages.count_tokens(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return int(counted.input_tokens)


class OpenAIProvider:
    """Real OpenAI path. The SDK is imported lazily, only when selected."""

    name = "openai"

    def __init__(self, api_key: str, model: str, max_tokens: int) -> None:
        try:
            import openai  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "AGENT_PROVIDER=openai but the 'openai' package is not "
                "installed. Run: pip install openai"
            ) from exc
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.last_usage: TokenUsage | None = None

    def complete(self, *, system: str, user: str) -> str:  # pragma: no cover - needs a live key
        self.last_usage = None
        response = self._client.chat.completions.create(
            model=self.model,
            # max_completion_tokens, not max_tokens.
            max_completion_tokens=self.max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        # Same rule as the Anthropic path: a filtered completion still billed for
        # the prompt it read, so usage is taken before the outcome is inspected.
        self.last_usage = openai_usage(response)
        choice = response.choices[0]
        if getattr(choice, "finish_reason", "") == "content_filter":
            return json.dumps(
                {"verdict": "abstain", "criteria": {}, "reasons": ["content filtered"]}
            )
        return choice.message.content or ""


def get_provider(settings: Settings | None = None) -> LLMProvider:
    """Return the configured single provider.

    The iron rule: a provider name without its matching key falls back to the
    mock rather than crashing, and a key alone never selects a provider. A stray
    environment variable must not be able to send a demo run to a paid API.
    """
    settings = settings or get_settings()
    provider = settings.agent_provider
    if provider == "anthropic" and settings.anthropic_api_key:
        return AnthropicProvider(
            settings.anthropic_api_key,
            settings.model_for("anthropic"),
            settings.max_output_tokens,
        )
    if provider == "openai" and settings.openai_api_key:
        return OpenAIProvider(
            settings.openai_api_key,
            settings.model_for("openai"),
            settings.max_output_tokens,
        )
    return MockJudgeStrict()


def get_panel(settings: Settings | None = None) -> list[LLMProvider]:
    """Return the judge panel: three slots, or one when the size is 1.

    Real mode fills the slots with claude-opus-5, claude-sonnet-5 and gpt-4o.
    Offline mode fills them with three distinct deterministic mocks. The same
    iron rule applies per slot: a slot whose credential is missing falls back to
    a mock, and the run records that the panel was degraded rather than
    reporting a partially real panel as a real three model panel.

    Size 1 uses the first slot only, so the harness can put a single judge and a
    three judge panel side by side and answer whether the panel earned its cost.
    """
    settings = settings or get_settings()
    size = 3 if settings.judge_panel_size != 1 else 1
    provider = settings.agent_provider
    anthropic_key = settings.anthropic_api_key
    openai_key = settings.openai_api_key

    offline = build_mock_panel(panel_mode_to_count(settings.judge_panel_mode))

    slots: list[LLMProvider] = []
    if provider == "anthropic" and anthropic_key:
        slots.append(
            AnthropicProvider(
                anthropic_key, settings.model_for("anthropic"), settings.max_output_tokens
            )
        )
        slots.append(
            AnthropicProvider(
                anthropic_key,
                settings.agent_model or settings.anthropic_model_secondary,
                settings.max_output_tokens,
            )
        )
        # Third slot needs the OTHER vendor's key. Without it the slot falls
        # back to a mock and the panel is marked degraded.
        if openai_key:
            slots.append(
                OpenAIProvider(openai_key, settings.openai_model, settings.max_output_tokens)
            )
        else:
            slots.append(offline[2])
    elif provider == "openai" and openai_key:
        slots.append(OpenAIProvider(openai_key, settings.openai_model, settings.max_output_tokens))
        if anthropic_key:
            slots.append(
                AnthropicProvider(
                    anthropic_key,
                    settings.model_for("anthropic"),
                    settings.max_output_tokens,
                )
            )
            slots.append(
                AnthropicProvider(
                    anthropic_key,
                    settings.anthropic_model_secondary,
                    settings.max_output_tokens,
                )
            )
        else:
            slots.append(offline[1])
            slots.append(offline[2])
    else:
        slots = list(offline)

    return slots[:size]


def get_shadow_judges(settings: Settings | None = None) -> list[LLMProvider]:
    """Return the non voting shadow bench, or an empty list when disabled.

    Real mode fills the slots from SHADOW_JUDGE_MODELS (gpt-5.6-luna and gpt-4o
    by default), which needs the OpenAI credential. Offline mode fills the SAME
    NUMBER of slots with that many distinct deterministic mocks.

    The bench size is configuration, offline as well as ONLINE. A hardcoded
    pair with an `or` fallback would make SHADOW_JUDGE_MODELS="" produce two
    shadow judges and four configured models produce two as well: either way
    reporting a bench size nobody asked for. The shadow metrics that reach the
    cost versus accuracy table are only interpretable if the bench in the
    report is the bench that was configured.

    Shadow judges are returned by a SEPARATE factory from get_panel() on purpose.
    A single factory with a flag would put voting and non voting judges in one
    list, and the one property that has to hold here is that a shadow judge can
    never reach the vote. Keeping them in different lists from the moment they are
    constructed is what makes that structural rather than conventional.
    """
    settings = settings or get_settings()
    if not settings.shadow_judges:
        return []
    models = settings.shadow_models()
    if not models:
        # No models configured is a configuration that says "no shadow bench",
        # and it is honored rather than overridden with a default pair.
        return []
    if settings.openai_api_key and settings.agent_provider in ("anthropic", "openai"):
        return [
            OpenAIProvider(settings.openai_api_key, model, settings.max_output_tokens)
            for model in models
        ]
    return shadow_mock_judges(len(models))
