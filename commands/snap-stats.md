---
allowed-tools: Bash(claude-snap:*), Bash(ls:*)
description: Report compression stats on a packed .snap.jsonl
argument-hint: "[path-to-file.snap.jsonl]"
---

## Context

- Argument from the user: $ARGUMENTS
- Most recent .snap.jsonl in cwd: !`ls -t *.snap.jsonl 2>/dev/null | head -1`

## Your task

Report compression stats for a packed claude-snap artifact.

If the user provided a path in $ARGUMENTS, use that. Otherwise default to the most recent `.snap.jsonl` in the current directory. If neither is available, tell the user a path is required.

Run `claude-snap stats <path>` and report the JSON output. Briefly explain what the fields mean:
- `events`: full event records preserved verbatim
- `refs`: dedup references introduced (re-Reads of unchanged files, repeated identical Bash output)
- `bytes_unpacked` / `bytes_packed`: byte counts before and after
- `compression_ratio`: how much was saved

Note: typical real-world compression depends entirely on session shape. Sessions with lots of re-Reads or repeated Bash compress well; sessions that are mostly unique Edits/Reads stay near 1.0×. The value of claude-snap is portability and losslessness, not gzip.
