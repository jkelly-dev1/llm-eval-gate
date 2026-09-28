# Sample run

Verbatim captures of `scripts/run_demo.py`, of all five gate invocations, and of
the real model measurement pass, so a reviewer without an API key can see exactly
what the harness does and what it refuses to do. This file is assembled from
the capture files by `scripts/build_sample_run.py`, and every code block below
is a verbatim slice of one: each kappa, flip rate, dollar figure, hash and exit
code in a block is the one its run produced. The captions between the blocks
come from the build script. The offline run and the five gate invocations were
captured 2026-09-27, the two real model runs 2026-09-26.

The last section, what the real model runs found, is written by hand, not
captured. `scripts/check_sample_run.py` holds every figure written in digits in
it to the capture blocks above: each one appears in a block, or is computed from
figures that do.

Timestamps, run ids and record hashes differ per run and are excluded from the
determinism claim rather than frozen. A faked timestamp in an audit trail is worse
than an honest one that varies, since the whole value of the log is that it says
when a decision was made. Reproducibility comes from canonical JSON with sorted
keys, and `tests/test_determinism.py` pins exactly that boundary: everything in
the trail reproduces except `timestamp`, and consequently the `prev_hash` and
`record_hash` that cover it.

- [Offline run (mock provider)](#offline-run-mock-provider)
- [Real model run (Anthropic primary)](#real-model-run-anthropic-primary)
- [Real model run (OpenAI primary, reduced)](#real-model-run-openai-primary-reduced)
- [What the real model runs found](#what-the-real-model-runs-found)

The gate always runs against the deterministic mock panel: a regression gate
has to be reproducible, the golden set pins which judge disagrees with which
human on which case, and a live model moves those around between runs. Real
model behavior is reported in the two sections below and never gates.

## Offline run (mock provider)

```
python scripts/run_demo.py
```

```

==============================================================================
llm-eval-gate demo
==============================================================================
provider        : mock
voting panel    : mock-strict (mock-deterministic-v1), mock-lenient (mock-deterministic-v1), mock-balanced (mock-deterministic-v1)
shadow bench    : mock-verbosity (mock-deterministic-v1), mock-literalist (mock-deterministic-v1)
panel degraded  : no (3 of 3 slots are offline deterministic mocks, so there is nothing real to degrade)
repeats         : 3
prompt manifest : 8b545e68da22a28e
  judge.v1   eec118bac951245f
  judge.v2   a110e3da9e902761
  sut.v1     c0b9794cc02d705c
  sut.v2     574044117dcc403b

note: the panel below is three DISTINCT deterministic judges, so the
      disagreement is real. Against real models the same golden set
      measures whatever those models actually do.

==============================================================================
0. The committed baseline record
==============================================================================
  file            : baseline.json
  record          : sut.v1 pass_rate 0.800 (honest panel, 3 repeats, prompts 8b545e68da22, run-cd7da0994aad)
  this is the number a candidate is measured against, and it is in version
  control. Comparing sut.v1 against sut.v2 inside one run is a measurement,
  not a gate: the golden set carries a planted regression, so that comparison
  reports one every time and CI would be permanently red. Comparing the
  active version against itself is not a fix either, because a comparison
  that can never fail is the vacuous gate this project warns about.

==============================================================================
1. What is being judged
==============================================================================
golden set      : 30 synthetic cases (Acme, *.example)
  sut.v1   human labels: 24 pass / 6 fail
  sut.v2   human labels: 16 pass / 14 fail
  sut.v2 is genuinely worse by human label. That gap is the regression the
  gate exists to catch, and it is planted rather than hoped for.
  the distribution is skewed on purpose: an all-pass judge scores 0.80 raw
  agreement on the baseline and 0.00 kappa, which is why kappa is reported.

==============================================================================
2. One case, three judges (gc-013, the ambiguous kind)
==============================================================================
question  What was fourth quarter revenue for the retail division?
source    The retail division reported revenue of 412,600,000 USD in the fourth quarter, up from 388,100,000 USD a year earlier.

sut.v1 answer: The retail division reported revenue of about 412.6 million USD in the fourth quarter, up from roughly 388.1 million USD a year earlier.
  human: pass (Rounding to millions preserves both figures exactly; a reader loses nothing.)
  repeat 1: mock-strict=fail  mock-lenient=pass  mock-balanced=fail  ->  panel fail (split=True)
  repeat 2: mock-strict=fail  mock-lenient=pass  mock-balanced=fail  ->  panel fail (split=True)
  repeat 3: mock-strict=fail  mock-lenient=pass  mock-balanced=pass  ->  panel pass (split=True)

sut.v2 answer: Retail division fourth quarter revenue was around 412.6 million USD, compared with about 388.1 million USD a year earlier.
  human: pass (Same rounding, same two figures, still faithful.)
  repeat 1: mock-strict=fail  mock-lenient=pass  mock-balanced=pass  ->  panel pass (split=True)
  repeat 2: mock-strict=fail  mock-lenient=pass  mock-balanced=fail  ->  panel fail (split=True)
  repeat 3: mock-strict=fail  mock-lenient=pass  mock-balanced=pass  ->  panel pass (split=True)

  the strict and lenient judges read the same rubric line differently: one
  demands the literal figure, the other accepts a figure that rounds to it.
  the balanced judge is the swing vote, and its variance across repeats IS
  the panel's noise floor.

==============================================================================
3. A tie is escalated, not resolved
==============================================================================
  votes      abstain,fail,pass
  verdict    abstain
  escalated  True  (abstentions 1)
  one abstention leaves one pass and one fail. The panel does not break the
  tie; it routes the case to a human. Breaking it would manufacture
  confidence the panel does not have.

==============================================================================
4. The honest panel, measured and gated (mock panel)
==============================================================================
Run
  run_id                             run-cd7da0994aad
  panel                              mock-strict (mock-deterministic-v1), mock-lenient (mock-deterministic-v1), mock-balanced (mock-deterministic-v1)
  panel_size                         3
  repeats                            3
  panel_mode                         honest
  panel_degraded                     no
  prompt_manifest_hash               8b545e68da22a28e

Per judge, against human labels
  judge                 agree   kappa  falsePass  falseFail  abstain
  mock-balanced         0.967   0.925      0.050      0.025    0.000
  mock-lenient          0.933   0.842      0.200      0.000    0.000
  mock-strict           0.933   0.857      0.000      0.100    0.000
  PANEL                 0.967   0.925      0.050      0.025    0.000
  raw agreement is misleading on this skewed label set; kappa is the number to read

Shadow judges (measured, NON VOTING, never gate)
  judge                 agree   kappa  falsePass  falseFail  abstain
  shadow:mock-literalist  0.900   0.791      0.000      0.150    0.000
  shadow:mock-verbosity  0.633   0.108      0.700      0.200    0.000
  excluded from the vote, from unanimity, from split detection, and from every gate threshold

Panel
  unanimity_rate                     0.867
  split_rate                         0.133
  escalation_rate                    0.000
  best_single_judge                  mock-balanced (kappa 0.925)
  panel_kappa_vs_best_single_judge   +0.000
  THE PANEL DID NOT BEAT ITS BEST MEMBER: three judges cost 3x the calls and bought nothing measurable here
  pass_rate[sut.v1]                  0.800
  pass_rate[sut.v2]                  0.533
  within_run_delta                   +0.267 (informative only; the gate compares the committed baseline)

Self consistency across repeats
  flip_rate[mock-balanced]           0.100
  flip_rate[mock-lenient]            0.000
  flip_rate[mock-strict]             0.000
  panel_flip_rate                    0.100
  NOISE_FLOOR                        0.100
  gate threshold                     0.150
  temperature is not available on claude-opus-5 or claude-sonnet-5 (HTTP 400), so this variance is measured rather than configured away

Pairwise error correlation
  pair                                       joint   indep   ratio  interpretation
  mock-balanced + mock-lenient               0.017   0.002    7.50  errors correlate; majority voting buys less than it appears to
  mock-balanced + mock-strict                0.017   0.002    7.50  errors correlate; majority voting buys less than it appears to
  mock-lenient + mock-strict                 0.000   0.004    0.00  errors are anti correlated; the pair covers for each other

Pairwise error correlation, pairs touching a shadow judge
  mock-balanced + shadow:mock-literalist     0.017   0.003    5.00  errors correlate; majority voting buys less than it appears to
  mock-balanced + shadow:mock-verbosity      0.017   0.012    1.36  errors correlate; majority voting buys less than it appears to
  mock-lenient + shadow:mock-literalist      0.000   0.007    0.00  errors are anti correlated; the pair covers for each other
  mock-lenient + shadow:mock-verbosity       0.067   0.024    2.73  errors correlate; majority voting buys less than it appears to
  mock-strict + shadow:mock-literalist       0.067   0.007   10.00  errors correlate; majority voting buys less than it appears to
  mock-strict + shadow:mock-verbosity        0.017   0.024    0.68  errors are anti correlated; the pair covers for each other
  shadow:mock-literalist + shadow:mock-verbosity   0.017   0.037    0.45  errors are anti correlated; the pair covers for each other

Vacuous gate metrics
  cases_that_never_discriminate      15/30 (0.500)
  panel_abstention_rate              0.000
  unanimity_rate                     0.867
  suspicion                          no vacuous gate signal fired

Regression, against the committed baseline record
  baseline record                    none recorded
  candidate                          sut.v1
  baseline_pass_rate                 0.800
  candidate_pass_rate                0.800
  drop_vs_baseline                   +0.000
  threshold                          0.150

Decision
  regression_detected                NO
  deployment_decision                ALLOW
  panel_healthy                      yes
  exit_code                          0
  exit_driver                        none: panel healthy and no regression against the committed baseline

  gate: PASS

==============================================================================
5. The candidate that must be blocked (mock panel)
==============================================================================

--- honest panel, candidate sut.v2 ------------------------------------
  panel                 mock-strict (mock-deterministic-v1), mock-lenient (mock-deterministic-v1), mock-balanced (mock-deterministic-v1)
  pass rate             sut.v1 0.800  ->  sut.v2 0.533
  candidate             sut.v2 0.533 vs committed baseline 0.800
  drop_vs_baseline      +0.267 (threshold 0.150)
  panel kappa           0.925
  panel false_pass_rate 0.050
  unanimity_rate        0.867
  NOISE_FLOOR           0.100
  regression detected   YES
  deployment decision   BLOCK
  gate                  FAIL (exit 1)
  exit driven by        regression detected against the committed baseline
    - REGRESSION: sut.v2 pass rate 0.533 is 0.267 below the committed baseline 0.800, more than the 0.150 threshold allows

  same panel, same golden set, same committed baseline. The only change is
  which prompt version is on trial, and the exit code moves with it. That is
  the claim: a regression gate wired into CI that blocks a merge.

==============================================================================
6. A threshold inside the noise floor (mock panel)
==============================================================================

--- threshold 0.05, noise floor 0.100 ---------------------------------
  panel                 mock-strict (mock-deterministic-v1), mock-lenient (mock-deterministic-v1), mock-balanced (mock-deterministic-v1)
  pass rate             sut.v1 0.800  ->  sut.v2 0.533
  candidate             sut.v1 0.800 vs committed baseline 0.800
  drop_vs_baseline      +0.000 (threshold 0.050)
  panel kappa           0.925
  panel false_pass_rate 0.050
  unanimity_rate        0.867
  NOISE_FLOOR           0.100
  regression detected   NO
  deployment decision   NO DECISION (refused)
  gate                  FAIL (exit 1)
  exit driven by        refused: threshold sits inside the measured noise floor
    - REFUSING TO RUN: regression threshold 0.050 is inside the measured noise floor 0.100 (panel flip rate). A gate tighter than its own judges' variance is not a gate. Raise the threshold above 0.100 or make the judges more consistent.

  the gate refused rather than reporting a number. Inside the noise, a
  regression and a rerun are the same measurement.

==============================================================================
7. One miscalibrated judge: outvoted (mock panel)
==============================================================================

--- panel = strict + lenient + miscalibrated --------------------------
  panel                 mock-strict (mock-deterministic-v1), mock-lenient (mock-deterministic-v1), mock-miscalibrated (mock-deterministic-v1)
  pass rate             sut.v1 0.867  ->  sut.v2 0.600
  candidate             sut.v2 0.600 vs committed baseline 0.867
  drop_vs_baseline      +0.267 (threshold 0.150)
  panel kappa           0.842
  panel false_pass_rate 0.200
  unanimity_rate        0.600
  NOISE_FLOOR           0.000
  regression detected   YES
  deployment decision   BLOCK
  gate                  FAIL (exit 1)
  exit driven by        regression detected against the committed baseline (also: panel calibration failed (1 check))
    - REGRESSION: sut.v2 pass rate 0.600 is 0.267 below the committed baseline 0.867, more than the 0.150 threshold allows
    - judge mock-miscalibrated kappa 0.000 < 0.200

  baseline record       sut.v1 pass_rate 0.867 (one_miscalibrated panel, 3 repeats, prompts 8b545e68da22, run-73e6ebe1f850)
  the bad judge passes everything, and the other two outvote it on every
  hallucinated case. The regression is still caught, and the calibration
  layer additionally names the judge that would have missed it. The number it
  is measured against was recorded under THIS panel, because a pass rate from
  one panel is not comparable to a pass rate from another.

==============================================================================
8. Two miscalibrated judges: the panel is captured (mock panel)
==============================================================================

--- panel = miscalibrated + lenient + miscalibrated -------------------
  panel                 mock-miscalibrated#1 (mock-deterministic-v1), mock-lenient (mock-deterministic-v1), mock-miscalibrated#3 (mock-deterministic-v1)
  pass rate             sut.v1 1.000  ->  sut.v2 1.000
  candidate             sut.v2 1.000 vs committed baseline 1.000
  drop_vs_baseline      +0.000 (threshold 0.150)
  panel kappa           0.000
  panel false_pass_rate 1.000
  unanimity_rate        0.733
  NOISE_FLOOR           0.000
  regression detected   NO
  deployment decision   ALLOW
  gate                  FAIL (exit 1)
  exit driven by        panel calibration failed (4 checks)
    - judge mock-miscalibrated#1 kappa 0.000 < 0.200
    - judge mock-miscalibrated#3 kappa 0.000 < 0.200
    - panel false_pass_rate 1.000 > 0.300 (a false pass ships a regression)
    - cases_that_never_discriminate 30/30 = 1.000 > 0.700; SUSPICIOUS: cases_that_never_discriminate 30/30 (1.000): most cases give the two versions the same verdict, so they carry no signal; panel kappa 0.000 at or near zero: the panel's verdicts carry no information about the human labels; panel false_pass_rate 1.000 near one: the panel passes almost everything the humans failed. suspect a CAPTURED PANEL: these verdicts cannot gate anything

  baseline record       sut.v1 pass_rate 1.000 (two_miscalibrated panel, 3 repeats, prompts 8b545e68da22, run-f36b1648784d)
  a team whose panel was captured would have recorded ITS baseline with that
  captured panel, so that is the record this run is gated against: same panel,
  same prompt set, same repeats, a valid comparison. The gate is fooled on its
  own terms rather than because it was handed two numbers it should have
  refused to compare.

  the panel now passes every case in both versions, so nothing looks like a
  regression and the merge would be ALLOWED. Note what does NOT happen:
  unanimity is 0.733, LOWER than the honest panel's 0.867, because the
  surviving lenient judge still disagrees on the cases it fails. A heuristic
  keyed on unanimity would stay quiet here. The tells that do fire:
    - cases_that_never_discriminate 30/30 (1.000): most cases give the two versions the same verdict, so they carry no signal
    - panel kappa 0.000 at or near zero: the panel's verdicts carry no information about the human labels
    - panel false_pass_rate 1.000 near one: the panel passes almost everything the humans failed
  suspicion: SUSPICIOUS: cases_that_never_discriminate 30/30 (1.000): most cases give the two versions the same verdict, so they carry no signal; panel kappa 0.000 at or near zero: the panel's verdicts carry no information about the human labels; panel false_pass_rate 1.000 near one: the panel passes almost everything the humans failed. suspect a CAPTURED PANEL: these verdicts cannot gate anything

  and the exit code follows: deployment_decision ALLOW, but the gate exits 1
  because the calibration layer caught the panel that allowed it.

  the same captured run, gated against the HONEST panel's baseline instead:

--- captured panel, honest panel's baseline record --------------------
  panel                 mock-miscalibrated#1 (mock-deterministic-v1), mock-lenient (mock-deterministic-v1), mock-miscalibrated#3 (mock-deterministic-v1)
  pass rate             sut.v1 1.000  ->  sut.v2 1.000
  candidate             sut.v2 1.000 vs committed baseline 0.800
  drop_vs_baseline      -0.200 (threshold 0.150)
  panel kappa           0.000
  panel false_pass_rate 1.000
  unanimity_rate        0.733
  NOISE_FLOOR           0.000
  regression detected   NO
  deployment decision   NO DECISION (refused)
  gate                  FAIL (exit 1)
  exit driven by        refused: committed baseline is not comparable to this run (panel_mode differs) (also: panel calibration failed (4 checks))
    - REFUSING TO DECIDE: the committed baseline does not measure the same thing this run did (panel_mode differs), so no deployment decision was computed. Comparing these two pass rates would produce a number the harness has already called meaningless. Re-record the baseline for this configuration, or run the configuration the baseline was recorded under.
    - judge mock-miscalibrated#1 kappa 0.000 < 0.200
    - judge mock-miscalibrated#3 kappa 0.000 < 0.200
    - panel false_pass_rate 1.000 > 0.300 (a false pass ships a regression)
    - cases_that_never_discriminate 30/30 = 1.000 > 0.700; SUSPICIOUS: cases_that_never_discriminate 30/30 (1.000): most cases give the two versions the same verdict, so they carry no signal; panel kappa 0.000 at or near zero: the panel's verdicts carry no information about the human labels; panel false_pass_rate 1.000 near one: the panel passes almost everything the humans failed. suspect a CAPTURED PANEL: these verdicts cannot gate anything

  no deployment decision at all this time. Printing BASELINE NOT
  COMPARABLE and then computing ALLOW from that very comparison is the
  same defect the noise floor refusal exists to prevent one layer down: a
  number the harness has already called meaningless, presented as a
  verdict. The refusal names the field that differs, so the operator
  knows whether to re-record the baseline or to fix the panel.

==============================================================================
9. The shadow bench: measured, non voting, and provably harmless
==============================================================================
  scored exactly like a voting judge, excluded from the vote:
    shadow:mock-literalist     kappa  0.791  falsePass 0.000  falseFail 0.150
    shadow:mock-verbosity      kappa  0.108  falsePass 0.700  falseFail 0.200

  shadow judges scored     : 2
  same run with SHADOW_JUDGES off:
    deployment_decision    ALLOW (was ALLOW)
    drop_vs_baseline       +0.000 (was +0.000)
    exit code              0 (was 0)
    exit_driver            none: panel healthy and no regression against the committed baseline
    NOISE_FLOOR            0.100 (was 0.100)
    panel kappa            0.925 (was 0.925)
    failures               none
  GATE OUTCOME UNCHANGED   : True

  that is the invariant, and it is structural rather than conventional: a
  shadow verdict is stamped shadow=True, travels on a separate field of the
  run result, lands in a separate list on the report, and panel.aggregate
  raises if one ever reaches the vote. A shadow judge that could move a gate
  outcome would be a voting judge with extra steps.

==============================================================================
10. What it costs, and whether the cost buys accuracy
==============================================================================
Single judge (priced as claude-opus-5)
  anthropic            claude-opus-5            180 calls    103917 in   10800 out  $  0.7896
  TOTAL                                         180 calls    103917 in   10800 out  $  0.7896
  single judge (first slot) $0.7896  panel $0.7896  multiplier 1.00x
  token counts are characters/4, an APPROXIMATION, not a tokenizer; real runs use client.messages.count_tokens
  prices as of 2026-08; claude-sonnet-5 is $2/$10 per MTok, the standard price (in effect today)

Voting panel (claude-opus-5, claude-sonnet-5, gpt-5.6-terra)
  anthropic            claude-opus-5            180 calls    103917 in   10800 out  $  0.7896
  anthropic            claude-sonnet-5          180 calls    103917 in   10800 out  $  0.3158
  openai               gpt-5.6-terra            180 calls    103917 in   10800 out  $  0.4218
  TOTAL                                         540 calls    311751 in   32400 out  $  1.5272
  single judge (first slot) $0.7896  panel $1.5272  multiplier 1.93x
  token counts are characters/4, an APPROXIMATION, not a tokenizer; real runs use client.messages.count_tokens
  prices as of 2026-08; claude-sonnet-5 is $2/$10 per MTok, the standard price (in effect today)

Shadow bench, NON VOTING (gpt-5.6-luna, gpt-4o)
  openai               gpt-5.6-luna             180 calls    103917 in   10800 out  $  0.1687
  openai               gpt-4o                   180 calls    103917 in   10800 out  $  0.3678
  TOTAL                                         360 calls    207834 in   21600 out  $  0.5365
  single judge (first slot) $0.1687  panel $0.5365  multiplier 3.18x
  token counts are characters/4, an APPROXIMATION, not a tokenizer; real runs use client.messages.count_tokens
  prices as of 2026-08; claude-sonnet-5 is $2/$10 per MTok, the standard price (in effect today)

  voting panel $1.5272  +  shadow $0.5365  =  panel plus shadow $2.0637
  the voting panel costs 1.93x the single judge, and on this run it
  measured +0.000 kappa against its own best member.

Does judge cost buy judge accuracy (sorted by dollars per sweep)
  judge                    model              votes   kappa  falsePass   $/sweep
  shadow:mock-verbosity    gpt-5.6-luna          no   0.108      0.700    0.1687
  mock-lenient             claude-sonnet-5      yes   0.842      0.200    0.3158
  shadow:mock-literalist   gpt-4o                no   0.791      0.000    0.3678
  mock-balanced            gpt-5.6-terra        yes   0.925      0.050    0.4218
  mock-strict              claude-opus-5        yes   0.857      0.000    0.7896
  cheapest shadow:mock-verbosity (gpt-5.6-luna, $0.1687) kappa 0.108
  dearest  mock-strict (claude-opus-5, $0.7896) kappa 0.857
  the kappa gap is +0.749 against measured noise 0.100, so the price difference is buying a real difference in agreement
  CHEAPER JUDGE WITHIN NOISE OF THE DEAREST: mock-lenient (claude-sonnet-5, $0.3158) kappa 0.842 vs 0.857, gap +0.015. The extra $0.4738 per sweep is not buying measurable agreement.
  dollars are the offline characters/4 approximation priced against the models each judge stands in for; prices as of 2026-08

==============================================================================
11. The real model measurement pass (LIVE JUDGES, NEVER GATES)
==============================================================================
  SKIPPED: AGENT_PROVIDER is "mock", so there is no live judge to measure.
  This is the only section that calls a real model, and it is the only
  section whose numbers can never reach an exit code. Everything above ran
  on the deterministic offline panel, which is what makes the gate
  reproducible and what makes this section necessary: five hand written mock
  judges demonstrate that the harness measures whatever judges it is given,
  and demonstrate nothing whatsoever about how a real judge behaves.

  To run it:
    ENV_FILE=~/.secrets/ai.env AGENT_PROVIDER=anthropic python scripts/run_demo.py

  At the configured sizes that is 460 API calls: 30 cases x 2 versions x 1 repeat for the
  calibration pass, plus 8 cases x 2 versions x repeats 2..3 for the consistency
  pass, across 3 voting and 2 shadow judges. Size it with --real-cases,
  --real-repeat-cases and --real-repeats. The run prints its own call count
  and dollar estimate before it makes the first call.

==============================================================================
12. Audit trail
==============================================================================
  records written : 61
  chain verifies  : True

  first record:
{
  "event": "run_started",
  "payload": {
    "cases": 30,
    "degraded": false,
    "panel": [
      "mock-strict (mock-deterministic-v1)",
      "mock-lenient (mock-deterministic-v1)",
      "mock-balanced (mock-deterministic-v1)"
    ],
    "panel_size": 3,
    "prompt_manifest_hash": "8b545e68da22a28e5514e428b63916fee57b3e61d455453aebf6b7842c631741",
    "repeats": 3,
    "shadow_judges": [
      "shadow:mock-verbosity",
      "shadow:mock-literalist"
    ],
    "sut_versions": [
      "sut.v1",
      "sut.v2"
    ]
  },
  "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "record_hash": "f76532ed997d8db6965447e035f6f5256858c2c0b31db9f0a1aabad90b6881f9",
  "run_id": "run-cd7da0994aad",
  "timestamp": "2026-09-27T17:26:18.626732+00:00"
}

  a scored case:
{
  "event": "case_scored",
  "payload": {
    "abstentions": 0,
    "case_id": "gc-001",
    "degraded": false,
    "escalated": false,
    "human_label": "pass",
    "panel_verdict": "pass",
    "split": false,
    "sut_version": "sut.v1",
    "tags": [
      "grounded"
    ],
    "unanimous": true,
    "votes": [
      "pass",
      "pass",
      "pass"
    ]
  },
  "prev_hash": "f76532ed997d8db6965447e035f6f5256858c2c0b31db9f0a1aabad90b6881f9",
  "record_hash": "2b04988398c2b1f6f750888d67840c40ff6f0a0a88a38779b5058a28897ec6fa",
  "run_id": "run-cd7da0994aad",
  "timestamp": "2026-09-27T17:26:18.627177+00:00"
}

  rewrote record 26 (gc-013 sut.v1) from fail to pass
  chain verifies now: False
  timestamps are not frozen, so they are excluded from the determinism
  claim rather than faked. Reproducibility comes from canonical JSON.

==============================================================================
13. Which eval run approved the prompt that is live
==============================================================================
  judge    active v2   hash a110e3da9e90  versions v1,v2      approved by NEVER APPROVED BY A GREEN GATE
  sut      active v2   hash 574044117dcc  versions v1,v2      approved by NEVER APPROVED BY A GREEN GATE

  sut.v2 is active and has never been approved by a green gate. That is the
  question a prompt registry exists to answer, and the answer here is the
  one worth catching before a postmortem.
  history sut             v2 -> v1 -> v2

  rollback('sut') -> v1
  judge    active v2   hash a110e3da9e90  versions v1,v2      approved by NEVER APPROVED BY A GREEN GATE
  sut      active v1   hash c0b9794cc02d  versions v1,v2      approved by run-cd7da0994aad
  history sut             v2 -> v1 -> v2 -> v1

  the history is an APPEND, not a pop: v2 is still on the record as having
  been live and then withdrawn. Popping it would leave a history byte for byte
  identical to a registry where v2 never shipped, which is how a registry comes
  to claim a version was never live. The rollback itself is a recorded decision:
{
  "event": "prompt_rollback",
  "payload": {
    "actor": "release-eng",
    "approved_by": "run-cd7da0994aad",
    "from_hash": "574044117dcc403b08970c7e4940810f549078b19bbb983788ba74b532ac33f3",
    "from_version": "v2",
    "prompt_name": "sut",
    "reason": "sut.v2 was never approved by a green gate",
    "to_hash": "c0b9794cc02d705ce91803a3c1a3950d3b5cb28d507265c77543d2851fdd29bd",
    "to_version": "v1"
  },
  "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "record_hash": "e2e4c10e26c152912c15515aefc7717b0758945c96cdb52cc72a2730185f415d",
  "run_id": "run-cd7da0994aad",
  "timestamp": "2026-09-27T17:26:18.665207+00:00"
}

  rollback log chain verifies : True
  binding written             : rolled-back-to (eval run run-cd7da0994aad)
  the binding's outcome is not "passed", so rolling back cannot launder an
  ungated version into an approved one: approving_run still answers with the
  run that actually gated v1, or with nothing at all.

==============================================================================
Summary
==============================================================================
  shadow judges off       gate outcome unchanged: True
  active sut.v1            gate PASS (0), deployment ALLOW
  candidate sut.v2        gate FAIL (1), deployment BLOCK  <- merge blocked
  threshold in the noise  gate FAIL (1) (refused=True)
  one bad judge           gate FAIL (1), deployment BLOCK
  two bad judges          gate FAIL (1), deployment ALLOW  <- ALLOW, and still exit 1
  incomparable baseline   gate FAIL (1), deployment NO DECISION (refused)  <- no verdict computed at all
  real model pass         skipped (provider is mock)

  a gate you have not calibrated is a coin flip wearing a lab coat. The two
  bad judge run is the proof: same gate, same golden set, same planted
  regression, and it would have shipped.
```

Exit code 0. The demo's own exit code follows the ACTIVE version's gate, which is
the one CI runs, so a red demo would mean the committed baseline no longer
describes the committed prompts. Section 11 is skipped here and contributes
nothing to that integer even when it runs:
`tests/test_real_pass.py::test_the_demo_exit_code_is_the_same_with_the_real_pass_running_as_without_it`
runs the same demo against a live looking panel with a judge that raises on four
cases and asserts both the exit code and the gate's summary lines are unchanged.

### The five gate invocations

Each capture below is the tail of the gate's own output, from the regression block
through the exit driver, plus the exit code the shell observed. The full report
above every tail is the same shape as section 4 of the demo.

#### 1. The honest panel gating its own committed baseline

```
python -m eval_gate.evals.gate ; echo "exit=$?"
```

```
Regression, against the committed baseline record
  baseline record                    sut.v1 pass_rate 0.800 (honest panel, 3 repeats, prompts 8b545e68da22, run-cd7da0994aad)
  candidate                          sut.v1
  baseline_pass_rate                 0.800
  candidate_pass_rate                0.800
  drop_vs_baseline                   +0.000
  threshold                          0.150

Decision
  regression_detected                NO
  deployment_decision                ALLOW
  panel_healthy                      yes
  exit_code                          0
  exit_driver                        none: panel healthy and no regression against the committed baseline

Does judge cost buy judge accuracy (sorted by dollars per sweep)
  judge                    model              votes   kappa  falsePass   $/sweep
  shadow:mock-verbosity    gpt-5.6-luna          no   0.108      0.700    0.1687
  mock-lenient             claude-sonnet-5      yes   0.842      0.200    0.3158
  shadow:mock-literalist   gpt-4o                no   0.791      0.000    0.3678
  mock-balanced            gpt-5.6-terra        yes   0.925      0.050    0.4218
  mock-strict              claude-opus-5        yes   0.857      0.000    0.7896
  cheapest shadow:mock-verbosity (gpt-5.6-luna, $0.1687) kappa 0.108
  dearest  mock-strict (claude-opus-5, $0.7896) kappa 0.857
  the kappa gap is +0.749 against measured noise 0.100, so the price difference is buying a real difference in agreement
  CHEAPER JUDGE WITHIN NOISE OF THE DEAREST: mock-lenient (claude-sonnet-5, $0.3158) kappa 0.842 vs 0.857, gap +0.015. The extra $0.4738 per sweep is not buying measurable agreement.
  dollars are the offline characters/4 approximation priced against the models each judge stands in for; prices as of 2026-08

  audit_records                      62
  audit_chain_intact                 yes

EVAL GATE PASSED (30 cases)
  exit driven by: none: panel healthy and no regression against the committed baseline
  deployment_decision ALLOW, candidate sut.v1 pass rate 0.800 vs committed baseline 0.800
exit=0
```

#### 2. The planted regression, blocked

```
python -m eval_gate.evals.gate --candidate sut.v2 ; echo "exit=$?"
```

```
Regression, against the committed baseline record
  baseline record                    sut.v1 pass_rate 0.800 (honest panel, 3 repeats, prompts 8b545e68da22, run-cd7da0994aad)
  candidate                          sut.v2
  baseline_pass_rate                 0.800
  candidate_pass_rate                0.533
  drop_vs_baseline                   +0.267
  threshold                          0.150

Decision
  regression_detected                YES
  deployment_decision                BLOCK
  panel_healthy                      yes
  exit_code                          1
  exit_driver                        regression detected against the committed baseline

Does judge cost buy judge accuracy (sorted by dollars per sweep)
  judge                    model              votes   kappa  falsePass   $/sweep
  shadow:mock-verbosity    gpt-5.6-luna          no   0.108      0.700    0.1687
  mock-lenient             claude-sonnet-5      yes   0.842      0.200    0.3158
  shadow:mock-literalist   gpt-4o                no   0.791      0.000    0.3678
  mock-balanced            gpt-5.6-terra        yes   0.925      0.050    0.4218
  mock-strict              claude-opus-5        yes   0.857      0.000    0.7896
  cheapest shadow:mock-verbosity (gpt-5.6-luna, $0.1687) kappa 0.108
  dearest  mock-strict (claude-opus-5, $0.7896) kappa 0.857
  the kappa gap is +0.749 against measured noise 0.100, so the price difference is buying a real difference in agreement
  CHEAPER JUDGE WITHIN NOISE OF THE DEAREST: mock-lenient (claude-sonnet-5, $0.3158) kappa 0.842 vs 0.857, gap +0.015. The extra $0.4738 per sweep is not buying measurable agreement.
  dollars are the offline characters/4 approximation priced against the models each judge stands in for; prices as of 2026-08

  audit_records                      62
  audit_chain_intact                 yes

EVAL GATE FAILED
  - REGRESSION: sut.v2 pass rate 0.533 is 0.267 below the committed baseline 0.800, more than the 0.150 threshold allows

  exit driven by: regression detected against the committed baseline
exit=1
```

#### 3. The captured panel, against a baseline recorded by that same panel

```
python -m eval_gate.evals.gate --panel two_miscalibrated --candidate sut.v2 \
    --baseline baseline.two_miscalibrated.json ; echo "exit=$?"
```

```
Regression, against the committed baseline record
  baseline record                    sut.v1 pass_rate 1.000 (two_miscalibrated panel, 3 repeats, prompts 8b545e68da22, run-f36b1648784d)
  candidate                          sut.v2
  baseline_pass_rate                 1.000
  candidate_pass_rate                1.000
  drop_vs_baseline                   +0.000
  threshold                          0.150

Decision
  regression_detected                NO
  deployment_decision                ALLOW
  panel_healthy                      NO
  exit_code                          1
  exit_driver                        panel calibration failed (4 checks)

Does judge cost buy judge accuracy (sorted by dollars per sweep)
  judge                    model              votes   kappa  falsePass   $/sweep
  shadow:mock-verbosity    gpt-5.6-luna          no   0.108      0.700    0.1687
  mock-lenient             claude-sonnet-5      yes   0.842      0.200    0.3158
  shadow:mock-literalist   gpt-4o                no   0.791      0.000    0.3678
  mock-miscalibrated#1     gpt-5.6-terra        yes   0.000      1.000    0.4218
  mock-miscalibrated#3     gpt-5.6-terra        yes   0.000      1.000    0.4218
  cheapest shadow:mock-verbosity (gpt-5.6-luna, $0.1687) kappa 0.108
  dearest  mock-miscalibrated#3 (gpt-5.6-terra, $0.4218) kappa 0.000
  the kappa gap is -0.108 against measured noise 0.000: THE CHEAPEST JUDGE AGREES WITH THE HUMANS MORE, so the extra spend is buying worse agreement
  dollars are the offline characters/4 approximation priced against the models each judge stands in for; prices as of 2026-08

  audit_records                      62
  audit_chain_intact                 yes

EVAL GATE FAILED
  - judge mock-miscalibrated#1 kappa 0.000 < 0.200
  - judge mock-miscalibrated#3 kappa 0.000 < 0.200
  - panel false_pass_rate 1.000 > 0.300 (a false pass ships a regression)
  - cases_that_never_discriminate 30/30 = 1.000 > 0.700; SUSPICIOUS: cases_that_never_discriminate 30/30 (1.000): most cases give the two versions the same verdict, so they carry no signal; panel kappa 0.000 at or near zero: the panel's verdicts carry no information about the human labels; panel false_pass_rate 1.000 near one: the panel passes almost everything the humans failed. suspect a CAPTURED PANEL: these verdicts cannot gate anything

  exit driven by: panel calibration failed (4 checks)
exit=1
```

#### 4. The same captured run against the honest panel's baseline: no decision

```
python -m eval_gate.evals.gate --panel two_miscalibrated --candidate sut.v2 ; echo "exit=$?"
```

```
Regression, against the committed baseline record
  baseline record                    sut.v1 pass_rate 0.800 (honest panel, 3 repeats, prompts 8b545e68da22, run-cd7da0994aad)
  candidate                          sut.v2
  baseline_pass_rate                 0.800
  candidate_pass_rate                1.000
  drop_vs_baseline                   -0.200
  threshold                          0.150
  BASELINE NOT COMPARABLE: baseline was measured by the 'honest' panel, this run used 'two_miscalibrated'; pass rates from different panels are not comparable
  the drop above is REPORTED AND NOT DECIDED ON: no deployment decision is computed from a comparison the harness has called meaningless

Decision
  regression_detected                NO
  deployment_decision                NO DECISION (refused)
  panel_healthy                      NO
  exit_code                          1
  exit_driver                        refused: committed baseline is not comparable to this run (panel_mode differs) (also: panel calibration failed (4 checks))

Does judge cost buy judge accuracy (sorted by dollars per sweep)
  judge                    model              votes   kappa  falsePass   $/sweep
  shadow:mock-verbosity    gpt-5.6-luna          no   0.108      0.700    0.1687
  mock-lenient             claude-sonnet-5      yes   0.842      0.200    0.3158
  shadow:mock-literalist   gpt-4o                no   0.791      0.000    0.3678
  mock-miscalibrated#1     gpt-5.6-terra        yes   0.000      1.000    0.4218
  mock-miscalibrated#3     gpt-5.6-terra        yes   0.000      1.000    0.4218
  cheapest shadow:mock-verbosity (gpt-5.6-luna, $0.1687) kappa 0.108
  dearest  mock-miscalibrated#3 (gpt-5.6-terra, $0.4218) kappa 0.000
  the kappa gap is -0.108 against measured noise 0.000: THE CHEAPEST JUDGE AGREES WITH THE HUMANS MORE, so the extra spend is buying worse agreement
  dollars are the offline characters/4 approximation priced against the models each judge stands in for; prices as of 2026-08

  audit_records                      62
  audit_chain_intact                 yes

EVAL GATE FAILED
  - REFUSING TO DECIDE: the committed baseline does not measure the same thing this run did (panel_mode differs), so no deployment decision was computed. Comparing these two pass rates would produce a number the harness has already called meaningless. Re-record the baseline for this configuration, or run the configuration the baseline was recorded under.
  - judge mock-miscalibrated#1 kappa 0.000 < 0.200
  - judge mock-miscalibrated#3 kappa 0.000 < 0.200
  - panel false_pass_rate 1.000 > 0.300 (a false pass ships a regression)
  - cases_that_never_discriminate 30/30 = 1.000 > 0.700; SUSPICIOUS: cases_that_never_discriminate 30/30 (1.000): most cases give the two versions the same verdict, so they carry no signal; panel kappa 0.000 at or near zero: the panel's verdicts carry no information about the human labels; panel false_pass_rate 1.000 near one: the panel passes almost everything the humans failed. suspect a CAPTURED PANEL: these verdicts cannot gate anything

  exit driven by: refused: committed baseline is not comparable to this run (panel_mode differs) (also: panel calibration failed (4 checks))
exit=1
```

#### 5. A threshold inside the measured noise floor: refused

```
python -m eval_gate.evals.gate --threshold 0.05 ; echo "exit=$?"
```

```
Regression, against the committed baseline record
  baseline record                    sut.v1 pass_rate 0.800 (honest panel, 3 repeats, prompts 8b545e68da22, run-cd7da0994aad)
  candidate                          sut.v1
  baseline_pass_rate                 0.800
  candidate_pass_rate                0.800
  drop_vs_baseline                   +0.000
  threshold                          0.050

Decision
  regression_detected                NO
  deployment_decision                NO DECISION (refused)
  panel_healthy                      yes
  exit_code                          1
  exit_driver                        refused: threshold sits inside the measured noise floor

Does judge cost buy judge accuracy (sorted by dollars per sweep)
  judge                    model              votes   kappa  falsePass   $/sweep
  shadow:mock-verbosity    gpt-5.6-luna          no   0.108      0.700    0.1687
  mock-lenient             claude-sonnet-5      yes   0.842      0.200    0.3158
  shadow:mock-literalist   gpt-4o                no   0.791      0.000    0.3678
  mock-balanced            gpt-5.6-terra        yes   0.925      0.050    0.4218
  mock-strict              claude-opus-5        yes   0.857      0.000    0.7896
  cheapest shadow:mock-verbosity (gpt-5.6-luna, $0.1687) kappa 0.108
  dearest  mock-strict (claude-opus-5, $0.7896) kappa 0.857
  the kappa gap is +0.749 against measured noise 0.100, so the price difference is buying a real difference in agreement
  CHEAPER JUDGE WITHIN NOISE OF THE DEAREST: mock-lenient (claude-sonnet-5, $0.3158) kappa 0.842 vs 0.857, gap +0.015. The extra $0.4738 per sweep is not buying measurable agreement.
  dollars are the offline characters/4 approximation priced against the models each judge stands in for; prices as of 2026-08

  audit_records                      62
  audit_chain_intact                 yes

EVAL GATE FAILED
  - REFUSING TO RUN: regression threshold 0.050 is inside the measured noise floor 0.100 (panel flip rate). A gate tighter than its own judges' variance is not a gate. Raise the threshold above 0.100 or make the judges more consistent.

  exit driven by: refused: threshold sits inside the measured noise floor
exit=1
```

## Real model run (Anthropic primary)

Only section 11 is reproduced, because it is the only section a real provider
changes and the only one whose numbers can never reach an exit code. Diffing this
run against the offline capture above, instead of assuming:

- **Sections 0 through 10 gate on the deterministic mock panel** whatever
  `AGENT_PROVIDER` says, which is the design. The header names the panel and
  differs as below (`-` this run, `+` the offline capture), once timestamps, run
  ids and hashes are set aside.

```
-provider        : anthropic
-voting panel    : anthropic#1 (claude-opus-5), anthropic#2 (claude-sonnet-5), openai (gpt-5.6-terra)
-shadow bench    : openai#1 (gpt-5.6-luna), openai#2 (gpt-4o)
-panel degraded  : no (3 of 3 slots real: 3 models, 2 vendors (anthropic, openai))
+provider        : mock
+voting panel    : mock-strict (mock-deterministic-v1), mock-lenient (mock-deterministic-v1), mock-balanced (mock-deterministic-v1)
+shadow bench    : mock-verbosity (mock-deterministic-v1), mock-literalist (mock-deterministic-v1)
+panel degraded  : no (3 of 3 slots are offline deterministic mocks, so there is nothing real to degrade)
-note: the sections marked (mock panel) below always run offline, because
-      a regression gate has to be reproducible. The live panel above is
-      measured in section 11, which spends money and never gates.
+note: the panel below is three DISTINCT deterministic judges, so the
+      disagreement is real. Against real models the same golden set
+      measures whatever those models actually do.
```

Sections 0 through 10 are byte for byte identical.

- **Sections 12 and 13 differ only in timestamps and the record hashes that cover
  them**, which is exactly the determinism boundary `tests/test_determinism.py`
  pins, plus one line of section 13's summary: `real model pass skipped (provider
  is mock)` becomes `real model pass ran, and contributed nothing to any line
  above`.

That last line is the claim of this whole section, printed by the demo about
itself: 460 live judge calls were made and no number above them moved.

```
ENV_FILE=~/.secrets/ai.env AGENT_PROVIDER=anthropic python scripts/run_demo.py
```

```
==============================================================================
11. The real model measurement pass (LIVE JUDGES, NEVER GATES)
==============================================================================
Before any call is made: the plan, the call count, and the estimate
  calibration pass      30 cases x 2 versions x 1 repeat = 60 judgments per judge
  consistency pass      8 cases x 2 versions x repeats 2..3 = 32 more per judge
  judges                3 voting + 2 shadow = 5
  TOTAL API CALLS       460  (92 per judge)
                        repeat 1 of every consistency case is reused from the calibration pass, so this is 80 fewer than the 540 two independent passes would cost
  slot                     model               calls    est in  est out     est $
  anthropic#1              claude-opus-5          92     53419     5520    0.4051
  anthropic#2              claude-sonnet-5        92     53419     5520    0.1620
  openai                   gpt-5.6-terra          92     53419     5520    0.2163
  shadow:openai#1          gpt-5.6-luna           92     53419     5520    0.0865
  shadow:openai#2          gpt-4o                 92     53419     5520    0.1887
  ESTIMATED COST        voting $0.7835  +  shadow $0.2753  =  $1.0588
  the estimate is the offline characters/4 approximation, priced against the models in the slots. It is NOT a token count. The measured figures below replace it, and the gap between the two is reported.

  calibration pass: 30 cases (gc-001..gc-030), both sut versions, 1 repeat
    gc-001  sut.v1  r1  anthropic#1              pass
    gc-001  sut.v1  r1  anthropic#2              pass
    gc-001  sut.v1  r1  openai                   pass
    gc-001  sut.v1  r1  shadow:openai#1          pass
    gc-001  sut.v1  r1  shadow:openai#2          fail
    gc-001  sut.v2  r1  anthropic#1              pass
    gc-001  sut.v2  r1  anthropic#2              pass
    gc-001  sut.v2  r1  openai                   pass
    gc-001  sut.v2  r1  shadow:openai#1          pass
    gc-001  sut.v2  r1  shadow:openai#2          pass
    gc-002  sut.v1  r1  anthropic#1              pass
    gc-002  sut.v1  r1  anthropic#2              pass
    gc-002  sut.v1  r1  openai                   pass
    gc-002  sut.v1  r1  shadow:openai#1          pass
    gc-002  sut.v1  r1  shadow:openai#2          pass
    gc-002  sut.v2  r1  anthropic#1              pass
    gc-002  sut.v2  r1  anthropic#2              pass
    gc-002  sut.v2  r1  openai                   pass
    gc-002  sut.v2  r1  shadow:openai#1          pass
    gc-002  sut.v2  r1  shadow:openai#2          pass
    gc-003  sut.v1  r1  anthropic#1              pass
    gc-003  sut.v1  r1  anthropic#2              pass
    gc-003  sut.v1  r1  openai                   pass
    gc-003  sut.v1  r1  shadow:openai#1          pass
    gc-003  sut.v1  r1  shadow:openai#2          pass
    gc-003  sut.v2  r1  anthropic#1              pass
    gc-003  sut.v2  r1  anthropic#2              pass
    gc-003  sut.v2  r1  openai                   pass
    gc-003  sut.v2  r1  shadow:openai#1          fail
    gc-003  sut.v2  r1  shadow:openai#2          pass
    gc-004  sut.v1  r1  anthropic#1              pass
    gc-004  sut.v1  r1  anthropic#2              pass
    gc-004  sut.v1  r1  openai                   pass
    gc-004  sut.v1  r1  shadow:openai#1          pass
    gc-004  sut.v1  r1  shadow:openai#2          pass
    gc-004  sut.v2  r1  anthropic#1              pass
    gc-004  sut.v2  r1  anthropic#2              pass
    gc-004  sut.v2  r1  openai                   pass
    gc-004  sut.v2  r1  shadow:openai#1          pass
    gc-004  sut.v2  r1  shadow:openai#2          pass
    gc-005  sut.v1  r1  anthropic#1              pass
    gc-005  sut.v1  r1  anthropic#2              pass
    gc-005  sut.v1  r1  openai                   pass
    gc-005  sut.v1  r1  shadow:openai#1          pass
    gc-005  sut.v1  r1  shadow:openai#2          pass
    gc-005  sut.v2  r1  anthropic#1              pass
    gc-005  sut.v2  r1  anthropic#2              pass
    gc-005  sut.v2  r1  openai                   pass
    gc-005  sut.v2  r1  shadow:openai#1          pass
    gc-005  sut.v2  r1  shadow:openai#2          fail
    gc-006  sut.v1  r1  anthropic#1              pass
    gc-006  sut.v1  r1  anthropic#2              pass
    gc-006  sut.v1  r1  openai                   pass
    gc-006  sut.v1  r1  shadow:openai#1          pass
    gc-006  sut.v1  r1  shadow:openai#2          pass
    gc-006  sut.v2  r1  anthropic#1              pass
    gc-006  sut.v2  r1  anthropic#2              pass
    gc-006  sut.v2  r1  openai                   pass
    gc-006  sut.v2  r1  shadow:openai#1          pass
    gc-006  sut.v2  r1  shadow:openai#2          pass
    gc-007  sut.v1  r1  anthropic#1              pass
    gc-007  sut.v1  r1  anthropic#2              pass
    gc-007  sut.v1  r1  openai                   pass
    gc-007  sut.v1  r1  shadow:openai#1          pass
    gc-007  sut.v1  r1  shadow:openai#2          pass
    gc-007  sut.v2  r1  anthropic#1              pass
    gc-007  sut.v2  r1  anthropic#2              pass
    gc-007  sut.v2  r1  openai                   pass
    gc-007  sut.v2  r1  shadow:openai#1          pass
    gc-007  sut.v2  r1  shadow:openai#2          pass
    gc-008  sut.v1  r1  anthropic#1              pass
    gc-008  sut.v1  r1  anthropic#2              pass
    gc-008  sut.v1  r1  openai                   pass
    gc-008  sut.v1  r1  shadow:openai#1          pass
    gc-008  sut.v1  r1  shadow:openai#2          fail
    gc-008  sut.v2  r1  anthropic#1              pass
    gc-008  sut.v2  r1  anthropic#2              pass
    gc-008  sut.v2  r1  openai                   pass
    gc-008  sut.v2  r1  shadow:openai#1          pass
    gc-008  sut.v2  r1  shadow:openai#2          pass
    gc-009  sut.v1  r1  anthropic#1              pass
    gc-009  sut.v1  r1  anthropic#2              pass
    gc-009  sut.v1  r1  openai                   pass
    gc-009  sut.v1  r1  shadow:openai#1          pass
    gc-009  sut.v1  r1  shadow:openai#2          pass
    gc-009  sut.v2  r1  anthropic#1              pass
    gc-009  sut.v2  r1  anthropic#2              pass
    gc-009  sut.v2  r1  openai                   pass
    gc-009  sut.v2  r1  shadow:openai#1          pass
    gc-009  sut.v2  r1  shadow:openai#2          pass
    gc-010  sut.v1  r1  anthropic#1              pass
    gc-010  sut.v1  r1  anthropic#2              pass
    gc-010  sut.v1  r1  openai                   pass
    gc-010  sut.v1  r1  shadow:openai#1          pass
    gc-010  sut.v1  r1  shadow:openai#2          fail
    gc-010  sut.v2  r1  anthropic#1              pass
    gc-010  sut.v2  r1  anthropic#2              pass
    gc-010  sut.v2  r1  openai                   pass
    gc-010  sut.v2  r1  shadow:openai#1          pass
    gc-010  sut.v2  r1  shadow:openai#2          pass
    gc-011  sut.v1  r1  anthropic#1              pass
    gc-011  sut.v1  r1  anthropic#2              pass
    gc-011  sut.v1  r1  openai                   pass
    gc-011  sut.v1  r1  shadow:openai#1          pass
    gc-011  sut.v1  r1  shadow:openai#2          pass
    gc-011  sut.v2  r1  anthropic#1              pass
    gc-011  sut.v2  r1  anthropic#2              pass
    gc-011  sut.v2  r1  openai                   pass
    gc-011  sut.v2  r1  shadow:openai#1          pass
    gc-011  sut.v2  r1  shadow:openai#2          pass
    gc-012  sut.v1  r1  anthropic#1              pass
    gc-012  sut.v1  r1  anthropic#2              pass
    gc-012  sut.v1  r1  openai                   pass
    gc-012  sut.v1  r1  shadow:openai#1          pass
    gc-012  sut.v1  r1  shadow:openai#2          pass
    gc-012  sut.v2  r1  anthropic#1              pass
    gc-012  sut.v2  r1  anthropic#2              pass
    gc-012  sut.v2  r1  openai                   pass
    gc-012  sut.v2  r1  shadow:openai#1          pass
    gc-012  sut.v2  r1  shadow:openai#2          pass
    gc-013  sut.v1  r1  anthropic#1              fail
    gc-013  sut.v1  r1  anthropic#2              pass
    gc-013  sut.v1  r1  openai                   fail
    gc-013  sut.v1  r1  shadow:openai#1          fail
    gc-013  sut.v1  r1  shadow:openai#2          pass
    gc-013  sut.v2  r1  anthropic#1              fail
    gc-013  sut.v2  r1  anthropic#2              pass
    gc-013  sut.v2  r1  openai                   fail
    gc-013  sut.v2  r1  shadow:openai#1          fail
    gc-013  sut.v2  r1  shadow:openai#2          fail
    gc-014  sut.v1  r1  anthropic#1              fail
    gc-014  sut.v1  r1  anthropic#2              pass
    gc-014  sut.v1  r1  openai                   fail
    gc-014  sut.v1  r1  shadow:openai#1          fail
    gc-014  sut.v1  r1  shadow:openai#2          fail
    gc-014  sut.v2  r1  anthropic#1              fail
    gc-014  sut.v2  r1  anthropic#2              fail
    gc-014  sut.v2  r1  openai                   fail
    gc-014  sut.v2  r1  shadow:openai#1          fail
    gc-014  sut.v2  r1  shadow:openai#2          fail
    gc-015  sut.v1  r1  anthropic#1              pass
    gc-015  sut.v1  r1  anthropic#2              pass
    gc-015  sut.v1  r1  openai                   pass
    gc-015  sut.v1  r1  shadow:openai#1          pass
    gc-015  sut.v1  r1  shadow:openai#2          pass
    gc-015  sut.v2  r1  anthropic#1              fail
    gc-015  sut.v2  r1  anthropic#2              fail
    gc-015  sut.v2  r1  openai                   fail
    gc-015  sut.v2  r1  shadow:openai#1          fail
    gc-015  sut.v2  r1  shadow:openai#2          fail
    gc-016  sut.v1  r1  anthropic#1              pass
    gc-016  sut.v1  r1  anthropic#2              pass
    gc-016  sut.v1  r1  openai                   pass
    gc-016  sut.v1  r1  shadow:openai#1          pass
    gc-016  sut.v1  r1  shadow:openai#2          pass
    gc-016  sut.v2  r1  anthropic#1              fail
    gc-016  sut.v2  r1  anthropic#2              fail
    gc-016  sut.v2  r1  openai                   fail
    gc-016  sut.v2  r1  shadow:openai#1          fail
    gc-016  sut.v2  r1  shadow:openai#2          fail
    gc-017  sut.v1  r1  anthropic#1              pass
    gc-017  sut.v1  r1  anthropic#2              pass
    gc-017  sut.v1  r1  openai                   pass
    gc-017  sut.v1  r1  shadow:openai#1          pass
    gc-017  sut.v1  r1  shadow:openai#2          pass
    gc-017  sut.v2  r1  anthropic#1              fail
    gc-017  sut.v2  r1  anthropic#2              fail
    gc-017  sut.v2  r1  openai                   fail
    gc-017  sut.v2  r1  shadow:openai#1          fail
    gc-017  sut.v2  r1  shadow:openai#2          fail
    gc-018  sut.v1  r1  anthropic#1              pass
    gc-018  sut.v1  r1  anthropic#2              pass
    gc-018  sut.v1  r1  openai                   pass
    gc-018  sut.v1  r1  shadow:openai#1          pass
    gc-018  sut.v1  r1  shadow:openai#2          pass
    gc-018  sut.v2  r1  anthropic#1              fail
    gc-018  sut.v2  r1  anthropic#2              fail
    gc-018  sut.v2  r1  openai                   fail
    gc-018  sut.v2  r1  shadow:openai#1          fail
    gc-018  sut.v2  r1  shadow:openai#2          fail
    gc-019  sut.v1  r1  anthropic#1              pass
    gc-019  sut.v1  r1  anthropic#2              pass
    gc-019  sut.v1  r1  openai                   pass
    gc-019  sut.v1  r1  shadow:openai#1          pass
    gc-019  sut.v1  r1  shadow:openai#2          pass
    gc-019  sut.v2  r1  anthropic#1              fail
    gc-019  sut.v2  r1  anthropic#2              fail
    gc-019  sut.v2  r1  openai                   fail
    gc-019  sut.v2  r1  shadow:openai#1          fail
    gc-019  sut.v2  r1  shadow:openai#2          fail
    gc-020  sut.v1  r1  anthropic#1              pass
    gc-020  sut.v1  r1  anthropic#2              pass
    gc-020  sut.v1  r1  openai                   pass
    gc-020  sut.v1  r1  shadow:openai#1          pass
    gc-020  sut.v1  r1  shadow:openai#2          pass
    gc-020  sut.v2  r1  anthropic#1              fail
    gc-020  sut.v2  r1  anthropic#2              fail
    gc-020  sut.v2  r1  openai                   fail
    gc-020  sut.v2  r1  shadow:openai#1          fail
    gc-020  sut.v2  r1  shadow:openai#2          fail
    gc-021  sut.v1  r1  anthropic#1              pass
    gc-021  sut.v1  r1  anthropic#2              pass
    gc-021  sut.v1  r1  openai                   pass
    gc-021  sut.v1  r1  shadow:openai#1          pass
    gc-021  sut.v1  r1  shadow:openai#2          pass
    gc-021  sut.v2  r1  anthropic#1              fail
    gc-021  sut.v2  r1  anthropic#2              fail
    gc-021  sut.v2  r1  openai                   fail
    gc-021  sut.v2  r1  shadow:openai#1          fail
    gc-021  sut.v2  r1  shadow:openai#2          fail
    gc-022  sut.v1  r1  anthropic#1              pass
    gc-022  sut.v1  r1  anthropic#2              pass
    gc-022  sut.v1  r1  openai                   pass
    gc-022  sut.v1  r1  shadow:openai#1          pass
    gc-022  sut.v1  r1  shadow:openai#2          pass
    gc-022  sut.v2  r1  anthropic#1              fail
    gc-022  sut.v2  r1  anthropic#2              fail
    gc-022  sut.v2  r1  openai                   fail
    gc-022  sut.v2  r1  shadow:openai#1          fail
    gc-022  sut.v2  r1  shadow:openai#2          fail
    gc-023  sut.v1  r1  anthropic#1              pass
    gc-023  sut.v1  r1  anthropic#2              pass
    gc-023  sut.v1  r1  openai                   pass
    gc-023  sut.v1  r1  shadow:openai#1          pass
    gc-023  sut.v1  r1  shadow:openai#2          pass
    gc-023  sut.v2  r1  anthropic#1              fail
    gc-023  sut.v2  r1  anthropic#2              fail
    gc-023  sut.v2  r1  openai                   fail
    gc-023  sut.v2  r1  shadow:openai#1          fail
    gc-023  sut.v2  r1  shadow:openai#2          fail
    gc-024  sut.v1  r1  anthropic#1              pass
    gc-024  sut.v1  r1  anthropic#2              pass
    gc-024  sut.v1  r1  openai                   pass
    gc-024  sut.v1  r1  shadow:openai#1          pass
    gc-024  sut.v1  r1  shadow:openai#2          pass
    gc-024  sut.v2  r1  anthropic#1              fail
    gc-024  sut.v2  r1  anthropic#2              fail
    gc-024  sut.v2  r1  openai                   fail
    gc-024  sut.v2  r1  shadow:openai#1          fail
    gc-024  sut.v2  r1  shadow:openai#2          fail
    gc-025  sut.v1  r1  anthropic#1              fail
    gc-025  sut.v1  r1  anthropic#2              fail
    gc-025  sut.v1  r1  openai                   fail
    gc-025  sut.v1  r1  shadow:openai#1          fail
    gc-025  sut.v1  r1  shadow:openai#2          fail
    gc-025  sut.v2  r1  anthropic#1              pass
    gc-025  sut.v2  r1  anthropic#2              pass
    gc-025  sut.v2  r1  openai                   pass
    gc-025  sut.v2  r1  shadow:openai#1          pass
    gc-025  sut.v2  r1  shadow:openai#2          pass
    gc-026  sut.v1  r1  anthropic#1              fail
    gc-026  sut.v1  r1  anthropic#2              fail
    gc-026  sut.v1  r1  openai                   fail
    gc-026  sut.v1  r1  shadow:openai#1          fail
    gc-026  sut.v1  r1  shadow:openai#2          fail
    gc-026  sut.v2  r1  anthropic#1              pass
    gc-026  sut.v2  r1  anthropic#2              pass
    gc-026  sut.v2  r1  openai                   pass
    gc-026  sut.v2  r1  shadow:openai#1          pass
    gc-026  sut.v2  r1  shadow:openai#2          pass
    gc-027  sut.v1  r1  anthropic#1              fail
    gc-027  sut.v1  r1  anthropic#2              fail
    gc-027  sut.v1  r1  openai                   fail
    gc-027  sut.v1  r1  shadow:openai#1          fail
    gc-027  sut.v1  r1  shadow:openai#2          fail
    gc-027  sut.v2  r1  anthropic#1              fail
    gc-027  sut.v2  r1  anthropic#2              fail
    gc-027  sut.v2  r1  openai                   fail
    gc-027  sut.v2  r1  shadow:openai#1          fail
    gc-027  sut.v2  r1  shadow:openai#2          fail
    gc-028  sut.v1  r1  anthropic#1              fail
    gc-028  sut.v1  r1  anthropic#2              fail
    gc-028  sut.v1  r1  openai                   fail
    gc-028  sut.v1  r1  shadow:openai#1          fail
    gc-028  sut.v1  r1  shadow:openai#2          fail
    gc-028  sut.v2  r1  anthropic#1              fail
    gc-028  sut.v2  r1  anthropic#2              fail
    gc-028  sut.v2  r1  openai                   fail
    gc-028  sut.v2  r1  shadow:openai#1          fail
    gc-028  sut.v2  r1  shadow:openai#2          fail
    gc-029  sut.v1  r1  anthropic#1              fail
    gc-029  sut.v1  r1  anthropic#2              fail
    gc-029  sut.v1  r1  openai                   fail
    gc-029  sut.v1  r1  shadow:openai#1          fail
    gc-029  sut.v1  r1  shadow:openai#2          fail
    gc-029  sut.v2  r1  anthropic#1              fail
    gc-029  sut.v2  r1  anthropic#2              fail
    gc-029  sut.v2  r1  openai                   fail
    gc-029  sut.v2  r1  shadow:openai#1          fail
    gc-029  sut.v2  r1  shadow:openai#2          fail
    gc-030  sut.v1  r1  anthropic#1              fail
    gc-030  sut.v1  r1  anthropic#2              fail
    gc-030  sut.v1  r1  openai                   fail
    gc-030  sut.v1  r1  shadow:openai#1          fail
    gc-030  sut.v1  r1  shadow:openai#2          fail
    gc-030  sut.v2  r1  anthropic#1              fail
    gc-030  sut.v2  r1  anthropic#2              fail
    gc-030  sut.v2  r1  openai                   fail
    gc-030  sut.v2  r1  shadow:openai#1          fail
    gc-030  sut.v2  r1  shadow:openai#2          fail

  consistency pass: 8 cases (gc-001..gc-008), both sut versions, repeats 2..3 (repeat 1 reused from above)
    gc-001  sut.v1  r2  anthropic#1              pass
    gc-001  sut.v1  r2  anthropic#2              pass
    gc-001  sut.v1  r2  openai                   pass
    gc-001  sut.v1  r2  shadow:openai#1          pass
    gc-001  sut.v1  r2  shadow:openai#2          fail
    gc-001  sut.v1  r3  anthropic#1              pass
    gc-001  sut.v1  r3  anthropic#2              pass
    gc-001  sut.v1  r3  openai                   pass
    gc-001  sut.v1  r3  shadow:openai#1          pass
    gc-001  sut.v1  r3  shadow:openai#2          pass
    gc-001  sut.v2  r2  anthropic#1              pass
    gc-001  sut.v2  r2  anthropic#2              pass
    gc-001  sut.v2  r2  openai                   pass
    gc-001  sut.v2  r2  shadow:openai#1          pass
    gc-001  sut.v2  r2  shadow:openai#2          pass
    gc-001  sut.v2  r3  anthropic#1              pass
    gc-001  sut.v2  r3  anthropic#2              pass
    gc-001  sut.v2  r3  openai                   pass
    gc-001  sut.v2  r3  shadow:openai#1          pass
    gc-001  sut.v2  r3  shadow:openai#2          pass
    gc-002  sut.v1  r2  anthropic#1              pass
    gc-002  sut.v1  r2  anthropic#2              pass
    gc-002  sut.v1  r2  openai                   pass
    gc-002  sut.v1  r2  shadow:openai#1          pass
    gc-002  sut.v1  r2  shadow:openai#2          fail
    gc-002  sut.v1  r3  anthropic#1              pass
    gc-002  sut.v1  r3  anthropic#2              pass
    gc-002  sut.v1  r3  openai                   pass
    gc-002  sut.v1  r3  shadow:openai#1          pass
    gc-002  sut.v1  r3  shadow:openai#2          fail
    gc-002  sut.v2  r2  anthropic#1              pass
    gc-002  sut.v2  r2  anthropic#2              pass
    gc-002  sut.v2  r2  openai                   pass
    gc-002  sut.v2  r2  shadow:openai#1          pass
    gc-002  sut.v2  r2  shadow:openai#2          pass
    gc-002  sut.v2  r3  anthropic#1              pass
    gc-002  sut.v2  r3  anthropic#2              pass
    gc-002  sut.v2  r3  openai                   pass
    gc-002  sut.v2  r3  shadow:openai#1          pass
    gc-002  sut.v2  r3  shadow:openai#2          pass
    gc-003  sut.v1  r2  anthropic#1              pass
    gc-003  sut.v1  r2  anthropic#2              pass
    gc-003  sut.v1  r2  openai                   pass
    gc-003  sut.v1  r2  shadow:openai#1          pass
    gc-003  sut.v1  r2  shadow:openai#2          pass
    gc-003  sut.v1  r3  anthropic#1              pass
    gc-003  sut.v1  r3  anthropic#2              pass
    gc-003  sut.v1  r3  openai                   pass
    gc-003  sut.v1  r3  shadow:openai#1          pass
    gc-003  sut.v1  r3  shadow:openai#2          pass
    gc-003  sut.v2  r2  anthropic#1              pass
    gc-003  sut.v2  r2  anthropic#2              pass
    gc-003  sut.v2  r2  openai                   pass
    gc-003  sut.v2  r2  shadow:openai#1          fail
    gc-003  sut.v2  r2  shadow:openai#2          pass
    gc-003  sut.v2  r3  anthropic#1              pass
    gc-003  sut.v2  r3  anthropic#2              pass
    gc-003  sut.v2  r3  openai                   pass
    gc-003  sut.v2  r3  shadow:openai#1          fail
    gc-003  sut.v2  r3  shadow:openai#2          pass
    gc-004  sut.v1  r2  anthropic#1              pass
    gc-004  sut.v1  r2  anthropic#2              pass
    gc-004  sut.v1  r2  openai                   pass
    gc-004  sut.v1  r2  shadow:openai#1          pass
    gc-004  sut.v1  r2  shadow:openai#2          pass
    gc-004  sut.v1  r3  anthropic#1              pass
    gc-004  sut.v1  r3  anthropic#2              pass
    gc-004  sut.v1  r3  openai                   pass
    gc-004  sut.v1  r3  shadow:openai#1          pass
    gc-004  sut.v1  r3  shadow:openai#2          pass
    gc-004  sut.v2  r2  anthropic#1              pass
    gc-004  sut.v2  r2  anthropic#2              pass
    gc-004  sut.v2  r2  openai                   pass
    gc-004  sut.v2  r2  shadow:openai#1          pass
    gc-004  sut.v2  r2  shadow:openai#2          pass
    gc-004  sut.v2  r3  anthropic#1              pass
    gc-004  sut.v2  r3  anthropic#2              pass
    gc-004  sut.v2  r3  openai                   pass
    gc-004  sut.v2  r3  shadow:openai#1          pass
    gc-004  sut.v2  r3  shadow:openai#2          pass
    gc-005  sut.v1  r2  anthropic#1              pass
    gc-005  sut.v1  r2  anthropic#2              pass
    gc-005  sut.v1  r2  openai                   pass
    gc-005  sut.v1  r2  shadow:openai#1          pass
    gc-005  sut.v1  r2  shadow:openai#2          pass
    gc-005  sut.v1  r3  anthropic#1              pass
    gc-005  sut.v1  r3  anthropic#2              pass
    gc-005  sut.v1  r3  openai                   pass
    gc-005  sut.v1  r3  shadow:openai#1          pass
    gc-005  sut.v1  r3  shadow:openai#2          fail
    gc-005  sut.v2  r2  anthropic#1              pass
    gc-005  sut.v2  r2  anthropic#2              pass
    gc-005  sut.v2  r2  openai                   pass
    gc-005  sut.v2  r2  shadow:openai#1          pass
    gc-005  sut.v2  r2  shadow:openai#2          fail
    gc-005  sut.v2  r3  anthropic#1              pass
    gc-005  sut.v2  r3  anthropic#2              pass
    gc-005  sut.v2  r3  openai                   pass
    gc-005  sut.v2  r3  shadow:openai#1          pass
    gc-005  sut.v2  r3  shadow:openai#2          fail
    gc-006  sut.v1  r2  anthropic#1              pass
    gc-006  sut.v1  r2  anthropic#2              pass
    gc-006  sut.v1  r2  openai                   pass
    gc-006  sut.v1  r2  shadow:openai#1          pass
    gc-006  sut.v1  r2  shadow:openai#2          pass
    gc-006  sut.v1  r3  anthropic#1              pass
    gc-006  sut.v1  r3  anthropic#2              pass
    gc-006  sut.v1  r3  openai                   pass
    gc-006  sut.v1  r3  shadow:openai#1          pass
    gc-006  sut.v1  r3  shadow:openai#2          pass
    gc-006  sut.v2  r2  anthropic#1              pass
    gc-006  sut.v2  r2  anthropic#2              pass
    gc-006  sut.v2  r2  openai                   pass
    gc-006  sut.v2  r2  shadow:openai#1          pass
    gc-006  sut.v2  r2  shadow:openai#2          pass
    gc-006  sut.v2  r3  anthropic#1              pass
    gc-006  sut.v2  r3  anthropic#2              pass
    gc-006  sut.v2  r3  openai                   pass
    gc-006  sut.v2  r3  shadow:openai#1          pass
    gc-006  sut.v2  r3  shadow:openai#2          pass
    gc-007  sut.v1  r2  anthropic#1              pass
    gc-007  sut.v1  r2  anthropic#2              pass
    gc-007  sut.v1  r2  openai                   pass
    gc-007  sut.v1  r2  shadow:openai#1          pass
    gc-007  sut.v1  r2  shadow:openai#2          pass
    gc-007  sut.v1  r3  anthropic#1              pass
    gc-007  sut.v1  r3  anthropic#2              pass
    gc-007  sut.v1  r3  openai                   pass
    gc-007  sut.v1  r3  shadow:openai#1          pass
    gc-007  sut.v1  r3  shadow:openai#2          pass
    gc-007  sut.v2  r2  anthropic#1              pass
    gc-007  sut.v2  r2  anthropic#2              pass
    gc-007  sut.v2  r2  openai                   pass
    gc-007  sut.v2  r2  shadow:openai#1          pass
    gc-007  sut.v2  r2  shadow:openai#2          pass
    gc-007  sut.v2  r3  anthropic#1              pass
    gc-007  sut.v2  r3  anthropic#2              pass
    gc-007  sut.v2  r3  openai                   pass
    gc-007  sut.v2  r3  shadow:openai#1          pass
    gc-007  sut.v2  r3  shadow:openai#2          pass
    gc-008  sut.v1  r2  anthropic#1              pass
    gc-008  sut.v1  r2  anthropic#2              pass
    gc-008  sut.v1  r2  openai                   pass
    gc-008  sut.v1  r2  shadow:openai#1          pass
    gc-008  sut.v1  r2  shadow:openai#2          pass
    gc-008  sut.v1  r3  anthropic#1              pass
    gc-008  sut.v1  r3  anthropic#2              pass
    gc-008  sut.v1  r3  openai                   pass
    gc-008  sut.v1  r3  shadow:openai#1          pass
    gc-008  sut.v1  r3  shadow:openai#2          pass
    gc-008  sut.v2  r2  anthropic#1              pass
    gc-008  sut.v2  r2  anthropic#2              pass
    gc-008  sut.v2  r2  openai                   pass
    gc-008  sut.v2  r2  shadow:openai#1          pass
    gc-008  sut.v2  r2  shadow:openai#2          pass
    gc-008  sut.v2  r3  anthropic#1              pass
    gc-008  sut.v2  r3  anthropic#2              pass
    gc-008  sut.v2  r3  openai                   pass
    gc-008  sut.v2  r3  shadow:openai#1          pass
    gc-008  sut.v2  r3  shadow:openai#2          pass

Which slots were real
  anthropic#1              claude-opus-5      real (anthropic)
  anthropic#2              claude-sonnet-5    real (anthropic)
  openai                   gpt-5.6-terra      real (openai)
  shadow:openai#1          gpt-5.6-luna       real (openai)
  shadow:openai#2          gpt-4o             real (openai)
  degraded                         no, every slot above is a live model
  calibration cases                30 (gc-001..gc-030), both sut versions, 1 repeat
  consistency cases                8 (gc-001..gc-008), first 8 by case_id, 3 repeats
  calls made                       460

Per judge, against human labels (REAL MODELS)
  judge                     agree   kappa  falsePass  falseFail  abstain
  anthropic#1               0.933   0.857      0.000      0.100    0.000
  anthropic#2               0.983   0.963      0.000      0.025    0.000
  openai                    0.933   0.857      0.000      0.100    0.000
  PANEL                     0.933   0.857      0.000      0.100    0.000
  raw agreement is misleading on this skewed label set; kappa is the number to read

Shadow judges (measured, NON VOTING, never gate)
  judge                     agree   kappa  falsePass  falseFail  abstain
  shadow:openai#1           0.917   0.824      0.000      0.125    0.000
  shadow:openai#2           0.883   0.759      0.000      0.175    0.000

Panel
  unanimity_rate                   0.950
  split_rate                       0.050
  escalation_rate                  0.000
  best_single_judge                anthropic#2 (kappa 0.963)
  panel_kappa_vs_best_single_judge -0.106
  THE PANEL DID NOT BEAT ITS BEST MEMBER on real models: three judges cost 3x the calls and bought nothing measurable here
  pass_rate[sut.v1]                0.733
  pass_rate[sut.v2]                0.467

Pairwise error correlation, every pair of judges that ran
  pair                                             joint   indep   ratio  interpretation
  anthropic#1 + anthropic#2                        0.017   0.001   15.00  errors correlate; majority voting buys less than it appears to
  anthropic#1 + openai                             0.067   0.004   15.00  errors correlate; majority voting buys less than it appears to
  anthropic#2 + openai                             0.017   0.001   15.00  errors correlate; majority voting buys less than it appears to
  anthropic#1 + shadow:openai#1                    0.067   0.006   12.00  errors correlate; majority voting buys less than it appears to
  anthropic#1 + shadow:openai#2                    0.050   0.008    6.43  errors correlate; majority voting buys less than it appears to
  anthropic#2 + shadow:openai#1                    0.017   0.001   12.00  errors correlate; majority voting buys less than it appears to
  anthropic#2 + shadow:openai#2                    0.017   0.002    8.57  errors correlate; majority voting buys less than it appears to
  openai + shadow:openai#1                         0.067   0.006   12.00  errors correlate; majority voting buys less than it appears to
  openai + shadow:openai#2                         0.050   0.008    6.43  errors correlate; majority voting buys less than it appears to
  shadow:openai#1 + shadow:openai#2                0.050   0.010    5.14  errors correlate; majority voting buys less than it appears to

Self consistency, measured on 8 cases x 2 versions x 3 repeats
  flip_rate[anthropic#1]           0.000
  flip_rate[anthropic#2]           0.000
  flip_rate[openai]                0.000
  flip_rate[shadow:openai#1]       0.000
  flip_rate[shadow:openai#2]       0.250
  panel_flip_rate                  0.000
  REAL NOISE FLOOR                 0.000
  temperature, top_p and top_k are removed on claude-opus-5 and claude-sonnet-5 (HTTP 400), so this is measured rather than configured away. It is NOT compared against any gate threshold: this pass does not gate.

MEASURED cost, voting panel
  anthropic#1          claude-opus-5             92 calls    117192 in   10538 out  $  0.8494
  anthropic#2          claude-sonnet-5           92 calls    117192 in    8622 out  $  0.3206
  openai               gpt-5.6-terra             92 calls     49371 in    5260 out  $  0.2023
  TOTAL                                         276 calls    283755 in   24420 out  $  1.3723
  single judge (first slot) $0.8494  panel $1.3723  multiplier 1.62x
  prices as of 2026-08; claude-sonnet-5 is $2/$10 per MTok, the standard price (in effect today)

MEASURED cost, shadow bench (NON VOTING)
  shadow:openai#1      gpt-5.6-luna              92 calls     49371 in    8699 out  $  0.1016
  shadow:openai#2      gpt-4o                    92 calls     49463 in    4766 out  $  0.1713
  TOTAL                                         184 calls     98834 in   13465 out  $  0.2729
  single judge (first slot) $0.1016  panel $0.2729  multiplier 2.69x
  prices as of 2026-08; claude-sonnet-5 is $2/$10 per MTok, the standard price (in effect today)

  voting $1.3723  +  shadow $0.2729  =  combined $1.6452

Measured against the offline characters/4 estimate
  slot                        est in   real in   ratio     est $    real $
  anthropic#1                  53419    117192    2.19    0.4051    0.8494
  anthropic#2                  53419    117192    2.19    0.1620    0.3206
  openai                       53419     49371    0.92    0.2163    0.2023
  shadow:openai#1              53419     49371    0.92    0.0865    0.1016
  shadow:openai#2              53419     49463    0.93    0.1887    0.1713
  TOTAL                       267095    382589    1.43    1.0588    1.6452
  ratio above 1.00 means characters/4 UNDERCOUNTS the real prompt. That is the reason the offline number is labeled an approximation everywhere it appears and is never quoted as a token count.

Does judge cost buy judge accuracy (sorted by dollars per sweep)
  judge                    model              votes   kappa  falsePass   $/sweep
  shadow:openai#1          gpt-5.6-luna          no   0.824      0.000    0.1016
  shadow:openai#2          gpt-4o                no   0.759      0.000    0.1713
  openai                   gpt-5.6-terra        yes   0.857      0.000    0.2023
  anthropic#2              claude-sonnet-5      yes   0.963      0.000    0.3206
  anthropic#1              claude-opus-5        yes   0.857      0.000    0.8494
  cheapest shadow:openai#1 (gpt-5.6-luna, $0.1016) kappa 0.824
  dearest  anthropic#1 (claude-opus-5, $0.8494) kappa 0.857
  the kappa gap is +0.034 against measured noise 0.000, so the price difference is buying a real difference in agreement
  CHEAPER JUDGE WITHIN NOISE OF THE DEAREST: openai (gpt-5.6-terra, $0.2023) kappa 0.857 vs 0.857, gap +0.000. The extra $0.6471 per sweep is not buying measurable agreement.
  dollars are MEASURED: vendor reported token usage for this run, priced against the models that actually ran; prices as of 2026-08

Calls that produced no usable verdict: 0
  none. Every judge returned a readable verdict on every call, which is a good run and is not the claim: the abstention path is exercised offline by tests/test_real_pass.py precisely because no live run can be relied on to exercise it.

  NOTHING IN THIS SECTION GATES. No exit code, no deployment decision and no threshold reads any number above it. The gate ran on the deterministic mock panel in the sections before this one, because a regression gate has to be reproducible and a live model moves its own numbers between runs.
```

## Real model run (OpenAI primary, reduced)

A reduced sweep, and NOT independent evidence. With both credentials present
`AGENT_PROVIDER` only reorders the panel slots, so the same models judge the same
golden set; a full second sweep would be a near duplicate of the one above at the
same price. What this run does add is a smaller sample over the cases that
separate the two sut versions, which is the part a reduced run has to get
right: see the findings below.

```
ENV_FILE=~/.secrets/ai.env AGENT_PROVIDER=openai python scripts/run_demo.py \
    --real-cases 8 --real-repeat-cases 4 --real-case-selection discriminating
```

```
==============================================================================
11. The real model measurement pass (LIVE JUDGES, NEVER GATES)
==============================================================================
Before any call is made: the plan, the call count, and the estimate
  calibration pass      8 cases x 2 versions x 1 repeat = 16 judgments per judge
  consistency pass      4 cases x 2 versions x repeats 2..3 = 16 more per judge
  judges                3 voting + 2 shadow = 5
  TOTAL API CALLS       160  (32 per judge)
                        repeat 1 of every consistency case is reused from the calibration pass, so this is 40 fewer than the 200 two independent passes would cost
  slot                     model               calls    est in  est out     est $
  openai                   gpt-5.6-terra          32     18426     1920    0.0749
  anthropic#2              claude-opus-5          32     18426     1920    0.1401
  anthropic#3              claude-sonnet-5        32     18426     1920    0.0561
  shadow:openai#1          gpt-5.6-luna           32     18426     1920    0.0299
  shadow:openai#2          gpt-4o                 32     18426     1920    0.0653
  ESTIMATED COST        voting $0.2710  +  shadow $0.0952  =  $0.3663
  the estimate is the offline characters/4 approximation, priced against the models in the slots. It is NOT a token count. The measured figures below replace it, and the gap between the two is reported.

  calibration pass: 8 cases (discriminating: gc-015, gc-025, gc-016, gc-026, gc-017, gc-018, gc-019, gc-020), both sut versions, 1 repeat
    gc-015  sut.v1  r1  openai                   pass
    gc-015  sut.v1  r1  anthropic#2              pass
    gc-015  sut.v1  r1  anthropic#3              pass
    gc-015  sut.v1  r1  shadow:openai#1          pass
    gc-015  sut.v1  r1  shadow:openai#2          pass
    gc-015  sut.v2  r1  openai                   fail
    gc-015  sut.v2  r1  anthropic#2              fail
    gc-015  sut.v2  r1  anthropic#3              fail
    gc-015  sut.v2  r1  shadow:openai#1          fail
    gc-015  sut.v2  r1  shadow:openai#2          fail
    gc-025  sut.v1  r1  openai                   fail
    gc-025  sut.v1  r1  anthropic#2              fail
    gc-025  sut.v1  r1  anthropic#3              fail
    gc-025  sut.v1  r1  shadow:openai#1          fail
    gc-025  sut.v1  r1  shadow:openai#2          fail
    gc-025  sut.v2  r1  openai                   pass
    gc-025  sut.v2  r1  anthropic#2              pass
    gc-025  sut.v2  r1  anthropic#3              pass
    gc-025  sut.v2  r1  shadow:openai#1          pass
    gc-025  sut.v2  r1  shadow:openai#2          pass
    gc-016  sut.v1  r1  openai                   pass
    gc-016  sut.v1  r1  anthropic#2              pass
    gc-016  sut.v1  r1  anthropic#3              pass
    gc-016  sut.v1  r1  shadow:openai#1          pass
    gc-016  sut.v1  r1  shadow:openai#2          pass
    gc-016  sut.v2  r1  openai                   fail
    gc-016  sut.v2  r1  anthropic#2              fail
    gc-016  sut.v2  r1  anthropic#3              fail
    gc-016  sut.v2  r1  shadow:openai#1          fail
    gc-016  sut.v2  r1  shadow:openai#2          fail
    gc-026  sut.v1  r1  openai                   fail
    gc-026  sut.v1  r1  anthropic#2              fail
    gc-026  sut.v1  r1  anthropic#3              fail
    gc-026  sut.v1  r1  shadow:openai#1          fail
    gc-026  sut.v1  r1  shadow:openai#2          fail
    gc-026  sut.v2  r1  openai                   pass
    gc-026  sut.v2  r1  anthropic#2              pass
    gc-026  sut.v2  r1  anthropic#3              pass
    gc-026  sut.v2  r1  shadow:openai#1          pass
    gc-026  sut.v2  r1  shadow:openai#2          pass
    gc-017  sut.v1  r1  openai                   pass
    gc-017  sut.v1  r1  anthropic#2              pass
    gc-017  sut.v1  r1  anthropic#3              pass
    gc-017  sut.v1  r1  shadow:openai#1          pass
    gc-017  sut.v1  r1  shadow:openai#2          pass
    gc-017  sut.v2  r1  openai                   fail
    gc-017  sut.v2  r1  anthropic#2              fail
    gc-017  sut.v2  r1  anthropic#3              fail
    gc-017  sut.v2  r1  shadow:openai#1          fail
    gc-017  sut.v2  r1  shadow:openai#2          fail
    gc-018  sut.v1  r1  openai                   pass
    gc-018  sut.v1  r1  anthropic#2              pass
    gc-018  sut.v1  r1  anthropic#3              pass
    gc-018  sut.v1  r1  shadow:openai#1          pass
    gc-018  sut.v1  r1  shadow:openai#2          pass
    gc-018  sut.v2  r1  openai                   fail
    gc-018  sut.v2  r1  anthropic#2              fail
    gc-018  sut.v2  r1  anthropic#3              fail
    gc-018  sut.v2  r1  shadow:openai#1          fail
    gc-018  sut.v2  r1  shadow:openai#2          fail
    gc-019  sut.v1  r1  openai                   pass
    gc-019  sut.v1  r1  anthropic#2              pass
    gc-019  sut.v1  r1  anthropic#3              pass
    gc-019  sut.v1  r1  shadow:openai#1          pass
    gc-019  sut.v1  r1  shadow:openai#2          pass
    gc-019  sut.v2  r1  openai                   fail
    gc-019  sut.v2  r1  anthropic#2              fail
    gc-019  sut.v2  r1  anthropic#3              fail
    gc-019  sut.v2  r1  shadow:openai#1          fail
    gc-019  sut.v2  r1  shadow:openai#2          fail
    gc-020  sut.v1  r1  openai                   pass
    gc-020  sut.v1  r1  anthropic#2              pass
    gc-020  sut.v1  r1  anthropic#3              pass
    gc-020  sut.v1  r1  shadow:openai#1          pass
    gc-020  sut.v1  r1  shadow:openai#2          pass
    gc-020  sut.v2  r1  openai                   fail
    gc-020  sut.v2  r1  anthropic#2              fail
    gc-020  sut.v2  r1  anthropic#3              fail
    gc-020  sut.v2  r1  shadow:openai#1          fail
    gc-020  sut.v2  r1  shadow:openai#2          fail

  consistency pass: 4 cases (discriminating: gc-015, gc-025, gc-016, gc-026), both sut versions, repeats 2..3 (repeat 1 reused from above)
    gc-015  sut.v1  r2  openai                   pass
    gc-015  sut.v1  r2  anthropic#2              pass
    gc-015  sut.v1  r2  anthropic#3              pass
    gc-015  sut.v1  r2  shadow:openai#1          pass
    gc-015  sut.v1  r2  shadow:openai#2          pass
    gc-015  sut.v1  r3  openai                   pass
    gc-015  sut.v1  r3  anthropic#2              pass
    gc-015  sut.v1  r3  anthropic#3              pass
    gc-015  sut.v1  r3  shadow:openai#1          pass
    gc-015  sut.v1  r3  shadow:openai#2          pass
    gc-015  sut.v2  r2  openai                   fail
    gc-015  sut.v2  r2  anthropic#2              fail
    gc-015  sut.v2  r2  anthropic#3              fail
    gc-015  sut.v2  r2  shadow:openai#1          fail
    gc-015  sut.v2  r2  shadow:openai#2          fail
    gc-015  sut.v2  r3  openai                   fail
    gc-015  sut.v2  r3  anthropic#2              fail
    gc-015  sut.v2  r3  anthropic#3              fail
    gc-015  sut.v2  r3  shadow:openai#1          fail
    gc-015  sut.v2  r3  shadow:openai#2          fail
    gc-025  sut.v1  r2  openai                   fail
    gc-025  sut.v1  r2  anthropic#2              fail
    gc-025  sut.v1  r2  anthropic#3              fail
    gc-025  sut.v1  r2  shadow:openai#1          fail
    gc-025  sut.v1  r2  shadow:openai#2          fail
    gc-025  sut.v1  r3  openai                   fail
    gc-025  sut.v1  r3  anthropic#2              fail
    gc-025  sut.v1  r3  anthropic#3              fail
    gc-025  sut.v1  r3  shadow:openai#1          fail
    gc-025  sut.v1  r3  shadow:openai#2          fail
    gc-025  sut.v2  r2  openai                   pass
    gc-025  sut.v2  r2  anthropic#2              pass
    gc-025  sut.v2  r2  anthropic#3              pass
    gc-025  sut.v2  r2  shadow:openai#1          pass
    gc-025  sut.v2  r2  shadow:openai#2          pass
    gc-025  sut.v2  r3  openai                   pass
    gc-025  sut.v2  r3  anthropic#2              pass
    gc-025  sut.v2  r3  anthropic#3              pass
    gc-025  sut.v2  r3  shadow:openai#1          pass
    gc-025  sut.v2  r3  shadow:openai#2          pass
    gc-016  sut.v1  r2  openai                   pass
    gc-016  sut.v1  r2  anthropic#2              pass
    gc-016  sut.v1  r2  anthropic#3              pass
    gc-016  sut.v1  r2  shadow:openai#1          pass
    gc-016  sut.v1  r2  shadow:openai#2          pass
    gc-016  sut.v1  r3  openai                   pass
    gc-016  sut.v1  r3  anthropic#2              pass
    gc-016  sut.v1  r3  anthropic#3              pass
    gc-016  sut.v1  r3  shadow:openai#1          pass
    gc-016  sut.v1  r3  shadow:openai#2          pass
    gc-016  sut.v2  r2  openai                   fail
    gc-016  sut.v2  r2  anthropic#2              fail
    gc-016  sut.v2  r2  anthropic#3              fail
    gc-016  sut.v2  r2  shadow:openai#1          fail
    gc-016  sut.v2  r2  shadow:openai#2          fail
    gc-016  sut.v2  r3  openai                   fail
    gc-016  sut.v2  r3  anthropic#2              fail
    gc-016  sut.v2  r3  anthropic#3              fail
    gc-016  sut.v2  r3  shadow:openai#1          fail
    gc-016  sut.v2  r3  shadow:openai#2          fail
    gc-026  sut.v1  r2  openai                   fail
    gc-026  sut.v1  r2  anthropic#2              fail
    gc-026  sut.v1  r2  anthropic#3              fail
    gc-026  sut.v1  r2  shadow:openai#1          fail
    gc-026  sut.v1  r2  shadow:openai#2          fail
    gc-026  sut.v1  r3  openai                   fail
    gc-026  sut.v1  r3  anthropic#2              fail
    gc-026  sut.v1  r3  anthropic#3              fail
    gc-026  sut.v1  r3  shadow:openai#1          fail
    gc-026  sut.v1  r3  shadow:openai#2          fail
    gc-026  sut.v2  r2  openai                   pass
    gc-026  sut.v2  r2  anthropic#2              pass
    gc-026  sut.v2  r2  anthropic#3              pass
    gc-026  sut.v2  r2  shadow:openai#1          pass
    gc-026  sut.v2  r2  shadow:openai#2          pass
    gc-026  sut.v2  r3  openai                   pass
    gc-026  sut.v2  r3  anthropic#2              pass
    gc-026  sut.v2  r3  anthropic#3              pass
    gc-026  sut.v2  r3  shadow:openai#1          pass
    gc-026  sut.v2  r3  shadow:openai#2          pass

Which slots were real
  openai                   gpt-5.6-terra      real (openai)
  anthropic#2              claude-opus-5      real (anthropic)
  anthropic#3              claude-sonnet-5    real (anthropic)
  shadow:openai#1          gpt-5.6-luna       real (openai)
  shadow:openai#2          gpt-4o             real (openai)
  degraded                         no, every slot above is a live model
  calibration cases                8 (discriminating: gc-015, gc-025, gc-016, gc-026, gc-017, gc-018, gc-019, gc-020), both sut versions, 1 repeat
  consistency cases                4 (discriminating: gc-015, gc-025, gc-016, gc-026), 3 repeats
  calls made                       160

Per judge, against human labels (REAL MODELS)
  judge                     agree   kappa  falsePass  falseFail  abstain
  anthropic#2               1.000   1.000      0.000      0.000    0.000
  anthropic#3               1.000   1.000      0.000      0.000    0.000
  openai                    1.000   1.000      0.000      0.000    0.000
  PANEL                     1.000   1.000      0.000      0.000    0.000
  raw agreement is misleading on this skewed label set; kappa is the number to read

Shadow judges (measured, NON VOTING, never gate)
  judge                     agree   kappa  falsePass  falseFail  abstain
  shadow:openai#1           1.000   1.000      0.000      0.000    0.000
  shadow:openai#2           1.000   1.000      0.000      0.000    0.000

Panel
  unanimity_rate                   1.000
  split_rate                       0.000
  escalation_rate                  0.000
  best_single_judge                anthropic#2 (kappa 1.000)
  panel_kappa_vs_best_single_judge +0.000
  THE PANEL DID NOT BEAT ITS BEST MEMBER on real models: three judges cost 3x the calls and bought nothing measurable here
  pass_rate[sut.v1]                0.750
  pass_rate[sut.v2]                0.250

Pairwise error correlation, every pair of judges that ran
  pair                                             joint   indep   ratio  interpretation
  anthropic#2 + anthropic#3                        0.000   0.000    0.00  no shared errors to compare
  anthropic#2 + openai                             0.000   0.000    0.00  no shared errors to compare
  anthropic#3 + openai                             0.000   0.000    0.00  no shared errors to compare
  anthropic#2 + shadow:openai#1                    0.000   0.000    0.00  no shared errors to compare
  anthropic#2 + shadow:openai#2                    0.000   0.000    0.00  no shared errors to compare
  anthropic#3 + shadow:openai#1                    0.000   0.000    0.00  no shared errors to compare
  anthropic#3 + shadow:openai#2                    0.000   0.000    0.00  no shared errors to compare
  openai + shadow:openai#1                         0.000   0.000    0.00  no shared errors to compare
  openai + shadow:openai#2                         0.000   0.000    0.00  no shared errors to compare
  shadow:openai#1 + shadow:openai#2                0.000   0.000    0.00  no shared errors to compare

Self consistency, measured on 4 cases x 2 versions x 3 repeats
  flip_rate[anthropic#2]           0.000
  flip_rate[anthropic#3]           0.000
  flip_rate[openai]                0.000
  flip_rate[shadow:openai#1]       0.000
  flip_rate[shadow:openai#2]       0.000
  panel_flip_rate                  0.000
  REAL NOISE FLOOR                 0.000
  temperature, top_p and top_k are removed on claude-opus-5 and claude-sonnet-5 (HTTP 400), so this is measured rather than configured away. It is NOT compared against any gate threshold: this pass does not gate.

MEASURED cost, voting panel
  openai               gpt-5.6-terra             32 calls     17068 in    1907 out  $  0.0713
  anthropic#2          claude-opus-5             32 calls     40587 in    3297 out  $  0.2854
  anthropic#3          claude-sonnet-5           32 calls     40587 in    2798 out  $  0.1092
  TOTAL                                          96 calls     98242 in    8002 out  $  0.4658
  single judge (first slot) $0.0713  panel $0.4658  multiplier 6.54x
  prices as of 2026-08; claude-sonnet-5 is $2/$10 per MTok, the standard price (in effect today)

MEASURED cost, shadow bench (NON VOTING)
  shadow:openai#1      gpt-5.6-luna              32 calls     17068 in    3344 out  $  0.0371
  shadow:openai#2      gpt-4o                    32 calls     17100 in    1765 out  $  0.0604
  TOTAL                                          64 calls     34168 in    5109 out  $  0.0975
  single judge (first slot) $0.0371  panel $0.0975  multiplier 2.63x
  prices as of 2026-08; claude-sonnet-5 is $2/$10 per MTok, the standard price (in effect today)

  voting $0.4658  +  shadow $0.0975  =  combined $0.5633

Measured against the offline characters/4 estimate
  slot                        est in   real in   ratio     est $    real $
  openai                       18426     17068    0.93    0.0749    0.0713
  anthropic#2                  18426     40587    2.20    0.1401    0.2854
  anthropic#3                  18426     40587    2.20    0.0561    0.1092
  shadow:openai#1              18426     17068    0.93    0.0299    0.0371
  shadow:openai#2              18426     17100    0.93    0.0653    0.0604
  TOTAL                        92130    132410    1.44    0.3663    0.5633
  ratio above 1.00 means characters/4 UNDERCOUNTS the real prompt. That is the reason the offline number is labeled an approximation everywhere it appears and is never quoted as a token count.

Does judge cost buy judge accuracy (sorted by dollars per sweep)
  judge                    model              votes   kappa  falsePass   $/sweep
  shadow:openai#1          gpt-5.6-luna          no   1.000      0.000    0.0371
  shadow:openai#2          gpt-4o                no   1.000      0.000    0.0604
  openai                   gpt-5.6-terra        yes   1.000      0.000    0.0713
  anthropic#3              claude-sonnet-5      yes   1.000      0.000    0.1092
  anthropic#2              claude-opus-5        yes   1.000      0.000    0.2854
  cheapest shadow:openai#1 (gpt-5.6-luna, $0.0371) kappa 1.000
  dearest  anthropic#2 (claude-opus-5, $0.2854) kappa 1.000
  THE CHEAPEST JUDGE'S KAPPA IS WITHIN NOISE OF THE MOST EXPENSIVE: gap +0.000, measured noise 0.000. On this golden set the extra spend is not buying agreement with humans.
  CHEAPER JUDGE WITHIN NOISE OF THE DEAREST: shadow:openai#1 (gpt-5.6-luna, $0.0371) kappa 1.000 vs 1.000, gap +0.000. The extra $0.2482 per sweep is not buying measurable agreement.
  dollars are MEASURED: vendor reported token usage for this run, priced against the models that actually ran; prices as of 2026-08

Calls that produced no usable verdict: 0
  none. Every judge returned a readable verdict on every call, which is a good run and is not the claim: the abstention path is exercised offline by tests/test_real_pass.py precisely because no live run can be relied on to exercise it.

  NOTHING IN THIS SECTION GATES. No exit code, no deployment decision and no threshold reads any number above it. The gate ran on the deterministic mock panel in the sections before this one, because a regression gate has to be reproducible and a live model moves its own numbers between runs.
```

## What the real model runs found

Seven findings, and most of them are negative results about this design rather
than confirmations of it. That is the reason they are published: a real model run
that only confirmed the offline story would be evidence that nobody looked.

Honest limit 2 applies to every number below. Kappa here is measured over 60
judged units per judge (30 cases x 2 sut versions) with no confidence interval,
and the reduced run's over 16. Read the leading digit.

These runs used `judge.v2`, which fences the candidate answer in
`<candidate_answer>` tags; the captures before 2026-09-26 used `judge.v1`. On
samples this small no change between the two can be put down to the fence.

### 1. The panel lost to a single judge, and this time the mechanism is visible

Panel kappa 0.857 against `claude-sonnet-5` alone at 0.963, a gap of -0.106. The
panel cost $1.3723 against $0.8494 for the first slot on its own, a 1.62x
multiplier for a worse answer. This harness prints the finding itself, in
capitals, because a negative number in a table is easy to skim past.

Offline, the same comparison is +0.000: the mock panel exactly ties its best
member. So the mock panel and the real panel agree that three judges bought
nothing, and disagree about whether they cost something.

### 2. The three voting judges' errors are COMPLETELY NESTED, across two vendors

Every voting pair reports an independence ratio of exactly 15.00, and that
number is not a ceiling, a clamp or a coincidence. The ratio is `joint / (rate_a
* rate_b)`, so it equals `1 / rate_a` exactly when `joint == rate_b`, that is,
when one judge's errors are a subset of the other's. Error counts say the
same thing directly:

```
claude-opus-5    4 errors / 60 units      opus + sonnet   joint 1 = all of sonnet's
claude-sonnet-5  1 error  / 60 units      opus + terra    joint 4 = all of both
gpt-5.6-terra    4 errors / 60 units      sonnet + terra  joint 1 = all of sonnet's
```

`claude-opus-5` and `gpt-5.6-terra` were wrong on identically the same four
units, and `claude-sonnet-5` got three of those four right that both others
missed. There is no unit on which any judge is wrong alone.

This is the mechanism behind finding 1. A majority vote can only correct an
error a minority makes; when every error is shared, the majority follows the
wrong answer every time, so the panel's 0.857 is exactly `claude-opus-5`'s and
exactly `gpt-5.6-terra`'s. It is also the sharpest available refutation of the
cross-vendor independence assumption this project was built to question: two
vendors, two model families, one set of mistakes.

CAVEAT, and it is load bearing: this is four error units out of sixty. The
nesting is exact, but it is exact over a very small number of errors, and a
fifth error landing anywhere would change the ratio. The 15.00 is a real
structural measurement, taken on a sample too small to settle it. Do not quote
finding 2 without this paragraph.

### 3. A judge at a quarter of the price tied the most expensive, exactly

`gpt-5.6-terra`, a voting judge, scored kappa 0.857 at $0.2023 per sweep.
`claude-opus-5` scored kappa 0.857 at $0.8494: 4.2x the price for the same
number, gap +0.000. The cheapest judge of all, `gpt-5.6-luna` in a non voting
shadow slot, scored 0.824 at $0.1016, and the harness reports that +0.034 gap
to the dearest as a real one against a measured noise floor of 0.000. The best
judge in the run was neither: the mid-priced `claude-sonnet-5` at 0.963 for $0.3206.

The offline mock panel ranks these models in almost the opposite order:
`mock-verbosity`, which stands in for `gpt-5.6-luna`, is the worst judge in the
whole bench at kappa 0.108. That inversion is the single best illustration of why
this repository says its offline numbers are evidence about the *harness* and
never about the *models*, and why no offline figure here is quoted as a fact
about `claude-opus-5`, `claude-sonnet-5`, `gpt-5.6-terra`, `gpt-5.6-luna` or
`gpt-4o`.

### 4. The real noise floor was 0.000, and that survived a harder subset

All three voting judges returned the same verdict on every repeat: flip rate
0.000 each, panel flip rate 0.000. That is despite judge variance being
impossible to configure away here. `claude-opus-5` and `claude-sonnet-5` reject
`temperature`, `top_p` and `top_k` with HTTP 400, so the number had to be
measured.

One shadow judge did flip: `gpt-4o` at 0.250, and `gpt-5.6-luna` at 0.000. That
is its own argument for the shadow bench not voting.

The obvious objection to a 0.000 floor is that the full run measures consistency
on the first eight cases by id, which are the easiest in the set. The reduced
run below re-measured it on cases the two versions actually disagree about and
still got 0.000, so the zero is not an artifact of an easy subset. It remains
unmeasured on the four genuinely ambiguous cases, which are labeled the same in
both versions and therefore cannot appear in a discriminating subset at all.
Those are the cases most likely to flip, and measuring them needs a third
selection mode that does not exist yet.

### 5. The cost estimate undercounts, asymmetrically, and reproducibly

The offline `characters/4` approximation was 2.19x low on the Anthropic prompts
and accurate to 0.92-0.93 on OpenAI. Measured $1.6452 against an estimated
$1.0588. The ratio quoted here and below is the token ratio, 1.43. The ratio of
those two dollar figures is 1.55, because the estimate and the measurement do
not share a per-model price mix.

A reduced run reproduced all three figures on a different sample and a
different set of cases: 2.20x Anthropic, 0.93 OpenAI, 1.44 total, against a
measured $0.5633. Two runs, same ratios. This is why the offline dollar figure is
labeled an approximation everywhere it appears and is never quoted as a token
count.

### 6. The reduced run measured nothing again: for the OPPOSITE reason, and that is the finding

The first attempt at a reduced sweep bought `gc-001..gc-008`, the first eight
cases by id. Every judge scored kappa 1.000 and both sut versions passed 100% of
them. That kappa is undefined, because every label and every verdict was pass
(the code reports 0.000 for it), and the eight are grounded cases that pass in
both versions: the subset contained nothing the two versions disagree about.
That is this project's own `cases_that_never_discriminate` warning, arriving as
a paid capture, and the reason `--real-case-selection discriminating` now exists
and is tested.

The selection works. The subset it buys separates the versions cleanly, 0.750 to
0.250, which is exactly the human labels for those eight cases.

Every judge still scored kappa 1.000. The cases where the two sut versions
disagree are the blatant ones, invented figures and outright deferrals, and
every model gets those right. The cases the judges actually get wrong are the
four ambiguous rounding cases, and those are labeled the same in both
versions, so they can never enter a discriminating subset.

So, on this golden set:

> **The cases that discriminate between VERSIONS and the cases that discriminate
> between JUDGES are disjoint sets.**

A reduced sweep can measure the gate's signal or the judges' disagreement. It
cannot measure both, and it is not a substitute for the full 30. The fix shipped
here solves the first failure and is honest about not solving the second.

### 7. Zero abstentions and zero incidents, which is a good run and not a claim

Every judge returned a readable verdict on all 460 calls, and on all 160 of the
reduced run. No slot was degraded; both runs were three models across two vendors
with two more in shadow. That is reported as what happened, not as evidence the
failure handling works: the abstention path is exercised offline by
`tests/test_real_pass.py` precisely because no live run can be relied on to
exercise it.

### What did NOT change as a result of these runs

The gate, the thresholds and the offline numbers. Nothing measured above feeds an
exit code, by construction, and none of it was used to tune a threshold after the
fact. The one code change these runs caused is the discriminating case selection
in finding 6, which is in the claims table with the test that pins it.
