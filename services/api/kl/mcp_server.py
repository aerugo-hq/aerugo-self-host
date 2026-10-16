"""MCP server (slice B): search and ask for MCP clients such as Claude Desktop, with the user's own
permissions.

It's an OAuth 2.1 resource server that publishes its protected-resource metadata. It accepts only
tokens issued for it (audience kl-mcp), so an API token can't be replayed here. It never passes a
client's token on, and it runs the same permission path as the REST API: RLS, the OpenFGA post-check,
the egress policy, and audit. Tools are read-only."""
import os

import anyio
from fastapi import HTTPException
from jwt.exceptions import InvalidTokenError, PyJWKClientError
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl, ValidationError

from . import config
from . import main as api
from .auth import caller_from_claims, decode_token

AUDIENCE = os.environ.get("KL_MCP_AUDIENCE", "kl-mcp")
PUBLIC_URL = os.environ.get("KL_MCP_PUBLIC_URL", "http://localhost:8090")


class AudienceBoundVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = await anyio.to_thread.run_sync(decode_token, token, AUDIENCE)
        except (InvalidTokenError, PyJWKClientError):
            return None
        return AccessToken(token=token, client_id=str(claims.get("azp", "unknown")),
                           scopes=str(claims.get("scope", "")).split(), expires_at=claims.get("exp"))


def _server() -> FastMCP:
    settings = dict(
        instructions="Search and ask your company's knowledge. Every result respects your own permissions.",
        host="0.0.0.0", port=8090,
        token_verifier=AudienceBoundVerifier(),
        auth=AuthSettings(issuer_url=AnyHttpUrl(config.OIDC_ISSUER), resource_server_url=AnyHttpUrl(PUBLIC_URL),
                          required_scopes=[]),
    )
    try:  # DNS-rebinding protection, on SDK versions that support it
        from mcp.server.transport_security import TransportSecuritySettings
        settings["transport_security"] = TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=["localhost:*", "127.0.0.1:*", "mcp:*"],
            allowed_origins=["http://localhost:*", "http://127.0.0.1:*"])
        return FastMCP("knowledge-layer", **settings)
    except (ImportError, TypeError):
        settings.pop("transport_security", None)
        return FastMCP("knowledge-layer", **settings)


mcp = _server()


def _token() -> str:
    access = get_access_token()
    if access is None:
        raise ValueError("not authenticated")
    return access.token


def _as_caller(raw: str, fn):
    """Run fn(caller) for the token's user; API refusals become tool errors with the same messages."""
    try:
        return fn(caller_from_claims(decode_token(raw, AUDIENCE), config.ORG_SLUG))
    except HTTPException as e:
        raise ValueError(e.detail if isinstance(e.detail, str) else "request refused")


@mcp.tool()
async def search(query: str, scope: str = "org", limit: int = 10) -> dict:
    """Search the company's knowledge by keyword and by meaning. You only ever see documents your own
    permissions allow. scope is 'org' or comma-separated workspace slugs."""
    raw = _token()
    if not 2 <= len(query) <= 200 or not 1 <= limit <= 50:
        raise ValueError("query must be 2-200 characters and limit 1-50")
    return await anyio.to_thread.run_sync(
        lambda: _as_caller(raw, lambda caller: api.search(q=query, scope=scope, limit=limit, rerank=True, source=None,
                                                           container=None, caller=caller)))


@mcp.tool()
async def ask(question: str, model: str, scope: str = "org") -> dict:
    """Answer a question from sources you can access, with numbered citations. model is
    '<workspace>/<name>' from a workspace you belong to; sources that model may not receive are withheld."""
    raw = _token()
    try:
        body = api.AskIn(question=question, model=model, scope=scope)
    except ValidationError as e:
        raise ValueError(f"invalid request: {e.errors()[0].get('msg')}")
    return await anyio.to_thread.run_sync(lambda: _as_caller(raw, lambda caller: api.ask(body, caller)))


@mcp.tool()
async def get_concept(key: str) -> dict:
    """Read one concept's latest OKF version by its id (for example from a search result), if your
    permissions allow it. Missing and hidden concepts look the same: not found."""
    raw = _token()
    return await anyio.to_thread.run_sync(lambda: _as_caller(raw, lambda caller: api.get_concept(key, caller)))


# Graph tools (slice K12): the knowledge graph for agents, through the same permission path as the app.
@mcp.tool()
async def related(key: str) -> dict:
    """A page's curated neighbors that you can see, by the page's id (for example from a search result). Hidden
    neighbors are left out entirely, so the answer never reveals that they exist."""
    raw = _token()
    return await anyio.to_thread.run_sync(lambda: _as_caller(raw, lambda caller: api.related_concepts(key, caller)))


@mcp.tool()
async def who_to_ask(key: str) -> dict:
    """Who to ask about a page, by its id: who verified it, who owns it, its owning team's members (only for teams
    you're on), and who it names, each with the reasons. A page you can't read looks the same as a missing one."""
    raw = _token()
    return await anyio.to_thread.run_sync(lambda: _as_caller(raw, lambda caller: api.concept_people(key, caller)))


@mcp.tool()
async def impact(namespace: str, name: str, kind: str = "job") -> dict:
    """What depends on a dataset or job: everything downstream of it in the lineage your own teams reported, nearest
    first, with hop counts. kind is 'job' or 'dataset'. An asset you can't see looks the same as a missing one."""
    raw = _token()
    if kind not in ("job", "dataset") or not 1 <= len(namespace) <= 200 or not 1 <= len(name) <= 300:
        raise ValueError("kind must be job or dataset; namespace 1-200 characters, name 1-300")
    return await anyio.to_thread.run_sync(lambda: _as_caller(
        raw, lambda caller: api.lineage_impact(namespace=namespace, name=name, kind=kind, caller=caller)))


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
