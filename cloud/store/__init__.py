"""PostgreSQL storage backend for Mengram Cloud.

`CloudStore` is assembled from domain mixins in this package; the public
surface is unchanged from the former single-file `cloud/store.py`.
"""

from ._common import (  # noqa: F401 — re-exported: callers and tests import these from cloud.store
    datetime, hashlib, json, logging, math, re, secrets, sys, threading, time, dataclass, Optional, contextmanager, annotate_steps, carry_step_history, estimate, _proc_name_tokens, procedure_similarity, is_near_duplicate_procedure, apply_step_outcome, psycopg2, PSYCOPG2_AVAILABLE, logger, ENTITY_TYPES, _normalize_fact, _normalize_step, _safe_parse_json, TTLCache, CloudEntity,
)
from ._core import CoreMixin
from ._auth import AuthMixin
from ._drips import DripMixin
from ._entities import EntityMixin
from ._search import SearchMixin
from ._reflection import ReflectionMixin
from ._embeddings import EmbeddingMixin
from ._profile import ProfileMixin
from ._billing import BillingMixin
from ._graph import GraphMixin
from ._episodes import EpisodeMixin
from ._procedures import ProcedureMixin
from ._agents import AgentMixin
from ._webhooks import WebhookMixin
from ._teams import TeamMixin
from ._triggers import TriggerMixin
from ._health import HealthMixin


class CloudStore(CoreMixin, AuthMixin, DripMixin, EntityMixin, SearchMixin, ReflectionMixin, EmbeddingMixin, ProfileMixin, BillingMixin, GraphMixin, EpisodeMixin, ProcedureMixin, AgentMixin, WebhookMixin, TeamMixin, TriggerMixin, HealthMixin):
    """
    PostgreSQL storage backend for Mengram Cloud.
    
    Features:
    - Connection pooling (ThreadedConnectionPool) for concurrent requests
    - TTL cache for frequent reads (stats, entities, insights)
    - Auto-reconnect on connection failures
    - Proper logging
    """
