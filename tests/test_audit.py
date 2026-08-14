"""The append only hash chained log, and what it can still be trusted to prove.

An audit log's only value is that it cannot be quietly revised after the fact, so
the property asserted here is not that records are written but that editing,
reordering or removing one is detectable. `prev_hash` is inside each record's hashed
payload, which is what turns the file into a chain rather than a list of
independently hashed lines: without it every record would still verify against its
own content while a deleted record went unnoticed.

The determinism boundary is stated rather than worked around. Two runs that made the
same decisions produce the same hashes for everything except the timestamped fields,
because canonical JSON with sorted keys is what makes a record hashable in the first
place. Timestamps are NOT frozen: a faked timestamp in an audit trail is worse than
an honest one that varies, since the whole value of the log is that it says when a
decision was made. So the tests below hold the timestamp fixed to assert key order
independence, and then show that a different timestamp does change the hash.
"""

from __future__ import annotations

import json
from pathlib import Path

from eval_gate.audit import GENESIS_HASH, AuditLog, compute_record_hash
from eval_gate.baseline import load_baseline, write_baseline
from eval_gate.models import AuditRecord

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_the_audit_chain_detects_a_tampered_record(
    audit_log, three_chained_records, hashing_without_prev_hash, tmp_path
):
    """Editing a past record breaks every hash after it, and the log says so.

    Mutation check, executed in-test: prev_hash is dropped from the hashed payload
    and a record is then EXCISED from the middle of a rebuilt chain with the
    survivors relinked. Under the real hashing that excision breaks a hash and
    verify_chain returns False. Under the mutation each record still verifies
    against its own content, the links still line up, and the missing record goes
    undetected, which is the difference between a chain and a list of independently
    hashed lines.
    """
    three_chained_records(audit_log)
    assert audit_log.verify_chain() is True
    assert len(audit_log.read_all()) == 3

    lines = audit_log.path.read_text(encoding="utf-8").strip().splitlines()
    tampered = json.loads(lines[1])
    tampered["payload"]["verdict"] = "fail"
    lines[1] = json.dumps(tampered)
    audit_log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert audit_log.verify_chain() is False

    def excise_the_middle_record(log: AuditLog) -> None:
        rows = [json.loads(line) for line in log.path.read_text(encoding="utf-8").strip().splitlines()]
        survivor = rows[2]
        survivor["prev_hash"] = rows[0]["record_hash"]
        log.path.write_text(
            "\n".join(json.dumps(row) for row in (rows[0], survivor)) + "\n", encoding="utf-8"
        )

    honest = AuditLog(tmp_path / "honest.audit.jsonl")
    three_chained_records(honest)
    excise_the_middle_record(honest)
    assert honest.verify_chain() is False

    hashing_without_prev_hash()
    mutant = AuditLog(tmp_path / "mutant.audit.jsonl")
    three_chained_records(mutant)
    assert mutant.verify_chain() is True, "the mutant chain must be internally consistent first"
    excise_the_middle_record(mutant)
    assert mutant.verify_chain() is True, (
        "with prev_hash outside the hashed payload, deleting a record is undetectable"
    )


def test_the_audit_hash_is_stable_across_key_order(tmp_path):
    """Canonical JSON with sort_keys=True, so two runs that decided the same agree.

    Timestamps are excluded from the determinism claim rather than frozen. A faked
    timestamp in an audit trail is worse than an honest one that varies, since the
    whole value of the log is that it says when a decision was made. So this test
    holds the timestamp fixed and varies only the key order, then shows that a
    different timestamp does change the hash, which is the documented boundary
    stated out loud instead of quietly worked around.
    """
    stamp = "2026-07-27T01:40:44.757182+00:00"
    payload = {"panel_mode": "honest", "exit_code": 0, "failures": ["a", "b"]}
    reordered = {"failures": ["a", "b"], "exit_code": 0, "panel_mode": "honest"}

    first = AuditRecord(
        event="gate_decision", run_id="run-89f64910a655", timestamp=stamp,
        payload=payload, prev_hash=GENESIS_HASH,
    )
    second = AuditRecord(
        event="gate_decision", run_id="run-89f64910a655", timestamp=stamp,
        payload=reordered, prev_hash=GENESIS_HASH,
    )
    assert list(payload) != list(reordered), "the two payloads must differ in key order"
    assert compute_record_hash(first) == compute_record_hash(second)

    later = AuditRecord(
        event="gate_decision", run_id="run-89f64910a655",
        timestamp="2026-07-27T02:00:00.000000+00:00",
        payload=payload, prev_hash=GENESIS_HASH,
    )
    assert compute_record_hash(later) != compute_record_hash(first)

    # The same canonical rule is what makes the committed baseline a readable diff.
    record = load_baseline(REPO_ROOT / "baseline.json")
    written = write_baseline(tmp_path / "baseline.json", record)
    keys = list(json.loads(written.read_text(encoding="utf-8")))
    assert keys == sorted(keys)


def test_a_record_spliced_from_another_log_breaks_the_chain(tmp_path):
    """The LINK is the only thing that catches a record which is itself valid.

    Every other attack on this chain also disturbs the record's own contents
    hash. Editing changes the payload, and excising leaves a relinked survivor
    whose hash was computed over the old link. A record lifted from a
    DIFFERENT log keeps a hash that verifies against its own contents and a
    prev_hash that points into the wrong history, so the contents check passes
    and only the link comparison can refuse it.

    Mutation check, executed against the verifier: delete the
    `record.prev_hash != previous` comparison in `verify_chain` and this test
    goes red while the tampered-record test above stays green.
    """
    log_a = AuditLog(tmp_path / "a.audit.jsonl")
    log_b = AuditLog(tmp_path / "b.audit.jsonl")

    log_a.append("run_started", "run-aaaaaaaaaaaa", {"cases": 30})
    log_a.append("case_scored", "run-aaaaaaaaaaaa", {"case_id": "gc-001", "verdict": "pass"})
    log_b.append("run_started", "run-bbbbbbbbbbbb", {"cases": 12})
    log_b.append("case_scored", "run-bbbbbbbbbbbb", {"case_id": "gc-002", "verdict": "fail"})

    assert log_a.verify_chain() is True
    assert log_b.verify_chain() is True

    rows_a = [json.loads(l) for l in log_a.path.read_text(encoding="utf-8").strip().splitlines()]
    rows_b = [json.loads(l) for l in log_b.path.read_text(encoding="utf-8").strip().splitlines()]

    donor = rows_b[1]
    assert donor["prev_hash"] != rows_a[0]["record_hash"], "the two logs must differ"
    assert (
        compute_record_hash(AuditRecord.model_validate(donor)) == donor["record_hash"]
    ), "the donor must be internally valid, or this tests the wrong thing"

    log_a.path.write_text(
        "\n".join(json.dumps(row) for row in (rows_a[0], donor)) + "\n", encoding="utf-8"
    )
    assert log_a.verify_chain() is False, "a spliced but internally valid record was accepted"
