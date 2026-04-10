"""
Central configuration — all settings loaded from environment / .env file.
"""
import sys
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, model_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Ollama ─────────────────────────────────────────────────────────────────
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.1:14b"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_api_key: str = ""


    # ── Ollama (Pi 5 / Dream mode) ────────────────────────────────────────────
    dream_ollama_base_url: str = "http://127.0.0.1:11434"
    dream_model: str = "phi3:mini"

    # ── Wake / Sleep schedule ─────────────────────────────────────────────────
    wake_start_time: str = "07:00"
    wake_end_time: str = "22:00"
    journal_time: str = "21:45"
    heartbeat_interval_hours: int = 6

    # ── Wake-on-LAN (deprecated — R710 retired) ───────────────────────────────
    wakeonlan_mac: str = ""
    wakeonlan_broadcast: str = "192.168.1.255"
    wakeonlan_boot_wait_secs: int = 180

    # ── Telegram ──────────────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_allowed_user_ids: str = ""    # comma-separated
    telegram_notification_chat_id: str = ""

    # ── Discord ───────────────────────────────────────────────────────────────
    discord_bot_token: str = ""
    discord_allowed_channel_ids: str = ""  # comma-separated
    discord_allowed_role_names: str = ""   # comma-separated role names (allowlist)
    discord_role_mapping: str = ""         # e.g. "Board->admin,Staff->staff"
    discord_status_channel_id: str = ""

    # ── Auth ──────────────────────────────────────────────────────────────────
    secret_key: str = "change-me-in-production"
    admin_username: str = "admin"
    admin_password_hash: str = ""

    # ── Gateway ───────────────────────────────────────────────────────────────
    gateway_bind_host: str = "127.0.0.1"
    gateway_port: int = 8400
    allow_public_bind: bool = False

    # ── Storage paths ─────────────────────────────────────────────────────────
    watched_folder: str = "data/documents"
    chroma_path: str = "data/chroma"
    memory_path: str = "data/memory"
    audit_log_path: str = "data/cdcn_audit.db"
    pending_changes_path: str = "skills_config/pending_changes"
    backup_path: str = "data/backups"

    # ── Indexer / RAG tuning ────────────────────────────────────────────────
    chunk_size_chars: int = 2000
    chunk_overlap_chars: int = 200
    vector_distance_threshold: float = 0.68
    search_overfetch_ratio: int = 3
    parser_timeout_secs: int = 10
    ocr_timeout_secs: int = 60

    # ── LLM timeouts ───────────────────────────────────────────────────────
    llm_chat_timeout_secs: int = 120
    llm_stream_timeout_secs: int = 300
    llm_embed_timeout_secs: int = 30

    # ── Rate limiting ───────────────────────────────────────────────────────
    rate_limit_per_minute: int = 20

    # ── Deadlines & reminders ───────────────────────────────────────────────
    deadline_reminder_days: int = 14
    session_archive_days: int = 90

    # ── Email (optional SMTP for board notifications) ───────────────────────
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_address: str = ""
    email_recipients: str = ""  # comma-separated

    # ── Derived helpers ───────────────────────────────────────────────────────
    @property
    def telegram_allowed_user_id_list(self) -> list[int]:
        if not self.telegram_allowed_user_ids:
            return []
        return [int(uid.strip()) for uid in self.telegram_allowed_user_ids.split(",") if uid.strip()]

    @property
    def discord_allowed_channel_id_list(self) -> list[int]:
        if not self.discord_allowed_channel_ids:
            return []
        return [int(cid.strip()) for cid in self.discord_allowed_channel_ids.split(",") if cid.strip()]

    @property
    def discord_allowed_role_name_list(self) -> list[str]:
        if not self.discord_allowed_role_names:
            return []
        return [r.strip() for r in self.discord_allowed_role_names.split(",") if r.strip()]

    # ── Validation ────────────────────────────────────────────────────────────
    @model_validator(mode="after")
    def validate_bind_host(self) -> "Settings":
        if self.gateway_bind_host == "0.0.0.0" and not self.allow_public_bind:
            raise ValueError(
                "GATEWAY_BIND_HOST is set to 0.0.0.0 but ALLOW_PUBLIC_BIND is false. "
                "Set ALLOW_PUBLIC_BIND=true to allow a public bind, or use 127.0.0.1."
            )
        if self.allow_public_bind:
            print(
                "WARNING: ALLOW_PUBLIC_BIND is true — the gateway is listening on a public interface. "
                "Ensure firewall rules and Tailscale ACLs are in place.",
                file=sys.stderr,
            )
        return self


settings = Settings()
