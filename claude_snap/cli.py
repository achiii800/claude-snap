"""claude-snap CLI — pack, unpack, stats."""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from . import codec


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
    p_pack.set_defaults(func=_cmd_pack)

    p_unp = sub.add_parser("unpack", help="restore a packed JSONL")
    p_unp.add_argument("input")
    p_unp.add_argument("-o", "--output")
    p_unp.set_defaults(func=_cmd_unpack)

    p_st = sub.add_parser("stats", help="report compression stats")
    p_st.add_argument("input")
    p_st.set_defaults(func=_cmd_stats)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
