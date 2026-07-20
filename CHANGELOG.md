# Changelog

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
