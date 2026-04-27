"""claude-snap — portable, lossless snapshots of Claude Code sessions."""

from .codec import (
    CLAUDE_SNAP_VERSION,
    pack,
    unpack,
    parse,
    stats,
    write_jsonl,
)

__version__ = CLAUDE_SNAP_VERSION
__all__ = [
    "CLAUDE_SNAP_VERSION",
    "__version__",
    "pack",
    "unpack",
    "parse",
    "stats",
    "write_jsonl",
]
