"""claude-snap CLI — pack, unpack, stats, chat, list.

Tab completion is opt-in via the `completion` extra:
    pip install 'claude-snap[completion]'
    eval "$(register-python-argcomplete claude-snap)"   # add to ~/.bashrc / ~/.zshrc

When argcomplete is not installed, the CLI behaves identically — completion
just doesn't fire.
"""

from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import codec
from . import sessions as ses


def _session_completer(prefix, parsed_args, **kwargs):
    """argcomplete completer for selector args.

    Completes UUID prefixes (always unambiguous), sourced from
    ~/.claude/projects/. Title substring matching still works without
    completion — we deliberately don't try to complete free-text titles
    because the shell quoting story is hairy and bash doesn't render
    descriptions.
    """
    try:
        rows = ses.enumerate_sessions()
    except Exception:
        return []
    out = []
    p = (prefix or "").lower()
    for r in rows:
        short = r.uuid[:8]
        if not p or short.startswith(p):
            # zsh shows the description; bash ignores it.
            title = (r.title or r.first_user_text or "")[:60].replace("\n", " ")
            if title:
                out.append(f"{short}\t{title}")
            else:
                out.append(short)
    return out


def _copy_to_clipboard(text: str) -> tuple[bool, str]:
    """
    Best-effort copy `text` to the OS clipboard. Returns (ok, tool_used).
    On macOS this enables Universal Clipboard — paste lands on iPhone/iPad
    within ~1-2 seconds when on the same Apple ID + iCloud + Bluetooth.
    """
    if sys.platform == "darwin" and shutil.which("pbcopy"):
        try:
            subprocess.run(
                ["pbcopy"], input=text.encode("utf-8"),
                check=True, timeout=10,
            )
            return True, "pbcopy"
        except (subprocess.SubprocessError, OSError):
            return False, "pbcopy"

    if sys.platform.startswith("linux"):
        if shutil.which("wl-copy"):
            try:
                subprocess.run(
                    ["wl-copy"], input=text.encode("utf-8"),
                    check=True, timeout=10,
                )
                return True, "wl-copy"
            except (subprocess.SubprocessError, OSError):
                return False, "wl-copy"
        if shutil.which("xclip"):
            try:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text.encode("utf-8"),
                    check=True, timeout=10,
                )
                return True, "xclip"
            except (subprocess.SubprocessError, OSError):
                return False, "xclip"

    if sys.platform == "win32" and shutil.which("clip"):
        try:
            # `clip` on Windows expects UTF-16LE.
            subprocess.run(
                ["clip"], input=text.encode("utf-16le"),
                check=True, timeout=10,
            )
            return True, "clip"
        except (subprocess.SubprocessError, OSError):
            return False, "clip"

    return False, "no-tool"


def _resolve_session_arg(selector: Optional[str], verb: str) -> Optional[Path]:
    """
    Resolve a CLI selector argument to a session JSONL path.

    Returns the resolved Path, or None if the user must disambiguate / no
    match — in which case a helpful message is printed to stderr and the
    caller should `return 2`.
    """
    chosen, candidates = ses.resolve_selector(selector)

    if chosen is not None:
        return chosen.path

    if not candidates:
        if not selector:
            sys.stderr.write(
                f"claude-snap {verb}: no session JSONLs found in "
                f"{ses.projects_root()}.\n"
            )
        else:
            sys.stderr.write(
                f"claude-snap {verb}: no session matches {selector!r}.\n"
                f"  try `claude-snap list` to see available sessions.\n"
            )
        return None

    # Multiple matches → list them.
    sys.stderr.write(
        f"claude-snap {verb}: {len(candidates)} sessions match {selector!r}. "
        f"refine with a longer substring or a UUID prefix:\n\n"
    )
    _print_session_table(candidates[:20], stream=sys.stderr)
    if len(candidates) > 20:
        sys.stderr.write(f"  ... and {len(candidates) - 20} more\n")
    return None


def _print_session_table(rows: list[ses.SessionInfo], stream=None) -> None:
    out = stream or sys.stdout
    if not rows:
        return
    title_w = max(20, min(60, max(len(r.display_title()) for r in rows)))
    for r in rows:
        title = r.display_title().replace("\n", " ").strip()
        if len(title) > title_w:
            title = title[: title_w - 1] + "…"
        out.write(
            f"  {r.uuid[:8]}  "
            f"{ses.format_relative_mtime(r.mtime):>10}  "
            f"{ses.format_size(r.size):>9}  "
            f"{title:<{title_w}}\n"
        )


def _cmd_pack(args):
    src = _resolve_session_arg(args.input, "pack")
    if src is None:
        return 2

    events = codec.parse(str(src))
    packed = codec.pack(events)

    out_path = args.output or _swap_ext(src.name, ".snap.jsonl")
    codec.write_jsonl(packed, out_path)

    s = codec.stats(packed)
    print(f"packed: {src} → {out_path}")
    print(f"  events in:  {len(events)}")
    print(f"  events out: {s['events']} ({s['refs']} refs introduced)")
    print(f"  bytes:      {s['bytes_unpacked']} → {s['bytes_packed']}  "
          f"({s['compression_ratio']}× ratio)")

    if args.clip:
        try:
            text = Path(out_path).read_text(encoding="utf-8")
        except OSError as e:
            print(f"  clip:       failed to read packed file: {e}", file=sys.stderr)
            return 0
        ok, tool = _copy_to_clipboard(text)
        if ok:
            size_kb = len(text.encode("utf-8")) / 1024
            extra = ""
            if sys.platform == "darwin":
                extra = " (Universal Clipboard → paste on iPhone/iPad)"
            print(f"  clip:       {size_kb:.1f} KB on clipboard via {tool}{extra}")
        else:
            print(f"  clip:       no clipboard tool available ({tool}); skipping",
                  file=sys.stderr)
    return 0


def _cmd_unpack(args):
    # `unpack` always takes a real .snap.jsonl path — selector resolution
    # doesn't apply, since this is operating on packed artifacts the user
    # has produced themselves.
    packed = list(codec._read_jsonl(args.input))
    out = codec.unpack(packed)

    out_path = args.output or _swap_ext(args.input, ".unpacked.jsonl")
    codec.write_jsonl(out, out_path)

    print(f"unpacked: {args.input} → {out_path} ({len(out)} events)")
    return 0


def _cmd_stats(args):
    # If the input doesn't exist as a path, try selector resolution
    # (handy: `claude-snap stats SGPDec` packs nothing but reports stats
    # against an existing .snap.jsonl with that fuzzy match if any).
    p = Path(args.input).expanduser()
    if not p.is_file():
        # No selector resolution for stats — it operates on packed files
        # which usually aren't in ~/.claude/projects/.
        sys.stderr.write(f"claude-snap stats: {args.input}: no such file\n")
        return 2
    packed = list(codec._read_jsonl(str(p)))
    s = codec.stats(packed)
    print(json.dumps(s, indent=2))
    return 0


def _cmd_chat(args):
    src: Optional[str] = None
    if args.input:
        resolved = _resolve_session_arg(args.input, "chat")
        if resolved is None:
            return 2
        src = str(resolved)
    from . import serve
    return serve.serve(
        snap_path=src,
        port=args.port,
        open_browser=(not args.no_browser),
    )


def _cmd_list(args):
    rows = ses.enumerate_sessions()
    if not rows:
        print(f"no session JSONLs found in {ses.projects_root()}")
        return 0
    if args.search:
        sl = args.search.lower()
        rows = [
            r for r in rows
            if sl in (r.title or "").lower()
            or sl in (r.first_user_text or "").lower()
            or sl in r.uuid.lower()
        ]
        if not rows:
            print(f"no sessions match {args.search!r}")
            return 0
    limit = args.limit if args.limit > 0 else len(rows)
    print(f"  {'ID':<8}  {'WHEN':>10}  {'SIZE':>9}  TITLE")
    _print_session_table(rows[:limit])
    if len(rows) > limit:
        print(f"  ... and {len(rows) - limit} more (use --limit to see more)")
    return 0


def _swap_ext(path: str, new_ext: str) -> str:
    p = Path(path)
    return str(p.with_suffix("")) + new_ext


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="claude-snap",
        description="Lossless structural codec for Claude Code session JSONLs."
    )
    parser.add_argument("--version", action="version",
                        version=f"claude-snap {codec.CLAUDE_SNAP_VERSION}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    selector_help = (
        "session selector — a path, a UUID prefix (e.g. `269b1190`), "
        "a fuzzy title substring (e.g. `'Analyze SGPDec'`), or omit "
        "for the most recent session. See `claude-snap list`."
    )

    p_pack = sub.add_parser("pack", help="compress a session JSONL")
    pack_input = p_pack.add_argument("input", nargs="?", help=selector_help)
    pack_input.completer = _session_completer
    p_pack.add_argument("-o", "--output",
                        help="output path (default: <input-stem>.snap.jsonl in cwd)")
    p_pack.add_argument("-c", "--clip", action="store_true",
                        help="also copy the packed contents to your "
                             "clipboard (uses Universal Clipboard on macOS "
                             "→ paste on iPhone/iPad in seconds)")
    p_pack.set_defaults(func=_cmd_pack)

    p_unp = sub.add_parser("unpack", help="restore a packed JSONL")
    p_unp.add_argument("input", help="path to a .snap.jsonl")
    p_unp.add_argument("-o", "--output")
    p_unp.set_defaults(func=_cmd_unpack)

    p_st = sub.add_parser("stats", help="report compression stats on a packed file")
    p_st.add_argument("input", help="path to a .snap.jsonl")
    p_st.set_defaults(func=_cmd_stats)

    p_chat = sub.add_parser(
        "chat",
        help="open the bundled PWA in your browser via a localhost proxy "
             "that holds the API key (set ANTHROPIC_API_KEY in your shell)",
    )
    chat_input = p_chat.add_argument("input", nargs="?", help=selector_help)
    chat_input.completer = _session_completer
    p_chat.add_argument("--port", type=int, default=0,
                        help="port to bind on 127.0.0.1 (default: random free port)")
    p_chat.add_argument("--no-browser", action="store_true",
                        help="don't open a browser tab automatically")
    p_chat.set_defaults(func=_cmd_chat)

    p_list = sub.add_parser("list", help="list sessions in ~/.claude/projects/")
    list_search = p_list.add_argument(
        "search", nargs="?",
        help="optional substring to filter title / first user message / UUID")
    list_search.completer = _session_completer
    p_list.add_argument("--limit", type=int, default=30,
                        help="max rows to show (default: 30, 0 = all)")
    p_list.set_defaults(func=_cmd_list)

    # Wire up tab completion if argcomplete is available. Soft-import so
    # the CLI works identically when it isn't.
    try:
        import argcomplete  # type: ignore
        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    args = parser.parse_args(argv)
    rc = args.func(args)
    return rc if isinstance(rc, int) else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
