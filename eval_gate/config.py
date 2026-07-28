"""Configuration. Every default is offline and deliberately conservative.

The thresholds here are the numbers a release manager would argue about in a
review, so they are named and settable rather than buried in the gate. Two of
them carry more weight than the rest:

`gate_max_pass_rate_drop` is the regression threshold, and it is only
meaningful relative to the panel's measured flip rate. The gate refuses to run
when the threshold is inside the noise, so this value cannot be tightened
without first making the judges more consistent.

`judge_repeats` is what makes that measurement possible at all. Claude Opus 5
and Claude Sonnet 5 do not accept a temperature parameter (it returns HTTP
400), so judge non-determinism cannot be configured away. It has to be
measured, and measuring it means running the same case more than once.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_file() -> str:
    """Support ENV_FILE=~/.secrets/ai.env so keys live outside the repo."""
    return os.path.expanduser(os.environ.get("ENV_FILE", ".env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Provider ------------------------------------------------------------
    # "mock" (default, offline, deterministic), "anthropic", or "openai".
    # A provider name without its matching key falls back to the mock, so the
    # demo, the tests, and the gate never depend on the network.
    agent_provider: str = "mock"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    # The second Anthropic judge slot. Two models from one vendor are expected
    # to correlate, which is exactly why calibration.py reports pairwise error
    # correlation instead of assuming independence.
    anthropic_model_secondary: str = "claude-sonnet-5"
    # gpt-5.6-terra is the current third voting slot: 2.50/15.00 per MTok and a
    # 1.05M context. Its input price matches gpt-4o's, so this is a model upgrade
    # at no extra input cost. gpt-5 (1.25/10.00, 400K context) is cheaper on
    # paper and is deliberately NOT the default: OpenAI's own docs mark it
    # superseded and recommend the GPT-5.6 line, and a gate should not be built
    # on a model the vendor has moved off.
    openai_model: str = "gpt-5.6-terra"
    agent_model: str | None = None
    max_output_tokens: int = 1200

    def model_for(self, provider: str) -> str:
        if self.agent_model:
            return self.agent_model
        return {
            "anthropic": self.anthropic_model,
            "openai": self.openai_model,
        }.get(provider, self.anthropic_model)

    # --- Panel ---------------------------------------------------------------
    # 1 or 3. Single judge mode stays supported so the harness can answer
    # "did the panel earn its 3x cost" with a measurement rather than a hope.
    judge_panel_size: int = 3
    # "honest", "one_miscalibrated", or "two_miscalibrated". The miscalibrated
    # modes are offline fixtures for the two headline demonstrations.
    judge_panel_mode: str = "honest"
    # Repeats of every case, used only to measure self consistency.
    judge_repeats: int = 3

    # --- Shadow judges -------------------------------------------------------
    # Shadow judges are scored exactly like voting judges and are excluded from
    # the vote, from unanimity, from split detection, and from the gate. They
    # exist to answer "would a cheaper judge have agreed with the humans just as
    # often" without letting the answer change a build outcome. Default on;
    # turn them off for a cost sensitive run.
    shadow_judges: bool = True
    #: Comma separated model ids for the shadow slots, in order.
    shadow_judge_models: str = "gpt-5.6-luna,gpt-4o"

    def shadow_models(self) -> list[str]:
        return [item.strip() for item in self.shadow_judge_models.split(",") if item.strip()]

    # --- The real model measurement pass -------------------------------------
    # Sizes for the one section of the demo that calls a live model. They are
    # settings rather than constants because this is the only pass in the project
    # that spends money, and an operator sizing a paid run should not have to edit
    # source to do it. Defaults are chosen so the numbers are defensible: a kappa
    # measured over the full 30 case golden set for both sut versions is worth
    # quoting, and one measured over 10 cases is not.
    real_pass_cases: int = 30
    # The consistency subset. Fewer cases at more repeats, because a flip rate
    # needs repeats and repeats are what multiply the invoice. 8 cases at 3
    # repeats costs less than 30 at 2 and says more.
    real_pass_repeat_cases: int = 8
    real_pass_repeats: int = 3
    # "prefix" (the whole set, by case id) or "discriminating" (only cases the two
    # sut versions are labeled differently on). A REDUCED pass wants the second:
    # the first eight cases by id all pass in both versions, so an 8 case prefix
    # run measures kappa 1.000 for every judge and separates nothing. See
    # real_pass.discriminating_cases, which exists because that run was paid for.
    real_pass_case_selection: str = "prefix"

    # --- Golden set and versions ---------------------------------------------
    # The two versions every run measures. Both are always scored, because the
    # calibration layer and the vacuous gate metrics need a pair to compare. This
    # is NOT the gate's baseline: the gate compares the candidate's measured pass
    # rate against the committed baseline record in baseline.json.
    baseline_sut_version: str = "sut.v1"
    candidate_sut_version: str = "sut.v2"

    # --- The committed baseline record ---------------------------------------
    # The pass rate a candidate is measured against, in version control so that
    # approving a prompt version is a reviewable diff. See baseline.py.
    baseline_path: str = "baseline.json"
    # Which measured version the gate treats as the candidate. None means "ask
    # the registry which sut version is active", which on a fresh checkout means
    # the version named in the committed baseline record.
    gate_candidate: str | None = None

    # --- Prompts and audit ---------------------------------------------------
    audit_log_path: str = "audit/audit.log.jsonl"
    registry_state_path: str = "audit/registry.json"

    # --- Gate thresholds -----------------------------------------------------
    # The regression threshold, as an absolute drop in panel pass rate between
    # the baseline and the candidate. It must exceed the measured noise floor.
    gate_max_pass_rate_drop: float = 0.15
    # Chance corrected agreement floor per judge. Raw agreement is not usable
    # as a floor on this label set; see calibration.py.
    gate_min_judge_kappa: float = 0.20
    # A false pass ships a regression, so the panel's false pass rate carries a
    # tighter ceiling than its false fail rate.
    gate_max_panel_false_pass_rate: float = 0.30
    gate_max_panel_false_fail_rate: float = 0.40
    gate_max_panel_abstention_rate: float = 0.20
    # Vacuous gate ceilings. A golden set whose cases never discriminate, or a
    # panel that is unanimous on everything, is not a gate.
    gate_max_never_discriminate_rate: float = 0.70
    gate_max_unanimity_rate: float = 0.95
    gate_min_cases: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
