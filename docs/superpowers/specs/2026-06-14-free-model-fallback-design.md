# Design: Curated Free-Model List + Fallback Chain for Self-Hosted LLM Config

## Problem

Self-hosted mengram (`~/.mengram/config.yaml`) points at a single free OpenRouter model
(currently `openrouter/owl-alpha`). When OpenRouter retires/renames a free model
(as happened with `qwen/qwen3.6-plus:free`), the configured model silently stops
working: `LLMClient.complete()` raises, the structured-output fallback in
`conversation_extractor.py` also raises, and extraction quietly fails with no
visible signal to the user.

`openrouter/free` (random router across ~24 free models) was considered as a fix
but rejected: non-deterministic model selection, some pooled models are too small
(<7B params) and choke on mengram's extraction prompt (see
[alibaizhanov/mengram#7](https://github.com/alibaizhanov/mengram/issues/7)), and
structured-output support varies per model, causing intermittent silent fallback.

## Goals

- Maintain a curated, weighted list of suitable free OpenRouter models, refreshed
  automatically.
- mengram tries models in weight order, falling back to the next on failure.
- If all models fail, mengram logs a clear error but keeps running (extraction
  for that turn is skipped, not a hard crash).
- Local caching to avoid hammering the list URL or OpenRouter.
- Backward compatible with existing `model:` config field.

## Part A — `mengram-model-list` repo (new)

A new standalone GitHub repo containing a daily GitHub Actions workflow that
generates `models.json`.

### Workflow (daily cron + `workflow_dispatch`)

Runs `uv run generate_models.py`:

1. **Fetch candidates**: `GET https://openrouter.ai/api/v1/models`, keep only
   `:free` variant IDs.
2. **Filter by suitability**: derive minimum thresholds from mengram's actual
   needs — extraction prompt size (`EXTRACTION_PROMPT_V2` + `EXTRACTION_SCHEMA`),
   expected output size — add a 10-15% buffer on top. Drop models below the
   `context_length` / `max_completion_tokens` thresholds, or that don't advertise
   structured-output / JSON-schema support in `supported_parameters`. If the
   buffer would shrink the candidate pool too aggressively, relax it rather than
   end up with an empty list.
3. **Probe live**: for each surviving candidate, send one minimal completion
   request. Append `{timestamp, success, latency_ms}` to
   `history/<model_id>.jsonl`, trimmed to a rolling 30-day window.
4. **Score**: from history, compute `uptime` (success rate over the window,
   neutral default if sample size too small) and `latency_p50`. Weighted score,
   in priority order: **uptime > latency > context/params** (capability is
   already gated by step 2, so context/params is just a minor tiebreaker).
5. **Write `models.json`**, sorted descending by score:
   ```json
   {
     "generated_at": "2026-06-14T00:00:00Z",
     "schema_version": 1,
     "models": [
       {
         "id": "openrouter/owl-alpha",
         "score": 0.94,
         "context_length": 1000000,
         "max_output_tokens": 32000,
         "uptime": 0.99,
         "latency_ms": 850
       }
     ]
   }
   ```
6. Commit `models.json` + updated `history/*.jsonl` if changed.

Consumed via raw URL:
`https://raw.githubusercontent.com/<user>/mengram-model-list/main/models.json`

## Part B — mengram-upstream changes

### `~/.mengram/config.yaml` (new optional field, backward compatible)

```yaml
llm:
  provider: openai
  openai:
    api_key: sk-or-v1-...
    model: openrouter/owl-alpha       # still supported: used as final fallback
    model_list_url: https://raw.githubusercontent.com/<user>/mengram-model-list/main/models.json
```

If `model_list_url` is absent, behavior is unchanged (single `model`, no fallback
chain).

### New module: `engine/extractor/model_source.py`

`get_model_candidates(config: dict) -> list[str]`

- Cache file: `~/.mengram/model-cache.json` —
  `{url, fetched_at, content_hash, models: [...]}`.
- If `now - fetched_at < 6h`: return cached `models` list directly, no network
  call.
- Else: conditional fetch (compare content hash). Unchanged → bump `fetched_at`,
  keep cached models. Changed → parse new `models.json`, update cache.
- Fetch fails but cache exists → use stale cache, log warning.
- Fetch fails and no cache → fall back to `[config model]` only (if set).
- Return value: model IDs from `models.json` in score order, with the static
  `model:` (if configured) appended last as the ultimate fallback. Dedup if it's
  already in the list.

### New class: `FallbackOpenAIClient` (in `llm_client.py`)

- Constructed with an ordered list of model IDs (from `get_model_candidates`)
  plus shared `api_key`.
- `complete()` / `chat()`: try each model in order via an underlying
  `OpenAIClient`. On exception, `_logger.warning("model %s failed: %s", model, e)`
  and try the next. On first success, return immediately.
- If every model fails: log an error to stderr summarizing all attempted models
  and their errors, then raise `AllModelsFailedError`.

### `create_llm_client` (`llm_client.py`)

For `provider == "openai"`, if `model_list_url` is configured, return
`FallbackOpenAIClient` constructed from `get_model_candidates(config)`. Otherwise
unchanged (`OpenAIClient` with single model).

### Call site: `engine/brain.py:123` (`self.extractor.extract(conversation)`)

Wrap in `try/except AllModelsFailedError`: log warning, skip extraction for this
turn (treat as empty `ExtractionResult`), continue normally. This is the
self-hosted local-mode path — `cloud/api.py` call sites are out of scope (cloud
SaaS uses its own model config, not `~/.mengram/config.yaml`).

## Out of scope

- Cloud (`cloud/api.py`) model selection — separate config/infra, not addressed
  here.
- Paid/non-free OpenRouter models in the curated list.
- UI/notification beyond stderr logging.
