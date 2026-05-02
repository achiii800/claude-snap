---
allowed-tools: Bash(claude-snap:*), Bash(ls:*), Bash(cp:*), Bash(mkdir:*), Bash(echo:*)
description: Pack the current session and drop the artifact at $CLAUDE_SNAP_DROP_PATH
argument-hint: "[path-to-session.jsonl]"
---

## Context

- Argument from the user: $ARGUMENTS
- Most recent session JSONL: !`ls -t ~/.claude/projects/*/*.jsonl 2>/dev/null | head -1`
- CLAUDE_SNAP_DROP_PATH (env var): !`echo "${CLAUDE_SNAP_DROP_PATH:-(not set)}"`
- Default drop dir: !`echo "${HOME}/Documents/claude-snaps"`

## Your task

Pack a session and drop the artifact at a configured share-path so the user can move it to another device (AirDrop, iCloud Drive, gist, etc.).

1. Determine the source session JSONL: $ARGUMENTS if provided, else the most recent shown above.
2. Determine the destination directory: `$CLAUDE_SNAP_DROP_PATH` if set, else `~/Documents/claude-snaps`. Create it with `mkdir -p` if needed.
3. Run `claude-snap pack <source> -o <dest>/<session-name>.snap.jsonl`.
4. Report the destination path and tell the user the artifact is ready to move to another device.

If the user hasn't set `CLAUDE_SNAP_DROP_PATH`, mention that they can configure it (e.g., point it at `~/Library/Mobile Documents/com~apple~CloudDocs/claude-snaps` on macOS for iCloud auto-sync).
