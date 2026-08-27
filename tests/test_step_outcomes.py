"""A run does not teach one thing — it teaches as many as there are steps.

When step 3 of 5 fails, steps 1 and 2 ran and worked. Recording only "the
procedure failed" throws that away and leaves every step equally suspect, which
is exactly what an agent cannot act on. It is also what kept the record from
surviving a revision: a revision usually touches one step, and the untouched
ones have no reason to forget the runs behind them.

Pure function, no database.
"""

from cloud.reliability import carry_step_history as carry
from cloud.store import CloudStore

apply = CloudStore._apply_step_outcome


def steps(n=5):
    return [{"step": i, "action": f"step {i}", "detail": ""} for i in range(1, n + 1)]


def counts(result):
    return [(s.get("success_count"), s.get("fail_count")) for s in result]


class TestStepOutcomes:
    def test_success_credits_every_step(self):
        assert counts(apply(steps(3), success=True)) == [(1, None), (1, None), (1, None)]

    def test_failure_credits_the_steps_that_ran_and_debits_the_one_that_broke(self):
        result = apply(steps(5), success=False, failed_at_step=3)
        assert counts(result) == [(1, None), (1, None), (None, 1), (None, None), (None, None)]

    def test_the_first_step_failing_credits_nothing(self):
        assert counts(apply(steps(3), success=False, failed_at_step=1)) == [
            (None, 1), (None, None), (None, None)]

    def test_a_failure_with_no_step_named_attributes_nothing(self):
        """Better a procedure-level count alone than blaming five steps for
        something one of them did."""
        assert counts(apply(steps(3), success=False)) == [
            (None, None), (None, None), (None, None)]

    def test_counts_accumulate_across_runs(self):
        s = steps(3)
        for _ in range(4):
            s = apply(s, success=True)
        s = apply(s, success=False, failed_at_step=3)
        assert counts(s) == [(5, None), (5, None), (4, 1)]

    def test_a_step_past_the_end_is_harmless(self):
        """`failed_at_step` arrives from a caller and may not match the steps."""
        assert counts(apply(steps(2), success=False, failed_at_step=9)) == [(1, None), (1, None)]

    def test_non_dict_steps_survive(self):
        """Older rows hold plain strings; a half-written update is worse than
        no update."""
        mixed = ["do the thing", {"step": 2, "action": "then this"}]
        result = apply(mixed, success=True)
        assert result[0] == "do the thing"
        assert result[1]["success_count"] == 1

    def test_the_original_is_not_mutated(self):
        original = steps(2)
        apply(original, success=True)
        assert original[0].get("success_count") is None


class TestRecordSurvivesRevision:
    """What a revision is allowed to keep.

    Raised by deelight_0909 on r/hermesagent: if a rewritten step keeps the
    counters of the text it replaced, a fresh `/health` check inherits eleven
    successes it never earned — "newest thing wins" wearing better numbers.
    They were right that it is the danger; the code had the opposite bug, and
    dropped every record at the revision boundary instead.
    """

    def test_an_untouched_step_keeps_its_record(self):
        old = [{"action": "push to main", "success_count": 12},
               {"action": "verify health", "success_count": 9, "fail_count": 3}]
        new = [{"action": "push to main"},
               {"action": "verify health"}]
        assert counts(carry(old, new)) == [(12, None), (9, 3)]

    def test_an_edited_step_starts_over(self):
        """The whole point. New text has no record, and saying otherwise is
        the claim that makes the number worthless."""
        old = [{"action": "verify health", "success_count": 11}]
        new = [{"action": "verify health with backoff"}]
        assert counts(carry(old, new)) == [(None, None)]

    def test_an_inserted_step_does_not_shift_records_onto_neighbours(self):
        old = [{"action": "push to main", "success_count": 12},
               {"action": "verify health", "success_count": 9}]
        new = [{"action": "wait for the pool"},
               {"action": "push to main"},
               {"action": "verify health"}]
        assert counts(carry(old, new)) == [(None, None), (12, None), (9, None)]

    def test_a_duplicated_step_cannot_claim_the_same_runs_twice(self):
        old = [{"action": "retry upload", "success_count": 4}]
        new = [{"action": "retry upload"}, {"action": "retry upload"}]
        assert counts(carry(old, new)) == [(4, None), (None, None)]

    def test_counters_invented_by_the_model_are_discarded(self):
        """`new_steps` is LLM output. Anything numeric in it is hallucinated."""
        old = [{"action": "push to main"}]
        new = [{"action": "push to main", "success_count": 999}]
        assert counts(carry(old, new)) == [(None, None)]

    def test_detail_is_part_of_what_makes_a_step_the_same_step(self):
        old = [{"action": "verify health", "detail": "expect 200 within 60s",
                "success_count": 9}]
        assert counts(carry(old, [{"action": "verify health",
                                   "detail": "expect 200 within 5s"}])) == [(None, None)]
        assert counts(carry(old, [{"action": "verify health",
                                   "detail": "expect 200 within 60s"}])) == [(9, None)]

    def test_whitespace_and_case_are_not_a_difference(self):
        old = [{"action": "Push  to main", "success_count": 12}]
        assert counts(carry(old, [{"action": "push to main"}])) == [(12, None)]

    def test_a_step_nobody_measured_carries_nothing_and_crashes_nothing(self):
        assert counts(carry([{"action": "a"}], [{"action": "a"}])) == [(None, None)]
        assert carry([], []) == []
        assert carry(None, None) == []

    def test_non_dict_steps_survive(self):
        assert carry([{"action": "a", "success_count": 1}],
                     ["a", {"action": "a"}]) == ["a", {"action": "a", "success_count": 1}]

    def test_the_originals_are_not_mutated(self):
        old = [{"action": "a", "success_count": 3}]
        new = [{"action": "a"}]
        carry(old, new)
        assert new[0].get("success_count") is None
        assert old[0]["success_count"] == 3
