# llm-eval-gate

[![CI](https://github.com/jkelly-dev1/llm-eval-gate/actions/workflows/ci.yml/badge.svg)](https://github.com/jkelly-dev1/llm-eval-gate/actions/workflows/ci.yml)

An eval harness, built as a personal learning project, that gates a deployment on
a panel of three LLM judges and then measures the judges instead of trusting
them. Three judges score a system under test against a four criterion rubric, two
more run in shadow and never vote, and the gate exits 1 when the candidate's pass
rate falls below a committed baseline. The part that carries the argument is the
calibration layer underneath: per judge agreement with human labels, Cohen's
kappa rather than raw agreement, self consistency across repeats, pairwise error
correlation, and a refusal to gate at all on a threshold inside the judges' own
measured noise.

The strongest thing in the repo is a demonstration of its own failure. A panel
whose judges have been captured says ALLOW on a genuine, planted regression while
comparing against a valid baseline recorded by that same panel, and the
calibration layer is the only reason that build goes red. An uncalibrated harness
merges that change with a green tick and a decisive looking ALLOW next to it.

It runs fully offline on five deterministic mock judges and switches to real
models (Anthropic and OpenAI) with two environment variables. Both real model
captures are in [SAMPLE_RUN.md](SAMPLE_RUN.md), verbatim: 460 live judge calls
across `claude-opus-5`, `claude-sonnet-5` and `gpt-5.6-terra` with `gpt-5.6-luna`
and `gpt-4o` in shadow, plus a smaller second run. They found that the panel's
three judges made *completely nested* errors across two vendors, that the
cheapest judge tied the most expensive, and that this repo's own reduced-sweep
advice was wrong; all three are written up with their caveats.

Unless a number is explicitly labeled as coming from those captures, every figure
in this README is from the offline mock panel, and **no offline number here is
evidence about any real model.** The mock bench ranks the five models in nearly
the opposite order to the one the live run measured, which is the point.

The rule this repo follows: no claim without a test. The table below maps each
claim in this README to the test that enforces it.

## The problem it addresses

An LLM judge is a measuring instrument, and nobody calibrates it. A team writes a
rubric, wires a judge into CI, watches the pass rate, and ships on it. The
questions that decide whether that pass rate means anything are never asked: does
this judge agree with a human, does it agree with itself twice in a row, do three
judges fail on the same cases, and is the threshold being enforced larger than
the variance of the judges enforcing it.

The failure mode is not a noisy gate. It is a quiet one. A judge that passes
everything agrees with the humans 80% of the time on this deliberately skewed
golden set, so a harness reporting accuracy reports 0.800 and looks healthy while
being structurally incapable of blocking anything. Cohen's kappa scores the same
judge 0.000. That gap is the whole project.

Five controls, all implemented here:

- **Per judge calibration against human labels.** Agreement, kappa, a confusion
  matrix, and false pass and false fail reported separately against different
  denominators, because one accuracy number hides which of the two failure modes
  you have.
- **A measured noise floor, and a refusal.** Every case is judged K times.
  `claude-opus-5` and `claude-sonnet-5` reject `temperature` (HTTP 400), so judge
  variance cannot be configured away and has to be measured. When the regression
  threshold sits inside that variance the gate refuses to produce a verdict
  rather than producing one it cannot support.
- **Pairwise error correlation.** Three judges cost 3x. If they are wrong on the
  same cases, majority voting bought less than it appears to, and the report says
  so with a ratio against what independence would predict.
- **Vacuous gate detection.** A golden set whose cases give both versions the
  same verdict is not a gate. `cases_that_never_discriminate`, panel kappa and
  panel false pass rate are ceilings the panel can fail, and a named suspicion
  line says which signal fired.
- **A shadow bench and a cost table.** Two cheap judges are scored exactly like
  voting judges and are excluded from the vote, from unanimity, from split
  detection and from every gate threshold, so "would a cheaper judge have agreed
  with the humans just as often" is answerable without betting a release on the
  answer.

Every decision, verdict and gate outcome lands in a hash chained append only log,
and a prompt registry binds prompt content hash to eval run id to gate outcome,
so "which eval run approved the prompt that is live" is a lookup rather than an
archaeology exercise.

**The gate always runs against the deterministic mock panel.** A regression gate
has to be reproducible: the golden set pins which judge disagrees with which
human on which case, and a live model moves those around between runs. Real model
behavior belongs in `SAMPLE_RUN.md` and never in a pass or fail signal for CI. A
gate whose verdict depends on the model of the day is a flaky test with a
governance story attached.

## What it demonstrates

Five gate invocations, verified by exit code:

```
python -m eval_gate.evals.gate                       exit 0  ALLOW, panel healthy,
                                                             sut.v1 0.800 vs baseline 0.800

python -m eval_gate.evals.gate --candidate sut.v2    exit 1  BLOCK, regression,
                                                             0.533 vs 0.800, drop 0.267 > 0.150

python -m eval_gate.evals.gate --panel two_miscalibrated --candidate sut.v2 \
    --baseline baseline.two_miscalibrated.json       exit 1  ALLOW, but panel_healthy NO;
                                                             exit driven by calibration failure

python -m eval_gate.evals.gate --panel two_miscalibrated --candidate sut.v2
                                                     exit 1  NO DECISION (refused);
                                                             baseline not comparable

python -m eval_gate.evals.gate --threshold 0.05      exit 1  NO DECISION (refused);
                                                             inside the 0.100 noise floor
```

The third invocation is the one that matters. The captured panel passes every
case of both versions, so the measured pass rate is 1.000 against a recorded
1.000: a drop of exactly 0.000, `regression_detected` NO, `deployment_decision`
ALLOW, on a regression the human labels call real. The comparison is valid, not
rigged: a team whose panel was captured would have recorded its baseline with
that captured panel, which is why the second baseline file exists. Nothing in the
regression comparison notices, and nothing can, because the measurement it
consumes is worthless. Four calibration checks fail, and that is the only reason
the build is red.

What the offline honest panel measured, and what it refuses to overclaim:

```
per judge kappa against the human labels
  mock-balanced    (stands in for gpt-5.6-terra)    0.925    best single member
  mock-strict      (claude-opus-5)                  0.857
  mock-lenient     (claude-sonnet-5)                0.842
  shadow:mock-literalist  (gpt-4o)                  0.791    non voting
  shadow:mock-verbosity   (gpt-5.6-luna)            0.108    non voting

panel kappa 0.925, which is EXACTLY its best single member's
noise floor 0.100, measured over 3 repeats per case
```

- **The panel did not beat a single judge.** It scored 0.925, the same kappa as
  `mock-balanced` alone to three decimal places, so the 3x spend bought nothing
  at all on this golden set, and the report prints that in capitals rather than
  leaving a `+0.000` in a table for someone to skim past. The claim here is not
  that a panel is better. It is that the harness measures whether the panel
  earned its cost and reports the answer either way, which is a better claim
  than a win would have been.
- **A cheaper judge came within noise of the dearest, on this golden set.** The
  `claude-sonnet-5` slot scored kappa 0.842 against `claude-opus-5`'s 0.857, a
  gap of 0.015 against a measured noise floor of 0.100, so the extra spend is not
  buying measurable agreement here. Read that with honest limit 2 attached: 30
  cases, no confidence interval, and a 0.015 gap is not a resolved finding.
- **The captured panel does not look unanimous, and that was worth measuring.**
  Unanimity falls to 0.733, BELOW the honest panel's 0.867, because the one
  surviving honest judge still disagrees on the cases it fails. A heuristic keyed
  on unanimity stays silent exactly when the panel is captured. The tells that do
  fire are `cases_that_never_discriminate` saturating at 30/30, panel kappa
  collapsing to 0.000, and panel false pass rate hitting 1.000.
- **Abstentions do not vote, and ties do not resolve.** A tie abstains and flags
  the case for a human, because a tie silently resolved toward pass is how a gate
  ships a regression while looking decisive.
- **A shadow judge cannot change a gate outcome.** That is structural rather than
  conventional: shadow verdicts carry a flag, travel on separately named fields
  of the run result, land in a separate list on the report, and aggregation raises
  if one ever reaches the vote.
- **An unreadable judge is an abstention, never a fail.** Coercing a parse
  failure into "fail" would attribute the harness's own bug to the system under
  test and block a release on evidence that does not exist.
- **The gate refuses twice, for two different reasons, and says which.** Once
  when the threshold is inside the noise floor, once when the committed baseline
  does not measure what this run measured. `exit_driver` names the condition, so a
  red build is diagnosable rather than merely red.

## Claims backed by tests

| Claim | Test |
| --- | --- |
| A captured panel says ALLOW on a genuine regression against a valid comparable baseline, and only the calibration layer turns the build red | `tests/test_gate.py::test_two_miscalibrated_judges_capture_the_panel_and_only_calibration_turns_the_build_red` (mutation-checked: strip the calibration checks and the same run exits 0 on the regression) |
| The capture is caught by the discrimination count saturating at 30/30, not by unanimity, and the suspicion line names which signal fired | `tests/test_gate.py::test_cases_that_never_discriminate_is_reported_and_saturates_under_a_captured_panel`, `tests/test_calibration.py::test_the_suspicion_line_names_which_vacuous_gate_signal_fired` (mutation-checked: revert the heuristic to its unanimity only form and it goes silent under total capture) |
| Raw agreement flatters a judge that cannot fail anything; kappa is what exposes it | `tests/test_calibration.py::test_raw_agreement_flatters_a_judge_that_always_answers_pass` (mutation-checked: read raw agreement instead of kappa against the same floor and the constant pass judge is admitted to the panel) |
| The report says so when the panel does not beat its best single member | `tests/test_calibration.py::test_the_report_says_so_when_the_panel_does_not_beat_its_best_member` |
| The honest panel gating its own committed baseline exits 0, ALLOW, 0.800 vs 0.800 | `tests/test_gate.py::test_the_honest_panel_gating_its_own_committed_baseline_exits_zero` |
| The planted regression is caught, blocked, and exits 1: 0.533 vs 0.800, a 0.267 drop past a 0.150 threshold | `tests/test_gate.py::test_the_honest_panel_catches_the_sut_v2_regression_and_exits_one` |
| A baseline recorded by a different panel produces no deployment decision at all | `tests/test_gate.py::test_a_baseline_from_another_panel_produces_no_deployment_decision` (mutation-checked: stub comparability to report no problems and the gate manufactures ALLOW from an incomparable baseline) |
| A threshold inside the measured noise floor is refused rather than enforced | `tests/test_gate.py::test_a_threshold_inside_the_measured_noise_floor_is_refused` (mutation-checked: bypass the noise floor guard and the same 0.050 threshold is accepted at exit 0 beside a printed 0.100 flip rate) |
| One miscalibrated judge is outvoted, the regression is still caught, and the bad judge is named in the same run | `tests/test_gate.py::test_one_miscalibrated_judge_is_outvoted_and_the_gate_still_catches_it` |
| Toggling the shadow bench off leaves every gate outcome and exit code identical, on all five invocations | `tests/test_gate.py::test_toggling_shadow_judges_off_leaves_every_gate_outcome_and_exit_code_identical` |
| A shadow verdict can never reach the vote, and the fields it travels on are not the fields the gate reads | `tests/test_panel.py::test_a_shadow_judge_never_appears_in_the_panel_vote`, `::test_shadow_results_travel_on_a_field_the_gate_never_reads`, `tests/test_gate.py::test_the_run_result_the_gate_is_handed_keeps_shadow_verdicts_off_every_voting_field` |
| A tie abstains and flags the case for human escalation | `tests/test_panel.py::test_a_tie_abstains_and_flags_the_case_for_human_escalation` (mutation-checked: resolve ties toward pass and the escalation premise raises) |
| Two of three votes carries the panel, and the record keeps the disagreement it resolved | `tests/test_panel.py::test_two_of_three_votes_carries_the_panel`, `::test_the_panel_record_keeps_the_disagreement_it_resolved`, `::test_the_panel_record_carries_the_case_it_judged` |
| An abstaining judge does not vote, and a panel reduced to one voter abstains instead of reporting a verdict | `tests/test_panel.py::test_an_abstaining_judge_does_not_vote`, `::test_fewer_than_two_voting_judges_abstains_the_panel` |
| No judges ran is an error, not a verdict | `tests/test_panel.py::test_an_empty_panel_is_an_error_rather_than_a_verdict` |
| Fenced or prose wrapped judge output still parses | `tests/test_judge.py::test_a_verdict_wrapped_in_code_fences_is_still_parsed`, `::test_a_verdict_with_prose_before_the_json_is_still_parsed` |
| Unreadable judge output abstains instead of raising, and a verdict outside the contract is not coerced toward pass or fail | `tests/test_judge.py::test_unparseable_output_becomes_an_abstention_rather_than_an_exception` (mutation-checked: swap the tolerant parser for a strict `json.loads` and the judge call raises), `::test_a_verdict_outside_the_contract_becomes_an_abstention` |
| A refusal or a content filtered response is an abstention, not a crash, in either vendor's spelling | `tests/test_judge.py::test_a_refusal_stop_reason_becomes_an_abstention`, `::test_a_content_filtered_openai_response_becomes_an_abstention` |
| A broken judge and a cautious judge do not look the same in the report | `tests/test_judge.py::test_an_abstention_from_a_broken_parse_carries_raw_ok_false` |
| Two judges of the same kind do not merge into one row | `tests/test_judge.py::test_a_judge_verdict_records_the_slot_it_was_asked_for` |
| Cohen's kappa is 0.000 for chance level agreement and 1.000 for a perfect judge on a mixed label set | `tests/test_calibration.py::test_cohens_kappa_is_zero_for_a_judge_that_agrees_only_by_chance`, `::test_cohens_kappa_is_one_for_a_perfect_judge` |
| False pass and false fail are reported separately, against different denominators | `tests/test_calibration.py::test_false_pass_and_false_fail_are_reported_separately` |
| The flip rate is 0.000 for a deterministic judge and rises for one that changes its mind, in the same unit as the gate's threshold | `tests/test_calibration.py::test_flip_rate_is_zero_for_a_deterministic_judge`, `::test_flip_rate_rises_for_a_judge_that_changes_its_mind` |
| Correlated errors are reported as correlated, and independent ones as independent | `tests/test_calibration.py::test_error_correlation_is_high_for_two_judges_with_the_same_bias`, `::test_error_correlation_is_near_independence_for_unrelated_biases` |
| An abstention is excluded from kappa and counted on its own | `tests/test_calibration.py::test_an_abstention_is_excluded_from_kappa_and_counted_on_its_own` |
| Provider selection needs both the name and the credential; a key alone selects nothing and keys never cross match | `tests/test_llm.py::test_provider_name_without_a_key_falls_back_to_mock`, `::test_a_key_without_the_matching_provider_name_stays_on_mock`, `::test_keys_do_not_cross_match_between_providers`, `::test_the_default_provider_is_the_offline_mock`, `::test_the_offline_settings_carry_no_api_keys` |
| A partially real panel is recorded as degraded, and a fully credentialed one is not | `tests/test_llm.py::test_a_partially_real_panel_is_recorded_as_degraded` (mutation-checked: make `is_mock` always answer False and the degraded flag disappears), `::test_a_panel_with_both_credentials_is_not_flagged_as_degraded` |
| The three offline judges genuinely disagree, and each one both passes and fails somewhere | `tests/test_llm.py::test_an_offline_panel_holds_three_distinct_judges_rather_than_three_clones`, `::test_the_three_offline_judges_do_not_all_agree_on_every_case`, `::test_each_offline_judge_both_passes_and_fails_somewhere` |
| The offline shadow bench is the size its configuration asks for, and every slot is a genuinely distinct behavior | `tests/test_llm.py::test_the_offline_shadow_bench_is_the_size_its_configuration_asks_for` (mutation-checked: restore the `or shadow_mock_judges()` fallback, or slice the bench to a fixed two, and it fails), `::test_every_offline_shadow_judge_is_a_genuinely_distinct_behavior` (mutation-checked: pad the bench with a repeat of the first behavior and distinctness stops holding), `::test_the_shadow_bench_falls_back_to_mocks_without_a_credential` |
| A three judge panel bills three times, and the non voting shadow bench bills too | `tests/test_cost.py::test_the_panel_costs_more_than_a_single_judge`, `::test_the_shadow_bench_is_billed_even_though_it_does_not_vote` |
| Every model a run could bill against has a price entry, and every offline mock prices against the real model it stands in for | `tests/test_cost.py::test_the_cost_table_covers_every_configured_judge_model` (mutation-checked: delete `gpt-5.6-terra` from `PRICES` and the coverage premise stops holding), `::test_every_offline_mock_judge_prices_against_a_real_stand_in_model` |
| A model with no price entry raises rather than costing nothing | `tests/test_cost.py::test_an_unknown_model_refuses_to_be_priced_at_zero` (mutation-checked: restore the `PRICES.get(model, (0.00, 0.00))` fallback in `price_for` and it fails) |
| An unpriced judge prints NO PRICE ON FILE with no dollar figure, and never wins the cheapest comparison | `tests/test_cost.py::test_a_report_row_for_an_unpriced_judge_says_no_price_on_file_not_zero_dollars` (mutation-checked: give the unknown model a `(0.00, 0.00)` entry and the marker becomes a figure that reads as free), `::test_the_cost_accuracy_table_never_calls_an_unpriced_judge_the_cheapest` (mutation-checked: price the unknown model at zero and it becomes the cheapest row) |
| A dated introductory price expires on its stated date rather than on a default that stays true | `tests/test_cost.py::test_the_sonnet_introductory_price_expires_on_its_stated_date` (mutation-checked: change the `sonnet_intro` default back to True and it fails) |
| Pricing more judges than there are slots raises instead of wrapping around and billing the wrong models | `tests/test_cost.py::test_pricing_more_judges_than_slots_is_an_error_not_a_wraparound` (mutation-checked: restore the modulo index in `priced_as_real` and it fails) |
| Offline token counts are labeled as the characters/4 approximation they are | `tests/test_cost.py::test_an_offline_cost_report_labels_its_token_counts_as_approximate` |
| Two offline runs produce identical verdicts and identical calibration numbers | `tests/test_determinism.py::test_two_offline_runs_produce_identical_verdicts`, `::test_two_offline_runs_produce_identical_calibration_numbers` |
| The run id is derived from what the run is rather than from a clock | `tests/test_determinism.py::test_the_run_id_is_derived_from_the_run_rather_than_from_a_clock` |
| Two runs produce the same audit trail apart from its timestamps, which are excluded rather than frozen | `tests/test_determinism.py::test_two_offline_runs_produce_the_same_audit_trail_apart_from_its_timestamps`, `tests/test_audit.py::test_the_audit_hash_is_stable_across_key_order` |
| The golden set skew the whole kappa argument depends on cannot be quietly balanced away | `tests/test_determinism.py::test_the_golden_set_label_distribution_is_deliberately_skewed` |
| Editing, reordering or removing an audit record is detectable | `tests/test_audit.py::test_the_audit_chain_detects_a_tampered_record` (mutation-checked: drop `prev_hash` from the hashed payload and an excised record goes undetected) |
| A CI gate run writes no records into the committed audit directory | `tests/test_gate.py::test_the_gate_writes_no_audit_records_into_the_committed_audit_directory` |
| A prompt version is identified by the hash of its body, so editing it in place does not inherit the old approval | `tests/test_registry.py::test_a_prompt_version_is_identified_by_its_content_hash`, `::test_editing_a_prompt_produces_a_new_version` |
| A rollback is appended to the history rather than popped from it, and is itself a recorded decision with an actor and a reason | `tests/test_registry.py::test_a_rollback_is_recorded_rather_than_erased` (mutation-checked: restore `history.pop()` in `PromptRegistry.rollback` and it fails), `::test_rollback_restores_the_previous_active_version` |
| Rolling back does not launder an ungated version into an approved one | `tests/test_registry.py::test_a_rollback_to_a_version_no_green_gate_approved_says_so` |
| Which eval run gated which version is a lookup, and a consciously accepted regression is not recorded as an approval | `tests/test_registry.py::test_the_registry_records_which_eval_run_gated_which_version` |
| Recording a baseline is refused when the panel is unhealthy, when the run was refused, or when the audit chain does not verify | `tests/test_registry.py::test_record_baseline_is_refused_when_the_panel_is_unhealthy`, `::test_record_baseline_is_refused_when_the_run_itself_was_refused`, `::test_record_baseline_is_refused_when_the_audit_chain_is_broken` |
| A fresh checkout takes its active version from the committed baseline record instead of the highest version on disk | `tests/test_registry.py::test_a_fresh_checkout_takes_its_active_version_from_the_committed_baseline_record` |
| Nothing the real model pass measures can reach a gate decision, and the demo's exit code is identical with it running and with it skipped | `tests/test_real_pass.py::test_nothing_the_real_pass_measures_can_reach_a_gate_decision` (mutation executed in-test: the same report handed to `evaluate_gate` fails four calibration checks), `::test_the_demo_exit_code_is_the_same_with_the_real_pass_running_as_without_it` (mutation-checked: return non-zero when the pass records an incident and the two exit codes diverge) |
| A real judge that errors, refuses or times out becomes a recorded abstention and the paid run carries on | `tests/test_real_pass.py::test_a_judge_that_raises_becomes_a_recorded_abstention_rather_than_an_exception` (mutation-checked: let the exception out of `GuardedJudge.complete` and the run dies mid pass), `::test_a_call_that_raised_is_not_billed_for_tokens_it_never_consumed` |
| The call count and dollar estimate printed before the first call are the arithmetic of the pass that follows, and computing them costs nothing | `tests/test_real_pass.py::test_the_call_count_arithmetic_is_the_calls_the_judges_actually_receive` (asserted against each judge's own call log), `::test_the_dollar_estimate_is_priced_arithmetic_and_never_costs_a_network_call` (mutation-checked: drop `approximate_only` and the preflight calls `count_tokens` 300 times to price a 460 call run) |
| A reduced sweep buys cases that separate the two sut versions, in both directions, and the pass sends exactly those cases | `tests/test_real_pass.py::test_a_reduced_sweep_of_discriminating_cases_separates_the_two_sut_versions` (mutation executed in-test: the same size taken as a prefix is gc-001..gc-008, which pass in both versions and separate nothing), `::test_the_plans_case_selection_is_the_cases_the_judges_are_actually_sent` (asserted against each judge's own call log, and the printed heading lists the ids rather than a range that skips cases) |
| Asking for more cases than a selection holds raises rather than quietly measuring fewer | `tests/test_real_pass.py::test_asking_for_more_cases_than_the_golden_set_holds_raises_rather_than_measuring_fewer` |
| Repeat 1 of a consistency case is bought once and read by both passes | `tests/test_real_pass.py::test_repeat_one_of_a_consistency_case_is_bought_once_and_read_twice` (mutation-checked: drop `first_repeat` and every consistency unit is scored at attempt 1 twice) |
| Real token counts come from the vendor's own accounting, and a slot that reports none is labeled approximate rather than measured | `tests/test_real_pass.py::test_the_measured_tokens_come_from_the_vendors_own_accounting`, `::test_a_slot_that_reports_no_usage_is_counted_by_approximation_and_says_so` (mutation-checked: keep `exact` true with no usage block and a guess is printed as a measurement) |
| A cost versus accuracy table on real numbers refuses to mix measured dollars with the offline estimate | `tests/test_real_pass.py::test_a_measured_cost_table_refuses_to_mix_measured_dollars_with_the_estimate` (mutation-checked: fall back to the estimate for an unmeasured judge and the cheapest row is decided by a guess) |
| The `panel degraded` header distinguishes an all real panel from an all mock one from a panel that fell back, and names the slot | `tests/test_real_pass.py::test_the_panel_degraded_header_distinguishes_all_three_panels` (mutation-checked: print the bare `panel_degraded` boolean and a two vendor panel and an offline panel read identically), `::test_the_real_section_names_which_slots_were_real` |
| The real model section is skipped offline and prints the call count the paid run would actually make | `tests/test_real_pass.py::test_the_real_model_section_is_skipped_offline_and_makes_no_judge_call`, `::test_the_skipped_line_quotes_the_call_count_the_paid_run_would_actually_make` (mutation-checked: hardcode 460 and a differently sized plan still prints it) |

## Quickstart

Requires Python 3.11 or newer. CI runs 3.11, 3.12, and 3.13.

```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

pytest -q                          # 98 tests, fully offline
python -m eval_gate.evals.gate     # the CI eval gate
python scripts/run_demo.py         # end to end demo over the whole golden set
```

The four other gate invocations, which are the demonstrations:

```
python -m eval_gate.evals.gate --candidate sut.v2
python -m eval_gate.evals.gate --panel two_miscalibrated --candidate sut.v2 \
    --baseline baseline.two_miscalibrated.json
python -m eval_gate.evals.gate --panel two_miscalibrated --candidate sut.v2
python -m eval_gate.evals.gate --threshold 0.05
```

`SAMPLE_RUN.md` holds a verbatim capture of the demo and of all five gate
invocations with their exit codes.

## How a run flows

```
golden set (30 synthetic cases, human labeled, 24 pass / 6 fail on sut.v1)
        |
        v
  two sut versions scored in the same pass    a drop between them cannot be an
        |                                     artifact of two panels or two days
        v
  3 voting judges x 30 cases x 2 versions x 3 repeats = 540 judged units
  + 2 shadow judges on separate fields the gate never reads
        |
        v
  tolerant parser        fences and prose stripped; a refusal, a content filter
        |                or an unreadable body becomes abstain, never fail
        v
  panel aggregation      abstentions do not vote; a tie abstains and escalates;
        |                a shadow verdict raises rather than being counted
        v
  calibration            kappa per judge and for the panel, confusion matrix,
        |                flip rate per judge, pairwise error correlation,
        |                cases_that_never_discriminate, unanimity, cost
        v
  refuse?                threshold inside the measured noise floor,  --> NO DECISION
        |                or a baseline that measured something else      exit 1
        v
  regression?            candidate pass rate vs the COMMITTED baseline --> BLOCK
        |                record in baseline.json                          exit 1
        v
  panel healthy?         kappa floors, false pass ceiling, abstention  --> exit 1
        |                ceiling, vacuous gate ceilings                   ALLOW is
        v                                                                still printed
  exit 0 only when the panel is healthy AND nothing regressed
        |
        v
  hash chained audit record + registry binding (prompt hash, run id, outcome)
```

Two things about that shape are deliberate. The comparison is against a file in
version control rather than against sut.v1 measured in the same run: the golden
set carries a planted regression, so an in run comparison would report one every
time and CI would be permanently red, while comparing the active version against
itself is a gate that can never fail. And the exit code follows both questions,
because an earlier version of this module printed `deployment_decision BLOCK` and
then exited 0, so CI went green while the harness said block.

## Real models

The harness switches off the offline mock when `AGENT_PROVIDER` is set to
`anthropic` or `openai` and the matching API key is present:

```
ENV_FILE=~/.secrets/ai.env AGENT_PROVIDER=anthropic python scripts/run_demo.py
ENV_FILE=~/.secrets/ai.env AGENT_PROVIDER=openai    python scripts/run_demo.py
ENV_FILE=~/.secrets/ai.env AGENT_PROVIDER=openai    OPENAI_MODEL=gpt-5.6-terra python scripts/run_demo.py
```

Selection requires both the provider name and its credential; anything else falls
back to the mock, so the tests, the demo and the gate never touch the network.
Both SDKs are imported lazily and are not loaded at all in mock mode. Credentials
come from the environment, a gitignored `.env`, or an `ENV_FILE` pointing at a
private file outside the repository.

With a provider configured, the demo runs one extra section and only one:
**section 11, the real model measurement pass.** It is the only section that
calls a live model and the only section whose numbers can never reach an exit
code. Sections 4 through 10 stay on the deterministic mock panel whatever
`AGENT_PROVIDER` says, because a regression gate has to be reproducible and a
live model moves its own numbers between runs.

The pass goes through the same `judge_case` and `run_panel` seam the offline
sections use, so there is no second measurement path that only real models take.
It sweeps the whole 30 case golden set once for both sut versions (a kappa over
60 judgments is worth quoting; one over ten cases is not), then re-scores the
first 8 cases by case id at three repeats to measure a real flip rate. Repeat 1
of those cases is reused from the first sweep rather than bought twice. That is
460 API calls across the three voting and two shadow judges, and the run prints
that count plus a dollar estimate **before it makes the first call**;
`--real-cases`, `--real-repeat-cases` and `--real-repeats` size it down.

**A reduced sweep needs `--real-case-selection discriminating`, and that flag
exists because a paid run proved it.** The first eight cases by id are grounded
and pass in both sut versions, so an 8 case prefix measured kappa 1.000 for every
judge and a 100% pass rate on both versions: a capture of the project's own
`cases_that_never_discriminate` warning rather than a result. The discriminating
selection takes only cases the two versions are labeled differently on, and
alternates the two directions so the subset holds both a regression and a fix
instead of eight failures pointing the same way. A judge that answers "fail" to
everything cannot score well on it.

A judge
that errors, refuses or times out is recorded as an abstention with the reason and
the pass carries on, because aborting call 401 of 460 throws away a paid
measurement. Tokens and dollars are reported from the vendor's own usage
accounting, next to the offline characters/4 estimate for the same calls, so the
capture shows how far the approximation was off.

The voting slots default to `claude-opus-5`, `claude-sonnet-5` and
`gpt-5.6-terra`; the shadow slots to `gpt-5.6-luna` and `gpt-4o`. Two Anthropic
slots and one OpenAI slot is a genuine three model, two vendor panel only when
both keys are present; with one vendor's key the third slot falls back to a mock,
and the `panel degraded` header says so and names the slot rather than printing a
boolean that reads the same for an all real panel and an all mock one. Two models from
one vendor are expected to correlate, which is exactly why the report measures
pairwise error correlation instead of assuming independence. `claude-opus-5` and
`claude-sonnet-5` reject `temperature`, `top_p` and `top_k` with HTTP 400, so
there is no determinism knob to set and `JUDGE_REPEATS` is how the variance gets
measured instead.

### What the real runs actually found

Two captures, 460 and 160 live calls, $1.5576 and $0.5320 measured. Full output
and the caveat on each finding are in
[SAMPLE_RUN.md](SAMPLE_RUN.md#what-the-real-model-runs-found); the short version
is that most of what they found is a negative result about this design:

- **The three voting judges' errors were completely nested, across two vendors.**
  `claude-opus-5` and `gpt-5.6-terra` were wrong on identically the same four of
  60 units, and `claude-sonnet-5`'s three errors were a subset of those four. No
  judge was ever wrong alone. Majority voting cannot correct an error all three
  make, which is why the panel scored kappa 0.857, exactly its two weaker
  members', and lost to `claude-sonnet-5` alone at 0.892 while costing 1.61x.
  Read it with the caveat: exact nesting, over four errors.
- **The cheapest judge tied the most expensive.** `gpt-5.6-luna` at $0.0952 per
  sweep scored the same kappa as `claude-opus-5` at $0.8130, and the best judge
  was the mid-priced `claude-sonnet-5`. The offline mock bench ranks those models
  in nearly the opposite order, which is the clearest possible demonstration that
  no offline number here is evidence about a real model.
- **The measured noise floor was 0.000** on both runs, including on a subset
  chosen to be harder, so the flip rate the gate refuses to go below is a real
  measurement rather than an artifact of easy cases. The non voting shadows did
  flip, up to 0.125.
- **`characters/4` undercounts Anthropic prompts by 2.35x** and is accurate on
  OpenAI, for a total estimate to measured ratio of 1.51 reproduced across both
  runs. That is why the offline dollar figure is labeled an approximation
  everywhere it appears.

Every model id and price in `eval_gate/cost.py` was checked against the vendor's
own documentation and against the account's model list (`client.models.list()`
on the OpenAI SDK), not against a search result. While these defaults were being
chosen, four aggregator pages returned four mutually contradictory price tables,
and the one that was wrong about prices was right about the naming. That is the
trap: a source which is neither reliably right nor reliably wrong is not evidence
in either direction. An account's own model list cannot be out of date or SEO
poisoned, so it is the authority here. `price_for` raises rather than defaulting,
so a model that is renamed or added without a price entry fails loudly instead of
being reported as free.

## Honest limits

1. **The human labels are synthetic, which caps every calibration claim.** The 30
   golden cases carry labels written by the author to look like plausible human
   judgment, including four tagged ambiguous where reasonable reviewers would
   disagree. They were not collected from annotators, so there is no measured
   inter annotator agreement. This is the ceiling on the whole project: you cannot
   calibrate a judge better than the labels you calibrate it against. A kappa of
   0.857 means the judge agrees with one author's opinion, not with human
   consensus. Read every number in the calibration report as "agreement with this
   golden set", never as "accuracy". A real version needs two or more independent
   annotators per case, a published inter annotator kappa, and adjudication of the
   cases they split on, which is a data collection project rather than a code
   change.
2. **Kappa on 30 cases has no confidence interval, and small gaps are noise.** The
   harness prints kappa to three decimals and does not bootstrap an interval. On
   30 cases that interval is wide. The headline cost finding, that
   `claude-sonnet-5` at 0.842 is within noise of `claude-opus-5` at 0.857, rests
   on a 0.015 gap an interval would very likely swallow whole. The report does
   compare that gap against the measured noise floor rather than treating it as
   meaningful, which is the honest half; quoting three decimals as if they were
   resolved would be the dishonest half. Read the leading digit. Worth doing:
   bootstrap an interval and print kappa as one. Until then, treat any kappa
   difference smaller than the noise floor as absent.
3. **The offline judges are hand written mocks, not recorded real responses.** The
   gate, the tests and every number in the offline path come from five
   deterministic mock judges with deliberately different biases. They exist so the
   gate is reproducible, because a live model moves verdicts around between runs
   and a regression gate that flakes is a governance story attached to a coin
   flip. They do not show that real judges behave similarly. They show that the
   harness measures whatever judges it is given. The real model evidence lives in
   `SAMPLE_RUN.md` and nowhere else, and the two must never be conflated.
4. **One domain, one rubric, 30 cases.** Synthetic grounded answer cases over
   synthetic Acme source snippets, judged against a four criterion rubric. Nothing
   here shows the design transfers to summarization, code review, tool use traces
   or open ended writing, where "is this answer grounded in the source" is not the
   question. A different domain needs a different rubric and a fresh calibration
   run, and the second is the expensive part.
5. **The noise floor is estimated from three repeats.** The flip rate the gate
   refuses to go below is measured over K=3 repeats per case. Three is enough to
   prove the mechanism and thin as an estimate of a rate. A serious deployment
   would use more repeats and would recompute the floor whenever the judge model
   or the prompt changes, because both move it.
6. **The gate compares one aggregate number.** Regression detection is a pass rate
   comparison against a committed baseline. It will not notice a change that
   trades one class of failure for another at a constant pass rate, and it does no
   per slice analysis: a candidate that fixes five grounded cases and breaks five
   ambiguous ones scores as no change. Slice level gating is a seam rather than an
   oversight, and the golden set already carries the tags that would drive it.
7. **Different models may judge different domains better, and that is unstudied.**
   The panel is three fixed judges plus two shadow judges. The repo measures how
   well each agrees with the golden set and how correlated their errors are. It
   does not search for the best judge per domain, and the shadow bench is a
   comparison rather than a selection procedure. Choosing judges per domain on
   measured agreement is the obvious next project and is deliberately out of scope.
8. **On this golden set, the cases that separate the two sut VERSIONS and the
   cases that separate the JUDGES are disjoint, so a reduced sweep cannot measure
   both.** The twelve cases the versions are labeled differently on are the
   blatant ones, invented figures and outright deferrals, and every real model
   scored kappa 1.000 on a sample of them. The cases judges actually get wrong are
   the four tagged ambiguous, where the rounding is arguable, and those are
   labeled the same in both versions so they can never enter a discriminating
   subset. `--real-case-selection discriminating` fixes a reduced sweep that
   measured no version signal at all; it does not and cannot fix this. A reduced
   sweep is not a substitute for the full 30, and a golden set with ambiguous
   cases on *both* sides of a version change would be the real fix.
9. **The shadow bench and the pricing slots disagree about size.** The offline
   shadow bench supports up to four distinct behaviors; the real shadow slots are
   a hardcoded pair. Configuring four shadow judges therefore raises
   `PricingSlotMismatch` rather than mispricing them silently. That is the intended
   loud failure and it is still an inconsistency: the two limits should be the same
   number.
10. **Offline dollar figures are approximations.** Offline token counts are
   characters divided by four, priced against the models each mock stands in for,
   and are labeled as such in the output. Real token counts come from the
   provider's own usage accounting (and `count_tokens` on the Anthropic side, never
   `tiktoken`), and appear only in section 11 of a real run and in `SAMPLE_RUN.md`.
   That section prints both numbers side by side so the size of the gap is on the
   record. Anyone quoting an offline dollar figure as a real cost is quoting an
   estimate of an estimate.
11. **What is deliberately excluded from the determinism claim.** Timestamps and
    run ids differ per run and are excluded rather than frozen; audit hashing is
    made stable by canonical JSON, not by faking the clock. Two audit record counts
    are pinned as literals in the tests (62 for a gate run, 61 for a bare run),
    which is brittle on purpose: if the golden set size changes, those two
    assertions are the first thing to fail, which is the intended alarm.

## Scope

This is a learning project, deliberately small. There is no HTTP API, no
authentication, no persistence beyond the log files, no annotator tooling, one
golden set, one rubric, and one worked domain. The mock judges stand in for a real
panel and the labels stand in for real annotators. Those are seams, not
oversights. Sibling repos follow the same discipline of claims mapped to tests,
mutation checks on the tests that matter, and behavior verified before
publishing:
[citation-abstention-rag](https://github.com/jkelly-dev1/citation-abstention-rag),
[least-privilege-agent](https://github.com/jkelly-dev1/least-privilege-agent),
[agentic-review-gate](https://github.com/jkelly-dev1/agentic-review-gate),
[typed-agent-service](https://github.com/jkelly-dev1/typed-agent-service),
[temporal-multi-agent](https://github.com/jkelly-dev1/temporal-multi-agent).

Two of them apply this repo's finding directly.
[prompt-injection-benchmark](https://github.com/jkelly-dev1/prompt-injection-benchmark) reports pairwise failure correlation
between defenses because of what the judge panel here did: three judges across
two vendors whose errors were completely nested, so majority voting corrected
nothing. [ai-data-boundary-proxy](https://github.com/jkelly-dev1/ai-data-boundary-proxy) measures the marginal gain of a
second PII detector for the same reason, and its real-model run found the
matching trap one layer down -- a neighbor-probe threshold calibrated against
one embedding model separates nothing under another, and reports zeros while
doing it.

## License

MIT
