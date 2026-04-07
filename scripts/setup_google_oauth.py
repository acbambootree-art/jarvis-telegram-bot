"""
One-time script to connect your Google account.

Usage:
  1. Start the server: uvicorn app.main:app
  2. Run this script: python scripts/setup_google_oauth.py
  3. Open the URL printed in your browser
  4. Grant permissions
  5. The callback will store encrypted tokens in the database
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth.google_oauth import get_authorization_url
from app.config import settings
from app.db.database import async_session
from app.db.repositories import UserRepository
from app.api.auth import set_pending_user


async def main():
    if not settings.owner_chat_id:
        print("ERROR: Set OWNER_CHAT_ID in your .env file first.")
        print("To get your chat ID, message @userinfobot on Telegram.")
        return

    # Get or create the owner user
    async with async_session() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_or_create(settings.owner_chat_id)

    # Set the pending user for the OAuth callback
    set_pending_user(user.id)

    # Get auth URL
    url = get_authorization_url()

    print("\n" + "=" * 60)
    print("Google Account Setup")
    print("=" * 60)
    print(f"\n1. Make sure the server is running:")
    print(f"   uvicorn app.main:app --reload")
    print(f"\n2. Open this URL in your browser:")
    print(f"\n   {url}")
    print(f"\n3. Grant all requested permissions")
    print(f"\n4. You'll be redirected back and see a success message")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
