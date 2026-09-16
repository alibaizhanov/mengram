# Product Hunt relaunch — September 2026

Previous launch: 7 months ago, 108 upvotes, #10 of the day, tagline "AI memory API with 3 types".
PH rule: 6 months + a significant update. Since then: compaction checkpoint, Codex + Cursor hooks,
salience gate, provenance per fact, host filter, task card (`mengram resume`), reproducible benchmark.

## Name
Mengram

## Tagline (≤60)
Memory that survives /clear, compaction and the next agent

## Description (≤260)
Open-source memory for Claude Code, Codex, Cursor and your own agents. Hooks recall on every prompt, save
after each turn, checkpoint before compaction, and hand the task to the next session. 23–62× less context
than sending history, with the numbers published.

## Topics
Developer Tools · Artificial Intelligence · Open Source · Productivity

## Links
Website https://mengram.io · GitHub https://github.com/alibaizhanov/mengram · Docs https://docs.mengram.io

## Pricing
Free plan + paid (Pro). Open source, Apache-2.

## First comment (maker)
Hi PH — Ali, solo maker. I launched Mengram here seven months ago as "an AI memory API with three types".
That was the wrong pitch: nobody buys memory types. What people actually lose is the work: Claude Code
compacts and forgets which files it just edited, a new session re-explains the project, Cursor and Codex
don't know what Claude decided yesterday, and the next agent redoes a step that was already done.

So this version is about that, and it comes with numbers instead of adjectives.

What's new since March:
- Hooks for Claude Code, Codex and Cursor: recall on every prompt, save after each turn, a checkpoint
  before compaction that puts the working state back (files you edited, last commands, your last request).
- `mengram resume`: a task card written when an agent stops — done, remaining, last test and the commit it
  ran on — keyed by repo + branch so another session, another agent or another machine picks it up.
- Every fact says where it came from (tool, session, date), and a Mac path never reaches a Linux session.
- A salience gate before write: 45% less junk stored, no recall lost in 18 runs.
- A benchmark you can re-run: over 90 simulated days, 23–62× fewer context tokens than sending the whole
  history, same or better recall on personal facts.

Where it still loses, published: on customer-support dialogue at 30 days recall was 0.25 until this week;
the bug was my contradiction pass archiving a loyalty number for a vaguer restatement. It's 0.875 now,
and the experiment log with the rejected results is in the repo (experiments/QUEUE.md, RESULTS.jsonl).

Install: `pip install mengram-ai && mengram setup --key <KEY>` — or no account at all, the checkpoint and
task card work locally. Apache-2.

Question for you: what do you lose most between sessions — the state, the decisions, or the reasons?
That's what I'll build next.

## Gallery (1270×760)
1. terminal: recall block with provenance tags
2. terminal: `mengram resume` card with the staleness line
3. benchmark table: full history vs memory
4. the resume page (confirm / copy context)

## Schedule
00:01 PT (12:01 Almaty), first Tue–Thu available. Criterion for the week: 100 signups, 10 accounts with a
first add by user_id.
