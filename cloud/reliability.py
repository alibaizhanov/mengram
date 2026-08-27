"""How much to trust a workflow, or one step of it, in words.

Kept in its own module because three places have to agree: what the API hands
an agent, what the Markdown export writes, and what the published memfmt
library reads back. Two of them already computed it separately and the third
did not compute it at all, which is how the same record came to mean different
things depending on where you read it.

A bare success/total ratio is the wrong number twice over. It overstates small
samples — one success is not 100% — and it punishes a revision, which opens at
0/0 and so reads worse than the version it was written to fix. Progressive
delivery and CI met both years ago (a canary confidence record, a flake
quarantine ledger) and answered with the same move: smooth against a prior
instead of comparing raw counts.

The word matters as much as the number. "Never run" and "run and it worked"
must not read alike, because an agent acts on the difference.
"""

from __future__ import annotations

#: How much of a predecessor's record carries into its successor's prior.
#: Half: enough that a long-reliable lineage does not re-earn trust from
#: scratch, little enough that a few runs of the new version dominate it.
#: Must match memfmt's LINEAGE_WEIGHT — these files are read back by it.
LINEAGE_WEIGHT = 0.5

NEUTRAL_PRIOR = (1.0, 1.0)


def estimate(success: int, fail: int, prior: tuple = NEUTRAL_PRIOR) -> str:
    """Trust in words: `untested`, `N% expected`, or `N% reliable`.

    `expected` means the version has no runs of its own and is standing on what
    came before it. `reliable` means it has a record. `untested` means there is
    nothing to go on at all, and inventing a number for that would be worse
    than saying so.
    """
    alpha, beta = prior
    observed = int(success or 0) + int(fail or 0)
    if observed == 0 and tuple(prior) == NEUTRAL_PRIOR:
        return "untested"
    value = (int(success or 0) + alpha) / (observed + alpha + beta)
    return f"{round(100 * value)}% {'reliable' if observed else 'expected'}"


def prior_from_lineage(evolution: list = None) -> tuple:
    """Beta prior taken from what the previous version retired with.

    A predecessor's history is evidence about a successor without being a claim
    about it, so it is discounted rather than carried forward whole. Carrying
    it forward whole would be the claim, and it would be false.
    """
    for entry in reversed(evolution or []):
        success, fail = entry.get("success_count"), entry.get("fail_count")
        if success is not None or fail is not None:
            return (1.0 + LINEAGE_WEIGHT * (success or 0),
                    1.0 + LINEAGE_WEIGHT * (fail or 0))
    return NEUTRAL_PRIOR


def _identity(step) -> str:
    """What makes a step the same step across a revision: what it says.

    Not its position. A revision that inserts a step at the top shifts every
    index below it, and carrying records by index would hand each step the
    history of its neighbour.
    """
    if not isinstance(step, dict):
        return " ".join(str(step).lower().split())
    parts = (step.get("action") or "", step.get("detail") or "")
    return " ".join(" ".join(str(p) for p in parts).lower().split())


def carry_step_history(old_steps: list, new_steps: list) -> list:
    """Move per-step records onto a revision, but only for steps it left alone.

    A revision rewrites one or two steps and leaves the rest verbatim. Dropping
    every record at that boundary — which is what wholesale step replacement
    did — means a workflow can never accumulate evidence about the parts of it
    that were never in question.

    The opposite mistake is worse, and it is the one worth being careful about:
    if a rewritten step keeps the counters of the text it replaced, a new
    `verify /health` inherits eleven successes it never earned, and the number
    reads as evidence while being the newest-thing-wins bug in better clothes.
    So a step keeps its record only when its text is unchanged; any edit starts
    it at zero, which is the honest reading — nobody has run *this* yet.

    Identical steps are matched one-for-one so a duplicated step cannot claim
    the same runs twice.
    """
    available: dict = {}
    for step in old_steps or []:
        if not isinstance(step, dict):
            continue
        if step.get("success_count") is None and step.get("fail_count") is None:
            continue
        available.setdefault(_identity(step), []).append(step)

    out = []
    for step in new_steps or []:
        if not isinstance(step, dict):
            out.append(step)
            continue
        step = dict(step)
        step.pop("success_count", None)      # never trust counters the LLM emitted
        step.pop("fail_count", None)
        match = available.get(_identity(step))
        if match:
            old = match.pop(0)
            for key in ("success_count", "fail_count"):
                if old.get(key) is not None:
                    step[key] = old[key]
        out.append(step)
    return out


def annotate_steps(steps: list) -> list:
    """Add `reliability` to each step that carries a record.

    Steps with no record are left alone: absence means nobody measured, never
    that something went wrong, and writing `untested` onto every step of every
    old procedure would say the opposite.
    """
    out = []
    for step in steps or []:
        if not isinstance(step, dict):
            out.append(step)
            continue
        if step.get("success_count") is not None or step.get("fail_count") is not None:
            step = dict(step)
            step["reliability"] = estimate(step.get("success_count") or 0,
                                           step.get("fail_count") or 0)
        out.append(step)
    return out
