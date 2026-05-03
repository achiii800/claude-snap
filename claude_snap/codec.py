"""
The claude-snap codec.

Heuristics, in priority order:
  1. User and assistant messages: kept verbatim, always.
  2. Edit / Write tool calls and their results: kept verbatim, always.
  3. First Read of a given file: kept verbatim.
  4. Re-Read of a file with identical content AND no Edit/Write to that path
     between the two reads: replace the second read's tool_result with a
     snap_ref pointing to the first.
  5. Identical Bash tool_results (content-hash match): ref the duplicate.
  6. Anything else (TodoWrite, MultiEdit, Glob, Grep, custom MCP tools):
     pass through verbatim.

Correctness condition: unpack(pack(events)) == events byte-for-byte,
modulo the header/footer metadata. When resolving a ref on unpack, the
restored payload's tool_use_id is patched to the ref event's id so the
tool_use ↔ tool_result linkage stays correct, and any captured per-event
metadata (uuid, parentUuid, timestamp, ...) is overlaid on the restored
payload.
"""

from __future__ import annotations
import copy
import json
from typing import Iterator

from .schema import (
    Event, REF, USER_MSG, ASSISTANT_MSG, TOOL_USE, TOOL_RESULT, META,
    normalize, is_mutating, READ_LIKE, SHELL, NETWORK,
)


CLAUDE_SNAP_VERSION = "0.3.0"

# Tool families whose results are safe to dedup. Mutating tools
# (Edit/Write/MultiEdit/NotebookEdit) and meta tools (TodoWrite/Task) are
# excluded because their results carry operation-specific data that varies
# per call even when the visible message is the same.
SAFE_TO_DEDUP_FAMILIES = READ_LIKE | SHELL | NETWORK

# Per-event metadata fields that vary between otherwise-identical events.
# Captured on the ref so unpack can restore byte-identical payloads.
PER_EVENT_METADATA_FIELDS = (
    "uuid", "parentUuid", "timestamp", "sessionId", "requestId",
    "sourceToolAssistantUUID", "promptId", "messageId", "leafUuid",
    "lastPrompt",
)


def _read_jsonl(path: str) -> Iterator[dict]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def parse(jsonl_path: str) -> list:
    """Read a Claude Code JSONL file into a list of canonical Events."""
    return [normalize(raw, i) for i, raw in enumerate(_read_jsonl(jsonl_path))]


def pack(events: list) -> list:
    """Apply the dedup heuristics. Returns JSON-serializable dicts."""
    out: list = [{
        "type": "snap_header",
        "version": CLAUDE_SNAP_VERSION,
        "original_event_count": len(events),
    }]

    last_read_hash: dict = {}        # (tool, path) -> hash
    last_mutation_seq: dict = {}     # path -> seq
    seen_result_hash: dict = {}      # hash -> seq
    tool_use_index: dict = {}        # tool_id -> Event

    for ev in events:
        if ev.kind == TOOL_USE and is_mutating(ev) and ev.target_path:
            last_mutation_seq[ev.target_path] = ev.seq

        if ev.kind in (USER_MSG, ASSISTANT_MSG, META):
            out.append(_emit(ev))
            continue

        if ev.kind == TOOL_USE:
            out.append(_emit(ev))
            if ev.tool_id:
                tool_use_index[ev.tool_id] = ev
            continue

        if ev.kind == TOOL_RESULT:
            matched_use = tool_use_index.get(ev.tool_id)

            # Only dedup tool_results from safe (non-mutating) families.
            # Edit/Write results carry operation-specific toolUseResult data
            # (oldString/newString/structuredPatch) and must never be ref'd.
            safe = (matched_use is not None
                    and matched_use.tool_name in SAFE_TO_DEDUP_FAMILIES)

            if safe:
                # Heuristic 4: re-read of unchanged file
                if matched_use.tool_name in READ_LIKE \
                        and matched_use.target_path:
                    key = (matched_use.tool_name, matched_use.target_path)
                    prior_hash = last_read_hash.get(key)
                    last_mut = last_mutation_seq.get(
                        matched_use.target_path, -1)

                    if prior_hash == ev.content_hash and last_mut < ev.seq:
                        out.append(_emit_ref(
                            ev, prior_hash,
                            reason="unchanged_read",
                            path=matched_use.target_path,
                        ))
                        continue
                    last_read_hash[key] = ev.content_hash

                # Heuristic 5: duplicate tool_result content (Bash etc.)
                if ev.content_hash in seen_result_hash:
                    out.append(_emit_ref(
                        ev, ev.content_hash,
                        reason="duplicate_result",
                    ))
                    continue
                seen_result_hash[ev.content_hash] = ev.seq

            out.append(_emit(ev))
            continue

        out.append(_emit(ev))

    refs = sum(1 for x in out if x.get("type") == "snap_ref")
    out.append({
        "type": "snap_footer",
        "events_in": len(events),
        "events_out": len(out) - 2,
        "refs_introduced": refs,
    })
    return out


def _emit(ev: Event) -> dict:
    """Serialize an Event to a snap JSONL line. Drop None fields."""
    rec = {
        "type": "snap_event",
        "kind": ev.kind,
        "seq": ev.seq,
        "content_hash": ev.content_hash,
        "payload": ev.payload,
    }
    if ev.tool_name is not None:
        rec["tool_name"] = ev.tool_name
    if ev.tool_id is not None:
        rec["tool_id"] = ev.tool_id
    if ev.target_path is not None:
        rec["target_path"] = ev.target_path
    return rec


def _emit_ref(ev: Event, ref_to: str, reason: str, path: str = None) -> dict:
    out = {
        "type": "snap_ref",
        "kind": REF,
        "seq": ev.seq,
        "ref_to": ref_to,
        "reason": reason,
        "tool_id": ev.tool_id,
    }
    if path:
        out["target_path"] = path
    # Capture per-event metadata so unpack restores byte-identical payloads.
    patch = {k: ev.payload[k] for k in PER_EVENT_METADATA_FIELDS
             if isinstance(ev.payload, dict) and k in ev.payload}
    if patch:
        out["patch"] = patch
    return out


def unpack(packed: list) -> list:
    """
    Resolve refs back to full payloads. Returns the original raw JSONL
    dicts in original order.

    Property: for any Claude Code session JSONL `f`,
        unpack(pack(parse(f))) == [ev.payload for ev in parse(f)]
    """
    by_hash: dict = {}
    out: list = []

    for x in packed:
        t = x.get("type")
        if t in ("snap_header", "snap_footer"):
            continue
        if t == "snap_event":
            by_hash[x["content_hash"]] = x["payload"]
            out.append(x["payload"])
            continue
        if t == "snap_ref":
            ref_to = x.get("ref_to")
            payload = by_hash.get(ref_to)
            if payload is None:
                out.append({
                    "type": "snap_dangling_ref",
                    "ref_to": ref_to,
                    "seq": x.get("seq"),
                })
                continue
            restored = copy.deepcopy(payload)
            new_tool_id = x.get("tool_id")
            if new_tool_id and isinstance(restored, dict):
                msg = restored.get("message", {})
                content = msg.get("content") if isinstance(msg, dict) else None
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            b["tool_use_id"] = new_tool_id
            patch = x.get("patch")
            if isinstance(patch, dict) and isinstance(restored, dict):
                restored.update(patch)
            out.append(restored)
            continue

    return out


def write_jsonl(records: list, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def stats(packed: list) -> dict:
    """Compression stats from a packed stream."""
    events = sum(1 for x in packed if x.get("type") == "snap_event")
    refs = sum(1 for x in packed if x.get("type") == "snap_ref")

    event_bytes_by_hash: dict = {}
    for x in packed:
        if x.get("type") == "snap_event":
            event_bytes_by_hash[x["content_hash"]] = len(json.dumps(x))

    full_bytes = 0
    packed_bytes = 0
    for x in packed:
        t = x.get("type")
        if t == "snap_event":
            b = len(json.dumps(x))
            full_bytes += b
            packed_bytes += b
        elif t == "snap_ref":
            full_bytes += event_bytes_by_hash.get(x.get("ref_to"), 0)
            packed_bytes += len(json.dumps(x))

    return {
        "events": events,
        "refs": refs,
        "events_plus_refs": events + refs,
        "bytes_unpacked": full_bytes,
        "bytes_packed": packed_bytes,
        "compression_ratio": round(full_bytes / max(packed_bytes, 1), 2),
    }
