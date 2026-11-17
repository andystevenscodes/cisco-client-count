"""Settings, all from environment variables (see .env.example)."""

import ipaddress
import os
import secrets
from pathlib import Path
from zoneinfo import ZoneInfo


def _bool(env, name, default=False):
    value = env.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _networks(value):
    nets = []
    for part in (value or "").replace(";", ",").split(","):
        part = part.strip()
        if part:
            nets.append(ipaddress.ip_network(part, strict=False))
    return nets


def _version_file():
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip() or "dev"
    except OSError:
        return "dev"


class Config:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.version = env.get("APP_VERSION", "") or _version_file()
        self.database_url = env.get("DATABASE_URL", "")

        self.telemetry_port = int(env.get("TELEMETRY_LISTEN_PORT", "57500"))
        self.web_port = int(env.get("WEB_LISTEN_PORT", "8080"))
        # The ports as published on the host (docker-compose maps them); these
        # are what the controller and Power BI connect to.
        self.telemetry_public_port = int(env.get("TELEMETRY_PORT", "") or self.telemetry_port)
        self.db_public_port = int(env.get("DB_PORT", "") or 5432)

        self.admin_password = env.get("ADMIN_PASSWORD", "")
        self.export_token = env.get("EXPORT_TOKEN", "")
        self.secret_key = env.get("SECRET_KEY", "") or secrets.token_hex(32)
        self.powerbi_db_password = env.get("POWERBI_DB_PASSWORD", "")

        self.tz = env.get("TZ", "Europe/Amsterdam") or "Europe/Amsterdam"
        ZoneInfo(self.tz)  # fail early on a typo

        # A periodic update arrives as a burst of messages. The burst is one
        # "cycle"; it is complete once the stream has been quiet this long.
        self.cycle_gap_seconds = float(env.get("CYCLE_GAP_SECONDS", "5"))
        # Which client states count as "connected".
        self.count_states = {
            s.strip()
            for s in env.get("COUNT_STATES", "client-status-run").split(",")
            if s.strip()
        }
        self.raw_buffer_messages = int(env.get("RAW_BUFFER_MESSAGES", "500"))
        self.keep_cycles = int(env.get("KEEP_CYCLES", "5"))
        self.anonymize_clients = _bool(env, "ANONYMIZE_CLIENTS", False)
        self.anonymize_salt = env.get("ANONYMIZE_SALT", "") or self.secret_key

        # Only these source addresses may deliver telemetry (empty = anyone).
        self.allowed_sources = _networks(env.get("ALLOWED_WLC_SOURCES", ""))

        self.retention_days = int(env.get("RETENTION_DAYS", "400"))
        # An AP list older than this is no longer used to write zero counts.
        self.inventory_max_age = int(env.get("INVENTORY_MAX_AGE_SECONDS", "3600"))
        # Without an AP list: an AP that had clients this recently gets a 0.
        self.zero_fill_days = int(env.get("ZERO_FILL_DAYS", "7"))

    def source_allowed(self, ip):
        if not self.allowed_sources:
            return True
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if getattr(addr, "ipv4_mapped", None):
            addr = addr.ipv4_mapped
        return any(addr in net for net in self.allowed_sources)
