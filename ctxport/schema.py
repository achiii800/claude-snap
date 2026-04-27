"""
Canonical event schema for Claude Code session JSONLs.

A session JSONL is a sequence of events. Each event in the original
Claude Code format may be one of:
  - user message (user typed something)
  - assistant message (with reasoning + content blocks)
  - tool_use (Claude calls Read / Edit / Write / Bash / Grep / Glob / etc.)
  - tool_result (the output of a tool_use)
  - meta events (file-mod tracking, summaries, etc.)

We normalize to a small set of canonical types so the codec can reason
about them uniformly without coupling to Claude Code's exact field names.
"""

from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


USER_MSG = "user_msg"
ASSISTANT_MSG = "assistant_msg"
TOOL_USE = "tool_use"
TOOL_RESULT = "tool_result"
META = "meta"
REF = "ref"


READ_LIKE = {"Read", "Glob", "Grep", "LS", "NotebookRead"}
MUTATING = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL = {"Bash", "BashOutput", "KillBash"}
NETWORK = {"WebFetch", "WebSearch"}
META_TOOL = {"TodoWrite", "Task"}


@dataclass
class Event:
    kind: str
    seq: int
    content_hash: str
    payload: dict
    tool_name: Optional[str] = None
    tool_id: Optional[str] = None
    target_path: Optional[str] = None
    refs: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, [], {})}


def hash_payload(payload: Any) -> str:
    """Stable sha256 over the payload, truncated to 16 hex chars (64 bits)."""
    s = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def normalize(raw: dict, seq: int) -> Event:
    """Convert a raw Claude Code JSONL line into a canonical Event."""
    t = raw.get("type", "")

    if t == "user":
        msg = raw.get("message", {})
        content = msg.get("content")
        if isinstance(content, list):
            tool_result_blocks = [b for b in content if isinstance(b, dict)
                                  and b.get("type") == "tool_result"]
            if tool_result_blocks:
                tr = tool_result_blocks[0]
                # Hash covers both the inner tool_result.content AND the rich
                # toolUseResult sibling field. Two events with the same hash
                # have identical dedup-relevant content; per-event metadata
                # (uuid, parentUuid, timestamp, ...) is patched back on unpack.
                return Event(
                    kind=TOOL_RESULT,
                    seq=seq,
                    content_hash=hash_payload(
                        [tr.get("content"), raw.get("toolUseResult")]
                    ),
                    payload=raw,
                    tool_id=tr.get("tool_use_id"),
                )
        return Event(
            kind=USER_MSG,
            seq=seq,
            content_hash=hash_payload(content),
            payload=raw,
        )

    if t == "assistant":
        msg = raw.get("message", {})
        content = msg.get("content", [])
        if isinstance(content, list):
            tool_uses = [b for b in content if isinstance(b, dict)
                         and b.get("type") == "tool_use"]
            if tool_uses:
                tu = tool_uses[0]
                tool_name = tu.get("name", "")
                tool_input = tu.get("input", {}) or {}
                target_path = tool_input.get("file_path") or tool_input.get("path")
                return Event(
                    kind=TOOL_USE,
                    seq=seq,
                    content_hash=hash_payload(tu),
                    payload=raw,
                    tool_name=tool_name,
                    tool_id=tu.get("id"),
                    target_path=target_path,
                )
        return Event(
            kind=ASSISTANT_MSG,
            seq=seq,
            content_hash=hash_payload(content),
            payload=raw,
        )

    return Event(
        kind=META,
        seq=seq,
        content_hash=hash_payload(raw),
        payload=raw,
    )


def is_read_only(event: Event) -> bool:
    if event.tool_name in READ_LIKE:
        return True
    if event.tool_name in NETWORK:
        return True
    return False


def is_mutating(event: Event) -> bool:
    return event.tool_name in MUTATING


def tool_result_payload(event: Event) -> Optional[str]:
    """Extract the textual content of a tool_result event."""
    if event.kind != TOOL_RESULT:
        return None
    msg = event.payload.get("message", {})
    content = msg.get("content")
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                inner = b.get("content")
                if isinstance(inner, str):
                    return inner
                if isinstance(inner, list):
                    parts = [x.get("text", "") for x in inner
                             if isinstance(x, dict)]
                    return "\n".join(parts)
    return None
