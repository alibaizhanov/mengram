# Reddit drafts — compaction checkpoint (2026-09-15)

Rules that shape these: r/codex (542K) ranks "high information" posts and weights
r/codex comment karma; r/ClaudeCode (729K) bans low-effort promotion and has a
Projects & Showcases rule; r/cursor (97K) limits self-promotion and removes slop.
House rules from MARKETING.md: numbers first, concessions early, one promo post per
account per day, every "X" claim names how it was measured. No signup CTA anywhere —
GitHub link only. Post order: r/codex first (new hooks, nobody has written this up),
r/ClaudeCode the next day, r/cursor only as a discussion thread and only if the
first two draw comments.

---

## 1. r/codex — flair: Guide / Tips (whichever exists)

**Title:** Codex hooks in practice: PreCompact + SessionStart put the working state back after compaction (what the summary drops, and 200 lines that keep it)

**Body:**

Codex got lifecycle hooks recently and I couldn't find a write-up of anyone using `PreCompact`, so here is one, with the gotchas.

**The problem.** Compaction keeps the conversation but not the work. The host writes a summary for itself, and what the summary drops is invisible from inside the session: which files were just edited, what you asked three prompts ago, the last command that failed. The agent carries on from the summary as if nothing were missing, and you notice later — a file edited from a stale idea of it, a finished step done twice. In the Codex tracker "control over auto-compaction" has 112 votes and "disable automatic recaps" 73; in the Claude Code tracker the same complaint shows up as "repeats itself after compaction".

**What the hook pair does.** `PreCompact` fires before the summary is written and hands you `transcript_path` on stdin. The hook reads the tail of the transcript (last 512 KB) and keeps only what locates the work:

- the last 4 things you asked (tool results and hook wrappers skipped)
- every file touched by an edit tool this session (`apply_patch`, `Edit`, `Write`…)
- the last 8 shell commands
- the last thing the assistant said

That goes to `~/.mengram/checkpoints/<session_id>.json`. Nothing is summarised or rewritten — the point is that it's verbatim, sitting next to whatever the host's summary kept.

`SessionStart` with `source: compact` (or `resume`) reads it back as `additionalContext`, once — the file is consumed, so a later `/clear` doesn't replay old work — and prints one line for you:

```
🧠 Mengram put the working state back after compaction: 1 file you edited,
your last request («…»), the last commands run. The summary may have dropped it; this is exact.
```

**Gotchas I hit, so you don't:**

1. Codex's `hooks.json` has Claude Code's shape (event → matcher groups → `{type, command}`) and returns context the same way (`hookSpecificOutput.additionalContext`), so one handler serves both tools. Differences: Codex has no `timeout` key and shows `statusMessage` while the hook runs.
2. The transcript is a different JSONL: Codex writes `{"type":"response_item","payload":{"type":"message"|"function_call",…}}`, Claude Code writes `{"type":"user"|"assistant","message":{…}}`. A tolerant reader that takes what it recognises costs nothing on unknown lines.
3. `SessionStart` on a `resume` can arrive with a new session id. Fall back to the newest checkpoint from the same `cwd`, capped at 7 days.
4. Keep the restore working with no network and no account. A checkpoint that lives on the machine must not depend on a cloud call at session start — ours restores through an outage.

**Install (both tools):**

```
pip install mengram-ai && mengram hook install --codex
```

Source: github.com/alibaizhanov/mengram — `local/checkpoint.py` is the whole mechanism, ~200 lines, Apache-2. The hooks also do recall/save to a memory backend, but the checkpoint itself is local-only and you can lift it without the rest.

What else do you lose at compaction that a hook should keep? I have the last prompts/files/commands; I suspect "the plan we agreed on" is next.

---

## 2. r/ClaudeCode — flair: Showcase (rule: Projects & Showcases)

**Title:** What compaction actually drops, and a PreCompact hook that puts it back — verbatim, in one line you can see (open source)

**Body:**

Everyone here has had the moment after auto-compaction where Claude confidently edits a file from a stale idea of it or redoes a step it finished ten minutes ago. The summary is lossy by design; what's missing is that you can't see *what* it lost.

I built a small hook pair for that. Sharing the mechanism because it's 200 lines and you may want your own version.

**PreCompact** fires before the summary is written, with `transcript_path` in the stdin JSON. The hook reads the transcript's tail and keeps the few things that locate the work: the last 4 prompts, every file touched by Edit/Write this session, the last 8 Bash commands, the last thing Claude said. Written to `~/.mengram/checkpoints/<session_id>.json`, verbatim, no LLM involved.

**SessionStart** with `source: "compact"` or `"resume"` reads it back into context, above everything else, and says so in the terminal:

```
🧠 Mengram put the working state back after compaction: 3 files you edited,
your last request («fix the failing build…»), the last commands run.
The summary may have dropped it; this is exact.
```

Details that turned out to matter:

- Restore **once**. The file is consumed on restore, otherwise `/clear` in the same session replays old work.
- `--resume` can come with a new session id; fall back to the newest checkpoint from the same `cwd` (7-day cap).
- It must work with no account and through a cloud outage — the checkpoint never leaves the machine, so session start can't be the moment it fails.
- Tool results and `<system-reminder>` wrappers are not "the person's prompts"; filter them or the checkpoint fills with noise.
- The same handlers run under Codex — its `hooks.json` has the same shape; only the transcript format differs.

Honest limits: this restores *state*, not *rules*. If Claude ignored a CLAUDE.md instruction that was in context, another block in context is not a proven fix. It's for "what was I doing", not "what did I tell it".

```
pip install mengram-ai && mengram hook install
```

Code: github.com/alibaizhanov/mengram, `local/checkpoint.py`. Apache-2. Happy to be told what else should survive compaction.

---

## 3. r/cursor — flair: Discussion (post only if 1 and 2 draw comments)

**Title:** Cursor + Claude Code + Codex people: how do you keep one memory across the three? (what worked for us, and where Cursor is the odd one out)

**Body:**

Genuine question plus what we found. If you use Cursor alongside Claude Code or Codex, the same project lives in three heads: each tool keeps its own idea of your stack, your decisions, what broke last week. The most-upvoted request in Claude Code's history (5k+ votes) was portability of instructions between agents — that part is solved by AGENTS.md now. Memory of *decisions* isn't.

What we ended up with:

- Claude Code and Codex both have lifecycle hooks, so memory can be automatic there: recall on each prompt, save after each turn, and a checkpoint before compaction that puts the working state back after it.
- Cursor has no hooks. The only door is MCP, so in Cursor memory is on request — the agent calls `recall`/`context_for` when it decides to. It works, but it's the tool that has to remember to remember.

So the honest state is: automatic in two tools, on-demand in the third. Two questions for people here:

1. Is there a Cursor-side trigger I'm missing (rules file tricks, `.cursor/rules` with an "always call X first" instruction actually holding)?
2. Does anyone actually want cross-tool memory, or is per-tool context fine because you keep the task in one tool at a time?

Not linking anything unless asked; the mechanism is open source and I'll drop it in comments if it's useful.

---

## Posted 2026-09-15 (Almaty evening), all three at once on Ali's call

- r/codex — flair Workaround — https://www.reddit.com/r/codex/comments/1wh7gbg/codex_hooks_in_practice_precompact_sessionstart/
- r/ClaudeCode — flair Built with Claude — https://www.reddit.com/r/ClaudeCode/comments/1wh7i5m/what_compaction_actually_drops_and_a_precompact/
- r/cursor — flair Question / Discussion — https://www.reddit.com/r/cursor/comments/1wh7ji6/cursor_claude_code_codex_people_how_do_you_keep/

Check on 2026-09-16 and 2026-09-17: votes, comments, removals; answer every
comment with numbers; the r/cursor thread is a question, so reply first to
anyone who answers it.

## Correction 2026-09-15 22:4x — r/cursor
u/lgmarian: "Am I missing something? https://cursor.com/docs/hooks". He was right: Cursor has
hooks (sessionStart with additional_context, preCompact with transcript_path, stop,
afterAgentResponse; beforeSubmitPrompt can only allow/block). Replied with the concession and
edited the post body: original claim struck through, corrected paragraph, questions reworded.
Follow-up owed publicly: ship `mengram hook install --cursor` and report what preCompact's
transcript contains.

## r/cursor thread, 2026-09-15 late evening — replies
- MacaroonAntique (thoughtful: durable vs transcript, drift in Cursor): replied with "no data yet",
  the two-store model, today's 24/100 chatter numbers, salience-gate target, asked for a heuristic
  on catching "rejected approach" moments.
- Frosty_Teeth: "Ban memory, store everything in project docs." — not answered yet.
- locbuilds: shared decisions.md in repo root read at session start, each tool appends a line
  before stop; in Cursor pastes last lines into a new chat. — REPLIED: agreed, asked what goes
  stale / who prunes / do rejected approaches get in; noted sessionStart injection replaces the paste.
  Product implication (see memory mengram-tasks-agent-builders): a one-line "decision + why +
  rejected" at Stop instead of fact extraction from chatter; decisions.md as export and import.
Owed publicly: Cursor drift measurement (turns until first voluntary recall) within a week.

## Check 2026-09-16 00:40 (2h after posting)
r/codex 1.5K views, 2 comments (OnoSendaiCSVII: "this is gold… audit log for deterministic work
state"; one empty). r/ClaudeCode 355 views, Alex__0021: loses rejected decisions, "dropped X because Y"
in one line — replied (asked: hook prompts for the line, or drafts it for veto?). r/cursor 1.7K views,
11 comments; "ban memory, store in docs" has 6 upvotes; cornmacabre: process > memory (2 upvotes) —
replied (agree, memory only for tried-and-dropped); AbleShower2801: strip tool-specific leftovers from
the shared pack? — replied (one blob today; provenance + host filter queued). farhan-x1987: .ai folder.
Signal: three independent people asked for the "dropped X because Y" line (task item 10).

## r/codex, 2026-09-16 ~00:50 — reply to OnoSendaiCSVII
Replied: accepted "audit log for deterministic work state" as the better name; added one new fact from
reading the Codex source (its own compaction keeps prior user messages verbatim, ~20k-token cap, plus a
free-form handoff summary, and drops every assistant/tool item, so the checkpoint adds exactly the
assistant side); named "tried X, dropped it because Y" as the gap neither covers; explained the tolerant
JSONL reader (rollout format already changed once between versions).

## r/cursor, 2026-09-16 morning — five replies
- cornmacabre (errata.md living corrections log): conceded it is the better answer to "tried and dropped";
  offered the agent appending the errata line at stop; asked whether a tool writing into errata.md is acceptable.
- AbleShower2801 (provenance shape): metadata beside each fact (JSON column: tool, session, timestamp, workspace
  path), short source tag in the rendered line, host filter at injection time; his Mac-path-on-Linux case = the test.
- kryptonite84 (plans/docs per solution): honest baseline; breaks across tools/machines; keep it if it works.
- Frosty_Teeth ("ban memory", 7 upvotes): agreed for one repo/one team; the layer is for cross-tool and for
  what was tried and dropped.
- MacaroonAntique (asked for gate results): E1 numbers — recall@old 1.00 in 18 runs, junk 30d 0.38→0.21,
  90d 0.58→0.42; L remainder is true-but-unasked facts; next signal = recall history; rejected approaches
  still zero in extraction, the "one line at stop" heuristic came from this thread.
Owed publicly now: the recall-history demotion (E1b), provenance + host filter, Cursor drift numbers.

## 2026-09-16 ~14:30 — promise to AbleShower2801 (r/cursor) kept: 2.46.0
Provenance on every fact (source tool, session, cwd, os, when, recalled count), rendered as a tag in hook
recall; X-Mengram-Host filter keeps another host's paths and tool files out, counted in
facts_left_out_for_host. Verified on prod: a /Users/... path saved from Cursor on a Mac is left out of a
linux/claude-code search (left_out=1). PyPI 2.46.0 published. Reply owed in the r/cursor thread.
- 2026-09-16 ~14:45 AbleShower2801: REPLIED "Shipped: 2.46.0" — facts_meta, host tag, X-Mengram-Host filter,
  facts_left_out_for_host, verified on prod with his exact case. Promise closed.
- piekwerk (new, ~14:20): decisions.md pattern — append-only, dated, one line why, "supersedes #12 because X",
  rejected approaches get rows, weekly pruning by the agent, two-line wiring in CLAUDE.md/AGENTS.md; Cursor
  via rules; mid-task drift still on you. REPLIED ~14:55: steal "supersedes #12 because X", same rule
  built server-side today, agent writes the stop line / human prunes, scope exception; asked whether a tool
  may read+write their decisions.md in their format. Product implication: this IS task item 10
  (decisions.md as export/import + one line at Stop) with the supersede semantics we just built server-side.
