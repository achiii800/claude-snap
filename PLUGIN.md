# claude-snap as a Claude Code plugin

This repo doubles as a Claude Code plugin. Once installed, you get four
slash commands inside Claude Code that wrap the `claude-snap` CLI.

## Install

You need `claude-snap` on your `PATH` first:

```bash
pip install claude-snap
```

Then install the plugin from this repo. From inside Claude Code:

```
/plugin install achiii800/claude-snap
```

(Or, equivalently, add the repo to `~/.claude/settings.json` per the
[official plugin docs](https://docs.claude.com/en/docs/claude-code/plugins).)

## Slash commands

| Command | What it does |
|---|---|
| `/snap` | Status: shows claude-snap version, locates the most recent session JSONL, and explains the other commands. Doesn't pack anything. |
| `/snap-pack [path]` | Packs a session JSONL into a `.snap.jsonl`. Defaults to the most recent session if no path given. Reports compression ratio and ref count. |
| `/snap-stats [path]` | Reports compression stats on a packed `.snap.jsonl`. |
| `/snap-share [path]` | Packs and drops the artifact at `$CLAUDE_SNAP_DROP_PATH` (or `~/Documents/claude-snaps` by default). Useful with iCloud Drive / Dropbox / Syncthing for automatic cross-device handoff. |

## Cross-device workflow

1. On your laptop, mid-session, run `/snap-share`. The plugin packs the
   current session and drops the `.snap.jsonl` into your configured
   share dir.
2. If that dir is iCloud Drive / Dropbox / a synced folder, the
   artifact is on your phone seconds later.
3. On your phone, open Claude (mobile app or claude.ai), upload or
   paste the `.snap.jsonl`, and tell Claude *"this is a packed prior
   session — use it as context."*
4. When you're back at the laptop, `claude-snap unpack` restores the
   session JSONL byte-for-byte; drop it into `~/.claude/projects/<...>/`
   and Claude Code resumes against it.

The asymmetry is intentional: phone-Claude reads, ideates, drafts —
it can't `Edit`/`Write`/`Bash` against your laptop's filesystem because
there's no executor connected. That's a feature, not a limitation.

## Auto-pack on session end

If you want sessions to be packed automatically when Claude Code
exits, see the example hook submitted upstream at
[`anthropics/claude-code` `examples/hooks/snap_pack_on_stop.py`](https://github.com/anthropics/claude-code/blob/main/examples/hooks/snap_pack_on_stop.py)
(linked once that PR lands; until then see this repo's
`examples/hooks/snap_pack_on_stop.py`).

The hook is opt-in — wiring it into your settings is a deliberate
choice, not a side effect of installing this plugin.
