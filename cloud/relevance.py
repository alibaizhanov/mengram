"""Does a recalled memory have anything to do with what was just asked?

Vector search always returns its nearest neighbours, because "nearest" is
defined for any query. Silence is not one of its outputs, so a three-word
question pulls three entities out of the store exactly as confidently as a
detailed one does, and whatever is least far away wins. On a real session the
result was facts about a Java backend at a bank arriving alongside a question
about hook payloads.

The same problem was already solved once in this codebase, for the execution
gate: before a learned workflow is allowed to interrupt a human, it has to
share a real word with the command it claims to be about. This is that guard,
moved to the other place vector search reaches the user.

It only ever removes. Nothing here can make recall return something it did not
already find, and when everything is filtered out the honest answer is to say
nothing at all.
"""

from __future__ import annotations

import re

#: Words of three or more characters, in any alphabet. Unicode matters here in
#: a way it does not for shell commands: a Russian prompt tokenised by an
#: ASCII-only pattern yields nothing at all, and a guard that sees no words
#: would silence recall completely rather than filter it.
_WORD = re.compile(r"\w{3,}", re.UNICODE)

#: Glue that two unrelated texts share by accident. Deliberately short: this
#: list exists to stop false matches, not to understand language.
_STOPWORDS = {
    # English
    "the", "and", "for", "with", "from", "into", "that", "this", "what", "why",
    "how", "when", "where", "who", "was", "were", "are", "have", "has", "had",
    "you", "your", "our", "not", "but", "can", "will", "would", "should",
    "about", "there", "then", "than", "them", "they", "его", "все",
    # Russian
    "что", "как", "это", "для", "или", "так", "уже", "они", "нас", "нам",
    "который", "которая", "которые", "если", "тоже", "надо", "нужно", "есть",
    "быть", "было", "были", "мне", "меня", "тебя", "вас", "чем", "чём", "при",
    "под", "над", "без", "про", "там", "тут", "вот", "почему", "теперь",
}


def words(text: str) -> set[str]:
    """Content words worth matching on, lowercased."""
    return {w.lower() for w in _WORD.findall(text or "")} - _STOPWORDS


def is_related(prompt_words: set[str], entity: str, facts: list) -> bool:
    """Does this result share a real word with the prompt?

    A single shared word is a low bar on purpose. The job is to reject the
    obviously unrelated, not to judge how relevant something is; anything
    stricter would start throwing away memories a person would recognise as
    useful, and the cost of that mistake is invisible.
    """
    if not prompt_words:
        return False         # nothing was asked that a memory could be about
    text = " ".join([str(entity or "")] + [str(f) for f in (facts or [])])
    return bool(prompt_words & words(text))


def filter_results(prompt: str, results: list) -> list:
    """The recalled entities that share a word with the prompt.

    A prompt with no content words at all — "та теперь?", "ok so" — returns
    nothing rather than everything. That is the opposite of the safe-looking
    choice and it is the right one: the shortest prompts are exactly the ones
    where vector search has least to go on, so passing them through unfiltered
    would leave the noisiest cases untouched. Nothing was asked that a memory
    could be about.

    Order is preserved: this decides what not to say, never what to rank first.
    """
    prompt_words = words(prompt)
    return [r for r in (results or [])
            if is_related(prompt_words, r.get("entity"), r.get("facts"))]
