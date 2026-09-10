# format: json vs строгая JSON-схема в Ollama (2026-09-10)

Гипотеза Sweet-Transition-787 (r/ollama): передавать Ollama не общий `format: "json"`, а саму JSON-схему,
с `procedures` в required — тогда 8B-модели перестают пропускать ключ.

Один и тот же промпт (EXTRACTION_PROMPT) и один транскрипт (benchmark/local-extraction/sample-transcript.md,
3984 символа), num_ctx 16384, think off, temperature 0.2, M1 Pro 16 ГБ, n=1 на ячейку.

| model | mode | s | valid JSON | entities | facts | episodes | procedures |
|---|---|---|---|---|---|---|---|
| llama3.1:8b | format: json | 50.7 | yes | 2 | 6 | 2 | 1 |
| llama3.1:8b | strict schema | 28.2 | yes | 2 | 5 | 2 | 1 |
| qwen2.5:7b | format: json | 73.4 | yes | 6 | 11 | 2 | 1 |
| qwen2.5:7b | strict schema | 70.4 | yes | 8 | 16 | 2 | 1 |
| qwen3:4b | format: json | 45.5 | yes | 4 | 9 | 1 | 1 |
| qwen3:4b | strict schema | 45.3 | yes | 5 | 17 | 1 | 1 |

Вывод: схема не хуже нигде и лучше в двух из трёх по богатству (факты 11→16 и 9→17), плюс llama3.1
ускорилась почти вдвое. Пропуска ключа `procedures` на этом транскрипте не было ни в одном режиме,
так что конкретная механика из совета здесь не воспроизвелась — выигрыш в другом.
Схема берётся как `EXTRACTION_SCHEMA["json_schema"]["schema"]`.
