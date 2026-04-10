"""Tests for threaded conversations (app/storage/threads.py)."""
from __future__ import annotations

import pytest
from app.storage.threads import (
    create_thread,
    get_participants,
    get_thread,
    is_participant,
    list_user_threads,
    save_message,
    get_messages,
    add_participant,
    remove_participant,
)


def test_create_thread_returns_id():
    tid = create_thread("Test Thread", created_by="alice")
    assert isinstance(tid, str)
    assert len(tid) > 0


def test_create_thread_adds_creator_as_participant():
    tid = create_thread("Test", created_by="alice")
    assert is_participant(tid, "alice")


def test_create_thread_with_participants():
    tid = create_thread("Group Chat", created_by="alice", participants=["bob", "carol"])
    assert is_participant(tid, "alice")
    assert is_participant(tid, "bob")
    assert is_participant(tid, "carol")


def test_get_thread_returns_data():
    tid = create_thread("My Thread", created_by="alice")
    thread = get_thread(tid)
    assert thread is not None
    assert thread["name"] == "My Thread"
    assert thread["created_by"] == "alice"


def test_get_thread_not_found():
    result = get_thread("nonexistent-id")
    assert result is None


def test_list_user_threads_returns_own():
    create_thread("Alice Thread", created_by="alice_list")
    create_thread("Bob Thread", created_by="bob_list")
    threads = list_user_threads("alice_list")
    names = [t["name"] for t in threads]
    assert "Alice Thread" in names
    assert "Bob Thread" not in names


def test_get_participants():
    tid = create_thread("Test", created_by="alice", participants=["bob"])
    parts = get_participants(tid)
    assert "alice" in parts
    assert "bob" in parts


def test_is_participant_false():
    tid = create_thread("Private", created_by="alice")
    assert not is_participant(tid, "eve")


def test_save_and_get_messages():
    tid = create_thread("Chat", created_by="alice")
    save_message(tid, sender="alice", role="user", content="Hello!")
    save_message(tid, sender="agent", role="assistant", content="Hi there!")

    msgs = get_messages(tid, limit=10)
    assert len(msgs) == 2
    contents = [m["content"] for m in msgs]
    assert "Hello!" in contents
    assert "Hi there!" in contents


def test_add_participant():
    tid = create_thread("Test", created_by="alice")
    add_participant(tid, "charlie", added_by="alice")
    assert is_participant(tid, "charlie")


def test_remove_participant():
    tid = create_thread("Test", created_by="alice", participants=["bob"])
    remove_participant(tid, "bob")
    assert not is_participant(tid, "bob")
