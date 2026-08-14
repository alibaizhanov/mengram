"""Sub-user scoping for request bodies.

The sub-user — the tenant a memory belongs to inside one account — is named
`user_id` in request bodies but `sub_user_id` in the query string of the read
endpoints (/v1/profile, /v1/feed). Two names for one concept, and the write
models silently dropped the one they did not declare: a client that scoped its
writes with `sub_user_id` or `sub_user` had the field ignored and every memory
merged into `default`, with a 200 back. Both spellings are accepted here, and
anything unrecognized is rejected so the next mismatch fails loudly.
"""

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class SubUserScoped(BaseModel):
    """Base for request bodies that target a sub-user."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    user_id: str = Field(
        default="default",
        validation_alias=AliasChoices("user_id", "sub_user_id", "sub_user"),
    )


def resolve_sub_user(body_value: str | None, query_value: str | None) -> str:
    """Pick the sub-user from the body, falling back to ?sub_user_id=.

    An explicit body value wins; the query param only fills in when the body
    left it at the default.
    """
    if body_value and body_value != "default":
        return body_value
    if query_value:
        return query_value
    return body_value or "default"
