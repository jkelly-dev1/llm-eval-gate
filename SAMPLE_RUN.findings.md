<!--
SOURCE FILE. This is the hand written section of SAMPLE_RUN.md, kept separate so
that everything else in that document can be regenerated from capture files
without a human touching it. Edit this file, then rebuild:

    .venv/bin/python scripts/build_sample_run.py --demo ... --findings SAMPLE_RUN.findings.md ...

Editing the copy inside SAMPLE_RUN.md instead will be overwritten by the next build.
-->

## What the real model runs found

Seven findings, and most of them are negative results about this design rather
than confirmations of it. That is the reason they are published: a real model run
that only confirmed the offline story would be evidence that nobody looked.

Honest limit 2 applies to every number below. Kappa here is measured over 60
judged units per judge (30 cases x 2 sut versions) with no confidence interval,
and the reduced run's over 16. Read the leading digit.

### 1. The panel lost to a single judge, and this time the mechanism is visible

Panel kappa 0.857 against `claude-sonnet-5` alone at 0.892, a gap of -0.034. The
panel cost $1.3089 against $0.8130 for the first slot on its own, a 1.61x
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
claude-opus-5    4 errors / 60 units      opus + sonnet   joint 3 = all of sonnet's
claude-sonnet-5  3 errors / 60 units      opus + terra    joint 4 = all of both
gpt-5.6-terra    4 errors / 60 units      sonnet + terra  joint 3 = all of sonnet's
```

`claude-opus-5` and `gpt-5.6-terra` were wrong on identically the same four
units, and `claude-sonnet-5` got one of those four right that both others
missed. There is no unit on which any judge is wrong alone.

This is the mechanism behind finding 1. A majority vote can only correct an
error a minority makes; when every error is shared, the majority follows the
wrong answer every time, so the panel's 0.857 is exactly `claude-opus-5`'s and
exactly `gpt-5.6-terra`'s. It is also the sharpest available refutation of the
cross-vendor independence assumption this project was built to question: two
vendors, two model families, one set of mistakes.

CAVEAT, and it is load bearing: this is four error units out of sixty. The
nesting is exact, but it is exact over a very small number of errors, and a
fifth error landing anywhere would change the ratio. The previous capture read
the same 15.00 as "a small-sample ceiling rather than a measurement", which was
half right: it is a real structural measurement, taken on a sample too small to
settle it. Do not quote finding 2 without this paragraph.

### 3. The cheapest judge tied the most expensive, exactly

`gpt-5.6-luna` in a non voting shadow slot scored kappa 0.857 at $0.0952 per
sweep. `claude-opus-5` scored kappa 0.857 at $0.8130: 8.5x the price for the
same number, gap +0.000. The best judge in the run was neither: the mid-priced
`claude-sonnet-5` at 0.892 for $0.3085.

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

The shadow judges did flip: `gpt-5.6-luna` at 0.062 and `gpt-4o` at 0.125. That
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

The offline `characters/4` approximation was 2.35x low on the Anthropic prompts
and accurate to 0.94-0.95 on OpenAI. Measured $1.5576 against an estimated
$0.9490. The ratio quoted here and below is the token ratio, 1.51. The ratio of
those two dollar figures is 1.64, because the estimate and the measurement do
not share a per-model price mix.

A reduced run reproduced all three figures on a different sample and a
different set of cases: 2.37x Anthropic, 0.95 OpenAI, 1.51 total, against a
measured $0.5320. Two runs, same ratios. This is why the offline dollar figure is
labeled an approximation everywhere it appears and is never quoted as a token
count.

### 6. The reduced run measured nothing again: for the OPPOSITE reason, and that is the finding

The first attempt at a reduced sweep bought `gc-001..gc-008`, the first eight
cases by id. Every judge scored kappa 1.000 and both sut versions passed 100% of
them, because those eight are grounded cases that pass in both versions: the
subset contained nothing the two versions disagree about. That is this project's
own `cases_that_never_discriminate` warning, arriving as a paid capture, and it
is the reason `--real-case-selection discriminating` now exists and is tested.

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
