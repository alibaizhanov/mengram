# Spec — Local mode: memory that lives in a folder

**Status:** in progress · decided 2026-09-04 (Ali: «открывай ядро, делай локальную версию»)
**Why:** in this category users arrive through an open, local tool that installs
without an account (claude-mem, Mnemosyne), not through a hosted SaaS from one
person. Hermes refused the cloud MCP for exactly that reason. The repo is already
Apache-2.0 including the cloud; what is missing is a local mode that is equal to
the cloud on the one thing nobody else has — procedures with outcomes — and
installs in one line with no key to Mengram.

## What "local" means

- **The folder is the memory.** A [memfmt](https://github.com/alibaizhanov/memfmt)
  tree (`entities/`, `episodes/`, `procedures/`, `MEMORY.md`) is the source of
  truth. Git diffs it, Obsidian draws it, `memfmt validate` checks it. No SQLite
  of record, no server, no account.
- **Bring your own model.** Extraction and failure-revision need an LLM: an
  Anthropic or OpenAI key the user already has, or Ollama (8B+, 8K+ context).
  Everything else — search, procedures, feedback, the policy gate — runs with
  no model and no network.
- **Same differentiator as the cloud.** Versioned procedures, `success_count` /
  `fail_count` per version and per step, smoothed reliability, `last_failure`,
  the failure → revision loop with the violated assumption, the cross-procedure
  regression gate (quarantine instead of silent promotion), write-time dedup of
  near-duplicate names, and the Claude Code policy gate.
- **The cloud is sync and history, not a gate.** Nothing local expires or asks
  for a key. The cloud adds cross-machine sync, embeddings, background
  extraction from sessions, and the dashboard.

## What exists and what is reused

| Piece | Reused from | Notes |
|---|---|---|
| Extraction (entities/facts/relations/knowledge/episodes/procedures) | `engine/extractor` | shared with the cloud already; `create_llm_client` supports anthropic/openai/ollama |
| Format | `memfmt` (PyPI, pinned `>=0.5,<0.6`) | model + parse + serialise + `validate` |
| Reliability words, per-step records | `cloud/reliability.py` | pure |
| Near-duplicate procedure names | `cloud/procedure_match.py` (moved out of `store.py`) | pure |
| Regression gate | `cloud/regression_gate.py` | pure |
| Failure → revision prompt | `cloud/evolution.py` (`EVOLVE_ON_FAILURE_PROMPT`) | pure string |
| Policy gate | `cloud/policy.py` | already reads a memfmt folder |

The old vault engine (`engine/brain.py`, `.procedures.json`, sentence-transformers)
is **not** extended. It stays for existing users of `mengram init`; the new path
does not depend on it and does not require `sentence-transformers`.

## Surface

```
mengram local init [DIR]                  # creates DIR (default ./memory), writes config
mengram local add "text" | --stdin        # extract with your model, write files
mengram local search "query"              # facts, events, workflows — word overlap, no model
mengram local procedures [query]          # with reliability, last failure
mengram local feedback NAME --success | --failure [--step N] [--context "..."]
mengram local stat                        # delegates to memfmt stat + quarantine count
mengram server --memory DIR               # MCP over stdio: remember/recall/list_procedures/procedure_feedback/context_for
mengram hook install                      # with MENGRAM_MEMORY_DIR set, all four hooks run locally, no key
```

Config: `DIR/.mengram/config.json` — `{"llm": {"provider": "anthropic|openai|ollama", ...}}`.
Env `MENGRAM_MEMORY_DIR` selects local mode for hooks and the MCP server.

## Steps (each its own PR)

1. **Store** — `local/store.py`: load/save a memfmt tree; `add_extraction` (entity
   merge, fact dedup, episode append, procedure created/refreshed/kept);
   `search`, `recall`, `procedures`, `procedure_feedback` (per-step outcomes),
   `profile`, `stats`. Pure helpers moved to `cloud/procedure_match.py`. Tests
   hermetic on a tmp folder.
2. **Failure → revision** — `local/evolve.py`: on `--failure --context`, call the
   model with the shared prompt, run `find_regressions` against the other
   procedures, promote (new version, `Revision` with the retiring record,
   `last_failure`/`last_failed`) or quarantine to `DIR/.mengram/quarantine.json`.
   A version with no successes is revised in place, as in the cloud (PR #89).
3. **CLI + hooks** — `mengram local …`; `auto-save` / `auto-recall` /
   `auto-context` / `auto-policy` use the store when `MENGRAM_MEMORY_DIR` is set.
4. **MCP server** — `api/local_mcp_server.py`, stdio, the connector's 4-tool
   surface plus `procedure_feedback`.
5. **Docs + release** — README "Local mode: no account", docs page, 2.33.0,
   post in the threads that asked for it.

## Not in v1

Embeddings locally (word overlap only, as memfmt `context`), sync between
machines, multiple users per folder, reflection/curator agents, deleting or
renaming files that a rename left behind, staleness (`last_succeeded` — needs
its own field, same as the cloud).
