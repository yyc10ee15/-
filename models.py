"""第三版使用的資料模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class Restaurant:
    """Google 餐廳資料與本系統的個人化欄位。"""

    place_id: str
    name: str
    address: str
    latitude: float
    longitude: float
    google_rating: float | None = None
    google_rating_count: int = 0
    price_level: str | None = None
    primary_type: str = "restaurant"
    cuisine_label: str = "其他"
    open_now: bool | None = None
    open_at_selected_time: bool | None = None
    map_url: str = ""
    distance_km: float | None = None
    app_rating: float | None = None
    app_rating_count: int = 0
    preference: int | None = None
    ai_advice: str = ""


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """使用者透過選項設定的搜尋條件，不再使用自由文字。"""

    radius_meters: int
    minimum_rating: float | None
    cuisines: tuple[str, ...]
    opening_mode: str
    selected_datetime: datetime | None = None
    price_levels: tuple[str, ...] = ()


@dataclass(slots=True)
class Room:
    """多人模式的一次投票活動。"""

    code: str
    title: str
    host_user_key: str
    host_name: str
    status: str
    winner_place_id: str | None
    created_at: str
