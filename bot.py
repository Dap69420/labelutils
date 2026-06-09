import logging
import os
import random
import socket
import string
import sys
import time
from io import BytesIO, StringIO
import csv
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlparse

import discord
import psycopg
from psycopg import sql
from cryptography.fernet import Fernet, InvalidToken
from discord import app_commands
from dotenv import load_dotenv


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("labelutils-bot")

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
CONFIG_ENCRYPTION_KEY = os.getenv("CONFIG_ENCRYPTION_KEY")
CLEAR_GLOBAL_COMMANDS = os.getenv("CLEAR_GLOBAL_COMMANDS", "1").lower() not in {"0", "false", "no"}
PREMIUM_CONTACT = os.getenv("PREMIUM_CONTACT", "Contact the bot owner to buy premium.")
BOT_INVITE_URL = os.getenv(
    "BOT_INVITE_URL",
    "https://discord.com/oauth2/authorize?client_id=1513286315201007737"
    "&permissions=4503926112110592&integration_type=0&scope=bot%20applications.commands",
)
POOL_DATABASE_URLS = {
    index: url
    for index, url in enumerate(
        [
            os.getenv("POOL_DATABASE_URL_1"),
            os.getenv("POOL_DATABASE_URL_2"),
            os.getenv("POOL_DATABASE_URL_3"),
        ],
        start=1,
    )
    if url
}
POOL_DATABASE_NAMES = {
    1: "West US",
    2: "Europe (UK)",
    3: "South-East Asia",
}
OWNER_USER_IDS = {
    int(value.strip())
    for value in os.getenv("OWNER_USER_IDS", "").split(",")
    if value.strip().isdigit()
}
try:
    DEFAULT_STAFF_CHANNEL_ID = int(os.getenv("STAFF_CHANNEL_ID", "0"))
except ValueError:
    DEFAULT_STAFF_CHANNEL_ID = 0
try:
    DISCORD_GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0"))
except ValueError:
    DISCORD_GUILD_ID = 0

COOLDOWN_MINUTES = 30
DB_TIMEOUT_SECONDS = 8
FORCE_IPV4 = os.getenv("FORCE_IPV4", "1").lower() not in {"0", "false", "no"}
HEALTH_HOST = os.getenv("HEALTH_HOST", "0.0.0.0")
try:
    HEALTH_PORT = int(os.getenv("PORT", os.getenv("HEALTH_PORT", "7860")))
except ValueError:
    HEALTH_PORT = 7860
PANEL_PAGE_SIZE = 4
LEADERBOARD_PAGE_SIZE = 10
class StorageContext:
    def __init__(
        self,
        database_url: str,
        schema_name: str | None = None,
        storage_mode: str = "custom",
        pool_slot: int | None = None,
    ):
        self.database_url = database_url
        self.schema_name = schema_name
        self.storage_mode = storage_mode
        self.pool_slot = pool_slot

    def __bool__(self) -> bool:
        return bool(self.database_url)


submission_cooldowns: dict[int, datetime] = {}
guild_database_cache: dict[int, StorageContext | None] = {}
guild_staff_channel_cache: dict[int, int] = {}
guild_brand_cache: dict[int, dict[str, object] | None] = {}
guild_pro_settings_cache: dict[int, dict[str, object]] = {}

LABEL_STATUSES = [
    "In Queue",
    "Needs Review",
    "Shortlisted",
    "Processed",
    "Contacted",
    "Signed",
    "Approved",
    "Rejected",
]
TICKET_STATUSES = ["Open", "Waiting", "Answered", "Resolved"]

SUBMISSIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS label_submissions (
    ticket_id TEXT PRIMARY KEY,
    user_id BIGINT,
    name TEXT NOT NULL,
    discord_username TEXT NOT NULL,
    track_name TEXT NOT NULL,
    track_link TEXT NOT NULL,
    artist_names TEXT NOT NULL,
    message TEXT NOT NULL,
    staff_notes TEXT NOT NULL DEFAULT '',
    reviewer_id BIGINT,
    rating INTEGER,
    shortlisted BOOLEAN NOT NULL DEFAULT FALSE,
    priority BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'In Queue',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

SUPPORT_TICKETS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS labelutils_support_tickets (
    ticket_id TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    username TEXT NOT NULL,
    subject TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Open',
    thread_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

GUILD_BRANDING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS labelutils_guild_branding (
    guild_id BIGINT PRIMARY KEY,
    display_name TEXT,
    tagline TEXT,
    embed_color INTEGER,
    updated_by BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

PRO_SETTINGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS labelutils_pro_settings (
    guild_id BIGINT PRIMARY KEY,
    message_label TEXT,
    message_placeholder TEXT,
    approval_template TEXT,
    rejection_template TEXT,
    cooldown_minutes INTEGER,
    max_submissions_per_user INTEGER,
    duplicate_policy TEXT,
    approved_channel_id BIGINT,
    rejected_channel_id BIGINT,
    footer_text TEXT,
    logo_url TEXT,
    success_message TEXT,
    rejection_reasons TEXT,
    digest_channel_id BIGINT,
    ticket_channel_id BIGINT,
    updated_by BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

MIGRATION_TABLES = [
    (
        "label_submissions",
        [
            "ticket_id", "user_id", "name", "discord_username", "track_name", "track_link",
            "artist_names", "message", "staff_notes", "reviewer_id", "rating",
            "shortlisted", "priority", "status", "created_at",
        ],
    ),
    (
        "labelutils_support_tickets",
        [
            "ticket_id", "user_id", "username", "subject", "message", "status",
            "thread_id", "created_at", "updated_at",
        ],
    ),
    (
        "labelutils_guild_branding",
        ["guild_id", "display_name", "tagline", "embed_color", "updated_by", "updated_at"],
    ),
    (
        "labelutils_pro_settings",
        [
            "guild_id", "message_label", "message_placeholder", "approval_template",
            "rejection_template", "cooldown_minutes", "max_submissions_per_user",
            "duplicate_policy", "approved_channel_id", "rejected_channel_id", "footer_text",
            "logo_url", "success_message", "rejection_reasons", "digest_channel_id",
            "ticket_channel_id", "updated_by", "updated_at",
        ],
    ),
]


intents = discord.Intents.default()
intents.message_content = True


def generate_ticket_id() -> str:
    part1 = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    part2 = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return f"LABEL-{part1}-{part2}"


def generate_support_ticket_id() -> str:
    part = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return f"TICKET-{part}"


def is_valid_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def get_cooldown_remaining(user_id: int) -> timedelta | None:
    now = datetime.now(timezone.utc)
    expires_at = submission_cooldowns.get(user_id)
    if not expires_at or expires_at <= now:
        submission_cooldowns.pop(user_id, None)
        return None
    return expires_at - now


def set_submission_cooldown(user_id: int, minutes: int = COOLDOWN_MINUTES) -> None:
    submission_cooldowns[user_id] = datetime.now(timezone.utc) + timedelta(minutes=minutes)


def is_valid_database_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"postgres", "postgresql"} and bool(parsed.netloc)


def parse_snowflake(value: str) -> int | None:
    cleaned = value.strip()
    if not cleaned.isdigit():
        return None
    return int(cleaned)


def normalize_ticket_id(value: str) -> str:
    return value.strip().strip("`").upper()


def normalize_coupon_code(value: str) -> str:
    cleaned = value.strip().strip("`").upper()
    return "".join(ch for ch in cleaned if ch.isalnum() or ch == "-")


def guild_schema_name(guild_id: int) -> str:
    return f"guild_{guild_id}"


def valid_schema_name(value: str | None) -> bool:
    if not value:
        return False
    return value.startswith("guild_") and value[6:].isdigit()


def parse_hex_color(value: str) -> int | None:
    cleaned = value.strip().lstrip("#")
    if len(cleaned) != 6:
        return None
    try:
        return int(cleaned, 16)
    except ValueError:
        return None


def discord_timestamp(value: object, style: str = "f") -> str:
    if value is None:
        return "Unknown"

    parsed = value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value

    if isinstance(parsed, datetime):
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return f"<t:{int(parsed.timestamp())}:{style}>"

    try:
        return f"<t:{int(float(parsed))}:{style}>"
    except (TypeError, ValueError):
        return str(value)


def encryption_ready() -> bool:
    if not CONFIG_ENCRYPTION_KEY:
        return False
    try:
        Fernet(CONFIG_ENCRYPTION_KEY.encode())
        return True
    except ValueError:
        return False


def fernet() -> Fernet:
    if not CONFIG_ENCRYPTION_KEY:
        raise RuntimeError("CONFIG_ENCRYPTION_KEY is missing.")
    return Fernet(CONFIG_ENCRYPTION_KEY.encode())


def encrypt_database_url(database_url: str) -> str:
    return fernet().encrypt(database_url.encode()).decode()


def decrypt_database_url(encrypted_database_url: str) -> str | None:
    try:
        return fernet().decrypt(encrypted_database_url.encode()).decode()
    except (InvalidToken, ValueError):
        logger.exception("Failed to decrypt a guild database URL.")
        return None


def storage_url(database_url: str | StorageContext) -> str:
    return database_url.database_url if isinstance(database_url, StorageContext) else database_url


def connect_db(database_url: str | StorageContext):
    conn = psycopg.connect(storage_url(database_url), connect_timeout=DB_TIMEOUT_SECONDS)
    if isinstance(database_url, StorageContext) and database_url.schema_name:
        if not valid_schema_name(database_url.schema_name):
            raise RuntimeError("Refusing to use an invalid guild schema name.")
        conn.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {};").format(sql.Identifier(database_url.schema_name))
        )
        conn.execute(
            sql.SQL("SET search_path TO {}, public;").format(sql.Identifier(database_url.schema_name))
        )
    return conn


def prefer_ipv4_dns() -> None:
    original_getaddrinfo = socket.getaddrinfo

    def getaddrinfo_ipv4(*args, **kwargs):
        results = original_getaddrinfo(*args, **kwargs)
        ipv4_results = [result for result in results if result[0] == socket.AF_INET]
        return ipv4_results or results

    socket.getaddrinfo = getaddrinfo_ipv4
    logger.info("IPv4-preferred DNS resolution is enabled.")


def ensure_control_tables() -> None:
    if not DATABASE_URL:
        return

    try:
        with connect_db(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS labelutils_guild_databases (
                        guild_id BIGINT PRIMARY KEY,
                        database_url_encrypted TEXT,
                        storage_mode TEXT NOT NULL DEFAULT 'custom',
                        pool_slot INTEGER,
                        table_schema TEXT,
                        label_name TEXT,
                        staff_channel_id BIGINT,
                        configured_by BIGINT,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE labelutils_guild_databases
                    ALTER COLUMN database_url_encrypted DROP NOT NULL,
                    ALTER COLUMN configured_by DROP NOT NULL;
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE labelutils_guild_databases
                    ADD COLUMN IF NOT EXISTS staff_channel_id BIGINT;
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE labelutils_guild_databases
                    ADD COLUMN IF NOT EXISTS storage_mode TEXT NOT NULL DEFAULT 'custom',
                    ADD COLUMN IF NOT EXISTS pool_slot INTEGER,
                    ADD COLUMN IF NOT EXISTS table_schema TEXT,
                    ADD COLUMN IF NOT EXISTS label_name TEXT;
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS labelutils_premium_guilds (
                        guild_id BIGINT PRIMARY KEY,
                        plan TEXT NOT NULL,
                        expires_at TIMESTAMPTZ NOT NULL,
                        added_by BIGINT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS labelutils_premium_coupons (
                        coupon_code TEXT PRIMARY KEY,
                        plan TEXT NOT NULL,
                        days INTEGER NOT NULL,
                        uses_remaining INTEGER NOT NULL,
                        max_uses INTEGER NOT NULL,
                        created_by BIGINT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS labelutils_dm_routes (
                        dm_message_id BIGINT PRIMARY KEY,
                        guild_id BIGINT NOT NULL,
                        ticket_id TEXT NOT NULL,
                        thread_id BIGINT NOT NULL,
                        user_id BIGINT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_labelutils_dm_routes_user_id
                    ON labelutils_dm_routes (user_id);
                    """
                )
    except Exception:
        logger.exception("Failed to ensure LabelUtils control tables.")


def ensure_submission_table(database_url: str | StorageContext) -> bool:
    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(SUBMISSIONS_TABLE_SQL)
                cur.execute(SUPPORT_TICKETS_TABLE_SQL)
                cur.execute(GUILD_BRANDING_TABLE_SQL)
                cur.execute(PRO_SETTINGS_TABLE_SQL)
                cur.execute(
                    """
                    ALTER TABLE label_submissions
                    ADD COLUMN IF NOT EXISTS user_id BIGINT;
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE label_submissions
                    ADD COLUMN IF NOT EXISTS staff_notes TEXT NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS reviewer_id BIGINT,
                    ADD COLUMN IF NOT EXISTS rating INTEGER,
                    ADD COLUMN IF NOT EXISTS shortlisted BOOLEAN NOT NULL DEFAULT FALSE,
                    ADD COLUMN IF NOT EXISTS priority BOOLEAN NOT NULL DEFAULT FALSE;
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE labelutils_pro_settings
                    ADD COLUMN IF NOT EXISTS rejection_reasons TEXT,
                    ADD COLUMN IF NOT EXISTS digest_channel_id BIGINT,
                    ADD COLUMN IF NOT EXISTS ticket_channel_id BIGINT;
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_label_submissions_user_id
                    ON label_submissions (user_id);
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_label_submissions_track_link
                    ON label_submissions (track_link);
                    """
                )
        return True
    except Exception:
        logger.exception("Failed to prepare guild submission database.")
        return False


def same_storage_context(left: StorageContext | None, right: StorageContext | None) -> bool:
    if not left or not right:
        return False
    return (
        storage_url(left) == storage_url(right)
        and (left.schema_name or "") == (right.schema_name or "")
    )


def fetch_table_rows(conn, table_name: str, columns: list[str]) -> list[tuple]:
    query = sql.SQL("SELECT {} FROM {};").format(
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        sql.Identifier(table_name),
    )
    with conn.cursor() as cur:
        cur.execute(query)
        return cur.fetchall()


def replace_table_rows(conn, table_name: str, columns: list[str], rows: list[tuple]) -> None:
    with conn.cursor() as cur:
        cur.execute(sql.SQL("DELETE FROM {};").format(sql.Identifier(table_name)))
        if not rows:
            return
        placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in columns)
        query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(table_name),
            sql.SQL(", ").join(sql.Identifier(column) for column in columns),
            placeholders,
        )
        cur.executemany(query, rows)


def count_table_rows(conn, table_name: str) -> int:
    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT COUNT(*) FROM {};").format(sql.Identifier(table_name)))
        row = cur.fetchone()
        return int(row[0] or 0) if row else 0


def migrate_storage_data(source: StorageContext | None, target: StorageContext) -> bool:
    if not source or same_storage_context(source, target):
        return ensure_submission_table(target)
    if not ensure_submission_table(source) or not ensure_submission_table(target):
        return False

    try:
        with connect_db(source) as source_conn, connect_db(target) as target_conn:
            expected_counts: dict[str, int] = {}
            for table_name, columns in MIGRATION_TABLES:
                rows = fetch_table_rows(source_conn, table_name, columns)
                expected_counts[table_name] = len(rows)
                replace_table_rows(target_conn, table_name, columns, rows)

            for table_name, expected_count in expected_counts.items():
                actual_count = count_table_rows(target_conn, table_name)
                if actual_count != expected_count:
                    logger.error(
                        "Migration verification failed for %s: expected %s rows, got %s.",
                        table_name,
                        expected_count,
                        actual_count,
                    )
                    raise RuntimeError(f"Migration verification failed for {table_name}.")
        return True
    except Exception:
        logger.exception("Failed to migrate guild storage data.")
        return False


def drop_managed_schema(context: StorageContext | None) -> None:
    if not context or context.storage_mode != "pooled" or not valid_schema_name(context.schema_name):
        return
    try:
        with psycopg.connect(storage_url(context), connect_timeout=DB_TIMEOUT_SECONDS) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE;").format(sql.Identifier(context.schema_name))
            )
    except Exception:
        logger.exception("Failed to drop old managed schema %s.", context.schema_name)


def set_guild_database_url(
    guild_id: int,
    configured_by: int,
    database_url: str,
    *,
    migrate_existing: bool = True,
) -> bool:
    if not DATABASE_URL:
        logger.warning("DATABASE_URL is missing; cannot store guild database settings.")
        return False
    if not encryption_ready():
        logger.warning("CONFIG_ENCRYPTION_KEY is missing; refusing to store guild database URL.")
        return False

    encrypted_database_url = encrypt_database_url(database_url)
    old_context = get_guild_database_url(guild_id)
    new_context = StorageContext(database_url)
    if migrate_existing and not migrate_storage_data(old_context, new_context):
        logger.warning("Refusing to switch guild %s to custom database because migration failed.", guild_id)
        return False

    try:
        with connect_db(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO labelutils_guild_databases (
                        guild_id, database_url_encrypted, storage_mode, pool_slot,
                        table_schema, configured_by, updated_at
                    ) VALUES (
                        %s, %s, 'custom', NULL, NULL, %s, NOW()
                    )
                    ON CONFLICT (guild_id)
                    DO UPDATE SET
                        database_url_encrypted = EXCLUDED.database_url_encrypted,
                        storage_mode = 'custom',
                        pool_slot = NULL,
                        table_schema = NULL,
                        configured_by = EXCLUDED.configured_by,
                        updated_at = NOW();
                    """,
                    (guild_id, encrypted_database_url, configured_by),
                )
        guild_database_cache[guild_id] = new_context
        guild_brand_cache.pop(guild_id, None)
        guild_pro_settings_cache.pop(guild_id, None)
        if migrate_existing and old_context and not same_storage_context(old_context, new_context):
            drop_managed_schema(old_context)
        return True
    except Exception:
        logger.exception("Failed to save guild database URL for %s.", guild_id)
        return False


def available_pool_slots() -> list[int]:
    return [slot for slot in sorted(POOL_DATABASE_URLS) if is_valid_database_url(POOL_DATABASE_URLS[slot])]


def pool_region_name(pool_slot: int | None) -> str:
    return POOL_DATABASE_NAMES.get(int(pool_slot or 0), "Unknown")


def choose_pool_slot(*, randomize: bool = False) -> int | None:
    slots = available_pool_slots()
    if not slots:
        return None
    if randomize:
        return random.choice(slots)

    usage = {slot: 0 for slot in slots}
    if DATABASE_URL:
        try:
            with connect_db(DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT pool_slot, COUNT(*)
                        FROM labelutils_guild_databases
                        WHERE storage_mode = 'pooled' AND pool_slot IS NOT NULL
                        GROUP BY pool_slot;
                        """
                    )
                    for pool_slot, count in cur.fetchall():
                        if pool_slot in usage:
                            usage[int(pool_slot)] = int(count or 0)
        except Exception:
            logger.exception("Failed to calculate pooled database usage.")

    return min(slots, key=lambda slot: (usage[slot], slot))


def assign_pooled_guild_database(
    guild_id: int,
    configured_by: int,
    label_name: str,
    pool_slot: int | None = None,
    *,
    randomize: bool = False,
) -> bool:
    if not DATABASE_URL:
        logger.warning("DATABASE_URL is missing; cannot store pooled database settings.")
        return False

    pool_slot = pool_slot or choose_pool_slot(randomize=randomize)
    if not pool_slot:
        logger.warning("No POOL_DATABASE_URL_1..3 values are configured.")
        return False
    if pool_slot not in POOL_DATABASE_URLS or not is_valid_database_url(POOL_DATABASE_URLS[pool_slot]):
        logger.warning("Requested pool slot %s is not configured.", pool_slot)
        return False

    schema_name = guild_schema_name(guild_id)
    safe_label_name = truncate_text(label_name.strip() or f"Guild {guild_id}", 120)
    old_context = get_guild_database_url(guild_id)
    new_context = StorageContext(
        POOL_DATABASE_URLS[pool_slot],
        schema_name=schema_name,
        storage_mode="pooled",
        pool_slot=pool_slot,
    )
    if not migrate_storage_data(old_context, new_context):
        logger.warning("Refusing to switch guild %s to pooled database because migration failed.", guild_id)
        return False

    try:
        with connect_db(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO labelutils_guild_databases (
                        guild_id, database_url_encrypted, storage_mode, pool_slot,
                        table_schema, label_name, configured_by, updated_at
                    ) VALUES (
                        %s, NULL, 'pooled', %s, %s, %s, %s, NOW()
                    )
                    ON CONFLICT (guild_id)
                    DO UPDATE SET
                        database_url_encrypted = NULL,
                        storage_mode = 'pooled',
                        pool_slot = EXCLUDED.pool_slot,
                        table_schema = EXCLUDED.table_schema,
                        label_name = EXCLUDED.label_name,
                        configured_by = EXCLUDED.configured_by,
                        updated_at = NOW();
                    """,
                    (guild_id, pool_slot, schema_name, safe_label_name, configured_by),
                )
        guild_database_cache[guild_id] = new_context
        guild_brand_cache.pop(guild_id, None)
        guild_pro_settings_cache.pop(guild_id, None)
        if old_context and not same_storage_context(old_context, new_context):
            drop_managed_schema(old_context)
        return ensure_submission_table(guild_database_cache[guild_id])
    except Exception:
        logger.exception("Failed to assign pooled database for guild %s.", guild_id)
        return False


def set_guild_staff_channel_id(guild_id: int, configured_by: int, channel_id: int) -> bool:
    if not DATABASE_URL:
        logger.warning("DATABASE_URL is missing; cannot store guild staff channel settings.")
        return False

    try:
        with connect_db(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO labelutils_guild_databases (
                        guild_id, staff_channel_id, configured_by, updated_at
                    ) VALUES (
                        %s, %s, %s, NOW()
                    )
                    ON CONFLICT (guild_id)
                    DO UPDATE SET
                        staff_channel_id = EXCLUDED.staff_channel_id,
                        configured_by = EXCLUDED.configured_by,
                        updated_at = NOW();
                    """,
                    (guild_id, channel_id, configured_by),
                )
        guild_staff_channel_cache[guild_id] = channel_id
        return True
    except Exception:
        logger.exception("Failed to save guild staff channel ID for %s.", guild_id)
        return False


def get_guild_database_url(guild_id: int | None) -> StorageContext | None:
    if not guild_id:
        return None
    if guild_id in guild_database_cache:
        return guild_database_cache[guild_id]
    if not DATABASE_URL:
        guild_database_cache[guild_id] = None
        return None

    try:
        with connect_db(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT database_url_encrypted, storage_mode, pool_slot, table_schema
                    FROM labelutils_guild_databases
                    WHERE guild_id = %s;
                    """,
                    (guild_id,),
                )
                row = cur.fetchone()
    except Exception:
        logger.exception("Failed to fetch guild database URL for %s.", guild_id)
        guild_database_cache[guild_id] = None
        return None

    database_context = None
    if row:
        encrypted_database_url, storage_mode, pool_slot, table_schema = row
        if storage_mode == "pooled" and pool_slot in POOL_DATABASE_URLS:
            database_context = StorageContext(
                POOL_DATABASE_URLS[int(pool_slot)],
                schema_name=table_schema if valid_schema_name(table_schema) else guild_schema_name(guild_id),
                storage_mode="pooled",
                pool_slot=int(pool_slot),
            )
        elif encrypted_database_url:
            if not encryption_ready():
                logger.warning(
                    "CONFIG_ENCRYPTION_KEY is missing; cannot decrypt custom database URL for guild %s.",
                    guild_id,
                )
                guild_database_cache[guild_id] = None
                return None
            custom_url = decrypt_database_url(encrypted_database_url)
            database_context = StorageContext(custom_url) if custom_url else None

    guild_database_cache[guild_id] = database_context
    return database_context


def database_configured_for_guild(guild_id: int | None) -> bool:
    return bool(get_guild_database_url(guild_id))


def get_guild_staff_channel_id(guild_id: int | None) -> int:
    if not guild_id:
        return DEFAULT_STAFF_CHANNEL_ID
    if guild_id in guild_staff_channel_cache:
        return guild_staff_channel_cache[guild_id]
    if not DATABASE_URL:
        guild_staff_channel_cache[guild_id] = DEFAULT_STAFF_CHANNEL_ID
        return DEFAULT_STAFF_CHANNEL_ID

    try:
        with connect_db(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT staff_channel_id
                    FROM labelutils_guild_databases
                    WHERE guild_id = %s;
                    """,
                    (guild_id,),
                )
                row = cur.fetchone()
    except Exception:
        logger.exception("Failed to fetch guild staff channel ID for %s.", guild_id)
        guild_staff_channel_cache[guild_id] = DEFAULT_STAFF_CHANNEL_ID
        return DEFAULT_STAFF_CHANNEL_ID

    channel_id = int(row[0]) if row and row[0] else DEFAULT_STAFF_CHANNEL_ID
    guild_staff_channel_cache[guild_id] = channel_id
    return channel_id


def staff_channel_configured_for_guild(guild_id: int | None) -> bool:
    return get_guild_staff_channel_id(guild_id) != 0


def user_is_bot_owner(user_id: int) -> bool:
    return user_id in OWNER_USER_IDS


def get_premium_guild(guild_id: int | None) -> tuple | None:
    if not guild_id or not DATABASE_URL:
        return None

    try:
        with connect_db(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT plan, expires_at
                    FROM labelutils_premium_guilds
                    WHERE guild_id = %s AND expires_at > NOW();
                    """,
                    (guild_id,),
                )
                return cur.fetchone()
    except Exception:
        logger.exception("Failed to fetch premium status for guild %s.", guild_id)
        return None


def guild_has_premium(guild_id: int | None) -> bool:
    return bool(get_premium_guild(guild_id))


def add_premium_guild(guild_id: int, plan: str, days: int, added_by: int) -> bool:
    if not DATABASE_URL:
        logger.warning("DATABASE_URL is missing; cannot save premium guild.")
        return False

    safe_days = max(1, min(days, 3650))
    safe_plan = truncate_text(plan.strip() or "premium", 80)
    try:
        with connect_db(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO labelutils_premium_guilds (
                        guild_id, plan, expires_at, added_by, updated_at
                    ) VALUES (
                        %s, %s, NOW() + (%s * INTERVAL '1 day'), %s, NOW()
                    )
                    ON CONFLICT (guild_id)
                    DO UPDATE SET
                        plan = EXCLUDED.plan,
                        expires_at = GREATEST(labelutils_premium_guilds.expires_at, NOW())
                            + (%s * INTERVAL '1 day'),
                        added_by = EXCLUDED.added_by,
                        updated_at = NOW();
                    """,
                    (guild_id, safe_plan, safe_days, added_by, safe_days),
                )
        return True
    except Exception:
        logger.exception("Failed to add premium for guild %s.", guild_id)
        return False


def generate_coupon_code() -> str:
    parts = [
        "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        for _ in range(3)
    ]
    return f"LU-{'-'.join(parts)}"


def create_premium_coupon(
    plan: str,
    days: int,
    uses: int,
    created_by: int,
    code: str | None = None,
) -> tuple[bool, str]:
    if not DATABASE_URL:
        logger.warning("DATABASE_URL is missing; cannot create premium coupon.")
        return False, "Control database is not configured."

    safe_plan = truncate_text(plan.strip() or "pro", 80)
    safe_days = max(1, min(days, 3650))
    safe_uses = max(1, min(uses, 10000))
    coupon_code = normalize_coupon_code(code or generate_coupon_code())
    if len(coupon_code) < 6:
        return False, "Coupon code must be at least 6 characters."

    try:
        with connect_db(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO labelutils_premium_coupons (
                        coupon_code, plan, days, uses_remaining, max_uses,
                        created_by, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, NOW(), NOW()
                    );
                    """,
                    (coupon_code, safe_plan, safe_days, safe_uses, safe_uses, created_by),
                )
        return True, coupon_code
    except psycopg.errors.UniqueViolation:
        return False, "That coupon code already exists. Try another code."
    except Exception:
        logger.exception("Failed to create premium coupon %s.", coupon_code)
        return False, "Could not create coupon. Check the control database logs."


def redeem_premium_coupon(guild_id: int, code: str, redeemed_by: int) -> tuple[bool, str]:
    if not DATABASE_URL:
        logger.warning("DATABASE_URL is missing; cannot redeem premium coupon.")
        return False, "Control database is not configured."

    coupon_code = normalize_coupon_code(code)
    if not coupon_code:
        return False, "Please enter a valid coupon code."

    try:
        with connect_db(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT plan, days, uses_remaining
                    FROM labelutils_premium_coupons
                    WHERE coupon_code = %s
                    FOR UPDATE;
                    """,
                    (coupon_code,),
                )
                row = cur.fetchone()
                if not row:
                    return False, "That premium coupon does not exist."

                plan, days, uses_remaining = row
                if int(uses_remaining or 0) <= 0:
                    return False, "That premium coupon has no uses remaining."

                cur.execute(
                    """
                    INSERT INTO labelutils_premium_guilds (
                        guild_id, plan, expires_at, added_by, updated_at
                    ) VALUES (
                        %s, %s, NOW() + (%s * INTERVAL '1 day'), %s, NOW()
                    )
                    ON CONFLICT (guild_id)
                    DO UPDATE SET
                        plan = EXCLUDED.plan,
                        expires_at = GREATEST(labelutils_premium_guilds.expires_at, NOW())
                            + (%s * INTERVAL '1 day'),
                        added_by = EXCLUDED.added_by,
                        updated_at = NOW();
                    """,
                    (guild_id, plan, days, redeemed_by, days),
                )
                cur.execute(
                    """
                    UPDATE labelutils_premium_coupons
                    SET uses_remaining = uses_remaining - 1,
                        updated_at = NOW()
                    WHERE coupon_code = %s;
                    """,
                    (coupon_code,),
                )
        return True, f"Redeemed `{coupon_code}` for {days} day(s) of **{plan}** premium."
    except Exception:
        logger.exception("Failed to redeem premium coupon %s for guild %s.", coupon_code, guild_id)
        return False, "Could not redeem that coupon. Check the bot logs."


def remove_premium_guild(guild_id: int) -> bool:
    if not DATABASE_URL:
        logger.warning("DATABASE_URL is missing; cannot remove premium guild.")
        return False

    try:
        with connect_db(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM labelutils_premium_guilds WHERE guild_id = %s;",
                    (guild_id,),
                )
                return cur.rowcount > 0
    except Exception:
        logger.exception("Failed to remove premium for guild %s.", guild_id)
        return False


def save_dm_route(
    dm_message_id: int,
    guild_id: int,
    ticket_id: str,
    thread_id: int,
    user_id: int,
) -> bool:
    if not DATABASE_URL:
        logger.warning("DATABASE_URL is missing; cannot save DM reply route.")
        return False

    try:
        with connect_db(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO labelutils_dm_routes (
                        dm_message_id, guild_id, ticket_id, thread_id, user_id, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, NOW()
                    )
                    ON CONFLICT (dm_message_id)
                    DO UPDATE SET
                        guild_id = EXCLUDED.guild_id,
                        ticket_id = EXCLUDED.ticket_id,
                        thread_id = EXCLUDED.thread_id,
                        user_id = EXCLUDED.user_id,
                        created_at = NOW();
                    """,
                    (dm_message_id, guild_id, ticket_id, thread_id, user_id),
                )
        return True
    except Exception:
        logger.exception("Failed to save DM reply route for message %s.", dm_message_id)
        return False


def get_dm_route(dm_message_id: int, user_id: int) -> tuple[int, str, int] | None:
    if not DATABASE_URL:
        return None

    try:
        with connect_db(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT guild_id, ticket_id, thread_id
                    FROM labelutils_dm_routes
                    WHERE dm_message_id = %s AND user_id = %s;
                    """,
                    (dm_message_id, user_id),
                )
                return cur.fetchone()
    except Exception:
        logger.exception("Failed to fetch DM reply route for message %s.", dm_message_id)
        return None


def fetch_legacy_guild_brand(guild_id: int) -> dict[str, object] | None:
    if not DATABASE_URL:
        return None

    try:
        with connect_db(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT display_name, tagline, embed_color
                    FROM labelutils_guild_branding
                    WHERE guild_id = %s;
                    """,
                    (guild_id,),
                )
                row = cur.fetchone()
    except psycopg.errors.UndefinedTable:
        return None
    except Exception:
        logger.exception("Failed to fetch legacy guild branding for %s.", guild_id)
        return None

    return (
        {"display_name": row[0], "tagline": row[1], "embed_color": row[2]}
        if row
        else None
    )


def get_guild_brand(guild_id: int | None) -> dict[str, object] | None:
    if not guild_id or not guild_has_premium(guild_id):
        return None
    if guild_id in guild_brand_cache:
        return guild_brand_cache[guild_id]

    database_url = get_guild_database_url(guild_id)
    if not database_url or not ensure_submission_table(database_url):
        guild_brand_cache[guild_id] = None
        return None

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT display_name, tagline, embed_color
                    FROM labelutils_guild_branding
                    WHERE guild_id = %s;
                    """,
                    (guild_id,),
                )
                row = cur.fetchone()
    except Exception:
        logger.exception("Failed to fetch guild branding for %s.", guild_id)
        guild_brand_cache[guild_id] = None
        return None

    if not row:
        legacy_brand = fetch_legacy_guild_brand(guild_id)
        if legacy_brand:
            set_guild_brand(
                guild_id,
                0,
                str(legacy_brand.get("display_name") or ""),
                str(legacy_brand.get("tagline") or ""),
                int(legacy_brand.get("embed_color") or 0x5865F2),
            )
            return get_guild_brand(guild_id)

    brand = (
        {"display_name": row[0], "tagline": row[1], "embed_color": row[2]}
        if row
        else None
    )
    guild_brand_cache[guild_id] = brand
    return brand


def set_guild_brand(
    guild_id: int,
    updated_by: int,
    display_name: str,
    tagline: str,
    embed_color: int,
) -> bool:
    database_url = get_guild_database_url(guild_id)
    if not database_url:
        logger.warning("Guild database URL is missing; cannot save guild branding.")
        return False
    if not ensure_submission_table(database_url):
        return False

    safe_display_name = truncate_text(display_name.strip(), 80)
    safe_tagline = truncate_text(tagline.strip(), 180)
    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO labelutils_guild_branding (
                        guild_id, display_name, tagline, embed_color, updated_by, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, NOW()
                    )
                    ON CONFLICT (guild_id)
                    DO UPDATE SET
                        display_name = EXCLUDED.display_name,
                        tagline = EXCLUDED.tagline,
                        embed_color = EXCLUDED.embed_color,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = NOW();
                    """,
                    (guild_id, safe_display_name, safe_tagline, embed_color, updated_by),
                )
        guild_brand_cache[guild_id] = {
            "display_name": safe_display_name,
            "tagline": safe_tagline,
            "embed_color": embed_color,
        }
        return True
    except Exception:
        logger.exception("Failed to save guild branding for %s.", guild_id)
        return False


def reset_guild_brand(guild_id: int) -> bool:
    database_url = get_guild_database_url(guild_id)
    if not database_url:
        logger.warning("Guild database URL is missing; cannot reset guild branding.")
        return False
    if not ensure_submission_table(database_url):
        return False

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM labelutils_guild_branding WHERE guild_id = %s;",
                    (guild_id,),
                )
        guild_brand_cache.pop(guild_id, None)
        return True
    except Exception:
        logger.exception("Failed to reset guild branding for %s.", guild_id)
        return False


DEFAULT_PRO_SETTINGS: dict[str, object] = {
    "message_label": "Message to Label",
    "message_placeholder": "Share any extra notes about your track here...",
    "approval_template": (
        "Your track **{track_name}** has been approved by the team at **{team_name}**. "
        "We will reach out with further details soon."
    ),
    "rejection_template": (
        "Thank you for submitting **{track_name}** to **{team_name}**. "
        "After review, your track was not selected at this time.\n\nReason: {reason}"
    ),
    "cooldown_minutes": COOLDOWN_MINUTES,
    "max_submissions_per_user": 0,
    "duplicate_policy": "block",
    "approved_channel_id": 0,
    "rejected_channel_id": 0,
    "footer_text": "",
    "logo_url": "",
    "success_message": "Complete! Your submission has been logged. Ticket ID: `{ticket_id}`",
    "rejection_reasons": "",
    "digest_channel_id": 0,
    "ticket_channel_id": 0,
}


PRO_SETTINGS_KEYS = [
    "message_label", "message_placeholder", "approval_template", "rejection_template",
    "cooldown_minutes", "max_submissions_per_user", "duplicate_policy",
    "approved_channel_id", "rejected_channel_id", "footer_text", "logo_url",
    "success_message", "rejection_reasons", "digest_channel_id", "ticket_channel_id",
]


def fetch_legacy_pro_settings(guild_id: int) -> dict[str, object]:
    if not DATABASE_URL:
        return {}

    try:
        with connect_db(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        message_label, message_placeholder, approval_template, rejection_template,
                        cooldown_minutes, max_submissions_per_user, duplicate_policy,
                        approved_channel_id, rejected_channel_id, footer_text, logo_url,
                        success_message
                    FROM labelutils_pro_settings
                    WHERE guild_id = %s;
                    """,
                    (guild_id,),
                )
                row = cur.fetchone()
    except psycopg.errors.UndefinedTable:
        return {}
    except Exception:
        logger.exception("Failed to fetch legacy Pro settings for guild %s.", guild_id)
        return {}

    legacy_keys = PRO_SETTINGS_KEYS[:12]
    return {key: value for key, value in zip(legacy_keys, row or []) if value not in {None, ""}}


def get_pro_settings(guild_id: int | None) -> dict[str, object]:
    settings = dict(DEFAULT_PRO_SETTINGS)
    if not guild_id or not guild_has_premium(guild_id):
        return settings
    if guild_id in guild_pro_settings_cache:
        settings.update(guild_pro_settings_cache[guild_id])
        return settings

    database_url = get_guild_database_url(guild_id)
    if not database_url or not ensure_submission_table(database_url):
        return settings

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        message_label, message_placeholder, approval_template, rejection_template,
                        cooldown_minutes, max_submissions_per_user, duplicate_policy,
                        approved_channel_id, rejected_channel_id, footer_text, logo_url,
                        success_message, rejection_reasons, digest_channel_id, ticket_channel_id
                    FROM labelutils_pro_settings
                    WHERE guild_id = %s;
                    """,
                    (guild_id,),
                )
                row = cur.fetchone()
    except Exception:
        logger.exception("Failed to fetch Pro settings for guild %s.", guild_id)
        return settings

    if row:
        loaded = {key: value for key, value in zip(PRO_SETTINGS_KEYS, row) if value not in {None, ""}}
        guild_pro_settings_cache[guild_id] = loaded
        settings.update(loaded)
    else:
        legacy_settings = fetch_legacy_pro_settings(guild_id)
        if legacy_settings:
            upsert_pro_settings(guild_id, 0, **legacy_settings)
            guild_pro_settings_cache[guild_id] = legacy_settings
            settings.update(legacy_settings)
    return settings


def cached_submission_form_settings(guild_id: int | None) -> dict[str, object]:
    settings = dict(DEFAULT_PRO_SETTINGS)
    if guild_id and guild_id in guild_pro_settings_cache:
        settings.update(guild_pro_settings_cache[guild_id])
    return settings


def upsert_pro_settings(guild_id: int, updated_by: int, **values: object) -> bool:
    database_url = get_guild_database_url(guild_id)
    if not database_url:
        logger.warning("Guild database URL is missing; cannot save Pro settings.")
        return False
    if not ensure_submission_table(database_url):
        return False

    allowed = {
        "message_label", "message_placeholder", "approval_template", "rejection_template",
        "cooldown_minutes", "max_submissions_per_user", "duplicate_policy",
        "approved_channel_id", "rejected_channel_id", "footer_text", "logo_url",
        "success_message", "rejection_reasons", "digest_channel_id", "ticket_channel_id",
    }
    updates = {key: value for key, value in values.items() if key in allowed}
    if not updates:
        return True

    columns = ["guild_id", *updates.keys(), "updated_by", "updated_at"]
    placeholders = ["%s", *["%s" for _ in updates], "%s", "NOW()"]
    update_sql = ", ".join(f"{key} = EXCLUDED.{key}" for key in updates)
    params = [guild_id, *updates.values(), updated_by]

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO labelutils_pro_settings ({", ".join(columns)})
                    VALUES ({", ".join(placeholders)})
                    ON CONFLICT (guild_id)
                    DO UPDATE SET
                        {update_sql},
                        updated_by = EXCLUDED.updated_by,
                        updated_at = NOW();
                    """,
                    params,
                )
        guild_pro_settings_cache.pop(guild_id, None)
        return True
    except Exception:
        logger.exception("Failed to save Pro settings for guild %s.", guild_id)
        return False


def format_template(template: object, **values: object) -> str:
    try:
        return str(template).format(**values)
    except Exception:
        logger.exception("Failed to format Pro template.")
        return str(template)


def format_rejection_template(template: object, reason: str, **values: object) -> str:
    text = format_template(template, reason=reason, **values)
    if "{reason}" not in str(template) and reason.strip():
        text = f"{text}\n\nReason: {reason.strip()}"
    return text


def parse_rejection_reasons(settings: dict[str, object]) -> list[str]:
    raw = str(settings.get("rejection_reasons") or "")
    reasons = [reason.strip() for reason in raw.split("|") if reason.strip()]
    return reasons[:10]


def save_submission_to_neon(
    database_url: str | None,
    ticket_id: str,
    name: str,
    discord_username: str,
    track_name: str,
    track_link: str,
    artist_names: str,
    message: str,
    user_id: int,
) -> bool:
    if not database_url:
        logger.warning("Guild database URL is missing; submission will only be logged in Discord.")
        return False

    current_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO label_submissions (
                        ticket_id, user_id, name, discord_username, track_name, track_link,
                        artist_names, message, status, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s
                    );
                    """,
                    (
                        ticket_id,
                        user_id,
                        name,
                        discord_username,
                        track_name,
                        track_link,
                        artist_names,
                        f"[User ID: {user_id}] {message}",
                        "In Queue",
                        current_timestamp,
                    ),
                )
        return True
    except Exception:
        logger.exception("Failed to insert submission into Neon.")
        return False


def find_duplicate_submission(database_url: str | None, track_link: str) -> tuple | None:
    if not database_url:
        return None

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ticket_id, track_name, status, created_at
                    FROM label_submissions
                    WHERE lower(track_link) = lower(%s)
                    ORDER BY created_at DESC
                    LIMIT 1;
                    """,
                    (track_link.strip(),),
                )
                return cur.fetchone()
    except Exception:
        logger.exception("Failed to check duplicate submission.")
        return None


def fetch_user_submissions(database_url: str | None, user_id: int, limit: int = 10) -> list[tuple]:
    if not database_url:
        return []

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ticket_id, track_name, status, created_at
                    FROM label_submissions
                    WHERE user_id = %s OR message LIKE %s
                    ORDER BY created_at DESC
                    LIMIT %s;
                    """,
                    (user_id, f"[User ID: {user_id}]%", limit),
                )
                return cur.fetchall()
    except Exception:
        logger.exception("Failed to fetch user submissions.")
        return []


def fetch_submission_by_ticket(database_url: str | None, ticket_id: str) -> tuple | None:
    if not database_url:
        return None

    normalized_ticket_id = normalize_ticket_id(ticket_id)
    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        ticket_id, user_id, name, discord_username, track_name, track_link,
                        artist_names, message, status, created_at, reviewer_id, staff_notes,
                        rating, shortlisted, priority
                    FROM label_submissions
                    WHERE upper(ticket_id) = %s;
                    """,
                    (normalized_ticket_id,),
                )
                return cur.fetchone()
    except Exception:
        logger.exception("Failed to fetch submission ticket %s.", normalized_ticket_id)
        return None


def update_submission_flags(
    database_url: str | None,
    ticket_id: str,
    *,
    shortlisted: bool | None = None,
    priority: bool | None = None,
    rating: int | None = None,
) -> bool:
    if not database_url:
        return False

    normalized_ticket_id = normalize_ticket_id(ticket_id)
    updates = {}
    if shortlisted is not None:
        updates["shortlisted"] = shortlisted
    if priority is not None:
        updates["priority"] = priority
    if rating is not None:
        updates["rating"] = max(1, min(rating, 10))
    if not updates:
        return True

    set_sql = ", ".join(f"{column} = %s" for column in updates)
    params = [*updates.values(), normalized_ticket_id]
    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE label_submissions SET {set_sql} WHERE upper(ticket_id) = %s;",
                    params,
                )
                return cur.rowcount > 0
    except Exception:
        logger.exception("Failed to update workflow flags for %s.", normalized_ticket_id)
        return False


def fetch_shortlisted_submissions(database_url: str | None, limit: int = 10) -> list[tuple]:
    if not database_url:
        return []

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ticket_id, name, track_name, status, created_at
                    FROM label_submissions
                    WHERE shortlisted = TRUE
                    ORDER BY priority DESC, rating DESC NULLS LAST, created_at DESC
                    LIMIT %s;
                    """,
                    (limit,),
                )
                return cur.fetchall()
    except Exception:
        logger.exception("Failed to fetch shortlisted submissions.")
        return []


def fetch_weekly_digest_stats(database_url: str | None) -> dict[str, int | float]:
    if not database_url:
        return {}

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'),
                        COUNT(*) FILTER (WHERE lower(status) = 'approved' AND created_at >= NOW() - INTERVAL '7 days'),
                        COUNT(*) FILTER (WHERE lower(status) = 'rejected' AND created_at >= NOW() - INTERVAL '7 days'),
                        COUNT(*) FILTER (WHERE lower(status) IN ('in queue', 'needs review', 'shortlisted')),
                        COUNT(*) FILTER (WHERE shortlisted = TRUE),
                        COUNT(*) FILTER (WHERE priority = TRUE),
                        ROUND(AVG(rating)::numeric, 1)
                    FROM label_submissions;
                    """
                )
                row = cur.fetchone()
    except Exception:
        logger.exception("Failed to fetch weekly digest stats.")
        return {}

    if not row:
        return {}
    return {
        "new": int(row[0] or 0),
        "approved": int(row[1] or 0),
        "rejected": int(row[2] or 0),
        "pending": int(row[3] or 0),
        "shortlisted": int(row[4] or 0),
        "priority": int(row[5] or 0),
        "avg_rating": float(row[6] or 0),
    }


def fetch_user_submission_stats(database_url: str | None, user_id: int) -> tuple[int, int, int, int]:
    if not database_url:
        return (0, 0, 0, 0)

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*),
                        COUNT(*) FILTER (WHERE lower(status) = 'approved'),
                        COUNT(*) FILTER (WHERE lower(status) = 'rejected'),
                        COUNT(*) FILTER (WHERE lower(status) = 'in queue')
                    FROM label_submissions
                    WHERE user_id = %s OR message LIKE %s;
                    """,
                    (user_id, f"[User ID: {user_id}]%"),
                )
                row = cur.fetchone()
                if not row:
                    return (0, 0, 0, 0)
                return tuple(int(value or 0) for value in row)
    except Exception:
        logger.exception("Failed to fetch user submission stats.")
        return (0, 0, 0, 0)


def count_user_submissions(database_url: str | None, user_id: int) -> int:
    total, _approved, _rejected, _in_queue = fetch_user_submission_stats(database_url, user_id)
    return total


def append_staff_note(database_url: str | None, ticket_id: str, note: str, staff_name: str) -> bool:
    if not database_url:
        return False

    normalized_ticket_id = normalize_ticket_id(ticket_id)
    timestamp = discord_timestamp(datetime.now(timezone.utc))
    entry = f"[{timestamp} by {staff_name}] {note.strip()}"
    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE label_submissions
                    SET staff_notes = trim(concat_ws(E'\n', NULLIF(staff_notes, ''), %s))
                    WHERE upper(ticket_id) = %s;
                    """,
                    (entry, normalized_ticket_id),
                )
                return cur.rowcount > 0
    except Exception:
        logger.exception("Failed to append staff note for %s.", normalized_ticket_id)
        return False


def assign_reviewer(database_url: str | None, ticket_id: str, reviewer_id: int) -> bool:
    if not database_url:
        return False

    normalized_ticket_id = normalize_ticket_id(ticket_id)
    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE label_submissions SET reviewer_id = %s WHERE upper(ticket_id) = %s;",
                    (reviewer_id, normalized_ticket_id),
                )
                return cur.rowcount > 0
    except Exception:
        logger.exception("Failed to assign reviewer for %s.", normalized_ticket_id)
        return False


def fetch_submission_analytics(database_url: str | None) -> tuple[int, int, int, int]:
    if not database_url:
        return (0, 0, 0, 0)

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*),
                        COUNT(*) FILTER (WHERE lower(status) = 'approved'),
                        COUNT(*) FILTER (WHERE lower(status) = 'rejected'),
                        COUNT(*) FILTER (WHERE lower(status) = 'in queue')
                    FROM label_submissions;
                    """
                )
                row = cur.fetchone()
                return tuple(int(value or 0) for value in row) if row else (0, 0, 0, 0)
    except Exception:
        logger.exception("Failed to fetch analytics.")
        return (0, 0, 0, 0)


def fetch_submissions_for_export(database_url: str | None) -> list[tuple]:
    if not database_url:
        return []

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        ticket_id, user_id, name, discord_username, track_name, track_link,
                        artist_names, message, status, created_at, reviewer_id, staff_notes,
                        rating, shortlisted, priority
                    FROM label_submissions
                    ORDER BY created_at DESC;
                    """
                )
                return cur.fetchall()
    except Exception:
        logger.exception("Failed to fetch submissions for export.")
        return []


def fetch_accepted_leaderboard(
    database_url: str | None,
    limit: int = LEADERBOARD_PAGE_SIZE,
    offset: int = 0,
) -> list[tuple]:
    if not database_url:
        return []

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COALESCE(NULLIF(discord_username, ''), name) AS submitter_name,
                        COUNT(*) AS approved_count
                    FROM label_submissions
                    WHERE lower(status) = 'approved'
                    GROUP BY submitter_name
                    ORDER BY approved_count DESC, submitter_name ASC
                    LIMIT %s OFFSET %s;
                    """,
                    (limit, offset),
                )
                return cur.fetchall()
    except Exception:
        logger.exception("Failed to fetch accepted leaderboard.")
        return []


def count_accepted_leaderboard_entries(database_url: str | None) -> int:
    if not database_url:
        return 0

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM (
                        SELECT COALESCE(NULLIF(discord_username, ''), name) AS submitter_name
                        FROM label_submissions
                        WHERE lower(status) = 'approved'
                        GROUP BY submitter_name
                    ) AS leaderboard;
                    """
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
    except Exception:
        logger.exception("Failed to count accepted leaderboard entries.")
        return 0


def update_submission_status(database_url: str | None, ticket_id: str, new_status: str) -> bool:
    if not database_url:
        logger.warning("Guild database URL is missing; cannot update ticket %s.", ticket_id)
        return False

    normalized_ticket_id = normalize_ticket_id(ticket_id)
    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE label_submissions SET status = %s WHERE upper(ticket_id) = %s;",
                    (new_status, normalized_ticket_id),
                )
                return cur.rowcount > 0
    except Exception:
        logger.exception("Failed to update submission status for %s.", normalized_ticket_id)
        return False


def fetch_submissions(database_url: str | None, status: str | None = None, limit: int = 5) -> list[tuple]:
    if not database_url:
        return []

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                if status:
                    cur.execute(
                        """
                        SELECT ticket_id, name, track_name, status, created_at
                        FROM label_submissions
                        WHERE status = %s
                        ORDER BY priority DESC, created_at DESC
                        LIMIT %s;
                        """,
                        (status, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT ticket_id, name, track_name, status, created_at
                        FROM label_submissions
                        ORDER BY priority DESC, created_at DESC
                        LIMIT %s;
                        """,
                        (limit,),
                    )
                return cur.fetchall()
    except Exception:
        logger.exception("Failed to fetch submissions.")
        return []


def fetch_panel_submissions(
    database_url: str | None,
    status: str | None = None,
    limit: int = PANEL_PAGE_SIZE,
    offset: int = 0,
    sort_order: str = "newest",
) -> list[tuple]:
    if not database_url:
        return []

    order_direction = "ASC" if sort_order == "oldest" else "DESC"
    order_sql = f"priority DESC, created_at {order_direction}"

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                if status:
                    cur.execute(
                        f"""
                        SELECT
                            ticket_id, name, discord_username, track_name, track_link,
                            artist_names, message, status, created_at
                        FROM label_submissions
                        WHERE status = %s
                        ORDER BY {order_sql}
                        LIMIT %s OFFSET %s;
                        """,
                        (status, limit, offset),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT
                            ticket_id, name, discord_username, track_name, track_link,
                            artist_names, message, status, created_at
                        FROM label_submissions
                        ORDER BY {order_sql}
                        LIMIT %s OFFSET %s;
                        """,
                        (limit, offset),
                    )
                return cur.fetchall()
    except Exception:
        logger.exception("Failed to fetch panel submissions.")
        return []


def count_submissions(database_url: str | None, status: str | None = None) -> int:
    if not database_url:
        return 0

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                if status:
                    cur.execute(
                        "SELECT COUNT(*) FROM label_submissions WHERE status = %s;",
                        (status,),
                    )
                else:
                    cur.execute("SELECT COUNT(*) FROM label_submissions;")
                row = cur.fetchone()
                return int(row[0]) if row else 0
    except Exception:
        logger.exception("Failed to count submissions.")
        return 0


def create_support_ticket(
    database_url: str | None,
    ticket_id: str,
    user_id: int,
    username: str,
    subject: str,
    message: str,
) -> bool:
    if not database_url:
        return False

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO labelutils_support_tickets (
                        ticket_id, user_id, username, subject, message, status, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, 'Open', NOW(), NOW()
                    );
                    """,
                    (ticket_id, user_id, username, subject, message),
                )
        return True
    except Exception:
        logger.exception("Failed to create support ticket %s.", ticket_id)
        return False


def set_support_ticket_thread(database_url: str | None, ticket_id: str, thread_id: int) -> bool:
    if not database_url:
        return False

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE labelutils_support_tickets
                    SET thread_id = %s, updated_at = NOW()
                    WHERE ticket_id = %s;
                    """,
                    (thread_id, ticket_id),
                )
                return cur.rowcount > 0
    except Exception:
        logger.exception("Failed to save support ticket thread for %s.", ticket_id)
        return False


def update_support_ticket_status(database_url: str | None, ticket_id: str, status: str) -> bool:
    if not database_url:
        return False

    normalized_ticket_id = normalize_ticket_id(ticket_id)
    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE labelutils_support_tickets
                    SET status = %s, updated_at = NOW()
                    WHERE upper(ticket_id) = %s;
                    """,
                    (status, normalized_ticket_id),
                )
                return cur.rowcount > 0
    except Exception:
        logger.exception("Failed to update support ticket %s.", normalized_ticket_id)
        return False


def fetch_support_tickets(database_url: str | None, status: str | None = None, limit: int = 10) -> list[tuple]:
    if not database_url:
        return []

    try:
        with connect_db(database_url) as conn:
            with conn.cursor() as cur:
                if status:
                    cur.execute(
                        """
                        SELECT ticket_id, username, subject, status, created_at
                        FROM labelutils_support_tickets
                        WHERE status = %s
                        ORDER BY created_at DESC
                        LIMIT %s;
                        """,
                        (status, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT ticket_id, username, subject, status, created_at
                        FROM labelutils_support_tickets
                        ORDER BY created_at DESC
                        LIMIT %s;
                        """,
                        (limit,),
                    )
                return cur.fetchall()
    except Exception:
        logger.exception("Failed to fetch support tickets.")
        return []


def embed_field(embed: discord.Embed, name: str, default: str = "") -> str:
    for field in embed.fields:
        if field.name == name:
            return str(field.value)
    return default


def submission_info_from_message(message: discord.Message) -> tuple[str, int, str]:
    embed = message.embeds[0]
    ticket_id = embed_field(embed, "Ticket ID")
    user_id = int(embed_field(embed, "User ID", "0"))
    track_name = embed_field(embed, "Track Name", "your track")
    return ticket_id, user_id, track_name


def set_embed_field(embed: discord.Embed, name: str, value: str, inline: bool = False) -> None:
    for index, field in enumerate(embed.fields):
        if field.name == name:
            embed.set_field_at(index, name=name, value=value, inline=inline)
            return
    embed.add_field(name=name, value=value, inline=inline)


def support_ticket_info_from_message(message: discord.Message) -> tuple[str, int, str]:
    embed = message.embeds[0]
    ticket_id = embed_field(embed, "Ticket ID")
    user_id = int(embed_field(embed, "User ID", "0"))
    subject = embed.description or embed_field(embed, "Subject", "support ticket")
    return ticket_id, user_id, subject


def submission_thread_id_from_message(message: discord.Message) -> int | None:
    if not message.embeds:
        return None
    thread_id = embed_field(message.embeds[0], "Thread ID")
    return int(thread_id) if thread_id.isdigit() else None


async def log_submission_thread(
    message: discord.Message,
    content: str,
    *,
    embed: discord.Embed | None = None,
) -> None:
    thread_id = submission_thread_id_from_message(message)
    if not thread_id:
        return

    thread = client.get_channel(thread_id)
    if not thread:
        try:
            thread = await client.fetch_channel(thread_id)
        except Exception:
            logger.exception("Failed to fetch submission thread %s.", thread_id)
            return

    try:
        await thread.send(content=content, embed=embed)
    except Exception:
        logger.exception("Failed to log to submission thread %s.", thread_id)


def format_dm_reply_attachments(attachments: list[discord.Attachment]) -> str:
    if not attachments:
        return ""

    lines = []
    for attachment in attachments[:10]:
        size_mb = attachment.size / (1024 * 1024) if attachment.size else 0
        details = f"{attachment.filename}"
        if attachment.content_type:
            details = f"{details} ({attachment.content_type})"
        if attachment.size:
            details = f"{details}, {size_mb:.2f} MB"
        lines.append(f"- [{details}]({attachment.url})")

    if len(attachments) > 10:
        lines.append(f"- plus {len(attachments) - 10} more attachment(s)")

    return "\n".join(lines)


async def forward_dm_reply_to_thread(message: discord.Message) -> bool:
    if message.author.bot or message.guild is not None:
        return False
    if not message.reference or not message.reference.message_id:
        return False

    route = get_dm_route(message.reference.message_id, message.author.id)
    if not route:
        return False

    guild_id, ticket_id, thread_id = route
    if not guild_has_premium(guild_id):
        return False

    thread = client.get_channel(thread_id)
    if not thread:
        try:
            thread = await client.fetch_channel(thread_id)
        except Exception:
            logger.exception("Failed to fetch routed DM reply thread %s.", thread_id)
            return False

    attachments_text = format_dm_reply_attachments(list(message.attachments))
    body = truncate_text(message.content, 1800) if message.content else ""
    if not body and not attachments_text:
        body = "Artist replied with an empty message."

    embed = discord.Embed(
        title="User DM Reply",
        description=body or None,
        color=0x5865F2,
    )
    embed.add_field(name="Ticket", value=f"`{ticket_id}`", inline=True)
    embed.add_field(name="User", value=message.author.mention, inline=True)
    embed.add_field(name="Received", value=discord_timestamp(message.created_at), inline=True)
    if attachments_text:
        embed.add_field(name="Attachments", value=truncate_text(attachments_text, 1000), inline=False)
    embed.set_footer(text=f"Source DM reply ID: {message.id} | Guild: {guild_id}")

    try:
        await thread.send(
            content=f"Reply from {message.author.mention} for `{ticket_id}`:",
            embed=embed,
        )
        return True
    except Exception:
        logger.exception("Failed to forward DM reply %s to thread %s.", message.id, thread_id)
        return False


async def notify_artist(artist_id: int, embed: discord.Embed) -> bool:
    try:
        artist = await client.fetch_user(artist_id)
        if not artist:
            return False
        await artist.send(embed=embed)
        return True
    except Exception:
        logger.exception("Could not DM user %s.", artist_id)
        return False


async def dm_artist_text(artist_id: int, content: str) -> discord.Message | None:
    try:
        artist = await client.fetch_user(artist_id)
        if not artist:
            return None
        return await artist.send(content)
    except Exception:
        logger.exception("Could not send staff DM to user %s.", artist_id)
        return None


async def send_status_route(
    interaction: discord.Interaction,
    ticket_id: str,
    track_name: str,
    status_value: str,
) -> None:
    settings = get_pro_settings(interaction.guild_id)
    key = "approved_channel_id" if status_value == "Approved" else "rejected_channel_id"
    channel_id = int(settings.get(key) or 0)
    if not channel_id:
        return

    channel = client.get_channel(channel_id)
    if not channel and interaction.guild:
        channel = interaction.guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        embed = discord.Embed(
            title=f"Submission {status_value}",
            description=f"**{truncate_text(track_name, 180)}**\nTicket: `{ticket_id}`",
            color=0x43B581 if status_value == "Approved" else 0xF04747,
        )
        await channel.send(embed=embed)
    except Exception:
        logger.exception("Failed to send %s route message for %s.", status_value, ticket_id)


def submission_decision_view(guild_id: int | None, *, final_decision: bool = False) -> discord.ui.View:
    view = ProDecisionButtonsView() if guild_has_premium(guild_id) else DecisionButtonsView()
    if final_decision:
        for item in view.children:
            if getattr(item, "custom_id", "") in {"submission:approve", "submission:reject"}:
                item.disabled = True
    return view


def status_color(status_value: str, default: int = 0x5865F2) -> int:
    return {
        "In Queue": 0x5865F2,
        "Needs Review": 0xF1C40F,
        "Shortlisted": 0x9B59B6,
        "Processed": 0x3498DB,
        "Contacted": 0x1ABC9C,
        "Signed": 0x2ECC71,
        "Approved": 0x43B581,
        "Rejected": 0xF04747,
    }.get(status_value, default)


def embed_field_value(embed: discord.Embed, field_name: str) -> str | None:
    for field in embed.fields:
        if field.name == field_name:
            return str(field.value)
    return None


def user_can_manage_submissions(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(
        permissions
        and (
            permissions.manage_messages
            or permissions.manage_channels
            or permissions.administrator
        )
    )


def user_is_admin(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


def truncate_text(value: object, limit: int = 900) -> str:
    text = str(value or "").strip()
    if not text:
        return "None"
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def clean_submission_message(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith("[User ID:"):
        closing = text.find("]")
        if closing != -1:
            return text[closing + 1 :].strip() or "None"
    return text or "None"


def server_display_name(interaction: discord.Interaction) -> str:
    brand = get_guild_brand(interaction.guild_id)
    if brand and brand.get("display_name"):
        return str(brand["display_name"])
    if interaction.guild and interaction.guild.name:
        return interaction.guild.name
    return "this server"


def server_embed_color(interaction: discord.Interaction, default: int = 0x5865F2) -> int:
    brand = get_guild_brand(interaction.guild_id)
    if brand and brand.get("embed_color") is not None:
        return int(brand["embed_color"])
    return default


def server_tagline(interaction: discord.Interaction) -> str | None:
    brand = get_guild_brand(interaction.guild_id)
    if brand and brand.get("tagline"):
        return str(brand["tagline"])
    return None


async def set_bot_server_nickname(
    interaction: discord.Interaction,
    display_name: str | None,
) -> str:
    if not interaction.guild or not interaction.guild.me:
        return "Nickname not changed: server member info was unavailable."

    try:
        nickname = truncate_text(display_name, 32) if display_name else None
        await interaction.guild.me.edit(
            nick=nickname,
            reason=f"LabelUtils branding updated by {interaction.user}",
        )
        return (
            f"Bot nickname changed to `{nickname}`."
            if nickname
            else "Bot nickname reset to default."
        )
    except discord.Forbidden:
        return (
            "Bot nickname not changed: give the bot Change Nickname or Manage Nicknames, "
            "and keep its role above the bot member."
        )
    except Exception:
        logger.exception("Failed to update bot server nickname for guild %s.", interaction.guild_id)
        return "Bot nickname not changed because Discord returned an error."


class LabelUtilsClient(discord.Client):
    async def setup_hook(self) -> None:
        self.add_view(ProDecisionButtonsView())
        self.add_view(SubmitPanelView())
        self.add_view(SupportTicketPanelView())
        self.add_view(SupportTicketButtonsView())
        command_names = ", ".join(command.name for command in tree.get_commands())
        logger.info("Registering slash command(s): %s", command_names)
        if DISCORD_GUILD_ID:
            guild = discord.Object(id=DISCORD_GUILD_ID)
            tree.copy_global_to(guild=guild)
            synced = await tree.sync(guild=guild)
            logger.info("Synced %s guild slash command(s) to %s.", len(synced), DISCORD_GUILD_ID)
            if CLEAR_GLOBAL_COMMANDS:
                tree.clear_commands(guild=None)
                cleared = await tree.sync()
                logger.info("Cleared global slash command(s); %s global command(s) remain.", len(cleared))
        else:
            logger.warning("DISCORD_GUILD_ID is missing. Global slash command updates can take up to 1 hour.")
            synced = await tree.sync()
            logger.info("Synced %s global slash command(s).", len(synced))


client = LabelUtilsClient(intents=intents)
tree = app_commands.CommandTree(client)


@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    logger.exception(
        "Slash command %s failed.",
        getattr(getattr(interaction, "command", None), "name", "unknown"),
        exc_info=error,
    )
    message = "That command failed inside the bot. Check the host logs for details."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.NotFound:
        logger.warning("Could not send slash-command error response because the interaction expired.")
    except Exception:
        logger.exception("Could not send slash-command error response.")


class RejectReasonModal(discord.ui.Modal, title="Reject Submission"):
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Short reason to send to the artist",
        style=discord.TextStyle.paragraph,
        max_length=600,
        required=True,
    )

    def __init__(self, staff_message: discord.Message, guild_id: int | None = None):
        super().__init__()
        self.staff_message = staff_message
        self.saved_reasons = parse_rejection_reasons(get_pro_settings(guild_id))
        if self.saved_reasons:
            preview = "; ".join(f"{index}. {reason}" for index, reason in enumerate(self.saved_reasons[:3], start=1))
            self.reason.placeholder = truncate_text(
                f"Type 1-{len(self.saved_reasons)} for saved reason, or write custom. {preview}",
                100,
            )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        ticket_id, artist_id, track_name = submission_info_from_message(self.staff_message)
        team_name = server_display_name(interaction)
        settings = get_pro_settings(interaction.guild_id)
        database_url = get_guild_database_url(interaction.guild_id)
        db_updated = update_submission_status(database_url, ticket_id, "Rejected")
        reason_text = self.reason.value.strip()
        if reason_text.isdigit():
            reason_index = int(reason_text) - 1
            if 0 <= reason_index < len(self.saved_reasons):
                reason_text = self.saved_reasons[reason_index]

        dm_embed = discord.Embed(
            title="Submission Update",
            description=format_rejection_template(
                settings.get("rejection_template"),
                reason=reason_text,
                track_name=track_name,
                team_name=team_name,
                ticket_id=ticket_id,
            ),
            color=0xF04747,
        )
        dm_sent = await notify_artist(artist_id, dm_embed)
        dm_status = "Artist notified by DM" if dm_sent else "DM failed or unavailable"
        db_status = "DB updated" if db_updated else "DB update failed"

        view = submission_decision_view(interaction.guild_id, final_decision=True)
        old_embed = self.staff_message.embeds[0]
        old_embed.color = status_color("Rejected")
        set_embed_field(old_embed, "Status", "Rejected", inline=True)
        set_embed_field(old_embed, "Rejection Reason", reason_text, inline=False)
        old_embed.set_footer(
            text=f"Rejected by @{interaction.user.name} | {dm_status} | {db_status}"
        )

        await self.staff_message.edit(embed=old_embed, view=view)
        await send_status_route(interaction, ticket_id, track_name, "Rejected")
        await log_submission_thread(
            self.staff_message,
            (
                f"Release log: rejected by {interaction.user.mention}.\n"
                f"Reason: {reason_text}\n"
                f"{dm_status} | {db_status}"
            ),
        )
        await interaction.followup.send("Rejected and processed.", ephemeral=True)


class StaffDmModal(discord.ui.Modal, title="DM Artist"):
    message = discord.ui.TextInput(
        label="Message",
        placeholder="Write the message to send to the artist.",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        required=True,
    )

    def __init__(self, staff_message: discord.Message):
        super().__init__()
        self.staff_message = staff_message

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        ticket_id, artist_id, track_name = submission_info_from_message(self.staff_message)
        team_name = server_display_name(interaction)
        reply_bridge_enabled = guild_has_premium(interaction.guild_id)
        content = (
            f"Message from **{team_name}** about your submission "
            f"**{track_name}** (`{ticket_id}`):\n\n{self.message.value}"
        )
        if reply_bridge_enabled:
            content = (
                f"{content}\n\n"
                "To reply to the team, use Discord's Reply action on this message."
            )
        dm_message = await dm_artist_text(artist_id, content)
        dm_sent = bool(dm_message)
        thread_id = submission_thread_id_from_message(self.staff_message)
        route_saved = (
            save_dm_route(dm_message.id, interaction.guild_id, ticket_id, thread_id, artist_id)
            if reply_bridge_enabled and dm_message and interaction.guild_id and thread_id
            else False
        )
        route_status = "enabled" if route_saved else "premium required"
        if reply_bridge_enabled and dm_sent and not route_saved:
            route_status = "not enabled"
        elif not dm_sent:
            route_status = "not enabled"
        await log_submission_thread(
            self.staff_message,
            (
                f"Release log: staff DM attempted by {interaction.user.mention}.\n"
                f"DM status: {'sent' if dm_sent else 'failed'}\n"
                f"Reply route: {route_status}\n"
                f"Message: {truncate_text(self.message.value, 1200)}"
            ),
        )
        if dm_sent:
            reply_note = " Artist replies will appear in this thread." if route_saved else ""
            await interaction.followup.send(
                f"DM sent to the artist. Status was not changed.{reply_note}",
                ephemeral=True,
            )
        else:
            await interaction.followup.send("Could not DM that artist. Status was not changed.", ephemeral=True)


class DecisionButtonsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, custom_id="submission:approve")
    async def approve_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not user_can_manage_submissions(interaction):
            await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        ticket_id, artist_id, track_name = submission_info_from_message(interaction.message)
        team_name = server_display_name(interaction)
        settings = get_pro_settings(interaction.guild_id)
        database_url = get_guild_database_url(interaction.guild_id)
        db_updated = update_submission_status(database_url, ticket_id, "Approved")

        dm_embed = discord.Embed(
            title="Submission Approved",
            description=format_template(
                settings.get("approval_template"),
                track_name=track_name,
                team_name=team_name,
                ticket_id=ticket_id,
            ),
            color=0x43B581,
        )
        dm_sent = await notify_artist(artist_id, dm_embed)
        dm_status = "Artist notified by DM" if dm_sent else "DM failed or unavailable"
        db_status = "DB updated" if db_updated else "DB update failed"

        view = submission_decision_view(interaction.guild_id, final_decision=True)
        old_embed = interaction.message.embeds[0]
        old_embed.color = status_color("Approved")
        set_embed_field(old_embed, "Status", "Approved", inline=True)
        old_embed.set_footer(
            text=f"Approved by @{interaction.user.name} | {dm_status} | {db_status}"
        )

        await interaction.message.edit(embed=old_embed, view=view)
        await send_status_route(interaction, ticket_id, track_name, "Approved")
        await log_submission_thread(
            interaction.message,
            f"Release log: approved by {interaction.user.mention}.\n{dm_status} | {db_status}",
        )
        await interaction.followup.send("Approved and processed.", ephemeral=True)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.red, custom_id="submission:reject")
    async def reject_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not user_can_manage_submissions(interaction):
            await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
            return

        await interaction.response.send_modal(RejectReasonModal(interaction.message, interaction.guild_id))

    @discord.ui.button(label="DM", style=discord.ButtonStyle.blurple, custom_id="submission:dm")
    async def dm_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not user_can_manage_submissions(interaction):
            await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
            return

        await interaction.response.send_modal(StaffDmModal(interaction.message))


class ProSubmissionStatusButton(discord.ui.Button):
    def __init__(self, status_value: str, label: str, row: int):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"submission:status:{status_value.lower().replace(' ', '_')}",
            row=row,
        )
        self.status_value = status_value

    async def callback(self, interaction: discord.Interaction):
        if not user_can_manage_submissions(interaction):
            await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
            return
        if not guild_has_premium(interaction.guild_id):
            await interaction.response.send_message("Quick status buttons are a Pro feature.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        ticket_id, _artist_id, track_name = submission_info_from_message(interaction.message)
        database_url = get_guild_database_url(interaction.guild_id)
        updated = update_submission_status(database_url, ticket_id, self.status_value)
        if updated and interaction.message.embeds:
            embed = interaction.message.embeds[0]
            was_final = embed_field_value(embed, "Status") in {"Approved", "Rejected"}
            set_embed_field(embed, "Status", self.status_value, inline=True)
            embed.color = status_color(self.status_value)
            embed.set_footer(text=f"{self.status_value} by @{interaction.user.name} | DB updated")
            await interaction.message.edit(
                embed=embed,
                view=submission_decision_view(interaction.guild_id, final_decision=was_final),
            )
            await log_submission_thread(
                interaction.message,
                f"Release log: marked **{self.status_value}** by {interaction.user.mention}.",
            )
        await interaction.followup.send(
            f"`{ticket_id}` marked **{self.status_value}**."
            if updated
            else f"Could not update `{ticket_id}`.",
            ephemeral=True,
        )


class ProDecisionButtonsView(DecisionButtonsView):
    def __init__(self):
        super().__init__()
        self.add_item(ProSubmissionStatusButton("Needs Review", "Review", row=1))
        self.add_item(ProSubmissionStatusButton("Shortlisted", "Shortlist", row=1))
        self.add_item(ProSubmissionStatusButton("Processed", "Processed", row=1))
        self.add_item(ProSubmissionStatusButton("Contacted", "Contacted", row=2))
        self.add_item(ProSubmissionStatusButton("Signed", "Signed", row=2))


class SupportTicketDmModal(discord.ui.Modal, title="DM Ticket User"):
    message = discord.ui.TextInput(
        label="Message",
        placeholder="Write the message to send to the ticket opener.",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        required=True,
    )

    def __init__(self, ticket_message: discord.Message):
        super().__init__()
        self.ticket_message = ticket_message

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        ticket_id, user_id, subject = support_ticket_info_from_message(self.ticket_message)
        content = (
            f"Message from **{server_display_name(interaction)}** about your ticket "
            f"**{subject}** (`{ticket_id}`):\n\n{self.message.value}\n\n"
            "To reply to staff, use Discord's Reply action on this message."
        )
        dm_message = await dm_artist_text(user_id, content)
        thread_id = submission_thread_id_from_message(self.ticket_message)
        route_saved = (
            save_dm_route(dm_message.id, interaction.guild_id, ticket_id, thread_id, user_id)
            if dm_message and interaction.guild_id and thread_id
            else False
        )
        await log_submission_thread(
            self.ticket_message,
            (
                f"Ticket log: staff DM attempted by {interaction.user.mention}.\n"
                f"DM status: {'sent' if dm_message else 'failed'}\n"
                f"Reply route: {'enabled' if route_saved else 'not enabled'}\n"
                f"Message: {truncate_text(self.message.value, 1200)}"
            ),
        )
        await interaction.followup.send(
            "DM sent. User replies will appear in this ticket thread."
            if dm_message and route_saved
            else "Could not fully enable the DM route. Check that the user allows DMs and the ticket has a thread.",
            ephemeral=True,
        )


class SupportTicketButtonsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Resolved", style=discord.ButtonStyle.green, custom_id="support_ticket:resolve")
    async def resolve_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not user_can_manage_submissions(interaction):
            await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        ticket_id, _user_id, _subject = support_ticket_info_from_message(interaction.message)
        database_url = get_guild_database_url(interaction.guild_id)
        updated = update_support_ticket_status(database_url, ticket_id, "Resolved")
        if updated and interaction.message.embeds:
            embed = interaction.message.embeds[0]
            set_embed_field(embed, "Status", "Resolved", inline=True)
            embed.color = 0x43B581
            embed.set_footer(text=f"Resolved by @{interaction.user.name}")
            view = SupportTicketButtonsView()
            for item in view.children:
                if getattr(item, "custom_id", "") == "support_ticket:resolve":
                    item.disabled = True
            await interaction.message.edit(embed=embed, view=view)
            await log_submission_thread(
                interaction.message,
                f"Ticket log: resolved by {interaction.user.mention}.",
            )
        await interaction.followup.send(
            f"`{ticket_id}` marked resolved." if updated else f"Could not resolve `{ticket_id}`.",
            ephemeral=True,
        )

    @discord.ui.button(label="DM", style=discord.ButtonStyle.blurple, custom_id="support_ticket:dm")
    async def dm_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not user_can_manage_submissions(interaction):
            await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
            return

        await interaction.response.send_modal(SupportTicketDmModal(interaction.message))


def panel_embed(
    status_filter: str | None,
    sort_order: str,
    page: int,
    rows: list[tuple],
    total: int,
) -> discord.Embed:
    filter_name = status_filter or "All"
    sort_name = "Oldest First" if sort_order == "oldest" else "Newest First"
    total_pages = max(1, (total + PANEL_PAGE_SIZE - 1) // PANEL_PAGE_SIZE)
    embed = discord.Embed(
        title="Submission Panel",
        description=(
            f"Filter: **{filter_name}** | Sort: **{sort_name}** | "
            f"Page **{page + 1}/{total_pages}** | Total: **{total}**"
        ),
        color=0x5865F2,
    )

    if not rows:
        embed.add_field(name="No submissions", value="Nothing matched this filter.", inline=False)
        return embed

    for row in rows:
        (
            ticket_id,
            name,
            discord_username,
            track_name,
            track_link,
            artist_names,
            message,
            status_value,
            created_at,
        ) = row
        value = (
            f"Submitter: {truncate_text(name, 120)} ({truncate_text(discord_username, 80)})\n"
            f"Artists: {truncate_text(artist_names, 180)}\n"
            f"Demo: {truncate_text(track_link, 220)}\n"
            f"Message: {truncate_text(message, 300)}\n"
            f"Created: {discord_timestamp(created_at)}"
        )
        embed.add_field(
            name=f"{ticket_id} | {status_value} | {truncate_text(track_name, 120)}",
            value=value,
            inline=False,
        )

    return embed


class SubmissionPanelView(discord.ui.View):
    def __init__(
        self,
        database_url: str | None,
        status_filter: str | None = None,
        sort_order: str = "newest",
        page: int = 0,
    ):
        super().__init__(timeout=600)
        self.database_url = database_url
        self.status_filter = status_filter
        self.sort_order = sort_order
        self.page = page
        self.total = 0
        self.refresh_button_state()

    def refresh_button_state(self) -> None:
        total_pages = max(1, (self.total + PANEL_PAGE_SIZE - 1) // PANEL_PAGE_SIZE)
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue
            if item.custom_id == "panel:prev":
                item.disabled = self.page <= 0
            elif item.custom_id == "panel:next":
                item.disabled = self.page >= total_pages - 1
            elif item.custom_id == "panel:all":
                item.style = discord.ButtonStyle.blurple if self.status_filter is None else discord.ButtonStyle.secondary
            elif item.custom_id == "panel:queue":
                item.style = discord.ButtonStyle.blurple if self.status_filter == "In Queue" else discord.ButtonStyle.secondary
            elif item.custom_id == "panel:approved":
                item.style = discord.ButtonStyle.green if self.status_filter == "Approved" else discord.ButtonStyle.secondary
            elif item.custom_id == "panel:rejected":
                item.style = discord.ButtonStyle.red if self.status_filter == "Rejected" else discord.ButtonStyle.secondary
            elif item.custom_id == "panel:newest":
                item.style = discord.ButtonStyle.blurple if self.sort_order == "newest" else discord.ButtonStyle.secondary
            elif item.custom_id == "panel:oldest":
                item.style = discord.ButtonStyle.blurple if self.sort_order == "oldest" else discord.ButtonStyle.secondary

    def load_embed(self) -> discord.Embed:
        self.total = count_submissions(self.database_url, self.status_filter)
        total_pages = max(1, (self.total + PANEL_PAGE_SIZE - 1) // PANEL_PAGE_SIZE)
        self.page = min(max(self.page, 0), total_pages - 1)
        rows = fetch_panel_submissions(
            self.database_url,
            status=self.status_filter,
            limit=PANEL_PAGE_SIZE,
            offset=self.page * PANEL_PAGE_SIZE,
            sort_order=self.sort_order,
        )
        self.refresh_button_state()
        return panel_embed(self.status_filter, self.sort_order, self.page, rows, self.total)

    async def update_panel(self, interaction: discord.Interaction) -> None:
        if not user_is_admin(interaction):
            await interaction.response.send_message("Only administrators can use this panel.", ephemeral=True)
            return

        embed = self.load_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="All", style=discord.ButtonStyle.blurple, custom_id="panel:all", row=0)
    async def all_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.status_filter = None
        self.page = 0
        await self.update_panel(interaction)

    @discord.ui.button(label="In Queue", style=discord.ButtonStyle.secondary, custom_id="panel:queue", row=0)
    async def queue_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.status_filter = "In Queue"
        self.page = 0
        await self.update_panel(interaction)

    @discord.ui.button(label="Approved", style=discord.ButtonStyle.secondary, custom_id="panel:approved", row=0)
    async def approved_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.status_filter = "Approved"
        self.page = 0
        await self.update_panel(interaction)

    @discord.ui.button(label="Rejected", style=discord.ButtonStyle.secondary, custom_id="panel:rejected", row=0)
    async def rejected_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.status_filter = "Rejected"
        self.page = 0
        await self.update_panel(interaction)

    @discord.ui.button(label="Newest", style=discord.ButtonStyle.blurple, custom_id="panel:newest", row=1)
    async def newest_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.sort_order = "newest"
        self.page = 0
        await self.update_panel(interaction)

    @discord.ui.button(label="Oldest", style=discord.ButtonStyle.secondary, custom_id="panel:oldest", row=1)
    async def oldest_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.sort_order = "oldest"
        self.page = 0
        await self.update_panel(interaction)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, custom_id="panel:prev", row=2)
    async def previous_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        await self.update_panel(interaction)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, custom_id="panel:refresh", row=2)
    async def refresh_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_panel(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, custom_id="panel:next", row=2)
    async def next_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        await self.update_panel(interaction)


def queue_list_embed(rows: list[tuple]) -> discord.Embed:
    embed = discord.Embed(
        title="Newest Queued Submissions",
        description="Click a number below to see full submission details.",
        color=0x5865F2,
    )

    if not rows:
        embed.description = "No queued submissions found."
        return embed

    for index, row in enumerate(rows, start=1):
        ticket_id, name, _discord_username, track_name, _track_link, artist_names, _message, status_value, created_at = row
        embed.add_field(
            name=f"{index}. {truncate_text(track_name, 120)}",
            value=(
                f"Ticket: `{ticket_id}`\n"
                f"Artists: {truncate_text(artist_names, 180)}\n"
                f"Submitter: {truncate_text(name, 120)}\n"
                f"Status: {status_value}\n"
                f"Created: {discord_timestamp(created_at)}"
            ),
            inline=False,
        )

    return embed


def queue_detail_embed(row: tuple, index: int, total: int) -> discord.Embed:
    (
        ticket_id,
        name,
        discord_username,
        track_name,
        track_link,
        artist_names,
        message,
        status_value,
        created_at,
    ) = row
    embed = discord.Embed(
        title=f"Queued Submission {index + 1}/{total}",
        description=f"**{truncate_text(track_name, 180)}**",
        color=0x5865F2,
    )
    embed.add_field(name="Ticket ID", value=f"`{ticket_id}`", inline=False)
    embed.add_field(name="Status", value=status_value, inline=True)
    embed.add_field(name="Submitter", value=truncate_text(name, 120), inline=True)
    embed.add_field(name="Discord Username", value=truncate_text(discord_username, 120), inline=True)
    embed.add_field(name="Artist Names", value=truncate_text(artist_names, 900), inline=False)
    embed.add_field(name="Demo Link", value=truncate_text(track_link, 900), inline=False)
    embed.add_field(name="Message", value=truncate_text(message, 900), inline=False)
    embed.add_field(name="Created", value=discord_timestamp(created_at), inline=False)
    return embed


class QueueSubmissionButton(discord.ui.Button):
    def __init__(self, index: int, disabled: bool = False):
        super().__init__(
            label=str(index + 1),
            style=discord.ButtonStyle.secondary,
            disabled=disabled,
            row=0,
        )
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, QueueSubmissionsView):
            await interaction.response.send_message("This queue view is unavailable.", ephemeral=True)
            return

        if not user_can_manage_submissions(interaction):
            await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
            return

        view.selected_index = self.index
        view.refresh_button_state()
        embed = queue_detail_embed(view.rows[self.index], self.index, len(view.rows))
        await interaction.response.edit_message(embed=embed, view=view)


class QueueSubmissionsView(discord.ui.View):
    def __init__(self, rows: list[tuple]):
        super().__init__(timeout=600)
        self.rows = rows
        self.selected_index: int | None = None
        for index in range(5):
            self.add_item(QueueSubmissionButton(index, disabled=index >= len(rows)))
        self.refresh_button_state()

    def refresh_button_state(self) -> None:
        for item in self.children:
            if isinstance(item, QueueSubmissionButton):
                item.disabled = item.index >= len(self.rows)
                item.style = (
                    discord.ButtonStyle.blurple
                    if self.selected_index == item.index
                    else discord.ButtonStyle.secondary
                )

    @discord.ui.button(label="List", style=discord.ButtonStyle.secondary, row=1)
    async def list_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not user_can_manage_submissions(interaction):
            await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
            return

        self.selected_index = None
        self.refresh_button_state()
        await interaction.response.edit_message(embed=queue_list_embed(self.rows), view=self)


class DatabaseSetupModal(discord.ui.Modal, title="Set Server Database"):
    database_url = discord.ui.TextInput(
        label="Neon Database URL",
        placeholder="postgresql://user:password@host/db?sslmode=require",
        style=discord.TextStyle.paragraph,
        max_length=1200,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild_id:
            await interaction.response.send_message(
                "Database setup must be run inside a server.",
                ephemeral=True,
            )
            return
        if not user_is_admin(interaction):
            await interaction.response.send_message(
                "Only administrators can configure the server database.",
                ephemeral=True,
            )
            return
        if not guild_has_premium(interaction.guild_id):
            await interaction.response.send_message(
                "Custom Neon databases are a Pro feature. Free servers can use `/start` for managed storage.",
                ephemeral=True,
            )
            return
        if not DATABASE_URL:
            await interaction.response.send_message(
                "The bot owner has not configured the control DATABASE_URL.",
                ephemeral=True,
            )
            return
        if not encryption_ready():
            await interaction.response.send_message(
                "The bot owner has not configured CONFIG_ENCRYPTION_KEY.",
                ephemeral=True,
            )
            return

        value = self.database_url.value.strip()
        if not is_valid_database_url(value):
            await interaction.response.send_message(
                "Please enter a valid PostgreSQL/Neon connection URL.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        if not ensure_submission_table(value):
            await interaction.followup.send(
                "I could not connect to that database or create the submissions table.",
                ephemeral=True,
            )
            return

        saved = set_guild_database_url(interaction.guild_id, interaction.user.id, value)
        if saved:
            await interaction.followup.send(
                "Custom database connected. Existing LabelUtils data was migrated before switching.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "The database worked, but I could not save it in the control database.",
                ephemeral=True,
            )


class AdvancedSubmissionModal(discord.ui.Modal, title="New Label Submission"):
    real_name = discord.ui.TextInput(
        label="Your Real Name",
        placeholder="e.g., Marcus Soune",
        max_length=100,
    )
    track_name = discord.ui.TextInput(
        label="Track Name",
        placeholder="e.g., FOURTEY FUNK",
        max_length=100,
    )
    demo_link = discord.ui.TextInput(
        label="Demo Link",
        placeholder="https://soundcloud.com/...",
        max_length=500,
    )
    artist_names = discord.ui.TextInput(
        label="Artist Names",
        placeholder="e.g., main artist, featured artist",
        max_length=300,
    )
    message = discord.ui.TextInput(
        label="Message to Label",
        placeholder="Share any extra notes about your track here...",
        style=discord.TextStyle.paragraph,
        max_length=700,
        required=False,
    )

    def __init__(self, guild_id: int | None = None):
        super().__init__()
        settings = cached_submission_form_settings(guild_id)
        self.message.label = truncate_text(settings["message_label"], 45)
        self.message.placeholder = truncate_text(settings["message_placeholder"], 100)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        database_url = get_guild_database_url(interaction.guild_id)
        if not database_url:
            await interaction.followup.send(
                "This server has not completed storage setup yet. Ask an admin to run `/start`, or `/setup_db` for a custom Neon database.",
                ephemeral=True,
            )
            return
        staff_channel_id = get_guild_staff_channel_id(interaction.guild_id)
        if staff_channel_id == 0:
            await interaction.followup.send(
                "This server has not set a staff channel yet. Ask an admin to run `/setup_staff`.",
                ephemeral=True,
            )
            return
        settings = get_pro_settings(interaction.guild_id)

        remaining = get_cooldown_remaining(interaction.user.id)
        if remaining:
            minutes = max(1, int(remaining.total_seconds() // 60))
            await interaction.followup.send(
                f"Please wait about {minutes} minute(s) before submitting again.",
                ephemeral=True,
            )
            return

        if not is_valid_url(self.demo_link.value):
            await interaction.followup.send(
                "Please enter a valid demo link starting with http:// or https://.",
                ephemeral=True,
            )
            return

        channel = client.get_channel(staff_channel_id)
        if not channel and interaction.guild:
            channel = interaction.guild.get_channel(staff_channel_id)
        if not channel:
            await interaction.followup.send(
                "Error: Staff feed channel is unreachable. Check bot permissions and channel ID.",
                ephemeral=True,
            )
            return

        ensure_submission_table(database_url)

        max_submissions = int(settings.get("max_submissions_per_user") or 0)
        if max_submissions and count_user_submissions(database_url, interaction.user.id) >= max_submissions:
            await interaction.followup.send(
                f"You have reached this server's limit of {max_submissions} submission(s).",
                ephemeral=True,
            )
            return

        duplicate = find_duplicate_submission(database_url, self.demo_link.value)
        if duplicate:
            duplicate_ticket_id, duplicate_track_name, duplicate_status, _created_at = duplicate
            duplicate_text = (
                "That demo link has already been submitted in this server.\n"
                f"Existing ticket: `{duplicate_ticket_id}`\n"
                f"Track: **{truncate_text(duplicate_track_name, 120)}**\n"
                f"Status: **{duplicate_status}**"
            )
            if str(settings.get("duplicate_policy", "block")).lower() == "block":
                await interaction.followup.send(duplicate_text, ephemeral=True)
                return
            await interaction.followup.send(f"Duplicate warning:\n{duplicate_text}", ephemeral=True)

        team_name = server_display_name(interaction)
        ticket_id = generate_ticket_id()
        discord_user = f"@{interaction.user.name}"
        artist_id = interaction.user.id
        msg_val = self.message.value.strip() or "No extra production message notes provided."

        db_saved = save_submission_to_neon(
            database_url=database_url,
            ticket_id=ticket_id,
            name=self.real_name.value,
            discord_username=discord_user,
            track_name=self.track_name.value,
            track_link=self.demo_link.value.strip(),
            artist_names=self.artist_names.value,
            message=msg_val,
            user_id=artist_id,
        )

        footer_text = str(settings.get("footer_text") or f"{team_name} Management System")
        logo_url = str(settings.get("logo_url") or "")
        embed = discord.Embed(
            title="New Label Submission",
            description=server_tagline(interaction),
            color=server_embed_color(interaction, 0x2B2D31),
        )
        if is_valid_url(logo_url):
            embed.set_thumbnail(url=logo_url)
        embed.add_field(name="Ticket ID", value=ticket_id, inline=False)
        embed.add_field(name="User ID", value=str(artist_id), inline=False)
        embed.add_field(name="Name", value=self.real_name.value, inline=True)
        embed.add_field(name="Discord Username", value=discord_user, inline=True)
        embed.add_field(name="Track Name", value=self.track_name.value, inline=False)
        embed.add_field(name="Artist Names", value=self.artist_names.value, inline=False)
        embed.add_field(name="Demo Link", value=self.demo_link.value, inline=False)
        embed.add_field(name="Status", value="In Queue", inline=True)
        embed.add_field(name="Message", value=msg_val, inline=False)

        status_text = "Synced to Dashboard DB" if db_saved else "Logged to Channel Only (DB Sync Failure)"
        embed.set_footer(text=f"{footer_text} | {status_text} | Pending Action")

        staff_message = await channel.send(embed=embed, view=submission_decision_view(interaction.guild_id))
        set_submission_cooldown(artist_id, int(settings.get("cooldown_minutes") or COOLDOWN_MINUTES))
        success_text = format_template(
            settings.get("success_message"),
            ticket_id=ticket_id,
            track_name=self.track_name.value,
            team_name=team_name,
        )
        await interaction.followup.send(success_text, ephemeral=True)
        thread = await create_submission_thread(
            staff_message,
            ticket_id,
            self.track_name.value,
            artist_id,
        )
        if thread:
            embed.add_field(name="Thread ID", value=str(thread.id), inline=False)
            await staff_message.edit(embed=embed, view=submission_decision_view(interaction.guild_id))
            await thread.send(
                "Release log: submission received and staff card created."
            )


@tree.command(name="submit", description="Submit your demo tracking profile to the label")
async def submit(interaction: discord.Interaction):
    await interaction.response.send_modal(AdvancedSubmissionModal(interaction.guild_id))


@tree.command(name="help", description="Show LabelUtils commands and setup steps")
async def help_command(interaction: discord.Interaction):
    logger.info("Received /help from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    premium_active = guild_has_premium(interaction.guild_id)
    embed = discord.Embed(
        title="LabelUtils Help",
        description=(
            "Submit demos, review them with staff, and keep artists updated."
        ),
        color=server_embed_color(interaction),
    )
    embed.add_field(
        name="For Artists",
        value=(
            "`/submit` - send a demo\n"
            "`/submission` - check a submission\n"
            "`/my_subs` - view your submissions\n"
            "`/my_stats` - view your acceptance stats\n"
            "`/leaderboard` - see accepted submitters\n"
            "`/invite` - invite LabelUtils to another server"
        ),
        inline=False,
    )
    embed.add_field(
        name="For Staff",
        value=(
            "`/queue`, `/recent`, `/panel` - browse submissions\n"
            "`/status` - update a ticket\n"
            "`/start`, `/setup_staff`, `/setup` - setup\n"
            "`/storage`, `/setup_db` - Pro storage options"
        ),
        inline=False,
    )
    embed.add_field(
        name="Pro",
        value=(
            "`/premium` and `/redeem` - upgrade this server\n"
            "`/brand`, `/templates`, `/limits`, `/routing` - customize workflows\n"
            "`/shortlist`, `/priority`, `/rate`, `/reasons`, `/digest` - A&R tools\n"
            "`/storage` - choose West US, Europe (UK), or South-East Asia storage\n"
            "`/ticket_channel`, `/ticket_panel`, `/tickets`, `/ticket_set` - support tickets"
        ),
        inline=False,
    )
    embed.add_field(
        name="Docs",
        value="Full setup guides and command reference: https://labelutils.dapmedia.tech",
        inline=False,
    )
    embed.set_footer(text=f"Premium: {'active' if premium_active else 'not active'}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="invite", description="Get the LabelUtils invite link")
async def invite_command(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"[Click Me to Invite]({BOT_INVITE_URL})",
        ephemeral=True,
    )


class SubmitPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Submit Demo", style=discord.ButtonStyle.blurple, custom_id="submit_panel:open")
    async def submit_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AdvancedSubmissionModal(interaction.guild_id))


class SupportTicketModal(discord.ui.Modal, title="Open Ticket"):
    subject = discord.ui.TextInput(
        label="Subject",
        placeholder="What do you need help with?",
        max_length=120,
        required=True,
    )
    message = discord.ui.TextInput(
        label="Details",
        placeholder="Share the details staff should know.",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        database_url = get_guild_database_url(interaction.guild_id)
        if not database_url:
            await interaction.followup.send("This server has not connected a database yet.", ephemeral=True)
            return
        if not ensure_submission_table(database_url):
            await interaction.followup.send("I could not reach this server's database.", ephemeral=True)
            return

        settings = get_pro_settings(interaction.guild_id)
        channel_id = int(settings.get("ticket_channel_id") or 0)
        if channel_id == 0:
            await interaction.followup.send(
                "This server has not set a separate ticket staff channel yet. Ask an admin to run `/ticket_channel`.",
                ephemeral=True,
            )
            return
        channel = client.get_channel(channel_id)
        if not channel and interaction.guild:
            channel = interaction.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                "This server's ticket staff channel is set, but I cannot access it. Ask an admin to run `/ticket_channel` again.",
                ephemeral=True,
            )
            return

        ticket_id = generate_support_ticket_id()
        username = f"@{interaction.user.name}"
        saved = create_support_ticket(
            database_url,
            ticket_id,
            interaction.user.id,
            username,
            self.subject.value,
            self.message.value,
        )
        if not saved:
            await interaction.followup.send("I could not create that ticket.", ephemeral=True)
            return

        embed = discord.Embed(
            title="New Support Ticket",
            description=truncate_text(self.subject.value, 180),
            color=server_embed_color(interaction),
        )
        embed.add_field(name="Ticket ID", value=ticket_id, inline=False)
        embed.add_field(name="User ID", value=str(interaction.user.id), inline=False)
        embed.add_field(name="User", value=interaction.user.mention, inline=True)
        embed.add_field(name="Subject", value=truncate_text(self.subject.value, 180), inline=False)
        embed.add_field(name="Status", value="Open", inline=True)
        embed.add_field(name="Details", value=truncate_text(self.message.value, 1000), inline=False)
        staff_message = await channel.send(embed=embed, view=SupportTicketButtonsView())
        thread = await create_submission_thread(staff_message, ticket_id, self.subject.value, interaction.user.id)
        if thread:
            set_support_ticket_thread(database_url, ticket_id, thread.id)
            embed.add_field(name="Thread ID", value=str(thread.id), inline=False)
            await staff_message.edit(embed=embed, view=SupportTicketButtonsView())
            await thread.send(f"Support ticket `{ticket_id}` opened by {interaction.user.mention}.")

        await interaction.followup.send(f"Ticket opened: `{ticket_id}`. Staff will contact you by DM if needed.", ephemeral=True)


class SupportTicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.green, custom_id="support_ticket:open")
    async def ticket_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SupportTicketModal())


def require_pro_admin(interaction: discord.Interaction) -> str | None:
    if not user_is_admin(interaction):
        return "Only administrators can use this Pro setup command."
    if not guild_has_premium(interaction.guild_id):
        return "This is a Pro feature. Use `/premium` to see how to upgrade."
    return None


@tree.command(name="start", description="Admin: auto-setup this server with LabelUtils managed storage")
@app_commands.describe(label_name="Display name for this label or server")
async def start(interaction: discord.Interaction, label_name: str):
    logger.info("Received /start from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    if not interaction.guild_id:
        await interaction.response.send_message("Setup must be run inside a server.", ephemeral=True)
        return
    if not user_is_admin(interaction):
        await interaction.response.send_message("Only administrators can set up this server.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    assigned = assign_pooled_guild_database(
        interaction.guild_id,
        interaction.user.id,
        label_name,
        randomize=True,
    )
    if not assigned:
        await interaction.followup.send(
            "I could not assign managed storage. Ask the bot owner to configure `POOL_DATABASE_URL_1` to `POOL_DATABASE_URL_3`, or use `/setup_db`.",
            ephemeral=True,
        )
        return

    context = get_guild_database_url(interaction.guild_id)
    storage_text = (
        f"managed {pool_region_name(context.pool_slot)} storage schema `{context.schema_name}`"
        if context and context.schema_name
        else "managed storage"
    )
    await interaction.followup.send(
        f"LabelUtils storage is ready for **{truncate_text(label_name, 120)}** using {storage_text}.\n"
        "Next: run `/setup_staff` to choose the staff channel.",
        ephemeral=True,
    )


@tree.command(name="storage", description="Pro admin: choose managed storage region")
@app_commands.describe(region="Managed storage region")
@app_commands.choices(
    region=[
        app_commands.Choice(name="West US", value=1),
        app_commands.Choice(name="Europe (UK)", value=2),
        app_commands.Choice(name="South-East Asia", value=3),
    ]
)
async def storage(interaction: discord.Interaction, region: app_commands.Choice[int]):
    logger.info("Received /storage from guild=%s user=%s region=%s.", interaction.guild_id, interaction.user.id, region.value)
    error = require_pro_admin(interaction)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    label_name = interaction.guild.name if interaction.guild else f"Guild {interaction.guild_id}"
    await interaction.response.defer(ephemeral=True)
    assigned = assign_pooled_guild_database(
        interaction.guild_id,
        interaction.user.id,
        label_name,
        pool_slot=region.value,
    )
    await interaction.followup.send(
        f"Managed storage switched to **{pool_region_name(region.value)}**. Existing LabelUtils data was migrated before switching."
        if assigned
        else f"I could not assign **{pool_region_name(region.value)}**. Check that `POOL_DATABASE_URL_{region.value}` is configured.",
        ephemeral=True,
    )


@tree.command(name="setup_db", description="Admin: connect this server to a Neon database")
async def setup_database(interaction: discord.Interaction):
    logger.info("Received /setup_database from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    if not user_is_admin(interaction):
        await interaction.response.send_message(
            "Only administrators can configure the server database.",
            ephemeral=True,
        )
        return
    if not guild_has_premium(interaction.guild_id):
        await interaction.response.send_message(
            "Custom Neon databases are a Pro feature. Free servers should use `/start`.",
            ephemeral=True,
        )
        return
    await interaction.response.send_modal(DatabaseSetupModal())


@tree.command(name="setup_staff", description="Admin: set the staff feed channel for this server")
@app_commands.describe(channel="Channel where new submissions should be sent")
async def setup_staff_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    logger.info(
        "Received /setup_staff_channel from guild=%s user=%s channel=%s.",
        interaction.guild_id,
        interaction.user.id,
        channel.id,
    )
    if not interaction.guild_id:
        await interaction.response.send_message(
            "Staff channel setup must be run inside a server.",
            ephemeral=True,
        )
        return
    if not user_is_admin(interaction):
        await interaction.response.send_message(
            "Only administrators can configure the staff channel.",
            ephemeral=True,
        )
        return
    if not DATABASE_URL:
        await interaction.response.send_message(
            "The bot owner has not configured the control DATABASE_URL.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    saved = set_guild_staff_channel_id(interaction.guild_id, interaction.user.id, channel.id)
    text = (
        f"Staff channel connected: {channel.mention}"
        if saved
        else "I could not save the staff channel in the control database."
    )
    await interaction.followup.send(text, ephemeral=True)


@tree.command(name="db_status", description="Admin: check whether this server has a database connected")
async def database_status(interaction: discord.Interaction):
    logger.info("Received /database_status from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    if not user_is_admin(interaction):
        await interaction.response.send_message(
            "Only administrators can check the server database.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    database_context = get_guild_database_url(interaction.guild_id)
    if database_context and database_context.storage_mode == "pooled":
        text = (
            f"This server uses managed **{pool_region_name(database_context.pool_slot)}** storage "
            f"in schema `{database_context.schema_name}`."
        )
    elif database_context:
        text = "This server uses a custom Pro Neon database."
    else:
        text = "No storage is connected for this server. Run `/start`, or `/setup_db` for a custom Neon database."
    await interaction.followup.send(text, ephemeral=True)


@tree.command(name="staff_status", description="Admin: check this server's staff feed channel")
async def staff_channel_status(interaction: discord.Interaction):
    logger.info("Received /staff_channel_status from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    if not user_is_admin(interaction):
        await interaction.response.send_message(
            "Only administrators can check the staff channel.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    channel_id = get_guild_staff_channel_id(interaction.guild_id)
    if channel_id == 0:
        await interaction.followup.send(
            "No staff channel is connected for this server. Run `/setup_staff`.",
            ephemeral=True,
        )
        return

    channel = client.get_channel(channel_id)
    if not channel and interaction.guild:
        channel = interaction.guild.get_channel(channel_id)
    text = (
        f"Staff channel is set to {channel.mention}."
        if isinstance(channel, discord.TextChannel)
        else f"Staff channel is set to `{channel_id}`, but I cannot access it."
    )
    await interaction.followup.send(text, ephemeral=True)


@tree.command(name="setup", description="Admin: check LabelUtils setup for this server")
async def setup_status(interaction: discord.Interaction):
    logger.info("Received /setup_status from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    if not user_is_admin(interaction):
        await interaction.response.send_message(
            "Only administrators can check setup status.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    staff_channel_id = get_guild_staff_channel_id(interaction.guild_id)
    staff_channel = client.get_channel(staff_channel_id) if staff_channel_id else None
    if not staff_channel and interaction.guild and staff_channel_id:
        staff_channel = interaction.guild.get_channel(staff_channel_id)

    control_status = "Configured" if DATABASE_URL else "Missing"
    encryption_status = "Configured" if encryption_ready() else "Missing or invalid"
    if database_url and database_url.schema_name:
        database_status_text = f"Managed {pool_region_name(database_url.pool_slot)} schema `{database_url.schema_name}`"
    elif database_url:
        database_status_text = "Custom database connected"
    else:
        database_status_text = "Not connected"
    premium = get_premium_guild(interaction.guild_id)
    premium_status = f"{premium[0]} until {discord_timestamp(premium[1])}" if premium else "Not active"
    pro_settings = get_pro_settings(interaction.guild_id)
    ticket_channel_id = int(pro_settings.get("ticket_channel_id") or 0)
    ticket_channel = client.get_channel(ticket_channel_id) if ticket_channel_id else None
    if not ticket_channel and interaction.guild and ticket_channel_id:
        ticket_channel = interaction.guild.get_channel(ticket_channel_id)
    brand = get_guild_brand(interaction.guild_id)
    if brand:
        brand_name = brand.get("display_name") or server_display_name(interaction)
        brand_color = brand.get("embed_color")
        brand_status = (
            f"{brand_name} (#{int(brand_color):06X})"
            if brand_color is not None
            else f"{brand_name} (default color)"
        )
    else:
        brand_status = "Default server branding"
    staff_status = (
        f"Connected: {staff_channel.mention}"
        if isinstance(staff_channel, discord.TextChannel)
        else "Not connected" if staff_channel_id == 0 else f"Set to `{staff_channel_id}`, but inaccessible"
    )
    thread_status = (
        "Requires Create Public Threads or Create Private Threads permission in the staff channel."
    )

    embed = discord.Embed(title="LabelUtils Setup Status", color=0x5865F2)
    embed.add_field(name="Control Database", value=control_status, inline=True)
    embed.add_field(name="Encryption Key", value=encryption_status, inline=True)
    embed.add_field(name="Server Database", value=database_status_text, inline=True)
    embed.add_field(name="Premium", value=premium_status, inline=True)
    embed.add_field(name="Branding", value=brand_status, inline=False)
    embed.add_field(name="Staff Channel", value=staff_status, inline=False)
    embed.add_field(
        name="Ticket Channel",
        value=(
            ticket_channel.mention
            if isinstance(ticket_channel, discord.TextChannel)
            else "Not set" if ticket_channel_id == 0 else f"Set to `{ticket_channel_id}`, but inaccessible"
        ),
        inline=False,
    )
    embed.add_field(name="Submission Threads", value=thread_status, inline=False)
    embed.set_footer(text="Run /start and /setup_staff to complete setup. Pro can use /storage or /setup_db.")
    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="premium", description="See how to buy LabelUtils premium")
async def premium(interaction: discord.Interaction):
    logger.info("Received /premium from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    premium_row = get_premium_guild(interaction.guild_id)
    embed = discord.Embed(title="LabelUtils Premium", color=0xF1C40F)
    if premium_row:
        plan, expires_at = premium_row
        embed.description = f"This server has the **{plan}** plan until {discord_timestamp(expires_at)}."
    else:
        embed.description = PREMIUM_CONTACT
    embed.add_field(
        name="How it works",
        value="Contact the owner, complete payment, then premium is enabled manually for this server.",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="pro_status", description="Check this server's premium status")
async def premium_status(interaction: discord.Interaction):
    logger.info("Received /premium_status from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    premium_row = get_premium_guild(interaction.guild_id)
    if premium_row:
        plan, expires_at = premium_row
        text = f"This server has **{plan}** premium until {discord_timestamp(expires_at)}."
    else:
        text = f"This server does not have premium.\n{PREMIUM_CONTACT}"
    await interaction.response.send_message(text, ephemeral=True)


@tree.command(name="redeem", description="Admin: redeem a premium coupon for this server")
@app_commands.describe(code="Premium coupon code")
async def premium_redeem(interaction: discord.Interaction, code: str):
    logger.info("Received /premium_redeem from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    if not interaction.guild_id:
        await interaction.response.send_message("Premium coupons must be redeemed inside a server.", ephemeral=True)
        return
    if not user_is_admin(interaction):
        await interaction.response.send_message("Only administrators can redeem premium for this server.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    redeemed, message = redeem_premium_coupon(interaction.guild_id, code, interaction.user.id)
    await interaction.followup.send(message if redeemed else f"Could not redeem coupon: {message}", ephemeral=True)


@tree.command(name="coupon", description="Owner: create a reusable premium coupon")
@app_commands.describe(
    days="Premium days granted per redemption",
    uses="How many times this coupon can be redeemed",
    plan="Plan name, such as pro",
    code="Optional custom coupon code",
)
async def premium_coupon_create(
    interaction: discord.Interaction,
    days: int,
    uses: int,
    plan: str = "pro",
    code: str = "",
):
    logger.info("Received /premium_coupon_create from user=%s.", interaction.user.id)
    if not user_is_bot_owner(interaction.user.id):
        await interaction.response.send_message("Only the bot owner can use this.", ephemeral=True)
        return
    if days < 1 or uses < 1:
        await interaction.response.send_message("Days and uses must both be at least 1.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    created, result = create_premium_coupon(plan, days, uses, interaction.user.id, code or None)
    if created:
        await interaction.followup.send(
            f"Premium coupon created: `{result}`\nPlan: `{plan}` | Days/use: `{days}` | Uses: `{uses}`",
            ephemeral=True,
        )
    else:
        await interaction.followup.send(f"Could not create coupon: {result}", ephemeral=True)


@tree.command(name="pro_add", description="Owner: manually grant premium to a server")
@app_commands.describe(
    guild_id="Discord server ID to grant premium to",
    days="Number of days to add",
    plan="Plan name, such as pro, premium, or lifetime",
)
async def premium_add(
    interaction: discord.Interaction,
    guild_id: str,
    days: int,
    plan: str,
):
    logger.info("Received /premium_add from user=%s for guild=%s.", interaction.user.id, guild_id)
    if not user_is_bot_owner(interaction.user.id):
        await interaction.response.send_message("Only the bot owner can use this.", ephemeral=True)
        return

    parsed_guild_id = parse_snowflake(guild_id)
    if not parsed_guild_id:
        await interaction.response.send_message("Please enter a valid numeric server ID.", ephemeral=True)
        return
    if days < 1:
        await interaction.response.send_message("Days must be at least 1.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    saved = add_premium_guild(parsed_guild_id, plan, days, interaction.user.id)
    text = (
        f"Premium granted to `{parsed_guild_id}` for {days} day(s) on plan `{plan}`."
        if saved
        else "Could not save premium. Check the control database logs."
    )
    await interaction.followup.send(text, ephemeral=True)


@tree.command(name="pro_remove", description="Owner: remove premium from a server")
@app_commands.describe(guild_id="Discord server ID to remove premium from")
async def premium_remove(interaction: discord.Interaction, guild_id: str):
    logger.info("Received /premium_remove from user=%s for guild=%s.", interaction.user.id, guild_id)
    if not user_is_bot_owner(interaction.user.id):
        await interaction.response.send_message("Only the bot owner can use this.", ephemeral=True)
        return

    parsed_guild_id = parse_snowflake(guild_id)
    if not parsed_guild_id:
        await interaction.response.send_message("Please enter a valid numeric server ID.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    removed = remove_premium_guild(parsed_guild_id)
    text = (
        f"Premium removed from `{parsed_guild_id}`."
        if removed
        else "No active premium record was found for that server."
    )
    await interaction.followup.send(text, ephemeral=True)


class BrandSetupModal(discord.ui.Modal, title="Setup Pro Branding"):
    display_name = discord.ui.TextInput(
        label="Display Name",
        placeholder="Name shown in DMs, footers, and bot server nickname",
        max_length=80,
        required=True,
    )
    caption = discord.ui.TextInput(
        label="Submit Panel Caption",
        placeholder="Text shown in the submit panel embed",
        style=discord.TextStyle.paragraph,
        max_length=180,
        required=True,
    )
    color = discord.ui.TextInput(
        label="Embed Color",
        placeholder="#5865F2",
        max_length=7,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        parsed_color = parse_hex_color(self.color.value)
        if parsed_color is None:
            await interaction.response.send_message(
                "Please use a valid hex color like `#5865F2`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        saved = set_guild_brand(
            interaction.guild_id,
            interaction.user.id,
            self.display_name.value,
            self.caption.value,
            parsed_color,
        )
        if not saved:
            await interaction.followup.send("I could not save this server's branding.", ephemeral=True)
            return

        nickname_status = await set_bot_server_nickname(interaction, self.display_name.value)
        embed = discord.Embed(
            title="Branding Updated",
            description=truncate_text(self.caption.value, 180),
            color=parsed_color,
        )
        embed.add_field(name="Display Name", value=truncate_text(self.display_name.value, 80), inline=False)
        embed.add_field(name="Server Nickname", value=nickname_status, inline=False)
        embed.add_field(name="Submit Panel Caption", value=truncate_text(self.caption.value, 180), inline=False)
        embed.set_footer(text="This branding appears in supported server-specific LabelUtils messages.")
        await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="brand", description="Pro: customize this server's LabelUtils branding")
async def setup_brand(interaction: discord.Interaction):
    logger.info("Received /setup_brand from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    if not interaction.guild_id:
        await interaction.response.send_message("Brand setup must be run inside a server.", ephemeral=True)
        return
    if not user_is_admin(interaction):
        await interaction.response.send_message("Only administrators can configure branding.", ephemeral=True)
        return
    if not guild_has_premium(interaction.guild_id):
        await interaction.response.send_message(
            "This is a Pro feature. Use `/premium` to see how to upgrade.",
            ephemeral=True,
        )
        return

    await interaction.response.send_modal(BrandSetupModal())


@tree.command(name="brand_info", description="Show this server's Pro branding")
async def brand_status(interaction: discord.Interaction):
    logger.info("Received /brand_status from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    await interaction.response.defer(ephemeral=True)
    premium_row = get_premium_guild(interaction.guild_id)
    brand = get_guild_brand(interaction.guild_id)
    embed = discord.Embed(title="Brand Status", color=server_embed_color(interaction))
    embed.add_field(
        name="Premium",
        value=f"{premium_row[0]} until {discord_timestamp(premium_row[1])}" if premium_row else "Not active",
        inline=False,
    )
    embed.add_field(name="Display Name", value=server_display_name(interaction), inline=False)
    embed.add_field(
        name="Bot Server Nickname",
        value=interaction.guild.me.display_name if interaction.guild and interaction.guild.me else "Unavailable",
        inline=False,
    )
    embed.add_field(name="Submit Panel Caption", value=server_tagline(interaction) or "Not set", inline=False)
    embed.add_field(
        name="Color",
        value=f"#{server_embed_color(interaction):06X}" if brand else "Default",
        inline=False,
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="brand_clear", description="Pro: reset this server's custom branding")
async def brand_reset(interaction: discord.Interaction):
    logger.info("Received /brand_reset from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    if not interaction.guild_id:
        await interaction.response.send_message("Brand reset must be run inside a server.", ephemeral=True)
        return
    if not user_is_admin(interaction):
        await interaction.response.send_message("Only administrators can reset branding.", ephemeral=True)
        return
    if not guild_has_premium(interaction.guild_id):
        await interaction.response.send_message(
            "This is a Pro feature. Use `/premium` to see how to upgrade.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    reset = reset_guild_brand(interaction.guild_id)
    nickname_status = await set_bot_server_nickname(interaction, None) if reset else ""
    await interaction.followup.send(
        f"Branding reset to server defaults.\n{nickname_status}" if reset else "I could not reset branding.",
        ephemeral=True,
    )


@tree.command(name="form", description="Pro: customize the optional submission question")
@app_commands.describe(
    label="Label for the final optional form field",
    placeholder="Placeholder text for that field",
)
async def setup_form(interaction: discord.Interaction, label: str, placeholder: str):
    logger.info("Received /setup_form from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    error = require_pro_admin(interaction)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    saved = upsert_pro_settings(
        interaction.guild_id,
        interaction.user.id,
        message_label=truncate_text(label, 45),
        message_placeholder=truncate_text(placeholder, 100),
    )
    await interaction.response.send_message(
        "Submission form prompt updated." if saved else "I could not save the form settings.",
        ephemeral=True,
    )


class TemplateSetupModal(discord.ui.Modal, title="Setup DM Templates"):
    approval_template = discord.ui.TextInput(
        label="Approval Template",
        placeholder="Leave blank to keep current. Supports {track_name}, {team_name}, {ticket_id}",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        required=False,
    )
    rejection_template = discord.ui.TextInput(
        label="Rejection Template",
        placeholder="Leave blank to keep current. Supports {track_name}, {team_name}, {ticket_id}, {reason}",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        updates = {}
        approval_value = self.approval_template.value.strip()
        rejection_value = self.rejection_template.value.strip()
        if approval_value:
            updates["approval_template"] = truncate_text(approval_value, 1500)
        if rejection_value:
            updates["rejection_template"] = truncate_text(rejection_value, 1500)

        if not updates:
            await interaction.response.send_message("No template changes were submitted.", ephemeral=True)
            return

        saved = upsert_pro_settings(interaction.guild_id, interaction.user.id, **updates)
        changed = ", ".join(key.replace("_template", "") for key in updates)
        await interaction.response.send_message(
            f"Updated {changed} template(s)." if saved else "I could not save the DM templates.",
            ephemeral=True,
        )


@tree.command(name="templates", description="Pro: customize approval and rejection DMs")
async def setup_templates(interaction: discord.Interaction):
    logger.info("Received /setup_templates from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    error = require_pro_admin(interaction)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    await interaction.response.send_modal(TemplateSetupModal())


@tree.command(name="limits", description="Pro: configure cooldowns, submission caps, and duplicate policy")
@app_commands.describe(
    cooldown_minutes="Minutes users must wait between submissions",
    max_submissions_per_user="0 means unlimited total submissions per user",
    duplicate_policy="block rejects duplicate links; warn allows them after warning",
)
@app_commands.choices(
    duplicate_policy=[
        app_commands.Choice(name="Block duplicates", value="block"),
        app_commands.Choice(name="Warn only", value="warn"),
    ]
)
async def setup_limits(
    interaction: discord.Interaction,
    cooldown_minutes: int,
    max_submissions_per_user: int,
    duplicate_policy: app_commands.Choice[str],
):
    logger.info("Received /setup_limits from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    error = require_pro_admin(interaction)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    saved = upsert_pro_settings(
        interaction.guild_id,
        interaction.user.id,
        cooldown_minutes=max(0, min(cooldown_minutes, 10080)),
        max_submissions_per_user=max(0, min(max_submissions_per_user, 10000)),
        duplicate_policy=duplicate_policy.value,
    )
    await interaction.response.send_message(
        "Submission limits updated." if saved else "I could not save submission limits.",
        ephemeral=True,
    )


@tree.command(name="routing", description="Pro: route approved/rejected updates to channels")
@app_commands.describe(
    approved_channel="Optional channel for approved submission updates",
    rejected_channel="Optional channel for rejected submission updates",
)
async def setup_routing(
    interaction: discord.Interaction,
    approved_channel: discord.TextChannel | None = None,
    rejected_channel: discord.TextChannel | None = None,
):
    logger.info("Received /setup_routing from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    error = require_pro_admin(interaction)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    saved = upsert_pro_settings(
        interaction.guild_id,
        interaction.user.id,
        approved_channel_id=approved_channel.id if approved_channel else 0,
        rejected_channel_id=rejected_channel.id if rejected_channel else 0,
    )
    await interaction.response.send_message(
        "Routing updated." if saved else "I could not save routing settings.",
        ephemeral=True,
    )


@tree.command(name="extras", description="Pro: set footer, logo, and submit success text")
@app_commands.describe(
    footer_text="Footer prefix on staff submission cards",
    logo_url="Image URL used as submission thumbnail, or none",
    success_message="Submitter confirmation. Supports {ticket_id}, {track_name}, {team_name}",
)
async def setup_brand_extras(
    interaction: discord.Interaction,
    footer_text: str,
    logo_url: str,
    success_message: str,
):
    logger.info("Received /setup_brand_extras from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    error = require_pro_admin(interaction)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return
    if logo_url and logo_url.lower() != "none" and not is_valid_url(logo_url):
        await interaction.response.send_message("Logo URL must be a valid http(s) URL or `none`.", ephemeral=True)
        return

    saved = upsert_pro_settings(
        interaction.guild_id,
        interaction.user.id,
        footer_text=truncate_text(footer_text, 160),
        logo_url="" if logo_url.lower() == "none" else truncate_text(logo_url, 500),
        success_message=truncate_text(success_message, 500),
    )
    await interaction.response.send_message(
        "Brand extras updated." if saved else "I could not save brand extras.",
        ephemeral=True,
    )


@tree.command(name="post_panel", description="Pro: post a branded submit button panel")
async def post_submit_panel(interaction: discord.Interaction):
    logger.info("Received /post_submit_panel from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    await interaction.response.defer()
    error = require_pro_admin(interaction)
    if error:
        await interaction.followup.send(error, ephemeral=True)
        return

    embed = discord.Embed(
        title=f"Submit to {server_display_name(interaction)}",
        description=server_tagline(interaction) or "Click the button below to submit your demo.",
        color=server_embed_color(interaction),
    )
    await interaction.followup.send(embed=embed, view=SubmitPanelView())


@tree.command(name="note", description="Pro staff: add a private note to a submission")
@app_commands.describe(ticket_id="Ticket ID to annotate", note="Private staff note")
async def staff_note(interaction: discord.Interaction, ticket_id: str, note: str):
    logger.info("Received /staff_note from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    if not user_can_manage_submissions(interaction):
        await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
        return
    if not guild_has_premium(interaction.guild_id):
        await interaction.response.send_message("This is a Pro feature.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    if not database_url:
        await interaction.followup.send("This server has not connected a submissions database yet.", ephemeral=True)
        return
    if not ensure_submission_table(database_url):
        await interaction.followup.send("I could not reach this server's submissions database.", ephemeral=True)
        return

    normalized_ticket_id = normalize_ticket_id(ticket_id)
    saved = append_staff_note(database_url, normalized_ticket_id, truncate_text(note, 1200), f"@{interaction.user.name}")
    await interaction.followup.send(
        f"Staff note added to `{normalized_ticket_id}`."
        if saved
        else f"Could not add that note. I could not find `{normalized_ticket_id}` in the database.",
        ephemeral=True,
    )


@tree.command(name="reviewer", description="Pro staff: assign a reviewer to a submission")
@app_commands.describe(ticket_id="Ticket ID to assign", reviewer="Reviewer responsible for this submission")
async def assign_reviewer_command(interaction: discord.Interaction, ticket_id: str, reviewer: discord.Member):
    logger.info("Received /assign_reviewer from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    if not user_can_manage_submissions(interaction):
        await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
        return
    if not guild_has_premium(interaction.guild_id):
        await interaction.response.send_message("This is a Pro feature.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    if not database_url:
        await interaction.followup.send("This server has not connected a submissions database yet.", ephemeral=True)
        return
    if not ensure_submission_table(database_url):
        await interaction.followup.send("I could not reach this server's submissions database.", ephemeral=True)
        return

    normalized_ticket_id = normalize_ticket_id(ticket_id)
    saved = assign_reviewer(database_url, normalized_ticket_id, reviewer.id)
    await interaction.followup.send(
        f"Reviewer assigned to `{normalized_ticket_id}`: {reviewer.mention}"
        if saved
        else f"Could not assign reviewer. I could not find `{normalized_ticket_id}` in the database.",
        ephemeral=True,
    )


@tree.command(name="shortlist", description="Pro staff: add or remove a submission from the shortlist")
@app_commands.describe(ticket_id="Submission ticket ID", enabled="Whether this submission is shortlisted")
async def shortlist(interaction: discord.Interaction, ticket_id: str, enabled: bool = True):
    logger.info("Received /shortlist from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    if not user_can_manage_submissions(interaction):
        await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
        return
    if not guild_has_premium(interaction.guild_id):
        await interaction.response.send_message("This is a Pro feature.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    ensure_submission_table(database_url) if database_url else None
    normalized_ticket_id = normalize_ticket_id(ticket_id)
    saved = update_submission_flags(database_url, normalized_ticket_id, shortlisted=enabled)
    if enabled and saved:
        update_submission_status(database_url, normalized_ticket_id, "Shortlisted")
    await interaction.followup.send(
        f"`{normalized_ticket_id}` {'added to' if enabled else 'removed from'} shortlist."
        if saved
        else f"Could not update `{normalized_ticket_id}`.",
        ephemeral=True,
    )


@tree.command(name="priority", description="Pro staff: mark a submission as priority")
@app_commands.describe(ticket_id="Submission ticket ID", enabled="Whether this submission is priority")
async def priority(interaction: discord.Interaction, ticket_id: str, enabled: bool = True):
    logger.info("Received /priority from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    if not user_can_manage_submissions(interaction):
        await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
        return
    if not guild_has_premium(interaction.guild_id):
        await interaction.response.send_message("This is a Pro feature.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    ensure_submission_table(database_url) if database_url else None
    normalized_ticket_id = normalize_ticket_id(ticket_id)
    saved = update_submission_flags(database_url, normalized_ticket_id, priority=enabled)
    await interaction.followup.send(
        f"`{normalized_ticket_id}` priority {'enabled' if enabled else 'disabled'}."
        if saved
        else f"Could not update `{normalized_ticket_id}`.",
        ephemeral=True,
    )


@tree.command(name="rate", description="Pro staff: rate a demo from 1 to 10")
@app_commands.describe(ticket_id="Submission ticket ID", score="Score from 1 to 10")
async def rate(interaction: discord.Interaction, ticket_id: str, score: int):
    logger.info("Received /rate from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    if not user_can_manage_submissions(interaction):
        await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
        return
    if not guild_has_premium(interaction.guild_id):
        await interaction.response.send_message("This is a Pro feature.", ephemeral=True)
        return
    if score < 1 or score > 10:
        await interaction.response.send_message("Score must be between 1 and 10.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    ensure_submission_table(database_url) if database_url else None
    normalized_ticket_id = normalize_ticket_id(ticket_id)
    saved = update_submission_flags(database_url, normalized_ticket_id, rating=score)
    await interaction.followup.send(
        f"`{normalized_ticket_id}` rated **{score}/10**."
        if saved
        else f"Could not rate `{normalized_ticket_id}`.",
        ephemeral=True,
    )


@tree.command(name="shortlisted", description="Pro staff: show shortlisted submissions")
async def shortlisted(interaction: discord.Interaction):
    logger.info("Received /shortlisted from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    if not user_can_manage_submissions(interaction):
        await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
        return
    if not guild_has_premium(interaction.guild_id):
        await interaction.response.send_message("This is a Pro feature.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    ensure_submission_table(database_url) if database_url else None
    rows = fetch_shortlisted_submissions(database_url)
    await interaction.followup.send(embed=submissions_to_embed("Shortlisted Submissions", rows), ephemeral=True)


@tree.command(name="reasons", description="Pro admin: set or view saved rejection reasons")
@app_commands.describe(reasons="Separate reasons with |, or leave blank to view current reasons")
async def reasons(interaction: discord.Interaction, reasons: str = ""):
    logger.info("Received /reasons from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    error = require_pro_admin(interaction)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    if not reasons.strip():
        current = parse_rejection_reasons(get_pro_settings(interaction.guild_id))
        text = "\n".join(f"- {reason}" for reason in current) if current else "No saved rejection reasons yet."
        await interaction.response.send_message(text, ephemeral=True)
        return

    cleaned = " | ".join(
        truncate_text(reason.strip(), 180)
        for reason in reasons.split("|")
        if reason.strip()
    )
    saved = upsert_pro_settings(interaction.guild_id, interaction.user.id, rejection_reasons=cleaned)
    await interaction.response.send_message(
        "Saved rejection reasons updated." if saved else "I could not save rejection reasons.",
        ephemeral=True,
    )


@tree.command(name="digest", description="Pro admin: post a weekly submission digest")
@app_commands.describe(channel="Optional channel to save as the weekly digest target")
async def digest(interaction: discord.Interaction, channel: discord.TextChannel = None):
    logger.info("Received /digest from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    error = require_pro_admin(interaction)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    if channel:
        upsert_pro_settings(interaction.guild_id, interaction.user.id, digest_channel_id=channel.id)
    settings = get_pro_settings(interaction.guild_id)
    target_id = channel.id if channel else int(settings.get("digest_channel_id") or 0)
    target = client.get_channel(target_id) if target_id else interaction.channel
    if not isinstance(target, discord.TextChannel):
        await interaction.followup.send("I could not find a digest channel.", ephemeral=True)
        return

    database_url = get_guild_database_url(interaction.guild_id)
    ensure_submission_table(database_url) if database_url else None
    stats = fetch_weekly_digest_stats(database_url)
    embed = discord.Embed(title="Weekly Submission Digest", color=server_embed_color(interaction))
    embed.add_field(name="New", value=str(stats.get("new", 0)), inline=True)
    embed.add_field(name="Approved", value=str(stats.get("approved", 0)), inline=True)
    embed.add_field(name="Rejected", value=str(stats.get("rejected", 0)), inline=True)
    embed.add_field(name="Pending", value=str(stats.get("pending", 0)), inline=True)
    embed.add_field(name="Shortlisted", value=str(stats.get("shortlisted", 0)), inline=True)
    embed.add_field(name="Priority", value=str(stats.get("priority", 0)), inline=True)
    embed.add_field(name="Avg Rating", value=str(stats.get("avg_rating", 0)), inline=True)
    await target.send(embed=embed)
    await interaction.followup.send(f"Digest posted in {target.mention}.", ephemeral=True)


@tree.command(name="analytics", description="Pro admin: view advanced submission analytics")
async def analytics(interaction: discord.Interaction):
    logger.info("Received /analytics from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    error = require_pro_admin(interaction)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    ensure_submission_table(database_url) if database_url else None
    total, approved, rejected, in_queue = fetch_submission_analytics(database_url)
    embed = discord.Embed(title="Submission Analytics", color=server_embed_color(interaction))
    embed.add_field(name="Total", value=str(total), inline=True)
    embed.add_field(name="Approved", value=str(approved), inline=True)
    embed.add_field(name="In Queue", value=str(in_queue), inline=True)
    embed.add_field(name="Rejected", value=str(rejected), inline=True)
    embed.add_field(
        name="Acceptance Rate",
        value=f"{round((approved / total) * 100, 1)}%" if total else "0%",
        inline=True,
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="export", description="Pro admin: export submissions as CSV")
async def export_submissions(interaction: discord.Interaction):
    logger.info("Received /export_submissions from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    error = require_pro_admin(interaction)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    ensure_submission_table(database_url) if database_url else None
    rows = fetch_submissions_for_export(database_url)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ticket_id", "user_id", "name", "discord_username", "track_name", "track_link",
        "artist_names", "message", "status", "created_at", "reviewer_id", "staff_notes",
        "rating", "shortlisted", "priority",
    ])
    writer.writerows(rows)
    data = output.getvalue().encode("utf-8")
    await interaction.followup.send(
        content=f"Exported {len(rows)} submission(s).",
        file=discord.File(fp=BytesIO(data), filename="labelutils_submissions.csv"),
        ephemeral=True,
    )


@tree.command(name="submission", description="Look up a label submission")
@app_commands.describe(ticket_id="Ticket ID, such as LABEL-ABCD1234-EFGH5678")
async def ticket(interaction: discord.Interaction, ticket_id: str):
    logger.info("Received /ticket from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    if not database_url:
        await interaction.followup.send(
            "This server has not connected a submissions database yet.",
            ephemeral=True,
        )
        return

    if not ensure_submission_table(database_url):
        await interaction.followup.send("I could not reach this server's submissions database.", ephemeral=True)
        return

    row = fetch_submission_by_ticket(database_url, ticket_id)
    if not row:
        await interaction.followup.send("I could not find that ticket.", ephemeral=True)
        return

    viewer_is_staff = user_can_manage_submissions(interaction)
    row_user_id = int(row[1] or 0)
    message = str(row[7] or "")
    owns_ticket = row_user_id == interaction.user.id or message.startswith(f"[User ID: {interaction.user.id}]")
    if not viewer_is_staff and not owns_ticket:
        await interaction.followup.send("You can only view tickets you submitted.", ephemeral=True)
        return

    await interaction.followup.send(embed=ticket_embed(row, viewer_is_staff), ephemeral=True)


@tree.command(name="ticket_panel", description="Pro admin: post an open-ticket button panel")
@app_commands.describe(channel="Channel to post the ticket panel in")
async def ticket_panel(interaction: discord.Interaction, channel: discord.TextChannel):
    logger.info("Received /ticket_panel from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    await interaction.response.defer(ephemeral=True)
    error = require_pro_admin(interaction)
    if error:
        await interaction.followup.send(error, ephemeral=True)
        return

    settings = get_pro_settings(interaction.guild_id)
    ticket_channel_id = int(settings.get("ticket_channel_id") or 0)
    if ticket_channel_id == 0:
        await interaction.followup.send(
            "Set a separate staff ticket channel first with `/ticket_channel`.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title=f"{server_display_name(interaction)} Support",
        description="Open a ticket and staff will help you in a private thread.",
        color=server_embed_color(interaction),
    )
    try:
        await channel.send(embed=embed, view=SupportTicketPanelView())
    except discord.Forbidden:
        await interaction.followup.send(
            f"I cannot post the ticket panel in {channel.mention}. Give me View Channel, Send Messages, and Embed Links there.",
            ephemeral=True,
        )
        return
    await interaction.followup.send(f"Ticket panel posted in {channel.mention}.", ephemeral=True)


@tree.command(name="ticket_channel", description="Pro admin: set the private staff ticket channel")
@app_commands.describe(channel="Private staff channel where ticket cards should be posted")
async def ticket_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    logger.info("Received /ticket_channel from guild=%s user=%s channel=%s.", interaction.guild_id, interaction.user.id, channel.id)
    await interaction.response.defer(ephemeral=True)
    error = require_pro_admin(interaction)
    if error:
        await interaction.followup.send(error, ephemeral=True)
        return

    submission_staff_channel_id = get_guild_staff_channel_id(interaction.guild_id)
    if submission_staff_channel_id and channel.id == submission_staff_channel_id:
        await interaction.followup.send(
            "Support tickets need a separate staff channel. Choose a different private channel than your demo submission staff channel.",
            ephemeral=True,
        )
        return

    saved = upsert_pro_settings(interaction.guild_id, interaction.user.id, ticket_channel_id=channel.id)
    await interaction.followup.send(
        f"Ticket channel set to {channel.mention}." if saved else "I could not save the ticket channel.",
        ephemeral=True,
    )


@tree.command(name="tickets", description="Pro staff: show recent support tickets")
@app_commands.describe(status="Optional ticket status filter")
@app_commands.choices(
    status=[
        app_commands.Choice(name="Open", value="Open"),
        app_commands.Choice(name="Waiting", value="Waiting"),
        app_commands.Choice(name="Answered", value="Answered"),
        app_commands.Choice(name="Resolved", value="Resolved"),
    ]
)
async def tickets(interaction: discord.Interaction, status: str = ""):
    logger.info("Received /tickets from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    if not user_can_manage_submissions(interaction):
        await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
        return
    if not guild_has_premium(interaction.guild_id):
        await interaction.response.send_message("This is a Pro feature.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    ensure_submission_table(database_url) if database_url else None
    rows = fetch_support_tickets(database_url, status if status else None)
    embed = discord.Embed(title="Support Tickets", color=server_embed_color(interaction))
    if not rows:
        embed.description = "No support tickets found."
    for ticket_id, username, subject, status_value, created_at in rows:
        embed.add_field(
            name=f"{ticket_id} - {status_value}",
            value=f"{truncate_text(subject, 120)}\nOpened by {username}\nCreated: {discord_timestamp(created_at)}",
            inline=False,
        )
    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="ticket_set", description="Pro staff: update a support ticket status")
@app_commands.describe(ticket_id="Support ticket ID", new_status="New support ticket status")
@app_commands.choices(
    new_status=[
        app_commands.Choice(name="Open", value="Open"),
        app_commands.Choice(name="Waiting", value="Waiting"),
        app_commands.Choice(name="Answered", value="Answered"),
        app_commands.Choice(name="Resolved", value="Resolved"),
    ]
)
async def ticket_status(
    interaction: discord.Interaction,
    ticket_id: str,
    new_status: app_commands.Choice[str],
):
    logger.info("Received /ticket_status from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    if not user_can_manage_submissions(interaction):
        await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
        return
    if not guild_has_premium(interaction.guild_id):
        await interaction.response.send_message("This is a Pro feature.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    ensure_submission_table(database_url) if database_url else None
    normalized_ticket_id = normalize_ticket_id(ticket_id)
    updated = update_support_ticket_status(database_url, normalized_ticket_id, new_status.value)
    await interaction.followup.send(
        f"`{normalized_ticket_id}` set to **{new_status.value}**."
        if updated
        else f"Could not update `{normalized_ticket_id}`.",
        ephemeral=True,
    )


@tree.command(name="my_subs", description="Show your submissions in this server")
async def my_submissions(interaction: discord.Interaction):
    logger.info("Received /my_submissions from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    if not database_url:
        await interaction.followup.send(
            "This server has not connected a submissions database yet.",
            ephemeral=True,
        )
        return

    ensure_submission_table(database_url)
    rows = fetch_user_submissions(database_url, interaction.user.id)
    await interaction.followup.send(embed=user_submissions_embed(rows), ephemeral=True)


@tree.command(name="my_demos", description="Show demos you submitted in this server")
async def my_demos(interaction: discord.Interaction):
    logger.info("Received /my_demos from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    if not database_url:
        await interaction.followup.send(
            "This server has not connected a submissions database yet.",
            ephemeral=True,
        )
        return

    ensure_submission_table(database_url)
    rows = fetch_user_submissions(database_url, interaction.user.id, limit=15)
    await interaction.followup.send(embed=user_submissions_embed(rows), ephemeral=True)


@tree.command(name="my_stats", description="Show how many of your demos were accepted")
async def my_stats(interaction: discord.Interaction):
    logger.info("Received /my_stats from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    if not database_url:
        await interaction.followup.send(
            "This server has not connected a submissions database yet.",
            ephemeral=True,
        )
        return

    ensure_submission_table(database_url)
    total, approved, rejected, in_queue = fetch_user_submission_stats(database_url, interaction.user.id)
    await interaction.followup.send(
        embed=user_stats_embed(total, approved, rejected, in_queue),
        ephemeral=True,
    )


@tree.command(name="leaderboard", description="Show artists with the most accepted demos")
async def accepted_leaderboard(interaction: discord.Interaction):
    logger.info("Received /accepted_leaderboard from guild=%s user=%s.", interaction.guild_id, interaction.user.id)
    await interaction.response.defer()
    database_url = get_guild_database_url(interaction.guild_id)
    if not database_url:
        await interaction.followup.send(
            "This server has not connected a submissions database yet.",
            ephemeral=True,
        )
        return

    ensure_submission_table(database_url)
    view = AcceptedLeaderboardView(database_url, server_display_name(interaction))
    embed = view.load_embed()
    await interaction.followup.send(
        embed=embed,
        view=view,
    )


@tree.command(name="status", description="Staff: update a submission status")
@app_commands.describe(ticket_id="Ticket ID to update", new_status="New status")
@app_commands.choices(
    new_status=[
        app_commands.Choice(name="In Queue", value="In Queue"),
        app_commands.Choice(name="Needs Review", value="Needs Review"),
        app_commands.Choice(name="Shortlisted", value="Shortlisted"),
        app_commands.Choice(name="Processed", value="Processed"),
        app_commands.Choice(name="Contacted", value="Contacted"),
        app_commands.Choice(name="Signed", value="Signed"),
        app_commands.Choice(name="Approved", value="Approved"),
        app_commands.Choice(name="Rejected", value="Rejected"),
    ]
)
async def status(
    interaction: discord.Interaction,
    ticket_id: str,
    new_status: app_commands.Choice[str],
):
    if not user_can_manage_submissions(interaction):
        await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
        return

    database_url = get_guild_database_url(interaction.guild_id)
    updated = update_submission_status(database_url, ticket_id.strip(), new_status.value)
    text = "Status updated." if updated else "Could not update that ticket. Check the ID and database."
    await interaction.response.send_message(text, ephemeral=True)


@tree.command(name="panel", description="Admin: browse submissions with filters and pages")
async def panel(interaction: discord.Interaction):
    if not user_is_admin(interaction):
        await interaction.response.send_message("Only administrators can use this panel.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    ensure_submission_table(database_url) if database_url else None
    view = SubmissionPanelView(database_url)
    embed = view.load_embed()
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


def submissions_to_embed(title: str, rows: list[tuple]) -> discord.Embed:
    embed = discord.Embed(title=title, color=0x5865F2)
    if not rows:
        embed.description = "No submissions found."
        return embed

    for ticket_id, name, track_name, status_value, created_at in rows:
        embed.add_field(
            name=f"{ticket_id} - {status_value}",
            value=f"{track_name} by {name}\nCreated: {discord_timestamp(created_at)}",
            inline=False,
        )
    return embed


def user_submissions_embed(rows: list[tuple]) -> discord.Embed:
    embed = discord.Embed(title="My Submissions", color=0x5865F2)
    if not rows:
        embed.description = "You have no submissions in this server yet."
        return embed

    for ticket_id, track_name, status_value, created_at in rows:
        embed.add_field(
            name=f"{track_name} - {status_value}",
            value=f"Ticket: `{ticket_id}`\nCreated: {discord_timestamp(created_at)}",
            inline=False,
        )
    return embed


def ticket_embed(row: tuple, viewer_is_staff: bool) -> discord.Embed:
    (
        ticket_id,
        user_id,
        name,
        discord_username,
        track_name,
        track_link,
        artist_names,
        message,
        status_value,
        created_at,
        reviewer_id,
        staff_notes,
        rating,
        shortlisted,
        priority,
    ) = row
    embed = discord.Embed(
        title=f"Ticket {ticket_id}",
        description=f"**{truncate_text(track_name, 180)}**",
        color=0x5865F2,
    )
    embed.add_field(name="Status", value=status_value, inline=True)
    embed.add_field(name="Created", value=discord_timestamp(created_at), inline=True)
    embed.add_field(name="Submitter", value=truncate_text(name, 120), inline=True)
    embed.add_field(name="Discord", value=truncate_text(discord_username, 120), inline=True)
    embed.add_field(name="Artists", value=truncate_text(artist_names, 500), inline=False)
    embed.add_field(name="Demo Link", value=truncate_text(track_link, 900), inline=False)
    embed.add_field(name="Message", value=truncate_text(clean_submission_message(message), 900), inline=False)
    embed.add_field(name="Priority", value="Yes" if priority else "No", inline=True)
    embed.add_field(name="Shortlisted", value="Yes" if shortlisted else "No", inline=True)
    embed.add_field(name="Rating", value=f"{rating}/10" if rating else "Unrated", inline=True)
    if viewer_is_staff:
        embed.add_field(name="User ID", value=str(user_id or "Unknown"), inline=True)
        embed.add_field(
            name="Reviewer",
            value=f"<@{reviewer_id}>" if reviewer_id else "Unassigned",
            inline=True,
        )
        if staff_notes:
            embed.add_field(name="Staff Notes", value=truncate_text(staff_notes, 1000), inline=False)
    return embed


def user_stats_embed(total: int, approved: int, rejected: int, in_queue: int) -> discord.Embed:
    embed = discord.Embed(title="My Demo Stats", color=0x43B581)
    embed.add_field(name="Submitted", value=str(total), inline=True)
    embed.add_field(name="Accepted", value=str(approved), inline=True)
    embed.add_field(name="In Queue", value=str(in_queue), inline=True)
    embed.add_field(name="Rejected", value=str(rejected), inline=True)
    if total:
        rate = round((approved / total) * 100, 1)
        embed.add_field(name="Acceptance Rate", value=f"{rate}%", inline=True)
    else:
        embed.description = "You have not submitted any demos in this server yet."
    return embed


def accepted_leaderboard_embed(
    rows: list[tuple],
    team_name: str,
    page: int,
    total: int,
) -> discord.Embed:
    total_pages = max(1, (total + LEADERBOARD_PAGE_SIZE - 1) // LEADERBOARD_PAGE_SIZE)
    embed = discord.Embed(
        title=f"{team_name} Accepted Submitter Leaderboard",
        color=0xF1C40F,
    )
    embed.set_footer(text=f"Page {page + 1}/{total_pages} | Grouped by Discord username")
    if not rows:
        embed.description = "No accepted demos yet."
        return embed

    lines = []
    start_rank = page * LEADERBOARD_PAGE_SIZE + 1
    for index, (submitter_name, approved_count) in enumerate(rows, start=start_rank):
        lines.append(f"**{index}.** {truncate_text(submitter_name, 80)} - **{approved_count}** accepted")
    embed.description = "\n".join(lines)
    return embed


class AcceptedLeaderboardView(discord.ui.View):
    def __init__(self, database_url: str | None, team_name: str, page: int = 0):
        super().__init__(timeout=600)
        self.database_url = database_url
        self.team_name = team_name
        self.page = page
        self.total = 0
        self.refresh_button_state()

    def refresh_button_state(self) -> None:
        total_pages = max(1, (self.total + LEADERBOARD_PAGE_SIZE - 1) // LEADERBOARD_PAGE_SIZE)
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue
            if item.custom_id == "leaderboard:prev":
                item.disabled = self.page <= 0
            elif item.custom_id == "leaderboard:next":
                item.disabled = self.page >= total_pages - 1

    def load_embed(self) -> discord.Embed:
        self.total = count_accepted_leaderboard_entries(self.database_url)
        total_pages = max(1, (self.total + LEADERBOARD_PAGE_SIZE - 1) // LEADERBOARD_PAGE_SIZE)
        self.page = min(max(self.page, 0), total_pages - 1)
        rows = fetch_accepted_leaderboard(
            self.database_url,
            limit=LEADERBOARD_PAGE_SIZE,
            offset=self.page * LEADERBOARD_PAGE_SIZE,
        )
        self.refresh_button_state()
        return accepted_leaderboard_embed(rows, self.team_name, self.page, self.total)

    async def update_leaderboard(self, interaction: discord.Interaction) -> None:
        embed = self.load_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, custom_id="leaderboard:prev")
    async def previous_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        await self.update_leaderboard(interaction)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, custom_id="leaderboard:refresh")
    async def refresh_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_leaderboard(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, custom_id="leaderboard:next")
    async def next_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        await self.update_leaderboard(interaction)


async def create_submission_thread(
    staff_message: discord.Message,
    ticket_id: str,
    track_name: str,
    submitter_id: int,
) -> discord.Thread | None:
    try:
        thread_name = truncate_text(f"{ticket_id} - {track_name}", 95)
        thread = await staff_message.create_thread(
            name=thread_name,
            auto_archive_duration=1440,
        )
        await thread.send(
            f"Private staff discussion for `{ticket_id}`.\nSubmitter: <@{submitter_id}>"
        )
        return thread
    except Exception:
        logger.exception("Failed to create staff thread for %s.", ticket_id)
        return None


@tree.command(name="queue", description="Staff: show the newest queued submissions")
async def queue(interaction: discord.Interaction):
    if not user_can_manage_submissions(interaction):
        await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    ensure_submission_table(database_url) if database_url else None
    rows = fetch_panel_submissions(database_url, status="In Queue", limit=5, sort_order="newest")
    await interaction.followup.send(
        embed=queue_list_embed(rows),
        view=QueueSubmissionsView(rows),
        ephemeral=True,
    )


@tree.command(name="recent", description="Staff: show the newest submissions")
async def recent(interaction: discord.Interaction):
    if not user_can_manage_submissions(interaction):
        await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    database_url = get_guild_database_url(interaction.guild_id)
    ensure_submission_table(database_url) if database_url else None
    rows = fetch_submissions(database_url, limit=5)
    await interaction.followup.send(embed=submissions_to_embed("Newest Submissions", rows), ephemeral=True)


@client.event
async def on_ready():
    logger.info("LabelUtils Interactive Bot Online: %s", client.user)
    logger.info("Default staff channel ID: %s", DEFAULT_STAFF_CHANNEL_ID)
    logger.info("Targeting sync table: label_submissions")


@client.event
async def on_message(message: discord.Message):
    await forward_dm_reply_to_thread(message)


def validate_startup_environment() -> bool:
    ok = True
    if not TOKEN:
        logger.critical("DISCORD_BOT_TOKEN is missing.")
        ok = False
    if not DATABASE_URL:
        logger.warning("DATABASE_URL is missing. Per-server database setup will fail.")
    if not available_pool_slots():
        logger.warning("POOL_DATABASE_URL_1..3 are missing. /start managed storage will fail; /setup_db can still be used.")
    if not CONFIG_ENCRYPTION_KEY:
        logger.warning("CONFIG_ENCRYPTION_KEY is missing. Per-server database setup will fail.")
    if not intents.message_content:
        logger.warning("Message Content Intent is disabled. DM reply forwarding will not work.")
    if not OWNER_USER_IDS:
        logger.warning("OWNER_USER_IDS is missing. Owner-only premium commands will reject everyone.")
    if DEFAULT_STAFF_CHANNEL_ID == 0:
        logger.warning("STAFF_CHANNEL_ID is missing. Servers must run /setup_staff.")
    if DISCORD_GUILD_ID == 0:
        logger.warning("DISCORD_GUILD_ID is missing. Global slash command updates can take up to 1 hour.")
    return ok


class HealthRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in {"/", "/health"}:
            self.send_response(404)
            self.end_headers()
            return

        body = b"LabelUtils Discord bot is running.\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        logger.debug("Health server: " + format, *args)


def start_health_server() -> None:
    try:
        server = ThreadingHTTPServer((HEALTH_HOST, HEALTH_PORT), HealthRequestHandler)
    except OSError:
        logger.exception("Could not start health server on %s:%s.", HEALTH_HOST, HEALTH_PORT)
        return

    thread = Thread(target=server.serve_forever, name="health-server", daemon=True)
    thread.start()
    logger.info("Health server listening on %s:%s.", HEALTH_HOST, HEALTH_PORT)


if validate_startup_environment():
    if FORCE_IPV4:
        prefer_ipv4_dns()
    ensure_control_tables()
    start_health_server()
    try:
        client.run(TOKEN)
    except Exception:
        logger.exception("Discord client stopped during startup or runtime. Restarting in 30 seconds.")
        time.sleep(30)
        os.execv(sys.executable, [sys.executable, *sys.argv])
