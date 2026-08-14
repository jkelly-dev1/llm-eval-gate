"""Which eval run approved the prompt that is live, and what a rollback records.

A git tag tells you what changed. A version number tells you the order things
changed in. Neither answers the question this module exists to answer: was the gate
green when this prompt went live, and can anyone still prove it. So a version is
identified by the sha256 of its body rather than by a label that can be edited in
place, and the binding recorded is a triple of content hash, eval run id and gate
outcome. The hash chained log those bindings sit beside is tested in
tests/test_audit.py, which mirrors the module it tests.

Rolling back is a decision, so the properties asserted here are about a rollback
being recorded rather than performed. The history is APPENDED to, because a history
that pops the withdrawn version is indistinguishable from one where that version
never shipped, and the registry then has no way to answer the one question a
postmortem asks. The rollback also writes a binding and an audit record, and neither
of them launders the restored version into an approved one.

Two properties are about refusal rather than recording. Approving a prompt version
is the act of rewriting a committed file, and that act is refused when the panel
that measured it just failed its own calibration checks, when the run was refused
outright, or when the audit chain does not verify: a baseline recorded on the word
of a captured panel would look authoritative forever.

And one is about a latent trap that was real. The registry's state file is a run
artifact and is gitignored, so on a fresh checkout the active pointer has to be
seeded from the committed baseline record. Left to its own fallback the registry
picks the highest version on disk, which is the deliberately regressed sut.v2, and
a clean clone would gate the regression and go red on its first CI run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eval_gate.audit import AuditLog
from eval_gate.baseline import load_baseline
from eval_gate.config import Settings
from eval_gate.evals.gate import resolve_candidate
from eval_gate.prompts import PromptLibrary, content_hash
from eval_gate.registry import PromptRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]


def _registry(prompt_dir: Path, state: Path, **kwargs) -> PromptRegistry:
    return PromptRegistry(library=PromptLibrary(prompt_dir), state_path=state, **kwargs)


def test_a_prompt_version_is_identified_by_its_content_hash(prompt_dir, tmp_path):
    """The identifier is the body, so metadata cannot masquerade as a change.

    Two prompts with the same hash are the same prompt whatever the frontmatter
    says, and editing the frontmatter alone therefore produces the same version.
    That is the difference between versioning the prompt and versioning the file.
    """
    registry = _registry(prompt_dir, tmp_path / "registry.json")
    asset = registry.library.get("sut", "v1")

    assert registry.content_hash("sut", "v1") == content_hash(asset.body)
    assert registry.content_hash("sut", "v1") != registry.content_hash("sut", "v2")

    header_edit = (prompt_dir / "sut.v1.md").read_text(encoding="utf-8").replace(
        "---\n", "---\nowner: platform-eng\n", 1
    )
    (prompt_dir / "sut.v1.md").write_text(header_edit, encoding="utf-8")
    reloaded = _registry(prompt_dir, tmp_path / "registry.json")
    assert reloaded.content_hash("sut", "v1") == registry.content_hash("sut", "v1")


def test_editing_a_prompt_produces_a_new_version(prompt_dir, tmp_path):
    """A label edited in place is a new prompt, and the old approval stops matching.

    A new file is the ordinary case: sut.v3 appears, carries its own hash, and can
    be activated. The case worth pinning is the sneaky one. Editing sut.v1's body
    while keeping its label leaves the registry holding a binding for a hash that no
    longer exists on disk, so `approving_run` answers None instead of vouching for a
    prompt nobody gated.
    """
    state = tmp_path / "registry.json"
    registry = _registry(prompt_dir, state, default_active={"sut": "v1"})
    original_hash = registry.content_hash("sut", "v1")
    registry.record_gate_outcome("sut", "v1", "run-aaaaaaaaaaaa", "passed", {"pass_rate": 0.767})
    assert registry.approving_run("sut", "v1").eval_run_id == "run-aaaaaaaaaaaa"

    (prompt_dir / "sut.v3.md").write_text(
        "---\nname: sut\nversion: v3\n---\nAnswer only from the source. Quote every figure.\n",
        encoding="utf-8",
    )
    with_v3 = _registry(prompt_dir, state, default_active={"sut": "v1"})
    assert with_v3.versions("sut") == ["v1", "v2", "v3"]
    assert with_v3.content_hash("sut", "v3") not in {
        with_v3.content_hash("sut", "v1"),
        with_v3.content_hash("sut", "v2"),
    }

    body = (prompt_dir / "sut.v1.md").read_text(encoding="utf-8")
    (prompt_dir / "sut.v1.md").write_text(body + "\nPrefer round, memorable figures.\n", encoding="utf-8")
    after_edit = _registry(prompt_dir, state, default_active={"sut": "v1"})
    assert after_edit.content_hash("sut", "v1") != original_hash
    assert after_edit.active_version("sut") == "v1"
    assert after_edit.approving_run("sut", "v1") is None, (
        "the recorded approval was for the old body, and a label cannot inherit it"
    )
    assert "NEVER APPROVED BY A GREEN GATE" in "\n".join(after_edit.describe())


def test_rollback_restores_the_previous_active_version(prompt_dir, tmp_path):
    """Rolling back moves the active pointer, and both refusals guard the pointer.

    A rollback with nothing to roll back to raises rather than quietly doing
    nothing, because a silent no-op is how an operator comes to believe a bad
    prompt was reverted while it is still live. A SECOND consecutive rollback
    raises for the mirror image reason: after v1 -> v2 -> v1 the entry before the
    live one is the version that was just withdrawn, so rolling back again would
    re-activate it, and a promotion that calls itself a rollback is worse than an
    error. The chosen behavior is to refuse rather than to walk further back, and
    the caller is told to name the version it wants.
    """
    registry = _registry(prompt_dir, tmp_path / "registry.json", default_active={"sut": "v1"})
    assert registry.active_version("sut") == "v1"

    registry.activate("sut", "v2")
    assert registry.active_version("sut") == "v2"
    assert registry.history("sut") == ["v1", "v2"]

    assert registry.rollback("sut") == "v1"
    assert registry.active_version("sut") == "v1"
    assert registry.active_hash("sut") == registry.content_hash("sut", "v1")

    with pytest.raises(ValueError, match="a second consecutive rollback"):
        registry.rollback("sut")
    assert registry.active_version("sut") == "v1", "a refused rollback moves nothing"
    assert registry.history("sut") == ["v1", "v2", "v1"], "and records nothing either"

    # Naming the version explicitly is the supported way forward, and a rollback
    # after that is allowed again: it is a return from a version that really was
    # promoted rather than from one that was already withdrawn.
    registry.activate("sut", "v2")
    assert registry.rollback("sut") == "v1"
    assert registry.history("sut") == ["v1", "v2", "v1", "v2", "v1"]

    with pytest.raises(ValueError, match="no previous version"):
        _registry(prompt_dir, tmp_path / "fresh.json").rollback("judge")

    # And the rollback survives a reload, because it was persisted, not remembered.
    reloaded = _registry(prompt_dir, tmp_path / "registry.json")
    assert reloaded.active_version("sut") == "v1"
    assert reloaded.history("sut") == ["v1", "v2", "v1", "v2", "v1"]


def test_a_rollback_is_recorded_rather_than_erased(prompt_dir, tmp_path, audit_log):
    """A history that forgets a rollback will one day claim a version was never live.

    Mutation check: restore `history.pop()` in PromptRegistry.rollback and this test
    fails. The pop leaves ["v1"], which is byte for byte the history of a registry
    where v2 never shipped, so the one fact a postmortem needs is the fact that gets
    deleted. The append leaves ["v1", "v2", "v1"]: v2 went live, and it was withdrawn.

    The other half is that a rollback is itself a recorded decision, with an actor
    and a reason, in the hash chained log where it cannot be quietly revised, and
    that "which eval run approved the prompt currently in production" is still a
    lookup afterwards. The rollback binding does not answer that question by
    pretending to be an approval: its outcome is "rolled-back-to", so `approving_run`
    keeps returning the eval run that actually gated v1.
    """
    state = tmp_path / "registry.json"
    registry = PromptRegistry(
        library=PromptLibrary(prompt_dir),
        state_path=state,
        default_active={"sut": "v1"},
        audit=audit_log,
    )
    registry.record_gate_outcome("sut", "v1", "run-aaaaaaaaaaaa", "passed", {"pass_rate": 0.767})
    registry.activate("sut", "v2")
    registry.record_gate_outcome("sut", "v2", "run-bbbbbbbbbbbb", "failed")

    assert registry.rollback("sut", actor="release-eng", reason="pass rate fell") == "v1"

    # The history is an append, so v2 is still on the record as having been live.
    assert registry.history("sut") == ["v1", "v2", "v1"]
    assert registry.active_version("sut") == "v1"

    # The rollback wrote a binding of its own, and it is not an approval.
    binding = registry.bindings("sut")[-1]
    assert binding.outcome == "rolled-back-to"
    assert binding.version == "v1"
    assert binding.content_hash == registry.content_hash("sut", "v1")
    assert binding.metrics["actor"] == "release-eng"
    assert binding.metrics["reason"] == "pass rate fell"
    assert binding.metrics["rolled_back_from"] == "v2"

    # So the question the registry exists to answer still has its old answer.
    approving = registry.approving_run("sut")
    assert approving is not None and approving.eval_run_id == "run-aaaaaaaaaaaa"
    assert approving.outcome == "passed", "the rollback binding must not be mistaken for one"

    # And the decision is in the hash chained trail, with who and why.
    assert audit_log.verify_chain() is True
    record = audit_log.read_all()[-1]
    assert record.event == "prompt_rollback"
    assert record.payload["from_version"] == "v2"
    assert record.payload["to_version"] == "v1"
    assert record.payload["actor"] == "release-eng"
    assert record.payload["reason"] == "pass rate fell"
    assert record.payload["from_hash"] == registry.content_hash("sut", "v2")
    assert record.payload["to_hash"] == registry.content_hash("sut", "v1")
    assert record.run_id == "run-aaaaaaaaaaaa", "joined to the run that approved v1"

    # All of it survives a reload, because it was persisted rather than remembered.
    reloaded = PromptRegistry(library=PromptLibrary(prompt_dir), state_path=state)
    assert reloaded.history("sut") == ["v1", "v2", "v1"]
    assert reloaded.bindings("sut")[-1].outcome == "rolled-back-to"
    assert reloaded.approving_run("sut").eval_run_id == "run-aaaaaaaaaaaa"


def test_a_rollback_to_a_version_no_green_gate_approved_says_so(prompt_dir, tmp_path):
    """Rolling back does not launder a version into an approved one.

    The version being restored here was never gated green, so the rollback binding
    records NO_APPROVING_RUN rather than borrowing the run id of the version it
    replaced, and `describe` still reports the live prompt as never approved. A
    rollback that made "which run approved this" answerable by inventing an answer
    would be worse than one that left the question open.
    """
    registry = PromptRegistry(
        library=PromptLibrary(prompt_dir),
        state_path=tmp_path / "registry.json",
        default_active={"sut": "v1"},
    )
    registry.activate("sut", "v2")
    registry.record_gate_outcome("sut", "v2", "run-cccccccccccc", "passed")

    assert registry.rollback("sut", actor="oncall") == "v1"

    binding = registry.bindings("sut")[-1]
    assert binding.eval_run_id == "no-approving-run"
    assert registry.approving_run("sut", "v1") is None
    assert "NEVER APPROVED BY A GREEN GATE" in "\n".join(registry.describe())


def test_the_registry_records_which_eval_run_gated_which_version(run_gate, baseline_copy):
    """The question asked in a postmortem: which eval run approved what is live.

    Recording a baseline binds prompt content hash to eval run id to gate outcome
    in one record, so the answer is a lookup rather than an archaeology exercise.
    The second half is the one this asserts: a regression that was consciously
    accepted is recorded as "regression-accepted", so `approving_run` still answers
    None and nobody can later claim a green gate approved it.
    """
    approved = run_gate("--record-baseline", baseline=baseline_copy)
    assert approved.exit_code == 0
    assert "baseline recorded" in approved.output
    assert "registry binding : sut.v1" in approved.output

    registry = PromptRegistry(state_path=approved.registry_path)
    binding = registry.approving_run("sut", "v1")
    assert binding is not None
    assert binding.eval_run_id == approved.report.run_id
    assert binding.outcome == "passed"
    assert binding.content_hash == registry.content_hash("sut", "v1")
    assert binding.metrics["measured_pass_rate"] == 0.8
    assert binding.metrics["panel_mode"] == "honest"
    assert load_baseline(baseline_copy).run_id == approved.report.run_id

    accepted = run_gate("--candidate", "sut.v2", "--record-baseline", baseline=baseline_copy)
    assert accepted.exit_code == 1, "accepting a regression is still a red build"
    assert "NOTE: this run detected a regression" in accepted.output
    after = PromptRegistry(state_path=accepted.registry_path)
    assert after.active_version("sut") == "v2"
    assert after.bindings("sut")[-1].outcome == "regression-accepted"
    assert after.approving_run("sut", "v2") is None
    assert "NEVER APPROVED BY A GREEN GATE" in "\n".join(after.describe())


def test_a_fresh_checkout_takes_its_active_version_from_the_committed_baseline_record(
    tmp_path, library
):
    """The latent trap: without the seed, a clean clone gates the regressed prompt.

    The registry's state file is gitignored, so a fresh checkout has no active
    pointer and the fallback picks the highest version on disk. That is sut.v2, the
    deliberately regressed one, so the default gate invocation would compare the
    regression against the baseline and go red on the first CI run of an untouched
    clone. The committed baseline record is the only committed statement of which
    version is live, so it is what seeds the pointer.
    """
    baseline = load_baseline(REPO_ROOT / "baseline.json")
    assert baseline is not None and baseline.sut_version == "sut.v1"

    unseeded = PromptRegistry(library=library, state_path=tmp_path / "unseeded.json")
    assert unseeded.active_version("sut") == "v2", (
        "this is the trap the seed exists to avoid, stated as a fact about the fallback"
    )

    seeded = PromptRegistry(
        library=library, state_path=tmp_path / "seeded.json", default_active={"sut": "v1"}
    )
    assert seeded.active_version("sut") == "v1"

    settings = Settings(
        agent_provider="mock",
        anthropic_api_key=None,
        openai_api_key=None,
        gate_candidate=None,
        registry_state_path=str(tmp_path / "resolved.json"),
    )
    candidate, registry = resolve_candidate(settings, baseline, library)
    assert candidate == "sut.v1"
    assert registry.active_version("sut") == "v1"

    without_baseline, _registry_again = resolve_candidate(
        Settings(
            agent_provider="mock",
            anthropic_api_key=None,
            openai_api_key=None,
            gate_candidate=None,
            registry_state_path=str(tmp_path / "unseeded-resolved.json"),
        ),
        None,
        library,
    )
    assert without_baseline == "sut.v2"


def test_record_baseline_is_refused_when_the_panel_is_unhealthy(run_gate, baseline_copy):
    """A baseline recorded by a captured panel would look authoritative forever.

    The two_miscalibrated panel reports the regressed candidate at pass rate 1.000.
    Recording that would enshrine a captured panel's number as the reference every
    future candidate is measured against, so the request is refused and the file on
    disk is left exactly as it was.
    """
    before = baseline_copy.read_text(encoding="utf-8")
    run = run_gate(
        "--panel", "two_miscalibrated", "--candidate", "sut.v2", "--record-baseline",
        baseline=baseline_copy,
    )

    assert run.exit_code == 1
    assert "--record-baseline REFUSED" in run.output
    assert "failed 4 calibration check(s)" in run.output
    assert baseline_copy.read_text(encoding="utf-8") == before
    assert PromptRegistry(state_path=run.registry_path).bindings("sut") == []


def test_record_baseline_is_refused_when_the_run_itself_was_refused(run_gate, baseline_copy):
    """No trustworthy pass rate was measured, so there is nothing to record.

    The refusal chains: a threshold inside the noise floor produces no verdict, and
    a run with no verdict cannot approve a prompt version either.
    """
    before = baseline_copy.read_text(encoding="utf-8")
    run = run_gate("--threshold", "0.05", "--record-baseline", baseline=baseline_copy)

    assert run.exit_code == 1
    assert run.decision.refused is True
    assert "--record-baseline REFUSED" in run.output
    assert "There is no trustworthy pass rate" in run.output
    assert baseline_copy.read_text(encoding="utf-8") == before


def test_record_baseline_is_refused_when_the_audit_chain_is_broken(
    run_gate, baseline_copy, monkeypatch
):
    """An approval whose evidence cannot be verified is not an approval.

    The chain check is forced to fail, which is the only honest way to test this
    offline: the gate's own log is written fresh into a temp dir on every run and
    therefore always verifies. A broken chain also drives the exit code on its own,
    ahead of anything the panel measured.
    """
    before = baseline_copy.read_text(encoding="utf-8")
    monkeypatch.setattr(AuditLog, "verify_chain", lambda self: False)

    run = run_gate("--record-baseline", baseline=baseline_copy)

    assert run.exit_code == 1
    assert "--record-baseline REFUSED: the audit chain did not verify." in run.output
    assert "audit chain verification failed" in run.output
    assert "  audit_chain_intact                 NO" in run.output
    assert baseline_copy.read_text(encoding="utf-8") == before
    assert run.decision.panel_healthy is True, (
        "the panel was fine; the evidence was not, and that is a separate failure"
    )
