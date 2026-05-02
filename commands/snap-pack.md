---
allowed-tools: Bash(claude-snap:*), Bash(ls:*)
description: Pack a Claude Code session JSONL into a portable .snap.jsonl
argument-hint: "[path-to-session.jsonl]"
---

## Context

- Argument from the user: $ARGUMENTS
- Most recent session JSONL: !`ls -t ~/.claude/projects/*/*.jsonl 2>/dev/null | head -1`

## Your task

Pack a Claude Code session into a portable, lossless `.snap.jsonl` artifact.

If the user provided a path in $ARGUMENTS, use that. If not, default to the most recent session JSONL shown above. If neither is available, tell the user the path is required.

Run `claude-snap pack <path>` and report:
- The output path
- The compression ratio
- The number of refs introduced (events deduplicated)

Remind the user the `.snap.jsonl` artifact is portable — they can move it to any device and load it into a Claude chat there as continuation context, or commit it to git for archival. Roundtrip via `claude-snap unpack` is byte-identical.
