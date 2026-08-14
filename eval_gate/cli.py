"""Command line entry point.

    python -m eval_gate.cli calibrate
    python -m eval_gate.cli calibrate --panel two_miscalibrated
    python -m eval_gate.cli judge --case gc-013 --version sut.v2
    python -m eval_gate.cli cost
    python -m eval_gate.cli registry
    python -m eval_gate.cli gate --candidate sut.v2
    python -m eval_gate.cli baseline

Every subcommand defaults to the offline deterministic panel. `calibrate` and
`judge` will use a real panel when AGENT_PROVIDER and the matching key are both
set; `gate` never will, by design.
"""

from __future__ import annotations

import argparse
import sys

from eval_gate.audit import AuditLog
from eval_gate.baseline import describe as describe_baseline
from eval_gate.baseline import load_baseline
from eval_gate.config import get_settings
from eval_gate.cost import (
    combined_total_usd,
    cost_accuracy_table,
    estimate_cost,
    format_cost,
    format_cost_accuracy,
    priced_as_real,
    real_shadow_slots,
)
from eval_gate.evals import gate as gate_module
from eval_gate.evals.golden import GOLDEN_SET, label_distribution
from eval_gate.evals.runner import build_report, evaluate_gate, format_metrics, run_panel
from eval_gate.judge import judge_case
from eval_gate.llm import (
    build_mock_panel,
    describe_panel,
    get_panel,
    get_shadow_judges,
    panel_degraded,
    panel_mode_to_count,
    shadow_mock_judges,
    unique_judge_names,
)
from eval_gate.panel import aggregate
from eval_gate.prompts import PromptLibrary
from eval_gate.registry import PromptRegistry


def _settings_with(**overrides):
    base = get_settings()
    clean = {key: value for key, value in overrides.items() if value is not None}
    return base.model_copy(update=clean) if clean else base


def _panel_for(settings, offline_only: bool = False):
    if offline_only or settings.agent_provider == "mock":
        panel = build_mock_panel(panel_mode_to_count(settings.judge_panel_mode))
        return panel[:1] if settings.judge_panel_size == 1 else panel
    return get_panel(settings)


def _shadow_for(settings, offline_only: bool = False):
    if not settings.shadow_judges:
        return []
    if offline_only or settings.agent_provider == "mock":
        return shadow_mock_judges()
    return get_shadow_judges(settings)


def cmd_calibrate(args: argparse.Namespace) -> int:
    settings = _settings_with(
        judge_panel_mode=args.panel,
        judge_repeats=args.repeats,
        judge_panel_size=args.panel_size,
        shadow_judges=False if args.no_shadow else None,
    )
    panel = _panel_for(settings)
    shadow = _shadow_for(settings)
    audit = AuditLog(settings.audit_log_path) if args.audit else None
    result = run_panel(settings, panel=panel, shadow=shadow, audit=audit)
    report = build_report(result)
    decision = evaluate_gate(report, settings)
    print(format_metrics(report, decision))
    print("")
    print(
        format_cost_accuracy(
            cost_accuracy_table(report, repeats=settings.judge_repeats),
            noise=report.consistency.noise_floor,
        )
    )
    if decision.failures:
        print("\nGate would FAIL:")
        for failure in decision.failures:
            print(f"  - {failure}")
    else:
        print("\nGate would PASS")
    return 0


def cmd_judge(args: argparse.Namespace) -> int:
    settings = _settings_with(judge_panel_mode=args.panel)
    case = next((item for item in GOLDEN_SET if item.case_id == args.case), None)
    if case is None:
        print(f"no such case: {args.case}", file=sys.stderr)
        return 2
    panel = _panel_for(settings)
    library = PromptLibrary()
    names = unique_judge_names(panel)
    print(f"case      {case.case_id}  tags {','.join(case.tags) or '-'}")
    print(f"question  {case.question}")
    print(f"source    {case.source}")
    print(f"version   {args.version}")
    print(f"answer    {case.answer(args.version)}")
    print(f"human     {case.label(args.version)}  ({case.human_rationale[args.version]})")
    print(f"panel     {', '.join(describe_panel(panel))}")
    print("")
    verdicts = [
        judge_case(
            judge, case, args.version, repeat=args.repeat, library=library, judge_name=name
        )
        for name, judge in zip(names, panel)
    ]
    for verdict in verdicts:
        failed = [name for name, ok in verdict.sorted_criteria() if not ok]
        print(
            f"  {verdict.judge_name:<22} {verdict.verdict:<8} raw_ok={verdict.raw_ok} "
            f"failed={','.join(failed) or '-'}"
        )
        for reason in verdict.reasons:
            print(f"      {reason}")
    panel_verdict = aggregate(verdicts, degraded=panel_degraded(panel))
    print("")
    print(
        f"  PANEL {panel_verdict.verdict}  votes={','.join(panel_verdict.votes)}  "
        f"unanimous={panel_verdict.unanimous}  split={panel_verdict.split}  "
        f"escalated={panel_verdict.escalated}  degraded={panel_verdict.degraded}"
    )
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    settings = _settings_with(
        judge_repeats=args.repeats,
        shadow_judges=False if args.no_shadow else None,
    )
    panel = _panel_for(settings)
    shadow = _shadow_for(settings)
    repeats = settings.judge_repeats
    voting = estimate_cost(panel, repeats=repeats)
    single = estimate_cost(panel[:1], repeats=repeats)
    print(
        format_cost(
            priced_as_real(single),
            "Single judge (priced as claude-opus-5)",
        )
    )
    print("")
    print(
        format_cost(
            priced_as_real(voting),
            "Voting panel (claude-opus-5, claude-sonnet-5, gpt-5.6-terra)",
        )
    )
    if shadow:
        shadow_cost = estimate_cost(shadow, repeats=repeats)
        priced_shadow = priced_as_real(shadow_cost, slots=real_shadow_slots())
        print("")
        print(
            format_cost(
                priced_shadow,
                "Shadow bench, NON VOTING (gpt-5.6-luna, gpt-4o)",
            )
        )
        print("")
        print(
            f"  voting panel ${priced_as_real(voting).total_usd:.4f}  +  shadow "
            f"${priced_shadow.total_usd:.4f}  =  panel plus shadow "
            f"${combined_total_usd(priced_as_real(voting), priced_shadow):.4f}"
        )
        print(
            "  shadow judges do not vote, but they do bill. Reporting the voting total\n"
            "  alone would understate what running this harness costs."
        )
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    settings = get_settings()
    record = load_baseline(settings.baseline_path)
    print(f"file   : {settings.baseline_path}")
    print(f"record : {describe_baseline(record)}")
    if record is None:
        print(
            "  no baseline: record one with\n"
            "    python -m eval_gate.evals.gate --candidate sut.v1 --record-baseline"
        )
        return 1
    print(
        "  the gate compares a candidate's pass rate against this number. Approving a\n"
        "  prompt version means rewriting this file with --record-baseline, which is a\n"
        "  reviewable diff rather than a decision made in a terminal."
    )
    return 0


def cmd_registry(args: argparse.Namespace) -> int:
    settings = get_settings()
    record = load_baseline(settings.baseline_path)
    registry = PromptRegistry(
        state_path=settings.registry_state_path,
        default_active={"sut": record.sut_version.split(".")[-1]} if record else None,
        # A rollback driven from a terminal is an operational event rather than a CI
        # measurement, so it belongs in the real hash chained log. The gate's own
        # runs still write to a temp dir and never touch this file.
        audit=AuditLog(settings.audit_log_path) if args.rollback else None,
    )
    if args.activate:
        name, _, version = args.activate.partition("=")
        registry.activate(name, version)
        print(f"activated {name} {version}")
    if args.rollback:
        # The actor is named rather than left blank: a rollback with no actor in the
        # record is the kind of entry that turns a postmortem into guesswork.
        previous = registry.rollback(
            args.rollback, actor="cli", reason=args.rollback_reason
        )
        print(f"rolled {args.rollback} back to {previous}")
    for line in registry.describe():
        print(line)
    print("")
    print("Bindings (prompt version hash -> eval run id -> gate outcome)")
    bindings = registry.bindings()
    if not bindings:
        print("  none recorded yet; run scripts/run_demo.py or the gate via the demo")
    for binding in bindings:
        print(
            f"  {binding.prompt_name}.{binding.version} "
            f"{binding.content_hash[:12]} {binding.eval_run_id} "
            f"{binding.outcome} {binding.timestamp}"
        )
    return 0


def cmd_golden(args: argparse.Namespace) -> int:
    distribution = label_distribution()
    print(f"cases {len(GOLDEN_SET)}")
    for version in sorted(distribution):
        counts = distribution[version]
        print(f"  {version:<8} {counts['pass']} pass / {counts['fail']} fail")
    print("")
    for case in GOLDEN_SET:
        print(
            f"  {case.case_id}  {case.human_label['sut.v1']:<4} -> "
            f"{case.human_label['sut.v2']:<4}  {','.join(case.tags)}"
        )
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    argv: list[str] = []
    if args.panel:
        argv += ["--panel", args.panel]
    if args.candidate:
        argv += ["--candidate", args.candidate]
    if args.baseline:
        argv += ["--baseline", args.baseline]
    if args.record_baseline:
        argv += ["--record-baseline"]
    if args.threshold is not None:
        argv += ["--threshold", str(args.threshold)]
    if args.repeats is not None:
        argv += ["--repeats", str(args.repeats)]
    if args.no_shadow:
        argv += ["--no-shadow"]
    return gate_module.main(argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m eval_gate.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    calibrate = subparsers.add_parser("calibrate", help="measure the panel and print the report")
    calibrate.add_argument("--panel", choices=gate_module.PANEL_MODES, default=None)
    calibrate.add_argument("--repeats", type=int, default=None)
    calibrate.add_argument("--panel-size", type=int, choices=(1, 3), default=None)
    calibrate.add_argument(
        "--audit", action="store_true", help="append to the committed audit log"
    )
    calibrate.add_argument(
        "--no-shadow", action="store_true", help="skip the non voting shadow judges"
    )
    calibrate.set_defaults(func=cmd_calibrate)

    judge = subparsers.add_parser("judge", help="score one case with the panel")
    judge.add_argument("--case", required=True)
    judge.add_argument("--version", default="sut.v2", choices=("sut.v1", "sut.v2"))
    judge.add_argument("--repeat", type=int, default=1)
    judge.add_argument("--panel", choices=gate_module.PANEL_MODES, default=None)
    judge.set_defaults(func=cmd_judge)

    cost = subparsers.add_parser("cost", help="tokens and dollars per run")
    cost.add_argument("--repeats", type=int, default=None)
    cost.add_argument("--no-shadow", action="store_true")
    cost.set_defaults(func=cmd_cost)

    registry = subparsers.add_parser("registry", help="prompt versions and gate bindings")
    registry.add_argument("--activate", default=None, metavar="name=version")
    registry.add_argument("--rollback", default=None, metavar="name")
    registry.add_argument(
        "--rollback-reason",
        default=None,
        metavar="text",
        help="why this rollback happened, recorded with it",
    )
    registry.set_defaults(func=cmd_registry)

    golden = subparsers.add_parser("golden", help="the golden set's label distribution")
    golden.set_defaults(func=cmd_golden)

    baseline = subparsers.add_parser(
        "baseline", help="show the committed baseline record"
    )
    baseline.set_defaults(func=cmd_baseline)

    gate = subparsers.add_parser("gate", help="run the CI gate (mock panel only)")
    gate.add_argument("--panel", choices=gate_module.PANEL_MODES, default=None)
    gate.add_argument("--candidate", choices=gate_module.SUT_VERSIONS, default=None)
    gate.add_argument("--baseline", default=None)
    gate.add_argument("--record-baseline", action="store_true")
    gate.add_argument("--threshold", type=float, default=None)
    gate.add_argument("--repeats", type=int, default=None)
    gate.add_argument("--no-shadow", action="store_true")
    gate.set_defaults(func=cmd_gate)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
