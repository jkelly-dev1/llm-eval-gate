"""End to end demo. Output is captured verbatim in SAMPLE_RUN.md.

    python scripts/run_demo.py
    ENV_FILE=~/.secrets/ai.env AGENT_PROVIDER=anthropic python scripts/run_demo.py
    ENV_FILE=~/.secrets/ai.env AGENT_PROVIDER=openai    python scripts/run_demo.py

The narrative is the argument the project makes, in order:

  0. The committed baseline record: the number a candidate is measured against.
  1. What is being judged, and what the humans said about it.
  2. One case scored by all three judges, so the disagreement is visible before
     any metric summarizes it away.
  3. The honest panel, measured, then gated. It catches the planted regression.
  4. The same gate with a threshold inside the measured noise floor. It refuses.
  5. One miscalibrated judge. Outvoted; the gate still catches the regression.
  6. Two miscalibrated judges. The panel is captured and the merge would be
     allowed through. Unanimity does NOT rise, which is worth knowing; the tells
     are the discrimination count saturating and the panel's kappa collapsing.
     Both miscalibrated runs are gated against the baseline recorded under THEIR
     panel, because a baseline names the panel that measured it. The same run
     against the honest panel's baseline is shown too, and there the gate refuses
     to produce a deployment decision at all rather than comparing two numbers it
     has just called incomparable.
  9. The non voting shadow bench, and the proof that turning it off changes
     nothing about the gate outcome.
 10. What it costs, single judge versus voting panel versus panel plus shadow,
     and whether the expensive judges are buying agreement with humans.
 11. The one section that calls a live model, and the only one whose numbers can
     never reach an exit code. Offline it prints why it was skipped.
 12. The audit chain, and what tampering with it looks like.
 13. The registry binding: which eval run approved the prompt that is live.

Sections 4 through 10 are marked (mock panel) and always run offline, whatever
AGENT_PROVIDER says, because a regression gate has to be reproducible. Section 11
is the opposite: it only runs when a real provider is configured, and nothing it
measures is compared against a threshold. Sizes for it:

    --real-cases N          calibration sweep, default 30 (the whole golden set)
    --real-repeat-cases N   consistency subset, default 8
    --real-repeats N        repeats of that subset, default 3
    --real-case-selection S prefix (default) or discriminating. Any run below the
                            full 30 cases wants discriminating: the first eight
                            cases by id pass in both sut versions, so a prefix of
                            that size measures kappa 1.000 and separates nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval_gate import real_pass  # noqa: E402
from eval_gate.audit import AuditLog  # noqa: E402
from eval_gate.baseline import describe as describe_baseline  # noqa: E402
from eval_gate.baseline import load_baseline  # noqa: E402
from eval_gate.config import get_settings  # noqa: E402
from eval_gate.cost import (  # noqa: E402
    combined_total_usd,
    cost_accuracy_table,
    estimate_cost,
    format_cost,
    format_cost_accuracy,
    priced_as_real,
    real_shadow_slots,
)
from eval_gate.evals.golden import GOLDEN_SET, label_distribution  # noqa: E402
from eval_gate.evals.runner import (  # noqa: E402
    build_report,
    evaluate_gate,
    format_metrics,
    run_panel,
)
from eval_gate.judge import judge_case  # noqa: E402
from eval_gate.llm import (  # noqa: E402
    build_mock_panel,
    describe_panel,
    describe_panel_health,
    get_panel,
    get_shadow_judges,
    panel_mode_to_count,
    shadow_mock_judges,
    unique_judge_names,
)
from eval_gate.models import JudgeVerdict  # noqa: E402
from eval_gate.panel import aggregate  # noqa: E402
from eval_gate.prompts import PromptLibrary  # noqa: E402
from eval_gate.registry import PromptRegistry  # noqa: E402

DEMO_LOG = "audit/demo.audit.jsonl"
DEMO_ROLLBACK_LOG = "audit/demo.rollback.jsonl"
DEMO_REGISTRY = "audit/demo.registry.json"
SPOTLIGHT_CASE = "gc-013"

#: One committed baseline record per panel mode. A baseline names the panel that
#: measured it, so gating a miscalibrated panel against the honest panel's record is
#: not a stricter test, it is a meaningless one. Sections 7 and 8 use these, and
#: section 8 also shows what the gate does when handed the wrong one.
PANEL_BASELINES = {
    "one_miscalibrated": "baseline.one_miscalibrated.json",
    "two_miscalibrated": "baseline.two_miscalibrated.json",
}


def rule(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def run_mode(
    settings,
    mode: str,
    *,
    threshold: float | None = None,
    shadow: bool = True,
    candidate: str = "sut.v1",
    baseline=None,
):
    """Run one offline panel configuration end to end and return its verdict."""
    overrides = {
        "judge_panel_mode": mode,
        "agent_provider": "mock",
        "shadow_judges": shadow,
    }
    if threshold is not None:
        overrides["gate_max_pass_rate_drop"] = threshold
    local = settings.model_copy(update=overrides)
    panel = build_mock_panel(panel_mode_to_count(mode))
    result = run_panel(
        local, panel=panel, shadow=shadow_mock_judges() if shadow else []
    )
    report = build_report(result)
    decision = evaluate_gate(
        report, local, baseline=baseline, candidate_version=candidate
    )
    return report, decision


def summarize(label: str, report, decision) -> None:
    print(f"\n--- {label} " + "-" * max(0, 66 - len(label)))
    print(f"  panel                 {', '.join(report.panel_description)}")
    print(
        f"  pass rate             {report.baseline_version} "
        f"{report.panel.pass_rate[report.baseline_version]:.3f}  ->  "
        f"{report.candidate_version} "
        f"{report.panel.pass_rate[report.candidate_version]:.3f}"
    )
    print(
        f"  candidate             {decision.candidate_version} "
        f"{decision.candidate_pass_rate:.3f} vs committed baseline "
        f"{decision.baseline_pass_rate:.3f}"
    )
    print(
        f"  drop_vs_baseline      {decision.drop_vs_baseline:+.3f} "
        f"(threshold {decision.threshold:.3f})"
    )
    print(f"  panel kappa           {report.panel.kappa:.3f}")
    print(f"  panel false_pass_rate {report.panel.false_pass_rate:.3f}")
    print(f"  unanimity_rate        {report.panel.unanimity_rate:.3f}")
    print(f"  NOISE_FLOOR           {report.consistency.noise_floor:.3f}")
    print(f"  regression detected   {'YES' if decision.regression_detected else 'NO'}")
    print(f"  deployment decision   {decision.deployment_decision}")
    print(
        f"  gate                  "
        f"{'PASS (exit 0)' if decision.passed else 'FAIL (exit 1)'}"
    )
    print(f"  exit driven by        {decision.exit_driver}")
    for failure in decision.failures:
        print(f"    - {failure}")


def build_parser() -> argparse.ArgumentParser:
    """Flags for the paid section only. Everything else is fixed by design.

    The offline sections take no options on purpose: they are the reproducible
    half, and a demo whose offline output depended on flags would make the capture
    in SAMPLE_RUN.md one run among many rather than the run.
    """
    parser = argparse.ArgumentParser(
        prog="python scripts/run_demo.py",
        description=(
            "The end to end demo. Section 11 calls a real model when "
            "AGENT_PROVIDER and the matching key are set; these flags size it."
        ),
    )
    parser.add_argument(
        "--real-cases",
        type=int,
        default=None,
        metavar="N",
        help="calibration sweep size, in cases (default 30, the whole golden set)",
    )
    parser.add_argument(
        "--real-repeat-cases",
        type=int,
        default=None,
        metavar="N",
        help="consistency subset size, in cases (default 8)",
    )
    parser.add_argument(
        "--real-repeats",
        type=int,
        default=None,
        metavar="N",
        help="repeats of the consistency subset (default 3)",
    )
    parser.add_argument(
        "--real-case-selection",
        choices=real_pass.CASE_SELECTIONS,
        default=None,
        help="which cases a REDUCED sweep buys: prefix (default, the whole set by "
        "case id) or discriminating (only cases the two sut versions are labeled "
        "differently on). Use discriminating whenever --real-cases is below 30: a "
        "prefix of eight passes in both versions and measures nothing",
    )
    return parser


def real_model_section(settings, library, panel, shadow) -> None:
    """Section 11. Runs only against a live provider, and never gates.

    The mock branch prints rather than returning silently, because an offline
    capture with a hole in it invites the reader to assume the section ran. Its
    call count comes from the same PassPlan the paid run uses, so the figure an
    offline reader is quoted is the figure they would be billed for.
    """
    plan = real_pass.plan_for(
        settings, voting_judges=len(panel), shadow_judges=len(shadow)
    )
    if settings.agent_provider == "mock":
        print(real_pass.skipped(plan))
        return
    result = real_pass.run_real_pass(
        settings, panel=panel, shadow=shadow, plan=plan, library=library
    )
    print(real_pass.format_real_pass(result))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    overrides = {
        "real_pass_cases": args.real_cases,
        "real_pass_repeat_cases": args.real_repeat_cases,
        "real_pass_repeats": args.real_repeats,
        "real_pass_case_selection": args.real_case_selection,
    }
    settings = settings.model_copy(
        update={key: value for key, value in overrides.items() if value is not None}
    )
    library = PromptLibrary()
    baseline = load_baseline(settings.baseline_path)
    active = baseline.sut_version if baseline else settings.baseline_sut_version
    live_panel = get_panel(settings)
    live_shadow = get_shadow_judges(settings)

    rule("llm-eval-gate demo")
    print(f"provider        : {settings.agent_provider}")
    print(f"voting panel    : {', '.join(describe_panel(live_panel))}")
    print(
        f"shadow bench    : "
        f"{', '.join(describe_panel(live_shadow)) if live_shadow else 'disabled'}"
    )
    # In words rather than as a bare boolean: False means two entirely different
    # things (every slot real, or every slot mock) and printing it alone told a
    # reader with one vendor key that their partially real panel was fine.
    print(f"panel degraded  : {describe_panel_health(live_panel)}")
    print(f"repeats         : {settings.judge_repeats}")
    print(f"prompt manifest : {library.manifest_hash()[:16]}")
    for key, value in sorted(library.hashes().items()):
        print(f"  {key:<10} {value[:16]}")
    if settings.agent_provider == "mock":
        print(
            "\nnote: the panel below is three DISTINCT deterministic judges, so the\n"
            "      disagreement is real. Against real models the same golden set\n"
            "      measures whatever those models actually do."
        )
    else:
        print(
            "\nnote: the sections marked (mock panel) below always run offline, because\n"
            "      a regression gate has to be reproducible. The live panel above is\n"
            "      measured in section 11, which spends money and never gates."
        )

    rule("0. The committed baseline record")
    print(f"  file            : {settings.baseline_path}")
    print(f"  record          : {describe_baseline(baseline)}")
    print(
        "  this is the number a candidate is measured against, and it is in version\n"
        "  control. Comparing sut.v1 against sut.v2 inside one run is a measurement,\n"
        "  not a gate: the golden set carries a planted regression, so that comparison\n"
        "  reports one every time and CI would be permanently red. Comparing the\n"
        "  active version against itself is not a fix either, because a comparison\n"
        "  that can never fail is the vacuous gate this project warns about."
    )

    rule("1. What is being judged")
    distribution = label_distribution()
    print(f"golden set      : {len(GOLDEN_SET)} synthetic cases (Acme, *.example)")
    for version in sorted(distribution):
        counts = distribution[version]
        print(
            f"  {version:<8} human labels: {counts['pass']} pass / {counts['fail']} fail"
        )
    print(
        "  sut.v2 is genuinely worse by human label. That gap is the regression the\n"
        "  gate exists to catch, and it is planted rather than hoped for."
    )
    print(
        "  the distribution is skewed on purpose: an all-pass judge scores 0.80 raw\n"
        "  agreement on the baseline and 0.00 kappa, which is why kappa is reported."
    )

    rule(f"2. One case, three judges ({SPOTLIGHT_CASE}, the ambiguous kind)")
    case = next(item for item in GOLDEN_SET if item.case_id == SPOTLIGHT_CASE)
    panel = build_mock_panel(0)
    names = unique_judge_names(panel)
    print(f"question  {case.question}")
    print(f"source    {case.source}")
    for version in ("sut.v1", "sut.v2"):
        print(f"\n{version} answer: {case.answer(version)}")
        print(f"  human: {case.label(version)} ({case.human_rationale[version]})")
        for repeat in (1, 2, 3):
            verdicts = [
                judge_case(judge, case, version, repeat=repeat, library=library, judge_name=name)
                for name, judge in zip(names, panel)
            ]
            aggregated = aggregate(verdicts)
            votes = "  ".join(
                f"{verdict.judge_name}={verdict.verdict}" for verdict in verdicts
            )
            print(
                f"  repeat {repeat}: {votes}  ->  panel {aggregated.verdict} "
                f"(split={aggregated.split})"
            )
    print(
        "\n  the strict and lenient judges read the same rubric line differently: one\n"
        "  demands the literal figure, the other accepts a figure that rounds to it.\n"
        "  the balanced judge is the swing vote, and its variance across repeats IS\n"
        "  the panel's noise floor."
    )

    rule("3. A tie is escalated, not resolved")
    tie = [
        JudgeVerdict(
            case_id=case.case_id,
            sut_version="sut.v2",
            judge_name="judge-a",
            judge_model="mock-deterministic-v1",
            verdict="pass",
        ),
        JudgeVerdict(
            case_id=case.case_id,
            sut_version="sut.v2",
            judge_name="judge-b",
            judge_model="mock-deterministic-v1",
            verdict="fail",
        ),
        JudgeVerdict(
            case_id=case.case_id,
            sut_version="sut.v2",
            judge_name="judge-c",
            judge_model="mock-deterministic-v1",
            verdict="abstain",
        ),
    ]
    tied = aggregate(tie)
    print(f"  votes      {','.join(tied.votes)}")
    print(f"  verdict    {tied.verdict}")
    print(f"  escalated  {tied.escalated}  (abstentions {tied.abstentions})")
    print(
        "  one abstention leaves one pass and one fail. The panel does not break the\n"
        "  tie; it routes the case to a human. Breaking it would manufacture\n"
        "  confidence the panel does not have."
    )

    rule("4. The honest panel, measured and gated (mock panel)")
    report, decision = run_mode(settings, "honest", candidate=active, baseline=baseline)
    print(format_metrics(report, decision))
    print(f"\n  gate: {'PASS' if decision.passed else 'FAIL'}")
    for failure in decision.failures:
        print(f"    - {failure}")

    rule("5. The candidate that must be blocked (mock panel)")
    cand_report, cand_decision = run_mode(
        settings, "honest", candidate="sut.v2", baseline=baseline
    )
    summarize("honest panel, candidate sut.v2", cand_report, cand_decision)
    print(
        "\n  same panel, same golden set, same committed baseline. The only change is\n"
        "  which prompt version is on trial, and the exit code moves with it. That is\n"
        "  the claim: a regression gate wired into CI that blocks a merge."
    )

    rule("6. A threshold inside the noise floor (mock panel)")
    tight_report, tight_decision = run_mode(
        settings, "honest", threshold=0.05, candidate=active, baseline=baseline
    )
    # The floor is read off the run rather than typed in, so editing a golden case
    # can never leave this heading quoting a number the run below it disagrees with.
    summarize(
        f"threshold 0.05, noise floor {tight_report.consistency.noise_floor:.3f}",
        tight_report,
        tight_decision,
    )
    print(
        "\n  the gate refused rather than reporting a number. Inside the noise, a\n"
        "  regression and a rerun are the same measurement."
    )

    rule("7. One miscalibrated judge: outvoted (mock panel)")
    one_baseline = load_baseline(PANEL_BASELINES["one_miscalibrated"])
    one_report, one_decision = run_mode(
        settings, "one_miscalibrated", candidate="sut.v2", baseline=one_baseline
    )
    summarize("panel = strict + lenient + miscalibrated", one_report, one_decision)
    print(
        f"\n  baseline record       {describe_baseline(one_baseline)}"
    )
    print(
        "  the bad judge passes everything, and the other two outvote it on every\n"
        "  hallucinated case. The regression is still caught, and the calibration\n"
        "  layer additionally names the judge that would have missed it. The number it\n"
        "  is measured against was recorded under THIS panel, because a pass rate from\n"
        "  one panel is not comparable to a pass rate from another."
    )

    rule("8. Two miscalibrated judges: the panel is captured (mock panel)")
    two_baseline = load_baseline(PANEL_BASELINES["two_miscalibrated"])
    two_report, two_decision = run_mode(
        settings, "two_miscalibrated", candidate="sut.v2", baseline=two_baseline
    )
    summarize("panel = miscalibrated + lenient + miscalibrated", two_report, two_decision)
    print(f"\n  baseline record       {describe_baseline(two_baseline)}")
    print(
        "  a team whose panel was captured would have recorded ITS baseline with that\n"
        "  captured panel, so that is the record this run is gated against: same panel,\n"
        "  same prompt set, same repeats, a valid comparison. The gate is fooled on its\n"
        "  own terms rather than because it was handed two numbers it should have\n"
        "  refused to compare."
    )
    print(
        f"\n  the panel now passes every case in both versions, so nothing looks like a\n"
        f"  regression and the merge would be ALLOWED. Note what does NOT happen:\n"
        f"  unanimity is {two_report.panel.unanimity_rate:.3f}, LOWER than the honest panel's "
        f"{report.panel.unanimity_rate:.3f}, because the\n"
        f"  surviving lenient judge still disagrees on the cases it fails. A heuristic\n"
        f"  keyed on unanimity would stay quiet here. The tells that do fire:"
    )
    for signal in two_report.discrimination.signals():
        print(f"    - {signal}")
    print(f"  suspicion: {two_report.discrimination.suspicion()}")
    print(
        "\n  and the exit code follows: deployment_decision ALLOW, but the gate exits 1\n"
        "  because the calibration layer caught the panel that allowed it."
    )

    print(
        "\n  the same captured run, gated against the HONEST panel's baseline instead:"
    )
    mismatched_report, mismatched_decision = run_mode(
        settings, "two_miscalibrated", candidate="sut.v2", baseline=baseline
    )
    summarize(
        "captured panel, honest panel's baseline record",
        mismatched_report,
        mismatched_decision,
    )
    print(
        "\n  no deployment decision at all this time. An earlier version of this gate\n"
        "  printed BASELINE NOT COMPARABLE and then computed ALLOW from that very\n"
        "  comparison, which is the same defect the noise floor refusal exists to\n"
        "  prevent one layer down: a number the harness has already called meaningless,\n"
        "  presented as a verdict. The refusal names the field that differs, so the\n"
        "  operator knows whether to re-record the baseline or to fix the panel."
    )

    rule("9. The shadow bench: measured, non voting, and provably harmless")
    print("  scored exactly like a voting judge, excluded from the vote:")
    for judge in report.shadow_judges:
        print(
            f"    {judge.name:<26} kappa {judge.kappa:>6.3f}  "
            f"falsePass {judge.false_pass_rate:>5.3f}  "
            f"falseFail {judge.false_fail_rate:>5.3f}"
        )
    no_shadow_report, no_shadow_decision = run_mode(
        settings, "honest", shadow=False, candidate=active, baseline=baseline
    )
    same = (
        no_shadow_decision.deployment_decision == decision.deployment_decision
        and no_shadow_decision.failures == decision.failures
        and no_shadow_decision.refused == decision.refused
        and abs(no_shadow_decision.drop_vs_baseline - decision.drop_vs_baseline) < 1e-12
        and no_shadow_decision.passed == decision.passed
        and no_shadow_decision.exit_driver == decision.exit_driver
        and abs(no_shadow_decision.noise_floor - decision.noise_floor) < 1e-12
        and no_shadow_report.panel.kappa == report.panel.kappa
        and no_shadow_report.panel.unanimity_rate == report.panel.unanimity_rate
    )
    print(f"\n  shadow judges scored     : {len(report.shadow_judges)}")
    print(f"  same run with SHADOW_JUDGES off:")
    print(
        f"    deployment_decision    {no_shadow_decision.deployment_decision} "
        f"(was {decision.deployment_decision})"
    )
    print(
        f"    drop_vs_baseline       {no_shadow_decision.drop_vs_baseline:+.3f} "
        f"(was {decision.drop_vs_baseline:+.3f})"
    )
    print(
        f"    exit code              {0 if no_shadow_decision.passed else 1} "
        f"(was {0 if decision.passed else 1})"
    )
    print(f"    exit_driver            {no_shadow_decision.exit_driver}")
    print(
        f"    NOISE_FLOOR            {no_shadow_decision.noise_floor:.3f} "
        f"(was {decision.noise_floor:.3f})"
    )
    print(
        f"    panel kappa            {no_shadow_report.panel.kappa:.3f} "
        f"(was {report.panel.kappa:.3f})"
    )
    print(f"    failures               {no_shadow_decision.failures or 'none'}")
    print(f"  GATE OUTCOME UNCHANGED   : {same}")
    print(
        "\n  that is the invariant, and it is structural rather than conventional: a\n"
        "  shadow verdict is stamped shadow=True, travels on a separate field of the\n"
        "  run result, lands in a separate list on the report, and panel.aggregate\n"
        "  raises if one ever reaches the vote. A shadow judge that could move a gate\n"
        "  outcome would be a voting judge with extra steps."
    )

    rule("10. What it costs, and whether the cost buys accuracy")
    mock_panel = build_mock_panel(0)
    mock_shadow = shadow_mock_judges()
    panel_cost = priced_as_real(estimate_cost(mock_panel, repeats=settings.judge_repeats))
    single_cost = priced_as_real(
        estimate_cost(mock_panel[:1], repeats=settings.judge_repeats)
    )
    shadow_cost = priced_as_real(
        estimate_cost(mock_shadow, repeats=settings.judge_repeats),
        slots=real_shadow_slots(),
    )
    print(format_cost(single_cost, "Single judge (priced as claude-opus-5)"))
    print("")
    print(
        format_cost(
            panel_cost,
            "Voting panel (claude-opus-5, claude-sonnet-5, gpt-5.6-terra)",
        )
    )
    print("")
    print(format_cost(shadow_cost, "Shadow bench, NON VOTING (gpt-5.6-luna, gpt-4o)"))
    print(
        f"\n  voting panel ${panel_cost.total_usd:.4f}  +  shadow "
        f"${shadow_cost.total_usd:.4f}  =  panel plus shadow "
        f"${combined_total_usd(panel_cost, shadow_cost):.4f}"
    )
    print(
        f"  the voting panel costs "
        f"{panel_cost.total_usd / max(single_cost.total_usd, 1e-12):.2f}x the single judge, and on this run it\n"
        f"  measured {report.panel.kappa_vs_best_single_judge:+.3f} kappa against its own best member."
    )
    print("")
    print(
        format_cost_accuracy(
            cost_accuracy_table(report, repeats=settings.judge_repeats),
            noise=report.consistency.noise_floor,
        )
    )

    rule("11. The real model measurement pass (LIVE JUDGES, NEVER GATES)")
    real_model_section(settings, library, live_panel, live_shadow)

    rule("12. Audit trail")
    log_path = Path(DEMO_LOG)
    if log_path.exists():
        log_path.unlink()
    audit = AuditLog(log_path)
    run_panel(
        settings.model_copy(
            update={"judge_panel_mode": "honest", "agent_provider": "mock"}
        ),
        panel=build_mock_panel(0),
        shadow=shadow_mock_judges(),
        audit=audit,
    )
    records = audit.read_all()
    print(f"  records written : {len(records)}")
    print(f"  chain verifies  : {audit.verify_chain()}")
    print("\n  first record:")
    print(json.dumps(records[0].model_dump(mode="json"), indent=2, sort_keys=True))
    print("\n  a scored case:")
    scored = next(item for item in records if item.event == "case_scored")
    print(json.dumps(scored.model_dump(mode="json"), indent=2, sort_keys=True))

    # Rewrite a recorded failure as a pass, which is the edit someone would
    # actually make. An earlier version of this demo overwrote a "pass" with
    # "pass" and reported that the chain still verified, which was true and
    # proved nothing.
    lines = log_path.read_text(encoding="utf-8").splitlines()
    target = next(
        index
        for index, line in enumerate(lines)
        if json.loads(line).get("payload", {}).get("panel_verdict") == "fail"
    )
    tampered = json.loads(lines[target])
    tampered["payload"]["panel_verdict"] = "pass"
    lines[target] = json.dumps(tampered)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"\n  rewrote record {target + 1} ({tampered['payload']['case_id']} "
        f"{tampered['payload']['sut_version']}) from fail to pass"
    )
    print(f"  chain verifies now: {audit.verify_chain()}")
    print(
        "  timestamps are not frozen, so they are excluded from the determinism\n"
        "  claim rather than faked. Reproducibility comes from canonical JSON."
    )

    rule("13. Which eval run approved the prompt that is live")
    registry_path = Path(DEMO_REGISTRY)
    if registry_path.exists():
        registry_path.unlink()
    # A separate log, because section 12 deliberately tampered with the other one and
    # a rollback record appended to a broken chain would prove nothing.
    rollback_log_path = Path(DEMO_ROLLBACK_LOG)
    if rollback_log_path.exists():
        rollback_log_path.unlink()
    rollback_log = AuditLog(rollback_log_path)
    registry = PromptRegistry(state_path=registry_path, audit=rollback_log)
    registry.activate("sut", "v1")
    registry.record_gate_outcome(
        "sut",
        "v1",
        report.run_id,
        "passed" if decision.passed else "failed",
        {"pass_rate_drop": round(report.pass_rate_drop, 3)},
    )
    registry.activate("sut", "v2")
    registry.record_gate_outcome(
        "sut",
        "v2",
        two_report.run_id,
        "failed",
        {"pass_rate_drop": round(two_report.pass_rate_drop, 3)},
    )
    for line in registry.describe():
        print(f"  {line}")
    print(
        "\n  sut.v2 is active and has never been approved by a green gate. That is the\n"
        "  question a prompt registry exists to answer, and the answer here is the\n"
        "  one worth catching before a postmortem."
    )
    print(f"  history sut             {' -> '.join(registry.history('sut'))}")
    rolled_back = registry.rollback(
        "sut", actor="release-eng", reason="sut.v2 was never approved by a green gate"
    )
    print(f"\n  rollback('sut') -> {rolled_back}")
    for line in registry.describe():
        print(f"  {line}")
    print(f"  history sut             {' -> '.join(registry.history('sut'))}")
    print(
        "\n  the history is an APPEND, not a pop: v2 is still on the record as having\n"
        "  been live and then withdrawn. Popping it would leave a history byte for byte\n"
        "  identical to a registry where v2 never shipped, which is how a registry comes\n"
        "  to claim a version was never live. The rollback itself is a recorded decision:"
    )
    rollback_record = rollback_log.read_all()[-1]
    print(json.dumps(rollback_record.model_dump(mode="json"), indent=2, sort_keys=True))
    print(f"\n  rollback log chain verifies : {rollback_log.verify_chain()}")
    print(
        f"  binding written             : "
        f"{registry.bindings('sut')[-1].outcome} "
        f"(eval run {registry.bindings('sut')[-1].eval_run_id})"
    )
    print(
        "  the binding's outcome is not \"passed\", so rolling back cannot launder an\n"
        "  ungated version into an approved one: approving_run still answers with the\n"
        "  run that actually gated v1, or with nothing at all."
    )

    rule("Summary")
    print(f"  shadow judges off       gate outcome unchanged: {same}")
    print(
        f"  active {active:<17} gate {'PASS (0)' if decision.passed else 'FAIL (1)'}, "
        f"deployment {decision.deployment_decision}"
    )
    print(
        f"  candidate sut.v2        gate "
        f"{'PASS (0)' if cand_decision.passed else 'FAIL (1)'}, "
        f"deployment {cand_decision.deployment_decision}  <- merge blocked"
    )
    print(
        f"  threshold in the noise  gate "
        f"{'PASS (0)' if tight_decision.passed else 'FAIL (1)'} "
        f"(refused={tight_decision.refused})"
    )
    print(
        f"  one bad judge           gate "
        f"{'PASS (0)' if one_decision.passed else 'FAIL (1)'}, "
        f"deployment {one_decision.deployment_decision}"
    )
    print(
        f"  two bad judges          gate "
        f"{'PASS (0)' if two_decision.passed else 'FAIL (1)'}, "
        f"deployment {two_decision.deployment_decision}  <- ALLOW, and still exit 1"
    )
    print(
        f"  incomparable baseline   gate "
        f"{'PASS (0)' if mismatched_decision.passed else 'FAIL (1)'}, "
        f"deployment {mismatched_decision.deployment_decision}  "
        f"<- no verdict computed at all"
    )
    print(
        f"  real model pass         "
        f"{'skipped (provider is mock)' if settings.agent_provider == 'mock' else 'ran, and contributed nothing to any line above'}"
    )
    print(
        "\n  a gate you have not calibrated is a coin flip wearing a lab coat. The two\n"
        "  bad judge run is the proof: same gate, same golden set, same planted\n"
        "  regression, and it would have shipped."
    )
    # The demo's own exit code follows the ACTIVE version's gate, which is the one
    # CI runs. A red demo would mean the committed baseline no longer describes the
    # committed prompts. `decision` comes from section 4, which always ran on the
    # mock panel; section 11 is not consulted here and has nothing to consult, since
    # run_real_pass returns no decision to read.
    return 0 if decision.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
