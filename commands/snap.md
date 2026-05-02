---
allowed-tools: Bash(claude-snap:*), Bash(ls:*), Bash(echo:*)
description: Show claude-snap status and the most recent Claude Code session JSONL
---

## Context

- claude-snap version: !`claude-snap --version 2>/dev/null || echo "claude-snap not installed (pip install claude-snap)"`
- Most recent session JSONL: !`ls -t ~/.claude/projects/*/*.jsonl 2>/dev/null | head -1 || echo "no session JSONLs found in ~/.claude/projects/"`
- Claude projects dir size: !`du -sh ~/.claude/projects 2>/dev/null || echo "(missing)"`

## Your task

Report claude-snap status to the user. If claude-snap isn't installed, tell them how to install it (`pip install claude-snap`). If installed, briefly describe what `/snap-pack`, `/snap-stats`, and `/snap-share` do, and which session JSONL would be packed by default if they ran `/snap-pack` with no arguments.

Don't pack anything yet — this is a status / overview command.
