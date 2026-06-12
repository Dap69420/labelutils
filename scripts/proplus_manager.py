import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("vektra-proplus-manager")

DATABASE_URL = os.getenv("DATABASE_URL", "")
CONFIG_ENCRYPTION_KEY = os.getenv("CONFIG_ENCRYPTION_KEY", "")
POLL_SECONDS = max(10, int(os.getenv("PROPLUS_MANAGER_POLL_SECONDS", "30")))
MAX_BOTS = max(1, int(os.getenv("MAX_WHITE_LABEL_BOTS", "5")))
BOT_SCRIPT = os.getenv("PROPLUS_BOT_SCRIPT", os.path.join(os.getcwd(), "bot.py"))
PYTHON_BIN = os.getenv("PROPLUS_PYTHON_BIN", sys.executable)

running = True


@dataclass(frozen=True)
class BotConfig:
    guild_id: int
    encrypted_token: str
    status_type: str
    status_text: str
    updated_at: datetime | None

    @property
    def signature(self) -> tuple:
        return (
            self.encrypted_token,
            self.status_type,
            self.status_text,
            self.updated_at.isoformat() if self.updated_at else "",
        )


class Worker:
    def __init__(self, config: BotConfig, process: subprocess.Popen):
        self.config = config
        self.process = process


workers: dict[int, Worker] = {}


def stop_running(_signum=None, _frame=None):
    global running
    running = False


signal.signal(signal.SIGINT, stop_running)
signal.signal(signal.SIGTERM, stop_running)


def connect_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing.")
    return psycopg.connect(DATABASE_URL, connect_timeout=8)


def ensure_table():
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS vektra_white_label_bots (
                    guild_id BIGINT PRIMARY KEY,
                    enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    client_id TEXT,
                    bot_token_encrypted TEXT,
                    status_type TEXT NOT NULL DEFAULT 'playing',
                    status_text TEXT NOT NULL DEFAULT 'reviewing demos',
                    manager_status TEXT NOT NULL DEFAULT 'pending',
                    manager_message TEXT NOT NULL DEFAULT '',
                    last_started_at TIMESTAMPTZ,
                    last_seen_at TIMESTAMPTZ,
                    updated_by BIGINT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )


def update_status(guild_id: int, status: str, message: str = "", *, started: bool = False):
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE vektra_white_label_bots
                SET manager_status = %s,
                    manager_message = %s,
                    last_seen_at = NOW()
                    {", last_started_at = NOW()" if started else ""}
                WHERE guild_id = %s;
                """,
                (status, message[:500], guild_id),
            )


def fetch_configs() -> list[BotConfig]:
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    wl.guild_id,
                    wl.bot_token_encrypted,
                    wl.status_type,
                    wl.status_text,
                    wl.updated_at
                FROM vektra_white_label_bots wl
                JOIN labelutils_premium_guilds pg
                  ON pg.guild_id = wl.guild_id
                 AND pg.expires_at > NOW()
                 AND lower(replace(pg.plan, '-', '_')) IN ('pro_plus', 'pro+', 'plus', 'proplus', 'white_label', 'whitelabel')
                WHERE wl.enabled = TRUE
                  AND wl.bot_token_encrypted IS NOT NULL
                ORDER BY wl.updated_at DESC
                LIMIT %s;
                """,
                (MAX_BOTS,),
            )
            return [
                BotConfig(
                    guild_id=int(row[0]),
                    encrypted_token=row[1],
                    status_type=row[2] or "playing",
                    status_text=row[3] or "reviewing demos",
                    updated_at=row[4],
                )
                for row in cur.fetchall()
            ]


def decrypt_token(encrypted_token: str) -> str:
    if not CONFIG_ENCRYPTION_KEY:
        raise RuntimeError("CONFIG_ENCRYPTION_KEY is missing.")
    try:
        return Fernet(CONFIG_ENCRYPTION_KEY.encode()).decrypt(encrypted_token.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise RuntimeError("Stored bot token could not be decrypted.") from exc


def spawn_worker(config: BotConfig) -> subprocess.Popen:
    token = decrypt_token(config.encrypted_token)
    env = os.environ.copy()
    env.update(
        {
            "DISCORD_BOT_TOKEN": token,
            "WHITE_LABEL_GUILD_ID": str(config.guild_id),
            "WHITE_LABEL_STATUS_TYPE": config.status_type,
            "WHITE_LABEL_STATUS_TEXT": config.status_text,
            "OWNER_GUILD_ID": "0",
            "STAFF_CHANNEL_ID": "0",
            "PORT": "0",
            "HEALTH_PORT": "0",
            "PYTHONUNBUFFERED": "1",
        }
    )
    logger.info("Starting Pro+ worker for guild %s.", config.guild_id)
    return subprocess.Popen(
        [PYTHON_BIN, BOT_SCRIPT],
        cwd=os.path.dirname(os.path.abspath(BOT_SCRIPT)) or os.getcwd(),
        env=env,
    )


def stop_worker(guild_id: int, worker: Worker):
    logger.info("Stopping Pro+ worker for guild %s.", guild_id)
    worker.process.terminate()
    try:
        worker.process.wait(timeout=12)
    except subprocess.TimeoutExpired:
        worker.process.kill()
        worker.process.wait(timeout=8)


def reconcile():
    configs = {config.guild_id: config for config in fetch_configs()}

    for guild_id in list(workers):
        worker = workers[guild_id]
        if guild_id not in configs:
            stop_worker(guild_id, worker)
            workers.pop(guild_id, None)
            update_status(guild_id, "stopped", "White-label bot is disabled or Pro+ expired.")
            continue

        if worker.process.poll() is not None:
            workers.pop(guild_id, None)
            update_status(guild_id, "restarting", f"Worker exited with code {worker.process.returncode}. Restarting.")
            continue

        if worker.config.signature != configs[guild_id].signature:
            stop_worker(guild_id, worker)
            workers.pop(guild_id, None)
            update_status(guild_id, "restarting", "Configuration changed. Restarting worker.")
            continue

        update_status(guild_id, "online", "Worker process is running.")

    for guild_id, config in configs.items():
        if guild_id in workers:
            continue
        try:
            process = spawn_worker(config)
            workers[guild_id] = Worker(config, process)
            update_status(guild_id, "starting", "Worker process started.", started=True)
        except Exception as exc:
            logger.exception("Could not start Pro+ worker for guild %s.", guild_id)
            update_status(guild_id, "error", str(exc))


def main():
    ensure_table()
    logger.info("Pro+ manager online. Max bots: %s. Poll: %ss.", MAX_BOTS, POLL_SECONDS)
    while running:
        try:
            reconcile()
        except Exception:
            logger.exception("Pro+ manager reconcile failed.")
        time.sleep(POLL_SECONDS)

    for guild_id, worker in list(workers.items()):
        stop_worker(guild_id, worker)
        update_status(guild_id, "stopped", "Manager service stopped.")


if __name__ == "__main__":
    main()
