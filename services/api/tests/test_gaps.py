"""Questions and gaps (F5a): a question naming one person's sensitive matters or personal data is never kept; a question
wasn't answered well when nothing was found, its asker said it didn't help, or, with no word from them, its confidence
was low; questions asking the same thing group together; and a group is a gap only once enough different people went
unanswered, worded as most of them asked it, with their teams and never their names."""
from datetime import datetime, timedelta, timezone

from kl.gaps import gap_of, group, kept_text, unanswered
from kl.unit_answers import question_words

T0 = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)


def q(n, user, text, found=True, confidence="medium", helpful=None, teams=("stores",)):
    return {"id": n, "username": user, "text": text, "words": sorted(question_words(text)), "teams": list(teams),
            "found": found, "confidence": confidence, "helpful": helpful, "asked_at": T0 + timedelta(minutes=n)}


def test_a_question_naming_someones_health_or_personal_data_is_never_kept():
    assert kept_text("  When does the  store open on Sunday? ") == "When does the store open on Sunday?"
    assert kept_text("Is Sam back from his surgery yet?") is None
    assert kept_text("What is 412-55-9876 for?") is None or kept_text("Call me at sam.rivera@example.com") is None


def test_unanswered_means_nothing_found_said_unhelpful_or_low_confidence_without_word():
    assert unanswered(q(1, "a", "x", found=False))
    assert unanswered(q(1, "a", "x", helpful=False))
    assert unanswered(q(1, "a", "x", confidence="low"))
    assert not unanswered(q(1, "a", "x", confidence="low", helpful=True))  # the asker said it helped
    assert not unanswered(q(1, "a", "x", confidence="high"))


def test_questions_asking_the_same_thing_group_together_and_others_dont():
    rows = [q(1, "ana", "Where do we store the spare ladder keys?"), q(2, "ben", "Where is the spare ladder key stored?"),
            q(3, "cora", "When is the Christmas party?")]
    assert [[r["id"] for r in g] for g in group(rows)] == [[1, 2], [3]]


def test_a_gap_needs_enough_different_people_unanswered_and_never_names_them():
    missed = [q(1, "ana", "Where are the spare ladder keys?", found=False),
              q(2, "ben", "Where are the spare ladder keys?", helpful=False, teams=("stores", "loss-prevention")),
              q(3, "ana", "Where are spare ladder keys kept?", confidence="low"),
              q(4, "cora", "Where are the spare ladder keys?", confidence="low")]
    gap = gap_of(missed, 3)
    assert gap["askers"] == 3 and gap["asked"] == 4 and gap["question"] == "Where are the spare ladder keys?"
    assert gap["teams"] == ["loss-prevention", "stores"] and not gap["answered"]
    assert "ana" not in str(gap) and "cora" not in str(gap)
    assert gap_of(missed[:3], 3) is None  # two people, however often
    answered = missed[:3] + [q(4, "cora", "Where are the spare ladder keys?", confidence="low", helpful=True)]
    assert gap_of(answered, 3) is None  # the third said it helped


def test_a_gap_whose_latest_ask_was_answered_is_marked_so():
    rows = [q(n, u, "Where are the spare ladder keys?", found=False) for n, u in enumerate(("ana", "ben", "cora"), 1)]
    rows.append(q(4, "dara", "Where are the spare ladder keys?", confidence="high"))
    assert gap_of(rows, 3)["answered"]
