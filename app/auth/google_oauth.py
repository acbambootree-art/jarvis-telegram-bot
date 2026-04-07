from __future__ import annotations

import json
from uuid import UUID

import structlog
from cryptography.fernet import Fernet
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.config import settings
from app.db.database import async_session
from app.db.repositories import UserRepository

logger = structlog.get_logger()

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
]

fernet = Fernet(settings.fernet_key.encode() if isinstance(settings.fernet_key, str) else settings.fernet_key)

# Store the active flow so the code verifier (PKCE) persists between auth URL and callback
_active_flow: Flow | None = None


def get_oauth_flow() -> Flow:
    """Create a Google OAuth2 flow."""
    client_config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = settings.google_redirect_uri
    return flow


def get_authorization_url() -> str:
    """Get the Google OAuth2 authorization URL."""
    global _active_flow
    _active_flow = get_oauth_flow()
    url, _ = _active_flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return url


async def exchange_code(code: str, user_id: UUID) -> bool:
    """Exchange auth code for tokens and store them encrypted."""
    global _active_flow
    try:
        flow = _active_flow if _active_flow else get_oauth_flow()
        flow.fetch_token(code=code)
        _active_flow = None
        creds = flow.credentials

        token_data = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes) if creds.scopes else SCOPES,
        }

        encrypted = fernet.encrypt(json.dumps(token_data).encode()).decode()

        async with async_session() as session:
            repo = UserRepository(session)
            await repo.update_google_tokens(user_id, {"encrypted": encrypted})

        logger.info("Google tokens stored successfully", user_id=str(user_id))
        return True

    except Exception as e:
        logger.exception("Failed to exchange Google auth code", error=str(e))
        return False


async def get_google_credentials(user_id: UUID) -> Credentials | None:
    """Load and refresh Google credentials for a user."""
    async with async_session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_id(user_id)

    if not user or not user.google_tokens:
        return None

    try:
        encrypted = user.google_tokens.get("encrypted")
        if not encrypted:
            return None

        token_data = json.loads(fernet.decrypt(encrypted.encode()))

        creds = Credentials(
            token=token_data["token"],
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id", settings.google_client_id),
            client_secret=token_data.get("client_secret", settings.google_client_secret),
            scopes=token_data.get("scopes", SCOPES),
        )

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

            # Persist refreshed token
            token_data["token"] = creds.token
            encrypted = fernet.encrypt(json.dumps(token_data).encode()).decode()

            async with async_session() as session:
                repo = UserRepository(session)
                await repo.update_google_tokens(user_id, {"encrypted": encrypted})

        return creds

    except Exception as e:
        logger.exception("Failed to load Google credentials", error=str(e))
        return None
