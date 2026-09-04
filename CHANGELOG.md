# Changelog

## Unreleased

### Added
- `mengram auto-policy` — a Claude Code PreToolUse hook (installed by
  `mengram hook install`, matcher `Bash`). A command that matches a learned
  workflow with a weak record (`untested`, inherited `N% expected`, or below
  `MENGRAM_POLICY_MIN_RELIABLE`, default 70) is answered with `ask`: the user
  sees why, Claude gets the steps on record. Proven workflows stay silent; the
  hook never denies. Offline mode via `MENGRAM_MEMORY_DIR` (memfmt folder).
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
