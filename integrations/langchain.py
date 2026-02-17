"""
Mengram LangChain Integration — drop-in persistent memory for LangChain.

Two classes:

1. MengramChatMessageHistory — standard BaseChatMessageHistory for LCEL/RunnableWithMessageHistory.
   Stores messages and extracts knowledge into Mengram automatically.

2. MengramMemory — returns rich structured context (semantic + episodic + procedural)
   as memory variables. Drop-in replacement for ConversationBufferMemory.

Usage (LCEL — recommended):

    from mengram.integrations.langchain import MengramChatMessageHistory

    history = MengramChatMessageHistory(api_key="om-...", session_id="session-1", user_id="ali")

    from langchain_core.runnables.history import RunnableWithMessageHistory
    chain_with_memory = RunnableWithMessageHistory(
        chain,
        lambda session_id: MengramChatMessageHistory(
            api_key="om-...", session_id=session_id, user_id="ali"
        ),
        input_messages_key="input",
        history_messages_key="history",
    )

Usage (legacy ConversationChain):

    from mengram.integrations.langchain import MengramMemory

    memory = MengramMemory(api_key="om-...", user_id="ali")
    chain = ConversationChain(llm=llm, memory=memory)
    chain.predict(input="I deployed my app on Railway")
    # Next call — Mengram provides relevant context from all 3 memory types
    chain.predict(input="How did my last deployment go?")

Usage (Cognitive Profile — instant personalization):

    from mengram.integrations.langchain import MengramMemory

    memory = MengramMemory(api_key="om-...", user_id="ali", use_profile=True)
    # memory.load_memory_variables({}) returns the full Cognitive Profile
    # as system context — who the user is, recent events, known workflows
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

logger = logging.getLogger("mengram.langchain")

# ---- Lazy imports to avoid hard dependency on langchain ----


def _import_langchain():
    try:
        from langchain_core.chat_history import BaseChatMessageHistory
        from langchain_core.messages import (
            AIMessage,
            BaseMessage,
            HumanMessage,
            SystemMessage,
        )

        return BaseChatMessageHistory, BaseMessage, HumanMessage, AIMessage, SystemMessage
    except ImportError:
        raise ImportError(
            "LangChain is required for this integration. "
            "Install it with: pip install langchain-core"
        )


def _get_mengram_client(api_key: str, base_url: str = None):
    """Create Mengram client."""
    try:
        from mengram.cloud.client import CloudMemory

        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return CloudMemory(**kwargs)
    except ImportError:
        raise ImportError(
            "Mengram SDK is required. Install it with: pip install mengram-ai"
        )


# =============================================================
# 1. MengramChatMessageHistory — standard LCEL integration
# =============================================================


class MengramChatMessageHistory:
    """LangChain BaseChatMessageHistory backed by Mengram.

    Stores chat messages and automatically extracts knowledge (semantic,
    episodic, procedural) into Mengram's memory system.

    Works with RunnableWithMessageHistory for LCEL chains.
    """

    def __init__(
        self,
        api_key: str,
        session_id: str = "default",
        user_id: str = "default",
        base_url: str = None,
        auto_extract: bool = True,
        extract_every: int = 4,
    ):
        """
        Args:
            api_key: Mengram API key
            session_id: Session identifier (maps to run_id)
            user_id: User identifier
            base_url: Custom API URL (default: https://mengram.io)
            auto_extract: Automatically extract knowledge on add (default True)
            extract_every: Extract after every N messages (default 4)
        """
        BaseChatMessageHistory, _, _, _, _ = _import_langchain()
        self._base_cls = BaseChatMessageHistory

        self.client = _get_mengram_client(api_key, base_url)
        self.session_id = session_id
        self.user_id = user_id
        self.auto_extract = auto_extract
        self.extract_every = extract_every

        self._messages: list = []
        self._pending: list = []  # Messages not yet extracted
        self._msg_count = 0

    @property
    def messages(self):
        """Return all messages in this session."""
        _, BaseMessage, _, _, _ = _import_langchain()
        return list(self._messages)

    def add_messages(self, messages: Sequence) -> None:
        """Add messages and optionally extract knowledge."""
        self._messages.extend(messages)
        self._pending.extend(messages)
        self._msg_count += len(messages)

        if self.auto_extract and self._msg_count >= self.extract_every:
            self._extract_pending()
            self._msg_count = 0

    def add_message(self, message) -> None:
        """Add a single message."""
        self.add_messages([message])

    def clear(self) -> None:
        """Clear session messages (does not delete extracted memories)."""
        self._messages = []
        self._pending = []
        self._msg_count = 0

    def _extract_pending(self):
        """Send pending messages to Mengram for knowledge extraction."""
        if not self._pending:
            return

        _, _, HumanMessage, AIMessage, _ = _import_langchain()

        # Convert LangChain messages to Mengram format
        mengram_messages = []
        for msg in self._pending:
            if isinstance(msg, HumanMessage):
                mengram_messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                mengram_messages.append({"role": "assistant", "content": msg.content})
            else:
                mengram_messages.append({"role": "user", "content": str(msg.content)})

        try:
            self.client.add(
                mengram_messages,
                user_id=self.user_id,
                run_id=self.session_id,
            )
            logger.info(f"📝 Extracted {len(mengram_messages)} messages to Mengram")
        except Exception as e:
            logger.error(f"⚠️ Mengram extraction failed: {e}")

        self._pending = []

    def flush(self):
        """Force extract any pending messages now."""
        self._extract_pending()


# =============================================================
# 2. MengramMemory — rich context memory for ConversationChain
# =============================================================


class MengramMemory:
    """LangChain-compatible memory that returns rich context from Mengram.

    Instead of returning raw message history, returns relevant knowledge
    from all 3 memory types (semantic, episodic, procedural).

    Drop-in replacement for ConversationBufferMemory with superpowers:
    - Persistent across sessions
    - Semantic search (returns relevant context, not just recent)
    - Episodic memory (events, decisions)
    - Procedural memory (learned workflows)
    - Cognitive Profile (full user context)
    """

    # LangChain memory interface
    memory_key: str = "history"
    input_key: str = "input"
    output_key: str = "output"
    return_messages: bool = False

    def __init__(
        self,
        api_key: str,
        user_id: str = "default",
        base_url: str = None,
        memory_key: str = "history",
        input_key: str = "input",
        output_key: str = "output",
        return_messages: bool = False,
        use_profile: bool = False,
        search_top_k: int = 5,
    ):
        """
        Args:
            api_key: Mengram API key
            user_id: User identifier
            base_url: Custom API URL
            memory_key: Key for memory variables (default "history")
            input_key: Key for input in chain (default "input")
            output_key: Key for output in chain (default "output")
            return_messages: Return as Message objects (default False = string)
            use_profile: Use Cognitive Profile for context (default False)
            search_top_k: Number of search results per type (default 5)
        """
        self.client = _get_mengram_client(api_key, base_url)
        self.user_id = user_id
        self.memory_key = memory_key
        self.input_key = input_key
        self.output_key = output_key
        self.return_messages = return_messages
        self.use_profile = use_profile
        self.search_top_k = search_top_k

        self._buffer: list = []  # Recent messages for extraction

    @property
    def memory_variables(self) -> list[str]:
        """Keys this memory provides to the chain."""
        return [self.memory_key]

    def load_memory_variables(self, inputs: dict) -> dict:
        """Load relevant context from Mengram based on current input.

        This is where the magic happens — instead of returning raw history,
        we search Mengram for relevant knowledge across all 3 memory types.
        """
        # Get the current user input to search for relevant context
        query = inputs.get(self.input_key, "")

        if self.use_profile:
            return self._load_profile_context(query)

        return self._load_search_context(query)

    def _load_search_context(self, query: str) -> dict:
        """Search all 3 memory types for relevant context."""
        if not query:
            return {self.memory_key: "" if not self.return_messages else []}

        try:
            results = self.client.search_all(query, user_id=self.user_id)
        except Exception as e:
            logger.error(f"⚠️ Mengram search failed: {e}")
            return {self.memory_key: "" if not self.return_messages else []}

        # Build context string from all 3 types
        parts = []

        # Semantic — facts and knowledge
        semantic = results.get("semantic", [])
        if semantic:
            facts = []
            for r in semantic[:self.search_top_k]:
                entity = r.get("entity", "")
                for f in r.get("facts", [])[:5]:
                    facts.append(f"{entity}: {f}")
            if facts:
                parts.append("Known facts:\n" + "\n".join(f"- {f}" for f in facts))

        # Episodic — relevant events
        episodic = results.get("episodic", [])
        if episodic:
            events = []
            for ep in episodic[:3]:
                line = ep.get("summary", "")
                if ep.get("outcome"):
                    line += f" → {ep['outcome']}"
                events.append(line)
            if events:
                parts.append("Relevant events:\n" + "\n".join(f"- {e}" for e in events))

        # Procedural — relevant workflows
        procedural = results.get("procedural", [])
        if procedural:
            procs = []
            for pr in procedural[:2]:
                name = pr.get("name", "")
                steps = pr.get("steps", [])
                steps_str = " → ".join(s.get("action", "") for s in steps[:5])
                success = pr.get("success_count", 0)
                procs.append(f"{name}: {steps_str} (used {success}x)")
            if procs:
                parts.append("Known workflows:\n" + "\n".join(f"- {p}" for p in procs))

        context = "\n\n".join(parts) if parts else ""

        if self.return_messages:
            _, _, _, _, SystemMessage = _import_langchain()
            if context:
                return {self.memory_key: [SystemMessage(content=context)]}
            return {self.memory_key: []}

        return {self.memory_key: context}

    def _load_profile_context(self, query: str) -> dict:
        """Use Cognitive Profile for full user context."""
        try:
            profile = self.client.get_profile(self.user_id, force=False)
            context = profile.get("system_prompt", "")
        except Exception as e:
            logger.error(f"⚠️ Mengram profile failed: {e}")
            context = ""

        # Optionally append search results for the specific query
        if query and context:
            search_context = self._load_search_context(query)
            search_text = search_context.get(self.memory_key, "")
            if isinstance(search_text, str) and search_text:
                context = f"{context}\n\nRelevant to current question:\n{search_text}"

        if self.return_messages:
            _, _, _, _, SystemMessage = _import_langchain()
            if context:
                return {self.memory_key: [SystemMessage(content=context)]}
            return {self.memory_key: []}

        return {self.memory_key: context}

    def save_context(self, inputs: dict, outputs: dict) -> None:
        """Save conversation turn to Mengram for extraction."""
        user_input = inputs.get(self.input_key, "")
        ai_output = outputs.get(self.output_key, "")

        if not user_input:
            return

        self._buffer.append({"role": "user", "content": user_input})
        if ai_output:
            self._buffer.append({"role": "assistant", "content": ai_output})

        # Extract every 2 turns (4 messages)
        if len(self._buffer) >= 4:
            self._flush()

    def _flush(self):
        """Send buffered messages to Mengram."""
        if not self._buffer:
            return
        try:
            self.client.add(self._buffer, user_id=self.user_id)
            logger.info(f"📝 Sent {len(self._buffer)} messages to Mengram")
        except Exception as e:
            logger.error(f"⚠️ Mengram add failed: {e}")
        self._buffer = []

    def clear(self) -> None:
        """Flush pending and clear buffer."""
        self._flush()
        self._buffer = []
