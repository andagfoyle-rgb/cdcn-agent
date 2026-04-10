"""
Unified configuration — single source of truth for all CDCN Agent settings.

Wraps the Pydantic Settings from app.config and adds:
  - Startup validation (URLs valid, API keys non-empty for active providers)
  - Configuration logging on startup (sensitive values masked)
  - Clear error messages for missing required config
"""
import logging
import os
from urllib.parse import urlparse

from app.config import settings

log = logging.getLogger(__name__)

_SENSITIVE_KEYS = {
    "siliconflow_api_key", "secret_key", "telegram_bot_token",
    "discord_bot_token", "admin_password_hash", "smtp_password",
    "ollama_api_key",
}


def _mask(value: str) -> str:
    if not value or len(value) < 8:
        return "***" if value else "(empty)"
    return value[:4] + "..." + value[-4:]


def validate_config() -> list[str]:
    """
    Validate configuration on startup. Returns list of warning/error strings.
    Empty list means all OK.
    """
    issues: list[str] = []

    # Check LLM provider config
    provider = os.environ.get("LLM_PROVIDER", "ollama").lower()
    if provider == "siliconflow":
        api_key = os.environ.get("SILICONFLOW_API_KEY", "")
        if not api_key:
            issues.append("ERROR: LLM_PROVIDER=siliconflow but SILICONFLOW_API_KEY is empty")
        base_url = os.environ.get("SILICONFLOW_BASE_URL", "")
        if base_url:
            parsed = urlparse(base_url)
            if not parsed.scheme or not parsed.netloc:
                issues.append(f"WARNING: SILICONFLOW_BASE_URL looks invalid: {base_url}")

    # Check Ollama URL
    ollama_url = settings.ollama_base_url
    parsed = urlparse(ollama_url)
    if not parsed.scheme or not parsed.netloc:
        issues.append(f"WARNING: OLLAMA_BASE_URL looks invalid: {ollama_url}")

    # Check storage paths
    from pathlib import Path
    for name, path_str in [
        ("WATCHED_FOLDER", settings.watched_folder),
        ("CHROMA_PATH", settings.chroma_path),
        ("MEMORY_PATH", settings.memory_path),
    ]:
        p = Path(path_str)
        if not p.exists():
            issues.append(f"WARNING: {name}={path_str} does not exist (will be created)")

    # Check security
    if settings.secret_key == "change-me-in-production":
        issues.append("WARNING: SECRET_KEY is still the default — change it for production")

    if settings.gateway_bind_host == "0.0.0.0" and settings.allow_public_bind:
        issues.append("INFO: Gateway bound to 0.0.0.0 (public) — ensure firewall is configured")

    return issues


def log_active_config() -> None:
    """Log the active configuration, masking sensitive values."""
    lines = ["Active configuration:"]
    for key in sorted(settings.model_fields.keys()):
        value = getattr(settings, key, "")
        if key in _SENSITIVE_KEYS:
            display = _mask(str(value))
        else:
            display = str(value)
        lines.append(f"  {key} = {display}")
    log.info("\n".join(lines))


def startup_check() -> None:
    """Run on startup: validate and log config."""
    issues = validate_config()
    for issue in issues:
        if issue.startswith("ERROR"):
            log.error(issue)
        elif issue.startswith("WARNING"):
            log.warning(issue)
        else:
            log.info(issue)

    log_active_config()

    if any(i.startswith("ERROR") for i in issues):
        log.error("Configuration has errors — agent may not function correctly")
