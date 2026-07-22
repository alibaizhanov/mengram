# Changelog

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
