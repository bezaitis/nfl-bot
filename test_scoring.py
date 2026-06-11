"""
Table-driven tests for the weighted scorer in scoring.py.

Encodes the calibration intent at the current weights:
  - A Bears mention alone posts.
  - A star player needs a transaction keyword to post.
  - Draft-pick or money language needs a strong transaction keyword.
  - Opinion/hypothetical phrasing kills a post even with strong keywords.
"""
import sys

from scoring import score_text, POST_THRESHOLD


# (name, text, team, should_post)
CASES = [
    # ── Followed team ────────────────────────────────────────────────────────
    ("Bears mention alone posts",
     "The Chicago Bears announced a new coaching staff hire", "", True),
    ("Bears one-liner posts",
     "Bears fans, today is a good day", "", True),
    ("CHI abbreviation posts",
     "CHI agrees to terms with a veteran lineman", "", True),
    ("team field alone posts",
     "Re-signed G Veteran Player to the practice squad", "Chicago Bears", True),
    ("Chiefs does not match 'chi'",
     "The Kansas City Chiefs sign a kicker to the roster", "", False),

    # ── Star players ─────────────────────────────────────────────────────────
    ("star player alone does not post",
     "Patrick Mahomes spotted at a charity golf event", "", False),
    ("star + transaction keyword posts",
     "Patrick Mahomes signed a restructured contract today", "", True),
    ("unknown player + keyword does not post",
     "John Smith signed with the practice squad", "", False),

    # ── Draft picks ──────────────────────────────────────────────────────────
    ("draft pick alone does not post",
     "He was once a first-round pick out of Ohio State", "", False),
    ("draft pick + strong keyword posts",
     "Team traded a 2026 first-round pick to move up", "", True),

    # ── Money ────────────────────────────────────────────────────────────────
    ("high AAV extension posts",
     "He agreed to a 4-year, $120 million extension", "", True),
    ("mid AAV + strong keyword posts",
     "Signed a 2-year, $24 million deal with incentives", "", True),
    ("small contract does not post",
     "Signed a one-year, $5 million deal", "", False),
    ("big total without years + strong keyword posts",
     "Signed a contract worth $150 million", "", True),
    ("big total without strong keyword does not post",
     "A contract worth $150 million is reportedly close", "", False),

    # ── Opinion / regressions ────────────────────────────────────────────────
    ("opinion with strong keyword never posts",
     "The Chiefs should have traded for a receiver at the deadline", "", False),
    ("opinion about a star never posts",
     "The Jets should have signed Davante Adams last offseason", "", False),
    ("weak keyword without sourcing does not post",
     "Thoughts on his contract situation heading into camp", "", False),
    ("strong keyword alone does not post",
     "An injury update is expected later today", "", False),
]


def test_table():
    failed = []
    for name, text, team, should_post in CASES:
        score, reasons = score_text(text, team=team)
        posts = score >= POST_THRESHOLD
        if posts != should_post:
            failed.append(
                f"{name}: expected post={should_post}, got score={score} reasons={reasons}"
            )
    assert not failed, "\n".join(failed)


def test_reasons_are_tagged():
    """Reason strings carry the emoji tags shown in embeds."""
    score, reasons = score_text("The Chicago Bears signed Patrick Mahomes "
                                "to a 4-year, $120 million extension")
    assert score >= POST_THRESHOLD
    assert "🐻 Bears" in reasons, reasons
    assert "⭐ Patrick Mahomes" in reasons, reasons
    assert any(r.startswith("💰") for r in reasons), reasons


def test_higher_signal_scores_higher():
    """Ordering matters: stacked signals must outrank a bare team mention."""
    bare, _ = score_text("Bears practice resumed today")
    stacked, _ = score_text("Bears traded a 2026 first-round pick for a star")
    assert stacked > bare, (stacked, bare)


# ── run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        ("calibration table", test_table),
        ("reasons carry emoji tags", test_reasons_are_tagged),
        ("stacked signals outrank bare mention", test_higher_signal_scores_higher),
    ]

    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            print(f"  FAIL  {name}: {e}")
            failed += 1

    print()
    if failed:
        print(f"{failed}/{len(tests)} test(s) FAILED")
        sys.exit(1)
    else:
        print(f"All {len(tests)} tests passed ({len(CASES)} table cases).")
