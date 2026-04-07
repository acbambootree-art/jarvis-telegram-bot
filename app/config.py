from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str
    telegram_webhook_secret: str = "jarvis-webhook-secret"
    owner_chat_id: str = ""  # Your Telegram chat ID (numeric)

    # Claude
    anthropic_api_key: str

    # Database
    database_url: str

    # Google OAuth2
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    # Encryption
    fernet_key: str

    # OpenAI (Whisper)
    openai_api_key: str = ""

    # App
    render_external_url: str = ""  # Auto-provided by Render
    app_base_url: str = "http://localhost:8000"
    default_timezone: str = "Asia/Singapore"
    briefing_time: str = "07:00"
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
