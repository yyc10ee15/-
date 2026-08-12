"""使用 Streamlit Custom Components v2 取得瀏覽器目前位置。"""

from __future__ import annotations

from typing import Any

import streamlit as st


_GEOLOCATION_COMPONENT = st.components.v2.component(
    "restaurant_selector_v3_geolocation",
    html="""
    <div class="location-box">
      <button id="locate-button" type="button">使用我的目前位置</button>
      <span id="location-status">尚未取得位置</span>
    </div>
    """,
    css="""
    .location-box {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      color: var(--st-text-color);
      font-family: var(--st-font);
    }
    button {
      border: 0;
      border-radius: var(--st-button-border-radius, 0.5rem);
      padding: 0.55rem 0.9rem;
      color: white;
      background: var(--st-primary-color);
      cursor: pointer;
      font-weight: 600;
    }
    button:disabled { opacity: 0.65; cursor: wait; }
    span { font-size: 0.9rem; }
    """,
    js="""
    export default function (component) {
      const { parentElement, setStateValue } = component
      const button = parentElement.querySelector("#locate-button")
      const status = parentElement.querySelector("#location-status")
      if (!button || !status) return

      button.onclick = () => {
        if (!navigator.geolocation) {
          status.textContent = "此瀏覽器不支援定位"
          return
        }
        button.disabled = true
        status.textContent = "正在取得位置…"
        navigator.geolocation.getCurrentPosition(
          (position) => {
            const value = {
              latitude: position.coords.latitude,
              longitude: position.coords.longitude,
              accuracy: position.coords.accuracy,
            }
            status.textContent = `定位完成（誤差約 ${Math.round(value.accuracy)} 公尺）`
            button.disabled = false
            setStateValue("position", value)
          },
          (error) => {
            status.textContent = `定位失敗：${error.message}`
            button.disabled = false
          },
          { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
        )
      }
    }
    """,
)


def browser_geolocation(*, key: str) -> dict[str, float] | None:
    """顯示定位按鈕，成功時回傳 latitude、longitude 與 accuracy。"""
    result: Any = _GEOLOCATION_COMPONENT(
        key=key,
        data={},
        on_position_change=lambda: None,
    )
    position = getattr(result, "position", None)
    return dict(position) if position else None
