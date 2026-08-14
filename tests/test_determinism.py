"""Reproducibility, and the one fixture every calibration claim rests on.

A regression gate whose verdict depends on the model of the day is a flaky test
with a governance story attached, so the offline path has no randomness in it at
all: sha256 in place of an RNG, sorted() on every dict and directory walk, and the
system under test's answers committed as fixtures rather than generated at eval
time. Two runs of the same panel over the same prompts therefore produce byte
identical verdicts and byte identical calibration numbers, and the run id is
derived from what the run IS rather than from a clock.

The documented boundary is timestamps. They are excluded from the determinism claim
rather than frozen, because a faked timestamp in an audit trail is worse than an
honest one that varies.

The last test in this module guards a fixture rather than a function.
test_the_golden_set_label_distribution_is_deliberately_skewed exists because the
whole argument for reporting Cohen's kappa depends on 24 of 30 baseline cases being
labeled pass. If someone "balances" the golden set to 50/50, raw agreement stops
flattering a constant-pass judge, every calibration claim quietly stops
demonstrating its point, and nothing else in the suite would notice.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from eval_gate.audit import AuditLog
from eval_gate.baseline import load_baseline
from eval_gate.calibration import cohen_kappa, raw_agreement
from eval_gate.config import Settings
from eval_gate.evals.golden import GOLDEN_SET, label_distribution
from eval_gate.evals.runner import build_report, run_panel
from eval_gate.llm import build_mock_panel, panel_mode_to_count, shadow_mock_judges

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_two_offline_runs_produce_identical_verdicts(offline_run):
    """Same panel, same prompts, same verdicts, down to the repeat index.

    Asserted on the whole RunResult and then on the pieces, so a failure says which
    part drifted: the per judge series, the aggregated panel series, or the run id
    the audit trail and the committed baseline record are joined on.
    """
    first_result, _first_report = offline_run("honest")
    second_result, _second_report = offline_run("honest")

    assert first_result.run_id == second_result.run_id
    assert first_result == second_result

    assert [verdict.model_dump() for verdict in first_result.judge_verdicts] == [
        verdict.model_dump() for verdict in second_result.judge_verdicts
    ]
    assert [verdict.model_dump() for verdict in first_result.panel_verdicts] == [
        verdict.model_dump() for verdict in second_result.panel_verdicts
    ]
    assert first_result.judge_series == second_result.judge_series
    assert first_result.shadow_series == second_result.shadow_series
    assert len(first_result.judge_verdicts) == 540, "3 judges x 30 cases x 2 versions x 3 repeats"


def test_two_offline_runs_produce_identical_calibration_numbers(offline_run):
    """Every reported number, not just the verdicts they were derived from.

    The report is compared as a whole, which covers kappa, the confusion matrices,
    the flip rates, the pairwise correlations and the vacuous gate counts in one
    assertion. Nothing in a CalibrationReport is timestamped, which is why this can
    be an equality rather than a subset.
    """
    _first_result, first = offline_run("honest")
    _second_result, second = offline_run("honest")

    assert dataclasses.asdict(first) == dataclasses.asdict(second)
    assert first.consistency.noise_floor == second.consistency.noise_floor == 6 / 60
    assert round(first.panel.kappa, 3) == 0.925
    assert first.panel.pass_rate == second.panel.pass_rate


def test_the_run_id_is_derived_from_the_run_rather_than_from_a_clock(offline_run):
    """A run id from a clock makes two identical runs look like two different facts.

    The seed is the prompt manifest hash, the repeat count, and each judge's slot
    name and model, so the committed baseline record's run id is reproducible from a
    clean checkout. That is what lets the registry binding, the audit trail and
    baseline.json be joined on it at all.
    """
    _result, report = offline_run("honest")
    committed = load_baseline(REPO_ROOT / "baseline.json")

    assert report.run_id == committed.run_id
    assert report.prompt_manifest_hash == committed.prompt_version_hash
    assert report.repeats == committed.repeats
    assert report.panel_mode == committed.panel_mode
    assert round(report.panel.pass_rate["sut.v1"], 6) == committed.measured_pass_rate

    # A different panel is a different run, so the id has to move.
    _captured_result, captured = offline_run("two_miscalibrated")
    assert captured.run_id != report.run_id


def test_two_offline_runs_produce_the_same_audit_trail_apart_from_its_timestamps(tmp_path):
    """The determinism claim's stated boundary, asserted rather than described.

    Everything in the trail is reproducible except `timestamp`, and consequently
    `prev_hash` and `record_hash`, which cover it. Freezing the clock would make the
    hashes match and make the log lie about when a decision was made, so the
    timestamps vary and the boundary is documented in audit.py and pinned here.
    """
    def trail(name: str) -> list:
        settings = Settings(
            agent_provider="mock",
            anthropic_api_key=None,
            openai_api_key=None,
            audit_log_path=str(tmp_path / f"{name}.audit.jsonl"),
            registry_state_path=str(tmp_path / f"{name}.registry.json"),
        )
        log = AuditLog(tmp_path / f"{name}.audit.jsonl")
        run_panel(
            settings,
            panel=build_mock_panel(panel_mode_to_count("honest")),
            shadow=shadow_mock_judges(),
            audit=log,
        )
        return log.read_all()

    first, second = trail("first"), trail("second")

    assert len(first) == len(second) == 61
    assert [(record.event, record.run_id, record.payload) for record in first] == [
        (record.event, record.run_id, record.payload) for record in second
    ]
    assert [record.timestamp for record in first] != [record.timestamp for record in second]
    assert first[-1].record_hash != second[-1].record_hash
    assert AuditLog(tmp_path / "first.audit.jsonl").verify_chain() is True


def test_the_golden_set_label_distribution_is_deliberately_skewed():
    """Guards the fixture the entire kappa argument depends on.

    24 pass / 6 fail on the baseline is what makes a constant-pass judge score
    0.800 raw agreement and 0.000 kappa. Balance the set to 50/50 and raw agreement
    for that judge drops to chance, the difference between the two statistics
    collapses, and every calibration claim in the suite still passes while proving
    nothing. This test is what notices.

    The candidate's 16 pass / 14 fail is the planted regression: sut.v2 is
    genuinely worse by human label, so a gate that has stopped catching it is
    provably broken rather than merely quiet.
    """
    distribution = label_distribution()
    assert distribution == {
        "sut.v1": {"pass": 24, "fail": 6},
        "sut.v2": {"pass": 16, "fail": 14},
    }
    assert len(GOLDEN_SET) == 30

    baseline = distribution["sut.v1"]
    assert baseline["pass"] != baseline["fail"], "a balanced set would make kappa redundant"
    assert baseline["pass"] / 30 == 0.8

    # Stated as the claim it serves: a judge that cannot fail anything still agrees
    # with the humans four times in five on THIS set, and scores zero on kappa.
    pairs = [("pass", case.label("sut.v1")) for case in GOLDEN_SET]
    assert raw_agreement(pairs) == 0.8
    assert cohen_kappa(pairs) == 0.0

    # And on a balanced set the same judge's raw agreement would be chance level,
    # which is why the skew is load bearing rather than incidental.
    balanced = [("pass", "pass")] * 15 + [("pass", "fail")] * 15
    assert raw_agreement(balanced) == 0.5

    # The planted regression is a real drop in human labeled quality.
    assert distribution["sut.v2"]["fail"] > distribution["sut.v1"]["fail"]
