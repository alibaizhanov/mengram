"""Reading the failing step out of what was already written.

Nobody calls the feedback tool with a step number — zero calls in a month of
production — so the per-step record stays empty unless the step can be inferred
from the failure text. It usually says it outright: "timed out on the
migration", "health check never returned 200".

A wrong index is worse than none: it debits a step that did nothing wrong, and
that record is exactly what an agent is meant to trust. So these tests care
more about the refusals than the hits.
"""

from cloud.evolution import EvolutionEngine
from cloud.reliability import annotate_steps, estimate

infer = EvolutionEngine.infer_failed_step

STEPS = [
    {"step": 1, "action": "push code", "detail": "to Railway"},
    {"step": 2, "action": "run migrations", "detail": "against the primary"},
    {"step": 3, "action": "verify health", "detail": "expect 200 within 60s"},
]


class TestInferFailedStep:
    def test_it_finds_the_step_the_text_describes(self):
        assert infer(STEPS, "the health check never returned 200, timed out") == 3
        assert infer(STEPS, "migrations failed against the primary") == 2
        assert infer(STEPS, "could not push code to Railway") == 1

    def test_a_vague_report_names_nothing(self):
        """"It broke" is every failure report ever written."""
        for text in ("it broke again", "failed", "error during the run", ""):
            assert infer(STEPS, text) is None

    def test_a_lone_word_counts_when_only_one_step_matches(self):
        """Terse reports are the norm. If exactly one step is mentioned at all,
        that is the step — refusing here would mean never firing."""
        assert infer(STEPS, "migrations broke") == 2

    def test_a_lone_word_shared_with_rivals_is_a_coincidence(self):
        steps = [{"action": "upload the report"}, {"action": "upload the invoice"},
                 {"action": "send the digest"}]
        assert infer(steps, "the upload failed") is None

    def test_two_steps_fitting_equally_well_names_neither(self):
        steps = [{"action": "upload the report"}, {"action": "upload the invoice"}]
        assert infer(steps, "the upload failed") is None

    def test_failure_vocabulary_does_not_decide(self):
        """Every report contains 'failed'. If a step happened to say it too,
        that word must not be what picks it."""
        steps = [{"action": "retry on failed uploads"}, {"action": "send the digest"}]
        assert infer(steps, "the digest never went out") == 2

    def test_no_steps_is_not_an_error(self):
        assert infer([], "anything") is None
        assert infer(None, "anything") is None

    def test_plain_string_steps_are_skipped_not_crashed(self):
        assert infer(["push code", {"action": "run migrations"}], "migrations broke") == 2


class TestReliabilityIsOneFormula:
    """Three places have to agree: what the API hands an agent, what the export
    writes, and what memfmt reads back."""

    def test_small_samples_do_not_read_as_certainty(self):
        assert estimate(1, 0) == "67% reliable"
        assert estimate(5, 0) == "86% reliable"
        assert estimate(4, 1) == "71% reliable"

    def test_nothing_to_go_on_says_so(self):
        assert estimate(0, 0) == "untested"

    def test_a_lineage_prior_reads_as_expected_not_reliable(self):
        assert estimate(0, 0, (6.5, 1.5)).endswith("expected")

    def test_steps_with_a_record_get_a_reading(self):
        out = annotate_steps([{"action": "a", "success_count": 4, "fail_count": 1}])
        assert out[0]["reliability"] == "71% reliable"
        assert out[0]["success_count"] == 4        # raw counts stay

    def test_steps_without_a_record_are_left_alone(self):
        """Absence means nobody measured, never that something went wrong."""
        out = annotate_steps([{"action": "a"}])
        assert "reliability" not in out[0]

    def test_the_input_is_not_mutated(self):
        original = [{"action": "a", "success_count": 1}]
        annotate_steps(original)
        assert "reliability" not in original[0]
