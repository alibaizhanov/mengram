"""A run does not teach one thing — it teaches as many as there are steps.

When step 3 of 5 fails, steps 1 and 2 ran and worked. Recording only "the
procedure failed" throws that away and leaves every step equally suspect, which
is exactly what an agent cannot act on. It is also what kept the record from
surviving a revision: a revision usually touches one step, and the untouched
ones have no reason to forget the runs behind them.

Pure function, no database.
"""

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
