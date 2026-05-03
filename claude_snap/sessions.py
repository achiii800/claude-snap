"""
Session discovery + selector resolution.

Walks ~/.claude/projects/<encoded-cwd>/<uuid>.jsonl and pulls a tiny
metadata record for each: title (from `aiTitle`), original cwd, first
user message excerpt, mtime, size. The selector resolver lets the CLI
accept a path, a UUID prefix, or a fuzzy title substring as a single
positional argument — so a user can say:

    claude-snap pack "Analyze SGPDec"
    claude-snap pack 269b1190
    claude-snap pack ~/.claude/projects/-foo/abc.jsonl
    claude-snap pack                     # (defaults to most recent)
"""

from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# How many lines to scan per file when extracting metadata.
# aiTitle / cwd / first user message reliably show up in the first
# few dozen events; capping the read keeps `list` fast even when the
# user has hundreds of sessions or one of them is a 100 MB monster.
_META_SCAN_LINES = 400

# How many leading hex chars (with optional dashes) qualifies a
# selector as "looks like a UUID prefix".
_UUID_MIN_PREFIX = 6


@dataclass
class SessionInfo:
    path: Path
    uuid: str                   # session ID = filename stem
    project_dir_encoded: str    # parent dir name (Claude's encoded cwd)
    cwd: Optional[str]          # decoded cwd if found in events
    title: Optional[str]        # aiTitle if set
    first_user_text: Optional[str]  # short excerpt of first user message
    mtime: float
    size: int

    def display_title(self) -> str:
        """A short title for table display — falls back through three layers."""
        if self.title:
            return self.title
        if self.first_user_text:
            return self.first_user_text.replace("\n", " ").strip()
        return "(no title)"


def projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def enumerate_sessions(root: Optional[Path] = None) -> list[SessionInfo]:
    """List all session JSONLs under ~/.claude/projects/, newest first."""
    if root is None:
        root = projects_root()
    out: list[SessionInfo] = []
    if not root.is_dir():
        return out
    for f in root.glob("*/*.jsonl"):
        info = parse_session_meta(f)
        if info is not None:
            out.append(info)
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


def parse_session_meta(path: Path) -> Optional[SessionInfo]:
    """Read the head of a session JSONL and extract title / cwd / first user msg."""
    if not path.is_file():
        return None
    try:
        stat = path.stat()
    except OSError:
        return None

    title: Optional[str] = None
    cwd: Optional[str] = None
    first_user: Optional[str] = None

    try:
        with path.open("r", encoding="utf-8") as fh:
            for i, raw in enumerate(fh):
                if i >= _META_SCAN_LINES:
                    break
                line = raw.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict):
                    continue
                if cwd is None:
                    c = ev.get("cwd")
                    if isinstance(c, str):
                        cwd = c
                if title is None:
                    t = ev.get("aiTitle")
                    if isinstance(t, str) and t.strip():
                        title = t.strip()
                if first_user is None and ev.get("type") == "user":
                    first_user = _extract_user_text(ev)
                if title and cwd and first_user:
                    break
    except OSError:
        return None

    return SessionInfo(
        path=path.resolve(),
        uuid=path.stem,
        project_dir_encoded=path.parent.name,
        cwd=cwd,
        title=title,
        first_user_text=(first_user[:200] if first_user else None),
        mtime=stat.st_mtime,
        size=stat.st_size,
    )


def _extract_user_text(ev: dict) -> Optional[str]:
    """Pull a string of user text out of a `type: user` event, skipping
    tool_result wrappers (which look like user events but aren't typed text)."""
    msg = ev.get("message")
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            # Skip tool_result wrappers — they're not user-typed text.
            if b.get("type") == "tool_result":
                return None
            if b.get("type") == "text":
                t = b.get("text")
                if isinstance(t, str):
                    return t
    return None


def _looks_like_uuid_prefix(s: str) -> bool:
    """Heuristic: 6+ chars from the [0-9a-f-] alphabet, no spaces."""
    if len(s) < _UUID_MIN_PREFIX:
        return False
    sl = s.lower()
    return all(c in "0123456789abcdef-" for c in sl)


def resolve_selector(
    selector: Optional[str],
    sessions: Optional[list[SessionInfo]] = None,
) -> tuple[Optional[SessionInfo], list[SessionInfo]]:
    """
    Resolve a user-facing selector to a session.

    Selector forms (tried in order):
      - None or "" or "@latest"  → most recent session
      - existing file path       → wrap as SessionInfo and return
      - UUID prefix (>= 6 hex)   → match against session UUIDs
      - otherwise                → case-insensitive substring against
                                   title and first_user_text

    Returns:
      (chosen, candidates):
        - chosen is the unique match, or None if 0 or >1 matches
        - candidates is the list of matches (helpful for error output)
    """
    if sessions is None:
        sessions = enumerate_sessions()

    if not selector or selector == "@latest":
        return ((sessions[0] if sessions else None), sessions[:1])

    # Existing path?
    p = Path(selector).expanduser()
    if p.is_file():
        info = parse_session_meta(p)
        if info is not None:
            return (info, [info])

    # UUID prefix?
    if _looks_like_uuid_prefix(selector):
        sl = selector.lower()
        matches = [s for s in sessions if s.uuid.lower().startswith(sl)]
        if len(matches) == 1:
            return (matches[0], matches)
        if len(matches) > 1:
            return (None, matches)
        # zero hex matches — fall through to substring search

    # Substring (case-insensitive) against title and first_user_text.
    sl = selector.lower()
    matches = []
    for s in sessions:
        hay = ((s.title or "") + " " + (s.first_user_text or "")).lower()
        if sl in hay:
            matches.append(s)
    if len(matches) == 1:
        return (matches[0], matches)
    return (None, matches)


def format_relative_mtime(mtime: float, now: Optional[float] = None) -> str:
    """Format a unix mtime as a short relative string suitable for a table."""
    import time
    if now is None:
        now = time.time()
    delta = max(0, int(now - mtime))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    if delta < 86400 * 14:
        return f"{delta // 86400}d ago"
    # Older than two weeks: show date.
    return time.strftime("%Y-%m-%d", time.localtime(mtime))


def format_size(n_bytes: int) -> str:
    """Compact size formatter for the list table."""
    for unit, threshold in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n_bytes >= threshold:
            return f"{n_bytes / threshold:.1f} {unit}"
    return f"{n_bytes} B"
