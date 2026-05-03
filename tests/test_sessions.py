"""Tests for claude_snap.sessions — selector resolution and meta extraction."""

import json
import tempfile
from pathlib import Path

from claude_snap import sessions as ses


def _write_session(dir_: Path, uuid: str, *, title: str = None,
                   cwd: str = "/tmp/proj", first_user: str = "hello") -> Path:
    """Write a minimal session JSONL with the bits the parser cares about."""
    f = dir_ / f"{uuid}.jsonl"
    events = []
    if title:
        events.append({"type": "summary", "aiTitle": title, "cwd": cwd})
    events.append({
        "type": "user",
        "cwd": cwd,
        "message": {"role": "user", "content": first_user},
    })
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return f


def _make_projects_root(tmp: Path) -> Path:
    """Build a fake ~/.claude/projects/ with a few sessions."""
    root = tmp / "projects"
    proj_a = root / "-tmp-proj-a"
    proj_a.mkdir(parents=True)
    proj_b = root / "-tmp-proj-b"
    proj_b.mkdir(parents=True)

    _write_session(proj_a, "11111111-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                   title="Analyze SGPDec theory and framework contributions",
                   first_user="Let's talk about SGPDec")
    _write_session(proj_a, "22222222-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                   title="Build ctxport: lossless session compression CLI",
                   first_user="The initial files are at /tmp/foo")
    _write_session(proj_b, "33333333-cccc-cccc-cccc-cccccccccccc",
                   title=None,  # untitled
                   first_user="Help me debug this Python script please")
    return root


def test_enumerate_finds_all_sessions():
    with tempfile.TemporaryDirectory() as td:
        root = _make_projects_root(Path(td))
        rows = ses.enumerate_sessions(root=root)
        assert len(rows) == 3
        # Most-recent-first ordering
        assert rows[0].mtime >= rows[1].mtime >= rows[2].mtime


def test_parse_meta_extracts_title_cwd_first_user():
    with tempfile.TemporaryDirectory() as td:
        root = _make_projects_root(Path(td))
        rows = ses.enumerate_sessions(root=root)
        by_uuid = {r.uuid[:8]: r for r in rows}
        a = by_uuid["11111111"]
        assert a.title == "Analyze SGPDec theory and framework contributions"
        assert a.cwd == "/tmp/proj"
        assert "SGPDec" in a.first_user_text
        c = by_uuid["33333333"]
        assert c.title is None
        assert c.first_user_text.startswith("Help me debug")


def test_resolve_by_path():
    with tempfile.TemporaryDirectory() as td:
        root = _make_projects_root(Path(td))
        rows = ses.enumerate_sessions(root=root)
        chosen, _ = ses.resolve_selector(str(rows[0].path), sessions=rows)
        assert chosen is not None
        assert chosen.path == rows[0].path


def test_resolve_by_uuid_prefix():
    with tempfile.TemporaryDirectory() as td:
        root = _make_projects_root(Path(td))
        rows = ses.enumerate_sessions(root=root)
        chosen, _ = ses.resolve_selector("11111111", sessions=rows)
        assert chosen is not None
        assert chosen.uuid.startswith("11111111")


def test_resolve_by_title_substring_unique():
    with tempfile.TemporaryDirectory() as td:
        root = _make_projects_root(Path(td))
        rows = ses.enumerate_sessions(root=root)
        chosen, candidates = ses.resolve_selector("SGPDec", sessions=rows)
        assert chosen is not None
        assert "SGPDec" in chosen.title
        assert len(candidates) == 1


def test_resolve_by_title_substring_ambiguous():
    """If two sessions share a substring, return None + the candidate list."""
    with tempfile.TemporaryDirectory() as td:
        root = _make_projects_root(Path(td))
        # Add another session with overlap
        proj = root / "-tmp-proj-a"
        _write_session(proj, "44444444-dddd-dddd-dddd-dddddddddddd",
                       title="Discuss SGPDec algorithm trade-offs")
        rows = ses.enumerate_sessions(root=root)
        chosen, candidates = ses.resolve_selector("SGPDec", sessions=rows)
        assert chosen is None
        assert len(candidates) == 2


def test_resolve_default_to_most_recent_when_empty():
    with tempfile.TemporaryDirectory() as td:
        root = _make_projects_root(Path(td))
        rows = ses.enumerate_sessions(root=root)
        chosen, _ = ses.resolve_selector(None, sessions=rows)
        assert chosen is not None
        assert chosen.uuid == rows[0].uuid


def test_resolve_no_matches():
    with tempfile.TemporaryDirectory() as td:
        root = _make_projects_root(Path(td))
        rows = ses.enumerate_sessions(root=root)
        chosen, candidates = ses.resolve_selector("zzz-not-a-thing", sessions=rows)
        assert chosen is None
        assert candidates == []


def test_substring_search_skips_tool_result_user_events():
    """User events that wrap tool_results should not count as 'first user text'."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "projects" / "-x"
        root.mkdir(parents=True)
        f = root / "55555555-eeee-eeee-eeee-eeeeeeeeeeee.jsonl"
        events = [
            # First "user" event is actually a tool_result wrapper — must skip
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu1",
                 "content": "The file ... was edited."}
            ]}},
            # Then the real user-typed message
            {"type": "user", "message": {"role": "user",
                                         "content": "the actual first thing the user said"}},
        ]
        f.write_text("\n".join(json.dumps(e) for e in events))
        info = ses.parse_session_meta(f)
        assert info is not None
        assert info.first_user_text == "the actual first thing the user said"


def test_format_helpers():
    assert ses.format_size(500) == "500 B"
    assert ses.format_size(2048) == "2.0 KB"
    assert ses.format_size(2 * 1024 * 1024) == "2.0 MB"
    # mtime helpers
    import time
    now = time.time()
    assert ses.format_relative_mtime(now - 30, now=now) == "30s ago"
    assert ses.format_relative_mtime(now - 3600, now=now) == "1h ago"


if __name__ == "__main__":
    test_enumerate_finds_all_sessions()
    test_parse_meta_extracts_title_cwd_first_user()
    test_resolve_by_path()
    test_resolve_by_uuid_prefix()
    test_resolve_by_title_substring_unique()
    test_resolve_by_title_substring_ambiguous()
    test_resolve_default_to_most_recent_when_empty()
    test_resolve_no_matches()
    test_substring_search_skips_tool_result_user_events()
    test_format_helpers()
    print("all sessions tests passed")
