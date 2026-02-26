# langchain-mengram

LangChain integration for [Mengram](https://mengram.io) — AI memory with semantic, episodic, and procedural memory types.

## Installation

```bash
pip install langchain-mengram
```

## Quick start

```python
from langchain_mengram import MengramRetriever

retriever = MengramRetriever(
    api_key="om-...",
    user_id="user-123",
)

docs = retriever.invoke("deployment issues")
for doc in docs:
    print(doc.metadata["memory_type"], doc.page_content)
```

## What it does

`MengramRetriever` searches across all three Mengram memory types and returns LangChain `Document` objects:

- **Semantic** — facts, entities, and knowledge graph relationships
- **Episodic** — events, experiences, and their outcomes
- **Procedural** — workflows, step-by-step procedures, and learned routines

Each document includes `metadata["memory_type"]` so you can filter or prioritize by type.

## Use in a chain

```python
from langchain_mengram import MengramRetriever
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

retriever = MengramRetriever(api_key="om-...", user_id="user-123")
llm = ChatOpenAI(model="gpt-4o-mini")

prompt = ChatPromptTemplate.from_messages([
    ("system", "Use the following memory context to answer:\n\n{context}"),
    ("human", "{question}"),
])

chain = (
    {"context": retriever | format_docs, "question": RunnablePassthrough()}
    | prompt
    | llm
    | StrOutputParser()
)

chain.invoke("What deployment steps did we follow last time?")
```

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | str | required | Mengram API key (starts with `om-`) |
| `user_id` | str | `"default"` | User to search memories for |
| `api_url` | str | `"https://mengram.io"` | Mengram API base URL |
| `top_k` | int | `5` | Max results per memory type |
| `memory_types` | list | `["semantic", "episodic", "procedural"]` | Which types to search |

## Links

- [Mengram docs](https://mengram.io/docs)
- [GitHub](https://github.com/alibaizhanov/mengram)
- [Get API key](https://mengram.io)
