"""第三版 SQLite：偏好、星級、AI 建議與多人投票持久化。"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import Restaurant, Room


class TieVoteError(ValueError):
    """最高票平手時不擅自加入其他決選規則。"""


class Database:
    """新版的資料存取 class。"""

    ROOM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def init_schema(self) -> None:
        # The pre-authentication V3 schema identified people by display name.
        # Authentication needs a provider-backed key, so this one-time migration
        # intentionally clears the legacy user data requested by the owner.
        with self._connect() as connection:
            profile_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'profiles'"
            ).fetchone()
            if profile_table:
                profile_columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(profiles)").fetchall()
                }
                if "user_key" not in profile_columns:
                    connection.executescript(
                        """
                        DROP TABLE IF EXISTS votes;
                        DROP TABLE IF EXISTS restaurants;
                        DROP TABLE IF EXISTS participants;
                        DROP TABLE IF EXISTS rooms;
                        DROP TABLE IF EXISTS ratings;
                        DROP TABLE IF EXISTS preferences;
                        DROP TABLE IF EXISTS profiles;
                        """
                    )

        schema = """
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_key TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            auth_type TEXT NOT NULL CHECK (auth_type IN ('google', 'guest')),
            email TEXT NOT NULL DEFAULT '',
            picture_url TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS preferences (
            profile_id INTEGER NOT NULL,
            place_id TEXT NOT NULL,
            preference INTEGER NOT NULL CHECK (preference IN (-1, 1)),
            name TEXT NOT NULL,
            address TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            google_rating REAL,
            google_rating_count INTEGER NOT NULL DEFAULT 0,
            price_level TEXT,
            primary_type TEXT NOT NULL DEFAULT 'restaurant',
            cuisine_label TEXT NOT NULL DEFAULT '其他',
            map_url TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (profile_id, place_id),
            FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS ratings (
            profile_id INTEGER NOT NULL,
            place_id TEXT NOT NULL,
            stars INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
            name TEXT NOT NULL,
            address TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            google_rating REAL,
            google_rating_count INTEGER NOT NULL DEFAULT 0,
            price_level TEXT,
            primary_type TEXT NOT NULL DEFAULT 'restaurant',
            cuisine_label TEXT NOT NULL DEFAULT '其他',
            map_url TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (profile_id, place_id),
            FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS rooms (
            code TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            host_user_key TEXT NOT NULL,
            host_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
            winner_place_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS participants (
            room_code TEXT NOT NULL,
            user_key TEXT NOT NULL,
            name TEXT NOT NULL,
            joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (room_code, user_key),
            FOREIGN KEY (room_code) REFERENCES rooms(code) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS restaurants (
            room_code TEXT NOT NULL,
            place_id TEXT NOT NULL,
            name TEXT NOT NULL,
            address TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            google_rating REAL,
            google_rating_count INTEGER NOT NULL DEFAULT 0,
            price_level TEXT,
            primary_type TEXT NOT NULL DEFAULT 'restaurant',
            cuisine_label TEXT NOT NULL DEFAULT '其他',
            open_now INTEGER,
            map_url TEXT NOT NULL DEFAULT '',
            ai_advice TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (room_code, place_id),
            FOREIGN KEY (room_code) REFERENCES rooms(code) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS votes (
            room_code TEXT NOT NULL,
            participant_user_key TEXT NOT NULL,
            place_id TEXT NOT NULL,
            voted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (room_code, participant_user_key),
            FOREIGN KEY (room_code, participant_user_key)
                REFERENCES participants(room_code, user_key) ON DELETE CASCADE,
            FOREIGN KEY (room_code, place_id)
                REFERENCES restaurants(room_code, place_id) ON DELETE CASCADE
        );
        """
        with self._connect() as connection:
            connection.executescript(schema)
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(restaurants)").fetchall()
            }
            if "ai_advice" not in columns:
                connection.execute(
                    "ALTER TABLE restaurants ADD COLUMN ai_advice TEXT NOT NULL DEFAULT ''"
                )

    @staticmethod
    def _validate_profile_name(name: str) -> str:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("請先輸入你的名稱。")
        return cleaned

    @staticmethod
    def _profile_id(connection: sqlite3.Connection, user_key: str) -> int:
        connection.execute(
            """
            INSERT OR IGNORE INTO profiles (user_key, display_name, auth_type)
            VALUES (?, ?, 'guest')
            """,
            (user_key, user_key),
        )
        row = connection.execute(
            "SELECT id FROM profiles WHERE user_key = ?", (user_key,)
        ).fetchone()
        if row is None:
            raise RuntimeError("無法建立使用者資料。")
        return int(row["id"])

    def ensure_profile(self, name: str) -> int:
        """取得或建立個人資料。"""
        name = self._validate_profile_name(name)
        with self._connect() as connection:
            return self._profile_id(connection, name)

    def upsert_user(
        self,
        user_key: str,
        display_name: str,
        auth_type: str,
        email: str = "",
        picture_url: str = "",
    ) -> int:
        """Create or refresh an authenticated/guest identity."""
        user_key = self._validate_profile_name(user_key)
        display_name = self._validate_profile_name(display_name)
        if auth_type not in {"google", "guest"}:
            raise ValueError("不支援的登入方式。")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO profiles (
                    user_key, display_name, auth_type, email, picture_url
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_key) DO UPDATE SET
                    display_name = excluded.display_name,
                    auth_type = excluded.auth_type,
                    email = excluded.email,
                    picture_url = excluded.picture_url,
                    last_seen_at = CURRENT_TIMESTAMP
                """,
                (user_key, display_name, auth_type, email, picture_url),
            )
            return self._profile_id(connection, user_key)

    def register_guest(self, user_key: str, requested_name: str) -> str:
        """Create a guest and suffix duplicate display names with ``(n)``."""
        user_key = self._validate_profile_name(user_key)
        requested_name = self._validate_profile_name(requested_name)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT display_name FROM profiles WHERE user_key = ?",
                (user_key,),
            ).fetchone()
            if current:
                return str(current["display_name"])

            existing = {
                str(row["display_name"]).casefold()
                for row in connection.execute(
                    "SELECT display_name FROM profiles WHERE auth_type = 'guest'"
                ).fetchall()
            }
            display_name = requested_name
            suffix = 2
            while display_name.casefold() in existing:
                display_name = f"{requested_name}({suffix})"
                suffix += 1
            connection.execute(
                """
                INSERT INTO profiles (user_key, display_name, auth_type)
                VALUES (?, ?, 'guest')
                """,
                (user_key, display_name),
            )
            return display_name

    def list_guest_profiles(self) -> list[tuple[str, str]]:
        """Return reusable local guest identities, most recently used first."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT user_key, display_name
                FROM profiles
                WHERE auth_type = 'guest'
                ORDER BY last_seen_at DESC, id DESC
                """
            ).fetchall()
        return [(str(row["user_key"]), str(row["display_name"])) for row in rows]

    @staticmethod
    def _restaurant_values(restaurant: Restaurant) -> tuple[object, ...]:
        return (
            restaurant.place_id,
            restaurant.name,
            restaurant.address,
            restaurant.latitude,
            restaurant.longitude,
            restaurant.google_rating,
            restaurant.google_rating_count,
            restaurant.price_level,
            restaurant.primary_type,
            restaurant.cuisine_label,
            restaurant.map_url,
        )

    def save_preference(
        self, profile_name: str, restaurant: Restaurant, preference: int
    ) -> None:
        """儲存喜歡（1）或不喜歡（-1），再次點選會更新。"""
        profile_name = self._validate_profile_name(profile_name)
        if preference not in (-1, 1):
            raise ValueError("偏好只能是喜歡或不喜歡。")
        with self._connect() as connection:
            profile_id = self._profile_id(connection, profile_name)
            connection.execute(
                """
                INSERT INTO preferences (
                    profile_id, place_id, preference, name, address, latitude,
                    longitude, google_rating, google_rating_count, price_level,
                    primary_type, cuisine_label, map_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, place_id) DO UPDATE SET
                    preference = excluded.preference,
                    name = excluded.name,
                    address = excluded.address,
                    google_rating = excluded.google_rating,
                    google_rating_count = excluded.google_rating_count,
                    price_level = excluded.price_level,
                    primary_type = excluded.primary_type,
                    cuisine_label = excluded.cuisine_label,
                    map_url = excluded.map_url,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    profile_id,
                    restaurant.place_id,
                    preference,
                    *self._restaurant_values(restaurant)[1:],
                ),
            )

    def remove_preference(self, profile_name: str, place_id: str) -> None:
        profile_name = self._validate_profile_name(profile_name)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM profiles WHERE user_key = ?",
                (profile_name,),
            ).fetchone()
            if row:
                connection.execute(
                    "DELETE FROM preferences WHERE profile_id = ? AND place_id = ?",
                    (row["id"], place_id),
                )

    def preference_map(self, profile_name: str) -> dict[str, int]:
        if not profile_name.strip():
            return {}
        query = """
        SELECT p.place_id, p.preference
        FROM preferences AS p
        JOIN profiles AS u ON u.id = p.profile_id
        WHERE u.user_key = ?
        """
        with self._connect() as connection:
            rows = connection.execute(query, (profile_name.strip(),)).fetchall()
        return {str(row["place_id"]): int(row["preference"]) for row in rows}

    def cuisine_affinity(self, profile_name: str) -> dict[str, int]:
        if not profile_name.strip():
            return {}
        query = """
        SELECT p.primary_type, SUM(p.preference) AS affinity
        FROM preferences AS p
        JOIN profiles AS u ON u.id = p.profile_id
        WHERE u.user_key = ?
        GROUP BY p.primary_type
        """
        with self._connect() as connection:
            rows = connection.execute(query, (profile_name.strip(),)).fetchall()
        return {str(row["primary_type"]): int(row["affinity"]) for row in rows}

    def list_preferences(
        self, profile_name: str, preference: int
    ) -> list[Restaurant]:
        if not profile_name.strip():
            return []
        query = """
        SELECT p.*, NULL AS open_now, NULL AS app_rating, 0 AS app_rating_count
        FROM preferences AS p
        JOIN profiles AS u ON u.id = p.profile_id
        WHERE u.user_key = ? AND p.preference = ?
        ORDER BY p.updated_at DESC
        """
        with self._connect() as connection:
            rows = connection.execute(query, (profile_name.strip(), preference)).fetchall()
        return [self._restaurant_from_row(row) for row in rows]

    def save_rating(
        self, profile_name: str, restaurant: Restaurant, stars: int
    ) -> None:
        """儲存個人對實際用餐餐廳的 1～5 星。"""
        profile_name = self._validate_profile_name(profile_name)
        if stars not in range(1, 6):
            raise ValueError("評分必須是 1～5 星。")
        with self._connect() as connection:
            profile_id = self._profile_id(connection, profile_name)
            connection.execute(
                """
                INSERT INTO ratings (
                    profile_id, place_id, stars, name, address, latitude,
                    longitude, google_rating, google_rating_count, price_level,
                    primary_type, cuisine_label, map_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, place_id) DO UPDATE SET
                    stars = excluded.stars,
                    name = excluded.name,
                    address = excluded.address,
                    google_rating = excluded.google_rating,
                    google_rating_count = excluded.google_rating_count,
                    price_level = excluded.price_level,
                    primary_type = excluded.primary_type,
                    cuisine_label = excluded.cuisine_label,
                    map_url = excluded.map_url,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    profile_id,
                    restaurant.place_id,
                    stars,
                    *self._restaurant_values(restaurant)[1:],
                ),
            )

    def rating_summary(self) -> dict[str, tuple[float, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT place_id, ROUND(AVG(stars), 2) AS average, COUNT(*) AS count
                FROM ratings GROUP BY place_id
                """
            ).fetchall()
        return {
            str(row["place_id"]): (float(row["average"]), int(row["count"]))
            for row in rows
        }

    def rating_history(self, profile_name: str) -> list[dict[str, object]]:
        if not profile_name.strip():
            return []
        query = """
        SELECT r.place_id, r.name, r.stars, r.map_url, r.updated_at
        FROM ratings AS r
        JOIN profiles AS u ON u.id = r.profile_id
        WHERE u.user_key = ?
        ORDER BY r.updated_at DESC
        """
        with self._connect() as connection:
            rows = connection.execute(query, (profile_name.strip(),)).fetchall()
        return [dict(row) for row in rows]

    def ai_preference_history(
        self, profile_name: str
    ) -> dict[str, list[dict[str, object]]]:
        """回傳給 AI 的最小化紀錄，不包含姓名、定位與完整地址。"""
        empty: dict[str, list[dict[str, object]]] = {
            "liked": [],
            "disliked": [],
            "ratings": [],
        }
        if not profile_name.strip():
            return empty
        with self._connect() as connection:
            preference_rows = connection.execute(
                """
                SELECT p.name, p.cuisine_label, p.primary_type, p.preference,
                       p.google_rating
                FROM preferences AS p
                JOIN profiles AS u ON u.id = p.profile_id
                WHERE u.user_key = ?
                ORDER BY p.updated_at DESC
                LIMIT 40
                """,
                (profile_name.strip(),),
            ).fetchall()
            rating_rows = connection.execute(
                """
                SELECT r.name, r.cuisine_label, r.primary_type, r.stars,
                       r.google_rating
                FROM ratings AS r
                JOIN profiles AS u ON u.id = r.profile_id
                WHERE u.user_key = ?
                ORDER BY r.updated_at DESC
                LIMIT 40
                """,
                (profile_name.strip(),),
            ).fetchall()

        for row in preference_rows:
            item = {
                "name": row["name"],
                "cuisine": row["cuisine_label"],
                "primary_type": row["primary_type"],
                "google_rating": row["google_rating"],
            }
            key = "liked" if int(row["preference"]) == 1 else "disliked"
            empty[key].append(item)
        empty["ratings"] = [dict(row) for row in rating_rows]
        return empty

    def create_room(
        self,
        title: str,
        host_user_key: str,
        host_name: str,
        restaurants: list[Restaurant],
    ) -> Room:
        title = title.strip()
        host_user_key = self._validate_profile_name(host_user_key)
        host_name = self._validate_profile_name(host_name)
        if not title:
            raise ValueError("活動名稱不能空白。")
        if len(restaurants) < 2:
            raise ValueError("請至少選擇兩間候選餐廳。")

        with self._connect() as connection:
            for _ in range(20):
                code = "".join(secrets.choice(self.ROOM_ALPHABET) for _ in range(6))
                if not connection.execute(
                    "SELECT 1 FROM rooms WHERE code = ?", (code,)
                ).fetchone():
                    break
            else:
                raise RuntimeError("無法產生唯一房間代碼，請稍後再試。")

            connection.execute(
                """
                INSERT INTO rooms (code, title, host_user_key, host_name)
                VALUES (?, ?, ?, ?)
                """,
                (code, title, host_user_key, host_name),
            )
            connection.execute(
                """
                INSERT INTO participants (room_code, user_key, name)
                VALUES (?, ?, ?)
                """,
                (code, host_user_key, host_name),
            )
            connection.executemany(
                """
                INSERT INTO restaurants (
                    room_code, place_id, name, address, latitude, longitude,
                    google_rating, google_rating_count, price_level, primary_type,
                    cuisine_label, open_now, map_url, ai_advice
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        code,
                        item.place_id,
                        item.name,
                        item.address,
                        item.latitude,
                        item.longitude,
                        item.google_rating,
                        item.google_rating_count,
                        item.price_level,
                        item.primary_type,
                        item.cuisine_label,
                        None if item.open_now is None else int(item.open_now),
                        item.map_url,
                        item.ai_advice,
                    )
                    for item in restaurants
                ],
            )
        room = self.get_room(code)
        if room is None:
            raise RuntimeError("房間建立失敗。")
        return room

    def get_room(self, code: str) -> Room | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM rooms WHERE code = ?", (code.strip().upper(),)
            ).fetchone()
        return Room(**dict(row)) if row else None

    def join_room(
        self, code: str, participant_user_key: str, participant_name: str
    ) -> None:
        code = code.strip().upper()
        participant_user_key = self._validate_profile_name(participant_user_key)
        participant_name = self._validate_profile_name(participant_name)
        room = self.get_room(code)
        if room is None:
            raise ValueError("找不到這個房間。")
        if room.status != "open":
            raise ValueError("此房間已結束投票。")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO participants (room_code, user_key, name)
                VALUES (?, ?, ?)
                ON CONFLICT(room_code, user_key) DO UPDATE SET
                    name = excluded.name
                """,
                (code, participant_user_key, participant_name),
            )

    def list_restaurants(self, code: str) -> list[Restaurant]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT r.*, NULL AS app_rating, 0 AS app_rating_count
                FROM restaurants AS r WHERE r.room_code = ? ORDER BY r.name
                """,
                (code.strip().upper(),),
            ).fetchall()
        return [self._restaurant_from_row(row) for row in rows]

    @staticmethod
    def _restaurant_from_row(row: sqlite3.Row) -> Restaurant:
        keys = row.keys()
        return Restaurant(
            place_id=row["place_id"],
            name=row["name"],
            address=row["address"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            google_rating=row["google_rating"],
            google_rating_count=row["google_rating_count"],
            price_level=row["price_level"],
            primary_type=row["primary_type"],
            cuisine_label=row["cuisine_label"],
            open_now=None if row["open_now"] is None else bool(row["open_now"]),
            map_url=row["map_url"],
            app_rating=row["app_rating"] if "app_rating" in keys else None,
            app_rating_count=row["app_rating_count"] if "app_rating_count" in keys else 0,
            preference=row["preference"] if "preference" in keys else None,
            ai_advice=row["ai_advice"] if "ai_advice" in keys else "",
        )

    def cast_vote(self, code: str, participant_user_key: str, place_id: str) -> None:
        code = code.strip().upper()
        participant_user_key = self._validate_profile_name(participant_user_key)
        room = self.get_room(code)
        if room is None or room.status != "open":
            raise ValueError("找不到投票中的房間。")
        with self._connect() as connection:
            if not connection.execute(
                "SELECT 1 FROM participants WHERE room_code = ? AND user_key = ?",
                (code, participant_user_key),
            ).fetchone():
                raise ValueError("請先加入房間再投票。")
            if not connection.execute(
                "SELECT 1 FROM restaurants WHERE room_code = ? AND place_id = ?",
                (code, place_id),
            ).fetchone():
                raise ValueError("這間餐廳不在候選名單中。")
            connection.execute(
                """
                INSERT INTO votes (room_code, participant_user_key, place_id)
                VALUES (?, ?, ?)
                ON CONFLICT(room_code, participant_user_key) DO UPDATE SET
                    place_id = excluded.place_id, voted_at = CURRENT_TIMESTAMP
                """,
                (code, participant_user_key, place_id),
            )

    def get_vote_results(self, code: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT r.place_id, r.name, COUNT(v.participant_user_key) AS votes
                FROM restaurants AS r
                LEFT JOIN votes AS v
                  ON v.room_code = r.room_code AND v.place_id = r.place_id
                WHERE r.room_code = ?
                GROUP BY r.place_id, r.name
                ORDER BY votes DESC, r.name ASC
                """,
                (code.strip().upper(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def finalize_vote(self, code: str) -> Restaurant:
        code = code.strip().upper()
        room = self.get_room(code)
        if room is None:
            raise ValueError("找不到這個房間。")
        if room.status == "closed":
            winner = self.get_winner(code)
            if winner is None:
                raise RuntimeError("找不到得勝餐廳。")
            return winner
        results = self.get_vote_results(code)
        if not results or int(results[0]["votes"]) == 0:
            raise ValueError("目前還沒有人投票。")
        top_votes = int(results[0]["votes"])
        tied = [item for item in results if int(item["votes"]) == top_votes]
        if len(tied) > 1:
            raise TieVoteError(
                "最高票平手：" + "、".join(str(item["name"]) for item in tied)
            )
        winner_id = str(results[0]["place_id"])
        with self._connect() as connection:
            connection.execute(
                "UPDATE rooms SET status = 'closed', winner_place_id = ? WHERE code = ?",
                (winner_id, code),
            )
        winner = self.get_winner(code)
        if winner is None:
            raise RuntimeError("找不到得勝餐廳。")
        return winner

    def get_winner(self, code: str) -> Restaurant | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT r.*, NULL AS app_rating, 0 AS app_rating_count
                FROM rooms
                JOIN restaurants AS r
                  ON r.room_code = rooms.code AND r.place_id = rooms.winner_place_id
                WHERE rooms.code = ? AND rooms.status = 'closed'
                """,
                (code.strip().upper(),),
            ).fetchone()
        return self._restaurant_from_row(row) if row else None
