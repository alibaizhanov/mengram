"""What one /v1/add costs and how large it may be (audit 2026-10-23).

Every 12 messages are one extraction call. Charged one unit whatever its size,
a single add could run a hundred model calls; now one unit covers two windows,
and the request itself is bounded."""
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent))
from cloud.plans import MAX_ADD_MESSAGES, MAX_MESSAGE_CHARS, add_units  # noqa: E402


def test_first_party_clients_still_cost_one_unit():
    assert add_units(2) == 1    # the Stop hook: last prompt + last reply
    assert add_units(20) == 1   # the importer's chunk size
    assert add_units(24) == 1


def test_long_conversations_cost_by_size():
    assert add_units(25) == 2
    assert add_units(48) == 2
    assert add_units(MAX_ADD_MESSAGES) == 21


def test_an_empty_add_still_costs_one():
    assert add_units(0) == 1


# The same field bounds cloud/api.py puts on Message / AddRequest (importing
# cloud.api needs a database, so the bounds are checked on a twin model).
class _Message(BaseModel):
    role: str
    content: str = Field(max_length=MAX_MESSAGE_CHARS)


class _Add(BaseModel):
    messages: list[_Message] = Field(max_length=MAX_ADD_MESSAGES)


def test_bounds_reject_oversized_requests():
    _Add(messages=[{"role": "user", "content": "x" * MAX_MESSAGE_CHARS}])
    with pytest.raises(ValidationError):
        _Add(messages=[{"role": "user", "content": "x" * (MAX_MESSAGE_CHARS + 1)}])
    with pytest.raises(ValidationError):
        _Add(messages=[{"role": "user", "content": "x"}] * (MAX_ADD_MESSAGES + 1))
