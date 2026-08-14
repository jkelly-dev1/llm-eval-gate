#!/usr/bin/env bash
# The five gate invocations the README pins, each run and each exit code
# printed. Run from the repository root:
#
#     .venv/bin/bash scripts/verify_gate_invocations.sh
#
# or simply:  bash scripts/verify_gate_invocations.sh
#
# Nothing here writes to the committed baselines: --record-baseline is not used.
set -u

PY=.venv/bin/python
OUT="${1:-/tmp/gate-invocations}"
mkdir -p "$OUT"

run() {
    local name="$1"
    shift
    "$PY" -m eval_gate.evals.gate "$@" > "$OUT/$name.txt" 2>&1
    local code=$?
    # Recorded in the capture itself, because the exit code is the claim and a
    # capture that does not carry it asks the reader to take it on trust.
    echo "EXIT=$code" >> "$OUT/$name.txt"
    printf '%-28s exit %d  %s\n' "$name" "$code" \
        "$(grep -m1 'exit driven by' "$OUT/$name.txt" | sed 's/^ *exit driven by: //')"
}

run default
run candidate-v2            --candidate sut.v2
run captured-own-baseline   --panel two_miscalibrated --candidate sut.v2 \
                            --baseline baseline.two_miscalibrated.json
run captured-wrong-baseline --panel two_miscalibrated --candidate sut.v2
run threshold-in-the-noise  --threshold 0.05

echo
echo "full output per invocation: $OUT"
