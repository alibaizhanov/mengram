"""Cooperating-process locks, conservative snapshot merging and atomic files.

The lock covers a read/modify/write operation. Each file is replaced atomically;
this is not a crash-atomic transaction over the entire Markdown tree. Editors
that do not take the lock can still race with a writer.
"""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import fields
import os
from pathlib import Path
import stat
import tempfile

from memfmt import Entity, Memory


class ConcurrentWriteError(RuntimeError):
    """A stale edit cannot safely be combined with the current folder."""


@contextmanager
def folder_lock(root: Path):
    directory = root / ".mengram"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "memory.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_write(path: Path, text: str) -> None:
    """Flush a sibling temporary file before replacing the destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if path.exists():
                os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _conflict(label):
    raise ConcurrentWriteError(
        f"Memory changed in another session ({label}); reload and retry the edit.")


def _value(base, edited, current, label):
    if edited == base:
        return deepcopy(current)
    if current == base or edited == current:
        return deepcopy(edited)
    if isinstance(base, list) and isinstance(edited, list) and isinstance(current, list):
        # Only independent additions can be combined without guessing which
        # deletion or replacement the user intended.
        if all(item in edited and item in current for item in base):
            result = deepcopy(current)
            for item in edited:
                if item not in result:
                    result.append(deepcopy(item))
            return result
    _conflict(label)


def _key(record):
    if hasattr(record, "name"):
        return ("name", " ".join(record.name.casefold().split()))
    return ("episode", record.happened, " ".join(record.summary.casefold().split()))


def _records(base, edited, current):
    def indexed(records):
        result = {}
        for record in records:
            key = _key(record)
            if key in result:
                _conflict(f"ambiguous duplicate {key}")
            result[key] = record
        return result

    before, ours, theirs = indexed(base), indexed(edited), indexed(current)
    result = []
    for key in dict.fromkeys([*theirs, *ours, *before]):
        b, e, c = before.get(key), ours.get(key), theirs.get(key)
        if e == b:
            merged = deepcopy(c)
        elif c == b:
            merged = deepcopy(e)
        elif isinstance(e, Entity) and isinstance(c, Entity):
            original = b if b is not None else Entity(name=c.name)
            merged = Entity(**{f.name: _value(getattr(original, f.name), getattr(e, f.name),
                                             getattr(c, f.name), f"{key[1]}.{f.name}")
                               for f in fields(Entity)})
        elif b is None and e == c:
            merged = deepcopy(c)  # the same newly extracted episode/procedure
        else:
            # Never combine workflow counters or revisions from stale copies.
            _conflict(str(key))
        if merged is not None:
            result.append(merged)
    return result


def merge_memory(base: Memory, edited: Memory, current: Memory) -> Memory:
    return Memory(
        entities=_records(base.entities, edited.entities, current.entities),
        episodes=_records(base.episodes, edited.episodes, current.episodes),
        procedures=_records(base.procedures, edited.procedures, current.procedures),
        profile=_value(base.profile, edited.profile, current.profile, "profile"),
    )
