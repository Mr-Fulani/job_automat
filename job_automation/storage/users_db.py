import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


@dataclass
class UserRow:
    slug: str
    display_name: str
    is_active: int
    created_at: str
    updated_at: str


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path) -> None:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                slug TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                user_slug TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_slug) REFERENCES users(slug)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def list_users(db_path: Path) -> list[UserRow]:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT slug, display_name, is_active, created_at, updated_at FROM users ORDER BY slug")
        rows = cur.fetchall()
        return [
            UserRow(
                slug=str(r["slug"]),
                display_name=str(r["display_name"]),
                is_active=int(r["is_active"]),
                created_at=str(r["created_at"]),
                updated_at=str(r["updated_at"]),
            )
            for r in rows
        ]
    finally:
        conn.close()


def get_user(db_path: Path, slug: str) -> Optional[UserRow]:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT slug, display_name, is_active, created_at, updated_at FROM users WHERE slug = ?",
            (slug,),
        )
        r = cur.fetchone()
        if not r:
            return None
        return UserRow(
            slug=str(r["slug"]),
            display_name=str(r["display_name"]),
            is_active=int(r["is_active"]),
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
        )
    finally:
        conn.close()


def create_user(db_path: Path, slug: str, display_name: str) -> None:
    now = datetime.now().isoformat()
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users(slug, display_name, is_active, created_at, updated_at) VALUES (?, ?, 1, ?, ?)",
            (slug, display_name, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_profile(db_path: Path, user_slug: str, profile_json: dict[str, Any]) -> None:
    now = datetime.now().isoformat()
    payload = json.dumps(profile_json, ensure_ascii=False, indent=2)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO profiles(user_slug, profile_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_slug) DO UPDATE SET profile_json=excluded.profile_json, updated_at=excluded.updated_at",
            (user_slug, payload, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_profile_json(db_path: Path, user_slug: str) -> Optional[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT profile_json FROM profiles WHERE user_slug = ?", (user_slug,))
        r = cur.fetchone()
        if not r:
            return None
        raw = str(r["profile_json"])
        return json.loads(raw)
    finally:
        conn.close()
