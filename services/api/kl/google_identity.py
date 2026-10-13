"""Linking a Google identity by signing in (slice F2b, ADR 0008): the Google twin of GitHub's linking (slice K2).

Signing in to Google returns an ID token: a JWT Google signs, naming the account's email and whether Google has
verified it. The platform checks it against Google's published keys, for this org's sign-in client, and still
current, and takes the email only if it's verified. That person's `ext:google.<email>` identity is then theirs, so
Drive files shared with that address reach them, still within Drive's own permissions. The token is used once and
never stored. The org's sign-in client lives in OpenBao, and its address must be on KL_GOOGLE_API_BASES, as a Drive
connection's must.
"""
import httpx
import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

from . import config, jwks
from .gdrive import GOOGLE_API
from .principals import is_valid

CLIENT_PATH = f"kl/{config.ORG_SLUG}/connectors/google-signin"
CLIENT_REF = f"openbao://secret/{CLIENT_PATH}"
_keys: dict[str, PyJWKClient] = {}


class SignInError(Exception):
    """Google's answer can't link an identity. The message is safe to show."""


def endpoints(api_base: str) -> dict:
    """Where sign-in goes: Google's own addresses, or the same paths under another allowed base (smoke's fake)."""
    if api_base == GOOGLE_API:
        return {"auth": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token",
                "certs": "https://www.googleapis.com/oauth2/v3/certs",
                "issuers": ("https://accounts.google.com", "accounts.google.com")}
    return {"auth": f"{api_base}/o/oauth2/v2/auth", "token": f"{api_base}/token", "certs": f"{api_base}/oauth2/v3/certs",
            "issuers": (api_base,)}


def verified_email(client: dict, code: str, redirect_uri: str) -> str:
    """Exchange a sign-in code for Google's ID token, check it, and return the account's verified email, lowercased."""
    if client.get("api_base") not in config.GOOGLE_API_BASES:
        raise SignInError("the sign-in client's address isn't allowed here")
    where = endpoints(client["api_base"])
    r = httpx.post(where["token"], timeout=15, data={"code": code, "client_id": client["client_id"],
                                                     "client_secret": client["client_secret"], "redirect_uri": redirect_uri,
                                                     "grant_type": "authorization_code"})
    token = r.json().get("id_token") if r.status_code == 200 else None
    if not token:
        raise SignInError("Google didn't accept that sign-in; try again")
    try:
        keys = _keys.setdefault(where["certs"], jwks.client(where["certs"]))
        claims = jwks.decode(keys, token, algorithms=["RS256"], audience=client["client_id"],
                             options={"require": ["exp", "iat", "iss", "aud", "email"]})
    except (jwt.InvalidTokenError, PyJWKClientError, httpx.HTTPError) as e:
        raise SignInError("Google's answer didn't verify; try again") from e
    if claims.get("iss") not in where["issuers"]:
        raise SignInError("Google's answer didn't verify; try again")
    if claims.get("email_verified") not in (True, "true"):
        raise SignInError("Google hasn't verified that account's email, so it can't be linked")
    email = str(claims["email"]).lower()
    if not is_valid(f"ext:google.{email}"):
        raise SignInError("that Google account's email can't be used here")
    return email
