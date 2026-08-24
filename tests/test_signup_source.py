"""Tests for signup attribution.

The point of the column is counting: if `Reddit`, `reddit ` and `reddit` land
as three rows, the number it produces is worse than no number, because it
looks like an answer.
"""

from cloud.attribution import MAX_LENGTH, clean_source


class TestCleanSource:
    def test_plain_tag_survives(self):
        assert clean_source("reddit") == "reddit"
        assert clean_source("memfmt") == "memfmt"

    def test_case_and_padding_collapse(self):
        """The whole reason this exists — one channel, one row."""
        assert clean_source("Reddit") == clean_source(" reddit ") == clean_source("REDDIT")

    def test_separators_normalise(self):
        assert clean_source("r/AI_Agents") == "r-ai_agents"
        assert clean_source("show hn") == "show-hn"

    def test_nothing_usable_is_none_not_empty(self):
        """NULL means we never knew. An empty string would claim we asked and
        got nothing, which is a different fact."""
        for value in ("", "   ", "!!!", "///", None, 0):
            assert clean_source(value) is None

    def test_non_latin_is_dropped_rather_than_mangled(self):
        assert clean_source("Показ") is None

    def test_length_is_capped_without_trailing_separator(self):
        long = clean_source("a" * 200)
        assert len(long) == MAX_LENGTH
        # A cut landing mid-separator must not leave a dangling dash, or the
        # same channel truncates two ways.
        assert clean_source("x" * (MAX_LENGTH - 1) + " tail").endswith("x")
        assert not clean_source("y" * (MAX_LENGTH - 1) + " tail").endswith("-")

    def test_hostile_input_is_reduced_to_a_tag(self):
        assert clean_source("'; DROP TABLE users;--") == "drop-table-users"
        assert clean_source("<script>alert(1)</script>") == "script-alert-1-script"
        # Dots survive on purpose: `mengram.io` and `dev.to` are real tags.
        assert clean_source("dev.to") == "dev.to"

    def test_is_idempotent(self):
        """Cleaning what was already cleaned must not change it — the GitHub
        flow runs it twice, once on the way out and once on the way back."""
        for value in ("reddit", "r/AI_Agents", "  Show HN  ", "a" * 200):
            once = clean_source(value)
            assert clean_source(once) == once
