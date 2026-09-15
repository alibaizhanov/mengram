"""A relative is never the speaker.

E0 (2026-09-16, companion/L) put every "I play the bass" on "User's sister":
"User" facts are forwarded to the account's top person entity, and with an
unnamed speaker that entity was the one relative mentioned. These hold the
rule that decides who can be the speaker at all.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from cloud.store._naming import looks_like_someone_else


def test_relatives_roles_and_possessives_are_someone_else():
    for name in ["User's sister", "User’s mother", "my brother", "Sam's daughter", "the neighbour",
                 "colleague from Riga", "a friend", "Dr. Chen (my doctor)", "the PR reviewer",
                 "User's colleague", "my partner"]:
        assert looks_like_someone_else(name), name


def test_actual_people_and_the_user_are_not():
    for name in ["Ali Baizhanov", "Sam", "Dr. Sarah Chen", "Baizhanov", "Jamie Lee", "User"]:
        assert not looks_like_someone_else(name), name


def test_empty_is_not_someone_else():
    assert not looks_like_someone_else("") and not looks_like_someone_else(None)
