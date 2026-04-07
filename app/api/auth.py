from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.auth.google_oauth import exchange_code, get_authorization_url
from app.config import settings
from app.db.database import async_session
from app.db.repositories import UserRepository

router = APIRouter(prefix="/auth")

# Store the pending user_id temporarily (single-user system)
_pending_user_id = None


def set_pending_user(user_id):
    global _pending_user_id
    _pending_user_id = user_id


@router.get("/google")
async def google_auth_start():
    """Redirect to Google OAuth consent page and auto-set pending user."""
    global _pending_user_id
    # Auto-create/fetch the owner user so setup script isn't needed
    if not _pending_user_id and settings.owner_chat_id:
        async with async_session() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_or_create(settings.owner_chat_id)
            _pending_user_id = user.id
    url = get_authorization_url()
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url)


@router.get("/google/callback")
async def google_auth_callback(request: Request):
    """Handle Google OAuth callback."""
    global _pending_user_id

    code = request.query_params.get("code")
    if not code:
        return HTMLResponse("<h1>Error: No authorization code received</h1>", status_code=400)

    # Auto-set pending user from owner_chat_id if not already set
    if not _pending_user_id and settings.owner_chat_id:
        async with async_session() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_or_create(settings.owner_chat_id)
            _pending_user_id = user.id

    if not _pending_user_id:
        return HTMLResponse("<h1>Error: No pending user. Set OWNER_CHAT_ID in .env.</h1>", status_code=400)

    success = await exchange_code(code, _pending_user_id)

    if success:
        return HTMLResponse(
            "<h1>Google account connected successfully!</h1>"
            "<p>You can close this window and return to Telegram.</p>"
        )
    else:
        return HTMLResponse("<h1>Failed to connect Google account. Please try again.</h1>", status_code=500)
