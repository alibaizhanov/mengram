"""Local mode — memory that lives in a folder.

A memfmt tree is the memory: entities, episodes and procedures as Markdown
you own, with the same procedures-with-outcomes the cloud keeps. No account,
no server; only extraction needs a model, and that one you bring.
"""

from .store import LocalStore

__all__ = ["LocalStore"]
