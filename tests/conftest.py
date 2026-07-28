"""Shared fixtures for the suite.

Everything here exists to make a claim in a test module non-vacuous, so each
fixture's docstring names the claim it serves rather than describing its own
plumbing.

Two rules bind every fixture below. First, no test may reach the network: the
autouse fixture strips the provider environment and points the settings loader at
a file that does not exist, so a developer's real `.env` or an exported
ANTHROPIC_API_KEY cannot turn a test run into a paid API run. Second, the real
vendor classes are never constructed; where a test needs a slot that is not a
mock, it gets `StubRealJudge`, which has a real looking model id and no client at
all.

Organized in sections so later fixtures can be appended without disturbing these:

    1. Offline environment
    2. Settings and shared assets
    3. Judge stand ins (subclassed mock judges)
    4. Fake vendor responses
    5. Verdict builders
    6. Executed mutations
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval_gate.config import Settings, get_settings
from eval_gate.evals.golden import GOLDEN_SET, GoldenCase
from eval_gate.llm import (
    MOCK_MODEL,
    MockJudgeStrict,
    honest_mock_panel,
    shadow_mock_judges,
)
from eval_gate.models import RUBRIC_CRITERIA, JudgeVerdict
from eval_gate.panel import aggregate
from eval_gate.prompts import PromptLibrary, render_judge_user_prompt

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Environment variables that could send a test run at a paid API.
PROVIDER_ENV_VARS = (
    "AGENT_PROVIDER",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_MODEL_SECONDARY",
    "OPENAI_MODEL",
    "AGENT_MODEL",
    "JUDGE_PANEL_MODE",
    "JUDGE_PANEL_SIZE",
    "SHADOW_JUDGES",
)


# --------------------------------------------------------------------------- #
# 1. Offline environment
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _offline_environment(monkeypatch, tmp_path: Path):
    """Force every test offline, whatever the developer's shell holds.

    Makes the provider selection claims in tests/test_llm.py mean something. Those
    tests assert that a name without a key falls back to the mock; if a real key
    leaked in from the ambient environment or from ENV_FILE, the same tests could
    pass while a live provider was being constructed behind them.
    """
    for name in PROVIDER_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "no-such-env-file"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# 2. Settings and shared assets
# --------------------------------------------------------------------------- #


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Offline settings with both keys pinned to None and isolated state paths.

    The pinned keys are the precondition for every claim in the suite: a judge
    that could reach a vendor would make the deterministic verdict assertions
    unreproducible, and the audit and registry paths point into tmp_path so no
    test can append to the committed sample log.
    """
    return Settings(
        agent_provider="mock",
        anthropic_api_key=None,
        openai_api_key=None,
        audit_log_path=str(tmp_path / "audit.log.jsonl"),
        registry_state_path=str(tmp_path / "registry.json"),
    )


@pytest.fixture
def library() -> PromptLibrary:
    """The committed prompt assets, loaded from disk.

    Judge calls in tests go through the same PromptLibrary a run uses, so the
    tolerant parser claims are made against the real rendered prompt rather than
    a hand written string that could drift away from it.
    """
    return PromptLibrary()


@pytest.fixture
def golden_set() -> tuple[GoldenCase, ...]:
    """The committed golden set, unmodified."""
    return GOLDEN_SET


@pytest.fixture
def grounded_case(golden_set: tuple[GoldenCase, ...]) -> GoldenCase:
    """A case whose baseline answer quotes the source exactly.

    Used where a test needs a real case to render a prompt from and does not care
    which one, so the parser claims are made against prompt text the judges
    actually see.
    """
    return golden_set[0]


@pytest.fixture
def honest_panel():
    """The three distinct offline voting judges, in slot order.

    Makes the disagreement claim in tests/test_llm.py non-vacuous: if this fixture
    ever returned three instances of one behavior, every split and unanimity
    number measured downstream would be an artifact of the fixture.
    """
    return honest_mock_panel()


@pytest.fixture
def shadow_bench():
    """The two offline non voting judges, from the separate shadow factory.

    Supports the structural shadow claims in tests/test_panel.py: voting and
    shadow judges are built by different factories, so a shadow judge reaching the
    vote would be a type level mistake rather than a naming slip.
    """
    return shadow_mock_judges()


# --------------------------------------------------------------------------- #
# 3. Judge stand ins (subclassed mock judges)
# --------------------------------------------------------------------------- #


class ScriptedRawJudge(MockJudgeStrict):
    """A judge that returns one canned raw string, whatever it is asked.

    Makes the tolerant parser claims non-vacuous end to end. Calling
    parse_judge_response directly proves the parser tolerates fenced or prose
    wrapped JSON; routing the same string through judge_case proves the tolerance
    survives into the JudgeVerdict the panel is handed, which is where a strict
    parser would otherwise take the run down.
    """

    name = "mock-scripted"

    def __init__(self, raw: str, model: str = MOCK_MODEL) -> None:
        super().__init__(model=model)
        self.raw = raw
        self.calls = 0

    def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        return self.raw


class StubRealJudge:
    """A slot that is not a mock, with no vendor client behind it.

    Makes the degraded panel claim testable at all. `panel_degraded` decides on
    the model id, so a real slot has to carry a real model id, and neither the
    anthropic nor the openai SDK is installed in this environment. Constructing
    the genuine provider class would raise on the lazy import, and if it did not
    it would build a live client. This carries the vendor's name and model and
    refuses to be called.
    """

    def __init__(self, api_key: str, model: str, max_tokens: int, name: str = "stub-real") -> None:
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.name = name

    def complete(self, *, system: str, user: str) -> str:
        raise AssertionError("a test tried to call a real provider")


@pytest.fixture
def scripted_judge():
    """Factory for a ScriptedRawJudge returning the raw text a test hands it."""
    def build(raw: str) -> ScriptedRawJudge:
        return ScriptedRawJudge(raw)

    return build


@pytest.fixture
def stub_real_providers(monkeypatch):
    """Replace both real provider classes with StubRealJudge.

    Makes test_a_partially_real_panel_is_recorded_as_degraded executable offline:
    get_panel has to build two real slots and fall back to a mock for the third,
    and that mixture cannot be observed without something standing in for the
    vendor classes.
    """
    def anthropic_factory(api_key: str, model: str, max_tokens: int) -> StubRealJudge:
        return StubRealJudge(api_key, model, max_tokens, name="anthropic")

    def openai_factory(api_key: str, model: str, max_tokens: int) -> StubRealJudge:
        return StubRealJudge(api_key, model, max_tokens, name="openai")

    monkeypatch.setattr("eval_gate.llm.AnthropicProvider", anthropic_factory)
    monkeypatch.setattr("eval_gate.llm.OpenAIProvider", openai_factory)


# --------------------------------------------------------------------------- #
# 4. Fake vendor responses
# --------------------------------------------------------------------------- #


class FakeAnthropicMessage:
    """One Anthropic response object, with a settable stop_reason.

    Makes test_a_refusal_stop_reason_becomes_an_abstention non-vacuous without a
    key. `content` is deliberately empty on a refusal, because that is the shape
    the API returns and reading content[0] unconditionally is the crash the
    stop_reason check exists to prevent.
    """

    def __init__(self, stop_reason: str, text: str = "") -> None:
        self.stop_reason = stop_reason
        self.content = [] if stop_reason == "refusal" else [FakeTextBlock(text)]


class FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class FakeAnthropicClient:
    """Just enough client to answer one messages.create call."""

    def __init__(self, message: FakeAnthropicMessage) -> None:
        self.messages = self
        self.message = message
        self.calls: list[dict] = []

    def create(self, **kwargs) -> FakeAnthropicMessage:
        self.calls.append(kwargs)
        return self.message


class FakeOpenAIChoice:
    def __init__(self, finish_reason: str, content: str | None) -> None:
        self.finish_reason = finish_reason
        self.message = type("FakeMessage", (), {"content": content})()


class FakeOpenAIClient:
    """Just enough client to answer one chat.completions.create call.

    Makes the content filter half of the abstention claim non-vacuous: OpenAI
    signals a refusal as finish_reason="content_filter" with no usable message,
    which is the same fact as an Anthropic refusal in a different spelling.
    """

    def __init__(self, choice: FakeOpenAIChoice) -> None:
        self.chat = self
        self.completions = self
        self.choice = choice

    def create(self, **kwargs):
        return type("FakeResponse", (), {"choices": [self.choice]})()


@pytest.fixture
def refusing_anthropic_judge():
    """An AnthropicProvider whose next response is a refusal.

    Built with __new__ because __init__ imports the anthropic SDK and constructs a
    live client, neither of which belongs in a test. The method under test only
    touches _client, model and max_tokens.
    """
    from eval_gate.llm import AnthropicProvider

    judge = AnthropicProvider.__new__(AnthropicProvider)
    judge.model = "claude-opus-5"
    judge.max_tokens = 256
    judge._client = FakeAnthropicClient(FakeAnthropicMessage("refusal"))
    return judge


@pytest.fixture
def content_filtered_openai_judge():
    """An OpenAIProvider whose next response was filtered rather than answered."""
    from eval_gate.llm import OpenAIProvider

    judge = OpenAIProvider.__new__(OpenAIProvider)
    judge.model = "gpt-5.6-terra"
    judge.max_tokens = 256
    judge._client = FakeOpenAIClient(FakeOpenAIChoice("content_filter", None))
    return judge


# --------------------------------------------------------------------------- #
# 5. Verdict builders
# --------------------------------------------------------------------------- #


@pytest.fixture
def make_verdict():
    """Build one JudgeVerdict with only the fields a vote depends on.

    Panel aggregation is tested on constructed verdicts rather than on judge
    output so the arithmetic of the vote is asserted directly. A test that had to
    coax three mocks into a tie would be asserting the mocks as much as the rule.
    """
    def build(
        verdict: str,
        *,
        judge_name: str = "judge",
        case_id: str = "gc-001",
        sut_version: str = "sut.v1",
        raw_ok: bool = True,
        shadow: bool = False,
        repeat: int = 1,
    ) -> JudgeVerdict:
        return JudgeVerdict(
            case_id=case_id,
            sut_version=sut_version,
            judge_name=judge_name,
            judge_model=MOCK_MODEL,
            verdict=verdict,
            criteria={name: verdict == "pass" for name in RUBRIC_CRITERIA},
            raw_ok=raw_ok,
            shadow=shadow,
            repeat=repeat,
        )

    return build


@pytest.fixture
def tied_verdicts(make_verdict) -> list[JudgeVerdict]:
    """One pass, one fail, one abstention: a genuine 1-1 among the voters.

    This is the shape the ambiguous cases in the golden set produce, and it is the
    shape a gate is most tempted to resolve for itself.
    """
    return [
        make_verdict("pass", judge_name="mock-lenient"),
        make_verdict("fail", judge_name="mock-strict"),
        make_verdict("abstain", judge_name="mock-balanced", raw_ok=False),
    ]


@pytest.fixture
def scripted_verdict_json():
    """A well formed judge response, for tests that need valid JSON to wrap."""
    def build(verdict: str = "pass", reasons: list[str] | None = None) -> str:
        return json.dumps(
            {
                "verdict": verdict,
                "criteria": {name: verdict == "pass" for name in RUBRIC_CRITERIA},
                "reasons": reasons or [],
            }
        )

    return build


@pytest.fixture
def rendered_judge_prompt(grounded_case: GoldenCase) -> str:
    """The exact user prompt a judge is handed for one baseline case."""
    return render_judge_user_prompt(
        case_id=grounded_case.case_id,
        sut_version="sut.v1",
        question=grounded_case.question,
        source=grounded_case.source,
        answer=grounded_case.answer("sut.v1"),
        attempt=1,
    )


# --------------------------------------------------------------------------- #
# 6. Executed mutations
#
# Each of these is a deliberately broken version of a rule, so a test can run the
# mutation and watch its own premise collapse instead of describing the mutation
# in prose and hoping.
# --------------------------------------------------------------------------- #


@pytest.fixture
def aggregate_with_ties_resolved_toward_pass():
    """Panel aggregation with the tie rule mutated to resolve toward pass.

    Serves test_a_tie_abstains_and_flags_the_case_for_human_escalation. A tie
    silently resolved toward pass is how a gate ships a regression while looking
    decisive, so the test runs this mutant and asserts that the escalation premise
    it just checked no longer holds.
    """
    def mutated(verdicts: list[JudgeVerdict], *, degraded: bool = False):
        result = aggregate(verdicts, degraded=degraded)
        voting = [item.verdict for item in verdicts if item.verdict != "abstain"]
        tied = len(voting) >= 2 and voting.count("pass") == voting.count("fail")
        if tied:
            return result.model_copy(update={"verdict": "pass", "escalated": False})
        return result

    return mutated


# --------------------------------------------------------------------------- #
# APPEND LATER FIXTURES BELOW THIS LINE
#
# Sections 1 to 6 above are the stable half of the suite (provider selection, the
# tolerant parser, panel aggregation, cost). Fixtures for calibration, the gate,
# the registry and determinism go after this marker so the two halves stay
# reviewable as separate diffs.
# --------------------------------------------------------------------------- #

# The file is append only below the marker, so the imports the later sections
# need are declared here rather than being added to the header block above.
import dataclasses  # noqa: E402
import shutil  # noqa: E402

from eval_gate.audit import AuditLog  # noqa: E402
from eval_gate.baseline import BaselineRecord  # noqa: E402
from eval_gate.calibration import ConsistencyReport, DiscriminationReport  # noqa: E402
from eval_gate.evals import gate as gate_module  # noqa: E402
from eval_gate.evals.runner import build_report, run_panel  # noqa: E402
from eval_gate.llm import build_mock_panel, panel_mode_to_count  # noqa: E402
from eval_gate.models import AuditRecord  # noqa: E402


# --------------------------------------------------------------------------- #
# 7. The gate, invoked in process
#
# The gate is exercised through its real entry point rather than through a
# reimplementation of it, because the property under test is what CI observes:
# the exit code, and the decision that produced it. A test that rebuilt the
# gate's own sequence of calls would be asserting the test's copy of the gate.
# --------------------------------------------------------------------------- #


@dataclasses.dataclass
class GateRun:
    """One invocation of `gate.main`, with everything it decided kept.

    `exit_code` is the field the CI claim rests on. The decision, the report and
    the RunResult are captured on the way past so a test can assert the printed
    text and the objects behind it are the same fact.
    """

    exit_code: int
    decision: object
    report: object
    result: object
    output: str
    audit_paths: list[Path]
    baseline_path: Path
    registry_path: Path

    def outcome(self) -> tuple:
        """Every gate outcome that must not depend on the shadow judges.

        Used by the shadow toggle test. The run id is included deliberately: it is
        derived from the VOTING panel and the prompt manifest, so a shadow judge
        leaking into the seed would show up here too.
        """
        decision = self.decision
        return (
            self.exit_code,
            self.report.run_id,
            decision.candidate_version,
            decision.regression_detected,
            decision.deployment_decision,
            decision.panel_healthy,
            round(decision.baseline_pass_rate, 9),
            round(decision.candidate_pass_rate, 9),
            round(decision.drop_vs_baseline, 9),
            round(decision.threshold, 9),
            round(decision.noise_floor, 9),
            decision.refused,
            decision.baseline_incomparable,
            tuple(decision.baseline_differing_fields),
            tuple(decision.panel_failures),
            tuple(decision.failures),
            decision.exit_driver,
        )

    def voting_numbers(self) -> dict:
        """The report numbers the gate reads, with nothing shadow in them."""
        report = self.report
        return {
            "judge_kappa": {judge.name: round(judge.kappa, 9) for judge in report.judges},
            "judge_false_pass": {
                judge.name: round(judge.false_pass_rate, 9) for judge in report.judges
            },
            "panel_kappa": round(report.panel.kappa, 9),
            "panel_pass_rate": {
                name: round(value, 9) for name, value in sorted(report.panel.pass_rate.items())
            },
            "panel_flip_rate": round(report.consistency.panel_flip_rate, 9),
            "per_judge_flip_rate": {
                name: round(value, 9)
                for name, value in sorted(report.consistency.per_judge_flip_rate.items())
            },
            "discrimination": dataclasses.asdict(report.discrimination),
            "correlations": [dataclasses.asdict(pair) for pair in report.correlations],
        }


@pytest.fixture
def run_gate(monkeypatch, capsys, tmp_path):
    """Run `gate.main(argv)` offline, against the COMMITTED baseline record.

    Makes every headline gate claim assertable on the exit code rather than on
    printed text alone. The baseline defaults to the committed baseline.json,
    because the four verified behaviors are statements about that file; the
    registry state path is redirected into tmp_path so a test run cannot move the
    active prompt pointer of the working tree.

    Every AuditLog the gate opens is recorded, which is what lets a test assert
    that none of them was inside the repository.
    """

    def run(*argv: str, baseline: Path | str | None = None, registry: Path | str | None = None):
        baseline_path = Path(baseline) if baseline else REPO_ROOT / "baseline.json"
        registry_path = Path(registry) if registry else tmp_path / "gate-registry.json"
        monkeypatch.setenv("BASELINE_PATH", str(baseline_path))
        monkeypatch.setenv("REGISTRY_STATE_PATH", str(registry_path))
        get_settings.cache_clear()

        captured: dict = {}
        real_run_panel = gate_module.run_panel
        real_evaluate = gate_module.evaluate_gate
        recorded: list[Path] = []
        real_audit_init = AuditLog.__init__

        def spy_run_panel(*args, **kwargs):
            captured["result"] = real_run_panel(*args, **kwargs)
            return captured["result"]

        def spy_evaluate(report, settings=None, **kwargs):
            captured["report"] = report
            captured["decision"] = real_evaluate(report, settings, **kwargs)
            return captured["decision"]

        def spy_audit_init(self, path):
            recorded.append(Path(path))
            real_audit_init(self, path)

        monkeypatch.setattr(gate_module, "run_panel", spy_run_panel)
        monkeypatch.setattr(gate_module, "evaluate_gate", spy_evaluate)
        monkeypatch.setattr(AuditLog, "__init__", spy_audit_init)
        exit_code = gate_module.main(list(argv))
        monkeypatch.setattr(AuditLog, "__init__", real_audit_init)
        return GateRun(
            exit_code=exit_code,
            decision=captured["decision"],
            report=captured["report"],
            result=captured["result"],
            output=capsys.readouterr().out,
            audit_paths=recorded,
            baseline_path=baseline_path,
            registry_path=registry_path,
        )

    return run


@pytest.fixture
def gate_blind_to_its_own_noise_floor(monkeypatch):
    """MUTATION: the noise floor guard, bypassed by silencing its input.

    Serves test_a_threshold_inside_the_measured_noise_floor_is_refused. The guard
    is `threshold < noise_floor`, so a gate told its noise floor is zero cannot
    refuse anything. The per judge and panel flip rates are still measured and
    still reported, which is what makes the mutant's failure legible: it accepts a
    0.050 threshold while printing a 0.100 panel flip rate on the same page.
    """

    def apply() -> None:
        monkeypatch.setattr(
            ConsistencyReport, "noise_floor", property(lambda self: 0.0), raising=True
        )

    return apply


@pytest.fixture
def gate_blind_to_baseline_comparability(monkeypatch):
    """MUTATION: the comparability guard, bypassed by silencing its input.

    Serves test_a_baseline_from_another_panel_produces_no_deployment_decision. The
    guard is `if decision.baseline_incomparable`, which is derived from what the
    baseline record reports about itself, so a record that reports no differences
    disarms it exactly as a deleted `if` would. Patched at the single private method
    both accessors read, so the prose warnings and the field names cannot disagree
    about whether the mutation is in force.

    Under this mutation the gate compares a captured panel's 1.000 against an honest
    panel's recorded 0.800 and prints ALLOW, which is the behavior the refusal was
    added to remove.
    """

    def apply() -> None:
        monkeypatch.setattr(
            BaselineRecord, "_differences", lambda self, **kwargs: [], raising=True
        )

    return apply


@pytest.fixture
def baseline_copy(tmp_path) -> Path:
    """A writable copy of the committed baseline record.

    --record-baseline rewrites the file it is pointed at. Every test that
    exercises recording points at this copy, so the committed record stays the
    fixture the four headline behaviors were verified against.
    """
    destination = tmp_path / "baseline.json"
    shutil.copyfile(REPO_ROOT / "baseline.json", destination)
    return destination


# --------------------------------------------------------------------------- #
# 8. Offline runs and calibration reports
# --------------------------------------------------------------------------- #


@pytest.fixture
def offline_run(tmp_path):
    """Factory: one complete offline run and its calibration report.

    Every calibration and determinism claim that needs real numbers rather than
    hand computed ones goes through here, so those tests measure the same panel
    the gate measures instead of a second, divergent construction of it. A full
    run is 900 mock judge calls and about 30 ms, so no test has to share one.
    """

    def build(panel_mode: str = "honest", *, shadow: bool = True, repeats: int = 3):
        settings = Settings(
            agent_provider="mock",
            anthropic_api_key=None,
            openai_api_key=None,
            judge_panel_mode=panel_mode,
            judge_repeats=repeats,
            shadow_judges=shadow,
            audit_log_path=str(tmp_path / f"{panel_mode}.audit.jsonl"),
            registry_state_path=str(tmp_path / f"{panel_mode}.registry.json"),
        )
        panel = build_mock_panel(panel_mode_to_count(panel_mode))
        bench = shadow_mock_judges() if shadow else []
        result = run_panel(settings, panel=panel, shadow=bench)
        return result, build_report(result)

    return build


# --------------------------------------------------------------------------- #
# 9. Hand computed calibration fixtures
#
# The arithmetic is asserted against numbers worked out by hand and written into
# the fixture docstrings. Asserting against the implementation's own output would
# only pin that it has not changed, not that it is right.
# --------------------------------------------------------------------------- #


@pytest.fixture
def chance_level_pairs() -> list[tuple[str, str]]:
    """A judge that agrees exactly as often as chance predicts: kappa 0.

    Four units, two pass and two fail on each side, agreeing on two of them.
    po = 0.5. pe = (0.5 x 0.5) + (0.5 x 0.5) = 0.5. kappa = (0.5 - 0.5)/0.5 = 0,
    while raw agreement reads a respectable 0.500.
    """
    return [("pass", "pass"), ("pass", "fail"), ("fail", "pass"), ("fail", "fail")]


@pytest.fixture
def perfect_pairs() -> list[tuple[str, str]]:
    """A judge that agrees on every unit of a mixed label set: kappa 1.

    po = 1.0, so kappa = (1 - pe)/(1 - pe) = 1 for any pe below 1. The labels are
    mixed on purpose: a perfect judge on a single class set is the degenerate case
    cohen_kappa documents separately, not this one.
    """
    return [
        ("pass", "pass"),
        ("pass", "pass"),
        ("fail", "fail"),
        ("pass", "pass"),
        ("fail", "fail"),
    ]


@pytest.fixture
def skewed_human_labels() -> dict[tuple[str, str], str]:
    """The real sut.v1 human labels: 24 pass, 6 fail.

    Taken from the committed golden set rather than invented, so the constant-pass
    argument in tests/test_calibration.py is made against the label distribution
    the project actually ships. tests/test_determinism.py guards the distribution
    itself.
    """
    return {("sut.v1", case.case_id): case.label("sut.v1") for case in GOLDEN_SET}


@pytest.fixture
def constant_pass_verdicts(skewed_human_labels) -> dict[tuple[str, str], str]:
    """The judge that answers "pass" to everything: 0.800 agreement, 0.000 kappa."""
    return {unit: "pass" for unit in skewed_human_labels}


@pytest.fixture
def opposite_error_profiles() -> tuple[dict, dict, dict]:
    """Two judges with the SAME raw agreement and mirror image error profiles.

    Ten units, eight labeled pass and two labeled fail.
      the lenient judge passes everything : 0.800 agreement, false_pass 2/2 = 1.000,
                                            false_fail 0/8 = 0.000
      the harsh judge fails four          : 0.800 agreement, false_pass 0/2 = 0.000,
                                            false_fail 2/8 = 0.250
    One accuracy number cannot tell these apart, and they are not the same risk:
    one ships a regression, the other blocks a good change.
    """
    labels = {("sut.v1", f"gc-{index:03d}"): ("fail" if index > 8 else "pass") for index in range(1, 11)}
    units = sorted(labels)
    lenient = {unit: "pass" for unit in units}
    harsh = dict(lenient)
    harsh[units[0]] = "fail"  # a genuine pass, blocked
    harsh[units[1]] = "fail"  # a genuine pass, blocked
    harsh[units[8]] = "fail"  # a genuine fail, correctly caught
    harsh[units[9]] = "fail"  # a genuine fail, correctly caught
    return labels, lenient, harsh


@pytest.fixture
def rater_health_rules(settings):
    """MUTATION: the panel health rule, reading raw agreement instead of kappa.

    Serves test_raw_agreement_flatters_a_judge_that_always_answers_pass. The gate
    admits a judge when `judge.kappa >= gate_min_judge_kappa`; the mutant reads
    `judge.raw_agreement` against the same floor. On the skewed golden set that
    single substitution admits a judge incapable of failing anything.
    """
    floor = settings.gate_min_judge_kappa

    def honest(rater) -> bool:
        return rater.kappa >= floor

    def reading_raw_agreement(rater) -> bool:
        return rater.raw_agreement >= floor

    return honest, reading_raw_agreement


@pytest.fixture
def deterministic_and_mind_changing_series() -> tuple[dict, dict]:
    """Two repeat series: one judge that never flips, one that flips on 1 of 4 units.

    Flip rate is the fraction of units whose repeats are not all the same verdict,
    so the second series reads 0.250 by hand.
    """
    units = [("sut.v1", f"gc-{index:03d}") for index in range(1, 5)]
    deterministic = {unit: ["pass", "pass", "pass"] for unit in units}
    changes_its_mind = dict(deterministic)
    changes_its_mind[units[2]] = ["pass", "fail", "pass"]
    return deterministic, changes_its_mind


@pytest.fixture
def shared_and_independent_biases() -> tuple[dict, dict, dict]:
    """Twenty units, and three judges: two sharing a bias, one unrelated.

    Labels are "pass" on every unit, so an error is simply a "fail".
      biased_a  wrong on units 1..4
      biased_b  wrong on units 1..4          -> joint 0.200, independence predicts
                                                0.040, ratio 5.00
    And a second, unrelated pair: one judge wrong on units 1..10, one wrong on the
    sliding window 7..14. They overlap on 4 units, so the joint rate is 4/20 = 0.200
    and independence predicts 0.500 x 0.400 = 0.200, ratio 1.00.
    Majority voting only buys accuracy when the errors are independent, so the
    ratio has to be able to say both things.
    """
    units = [("sut.v1", f"gc-{index:03d}") for index in range(1, 21)]
    labels = {unit: "pass" for unit in units}
    shared = {
        "biased-a": {unit: ("fail" if index < 4 else "pass") for index, unit in enumerate(units)},
        "biased-b": {unit: ("fail" if index < 4 else "pass") for index, unit in enumerate(units)},
    }
    independent = {
        "wrong-on-the-first-half": {
            unit: ("fail" if index < 10 else "pass") for index, unit in enumerate(units)
        },
        "wrong-on-a-sliding-window": {
            unit: ("fail" if 6 <= index < 14 else "pass") for index, unit in enumerate(units)
        },
    }
    return labels, shared, independent


@pytest.fixture
def suspicion_reading_unanimity_only():
    """MUTATION: the suspicion heuristic as it was originally written.

    Serves test_the_suspicion_line_names_which_vacuous_gate_signal_fired. The
    original keyed on unanimity alone. The captured panel measures 0.733 unanimity
    against the honest panel's 0.867, so the original stayed silent in the one
    configuration where the line had to say something, and a reassuring suspicion
    line during total capture is worse than no line at all.
    """

    def suspicion(discrimination: DiscriminationReport) -> str:
        if discrimination.unanimity_rate >= 0.90:
            return (
                f"SUSPICIOUS: unanimity_rate {discrimination.unanimity_rate:.3f} near "
                f"total: the judges almost never disagree"
            )
        return "no vacuous gate signal fired"

    return suspicion


# --------------------------------------------------------------------------- #
# 10. Registry and audit fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def prompt_dir(tmp_path) -> Path:
    """A writable copy of the committed prompt assets.

    Editing a prompt has to be done on disk, because a version is identified by
    the sha256 of the file body. Copying the real assets rather than writing
    synthetic ones keeps the hash claims about the prompts the project ships.
    """
    destination = tmp_path / "prompts"
    shutil.copytree(REPO_ROOT / "eval_gate" / "prompts", destination)
    return destination


@pytest.fixture
def audit_log(tmp_path) -> AuditLog:
    """An empty hash chained log in tmp_path, never the committed one."""
    return AuditLog(tmp_path / "chain.audit.jsonl")


@pytest.fixture
def hashing_without_prev_hash(monkeypatch):
    """MUTATION: prev_hash dropped from the hashed payload.

    Serves test_the_audit_chain_detects_a_tampered_record. With prev_hash inside
    the payload, every record's hash covers its predecessor, so removing a record
    and relinking the survivors breaks a hash. Drop it and each record still
    verifies against its own content while the excision goes undetected, which is
    the difference between a chain and a list of independently hashed lines.
    """

    def apply() -> None:
        def payload_for_hash(self) -> dict:
            return {
                "event": self.event,
                "run_id": self.run_id,
                "timestamp": self.timestamp,
                "payload": self.payload,
            }

        monkeypatch.setattr(AuditRecord, "payload_for_hash", payload_for_hash)

    return apply


@pytest.fixture
def three_chained_records():
    """Append three linked records to a log and hand back what was written."""

    def build(log: AuditLog) -> list[AuditRecord]:
        return [
            log.append("run_started", "run-000000000001", {"cases": 30}),
            log.append("case_scored", "run-000000000001", {"case_id": "gc-001", "verdict": "pass"}),
            log.append("gate_decision", "run-000000000001", {"exit_code": 0}),
        ]

    return build


# --------------------------------------------------------------------------- #
# 11. The real model pass, exercised without a network
#
# The paid pass is the one path in this project that cannot be verified by
# running it, because running it spends the user's money against a live vendor.
# Everything below stands in for that vendor: a judge that looks real, answers
# with canned JSON, reports usage the way a vendor does, and can be told to raise.
# --------------------------------------------------------------------------- #


import importlib.util  # noqa: E402

from eval_gate.llm import (  # noqa: E402
    MockJudgeBalanced,
    MockJudgeLenient,
    MockJudgeLiteralist,
    MockJudgeVerbosityBiased,
)
from eval_gate.models import TokenUsage  # noqa: E402
from eval_gate.real_pass import prompt_context  # noqa: E402


class CannedRealJudge:
    """A judge that looks real to every caller and never reaches a network.

    Makes every claim in tests/test_real_pass.py non-vacuous. The real pass keys
    almost everything off two facts about a judge: that its model id is not
    MOCK_MODEL, so it counts as a real slot, and that it exposes `last_usage`, so
    its tokens are measured rather than approximated. Both are true here and
    neither costs anything.

    `raise_on` is the important half. A live judge that times out on one case is
    the failure this pass is built to survive, and no live run can be relied on to
    produce one on demand, so it is produced here instead.

    `usage` may be None, which stands in for an SDK that returned no usage block:
    the pass then falls back to the characters/4 approximation and must say so.
    """

    def __init__(
        self,
        name: str,
        model: str,
        behavior,
        *,
        raise_on: tuple[str, ...] = (),
        reports_usage: bool = True,
    ) -> None:
        self._behavior = behavior
        self.name = name
        self.model = model
        self.raise_on = set(raise_on)
        self.reports_usage = reports_usage
        self.last_usage: TokenUsage | None = None
        #: (case_id, sut_version, attempt) for every call, in call order, so a test
        #: can assert what was bought rather than what was reported as bought.
        self.seen: list[tuple[str, str, int]] = []
        #: What this stand in claimed to have consumed, summed. A test compares the
        #: cost report against these rather than against the formula below, so the
        #: claim is "the report carries what the vendor said" and not "the report
        #: agrees with a second copy of the arithmetic".
        self.reported_input = 0
        self.reported_output = 0

    def complete(self, *, system: str, user: str) -> str:
        case_id, version, attempt = prompt_context(user)
        self.seen.append((case_id, version, attempt))
        if case_id in self.raise_on:
            self.last_usage = None
            raise RuntimeError("connection reset by peer")
        self.last_usage = (
            TokenUsage(
                # Deliberately NOT characters/4: the pass reports how far the
                # offline approximation was off, and a stand in that agreed with it
                # exactly would make that comparison vacuous.
                input_tokens=int(len(system + user) / 3.4),
                output_tokens=57 + attempt,
                exact=True,
            )
            if self.reports_usage
            else None
        )
        if self.last_usage is not None:
            self.reported_input += self.last_usage.input_tokens
            self.reported_output += self.last_usage.output_tokens
        return self._behavior.complete(system=system, user=user)


@pytest.fixture
def canned_real_judge():
    """Factory for one CannedRealJudge."""

    def build(
        name: str,
        model: str,
        behavior=None,
        *,
        raise_on: tuple[str, ...] = (),
        reports_usage: bool = True,
    ) -> CannedRealJudge:
        return CannedRealJudge(
            name,
            model,
            behavior or MockJudgeStrict(),
            raise_on=raise_on,
            reports_usage=reports_usage,
        )

    return build


@pytest.fixture
def fake_real_panel(canned_real_judge):
    """A three model, two vendor voting panel and a two model shadow bench.

    The model ids are the ones config.py actually defaults to, so the dollar
    figures the tests assert are priced against the same PRICES rows a real run
    would use. The behaviors behind them are the three distinct offline judges, so
    the panel disagrees for the same reasons the offline panel does and the
    calibration numbers are not an artifact of five clones.
    """

    def build(*, raise_on: tuple[str, ...] = (), reports_usage: bool = True):
        panel = [
            canned_real_judge(
                "anthropic", "claude-opus-5", MockJudgeStrict(), reports_usage=reports_usage
            ),
            canned_real_judge(
                "anthropic",
                "claude-sonnet-5",
                MockJudgeLenient(),
                reports_usage=reports_usage,
            ),
            canned_real_judge(
                "openai",
                "gpt-5.6-terra",
                MockJudgeBalanced(),
                raise_on=raise_on,
                reports_usage=reports_usage,
            ),
        ]
        shadow = [
            canned_real_judge(
                "openai",
                "gpt-5.6-luna",
                MockJudgeVerbosityBiased(),
                reports_usage=reports_usage,
            ),
            canned_real_judge(
                "openai", "gpt-4o", MockJudgeLiteralist(), reports_usage=reports_usage
            ),
        ]
        return panel, shadow

    return build


@pytest.fixture
def real_settings(tmp_path) -> Settings:
    """Settings that name a real provider and carry keys that go nowhere.

    The keys are non-empty because provider selection requires both halves, and
    they are never used: every test that takes these settings also supplies its own
    panel, so no provider class is ever constructed from them.
    """
    return Settings(
        agent_provider="anthropic",
        anthropic_api_key="not-a-real-key",
        openai_api_key="not-a-real-key",
        audit_log_path=str(tmp_path / "real.audit.jsonl"),
        registry_state_path=str(tmp_path / "real.registry.json"),
    )


@pytest.fixture
def demo_run(monkeypatch, capsys, tmp_path):
    """Run scripts/run_demo.py in process, with every write redirected out of the repo.

    The demo is the only place the real model section is wired to anything, so the
    claim that it cannot move the exit code has to be asserted on the demo's own
    return value rather than on a reconstruction of it. The three log paths and the
    two miscalibrated baseline records are absolute so the run does not depend on
    the working directory, and so a test run cannot append to the committed audit
    directory or move the working tree's active prompt pointer.
    """

    def run(*argv: str, panel=None, shadow=None, provider: str = "mock"):
        spec = importlib.util.spec_from_file_location(
            "run_demo_under_test", REPO_ROOT / "scripts" / "run_demo.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        monkeypatch.setenv("BASELINE_PATH", str(REPO_ROOT / "baseline.json"))
        monkeypatch.setenv("REGISTRY_STATE_PATH", str(tmp_path / "settings-registry.json"))
        if provider != "mock":
            monkeypatch.setenv("AGENT_PROVIDER", provider)
            monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
            monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
        get_settings.cache_clear()

        monkeypatch.setattr(module, "DEMO_LOG", str(tmp_path / "demo.audit.jsonl"))
        monkeypatch.setattr(module, "DEMO_ROLLBACK_LOG", str(tmp_path / "demo.rollback.jsonl"))
        monkeypatch.setattr(module, "DEMO_REGISTRY", str(tmp_path / "demo.registry.json"))
        monkeypatch.setattr(
            module,
            "PANEL_BASELINES",
            {
                "one_miscalibrated": str(REPO_ROOT / "baseline.one_miscalibrated.json"),
                "two_miscalibrated": str(REPO_ROOT / "baseline.two_miscalibrated.json"),
            },
        )
        if panel is not None:
            monkeypatch.setattr(module, "get_panel", lambda settings: list(panel))
        if shadow is not None:
            monkeypatch.setattr(module, "get_shadow_judges", lambda settings: list(shadow))

        exit_code = module.main(list(argv))
        return exit_code, capsys.readouterr().out

    return run
