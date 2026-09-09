| model | session | transcript chars | seconds | valid JSON 1st try | entities | episodes | procedures |
|---|---|---|---|---|---|---|---|
| qwen3:8b | 1 | 5859 | 47.2 | yes | 2 | 1 | 0 |
| qwen3:8b | 2 | 4676 | 104.2 | yes | 3 | 4 | 2 |
| llama3.1:8b | 1 | 5859 | 31.7 | yes | 2 | 0 | 0 |
| llama3.1:8b | 2 | 4676 | 29.7 | yes | 3 | 2 | 0 |
| qwen2.5:7b | 1 | 5859 | 31.0 | yes | 1 | 0 | 0 |
| qwen2.5:7b | 2 | 4676 | 54.7 | yes | 4 | 2 | 2 |
| qwen3:4b | 1 | 5859 | 51.6 | yes | 6 | 1 | 1 |
| qwen3:4b | 2 | 4676 | 41.9 | yes | 4 | 2 | 1 |

## Sample transcript (benchmark/local-extraction/sample-transcript.md, 3984 chars), 2026-09-08, n=1

| model | format | seconds | valid JSON 1st try | entities | facts | episodes | procedures |
|---|---|---|---|---|---|---|---|
| qwen3:4b | json | 49.5 | yes | 5 | 10 | 1 | 1 |
| llama3.1:8b | json | 49.2 | yes | 2 | 5 | 2 | 1 |
| qwen3:4b | none | 231.3 | no (27k chars of prose) | 0 | 0 | 0 | 0 |
| llama3.1:8b | none | 55.9 | yes | 3 | 10 | 2 | 1 |
