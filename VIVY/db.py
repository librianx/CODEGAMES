import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple


DB_PATH = os.getenv("VIVY_DB_PATH", os.path.join(os.path.dirname(__file__), "vivy.sqlite"))


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True) if os.path.dirname(DB_PATH) else None
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_memory (
                user_id TEXT PRIMARY KEY,
                preferences TEXT NOT NULL,
                summary TEXT NOT NULL,
                summary_long TEXT NOT NULL,
                last_interaction INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS interaction_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                sentiment TEXT,
                content TEXT,
                timestamp INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES user_memory(user_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_turn (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                mode TEXT,
                interest_signal TEXT,
                timestamp INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES user_memory(user_id)
            )
            """
        )
        conn.commit()

        # lightweight migration for existing DBs
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(user_memory)").fetchall()}
        if "summary_long" not in cols:
            conn.execute("ALTER TABLE user_memory ADD COLUMN summary_long TEXT NOT NULL DEFAULT ''")
            conn.commit()


def get_user(user_id: str) -> Optional[sqlite3.Row]:
    with _connect() as conn:
        cur = conn.execute("SELECT * FROM user_memory WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return row


def upsert_user(
    user_id: str,
    preferences: Dict[str, Any],
    summary: str,
    summary_long: str = "",
    last_interaction: Optional[int] = None,
):
    now = int(time.time())
    last_interaction = last_interaction or now
    pref_text = json.dumps(preferences, ensure_ascii=False)

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_memory(user_id, preferences, summary, summary_long, last_interaction)
            VALUES(?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                preferences=excluded.preferences,
                summary=excluded.summary,
                summary_long=excluded.summary_long,
                last_interaction=excluded.last_interaction
            """,
            (user_id, pref_text, summary or "", summary_long or "", last_interaction),
        )
        conn.commit()


def ensure_user(user_id: str):
    row = get_user(user_id)
    if row is not None:
        return

    init_preferences = {
        "initialized": False,
        "questionnaire_asked": [],
        "questionnaire_answered": {},
        "last_inspiration_date": None,
        "last_inspiration_text": None,
    }
    upsert_user(user_id, init_preferences, summary="", summary_long="", last_interaction=int(time.time()))


def log_interaction(user_id: str, topic: str, sentiment: Optional[str], content: Optional[str]):
    ts = int(time.time())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO interaction_log(user_id, topic, sentiment, content, timestamp) VALUES(?,?,?,?,?)",
            (user_id, topic, sentiment, content, ts),
        )
        conn.commit()


def list_recent_interactions(user_id: str, limit: int = 50) -> List[sqlite3.Row]:
    with _connect() as conn:
        cur = conn.execute(
            "SELECT id, topic, sentiment, content, timestamp FROM interaction_log WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, int(limit)),
        )
        return cur.fetchall()


def count_interactions(user_id: str) -> int:
    with _connect() as conn:
        cur = conn.execute("SELECT COUNT(1) AS c FROM interaction_log WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return int(row["c"]) if row is not None else 0


def log_conversation_turn(
    user_id: str,
    role: str,
    content: str,
    mode: Optional[str] = None,
    interest_signal: Optional[str] = None,
):
    ts = int(time.time())
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO conversation_turn(user_id, role, content, mode, interest_signal, timestamp)
            VALUES(?,?,?,?,?,?)
            """,
            (user_id, role, content or "", mode, interest_signal, ts),
        )
        conn.commit()


def list_recent_conversation_turns(user_id: str, limit: int = 40) -> List[sqlite3.Row]:
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT id, role, content, mode, interest_signal, timestamp
            FROM conversation_turn
            WHERE user_id = ?
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (user_id, int(limit)),
        )
        return cur.fetchall()


def delete_conversation_turn(user_id: str, turn_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM conversation_turn WHERE user_id = ? AND id = ?",
            (user_id, int(turn_id)),
        )
        conn.commit()
        return cur.rowcount > 0


def parse_preferences(row: sqlite3.Row) -> Dict[str, Any]:
    try:
        return json.loads(row["preferences"])
    except Exception:
        return {}


def update_user_preferences(user_id: str, patch: Dict[str, Any]):
    row = get_user(user_id)
    if row is None:
        ensure_user(user_id)
        row = get_user(user_id)

    preferences = parse_preferences(row)
    # shallow merge by default
    for k, v in patch.items():
        preferences[k] = v

    upsert_user(
        user_id,
        preferences,
        summary=row["summary"],
        summary_long=(row["summary_long"] if "summary_long" in row.keys() else ""),
        last_interaction=int(time.time()),
    )


def update_user_summary(user_id: str, summary: str):
    row = get_user(user_id)
    if row is None:
        ensure_user(user_id)
        row = get_user(user_id)

    upsert_user(
        user_id,
        parse_preferences(row),
        summary=summary,
        summary_long=(row["summary_long"] if "summary_long" in row.keys() else ""),
        last_interaction=int(time.time()),
    )


def update_user_summary_long(user_id: str, summary_long: str):
    row = get_user(user_id)
    if row is None:
        ensure_user(user_id)
        row = get_user(user_id)

    upsert_user(
        user_id,
        parse_preferences(row),
        summary=row["summary"],
        summary_long=summary_long,
        last_interaction=int(time.time()),
    )
