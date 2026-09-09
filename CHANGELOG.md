# Changelog

## 2.38.2 — 2026-09-09

### Fixed
- **The run-outcome hook recorded nothing.** It looked for an `exit_code` in
  the PostToolUse payload, following the documented schema. A real Claude Code
  sends no exit code for Bash: the payload carries `stdout`, `stderr`,
  `interrupted`, `isImage` and `noOutputExpected`, and — measured with a
  logging hook — the event fires *only after a command that succeeded*. A
  failing command produces no PostToolUse at all. So the arrival of the event
  is the signal, and 2.38.0 treated it as "no verdict" and wrote nothing.

### Known limit
- Failures cannot be seen from this hook, because it does not fire for them.
  The loop records successes and never guesses a failure; a failure still has
  to come from `mengram local feedback`, or from a host that does send an exit
  code. Reading them out of the session transcript is the obvious next step.

## 2.38.1 — 2026-09-09

### Fixed
- **`hook install` writes a command Claude Code can actually run.** It wrote a
  bare `mengram`, which resolves only when the install happened to land on
  PATH. A `pip install --user` on macOS does not, and Claude Code runs hooks in
  a shell that never reads your profile, so the command was not found. Every
  hook is built to fail silently, so nothing was ever printed: auto-save,
  auto-recall and the session profile simply never ran. Found on a machine
  where they had been dead since May. The install now writes the full path of
  the program that is running, and verifies it can be launched before it
  finishes.
- **`hook status` runs the hooks instead of trusting the settings file.** It
  used to look for the command text in `settings.json` and print "installed".
  It now executes each one the way Claude Code would, through a plain shell,
  and names any that cannot run along with the shell's own error.
- **`doctor` checks the hooks before the cloud.** A round-trip against the API
  said "OK" while every hook was dead. Hooks that are installed and cannot run
  now fail `doctor` with the offending command.

## 2.38.0 — 2026-09-09

### Added
- **Run outcomes are recorded.** A new `PostToolUse` hook, `mengram auto-outcome`,
  recognises a Bash command as one step of a learned workflow and writes that
  command's exit code against that step. Until now nothing produced a track
  record at all: `procedure_feedback` existed only as a command a human had to
  type, so procedures stayed `untested` forever and the policy gate had no
  evidence to judge by. `mengram hook install` installs it; folder mode only
  for now (the cloud endpoint records whole runs, and a single command written
  there would inflate the record).
- `mengram local` gained `step_outcome` underneath: one command credits one
  step, never the whole workflow.
- A workflow whose steps have been watched working no longer reads `untested`.
  The weakest watched step sets the number; steps nobody measured are ignored
  rather than counted as failures.

### Changed
- **The policy gate asks far less, and about the right things.** It used to
  fire when a command shared a single word with a procedure. Replaying 3456
  real Bash commands against a real 37-procedure folder, that was 795
  confirmation prompts — one command in six — on matches like `rm -rf dist
  build` against "Full Mengram codebase audit". A command must now *be* a
  step: the same tool named and half the step's words covered, or nearly all
  of them when the step names no tool. The same replay now fires 8 times.
  Heredoc bodies and huge inline scripts are stripped before matching, so a
  `cat >> notes.md` whose text mentions `pip install` no longer matches an
  install step.

## 2.37.2 — 2026-09-07

### Fixed
- `import claude-code` saves the imported-sessions list after every session,
  not at the end: a killed or timed-out import resumes instead of extracting
  everything twice (episodes are appended, so a re-run duplicated them).
- The Anthropic client has a 5-minute request timeout and one retry (the SDK
  default was 10 minutes × 3); one session that never answered no longer
  stalls the whole import. Seen on a real import with claude-sonnet-5.
- The Ollama client now sends a 16k context window (`num_ctx`, overridable
  in the folder config), disables model "thinking", and has a 10-minute
  timeout. Ollama's default context is ~4k, which silently truncated the
  ~3.6k-token extraction prompt plus the transcript.

## 2.37.1 — 2026-09-07

### Fixed
- Anthropic extraction failed on `anthropic>=1.0` with
  `Messages.create() got an unexpected keyword argument 'temperature'` — the
  SDK dropped the parameter. Not sent any more. Found on the first real
  `mengram import claude-code --memory` run.
- Claude 5 models return a thinking block first; `content[0].text` raised
  `AttributeError: 'ThinkingBlock' object has no attribute 'text'` and every
  extraction fell back and failed. The text blocks are read now.
- Thinking counts against `max_tokens`; at 4096 the visible JSON was cut
  mid-object ("Failed to parse JSON from LLM"). The Anthropic client now
  asks for 16384.
- A folder configured for a provider whose SDK is not installed now says
  `pip install 'mengram-ai[anthropic]'` (or `[openai]`) instead of a
  traceback, from `local add`, `local feedback` and `import claude-code`.

## 2.37.0 — 2026-09-07

### Added
- **Cloud: `procedures.last_succeeded`** (migration v2.23, no backfill). Set
  only by a successful `PATCH /v1/procedures/{id}/feedback`, never by
  retrieval. Returned by `/v1/procedures`, procedure search, the connector's
  `list_procedures`, and written into `mengram export markdown` as
  `last_succeeded` + `**Last success**`, so the cloud and the folder now carry
  the same staleness signal.

## 2.36.0 — 2026-09-07

### Added
- **`last_succeeded`** — when a workflow last worked. Set only when a
  successful run is recorded (`mengram local feedback --success`), never by
  a read, so "unverified for N days" is a fact about runs. Shown by
  `mengram local procedures` (with days ago), on the memory map (flagged
  after 30 days), and in the policy gate's plan. Needs memfmt 0.5.2, which
  adds the field to the format.

## 2.35.0 — 2026-09-07

### Added
- **`mengram local map`** — one self-contained HTML page of what a memory
  folder holds (`memory-map.html` in the folder, or `--out`; `--open` for the
  browser). Three views: who you are (entities by type, facts, relations),
  what happened (episodes with outcomes), what your agent learned (each
  workflow as a step chain with the per-step record, its revisions with the
  belief that broke, and the quarantine). Rendered from the Markdown files;
  nothing is fetched or sent. `mengram import claude-code --memory` now ends
  by writing it, so an import answers "what do you know about me?" with a
  page instead of a count.

## 2.34.0 — 2026-09-07

### Added
- **`mengram import claude-code --memory DIR`** — the cold start for a memory
  folder. Your local Claude Code sessions (`~/.claude/projects`) are parsed,
  redacted for secrets, and extracted with the folder's own model; the run ends
  with what the folder now holds and the workflows it learned, with their
  record. The folder keeps its own imported-sessions list
  (`.mengram/claude-code-imported.json`), separate from the cloud account's, so
  the two never skip each other's sessions. `MENGRAM_MEMORY_DIR` works too.

### Changed
- `cloud/api.py` and `cloud/store.py` were split by domain (#107): the public
  site and its content live in `cloud/site.py` + `cloud/content/`, billing in
  `cloud/billing.py`, and `CloudStore` is assembled from mixins in
  `cloud/store/`. No behaviour change; every route, page and method verified
  identical.

## 2.33.0 — 2026-09-04

### Added
- **Local mode — memory in a folder, no account.** `mengram local init|add|
  search|procedures|feedback|stat|quarantine` on a memfmt tree you own; the
  four Claude Code hooks and the policy gate run against it with
  `--memory DIR` (or `MENGRAM_MEMORY_DIR`); `mengram server --memory DIR`
  serves the connector's four tools plus `procedure_feedback` over stdio.
  Same procedures-with-outcomes as the cloud: versions, per-step counts,
  `last_failure`, write-time dedup of near-duplicate names, failure →
  revision with the violated assumption, and the cross-procedure regression
  gate (a fix that would break another workflow is quarantined to
  `.mengram/quarantine.json`). Extraction and revision use the model you
  configure (Anthropic, OpenAI, Ollama); everything else needs none.
- `cloud/procedure_match.py`: the pure procedure helpers, shared by the cloud
  store and the folder store.

### Changed
- `memfmt>=0.5,<0.6` is now a dependency.

## 2.32.0 — 2026-09-04

### Added
- `mengram auto-policy` — a Claude Code PreToolUse hook (installed by
  `mengram hook install`, matcher `Bash`). A command that matches a learned
  workflow with a weak record (`untested`, inherited `N% expected`, or below
  `MENGRAM_POLICY_MIN_RELIABLE`, default 70) is answered with `ask`: the user
  sees why, Claude gets the steps on record. Proven workflows stay silent; the
  hook never denies. Offline mode via `MENGRAM_MEMORY_DIR` (memfmt folder).
- Extraction no longer mints a near-duplicate procedure. A freshly extracted
  workflow is compared (normalised name tokens + step tokens) against the
  user's current procedures: if it is the same workflow under a slightly
  different name and the existing one has never been run, the existing one
  is refreshed in place; if the existing one has a record, it is kept
  untouched. One user had reached 450 current procedures this way.
- Markdown export writes `last_failure` / `last_failed` on a procedure (memfmt
  0.5): the violated assumption recorded by the most recent failure revision,
  and its date. The counts say how often a workflow fails; this says what to
  look at first. The policy gate shows it in the plan it hands Claude.
- `/v1/procedures/search` results now carry `reliability` and `last_used`
  for both vector and text search (previously only text search had
  `reliability`; neither had `last_used`).

## 2.31.0 — 2026-09-04

### Fixed
- `get_feed` MCP tool rendered every entry as `- **?** entity`: the formatter
  read `action`/`detail` while `/v1/feed` returns `entity`/`fact`/`created_at`.
  Now renders `entity — fact (timestamp)`. Fixes #70. Thanks @Moviw (#99) and
  @smoochy for the report.
- Facts that are re-asserted after being archived or superseded revive instead
  of staying hidden; a procedure can no longer be left with no current version
  after a revision (#95).
- The MCP streamable-HTTP endpoints no longer answer GET (long-poll hangs were
  starving the workers) (#94).
- `HEAD` is answered on all public pages.
- Privacy delete removes everything the user asked to delete; writes are scoped
  by `sub_user_id` instead of merging into `default`.
- OAuth redirect targets are validated before a code is issued; cron reacquires
  its advisory lock each tick; dry-run extractions are metered.

### Added
- `mengram export markdown|obsidian` and `GET /v1/export`: the whole memory as a
  plain-Markdown tree in the published memfmt format (frontmatter is the source
  of truth; `[[wikilinks]]` for Obsidian).
- Per-step track record on procedures: step counters survive a revision only
  for steps whose text is unchanged; the failing step is read out of the failure
  text and handed to the agent (#92).
- Reliability is reported in words (`untested`, `N% expected`, `N% reliable`)
  with the same smoothing as memfmt, so one record means one thing everywhere.
- Slim `/mcp/connector` surface with OAuth 2.1 + PKCE for the Claude connector.

### Changed
- `mcp` pinned to `1.28.1` (unpinned builds drifted to an incompatible API).
- Dashboard and site moved to the light theme; new "m" monogram mark.

## 2.28.0 – 2.30.0 — 2026-07-27/28

- `mengram setup`: one-command onboarding (signup → Claude Code hooks → MCP
  configs for detected editors → history import → doctor).
- Weekly memory report, auto-shown once a week at session start.

## 2.27.1 — 2026-07-22

### Fixed
- `mengram server --cloud` now resolves the API key from
  `~/.mengram/config.json` when the env var is unset — same order as the
  hooks (MCP hosts often spawn without the user's shell profile, which
  made the server exit with 'Set MENGRAM_API_KEY' on otherwise-configured
  machines, especially Windows).


## 2.27.0 — 2026-07-21

### Added
- `mengram try` — zero-account, local-only preview of what memory would
  know: scans your Claude Code history on-device (nothing uploaded) and
  shows projects, stack, and detected workflow patterns. The first taste
  of Mengram now comes before signup, not after.


## 2.26.1 — 2026-07-21

### Improved
- `mengram import claude-code` now shows what memory actually learned after
  extraction (entities/facts/episodes/workflows + up to 3 learned workflow
  names) instead of a bare counter, and reports honestly when sessions were
  deduplicated against existing memory.


## 2.26.0 — 2026-07-20

### Added
- `mengram import claude-code` — import your local Claude Code session
  transcripts (`~/.claude/projects`) into memory. Kills the cold-start
  problem: memory knows your projects from minute one. Secrets (API keys,
  tokens, JWTs) are redacted client-side before upload; re-runs skip
  already-imported sessions (`--reimport` to force); `--last N`,
  `--project <substring>`, `--yes` flags.


## 2.25.4 — 2026-07-20

### Fixed
- `auto-recall`, `auto-context`, and `auto-save` Claude Code hooks now resolve
  the API key and base URL from `~/.mengram/config.json` as a fallback when
  `MENGRAM_API_KEY`/`MENGRAM_URL` env vars are unset (fixes self-hosted setups
  on Windows, where `setup --key` only persists to config.json).

### Added
- `--verbose` flag for `auto-recall`, `auto-context`, and `auto-save` hooks —
  emits a one-line `[mengram:<hook>] <status>` marker via `systemMessage` so
  hook activity is visible in Claude Code. Off by default.
