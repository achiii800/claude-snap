"""claude-snap CLI — pack, unpack, stats, chat."""

from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import codec


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


def _cmd_pack(args):
    events = codec.parse(args.input)
    packed = codec.pack(events)

    out_path = args.output or _swap_ext(args.input, ".snap.jsonl")
    codec.write_jsonl(packed, out_path)

    s = codec.stats(packed)
    print(f"packed: {args.input} → {out_path}")
    print(f"  events in:  {len(events)}")
    print(f"  events out: {s['events']} ({s['refs']} refs introduced)")
    print(f"  bytes:      {s['bytes_unpacked']} → {s['bytes_packed']}  "
          f"({s['compression_ratio']}× ratio)")

    if args.clip:
        try:
            text = Path(out_path).read_text(encoding="utf-8")
        except OSError as e:
            print(f"  clip:       failed to read packed file: {e}", file=sys.stderr)
            return
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


def _cmd_unpack(args):
    packed = list(codec._read_jsonl(args.input))
    out = codec.unpack(packed)

    out_path = args.output or _swap_ext(args.input, ".unpacked.jsonl")
    codec.write_jsonl(out, out_path)

    print(f"unpacked: {args.input} → {out_path} ({len(out)} events)")


def _cmd_stats(args):
    packed = list(codec._read_jsonl(args.input))
    s = codec.stats(packed)
    print(json.dumps(s, indent=2))


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

    p_pack = sub.add_parser("pack", help="compress a session JSONL")
    p_pack.add_argument("input", help="path to source .jsonl")
    p_pack.add_argument("-o", "--output",
                        help="output path (default: <input>.snap.jsonl)")
    p_pack.add_argument("-c", "--clip", action="store_true",
                        help="also copy the packed contents to your "
                             "clipboard (uses Universal Clipboard on macOS "
                             "→ paste on iPhone/iPad in seconds)")
    p_pack.set_defaults(func=_cmd_pack)

    p_unp = sub.add_parser("unpack", help="restore a packed JSONL")
    p_unp.add_argument("input")
    p_unp.add_argument("-o", "--output")
    p_unp.set_defaults(func=_cmd_unpack)

    p_st = sub.add_parser("stats", help="report compression stats")
    p_st.add_argument("input")
    p_st.set_defaults(func=_cmd_stats)

    p_chat = sub.add_parser(
        "chat",
        help="open the bundled PWA in your browser via a localhost proxy "
             "that holds the API key (set ANTHROPIC_API_KEY in your shell)",
    )
    p_chat.add_argument("input", nargs="?",
                        help="optional path to a .snap.jsonl (or .jsonl) "
                             "to autoload in the page")
    p_chat.add_argument("--port", type=int, default=0,
                        help="port to bind on 127.0.0.1 (default: random free port)")
    p_chat.add_argument("--no-browser", action="store_true",
                        help="don't open a browser tab automatically")
    p_chat.set_defaults(func=_cmd_chat)

    args = parser.parse_args(argv)
    return args.func(args)


def _cmd_chat(args):
    from . import serve
    return serve.serve(
        snap_path=args.input,
        port=args.port,
        open_browser=(not args.no_browser),
    )


if __name__ == "__main__":
    sys.exit(main() or 0)
