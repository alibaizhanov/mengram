"""Mengram chat message history for LangChain."""

from __future__ import annotations

import logging
from typing import List, Optional, Sequence

from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage

logger = logging.getLogger(__name__)


class MengramChatMessageHistory(BaseChatMessageHistory):
    """Chat message history backed by Mengram.

    Every time messages are added, Mengram's extraction pipeline runs in the
    background — automatically extracting facts, events, and workflows into
    semantic, episodic, and procedural memory.

    Usage:
        from langchain_mengram import MengramChatMessageHistory
        from langchain_core.runnables.history import RunnableWithMessageHistory

        def get_history(session_id: str):
            return MengramChatMessageHistory(
                api_key="om-...",
                user_id=session_id,
            )

        chain_with_history = RunnableWithMessageHistory(
            chain, get_history,
            input_messages_key="input",
            history_messages_key="history",
        )
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        user_id: str = "default",
        api_url: str = "https://mengram.io",
        agent_id: Optional[str] = None,
        app_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ):
        from mengram import Mengram

        self.client = Mengram(api_key=api_key, base_url=api_url)
        self.user_id = user_id
        self.agent_id = agent_id
        self.app_id = app_id
        self.run_id = run_id
        self._messages: List[BaseMessage] = []

    @property
    def messages(self) -> List[BaseMessage]:
        return list(self._messages)

    def add_messages(self, messages: Sequence[BaseMessage]) -> None:
        self._messages.extend(messages)

        mengram_messages = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                mengram_messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                mengram_messages.append({"role": "assistant", "content": msg.content})
            elif isinstance(msg, SystemMessage):
                continue
            else:
                mengram_messages.append({"role": "user", "content": str(msg.content)})

        if mengram_messages:
            try:
                self.client.add(
                    mengram_messages,
                    user_id=self.user_id,
                    agent_id=self.agent_id,
                    app_id=self.app_id,
                    run_id=self.run_id,
                )
            except Exception as e:
                logger.warning("Failed to send to Mengram: %s", e)

    def clear(self) -> None:
        self._messages.clear()
