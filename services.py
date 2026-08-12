"""Google Places 串接與第三版個人化排序。"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime

import requests

from .models import Restaurant, SearchFilters


CUISINE_QUERIES = {
    "中式": "中式料理",
    "日式": "日式料理",
    "韓式": "韓式料理",
    "義式": "義式料理",
    "美式": "美式料理",
    "泰式": "泰式料理",
    "素食": "素食餐廳",
    "咖啡輕食": "咖啡輕食",
}

TYPE_LABELS = {
    "chinese_restaurant": "中式",
    "japanese_restaurant": "日式",
    "korean_restaurant": "韓式",
    "italian_restaurant": "義式",
    "american_restaurant": "美式",
    "thai_restaurant": "泰式",
    "vegetarian_restaurant": "素食",
    "vegan_restaurant": "素食",
    "cafe": "咖啡輕食",
}

class GooglePlacesError(RuntimeError):
    """Google Places API 搜尋失敗。"""


class GooglePlacesClient:
    """Places Text Search（New）的第三版 client。"""

    SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
    FIELD_MASK = ",".join(
        [
            "places.id",
            "places.displayName",
            "places.formattedAddress",
            "places.location",
            "places.rating",
            "places.userRatingCount",
            "places.priceLevel",
            "places.primaryType",
            "places.primaryTypeDisplayName",
            "places.currentOpeningHours.openNow",
            "places.regularOpeningHours.periods",
            "places.googleMapsUri",
        ]
    )

    def __init__(self, api_key: str | None) -> None:
        self.api_key = (api_key or "").strip()

    @property
    def demo_mode(self) -> bool:
        return not self.api_key

    def search(
        self,
        filters: SearchFilters,
        latitude: float,
        longitude: float,
        max_results: int = 20,
    ) -> list[Restaurant]:
        """把選項式條件轉成 Google request，並執行本地的嚴格篩選。"""
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("經緯度超出有效範圍。")
        max_results = max(1, min(int(max_results), 20))

        if self.demo_mode:
            restaurants = self._demo_restaurants(latitude, longitude, filters)
        else:
            restaurants = self._request_google(
                filters, latitude, longitude, max_results
            )

        filtered: list[Restaurant] = []
        for restaurant in restaurants:
            restaurant.distance_km = self._distance_km(
                latitude,
                longitude,
                restaurant.latitude,
                restaurant.longitude,
            )
            if restaurant.distance_km * 1000 > filters.radius_meters:
                continue
            if (
                filters.minimum_rating is not None
                and (restaurant.google_rating or 0) < filters.minimum_rating
            ):
                continue
            if (
                filters.price_levels
                and restaurant.price_level not in filters.price_levels
            ):
                continue
            if filters.opening_mode == "現在營業" and restaurant.open_now is not True:
                continue
            if (
                filters.opening_mode == "指定時間"
                and restaurant.open_at_selected_time is not True
            ):
                continue
            filtered.append(restaurant)
        return filtered

    def _request_google(
        self,
        filters: SearchFilters,
        latitude: float,
        longitude: float,
        max_results: int,
    ) -> list[Restaurant]:
        cuisine_text = "、".join(
            CUISINE_QUERIES[item]
            for item in filters.cuisines
            if item in CUISINE_QUERIES
        )
        payload: dict[str, object] = {
            "textQuery": f"{cuisine_text or '餐廳'} 附近餐廳",
            "includedType": "restaurant",
            "languageCode": "zh-TW",
            "maxResultCount": max_results,
            "locationBias": {
                "circle": {
                    "center": {"latitude": latitude, "longitude": longitude},
                    "radius": float(filters.radius_meters),
                }
            },
        }
        if filters.minimum_rating is not None:
            payload["minRating"] = filters.minimum_rating
        if filters.opening_mode == "現在營業":
            payload["openNow"] = True

        try:
            response = requests.post(
                self.SEARCH_URL,
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": self.api_key,
                    "X-Goog-FieldMask": self.FIELD_MASK,
                },
                json=payload,
                timeout=15,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            detail = ""
            if getattr(exc, "response", None) is not None:
                detail = f"：{exc.response.text[:300]}"
            raise GooglePlacesError(f"Google Places 搜尋失敗{detail}") from exc

        return [
            self._parse_place(item, filters.selected_datetime)
            for item in response.json().get("places", [])
        ]

    @classmethod
    def _parse_place(
        cls,
        item: dict[str, object],
        selected_datetime: datetime | None,
    ) -> Restaurant:
        location = item.get("location") or {}
        display_name = item.get("displayName") or {}
        primary_display = item.get("primaryTypeDisplayName") or {}
        current_hours = item.get("currentOpeningHours") or {}
        regular_hours = item.get("regularOpeningHours") or {}
        primary_type = str(item.get("primaryType", "restaurant"))
        cuisine_label = TYPE_LABELS.get(
            primary_type, str(primary_display.get("text", "其他"))
        )
        return Restaurant(
            place_id=str(item.get("id", "")),
            name=str(display_name.get("text", "未命名餐廳")),
            address=str(item.get("formattedAddress", "地址未提供")),
            latitude=float(location.get("latitude", 0)),
            longitude=float(location.get("longitude", 0)),
            google_rating=(
                float(item["rating"]) if item.get("rating") is not None else None
            ),
            google_rating_count=int(item.get("userRatingCount", 0)),
            price_level=str(item["priceLevel"]) if item.get("priceLevel") else None,
            primary_type=primary_type,
            cuisine_label=cuisine_label,
            open_now=current_hours.get("openNow"),
            open_at_selected_time=cls._is_open_at(
                list(regular_hours.get("periods", [])), selected_datetime
            ),
            map_url=str(item.get("googleMapsUri", "")),
        )

    @staticmethod
    def _is_open_at(
        periods: list[dict[str, object]], target: datetime | None
    ) -> bool | None:
        """依 Google 的 weekly periods 判斷指定本地時間是否營業。"""
        if target is None:
            return None
        if not periods:
            return None
        google_day = (target.weekday() + 1) % 7
        target_minute = google_day * 1440 + target.hour * 60 + target.minute
        week_minutes = 7 * 1440

        for period in periods:
            opened = period.get("open") or {}
            closed = period.get("close") or {}
            if not opened:
                continue
            open_minute = (
                int(opened.get("day", 0)) * 1440
                + int(opened.get("hour", 0)) * 60
                + int(opened.get("minute", 0))
            )
            if not closed:
                return True
            close_minute = (
                int(closed.get("day", 0)) * 1440
                + int(closed.get("hour", 0)) * 60
                + int(closed.get("minute", 0))
            )
            if close_minute <= open_minute:
                close_minute += week_minutes
            adjusted_target = target_minute
            if adjusted_target < open_minute:
                adjusted_target += week_minutes
            if open_minute <= adjusted_target < close_minute:
                return True
        return False

    @staticmethod
    def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius = 6371.0088
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)
        value = (
            math.sin(d_phi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        )
        return 2 * radius * math.atan2(math.sqrt(value), math.sqrt(1 - value))

    @staticmethod
    def _demo_restaurants(
        latitude: float,
        longitude: float,
        filters: SearchFilters,
    ) -> list[Restaurant]:
        """產生虛構資料，讓沒有 API Key 時也能完整測試新版。"""
        seed_text = "|".join(filters.cuisines) or "all"
        seed = hashlib.sha1(seed_text.encode("utf-8")).hexdigest()[:8]
        types = list(filters.cuisines) or list(CUISINE_QUERIES)
        prices = [
            "PRICE_LEVEL_INEXPENSIVE",
            "PRICE_LEVEL_MODERATE",
            "PRICE_LEVEL_EXPENSIVE",
            "PRICE_LEVEL_VERY_EXPENSIVE",
        ]
        results: list[Restaurant] = []
        for index in range(12):
            cuisine = types[index % len(types)]
            angle = index * math.pi / 6
            distance_degree = 0.0015 + index * 0.00035
            results.append(
                Restaurant(
                    place_id=f"demo-v3-{seed}-{index}",
                    name=f"{cuisine}示範餐廳 {index + 1}",
                    address="展示模式地址（不是實際店家）",
                    latitude=latitude + math.cos(angle) * distance_degree,
                    longitude=longitude + math.sin(angle) * distance_degree,
                    google_rating=round(3.4 + (index % 7) * 0.25, 1),
                    google_rating_count=45 + index * 37,
                    price_level=prices[index % len(prices)],
                    primary_type=f"demo_{cuisine}",
                    cuisine_label=cuisine,
                    open_now=index % 4 != 0,
                    open_at_selected_time=index % 3 != 0,
                    map_url=(
                        "https://www.google.com/maps/search/?api=1&query="
                        f"{latitude},{longitude}"
                    ),
                )
            )
        return results


class RankingService:
    """以個人偏好與星級排序，但不改變已套用的篩選條件。"""

    @staticmethod
    def rank(
        restaurants: list[Restaurant],
        rating_summary: dict[str, tuple[float, int]],
        preferences: dict[str, int],
        cuisine_affinity: dict[str, int],
    ) -> list[Restaurant]:
        ranked: list[Restaurant] = []
        for restaurant in restaurants:
            restaurant.preference = preferences.get(restaurant.place_id)
            if restaurant.preference == -1:
                continue
            if restaurant.place_id in rating_summary:
                restaurant.app_rating, restaurant.app_rating_count = rating_summary[
                    restaurant.place_id
                ]
            ranked.append(restaurant)

        def score(item: Restaurant) -> tuple[float, int, str]:
            value = 0.0
            if item.preference == 1:
                value += 100
            value += cuisine_affinity.get(item.primary_type, 0) * 8
            value += (item.app_rating or 0) * 5
            value += item.google_rating or 0
            value -= item.distance_km or 0
            return value, item.google_rating_count, item.name

        return sorted(ranked, key=score, reverse=True)
