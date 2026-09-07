"""Request auth context shared by the API and its routers."""

from dataclasses import dataclass


@dataclass
class AuthContext:
    """Auth result with plan info for quota enforcement."""
    user_id: str
    plan: str         # free, starter, pro, growth, business
    rate_limit: int   # per-minute rate limit
