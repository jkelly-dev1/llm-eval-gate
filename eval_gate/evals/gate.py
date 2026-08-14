"""CI gate: exits 1 when the candidate regresses or the panel cannot be trusted.

    python -m eval_gate.evals.gate
    python -m eval_gate.evals.gate --candidate sut.v2
    python -m eval_gate.evals.gate --panel two_miscalibrated --candidate sut.v2 \
        --baseline baseline.two_miscalibrated.json
    python -m eval_gate.evals.gate --candidate sut.v2 --record-baseline

The captured panel demonstration needs the second baseline file, because the
committed baseline.json was recorded under the honest panel and a pass rate from a
captured panel is not comparable to it. Run that invocation without --baseline and
the gate refuses to decide instead, which is the other half of the same claim.

IT ALWAYS RUNS AGAINST THE DETERMINISTIC MOCK PANEL, because a regression gate has
to be reproducible: the golden set pins which judge disagrees with which human on
which case, and a live model moves those around between runs. Real model behavior
belongs in SAMPLE_RUN.md, and never in a pass or fail signal for CI. A gate whose
verdict depends on the model of the day is a flaky test with a governance story
attached.

THE GATE HAS TWO JOBS AND THEY ARE NOT THE SAME JOB

  1. The merge blocking decision: has the candidate's pass rate fallen more than
     the threshold below the COMMITTED baseline record in baseline.json. That is
     printed as `deployment_decision`.
  2. The harness health decision: is this panel fit to be making call 1 at all.
     That is the calibration block.

THE EXIT CODE FOLLOWS BOTH. It is non-zero when any of four conditions holds, and
`exit_driver` names which one:

  - the run was refused because the threshold sits inside the measured noise floor
  - the run was refused because the committed baseline does not measure what this
    run measured (a different panel, a different repeat count, a different prompt
    set), so no deployment decision was computed from it
  - a regression was detected against the committed baseline
  - the panel failed a calibration check

Exit 0 only when the panel is healthy AND nothing regressed. Printing
`deployment_decision BLOCK` and then exiting 0 sends the CI step green while
the harness says block, which makes this project's central claim false in the
one place it is executable. A green build from a broken panel is worse than a
red one, and the exit code has to implement that rather than contradict it.
A captured panel that says ALLOW still fails, because the calibration layer catches
the panel that allowed it.

WHY THE COMPARISON IS AGAINST A COMMITTED FILE

Comparing sut.v1 against sut.v2 inside one run is a measurement, not a gate: the
golden set carries a planted regression, so the default invocation would report one
every time and CI would be permanently red. Comparing the active version against
itself is not a fix either, because a comparison that can never fail is the vacuous
gate this project exists to warn about. So the number a candidate is measured
against lives in baseline.json, in version control, and approving a prompt version
means rewriting that file with --record-baseline. That is a reviewable diff, and it
is what binds prompt version hash to eval run id to gate outcome in the registry.

SHADOW JUDGES ARE PRINTED AND NEVER GATED ON. A shadow judge can never change a
gate outcome. If it could, it would be a voting judge with extra steps, and the
reason to run a cheap judge in shadow is to learn what it would have said without
betting a release on the answer. Toggling SHADOW_JUDGES changes the report's length
and nothing about the exit code.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from eval_gate.audit import AuditLog, utc_now
from eval_gate.baseline import BaselineRecord, describe, load_baseline, write_baseline
from eval_gate.config import Settings, get_settings
from eval_gate.cost import cost_accuracy_table, format_cost_accuracy
from eval_gate.evals.runner import build_report, evaluate_gate, format_metrics, run_panel
from eval_gate.llm import build_mock_panel, panel_mode_to_count, shadow_mock_judges
from eval_gate.prompts import PromptLibrary
from eval_gate.registry import PromptRegistry

PANEL_MODES = ("honest", "one_miscalibrated", "two_miscalibrated")
SUT_VERSIONS = ("sut.v1", "sut.v2")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m eval_gate.evals.gate",
        description="Eval gate. Always runs on the deterministic mock panel.",
    )
    parser.add_argument(
        "--panel",
        choices=PANEL_MODES,
        default=None,
        help="which offline panel to gate with (default: JUDGE_PANEL_MODE, or honest)",
    )
    parser.add_argument(
        "--candidate",
        choices=SUT_VERSIONS,
        default=None,
        help="which sut version to gate (default: the registry's active version, "
        "which on a fresh checkout is the one named in the committed baseline)",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="path to the committed baseline record (default: baseline.json)",
    )
    parser.add_argument(
        "--record-baseline",
        action="store_true",
        help="approve this candidate: rewrite the baseline record and bind it in the "
        "registry. Refused when the panel is unhealthy or the run was refused.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="regression threshold as an absolute pass rate drop (default: settings)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=None,
        help="repeats per case, used to measure the noise floor (default: settings)",
    )
    parser.add_argument(
        "--no-shadow",
        action="store_true",
        help="skip the non voting shadow judges (must not change the outcome)",
    )
    parser.add_argument(
        "--panel-size",
        type=int,
        choices=(1, 3),
        default=None,
        help="1 to gate with a single judge, for the cost comparison",
    )
    return parser.parse_args(argv)


def _settings_for(args: argparse.Namespace) -> Settings:
    base = get_settings()
    overrides: dict = {}
    if args.panel is not None:
        overrides["judge_panel_mode"] = args.panel
    if args.threshold is not None:
        overrides["gate_max_pass_rate_drop"] = args.threshold
    if args.repeats is not None:
        overrides["judge_repeats"] = args.repeats
    if args.panel_size is not None:
        overrides["judge_panel_size"] = args.panel_size
    if args.candidate is not None:
        overrides["gate_candidate"] = args.candidate
    if args.baseline is not None:
        overrides["baseline_path"] = args.baseline
    if args.no_shadow:
        overrides["shadow_judges"] = False
    # The gate never selects a real provider, whatever the environment says.
    overrides["agent_provider"] = "mock"
    return base.model_copy(update=overrides)


def resolve_candidate(
    settings: Settings, baseline: BaselineRecord | None, library: PromptLibrary
) -> tuple[str, PromptRegistry]:
    """Which sut version is being gated, and the registry that decided.

    The registry's state file is a run artifact and is gitignored, so on a fresh
    checkout its active pointer is seeded from the committed baseline record. Left
    to its own fallback it would pick the highest version on disk, which is the
    deliberately regressed sut.v2, and a clean clone would go red for no reason.
    """
    registry = PromptRegistry(
        library=library,
        state_path=settings.registry_state_path,
        default_active={"sut": baseline.sut_version.split(".")[-1]} if baseline else None,
    )
    if settings.gate_candidate:
        return settings.gate_candidate, registry
    return f"sut.{registry.active_version('sut')}", registry


def _record(
    settings: Settings,
    report,
    decision,
    candidate: str,
    registry: PromptRegistry,
    chain_ok: bool,
) -> str:
    """Rewrite the baseline record, or explain why this run cannot approve one.

    Refused when the panel is unhealthy, the run was refused, or the audit chain is
    broken. Approving a prompt version on the word of a panel that just failed its
    own calibration checks is how a captured panel becomes the reference, and the
    committed file would then look authoritative forever.

    AN INCOMPARABLE BASELINE IS NOT ON THAT LIST, deliberately. Recording writes a
    NEW record out of what THIS run measured, and nothing in it is derived from the
    old one, so the old record's comparability cannot taint it. Re-recording is in
    fact the fix for an incomparable baseline, and refusing here would leave the
    operator no way to apply it.
    """
    if decision.refused:
        return (
            "  --record-baseline REFUSED: the run itself was refused because the "
            "threshold sits inside the noise floor. There is no trustworthy pass rate "
            "to record."
        )
    if not decision.panel_healthy:
        return (
            "  --record-baseline REFUSED: the panel failed "
            f"{len(decision.panel_failures)} calibration check(s). A baseline recorded "
            "by a panel that cannot gate would look authoritative forever."
        )
    if not chain_ok:
        return "  --record-baseline REFUSED: the audit chain did not verify."

    record = BaselineRecord(
        prompt_version_hash=report.prompt_manifest_hash,
        sut_version=candidate,
        panel_mode=report.panel_mode,
        repeats=report.repeats,
        measured_pass_rate=round(decision.candidate_pass_rate, 6),
        recorded_at=utc_now(),
        run_id=report.run_id,
    )
    path = write_baseline(settings.baseline_path, record)
    version = candidate.split(".")[-1]
    registry.activate("sut", version)
    # The binding records whether the PANEL approved this version, which is the
    # condition under which recording is allowed at all. Reusing decision.passed
    # here would have written "failed" during bootstrap, when the only problem was
    # that no baseline existed yet. A regression that was consciously accepted is
    # recorded as such, so registry.approving_run still reports it as not approved
    # by a green gate.
    binding = registry.record_gate_outcome(
        "sut",
        version,
        report.run_id,
        "regression-accepted" if decision.regression_detected else "passed",
        {
            "measured_pass_rate": round(decision.candidate_pass_rate, 3),
            "panel_mode": report.panel_mode,
        },
    )
    lines = [
        f"  baseline recorded: {path} -> {describe(record)}",
        f"  registry binding : sut.{version} {binding.content_hash[:12]} "
        f"{binding.eval_run_id} {binding.outcome}",
    ]
    if decision.regression_detected:
        lines.append(
            f"  NOTE: this run detected a regression against the PREVIOUS baseline "
            f"({decision.baseline_pass_rate:.3f} -> {decision.candidate_pass_rate:.3f}). "
            f"The new baseline enshrines the lower pass rate, so accepting it is an "
            f"explicit decision rather than a silent one. This run still exits 1."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = _settings_for(args)
    library = PromptLibrary()
    baseline = load_baseline(settings.baseline_path)
    candidate, registry = resolve_candidate(settings, baseline, library)

    panel = build_mock_panel(panel_mode_to_count(settings.judge_panel_mode))
    if settings.judge_panel_size == 1:
        panel = panel[:1]
    shadow = shadow_mock_judges() if settings.shadow_judges else []

    # The gate writes its audit trail to a temporary log so a CI run never appends
    # to the committed sample log.
    with tempfile.TemporaryDirectory() as directory:
        audit = AuditLog(Path(directory) / "eval.audit.jsonl")
        result = run_panel(
            settings, panel=panel, shadow=shadow, audit=audit, library=library
        )
        report = build_report(result)
        decision = evaluate_gate(
            report, settings, baseline=baseline, candidate_version=candidate
        )
        audit.append(
            "gate_decision",
            report.run_id,
            {
                "panel_mode": settings.judge_panel_mode,
                "candidate": candidate,
                "baseline_pass_rate": decision.baseline_pass_rate,
                "candidate_pass_rate": decision.candidate_pass_rate,
                "drop_vs_baseline": decision.drop_vs_baseline,
                "threshold": decision.threshold,
                "noise_floor": decision.noise_floor,
                "regression_detected": decision.regression_detected,
                "deployment_decision": decision.deployment_decision,
                "panel_healthy": decision.panel_healthy,
                "refused": decision.refused,
                "baseline_incomparable": decision.baseline_incomparable,
                "baseline_differing_fields": decision.baseline_differing_fields,
                "exit_code": 0 if decision.passed else 1,
                "exit_driver": decision.exit_driver,
                "failures": decision.failures,
            },
        )
        chain_ok = audit.verify_chain()
        records = len(audit.read_all())

    print("=" * 78)
    print(
        f"eval gate: {settings.judge_panel_mode} mock panel, "
        f"{settings.judge_repeats} repeats, candidate {candidate}"
    )
    print(f"baseline  : {describe(baseline)}")
    print("=" * 78)
    print(format_metrics(report, decision, baseline=baseline))
    print("")
    print(
        format_cost_accuracy(
            cost_accuracy_table(report, repeats=settings.judge_repeats),
            noise=report.consistency.noise_floor,
        )
    )
    print("")
    print(f"  {'audit_records':<34} {records}")
    print(f"  {'audit_chain_intact':<34} {'yes' if chain_ok else 'NO'}")

    if args.record_baseline:
        print("")
        print(_record(settings, report, decision, candidate, registry, chain_ok))

    failures = list(decision.failures)
    if not chain_ok:
        failures.append("audit chain verification failed")

    if failures:
        print("\nEVAL GATE FAILED")
        for failure in failures:
            print(f"  - {failure}")
        drivers = decision.exit_driver
        if not chain_ok:
            drivers = f"audit chain verification failed (also: {drivers})"
        print(f"\n  exit driven by: {drivers}")
        return 1

    print(f"\nEVAL GATE PASSED ({report.discrimination.cases} cases)")
    print(
        f"  exit driven by: {decision.exit_driver}"
    )
    print(
        f"  deployment_decision {decision.deployment_decision}, candidate "
        f"{decision.candidate_version} pass rate {decision.candidate_pass_rate:.3f} "
        f"vs committed baseline {decision.baseline_pass_rate:.3f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
