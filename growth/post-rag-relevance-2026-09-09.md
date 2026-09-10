# r/Rag — пост про «молчание» в векторном поиске — ОПУБЛИКОВАН 2026-09-09
https://www.reddit.com/r/Rag/comments/1wbm46c/ (флер Discussion, обязателен в этом сабе)

Статистика аккаунта на момент выбора площадки (100 постов):
r/AI_Agents 12 постов avg 3,7 / 11,4 коммента | r/ClaudeCode 9 постов avg 0,6 (мертво, не постить)
r/mcp 7 avg 2,3 | r/LangChain 6 avg 5,3 | r/LLMDevs 4 avg 5,3 | r/Rag 3 avg 14,3 лучший 31 — ВЫБРАН


Выбор площадки по данным аккаунта: r/Rag — 3 поста, средний счёт 14,3, лучший 31, последний 16 дней назад.
Жанр совпадает с их сработавшим постом («мониторинг врал, потому что две метрики лежали в одной колонке»).
Из таблицы вычищены имя работодателя и названия внутренних сервисов.

**Title:** Vector search has no way to say "nothing here is relevant" — I measured what that costs

**Body:**

I run a memory layer that injects relevant past context into an agent's prompt automatically, every turn. Yesterday I stopped assuming it worked and looked at what it actually injected, across seven consecutive prompts of a real working session.

| prompt | what got injected | related? |
|---|---|---|
| "check reddit" | a websocket note, a competitor, a GitHub repo | no |
| "why specifically with claude code" | Claude Desktop, a person, an internal service | partly |
| "what if they don't have claude code" | Claude Desktop, Mem0, an internal service | partly |
| "what is the value of our product" | an internal service, Redis, another project of mine | no |
| "restart it and check" | Spring Boot, a former employer, another project | no |
| "so now?" | sentence-transformers, a former employer, a person | no |
| "what do you mean, relevance" | SQLite, another project, a GitHub repo | no |

Five of seven had nothing to do with what was asked. A question about hook payloads came back with facts about a Java backend at a bank, stored in February for a completely different project.

The cause is not the ranking. It is that "nearest" is defined for every query, so the search always has an answer. Silence is not in its output set. There was a floor — `min_score = 0.2` cosine — but 0.2 is noise. Two unrelated sentences clear it routinely, because they are both sentences.

The same system has a plain-files mode that retrieves by word overlap, and it never does this: zero overlap means nothing is injected. That property was lost the moment retrieval moved to embeddings, and nobody noticed, because this failure looks like a feature. There is always something in the context.

The fix only removes, never adds: before injecting, require the memory to share at least one content word with the prompt. I already had exactly that guard in another part of the system, where a learned workflow has to share a word with a shell command before it is allowed to interrupt the user with a confirmation. It never occurred to me that retrieval needed the same thing.

Two mistakes I made while fixing it, both instructive:

1. My first version passed everything through when the prompt had no content words at all, reasoning that if you cannot read the query you should not silence recall. Exactly backwards. The shortest prompts are where the search has least to go on, so the "safe" default left the worst cases untouched. "so now?" pulled three entities out of the store as confidently as a detailed question would.

2. I reused a tokenizer from elsewhere in the codebase that only matched ASCII letters. Plenty of my prompts are not in English. It found zero words in them, which would have silenced recall completely instead of filtering it — a much worse bug, and one that ships silently.

After the change: silent on 5 of 7, and the 2 that kept anything kept only the entity the question was actually about.

I did not touch the 0.2 floor. Picking a real number needs the actual score distribution from production, which is a separate measurement I have not earned yet.

So: does anyone here run an explicit "return nothing" path in production RAG? And what do you gate it on — an absolute score floor, the margin between top-1 and top-2, a lexical check like this one, or a reranker with a reject option?
