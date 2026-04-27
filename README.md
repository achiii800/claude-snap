# claude-snap

**Portable, lossless snapshots of Claude Code sessions.**

`--fork-session` is a Claude Code primitive that already works locally: pick up
where you left off in a new shell, with full context. claude-snap is the same
primitive when the destination is a different device.

Pack a session JSONL on your laptop. Drop the artifact into a Claude chat on
your phone. Keep the chain of ideas going. Read-only on a device that can't
execute (the phone); read-write when reloaded into Claude Code on a device
that has the repo.

This is not a memory layer. Not a search tool. Not a history browser. Not a
summarizer. Not a markdown renderer. It's a codec.

## What's in the box

```
claude-snap pack    session.jsonl  →  session.snap.jsonl   # compress
claude-snap unpack  session.snap.jsonl  →  session.jsonl   # restore (byte-identical events)
claude-snap stats   session.snap.jsonl                     # how much did we save
```

Zero runtime deps. Pure stdlib Python 3.9+. MIT.

## Install

```bash
pip install claude-snap
```

## End-to-end UX

Today (manual, but works on day one):

1. On your laptop, after a Claude Code session:
   ```bash
   claude-snap pack ~/.claude/projects/<encoded-path>/<uuid>.jsonl
   # → <uuid>.snap.jsonl
   ```
2. Move the `.snap.jsonl` to your phone — AirDrop, iCloud Drive, email-to-self,
   gist, whatever moves a text file.
3. On your phone, open Claude (mobile app or claude.ai), upload/paste the file,
   and tell Claude *"this is a packed prior session — use it as context."*
   Phone-Claude now has the full conversational chain, every file state
   laptop-Claude saw, every edit it made.

Reattaching to laptop:

```bash
claude-snap unpack session.snap.jsonl   # → session.jsonl, byte-identical events
```

Drop the unpacked JSONL back into `~/.claude/projects/<...>/` and Claude Code
resumes against it.

## Asymmetry: phone vs laptop

When you load a snapshot into a Claude chat on a device that doesn't hold the
repo:

- **Can:** ideate, discuss, suggest code, draft the next edits, plan, review
  what was done.
- **Can't:** actually `Edit` / `Write` / `Bash` against the laptop's files.

Two reasons, the second is load-bearing:

1. No executor connected to the laptop's filesystem.
2. The snapshot itself has no execution surface. It's inert text. There's no
   protocol where phone-Claude could reach back through it to your laptop.

That's not a discipline imposed by the codec; it's what the medium *is*. The
right architecture for cross-device mobility: by construction, the snapshot
moves without dragging side-effect capability with it.

## What "lossless" means here

Roundtrip property: `unpack(pack(events))` produces the original event
payloads byte-for-byte (modulo header/footer metadata).

The codec only ever replaces *redundant* events with refs. It never:

- summarizes
- truncates conversational turns
- drops reasoning
- collapses Edit/Write payloads
- approximates anything

The redundancy it exploits is structural:

1. **Re-reading an unchanged file.** If Claude does `Read(foo.py)` twice and
   nothing has Edit'd `foo.py` between them, the second read becomes a ref.
   Content preserved exactly once.
2. **Repeated identical tool output.** If `pytest -q` returns the same bytes
   twice, the second result becomes a ref.
3. **Mutation invalidates dedup.** If `foo.py` was Edit'd between two Reads,
   the second Read is *not* ref'd — its content has genuinely changed.

Conversational turns (you and Claude talking) are *never* dedup'd. That's the
chain of ideas; you keep all of it.

## What compression to expect

The codec only removes *structural* redundancy: repeat reads of unchanged
files, repeat tool calls with identical output. If your session re-reads or
re-runs a lot, expect a meaningful ratio. If every Read and every Bash is
unique (common in modern Claude Code sessions, where Claude tends to
retain what it has already seen), expect close to 1.0× — and that's
correct, not a bug. The artifact is still portable and lossless, which is
the point. Compression is incidental.

## Why this exists

The space around Claude Code history has two kinds of tools:

- **Dumpers** (`claude-conversation-extractor`, `cctrace`,
  `claude-code-history-mcp`) give you the full firehose. Hit context window
  limits the moment a session has nontrivial Read/Bash bloat.
- **AI summarizers** (`claude-mem`) give you a summary. Lossy by design.
  Throws away the chain-of-changes that's the whole point.

Neither lets you "transplant the session into a fresh chat on another device
and pick up where you left off." That requires the *real* conversation, just
without the redundant payload bytes.

## Roadmap

- **v0.1.0** (you are here): the codec.
- A Claude Code skill / configurable script that finds the active session,
  packs it, and drops the artifact at a configurable location (iCloud Drive
  folder, S3 bucket, etc.). Removes step 1 of the manual flow.
- Schema adapters for other agentic CLIs (Cursor, Aider, Codex, Gemini CLI).
  The Event format is intentionally tool-agnostic.

The phone-side step (upload into a chat) remains an unsolvable workaround
until Claude clients natively understand fork-session payloads. See the
linked `anthropics/claude-code` issue.

## Status

v0.1.0. Codec is correct on the roundtrip property and on the dedup
heuristics tested in `tests/test_codec.py`. Schema normalization handles the
dominant Claude Code JSONL shape; edge cases in unusual tool invocations
(custom MCP tools, Task subagents) currently fall through to the generic
`META` bucket and pass through unchanged — safe but uncompressed.

## Contributing

Issues and PRs welcome. Particularly interested in:

- Dedup heuristics for tools we don't currently special-case
- Schema adapters for other agentic CLIs
- A laptop-side packing skill / hook for Claude Code

## License

MIT.
