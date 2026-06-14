# Changelog

## 2026-06-14

### Fixed
- `auto-recall`, `auto-context`, and `auto-save` Claude Code hooks now resolve
  the API key and base URL from `~/.mengram/config.json` as a fallback when
  `MENGRAM_API_KEY`/`MENGRAM_URL` env vars are unset (fixes self-hosted setups
  on Windows, where `setup --key` only persists to config.json).

### Added
- `--verbose` flag for `auto-recall`, `auto-context`, and `auto-save` hooks —
  emits a one-line `[mengram:<hook>] <status>` marker via `systemMessage` so
  hook activity is visible in Claude Code. Off by default.
