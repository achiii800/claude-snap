"""
Tests for the ctxport codec.

Key property: pack-then-unpack restores original event payloads in order.
"""

import json
from pathlib import Path

from ctxport import codec


EXAMPLE = Path(__file__).parent.parent / "examples" / "synthetic_session.jsonl"


def _canonical_jsonl_lines(records):
    """Serialize records the same way write_jsonl does — for byte comparison."""
    return [json.dumps(r, ensure_ascii=False) for r in records]


def test_roundtrip_payloads_match():
    """unpack(pack(parse(x))) == [ev.payload for ev in parse(x)]"""
    events = codec.parse(str(EXAMPLE))
    packed = codec.pack(events)
    unpacked = codec.unpack(packed)

    assert len(unpacked) == len(events), \
        f"event count mismatch: {len(unpacked)} vs {len(events)}"

    for ev, restored in zip(events, unpacked):
        assert restored == ev.payload, \
            f"payload mismatch at seq={ev.seq}"


def test_roundtrip_byte_identical():
    """Re-serialized unpacked JSONL must match re-serialized original lines."""
    original_dicts = list(codec._read_jsonl(str(EXAMPLE)))
    events = codec.parse(str(EXAMPLE))
    packed = codec.pack(events)
    unpacked = codec.unpack(packed)

    orig_bytes = _canonical_jsonl_lines(original_dicts)
    unpacked_bytes = _canonical_jsonl_lines(unpacked)

    assert len(orig_bytes) == len(unpacked_bytes)
    for i, (a, b) in enumerate(zip(orig_bytes, unpacked_bytes)):
        assert a == b, f"byte mismatch at line {i}:\n  orig: {a}\n  back: {b}"


def test_compression_reduces_byte_count():
    events = codec.parse(str(EXAMPLE))
    packed = codec.pack(events)
    s = codec.stats(packed)
    assert s["refs"] > 0, "expected at least one ref to be introduced"
    assert s["compression_ratio"] > 1.0, \
        f"expected compression > 1.0, got {s['compression_ratio']}"


def test_no_user_or_assistant_msg_is_dedup_d():
    """Conversational turns must always pass through verbatim."""
    events = codec.parse(str(EXAMPLE))
    packed = codec.pack(events)

    user_assistant_in = sum(
        1 for ev in events if ev.kind in ("user_msg", "assistant_msg")
    )
    user_assistant_out = sum(
        1 for x in packed
        if x.get("type") == "ctxport_event"
        and x.get("kind") in ("user_msg", "assistant_msg")
    )
    assert user_assistant_in == user_assistant_out


def test_mutation_invalidates_dedup():
    """If a file is Edit'd, a subsequent Read should NOT be ref'd."""
    events = codec.parse(str(EXAMPLE))
    packed = codec.pack(events)

    full_event_tool_ids = [
        x.get("tool_id") for x in packed
        if x.get("type") == "ctxport_event" and x.get("kind") == "tool_result"
    ]
    assert "toolu_01" in full_event_tool_ids, \
        "first read of client.py should be a full event"
    assert "toolu_06" in full_event_tool_ids, \
        "post-edit read of client.py should be a full event (content changed)"


def test_repeat_read_is_ref_d():
    """The second read of an unchanged file is ref'd."""
    events = codec.parse(str(EXAMPLE))
    packed = codec.pack(events)
    ref_tool_ids = [
        x.get("tool_id") for x in packed if x.get("type") == "ctxport_ref"
    ]
    assert "toolu_03" in ref_tool_ids, \
        f"expected toolu_03 (re-read of unchanged client.py) to be ref'd. " \
        f"got refs: {ref_tool_ids}"


def test_repeat_bash_with_same_output_is_ref_d():
    """Repeated identical Bash output should be ref'd."""
    events = codec.parse(str(EXAMPLE))
    packed = codec.pack(events)
    ref_tool_ids = [
        x.get("tool_id") for x in packed if x.get("type") == "ctxport_ref"
    ]
    assert "toolu_08" in ref_tool_ids, \
        f"expected toolu_08 (repeat pytest output) to be ref'd. " \
        f"got refs: {ref_tool_ids}"


def test_real_format_metadata_is_preserved_through_dedup():
    """
    Regression: a ref'd event must restore byte-identical including per-event
    metadata (uuid, parentUuid, timestamp) that varies between two events
    even when their dedup-relevant content is identical.
    """
    import json, tempfile, os
    # Two Bash calls with identical stdout but different uuids/timestamps.
    events_raw = [
        {"type": "assistant", "uuid": "a1", "timestamp": "T1",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "tu1", "name": "Bash",
              "input": {"command": "pytest -q"}}]}},
        {"type": "user", "uuid": "u1", "parentUuid": "a1", "timestamp": "T2",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "tu1",
              "content": "1 passed in 0.04s\n"}]},
         "toolUseResult": {"stdout": "1 passed in 0.04s\n", "stderr": ""}},
        {"type": "assistant", "uuid": "a2", "timestamp": "T3",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "tu2", "name": "Bash",
              "input": {"command": "pytest -q"}}]}},
        {"type": "user", "uuid": "u2", "parentUuid": "a2", "timestamp": "T4",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "tu2",
              "content": "1 passed in 0.04s\n"}]},
         "toolUseResult": {"stdout": "1 passed in 0.04s\n", "stderr": ""}},
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        for e in events_raw:
            f.write(json.dumps(e) + "\n")
        path = f.name
    try:
        events = codec.parse(path)
        packed = codec.pack(events)
        unpacked = codec.unpack(packed)
        # The second tool_result should be ref'd (duplicate Bash output).
        refs = [x for x in packed if x.get("type") == "ctxport_ref"]
        assert len(refs) == 1, f"expected 1 ref, got {len(refs)}"
        # And the restored payload must equal the original byte-for-byte.
        assert unpacked == events_raw
    finally:
        os.unlink(path)


def test_edit_results_are_never_dedup_d():
    """Edit/Write tool_results carry per-call data — must not be ref'd
    even when the visible success message collides."""
    import json, tempfile, os
    events_raw = [
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu1", "name": "Edit",
             "input": {"file_path": "/a.py", "old_string": "x", "new_string": "y"}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu1",
             "content": "File edited successfully."}]},
         "toolUseResult": {"oldString": "x", "newString": "y"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu2", "name": "Edit",
             "input": {"file_path": "/b.py", "old_string": "p", "new_string": "q"}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu2",
             "content": "File edited successfully."}]},
         "toolUseResult": {"oldString": "p", "newString": "q"}},
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        for e in events_raw:
            f.write(json.dumps(e) + "\n")
        path = f.name
    try:
        events = codec.parse(path)
        packed = codec.pack(events)
        unpacked = codec.unpack(packed)
        refs = [x for x in packed if x.get("type") == "ctxport_ref"]
        assert refs == [], f"Edit results must not be ref'd, got: {refs}"
        assert unpacked == events_raw
    finally:
        os.unlink(path)


def test_dangling_ref_is_handled():
    """Unpack should not crash on refs whose target is missing."""
    bogus = [
        {"type": "ctxport_header", "version": "0.1.0",
         "original_event_count": 0},
        {"type": "ctxport_ref", "kind": "ref", "seq": 5, "ref_to": "deadbeef",
         "reason": "unchanged_read"},
        {"type": "ctxport_footer", "events_in": 0, "events_out": 1,
         "refs_introduced": 1},
    ]
    out = codec.unpack(bogus)
    assert len(out) == 1
    assert out[0].get("type") == "ctxport_dangling_ref"


if __name__ == "__main__":
    test_roundtrip_payloads_match()
    test_roundtrip_byte_identical()
    test_compression_reduces_byte_count()
    test_no_user_or_assistant_msg_is_dedup_d()
    test_mutation_invalidates_dedup()
    test_repeat_read_is_ref_d()
    test_repeat_bash_with_same_output_is_ref_d()
    test_dangling_ref_is_handled()
    print("all tests passed")
