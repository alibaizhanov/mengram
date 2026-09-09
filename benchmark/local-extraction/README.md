# Local-model extraction benchmark

The exact prompt Mengram uses to turn a Claude Code session into memory
(entities + facts, episodes, procedures), run against local Ollama models so
anyone can check JSON reliability and extraction quality on their own hardware.

Prompt: [`engine/extractor/conversation_extractor.py`](../../engine/extractor/conversation_extractor.py)
(`EXTRACTION_PROMPT`, JSON schema in `EXTRACTION_SCHEMA`). The cloud runs the same
prompt with a hosted model, so the numbers here are directly comparable.

## Run

```bash
pip install mengram-ai
ollama pull llama3.1:8b qwen2.5:14b gemma3:12b     # whatever you want to test
python bench.py --models llama3.1:8b,qwen2.5:14b,gemma3:12b --runs 3
```

Flags that change the outcome:

| flag | default | why it matters |
|---|---|---|
| `--num-ctx` | 16384 | Ollama's default context (~4k) is smaller than the prompt; without this, models silently truncate and return broken JSON |
| `--format json\|none` | json | whether to send Ollama's `format: json` grammar constraint; `none` lets you test if it hurts quality |
| `--think` | off | thinking models (qwen3, deepseek-r1) burn minutes and tokens in thinking; off by default |
| `--runs N` | 1 | medians over N runs; use ≥3 for anything you want to quote |
| `--transcript` | `sample-transcript.md` | your own session; `User:` / `Assistant:` paragraphs |
| `--dump-prompt FILE` | | write the rendered prompt to use with any other runner |

## What gets reported

- **valid JSON 1st try** — whether the raw model output parsed with `json.loads` before any repair.
  Mengram has fallback parsing, so a `no` here doesn't always mean lost data, but it does mean the model
  is unreliable.
- **entities / facts / episodes / procedures** — counts after Mengram's parser. The sample transcript
  contains, by hand count: 4 entities worth having (the user, the notify-svc project, Marcus, Zed),
  ~10 facts, 2 episodes (the failed deploy + diagnosis, the constraint decisions), and 1 procedure
  (the 6-step deploy checklist). A model that returns 0 procedures missed the most valuable part.
- **seconds** — wall time per session, including any retry call.

## Reference numbers

M1 Pro, 16 GB, Ollama 0.32.5, `num_ctx` 16k, `format: json`, think off, two real 5–6k-char sessions,
one run each. See [`growth/bench-ollama-2026-09-08.md`](../../growth/bench-ollama-2026-09-08.md).

| model | seconds | valid JSON | entities | episodes | procedures |
|---|---|---|---|---|---|
| llama3.1:8b | 30–32 | yes | 2–3 | 0–2 | 0 |
| qwen2.5:7b | 31–55 | yes | 1–4 | 0–2 | 0–2 |
| qwen3:8b | 47–104 | yes | 2–3 | 1–4 | 0–2 |
| qwen3:4b | 42–52 | yes | 4–6 | 1–2 | 1 |
| claude-sonnet-5 (cloud) | 52 | yes | 8 | 4 | 2 |

If you run this on other hardware or models, open an issue or PR with the table. Larger models
(14B+) and the gemma family are the gap we most want filled.
