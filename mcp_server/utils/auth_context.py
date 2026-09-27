"""
Per the Alexa+ MCP Design Guide (Tools, Schema, and Data Design):

    "Declare only what you honor... A parameter the model can send but your server
    silently ignores produces confidently wrong answers, since the model trusts the
    schema and fills arguments based on it."

`agent_id` used to be a tool argument -- but caller identity isn't something the
calling model can honestly know; it would have to invent it, and a bad-faith caller
could pass any agent_id it wanted, quietly defeating the trust-score check in
risk_evaluator.py.

This module now does the real thing: it validates an OAuth 2.1 Bearer token against
a real authorization server's published JWKS (JSON Web Key Set) and reads the
verified client identity off of THAT, via the MCP SDK's TokenVerifier hook -- not
from anything the model supplies. This works with any OAuth 2.1 + PKCE S256
authorization server (Auth0, AWS Cognito, Okta, Keycloak...), exactly as described in
the Alexa+ Account Linking guide for MCP add-ons -- nothing Alexa-specific is
hardcoded here.

STATUS: this is real, working OAuth 2.1 resource-server validation -- point OAUTH_*
env vars at any real authorization server and it verifies real tokens. What's still
open: actually running an authorization server for account linking, and wiring
Alexa's specific redirect URIs into it (mcp-toolkit-account-linking.html) -- that's
your own OAuth server's setup, not something this module can do for you. Until
OAUTH_* env vars are set, the server runs with no auth at all (see server.py) and
resolve_agent_identity() returns a loudly-logged placeholder -- fine for local dev,
not for certification.

KNOWN CAVEAT: the Alexa+ MCP Authentication checklist explicitly says the
WWW-Authenticate header on 401 responses is "Not Supported Yet" on Alexa's side --
but this SDK's built-in auth middleware adds that header by default (it's correct
per the general MCP/RFC 9728 spec, just apparently not yet expected by Alexa+'s
client). If a real Alexa+ connection ever chokes on it, that header is the first
thing to check -- would need custom middleware to strip it, not fixed here.
"""
import logging
import os
from typing import List, Optional

import jwt
from jwt import PyJWKClient
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier

logging.getLogger("Alguard.auth")

_DEV_PLACEHOLDER = "UNVERIFIED_DEV_AGENT"


class JWTBearerTokenVerifier(TokenVerifier):
    """
    Validates an incoming Bearer JWT: signature (via the authorization server's JWKS),
    issuer, audience, expiry, and required scopes. Returns an AccessToken (accepted)
    or None (rejected) -- the MCP SDK's auth middleware does the rest (401s,
    WWW-Authenticate, RFC 9728 discovery) automatically once this is wired into
    AuthSettings in server.py.
    """

    def __init__(self, jwks_url: str, issuer: str, audience: str, required_scopes: Optional[List[str]] = None):
        # PyJWKClient caches fetched keys in memory and re-fetches on an unknown kid,
        # so this doesn't hit the network on every single request.
        self._jwk_client = PyJWKClient(jwks_url)
        self._issuer = issuer
        self._audience = audience
        self._required_scopes = required_scopes or []

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        # NOTE: get_signing_key_from_jwt is a blocking (sync) network call the first
        # time a given key id is seen. Acceptable for this MVP's request volume;
        # swap for an async JWKS client before this needs to handle real concurrency.
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["exp", "iat"]},
            )
        except jwt.PyJWTError as e:
            logger.warning("Rejected bearer token: %s", e)
            return None

        scopes = claims.get("scope", "")
        if isinstance(scopes, str):
            scopes = scopes.split()
        if self._required_scopes and not set(self._required_scopes).issubset(set(scopes)):
            logger.warning("Token missing required scopes %s (has %s)", self._required_scopes, scopes)
            return None

        client_id = claims.get("client_id") or claims.get("azp") or claims.get("sub")
        if not client_id:
            logger.warning("Token has no client_id/azp/sub claim to identify the caller")
            return None

        return AccessToken(token=token, client_id=str(client_id), scopes=list(scopes))


def build_token_verifier_from_env() -> Optional[JWTBearerTokenVerifier]:
    """
    Returns a configured verifier if OAUTH_JWKS_URL, OAUTH_ISSUER_URL, and
    OAUTH_AUDIENCE are all set in the environment; otherwise None, meaning
    server.py should run with no auth at all (local dev only).
    """
    jwks_url = os.environ.get("OAUTH_JWKS_URL")
    issuer = os.environ.get("OAUTH_ISSUER_URL")
    audience = os.environ.get("OAUTH_AUDIENCE")
    if not (jwks_url and issuer and audience):
        return None
    required_scopes = [s for s in os.environ.get("OAUTH_REQUIRED_SCOPES", "").split() if s]
    return JWTBearerTokenVerifier(jwks_url, issuer, audience, required_scopes)


def resolve_agent_identity() -> str:
    """
    Returns the verified caller identity for the current tool call.

    Reads it off the SDK's contextvar-based get_access_token() -- populated by the
    auth middleware AFTER JWTBearerTokenVerifier.verify_token() above has already
    accepted the token, so by the time a tool body runs this is trustworthy, not
    something the calling model could spoof. Falls back to a loud, clearly-fake
    placeholder only when no token verifier is configured at all.
    """
    access_token = get_access_token()
    if access_token is not None:
        return access_token.client_id

    logger.warning(
        "resolve_agent_identity: no OAuth configured (OAUTH_* env vars unset) -- "
        "falling back to %s. Do not ship this to certification without Account "
        "Linking configured.",
        _DEV_PLACEHOLDER,
    )
    return _DEV_PLACEHOLDER