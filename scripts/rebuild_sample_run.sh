#!/usr/bin/env bash
# Rebuild SAMPLE_RUN.md from the captures. One short line instead of the six
# wrapping path arguments the underlying build takes.
#
#     bash scripts/rebuild_sample_run.sh <demo.txt> <gate-dir> <YYYY-MM-DD>
#
# Inputs, all looked for in the repository root:
#   SAMPLE_RUN.findings.md            the one hand written section, committed
#   SAMPLE_RUN.anthropic.local.md     real model capture, gitignored
#   SAMPLE_RUN.openai.local.md        real model capture, gitignored
#
# The two captures are 100KB of run output each and match *.local.md in
# .gitignore, so they are never committed. If they are being kept somewhere
# else, point CAPTURES at that directory rather than editing this script:
#
#     CAPTURES=/path/to/captures bash scripts/rebuild_sample_run.sh ...
set -euo pipefail

DEMO="${1:?usage: rebuild_sample_run.sh <demo.txt> <gate-dir> <YYYY-MM-DD>}"
GATE_DIR="${2:?missing gate capture directory}"
CAPTURED_ON="${3:?missing capture date, YYYY-MM-DD}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CAPTURES="${CAPTURES:-$ROOT}"

FINDINGS="$ROOT/SAMPLE_RUN.findings.md"
ANTHROPIC="$CAPTURES/SAMPLE_RUN.anthropic.local.md"
OPENAI="$CAPTURES/SAMPLE_RUN.openai.local.md"

for input in "$FINDINGS" "$ANTHROPIC" "$OPENAI"; do
    if [[ ! -f "$input" ]]; then
        echo "missing input: $input" >&2
        echo "the *.local.md captures are gitignored run output; set CAPTURES=<dir>" >&2
        echo "if they are kept outside the repository." >&2
        exit 1
    fi
done

"$ROOT/.venv/bin/python" "$ROOT/scripts/build_sample_run.py" \
    --demo "$DEMO" \
    --gate-dir "$GATE_DIR" \
    --anthropic "$ANTHROPIC" \
    --openai "$OPENAI" \
    --findings "$FINDINGS" \
    --captured-on "$CAPTURED_ON"
