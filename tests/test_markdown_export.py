"""Tests for the Markdown export serialiser.

The export exists to make one promise checkable: the memory is portable, and
the files you get are the whole graph. These tests hold the parts that make
that true — links that resolve, ids that survive, and filenames that do not
collide or break a filesystem.

Pure functions throughout: no database, no network, no filesystem.
"""

import pytest

from cloud.markdown_export import (
    build_tree, entity_file, episode_file, frontmatter, procedure_file,
    slugify, wikilink,
)


def entity(name, **kw):
    base = {"id": "id-" + name.lower(), "entity": name, "type": "technology",
            "facts": [], "relations": [], "knowledge": []}
    base.update(kw)
    return base


class TestFilenames:
    def test_a_readable_name_is_kept_as_is(self):
        """The filename is the note title users link by — don't mangle it."""
        assert slugify("PostgreSQL") == "PostgreSQL"
        assert slugify("Project Alpha") == "Project Alpha"

    @pytest.mark.parametrize("raw", ["a/b", "a\\b", "a:b", "a*b", "a?b", 'a"b', "a<b", "a>b", "a|b"])
    def test_characters_a_filesystem_rejects_are_replaced(self, raw):
        assert not set(slugify(raw)) & set('\\/:*?"<>|')

    def test_control_characters_are_replaced(self):
        assert "\n" not in slugify("line\nbreak")

    def test_overlong_names_are_trimmed(self):
        assert len(slugify("x" * 300)) <= 80

    def test_a_name_that_is_all_punctuation_still_yields_a_file(self):
        assert slugify("///") not in ("", None)
        assert slugify("") == "unnamed"

    def test_colliding_names_do_not_overwrite_each_other(self):
        tree = build_tree([entity("a/b"), entity("a:b")])
        files = [p for p in tree if p.startswith("Mengram/entities/")]
        assert len(files) == 2, "one entity silently replaced the other"


class TestLinksResolve:
    def test_relation_becomes_a_wikilink(self):
        tree = build_tree([
            entity("PostgreSQL", relations=[{"type": "caches", "target": "Redis"}]),
            entity("Redis"),
        ])
        assert "[[Redis]]" in tree["Mengram/entities/PostgreSQL.md"]

    def test_a_renamed_target_keeps_its_real_name_via_an_alias(self):
        """The file had to be renamed to be safe; the link must still read right."""
        targets = {"a/b": "a-b"}
        assert wikilink("a/b", targets) == "[[a-b|a/b]]"

    def test_a_target_outside_the_export_still_links(self):
        """A relation can point at something not in this slice. An unresolved
        node is honest; dropping the link would hide that it exists."""
        tree = build_tree([entity("PostgreSQL", relations=[{"type": "caches", "target": "Redis"}])])
        assert "[[Redis]]" in tree["Mengram/entities/PostgreSQL.md"]

    def test_incoming_relations_point_the_other_way(self):
        out = entity_file(entity("A", relations=[
            {"type": "uses", "target": "B", "direction": "incoming"}]), {"B": "B"})
        assert "←" in out


class TestRoundTripIds:
    def test_every_kind_carries_its_id(self):
        tree = build_tree(
            [entity("A")],
            [{"id": "e1", "summary": "s", "created_at": "2026-04-29T00:00:00"}],
            [{"id": "p1", "name": "P"}],
        )
        assert "id: id-a" in tree["Mengram/entities/A.md"]
        assert "id: e1" in tree["Mengram/episodes/2026-04-29-s.md"]
        assert "id: p1" in tree["Mengram/procedures/P.md"]


class TestFrontmatter:
    def test_empty_values_are_omitted(self):
        assert "blank" not in frontmatter({"kept": "yes", "blank": None, "empty": ""})

    def test_values_that_would_break_yaml_are_quoted(self):
        assert frontmatter({"k": "a: b"}).count('"a: b"') == 1
        assert frontmatter({"k": "- dash"}).count('"- dash"') == 1

    def test_lists_render_as_yaml_sequences(self):
        assert "  - Ali" in frontmatter({"participants": ["Ali"]})


class TestProcedures:
    """The differentiator — a workflow with the record that earned its trust."""

    def test_title_carries_version_and_reliability(self):
        out = procedure_file({"name": "Deploy", "version": 3,
                              "success_count": 11, "fail_count": 1})
        assert "# Deploy (v3 · 92% reliable)" in out

    def test_an_untested_procedure_does_not_claim_a_rate(self):
        out = procedure_file({"name": "Deploy", "version": 1})
        assert "untested" in out and "%" not in out.split("\n")[7]

    def test_steps_survive_both_shapes(self):
        """steps is list[dict]; older rows hold plain strings. A half-written
        export is worse than a plain one."""
        out = procedure_file({"name": "P", "steps": [
            {"action": "run tests"}, {"action": "migrate", "detail": "first"}, "push"]})
        assert "1. run tests" in out and "2. migrate — first" in out and "3. push" in out

    def test_evolution_records_what_changed_it(self):
        out = procedure_file({"name": "P", "version": 3}, evolution=[
            {"version_before": 2, "version_after": 3, "created_at": "2026-04-12",
             "diff": {"reason": "added migrations after a prod failure"}}])
        assert "v2 → v3 (2026-04-12): added migrations after a prod failure" in out

    def test_preconditions_are_surfaced(self):
        out = procedure_file({"name": "P", "metadata": {"preconditions": ["run migrations"]}})
        assert "**Preconditions**" in out and "- run migrations" in out


class TestEpisodes:
    def test_filename_leads_with_the_date(self):
        tree = build_tree([], [{"id": "e", "summary": "prod crash",
                                "created_at": "2026-04-29T02:00:00"}])
        assert "Mengram/episodes/2026-04-29-prod crash.md" in tree

    def test_happened_at_wins_over_row_creation_time(self):
        """When the event happened is what a timeline should order by."""
        out = episode_file({"summary": "s", "created_at": "2026-08-01T00:00:00",
                            "happened_at": "2026-04-29T00:00:00"})
        assert "happened: 2026-04-29" in out

    def test_outcome_is_stated_plainly(self):
        assert "**Outcome** — failure" in episode_file({"summary": "s", "outcome": "failure"})


class TestWholeTree:
    def test_the_layout_matches_the_spec(self):
        tree = build_tree([entity("A")], [{"id": "e", "summary": "s", "created_at": "2026-01-02"}],
                          [{"id": "p", "name": "P"}], profile="A developer.")
        assert set(tree) == {
            "Mengram/entities/A.md", "Mengram/episodes/2026-01-02-s.md",
            "Mengram/procedures/P.md", "Mengram/profile.md", "Mengram/_index.md",
        }

    def test_index_counts_and_links_what_was_exported(self):
        tree = build_tree([entity("A"), entity("B")])
        index = tree["Mengram/_index.md"]
        assert "- 2 entities" in index and "[[A]]" in index and "[[B]]" in index

    def test_an_empty_memory_explains_itself(self):
        """An empty folder looks like a broken export."""
        index = build_tree()["Mengram/_index.md"]
        assert "empty" in index.lower()

    def test_every_file_ends_with_exactly_one_newline(self):
        for text in build_tree([entity("A")], [], [{"id": "p", "name": "P"}]).values():
            assert text.endswith("\n") and not text.endswith("\n\n")
