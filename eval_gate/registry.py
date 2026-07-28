"""Prompt registry: which prompt version is live, and which eval run approved it.

The question this module exists to answer is "which eval run approved the prompt
currently in production", and it is a question most prompt versioning schemes
cannot answer. A git tag tells you what changed. A prompt version number tells
you the order things changed in. Neither tells you whether the gate was green
when that version went live, or whether it went live because someone was in a
hurry on a Friday.

So the binding recorded here is a triple: prompt content hash -> eval run id ->
gate outcome. Versions are identified by sha256 of the prompt body rather than by
a label, because a label can be edited in place and a content hash cannot. Two
prompts with the same hash are the same prompt no matter what the frontmatter
says.

`rollback()` moves the active pointer back to the previous version by APPENDING to
the history rather than by popping it, so v1 -> v2 -> v1 says what happened: v2
went live and was withdrawn. Rolling back is a decision, and a registry that
quietly forgets a rollback is a registry that will one day claim a version was
never live.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from eval_gate.audit import AuditLog, utc_now
from eval_gate.prompts import PromptLibrary

#: The binding outcome written for a rollback. It is deliberately NOT "passed",
#: because a rollback is not an approval: `approving_run` still has to answer with
#: the eval run that gated the restored version, or None if no run ever did.
ROLLBACK_OUTCOME = "rolled-back-to"

#: The eval_run_id recorded when the restored version was never gated green. A
#: literal is better than an empty string here: it shows up in `describe()` and in
#: the audit payload as a fact rather than as a missing field.
NO_APPROVING_RUN = "no-approving-run"


@dataclass
class GateBinding:
    """One recorded answer to "did the gate approve this prompt version"."""

    prompt_name: str
    version: str
    content_hash: str
    eval_run_id: str
    #: "passed" | "failed" | "refused" | "regression-accepted" | "rolled-back-to".
    outcome: str
    timestamp: str
    metrics: dict = field(default_factory=dict)


class PromptRegistry:
    """Content hash versioning, an active pointer, rollback, and gate bindings."""

    def __init__(
        self,
        library: PromptLibrary | None = None,
        state_path: str | Path | None = None,
        default_active: dict[str, str] | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.library = library or PromptLibrary()
        self.state_path = Path(state_path) if state_path else None
        # Optional, because the gate builds a registry before it opens its log and
        # never rolls back. When a log IS attached, `rollback` writes the actor and
        # the reason into the hash chained trail, where they cannot be edited after
        # the fact. The registry's own state file is not hash chained.
        self.audit = audit
        # `default_active` is how a committed fact overrides the fallback below.
        # The registry's state file is a run artifact and is gitignored, so on a
        # fresh checkout there is no state and the fallback would pick the highest
        # version on disk, which is sut.v2, the deliberately regressed one. That
        # would make the default gate invocation compare the regressed prompt and
        # go red on a clean clone. The gate therefore seeds this from
        # baseline.json, which IS committed.
        self._default_active = dict(default_active or {})
        self._active: dict[str, str] = {}
        self._history: dict[str, list[str]] = {}
        self._bindings: list[GateBinding] = []
        self._load()
        for name in self.library.names():
            if name not in self._active:
                seeded = self._default_active.get(name)
                if seeded and seeded in self.library.versions(name):
                    self.activate(name, seeded, persist=False)
                else:
                    # Fallback: the highest version on disk, which for this naming
                    # scheme is lexical order (v1 < v2).
                    self.activate(name, self.library.versions(name)[-1], persist=False)
        self._save()

    # --- persistence ------------------------------------------------------- #

    def _load(self) -> None:
        if not self.state_path or not self.state_path.exists():
            return
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self._active = dict(state.get("active", {}))
        self._history = {key: list(value) for key, value in state.get("history", {}).items()}
        self._bindings = [GateBinding(**row) for row in state.get("bindings", [])]

    def _save(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "active": {key: self._active[key] for key in sorted(self._active)},
            "history": {key: self._history[key] for key in sorted(self._history)},
            "bindings": [asdict(binding) for binding in self._bindings],
        }
        self.state_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    # --- versions ---------------------------------------------------------- #

    def versions(self, name: str) -> list[str]:
        return self.library.versions(name)

    def content_hash(self, name: str, version: str) -> str:
        return self.library.get(name, version).content_hash

    def active_version(self, name: str) -> str:
        return self._active[name]

    def active_hash(self, name: str) -> str:
        return self.content_hash(name, self.active_version(name))

    def activate(self, name: str, version: str, *, persist: bool = True) -> str:
        """Point `name` at `version`, appending to its history."""
        self.library.get(name, version)  # raises if the asset does not exist
        history = self._history.setdefault(name, [])
        if not history or history[-1] != version:
            history.append(version)
        self._active[name] = version
        if persist:
            self._save()
        return version

    def rollback(
        self, name: str, *, actor: str = "unknown", reason: str | None = None
    ) -> str:
        """Withdraw the live version and re-activate the one before it, by APPENDING.

        WHY THIS APPENDS INSTEAD OF POPPING. The history is the record of which
        versions were live, so after a rollback it has to read v1 -> v2 -> v1: v2
        went live, and it was withdrawn. An earlier version of this method popped
        v2 off the end, which left ["v1"], a history indistinguishable from a
        registry where v2 never shipped at all. That is the exact failure this
        module's docstring warns about, so the pop is the mutation this method's
        tests are checked against.

        A ROLLBACK IS RECORDED THREE WAYS, because each answers a different
        question. The history append says v2 was live. The binding says why the
        version now live is live, and carries the eval run that approved it, or
        NO_APPROVING_RUN when no run ever did, so "which eval run approved the
        prompt currently in production" is still a lookup after a rollback. And
        the audit record, when a log is attached, puts the actor, both versions
        and the reason into the hash chained trail.

        The binding's outcome is ROLLBACK_OUTCOME rather than "passed", so
        `approving_run` cannot mistake the act of rolling back for an approval.

        A SECOND CONSECUTIVE ROLLBACK IS REFUSED rather than walking further back.
        Once the history reads v1 -> v2 -> v1 the entry before the live one is v2,
        the version just withdrawn, so rolling back again would re-activate it: a
        promotion wearing a rollback's name. Skipping past it to some earlier entry
        would be the registry choosing a version nobody named. Both are worse than
        an error, so the caller is told to call `activate` with the version it
        actually wants. The refusal is decided from the last recorded binding
        rather than by pattern matching the history, because the history alone
        cannot tell a rollback from a fresh activation of an older version.

        A rollback with nothing to roll back to is an error rather than a no op,
        for the same reason: a silent no-op is how an operator comes to believe a
        bad prompt was reverted while it is still live.
        """
        history = self._history.get(name, [])
        if len(history) < 2:
            raise ValueError(f"no previous version to roll back to for {name!r}")
        live, target = history[-1], history[-2]
        recorded = self.bindings(name)
        if recorded and recorded[-1].outcome == ROLLBACK_OUTCOME and recorded[-1].version == live:
            raise ValueError(
                f"{name!r} is already at {live!r} after a rollback that withdrew "
                f"{target!r}; a second consecutive rollback would re-activate the "
                f"version just withdrawn. Call activate() with the version you want."
            )

        approving = self.approving_run(name, target)
        history.append(target)
        self._active[name] = target
        binding = GateBinding(
            prompt_name=name,
            version=target,
            content_hash=self.content_hash(name, target),
            eval_run_id=approving.eval_run_id if approving else NO_APPROVING_RUN,
            outcome=ROLLBACK_OUTCOME,
            timestamp=utc_now(),
            metrics={"actor": actor, "reason": reason or "", "rolled_back_from": live},
        )
        self._bindings.append(binding)
        if self.audit is not None:
            self.audit.append(
                "prompt_rollback",
                binding.eval_run_id,
                {
                    "prompt_name": name,
                    "actor": actor,
                    "reason": reason or "",
                    "from_version": live,
                    "from_hash": self.content_hash(name, live),
                    "to_version": target,
                    "to_hash": binding.content_hash,
                    "approved_by": binding.eval_run_id,
                },
            )
        self._save()
        return target

    def history(self, name: str) -> list[str]:
        return list(self._history.get(name, []))

    # --- gate bindings ----------------------------------------------------- #

    def record_gate_outcome(
        self,
        prompt_name: str,
        version: str,
        eval_run_id: str,
        outcome: str,
        metrics: dict | None = None,
    ) -> GateBinding:
        binding = GateBinding(
            prompt_name=prompt_name,
            version=version,
            content_hash=self.content_hash(prompt_name, version),
            eval_run_id=eval_run_id,
            outcome=outcome,
            timestamp=utc_now(),
            metrics=dict(sorted((metrics or {}).items())),
        )
        self._bindings.append(binding)
        self._save()
        return binding

    def bindings(self, prompt_name: str | None = None) -> list[GateBinding]:
        rows = [
            binding
            for binding in self._bindings
            if prompt_name is None or binding.prompt_name == prompt_name
        ]
        return rows

    def approving_run(self, prompt_name: str, version: str | None = None) -> GateBinding | None:
        """The most recent run whose gate PASSED for this prompt version.

        None is a real answer and means the version in question was never
        approved by a green gate, which is exactly the situation worth knowing
        about before a postmortem.
        """
        version = version or self.active_version(prompt_name)
        target = self.content_hash(prompt_name, version)
        for binding in reversed(self._bindings):
            if (
                binding.prompt_name == prompt_name
                and binding.content_hash == target
                and binding.outcome == "passed"
            ):
                return binding
        return None

    def describe(self) -> list[str]:
        lines = []
        for name in self.library.names():
            active = self.active_version(name)
            approving = self.approving_run(name, active)
            approved = approving.eval_run_id if approving else "NEVER APPROVED BY A GREEN GATE"
            lines.append(
                f"{name:<8} active {active:<4} "
                f"hash {self.content_hash(name, active)[:12]}  "
                f"versions {','.join(self.versions(name)):<10} "
                f"approved by {approved}"
            )
        return lines
