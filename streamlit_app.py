"""餐廳選擇器第三版：加入 Web AI 個人偏好分析與候選解說。"""

from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="一鍵開吃",
    page_icon=":material/restaurant:",
    layout="centered",
)

from app_core import (  # noqa: E402 - page config 必須最先執行
    AIAdvisor,
    AIAdvisorError,
    Database,
    GooglePlacesClient,
    GooglePlacesError,
    RankingService,
    Restaurant,
    Room,
    SearchFilters,
    TieVoteError,
    UserIdentity,
)
from app_core.google_map import (  # noqa: E402
    google_map_picker,
    google_restaurant_map,
)
from app_core.share_link import share_room_link  # noqa: E402


ROOT = Path(__file__).resolve().parent
DISTANCE_OPTIONS = {"不限": 50_000, "1 km": 1_000, "3 km": 3_000, "5 km": 5_000}
RATING_OPTIONS = {"不限": None, "3 星以上": 3.0, "4 星以上": 4.0}
PRICE_OPTIONS = {
    "不限": (),
    "NT$0–200": ("PRICE_LEVEL_INEXPENSIVE",),
    "NT$200–400": ("PRICE_LEVEL_MODERATE",),
    "NT$400–600": ("PRICE_LEVEL_EXPENSIVE",),
    "NT$600 以上": ("PRICE_LEVEL_VERY_EXPENSIVE",),
}
CUISINE_OPTIONS = ["中式", "日式", "韓式", "義式", "美式", "泰式", "素食", "咖啡輕食"]


@st.cache_resource
def get_database() -> Database:
    """取得資料庫"""
    return Database(ROOT / "data" / "restaurant_selector_v3.db")


@st.cache_data(ttl=600, show_spinner=False)
def cached_search(
    api_key: str,
    filters: SearchFilters,
    latitude: float,
    longitude: float,
) -> list[Restaurant]:
    """快取相同選項與位置的搜尋 10 分鐘。"""
    return GooglePlacesClient(api_key).search(
        filters=filters,
        latitude=latitude,
        longitude=longitude,
    )


@st.cache_data(ttl=3600, max_entries=100, show_spinner=False)
def cached_ai_analysis(
    _base_url: str,
    _api_key: str,
    model: str,
    history: dict[str, list[dict[str, object]]],
    restaurants: list[Restaurant],
) -> dict[str, object]:
    """相同設定、歷史與候選名單一小時內不重複呼叫 Open WebUI。"""
    return (
        AIAdvisor(_base_url, _api_key, model)
        .analyze(history, restaurants)
        .model_dump()
    )


def read_api_key() -> str:
    env_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        return str(st.secrets.get("GOOGLE_MAPS_API_KEY", "")).strip()
    except FileNotFoundError:
        return ""


def read_open_webui_config() -> tuple[str, str, str]:
    """Open WebUI 設定僅從伺服器環境或 Streamlit secrets 讀取。"""
    base_url = os.getenv("OPEN_WEBUI_BASE_URL", "").strip()
    api_key = os.getenv("OPEN_WEBUI_API_KEY", "").strip()
    model = os.getenv("OPEN_WEBUI_MODEL", "").strip()
    try:
        base_url = base_url or str(
            st.secrets.get("OPEN_WEBUI_BASE_URL", "")
        ).strip()
        api_key = api_key or str(
            st.secrets.get("OPEN_WEBUI_API_KEY", "")
        ).strip()
        model = model or str(st.secrets.get("OPEN_WEBUI_MODEL", "")).strip()
    except FileNotFoundError:
        pass
    return base_url.rstrip("/"), api_key, model or "gpt-oss:20b"


def read_google_maps_browser_config() -> tuple[str, str]:
    """讀取前端 Google Maps 專用 Key 與 Map ID。"""
    browser_key = os.getenv("GOOGLE_MAPS_BROWSER_API_KEY", "").strip()
    map_id = os.getenv("GOOGLE_MAP_ID", "").strip()
    try:
        browser_key = browser_key or str(
            st.secrets.get("GOOGLE_MAPS_BROWSER_API_KEY", "")
        ).strip()
        map_id = map_id or str(st.secrets.get("GOOGLE_MAP_ID", "")).strip()
    except FileNotFoundError:
        pass
    return browser_key, map_id


def google_auth_is_configured() -> bool:
    """Return whether the named Google OIDC provider exists in Secrets."""
    try:
        auth = st.secrets.get("auth", {})
        google = auth.get("google", {}) if auth else {}
        return bool(
            auth.get("redirect_uri")
            and auth.get("cookie_secret")
            and google.get("client_id")
            and google.get("client_secret")
            and google.get("server_metadata_url")
        )
    except (FileNotFoundError, AttributeError):
        return False


def current_google_identity() -> UserIdentity | None:
    """Build an application identity from Streamlit's verified OIDC claims."""
    if not getattr(st.user, "is_logged_in", False):
        return None
    subject = str(st.user.get("sub", "")).strip()
    if not subject:
        return None
    name = str(st.user.get("name", "")).strip()
    email = str(st.user.get("email", "")).strip()
    return UserIdentity(
        user_key=f"google:{subject}",
        display_name=name or email or "Google 使用者",
        auth_type="google",
        email=email,
        picture_url=str(st.user.get("picture", "")).strip(),
    )


def clear_personal_session() -> None:
    """Remove identity-specific UI state without affecting room query params."""
    for key in (
        "guest_user_key",
        "guest_display_name",
        "recommendations",
        "chosen_restaurant",
        "ai_preference_summary",
        "ai_error",
    ):
        st.session_state.pop(key, None)


def reusable_guest_profiles(database: Database) -> list[tuple[str, str]]:
    """Read local profiles without depending on a hot-reloaded Database method."""
    with database._connect() as connection:
        rows = connection.execute(
            """
            SELECT user_key, display_name
            FROM profiles
            WHERE auth_type = 'guest'
            ORDER BY last_seen_at DESC, id DESC
            """
        ).fetchall()
    return [(str(row["user_key"]), str(row["display_name"])) for row in rows]


def render_sign_in(database: Database) -> None:
    """Create a local guest identity for preferences and multiplayer voting."""
    st.title("一鍵開吃", text_alignment="center")
    st.caption("繼續使用原有紀錄，或建立新的使用者。", text_alignment="center")
    with st.container(border=True):
        guest_profiles = reusable_guest_profiles(database)
        if guest_profiles:
            st.subheader("繼續使用原有紀錄")
            profile_by_name = {
                display_name: user_key for user_key, display_name in guest_profiles
            }
            selected_name = st.selectbox(
                "選擇使用者",
                list(profile_by_name),
                key="existing_guest_profile",
            )
            if st.button(
                "繼續使用",
                icon=":material/login:",
                type="primary",
                width="stretch",
            ):
                st.session_state.guest_user_key = profile_by_name[selected_name]
                st.session_state.guest_display_name = selected_name
                st.rerun()
            st.divider()

        st.subheader("建立新使用者")
        with st.form("new-guest-form"):
            requested_name = st.text_input(
                "新使用者名稱",
                placeholder="例如：小明",
                max_chars=40,
            )
            guest_submit = st.form_submit_button(
                "建立並開始使用",
                icon=":material/person_add:",
                width="stretch",
            )
        if guest_submit:
            try:
                user_key = f"guest:{uuid.uuid4().hex}"
                display_name = database.register_guest(user_key, requested_name)
                st.session_state.guest_user_key = user_key
                st.session_state.guest_display_name = display_name
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def read_app_base_url() -> str:
    """取得其他裝置也能開啟的網站網址。"""
    base_url = os.getenv("APP_BASE_URL", "").strip()
    try:
        return base_url or str(st.secrets.get("APP_BASE_URL", "")).strip()
    except FileNotFoundError:
        return base_url


def price_label(value: str | None) -> str:
    reverse = {
        "PRICE_LEVEL_INEXPENSIVE": "$",
        "PRICE_LEVEL_MODERATE": "$$",
        "PRICE_LEVEL_EXPENSIVE": "$$$",
        "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$",
    }
    return reverse.get(value or "", "價位未提供")


def build_room_share_url(room_code: str, configured_base_url: str = "") -> str:
    """建立可直接開啟多人房間的完整連結。"""
    base_url = configured_base_url or str(st.context.url or "")
    if not base_url:
        return f"?room={room_code}"
    parsed = urlsplit(base_url)
    clean_base = urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
    )
    return f"{clean_base}?room={room_code}"


def render_room_invitation(room: Room, app_base_url: str) -> None:
    """顯示手機分享、複製及可長按的房間連結。"""
    share_url = build_room_share_url(room.code, app_base_url)
    with st.container(border=True):
        st.subheader(":material/share: 邀請朋友")
        share_room_link(
            share_url,
            room.title,
            key=f"share-room-{room.code}",
        )
        st.text_input(
            "邀請連結（手機可長按選取）",
            value=share_url,
            key=f"share-url-{room.code}",
        )
        if "localhost" in share_url or "127.0.0.1" in share_url:
            st.warning(
                "目前連結只適用這台電腦。若要讓手機加入，請在 secrets.toml 設定 APP_BASE_URL。",
                icon=":material/warning:",
            )


def render_location_controls(
    browser_api_key: str,
    map_id: str,
) -> tuple[float, float]:
    """以 Google 地圖或手動座標選擇搜尋中心。"""
    with st.expander("位置", icon=":material/my_location:", expanded=False):
        if browser_api_key:
            map_position = google_map_picker(
                browser_api_key,
                float(st.session_state.current_lat),
                float(st.session_state.current_lng),
                map_id=map_id,
                key="v3-google-map-picker",
            )
            map_signature = (
                (
                    round(float(map_position["latitude"]), 7),
                    round(float(map_position["longitude"]), 7),
                )
                if map_position
                else None
            )
            if map_signature and map_signature != st.session_state.last_applied_position:
                st.session_state.current_lat, st.session_state.current_lng = map_signature
                st.session_state.last_applied_position = map_signature
            if map_position and map_position.get("label"):
                st.caption(f"地圖選擇：{map_position['label']}")
        else:
            st.caption(
                "加入 GOOGLE_MAPS_BROWSER_API_KEY 後，可直接搜尋地址或在 Google 地圖點選位置。"
            )
        columns = st.columns(2)
        with columns[0]:
            latitude = st.number_input(
                "緯度",
                min_value=-90.0,
                max_value=90.0,
                format="%.7f",
                key="current_lat",
            )
        with columns[1]:
            longitude = st.number_input(
                "經度",
                min_value=-180.0,
                max_value=180.0,
                format="%.7f",
                key="current_lng",
            )
    return float(latitude), float(longitude)


def render_filter_controls() -> SearchFilters:
    """使用可點選、可拖曳的元件組成選項式條件。"""
    with st.container(border=True):
        st.subheader(":material/filter_alt: 篩選條件")
        distance = st.segmented_control(
            "距離",
            list(DISTANCE_OPTIONS),
            default="3 km",
            key="filter_distance",
        )
        rating = st.segmented_control(
            "Google 評分",
            list(RATING_OPTIONS),
            default="不限",
            key="filter_rating",
        )
        price = st.segmented_control(
            "每人預算（約）",
            list(PRICE_OPTIONS),
            default="不限",
            key="filter_price",
        )
        st.caption("價位依 Google Places 提供的概略等級換算，實際消費依店家為準。")
        cuisines = st.pills(
            "菜系（可複選）",
            CUISINE_OPTIONS,
            selection_mode="multi",
            key="filter_cuisines",
        )
        opening_mode = st.segmented_control(
            "營業時間",
            ["不限", "現在營業", "指定時間"],
            default="不限",
            key="filter_opening",
        )
        selected_datetime = None
        if opening_mode == "指定時間":
            time_columns = st.columns(2)
            with time_columns[0]:
                selected_date = st.date_input("日期", key="filter_date")
            with time_columns[1]:
                selected_time = st.time_input("時間", key="filter_time")
            selected_datetime = datetime.combine(selected_date, selected_time)

    return SearchFilters(
        radius_meters=DISTANCE_OPTIONS.get(distance or "3 km", 3_000),
        minimum_rating=RATING_OPTIONS.get(rating or "不限"),
        cuisines=tuple(cuisines or []),
        opening_mode=opening_mode or "不限",
        selected_datetime=selected_datetime,
        price_levels=PRICE_OPTIONS.get(price or "不限", ()),
    )


def restaurant_facts(restaurant: Restaurant) -> str:
    facts = [restaurant.cuisine_label]
    if restaurant.distance_km is not None:
        facts.append(f"{restaurant.distance_km:.1f} km")
    if restaurant.google_rating is not None:
        facts.append(f"Google {restaurant.google_rating:.1f} ★")
    facts.append(price_label(restaurant.price_level))
    if restaurant.app_rating is not None:
        facts.append(f"用餐回饋 {restaurant.app_rating:.1f} ★")
    if restaurant.open_now is True:
        facts.append("營業中")
    return " · ".join(facts)


def render_restaurant_card(restaurant: Restaurant) -> None:
    """餐廳資訊卡；操作按鈕由外層依單人或多人模式決定。"""
    st.subheader(restaurant.name)
    st.caption(restaurant_facts(restaurant))
    st.write(restaurant.address)
    if restaurant.map_url:
        st.link_button(
            "Google Maps",
            restaurant.map_url,
            icon=":material/map:",
        )
    if restaurant.ai_advice:
        st.info(
            f"AI 挑選建議：{restaurant.ai_advice}",
            icon=":material/psychology:",
        )


def save_preference(
    database: Database,
    profile_name: str,
    restaurant: Restaurant,
    preference: int,
) -> None:
    try:
        database.save_preference(profile_name, restaurant, preference)
        message = "已加入喜歡清單" if preference == 1 else "已加入不喜歡清單"
        st.toast(message, icon=":material/favorite:" if preference == 1 else ":material/close:")
        if preference == -1:
            st.session_state.recommendations = [
                item
                for item in st.session_state.recommendations
                if item.place_id != restaurant.place_id
            ]
        st.rerun()
    except ValueError as exc:
        st.error(str(exc), icon=":material/error:")


def render_rating_panel(
    database: Database,
    profile_name: str,
    restaurant: Restaurant,
) -> None:
    """決定餐廳後才顯示 1～5 星，和喜歡／不喜歡分開。"""
    with st.container(border=True):
        st.subheader(":material/check_circle: 最後選中的餐廳")
        render_restaurant_card(restaurant)
        st.caption("用餐後可留下 1～5 星；這是實際用餐評價。")
        score = st.feedback(
            "stars",
            key=f"rating-{profile_name}-{restaurant.place_id}",
        )
        if st.button(
            "儲存星級",
            type="primary",
            icon=":material/save:",
            disabled=score is None,
            key=f"save-rating-{restaurant.place_id}",
        ):
            try:
                database.save_rating(profile_name, restaurant, int(score) + 1)
                st.success(f"已儲存 {int(score) + 1} 星。", icon=":material/check_circle:")
            except ValueError as exc:
                st.error(str(exc), icon=":material/error:")


def render_single_results(
    database: Database,
    profile_name: str,
    recommendations: list[Restaurant],
) -> None:
    """單人推薦：每批五間，可喜歡、不喜歡、換一批或隨機決定。"""
    if not recommendations:
        st.warning("目前沒有符合條件的餐廳，請放寬部分條件。", icon=":material/search_off:")
        return
    batch_size = 5
    batch_count = max(1, (len(recommendations) + batch_size - 1) // batch_size)
    st.session_state.batch_index %= batch_count
    start = st.session_state.batch_index * batch_size
    batch = recommendations[start : start + batch_size]

    st.subheader(f"推薦結果（第 {st.session_state.batch_index + 1} 批）")
    for restaurant in batch:
        with st.container(border=True):
            render_restaurant_card(restaurant)
            with st.container(horizontal=True):
                if st.button(
                    "不喜歡",
                    icon=":material/thumb_down:",
                    key=f"dislike-{restaurant.place_id}",
                ):
                    save_preference(database, profile_name, restaurant, -1)
                if st.button(
                    "喜歡",
                    icon=":material/favorite:",
                    key=f"like-{restaurant.place_id}",
                ):
                    save_preference(database, profile_name, restaurant, 1)
                if st.button(
                    "決定吃這間",
                    type="primary",
                    icon=":material/check:",
                    key=f"choose-{restaurant.place_id}",
                ):
                    st.session_state.chosen_restaurant = restaurant
                    st.rerun()

    with st.container(horizontal=True, horizontal_alignment="distribute"):
        if st.button("換一批", icon=":material/refresh:", disabled=batch_count == 1):
            st.session_state.batch_index = (st.session_state.batch_index + 1) % batch_count
            st.rerun()
        if st.button("隨機選一間", type="primary", icon=":material/casino:"):
            st.session_state.chosen_restaurant = secrets.choice(batch)
            st.rerun()


def render_multiplayer_setup(
    database: Database,
    user_key: str,
    display_name: str,
    recommendations: list[Restaurant],
    app_base_url: str,
) -> None:
    """多人模式：從已篩好的推薦結果建立房間。"""
    if len(recommendations) < 2:
        st.warning("至少需要兩間符合條件的餐廳才能建立投票。")
        return
    st.subheader(":material/groups: 建立多人投票")
    st.caption("以下是本次篩選出的候選餐廳，資訊與單人模式相同。")
    for restaurant in recommendations:
        with st.container(border=True):
            render_restaurant_card(restaurant)

    activity_name = st.text_input(
        "活動名稱",
        placeholder="例如：週五晚餐",
        key="multiplayer_title",
    )
    lookup = {item.place_id: item for item in recommendations}
    selected_ids = st.multiselect(
        "候選餐廳（至少兩間）",
        options=list(lookup),
        format_func=lambda place_id: lookup[place_id].name,
        key="multiplayer_candidates",
    )
    if st.button(
        "建立房間",
        type="primary",
        icon=":material/group_add:",
        width="stretch",
    ):
        try:
            room = database.create_room(
                activity_name,
                user_key,
                display_name,
                [lookup[place_id] for place_id in selected_ids],
            )
            st.query_params["room"] = room.code
            st.session_state.room_code = room.code
            st.success(f"房間建立成功：{room.code}", icon=":material/check_circle:")
            render_room_invitation(room, app_base_url)
        except ValueError as exc:
            st.error(str(exc), icon=":material/error:")


def show_recommendation_page(
    database: Database,
    api_key: str,
    open_webui_base_url: str,
    open_webui_api_key: str,
    open_webui_model: str,
    browser_api_key: str,
    map_id: str,
    user_key: str,
    display_name: str,
    app_base_url: str,
) -> None:
    st.header(":material/restaurant: 今天想吃什麼？")
    mode = st.segmented_control(
        "使用模式",
        ["單人模式", "多人模式"],
        default="單人模式",
        key="use_mode",
    )
    st.caption("預設單人推薦；需要朋友一起決定時再切換多人模式。")
    latitude, longitude = render_location_controls(browser_api_key, map_id)
    filters = render_filter_controls()

    if not api_key:
        st.info(
            "目前為展示模式，使用虛構餐廳；設定 Google API Key 後會搜尋真實店家。",
            icon=":material/info:",
        )
    if not open_webui_base_url or not open_webui_api_key:
        st.info(
            "AI 偏好分析尚未啟用；加入 Open WebUI 網址與 API Key 後，每間候選餐廳會顯示個人化挑選建議。",
            icon=":material/psychology:",
        )

    if st.button(
        "套用條件並推薦",
        type="primary",
        icon=":material/auto_awesome:",
        width="stretch",
    ):
        try:
            with st.spinner("正在整理最適合的餐廳…"):
                found = cached_search(api_key, filters, latitude, longitude)
                recommendations = RankingService.rank(
                    found,
                    database.rating_summary(),
                    database.preference_map(user_key),
                    database.cuisine_affinity(user_key),
                )
                st.session_state.ai_preference_summary = ""
                st.session_state.ai_error = ""
                if open_webui_base_url and open_webui_api_key and recommendations:
                    analysis = cached_ai_analysis(
                        open_webui_base_url,
                        open_webui_api_key,
                        open_webui_model,
                        database.ai_preference_history(user_key),
                        recommendations,
                    )
                    advice_map = {
                        str(item["place_id"]): str(item["advice"])
                        for item in analysis.get("candidates", [])
                    }
                    for restaurant in recommendations:
                        restaurant.ai_advice = advice_map.get(restaurant.place_id, "")
                    st.session_state.ai_preference_summary = str(
                        analysis.get("preference_summary", "")
                    )
                st.session_state.recommendations = recommendations
                st.session_state.batch_index = 0
                st.session_state.chosen_restaurant = None
        except AIAdvisorError as exc:
            st.session_state.recommendations = recommendations
            st.session_state.batch_index = 0
            st.session_state.chosen_restaurant = None
            st.session_state.ai_error = str(exc)
            st.warning(
                "餐廳推薦已完成，但 AI 解說暫時失敗；你仍可正常挑選。",
                icon=":material/warning:",
            )
        except (ValueError, GooglePlacesError) as exc:
            st.error(str(exc), icon=":material/error:")

    recommendations: list[Restaurant] = st.session_state.recommendations
    if st.session_state.ai_preference_summary:
        st.success(
            f"AI 偏好摘要：{st.session_state.ai_preference_summary}",
            icon=":material/psychology:",
        )
    if st.session_state.ai_error:
        st.caption(f"AI 狀態：{st.session_state.ai_error}")
    if recommendations:
        if browser_api_key:
            google_restaurant_map(
                browser_api_key,
                [
                    {
                        "place_id": item.place_id,
                        "name": item.name,
                        "address": item.address,
                        "latitude": item.latitude,
                        "longitude": item.longitude,
                        "cuisine": item.cuisine_label,
                        "distance_km": item.distance_km,
                        "google_rating": item.google_rating,
                        "map_url": item.map_url,
                    }
                    for item in recommendations
                ],
                latitude=latitude,
                longitude=longitude,
                map_id=map_id,
                key="v3-google-restaurant-map",
            )
        else:
            map_frame = pd.DataFrame(
                [{"lat": item.latitude, "lon": item.longitude} for item in recommendations]
            )
            st.map(map_frame)

    if mode == "多人模式":
        render_multiplayer_setup(
            database,
            user_key,
            display_name,
            recommendations,
            app_base_url,
        )
    else:
        render_single_results(database, user_key, recommendations)

    chosen: Restaurant | None = st.session_state.chosen_restaurant
    if chosen is not None:
        render_rating_panel(database, user_key, chosen)


def render_preference_list(
    database: Database,
    profile_name: str,
    preference: int,
) -> None:
    restaurants = database.list_preferences(profile_name, preference)
    if not restaurants:
        st.caption("目前沒有紀錄。")
        return
    for restaurant in restaurants:
        with st.container(border=True):
            render_restaurant_card(restaurant)
            with st.container(horizontal=True):
                opposite = -preference
                if st.button(
                    "改成不喜歡" if preference == 1 else "改成喜歡",
                    key=f"flip-{preference}-{restaurant.place_id}",
                ):
                    database.save_preference(profile_name, restaurant, opposite)
                    st.rerun()
                if st.button(
                    "移除紀錄",
                    icon=":material/delete:",
                    key=f"remove-{preference}-{restaurant.place_id}",
                ):
                    database.remove_preference(profile_name, restaurant.place_id)
                    st.rerun()


def show_preferences_page(database: Database, profile_name: str) -> None:
    st.header(":material/favorite: 我的偏好")
    if not profile_name.strip():
        st.info("請先在左側輸入你的名稱。", icon=":material/person:")
        return
    liked_tab, disliked_tab, ratings_tab = st.tabs(
        ["喜歡的餐廳", "不喜歡的餐廳", "用餐星級"]
    )
    with liked_tab:
        render_preference_list(database, profile_name, 1)
    with disliked_tab:
        render_preference_list(database, profile_name, -1)
    with ratings_tab:
        history = database.rating_history(profile_name)
        if not history:
            st.caption("目前沒有用餐後星級。")
        for item in history:
            with st.container(border=True):
                st.subheader(str(item["name"]))
                st.write(f"{item['stars']} ★")
                if item["map_url"]:
                    st.link_button("Google Maps", str(item["map_url"]))


def show_room_page(
    database: Database,
    user_key: str,
    display_name: str,
    app_base_url: str,
) -> None:
    st.header(":material/how_to_vote: 多人房間")
    room_code = st.text_input("房間代碼", key="room_code").strip().upper()
    if not room_code:
        st.caption("從推薦頁建立房間，或輸入朋友分享的六碼代碼。")
        return
    room = database.get_room(room_code)
    if room is None:
        st.warning("找不到這個房間。", icon=":material/warning:")
        return
    if not user_key.strip():
        st.info("請先在左側輸入你的名稱。")
        return

    st.subheader(room.title)
    st.caption(f"發起者：{room.host_name} · {'投票中' if room.status == 'open' else '已結束'}")
    render_room_invitation(room, app_base_url)
    restaurants = database.list_restaurants(room.code)
    lookup = {item.place_id: item for item in restaurants}

    st.subheader(":material/restaurant: 候選餐廳資訊")
    for restaurant in restaurants:
        with st.container(border=True):
            render_restaurant_card(restaurant)

    if room.status == "open":
        if st.button("加入房間", icon=":material/login:"):
            try:
                database.join_room(room.code, user_key, display_name)
                st.toast("已加入房間")
            except ValueError as exc:
                st.error(str(exc))
        selected = st.selectbox(
            "選擇一間餐廳",
            options=list(lookup),
            format_func=lambda place_id: lookup[place_id].name,
            index=None,
            placeholder="選擇你想吃的餐廳",
        )
        if st.button(
            "送出我的票",
            type="primary",
            icon=":material/how_to_vote:",
            disabled=selected is None,
        ):
            try:
                database.cast_vote(room.code, user_key, str(selected))
                st.toast("投票成功；再次投票會改票。")
            except ValueError as exc:
                st.error(str(exc))

    results = database.get_vote_results(room.code)
    result_frame = pd.DataFrame(results)
    st.bar_chart(result_frame, x="name", y="votes", x_label="餐廳", y_label="票數")

    if room.status == "open" and user_key == room.host_user_key:
        if st.button("結束投票並選出最高票", icon=":material/flag:"):
            try:
                database.finalize_vote(room.code)
                st.rerun()
            except TieVoteError as exc:
                st.warning(str(exc), icon=":material/balance:")
            except ValueError as exc:
                st.error(str(exc))

    if room.status == "closed":
        winner = database.get_winner(room.code)
        if winner:
            st.success(f"本次選擇：{winner.name}", icon=":material/trophy:")
            with st.container(border=True):
                render_restaurant_card(winner)
            render_rating_panel(database, user_key, winner)


database = get_database()
api_key = read_api_key()
open_webui_base_url, open_webui_api_key, open_webui_model = (
    read_open_webui_config()
)
browser_api_key, google_map_id = read_google_maps_browser_config()
app_base_url = read_app_base_url()
initial_room = str(st.query_params.get("room", "")).strip().upper()

st.session_state.setdefault("guest_user_key", "")
st.session_state.setdefault("guest_display_name", "")
st.session_state.setdefault("recommendations", [])
st.session_state.setdefault("batch_index", 0)
st.session_state.setdefault("chosen_restaurant", None)
st.session_state.setdefault("ai_preference_summary", "")
st.session_state.setdefault("ai_error", "")
st.session_state.setdefault("current_lat", 24.7957)
st.session_state.setdefault("current_lng", 120.9967)
st.session_state.setdefault("last_applied_position", None)
st.session_state.setdefault("room_code", initial_room)
st.session_state.setdefault(
    "navigation_page",
    "多人房間" if initial_room else "餐廳推薦",
)

# 若使用者是在展示模式搜尋後才加入 API Key，清除瀏覽器 session 中的舊示範結果。
if api_key and any(
    item.place_id.startswith("demo-v3-")
    for item in st.session_state.recommendations
):
    st.session_state.recommendations = []
    st.session_state.chosen_restaurant = None

identity = current_google_identity()
if identity is not None:
    database.upsert_user(
        identity.user_key,
        identity.display_name,
        identity.auth_type,
        identity.email,
        identity.picture_url,
    )
elif st.session_state.guest_user_key and st.session_state.guest_display_name:
    identity = UserIdentity(
        user_key=st.session_state.guest_user_key,
        display_name=st.session_state.guest_display_name,
        auth_type="guest",
    )
    database.upsert_user(
        identity.user_key,
        identity.display_name,
        identity.auth_type,
    )
else:
    render_sign_in(database)
    st.stop()

st.title("一鍵開吃", text_alignment="center")
st.caption("選好條件，讓推薦更懂你的口味。", text_alignment="center")

with st.sidebar:
    if identity.picture_url:
        st.image(identity.picture_url, width=56)
    st.markdown(f"**{identity.display_name}**")
    st.caption("Google 帳戶" if identity.is_google else "訪客")
    if identity.email:
        st.caption(identity.email)
    if st.button("登出", icon=":material/logout:"):
        clear_personal_session()
        if identity.is_google:
            st.logout()
        st.rerun()
    page = st.radio(
        "功能",
        ["餐廳推薦", "我的偏好", "多人房間"],
        key="navigation_page",
    )

if page == "餐廳推薦":
    show_recommendation_page(
        database,
        api_key,
        open_webui_base_url,
        open_webui_api_key,
        open_webui_model,
        browser_api_key,
        google_map_id,
        identity.user_key,
        identity.display_name,
        app_base_url,
    )
elif page == "我的偏好":
    show_preferences_page(database, identity.user_key)
else:
    show_room_page(
        database,
        identity.user_key,
        identity.display_name,
        app_base_url,
    )
