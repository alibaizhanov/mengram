"""
Auto-Memory Middleware — automatic memory for any LLM.

Like Mem0 proxy: wraps any LLM call.
- BEFORE response: recall → adds context from vault
- AFTER response: remember → extracts and saves new knowledge

Usage:
    from mengram import Memory
    from mengram_middleware import AutoMemory

    m = Memory(vault_path="./vault", llm_provider="anthropic", api_key="...")
    auto = AutoMemory(memory=m, user_id="ali")

    # Just chat — memory works automatically
    response = auto.chat("We have a problem with Kafka consumer lag")
    # → Automatically: recall context → LLM response → remember new knowledge

    # Or use with OpenAI-compatible API
    response = auto.chat_with_history([
        {"role": "user", "content": "Help with PostgreSQL"},
    ])
"""

from typing import Optional

from mengram import Memory
from engine.extractor.llm_client import LLMClient


class AutoMemory:
    """
    Automatic memory for LLM.

    Wraps each call:
    1. recall → searches context in vault
    2. Adds context to system prompt
    3. Calls LLM
    4. remember → extracts knowledge from conversation
    5. Returns response
    """

    def __init__(
        self,
        memory: Memory,
        user_id: str = "default",
        auto_remember: bool = True,
        auto_recall: bool = True,
        system_prompt: str = "",
    ):
        self.memory = memory
        self.user_id = user_id
        self.auto_remember = auto_remember
        self.auto_recall = auto_recall
        self.base_system_prompt = system_prompt or (
            "You are a helpful AI assistant. Use context from memory "
            "to give personalized answers."
        )
        self.conversation_history: list[dict] = []

    def chat(self, message: str) -> str:
        """
        Send a message with automatic memory.

        Args:
            message: User message

        Returns:
            LLM response (with context from vault)
        """
        # Step 1: Recall — search for context
        context = ""
        if self.auto_recall:
            brain = self.memory._get_brain(self.user_id)
            context = brain.recall(message)
            if context and context != f"Nothing found for query: '{message}'":
                print(f"🔍 Recall: found context ({len(context)} chars)")
            else:
                context = ""

        # Step 2: Build system prompt with context
        system = self.base_system_prompt
        if context:
            system += f"\n\n## User memory context:\n{context}"

        # Step 3: Call LLM
        self.conversation_history.append({"role": "user", "content": message})

        # Format history into single prompt
        conv_text = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
            for m in self.conversation_history[-10:]  # Last 10 messages
        )

        response = self.memory.llm.complete(
            prompt=conv_text,
            system=system,
        )

        self.conversation_history.append({"role": "assistant", "content": response})

        # Step 4: Remember — extract knowledge
        if self.auto_remember:
            try:
                # Take last 2 messages (user + assistant)
                recent = self.conversation_history[-2:]
                result = self.memory.add(recent, user_id=self.user_id)
                created = result.get("entities_created", [])
                updated = result.get("entities_updated", [])
                if created or updated:
                    print(f"💾 Remember: +{len(created)} created, ~{len(updated)} updated")
            except Exception as e:
                print(f"⚠️ Remember failed: {e}")

        return response

    def chat_with_history(self, messages: list[dict]) -> str:
        """
        Call with full message history (OpenAI-style).

        Args:
            messages: [{"role": "user"|"assistant", "content": "..."}]

        Returns:
            LLM response
        """
        if not messages:
            return ""

        # Take last message as query
        last_message = messages[-1]["content"]
        self.conversation_history = messages[:-1]

        return self.chat(last_message)

    def reset(self):
        """Reset conversation history (vault memory is preserved)"""
        self.conversation_history = []


# ==========================================
# Wrapper for OpenAI-compatible API
# ==========================================

class MemoryOpenAIWrapper:
    """
    Drop-in replacement for OpenAI client with automatic memory.

    Usage:
        from openai import OpenAI
        from mengram_middleware import MemoryOpenAIWrapper

        client = MemoryOpenAIWrapper(
            openai_client=OpenAI(),
            memory=Memory(vault_path="./vault", ...),
            user_id="ali",
        )

        # Use like a normal OpenAI client
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Help with the project"}],
        )
    """

    def __init__(self, openai_client, memory: Memory, user_id: str = "default"):
        self._client = openai_client
        self.memory = memory
        self.user_id = user_id
        self.chat = self._ChatCompletions(self)

    class _ChatCompletions:
        def __init__(self, wrapper):
            self.wrapper = wrapper
            self.completions = self

        def create(self, model: str, messages: list[dict], **kwargs):
            # 1. Recall
            last_msg = messages[-1]["content"] if messages else ""
            brain = self.wrapper.memory._get_brain(self.wrapper.user_id)
            context = brain.recall(last_msg)

            # 2. Inject context into system message
            enhanced_messages = list(messages)
            if context and "Nothing found" not in context:
                system_msg = {
                    "role": "system",
                    "content": f"User memory context:\n{context}",
                }
                enhanced_messages.insert(0, system_msg)

            # 3. Call original OpenAI
            response = self.wrapper._client.chat.completions.create(
                model=model,
                messages=enhanced_messages,
                **kwargs,
            )

            # 4. Remember
            try:
                full_conv = messages + [
                    {"role": "assistant", "content": response.choices[0].message.content}
                ]
                self.wrapper.memory.add(full_conv[-4:], user_id=self.wrapper.user_id)
            except Exception:
                pass

            return response


if __name__ == "__main__":
    print("=" * 60)
    print("🤖 Auto-Memory Middleware — Demo")
    print("=" * 60)

    # Mock for testing
    m = Memory(vault_path="./demo_auto_vault", llm_provider="mock")
    auto = AutoMemory(memory=m, user_id="ali")

    print("\n💬 Chat 1:")
    resp = auto.chat("I work at Uzum Bank, backend on Spring Boot")
    print(f"   Response: {resp[:100]}...")

    print(f"\n📁 Vault: {m.get_all(user_id='ali')}")

    print("\n💬 Chat 2:")
    resp = auto.chat("We have a PostgreSQL connection pool issue")
    print(f"   Response: {resp[:100]}...")

    print(f"\n📁 Vault now: {m.get_all(user_id='ali')}")
