# Created: 2026-07-01
# Purpose: Regression tests for INT-2265 — superthread_my_cards (assignee/creator filter
#          via project→board walk) and INT-2266 hallucination guard presence.

from __future__ import annotations

from pathlib import Path

import pytest


def _fake_st(board):
    def st(path, **kw):
        if path == "projects":
            return {"projects": [{"id": "p1", "board_order": ["b1"]}]}
        if path == "boards/b1":
            return {"board": board}
        return {}
    return st


_BOARD = {
    "title": "B1",
    "lists": [
        {"title": "To do", "behavior": "", "cards": [
            {"id": "1", "title": "assigned-to-me", "members": [{"user_id": "ME"}], "user_id": "other"},
            {"id": "2", "title": "created-by-me", "members": [], "user_id": "ME"},
            {"id": "3", "title": "neither", "members": [{"user_id": "X"}], "user_id": "Y"},
        ]},
        {"title": "Done", "behavior": "done", "cards": [
            {"id": "4", "title": "done-mine", "members": [{"user_id": "ME"}], "user_id": "ME"},
        ]},
    ],
}


def test_my_cards_any_excludes_unrelated_and_done(monkeypatch):
    from pipeline import tools_superthread as t
    monkeypatch.setattr(t, "_my_user_id", lambda: "ME")
    monkeypatch.setattr(t, "_st", _fake_st(_BOARD))

    ids = {c["id"] for c in t.superthread_my_cards(role="any")}
    assert ids == {"1", "2"}  # 3 unrelated, 4 in done list


def test_my_cards_role_assignee_vs_creator(monkeypatch):
    from pipeline import tools_superthread as t
    monkeypatch.setattr(t, "_my_user_id", lambda: "ME")
    monkeypatch.setattr(t, "_st", _fake_st(_BOARD))

    assert {c["id"] for c in t.superthread_my_cards(role="assignee")} == {"1"}
    assert {c["id"] for c in t.superthread_my_cards(role="creator")} == {"2"}
    # role label reflects assignee precedence
    row = next(c for c in t.superthread_my_cards(role="any") if c["id"] == "1")
    assert row["role"] == "assignee"


def test_my_cards_include_done(monkeypatch):
    from pipeline import tools_superthread as t
    monkeypatch.setattr(t, "_my_user_id", lambda: "ME")
    monkeypatch.setattr(t, "_st", _fake_st(_BOARD))

    ids = {c["id"] for c in t.superthread_my_cards(role="any", include_done=True)}
    assert "4" in ids


def test_my_cards_dedupes_same_card_across_lists(monkeypatch):
    from pipeline import tools_superthread as t
    board = {"title": "B", "lists": [
        {"title": "A", "behavior": "", "cards": [{"id": "9", "members": [{"user_id": "ME"}], "user_id": "z"}]},
        {"title": "B", "behavior": "", "cards": [{"id": "9", "members": [{"user_id": "ME"}], "user_id": "z"}]},
    ]}
    monkeypatch.setattr(t, "_my_user_id", lambda: "ME")
    monkeypatch.setattr(t, "_st", _fake_st(board))
    assert len(t.superthread_my_cards(role="any")) == 1


def test_my_cards_skips_unreadable_board(monkeypatch):
    """A board that 403s is skipped, not fatal to the whole sweep."""
    from pipeline import tools_superthread as t
    monkeypatch.setattr(t, "_my_user_id", lambda: "ME")

    def st(path, **kw):
        if path == "projects":
            return {"projects": [{"id": "p1", "board_order": ["bad", "good"]}]}
        if path == "boards/bad":
            raise RuntimeError("Superthread 미연결/만료 (HTTP 403)")
        if path == "boards/good":
            return {"board": {"title": "G", "lists": [
                {"title": "T", "behavior": "", "cards": [{"id": "7", "members": [{"user_id": "ME"}], "user_id": "z"}]},
            ]}}
        return {}
    monkeypatch.setattr(t, "_st", st)
    assert {c["id"] for c in t.superthread_my_cards(role="any")} == {"7"}


def test_my_cards_requires_auth(monkeypatch):
    from pipeline import tools_superthread as t
    monkeypatch.setattr(t, "_my_user_id", lambda: None)
    with pytest.raises(RuntimeError):
        t.superthread_my_cards()


def test_my_cards_registered_in_toolset_and_schema():
    from pipeline import tools_superthread as t
    from pipeline import tool_registry as tr
    assert "superthread_my_cards" in t.SUPERTHREAD_TOOL_FUNCTIONS
    assert any(s["name"] == "superthread_my_cards" for s in t.SUPERTHREAD_TOOL_SCHEMAS)
    assert "superthread_my_cards" in tr.WORKSPACE_TOOLSETS["superthread"]["tools"]


def test_hallucination_guard_present_in_default_constitution():
    """INT-2266: the default agent constitution must forbid fabricating specifics."""
    text = Path("data/agents/_default.md").read_text(encoding="utf-8").lower()
    assert "fabricate" in text
    assert "error rate" in text or "accuracy" in text
