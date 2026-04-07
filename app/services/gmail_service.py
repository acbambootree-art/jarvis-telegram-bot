import base64
from email.mime.text import MIMEText
from uuid import UUID

import structlog
from googleapiclient.discovery import build

from app.auth.google_oauth import get_google_credentials

logger = structlog.get_logger()


async def _get_gmail_service(user_id: UUID):
    creds = await get_google_credentials(user_id)
    if not creds:
        return None
    return build("gmail", "v1", credentials=creds)


async def search_emails(user_id: UUID, query: str, max_results: int = 5) -> dict:
    service = await _get_gmail_service(user_id)
    if not service:
        return {"success": False, "error": "Gmail not connected. Please connect your Google account first."}

    try:
        results = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        messages = results.get("messages", [])

        emails = []
        for msg_info in messages:
            msg = service.users().messages().get(userId="me", id=msg_info["id"], format="metadata").execute()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

            emails.append({
                "email_id": msg["id"],
                "thread_id": msg["threadId"],
                "subject": headers.get("Subject", "No subject"),
                "from": headers.get("From", "Unknown"),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", "")[:150],
                "is_unread": "UNREAD" in msg.get("labelIds", []),
            })

        return {"success": True, "count": len(emails), "emails": emails}
    except Exception as e:
        logger.exception("Failed to search emails", error=str(e))
        return {"success": False, "error": str(e)}


async def read_email(user_id: UUID, email_id: str) -> dict:
    service = await _get_gmail_service(user_id)
    if not service:
        return {"success": False, "error": "Gmail not connected."}

    try:
        msg = service.users().messages().get(userId="me", id=email_id, format="full").execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

        # Extract body
        body = _extract_body(msg.get("payload", {}))

        return {
            "success": True,
            "email_id": msg["id"],
            "thread_id": msg["threadId"],
            "subject": headers.get("Subject", "No subject"),
            "from": headers.get("From", "Unknown"),
            "to": headers.get("To", ""),
            "date": headers.get("Date", ""),
            "body": body[:3000],  # Truncate for WhatsApp
            "has_attachments": any(
                part.get("filename") for part in msg.get("payload", {}).get("parts", [])
            ),
        }
    except Exception as e:
        logger.exception("Failed to read email", error=str(e))
        return {"success": False, "error": str(e)}


async def draft_reply(user_id: UUID, email_id: str, body: str) -> dict:
    service = await _get_gmail_service(user_id)
    if not service:
        return {"success": False, "error": "Gmail not connected."}

    try:
        # Get original message for threading
        original = service.users().messages().get(userId="me", id=email_id, format="metadata").execute()
        headers = {h["name"]: h["value"] for h in original.get("payload", {}).get("headers", [])}

        reply_to = headers.get("From", "")
        subject = headers.get("Subject", "")
        if not subject.startswith("Re: "):
            subject = f"Re: {subject}"

        message = MIMEText(body)
        message["to"] = reply_to
        message["subject"] = subject
        message["In-Reply-To"] = headers.get("Message-ID", "")
        message["References"] = headers.get("Message-ID", "")

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        draft_body = {"message": {"raw": raw, "threadId": original["threadId"]}}

        draft = service.users().drafts().create(userId="me", body=draft_body).execute()

        return {
            "success": True,
            "draft_id": draft["id"],
            "to": reply_to,
            "subject": subject,
            "message": "Draft created. You can review and send it from Gmail.",
        }
    except Exception as e:
        logger.exception("Failed to create draft", error=str(e))
        return {"success": False, "error": str(e)}


async def get_unread_count(user_id: UUID) -> dict:
    service = await _get_gmail_service(user_id)
    if not service:
        return {"success": False, "error": "Gmail not connected."}

    try:
        results = service.users().messages().list(userId="me", q="is:unread", maxResults=1).execute()
        count = results.get("resultSizeEstimate", 0)
        return {"success": True, "unread_count": count}
    except Exception as e:
        logger.exception("Failed to get unread count", error=str(e))
        return {"success": False, "error": str(e)}


def _extract_body(payload: dict) -> str:
    """Extract text body from email payload."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    parts = payload.get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")

    # Fallback to HTML
    for part in parts:
        if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
            html = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            # Basic HTML stripping
            import re
            text = re.sub(r"<[^>]+>", "", html)
            return text

    # Recurse into nested parts
    for part in parts:
        result = _extract_body(part)
        if result:
            return result

    return ""
