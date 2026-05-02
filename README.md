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

### As a Claude Code plugin

This repo doubles as a [Claude Code plugin](https://docs.claude.com/en/docs/claude-code/plugins).
Install it from inside Claude Code to get four slash commands:

```
/plugin install achiii800/claude-snap
```

| Command | What it does |
|---|---|
| `/snap` | Status: shows version, locates the most recent session JSONL, explains the rest. |
| `/snap-pack [path]` | Pack a session JSONL into `.snap.jsonl`. Defaults to most recent. |
| `/snap-stats [path]` | Compression stats on a packed file. |
| `/snap-share [path]` | Pack and drop the artifact at `$CLAUDE_SNAP_DROP_PATH` (or `~/Documents/claude-snaps`). Pair with iCloud Drive / Dropbox / Syncthing for hands-off cross-device handoff. |

See [PLUGIN.md](./PLUGIN.md) for full plugin docs and the cross-device workflow.

For auto-pack-on-session-end, see [`examples/hooks/snap_pack_on_stop.py`](./examples/hooks/snap_pack_on_stop.py)
— a robust, opt-in `Stop` hook (also submitted upstream as a PR to
`anthropics/claude-code/examples/hooks/`).

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

## Where this sits in the ecosystem

The space around moving Claude Code sessions between machines has more
tools than you'd think. They each solve a *different* shape of the
problem:

| Tool | Approach | Lossless? | Byte-identical roundtrip? | Codec? | Single artifact? | Network setup? |
|---|---|---|---|---|---|---|
| [claude-mem](https://github.com/thedotmack/claude-mem) | AI summarization via agent-sdk | no — lossy by design | no | no (summarizer) | yes | none |
| [cctrace](https://github.com/jimmc414/cctrace) | Markdown / XML render + verbatim JSONL copy | partial — md/xml lossy, JSONL copy lossless | the JSONL copy yes; the renders no | no (transcriber) | bundle dir | none |
| [claude-conversation-extractor](https://github.com/ZeroSumQuant/claude-conversation-extractor) | Markdown export | no — markdown loses JSON structure | no | no (extractor) | yes | none |
| [session-roam](https://github.com/VirelNode/session-roam) | Syncthing P2P sync of `~/.claude/projects/` | yes (it's the same file) | trivially | no — file sync, structure-blind | no — the live directory | Syncthing peering, both nodes online |
| [claude-handoff](https://github.com/NeoAcar/claude-handoff) | Git-based bundle with absolute-path scrubbing & secret redaction | structurally yes | **no — paths intentionally rewritten** | partial — structural transformer | yes (`.claude-shared/` dir) | shared git repo |
| **claude-snap** (this repo) | sha256 content-hash refs in JSONL stream, mutation-aware structural dedup, per-event metadata patched on restore | yes | **yes — regression-tested** | yes — true codec | yes (single `.snap.jsonl`) | none |

Where claude-snap is unique:

- It's the only one of these that's a **true codec** — encoding/decoding
  between two valid representations of the *same* data, not a render, not
  a summary, not a sync, not a transformer.
- It produces a **single portable artifact** that roundtrips byte-for-byte.
  No peer setup, no shared repo, no online dependencies, no path
  rewriting.
- It composes with everything else. cctrace bundles include a JSONL copy
  that ships unchanged through claude-snap. claude-handoff's normalized
  bundles can be packed before commit. session-roam's synced directory is
  the input.

If your problem is *"I want both my machines online and the directory
mirrored"*, use session-roam. If it's *"I want to share sessions through
a git repo, with secrets and absolute paths scrubbed"*, use
claude-handoff. If it's *"I want a single small file I can drop into
another Claude chat or commit to a repo, byte-identical on roundtrip"*,
use claude-snap.

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
