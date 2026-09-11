# Slim extraction evaluation — 2026-09-08

**Not ready for merge: the prompt-only candidate does not clear the small-model bar.**
Related: #7. No README model-support claim has been changed.

## Scope and method

Production changes are a new `EXTRACTION_PROMPT_SLIM` constant and an explicit
lookup entry. All five output collections remain; v1/v2, the schema, parsing,
provider clients and local configuration are unchanged.

The current slim template is 571 `cl100k_base` tokens when rendered with empty
conversation/context. This is not a total request budget or an Ollama tokenizer
measurement: conversation, existing context, provider framing, schema handling
and generation capacity must be accounted for separately.

The original ten cases retain their expectations. Two new ten-message cases
check example contamination and complete JSON with a six-step workflow and all
five output types. Their raw responses must validate against `EXTRACTION_SCHEMA`;
parser recovery cannot turn truncated or malformed JSON into a pass. Forbidden
terms are checked across all extracted fields, including relations. Empty-output
cases now count episodes and relations too. These stricter checks apply to both
v1 and slim. A pass count is not proof of losslessness outside this fixture set.

## Final-candidate results

One full run per row, using the same final candidate and cases. Ollama baseline
and slim share model weights, a 4096-token context, 2048-token output cap,
temperature 0 and seed 7. These are harness settings via local model aliases,
not a change to the production Ollama client. Claude uses the existing
Anthropic client (16384-token output cap, no temperature override).

| Model | Prompt | Original ten | New two | Total |
| --- | --- | ---: | ---: | ---: |
| Claude Sonnet 4.6 | slim | 10/10 | 2/2 | 12/12 |
| Claude Sonnet 5 | slim | 9/10 | 2/2 | 11/12 |
| phi4-mini:3.8b Q4_K_M | v1 | 6/10 | 0/2 | 6/12 |
| phi4-mini:3.8b Q4_K_M | slim | 7/10 | 0/2 | 7/12 |
| llama3.1:8b Q4_K_M | v1 | 7/10 | 0/2 | 7/12 |
| llama3.1:8b Q4_K_M | slim | 4/10 | 0/2 | 4/12 |

Sonnet 4.6 passes the large-model regression gate. Sonnet 5 produced malformed
JSON for the existing workflow case; that failure is retained, not rerun away.
Neither target small model passes either new case. phi4 improves its overall
count slightly, while llama3.1 regresses. These results do **not** justify
advertising either small model as supported by slim.

Small-model failures include wrong nested field types, missing required fields,
incomplete workflow retention, assistant/example contamination and lost facts.
The new complete-JSON case detects malformed output, but these runs do not
establish a general reduction in output truncation frequency. There is no
unconditional claim that a shorter prompt solves the capacity/quality problem.

## Reproduce

Install the project's `dev` and chosen provider extras (the eval schema check
uses `jsonschema`, a dev-only dependency). Run from the repository root:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_extraction_slim.py tests/test_llm_client_anthropic.py -q
LLM_PROVIDER=anthropic LLM_MODEL=claude-sonnet-4-6 python3 evals/run_extraction_evals.py --prompt-version slim --verbose
```

Seven offline checks passed (four slim/eval checks and three existing client
checks). They verify wiring and gate behavior, not model quality.

For each of `phi4-mini:3.8b` and `llama3.1:8b`, pull the model and create a local
eval alias from a Modelfile with this content (substitute the model in `FROM`):

```text
FROM phi4-mini:3.8b
PARAMETER num_ctx 4096
PARAMETER num_predict 2048
PARAMETER temperature 0
PARAMETER seed 7
```

```sh
ollama create mengram-eval-phi4-4k -f /path/to/Modelfile
LLM_PROVIDER=ollama LLM_MODEL=mengram-eval-phi4-4k python3 evals/run_extraction_evals.py --prompt-version v1 --verbose
LLM_PROVIDER=ollama LLM_MODEL=mengram-eval-phi4-4k python3 evals/run_extraction_evals.py --prompt-version slim --verbose
```

Repeat with a separate llama alias. The existing Ollama client requests JSON
mode, not full JSON-schema enforcement; slim therefore retains a compact field
contract. `OLLAMA_BASE_URL` selects the eval endpoint (default localhost:11434).
The default eval prompt remains v2. `--case ID` selects one case.

Recorded Ollama base-model digests:

- phi4-mini:3.8b: `78fad5d182a7c33065e153a5f8ba210754207ba9d91973f57dffa7f487363753`
- llama3.1:8b: `46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e`

Earlier development runs were not acceptance evidence: the initial OpenAI
attempt returned 401, an unavailable Claude identifier returned 404, and earlier
prompt candidates failed multiple cases. Those failures led to the current
field contract and stricter gate; no reduced-output variant was introduced.
