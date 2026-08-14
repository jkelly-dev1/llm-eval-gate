"""Prompt assets are files with content hashes, not string literals.

Every asset lives at eval_gate/prompts/<name>.<version>.md with a small
frontmatter header. The loader records a sha256 of the body, and that hash is
what registry.py versions and what the audit trail records.

Hashing the body rather than the whole file matters: the frontmatter is
metadata, so an editorial change to the header should not read as a change to
the prompt the model saw. Iteration is sorted() on the directory glob and on
the asset dict, because the loaded set is walked when computing a manifest hash
and an unsorted walk would make identical prompt sets hash differently on
different filesystems.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class PromptAsset:
    name: str
    version: str
    body: str
    content_hash: str
    path: str

    @property
    def key(self) -> str:
        return f"{self.name}.{self.version}"


def content_hash(body: str) -> str:
    """sha256 of the prompt body. The version identifier of record."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _parse(path: Path) -> PromptAsset:
    text = path.read_text(encoding="utf-8")
    name = version = None
    body = text
    if text.startswith("---"):
        _, header, body = text.split("---", 2)
        for line in header.strip().splitlines():
            if ":" in line:
                field, _, value = line.partition(":")
                field, value = field.strip(), value.strip()
                if field == "name":
                    name = value
                elif field == "version":
                    version = value
    # Fall back to the filename (<name>.<version>.md) when the header is
    # incomplete, so a missing header degrades to a warning rather than a crash.
    stem = path.name[: -len(".md")] if path.name.endswith(".md") else path.stem
    file_name, _, file_version = stem.partition(".")
    body = body.strip()
    return PromptAsset(
        name=name or file_name,
        version=version or file_version,
        body=body,
        content_hash=content_hash(body),
        path=str(path),
    )


class PromptLibrary:
    """All prompt assets on disk, keyed by "<name>.<version>"."""

    def __init__(self, directory: Path | str = PROMPTS_DIR) -> None:
        self.dir = Path(directory)
        self._assets: dict[str, PromptAsset] = {}
        for path in sorted(self.dir.glob("*.md")):
            asset = _parse(path)
            self._assets[asset.key] = asset

    def keys(self) -> list[str]:
        return sorted(self._assets)

    def names(self) -> list[str]:
        return sorted({asset.name for asset in self._assets.values()})

    def versions(self, name: str) -> list[str]:
        return sorted(
            asset.version for asset in self._assets.values() if asset.name == name
        )

    def get(self, name: str, version: str | None = None) -> PromptAsset:
        version = version or (self.versions(name) or [""])[-1]
        try:
            return self._assets[f"{name}.{version}"]
        except KeyError as exc:
            raise KeyError(f"no prompt asset {name!r} version {version!r}") from exc

    def hashes(self) -> dict[str, str]:
        """Asset key -> content hash, sorted, for the audit trail."""
        return {key: self._assets[key].content_hash for key in sorted(self._assets)}

    def manifest_hash(self) -> str:
        """One hash covering every asset, so a run pins the whole prompt set."""
        joined = "\n".join(f"{key}={value}" for key, value in sorted(self.hashes().items()))
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def render_judge_user_prompt(
    *,
    case_id: str,
    sut_version: str,
    question: str,
    source: str,
    answer: str,
    attempt: int,
) -> str:
    """Render the user half of a judge call.

    The block markers are load bearing. Offline judges parse this string back
    apart to reach the question, source, and answer, which is slightly ugly but
    keeps the seam honest: every judge, mock or real, is handed exactly the same
    prompt string, so there is no second code path that only the mock sees.

    ATTEMPT is included because judge non-determinism cannot be configured away
    on Opus 5 or Sonnet 5 (no temperature parameter, HTTP 400 if you send one).
    A real panel varies across repeats on its own. The offline panel would not,
    so the repeat index is put in the prompt where the deterministic judges can
    react to it. That makes the self consistency machinery exercisable offline
    while keeping every run reproducible: repeat 2 of case gc-013 is always the
    same call with the same answer.
    """
    return (
        f"CASE_ID: {case_id}\n"
        f"SUT_VERSION: {sut_version}\n"
        f"ATTEMPT: {attempt}\n"
        f"\nQUESTION:\n{question.strip()}\n"
        f"\nSOURCE:\n{source.strip()}\n"
        f"\nCANDIDATE ANSWER:\n{answer.strip()}\n"
    )


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail", "abstain"]},
        "criteria": {
            "type": "object",
            "properties": {
                "answers_the_question": {"type": "boolean"},
                "grounded_in_source": {"type": "boolean"},
                "no_invented_numbers": {"type": "boolean"},
                "well_formed": {"type": "boolean"},
            },
            "required": [
                "answers_the_question",
                "grounded_in_source",
                "no_invented_numbers",
                "well_formed",
            ],
            "additionalProperties": False,
        },
        "reasons": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "criteria", "reasons"],
    "additionalProperties": False,
}
