"""
Tests for the seen_ids deduplication fix.

Verifies that save_seen preserves insertion order when trimming,
so recently-added IDs are never dropped (the bug: list(set)[-N:]
trims arbitrarily from an unordered set).

We test the helpers in isolation to avoid needing a Discord token.
"""
import json
import tempfile
import os
import sys


# ── copy of the helpers under test (matches bot.py exactly) ──────────────────

SEEN_MAX_SIZE = 500
SEEN_FILE = "seen_ids.json"  # overridden per-test


def load_seen(path=None):
    target = path or SEEN_FILE
    try:
        with open(target) as f:
            data = json.load(f)
        return set(data), list(data)
    except (FileNotFoundError, json.JSONDecodeError):
        return set(), []


def save_seen(seen_list, path=None):
    target = path or SEEN_FILE
    trimmed = seen_list[-SEEN_MAX_SIZE:]
    with open(target, "w") as f:
        json.dump(trimmed, f)


# ── helpers ───────────────────────────────────────────────────────────────────

def run_test(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        raise


# ── tests ─────────────────────────────────────────────────────────────────────

def test_roundtrip():
    """IDs written and read back are identical."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        ids = [f"id{i}" for i in range(10)]
        save_seen(ids, path)
        seen_set, seen_list = load_seen(path)
        assert seen_set == set(ids), f"set mismatch: {seen_set}"
        assert seen_list == ids, f"list mismatch: {seen_list}"
    finally:
        os.unlink(path)


def test_trimming_removes_oldest():
    """When list exceeds SEEN_MAX_SIZE, oldest entries are removed, not newest."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        old_ids = [f"old{i}" for i in range(SEEN_MAX_SIZE)]
        new_ids = [f"new{i}" for i in range(50)]
        all_ids = old_ids + new_ids  # new_ids appended last

        save_seen(all_ids, path)
        _, saved = load_seen(path)

        assert len(saved) == SEEN_MAX_SIZE, f"expected {SEEN_MAX_SIZE} entries, got {len(saved)}"

        for nid in new_ids:
            assert nid in saved, f"recently-added ID '{nid}' was trimmed"

        dropped = old_ids[:50]
        for did in dropped:
            assert did not in saved, f"old ID '{did}' should have been trimmed"
    finally:
        os.unlink(path)


def test_new_id_not_lost_at_boundary():
    """
    Reproduce the exact bug: seen list has SEEN_MAX_SIZE items, a new ID is
    appended, and save_seen must retain the new ID (not trim it).
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        existing = [f"existing{i}" for i in range(SEEN_MAX_SIZE)]
        save_seen(existing, path)

        # Simulate the bot loop: load, add a new post, save
        seen_set, seen_list = load_seen(path)
        new_id = "brand_new_post_id"
        seen_set.add(new_id)
        seen_list.append(new_id)
        save_seen(seen_list, path)

        seen_set2, seen_list2 = load_seen(path)
        assert new_id in seen_set2, "new post ID was trimmed — duplicate would be re-posted"
        assert len(seen_list2) == SEEN_MAX_SIZE
    finally:
        os.unlink(path)


def test_old_bug_would_fail():
    """
    Demonstrate that the OLD buggy implementation (list(set)[-N:]) could drop
    a recently-added ID, while the new implementation does not.
    """
    # Build a set with SEEN_MAX_SIZE + 1 entries
    existing = {f"existing{i}" for i in range(SEEN_MAX_SIZE)}
    new_id = "brand_new_post_id"
    existing.add(new_id)
    assert len(existing) == SEEN_MAX_SIZE + 1

    # Old buggy trimming
    old_trimmed = list(existing)[-SEEN_MAX_SIZE:]
    # New correct trimming (list with known order, new_id appended last)
    ordered = [f"existing{i}" for i in range(SEEN_MAX_SIZE)] + [new_id]
    new_trimmed = ordered[-SEEN_MAX_SIZE:]

    # New approach always keeps new_id
    assert new_id in new_trimmed, "new implementation must retain the new ID"

    # Old approach may or may not keep it (non-deterministic across runs),
    # but we can confirm the new approach is correct.
    print(f"         (old approach kept new_id: {new_id in old_trimmed} — may vary by run)")


def test_missing_file_returns_empty():
    """load_seen on a nonexistent file returns empty structures."""
    seen_set, seen_list = load_seen("/tmp/definitely_does_not_exist_nflbot_test.json")
    assert seen_set == set()
    assert seen_list == []


def test_order_preserved():
    """Insertion order is preserved through a save/load cycle."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        ids = ["z", "a", "m", "b", "q"]
        save_seen(ids, path)
        _, seen_list = load_seen(path)
        assert seen_list == ids, f"order changed: {seen_list}"
    finally:
        os.unlink(path)


# ── run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        ("roundtrip save/load", test_roundtrip),
        ("trimming removes oldest", test_trimming_removes_oldest),
        ("new ID not lost at boundary (the bug)", test_new_id_not_lost_at_boundary),
        ("old buggy impl demo", test_old_bug_would_fail),
        ("missing file returns empty", test_missing_file_returns_empty),
        ("insertion order preserved", test_order_preserved),
    ]

    failed = 0
    for name, fn in tests:
        try:
            run_test(name, fn)
        except AssertionError:
            failed += 1

    print()
    if failed:
        print(f"{failed}/{len(tests)} test(s) FAILED")
        sys.exit(1)
    else:
        print(f"All {len(tests)} tests passed.")
