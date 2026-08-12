"""Google Maps JavaScript API 的位置選擇器與餐廳標記地圖。"""

from __future__ import annotations

from typing import Any

import streamlit as st


_GOOGLE_MAP_COMPONENT = st.components.v2.component(
    "restaurant_selector_v3_google_map",
    html="""
    <div class="google-map-shell">
      <div id="map-search"></div>
      <div id="google-map"></div>
      <div id="map-status"></div>
    </div>
    """,
    css="""
    .google-map-shell {
      display: grid;
      gap: 0.6rem;
      color: var(--st-text-color);
      font-family: var(--st-font);
    }
    #map-search:empty { display: none; }
    #map-search gmp-place-autocomplete { width: 100%; }
    #google-map {
      width: 100%;
      height: 420px;
      overflow: hidden;
      border: 1px solid var(--st-border-color, #ddd);
      border-radius: var(--st-border-radius, 0.75rem);
    }
    #map-status {
      min-height: 1.2rem;
      color: var(--st-text-color);
      font-size: 0.88rem;
    }
    """,
    js="""
    function loadGoogleMaps(apiKey) {
      if (globalThis.google?.maps?.importLibrary) return Promise.resolve()
      if (globalThis.__restaurantSelectorGoogleMapsPromise) {
        return globalThis.__restaurantSelectorGoogleMapsPromise
      }

      globalThis.__restaurantSelectorGoogleMapsPromise = new Promise((resolve, reject) => {
        const callbackName = `__restaurantSelectorMapReady${Date.now()}`
        globalThis[callbackName] = () => {
          delete globalThis[callbackName]
          resolve()
        }
        const script = document.createElement("script")
        const parameters = new URLSearchParams({
          key: apiKey,
          v: "weekly",
          libraries: "maps,marker,places",
          language: "zh-TW",
          region: "TW",
          loading: "async",
          callback: callbackName,
        })
        script.src = `https://maps.googleapis.com/maps/api/js?${parameters}`
        script.async = true
        script.onerror = () => reject(new Error("Google Maps JavaScript API 載入失敗"))
        document.head.appendChild(script)
      })
      return globalThis.__restaurantSelectorGoogleMapsPromise
    }

    function numericPosition(position) {
      if (!position) return null
      const latitude = typeof position.lat === "function" ? position.lat() : position.lat
      const longitude = typeof position.lng === "function" ? position.lng() : position.lng
      if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null
      return { latitude, longitude }
    }

    function restaurantInfoNode(item) {
      const root = document.createElement("div")
      root.style.maxWidth = "260px"
      const title = document.createElement("strong")
      title.textContent = item.name || "餐廳"
      root.appendChild(title)

      const facts = document.createElement("div")
      const details = []
      if (item.cuisine) details.push(item.cuisine)
      if (Number.isFinite(item.distance_km)) details.push(`${item.distance_km.toFixed(1)} km`)
      if (Number.isFinite(item.google_rating)) details.push(`Google ${item.google_rating.toFixed(1)} ★`)
      facts.textContent = details.join(" · ")
      facts.style.margin = "0.35rem 0"
      root.appendChild(facts)

      if (item.address) {
        const address = document.createElement("div")
        address.textContent = item.address
        address.style.marginBottom = "0.45rem"
        root.appendChild(address)
      }
      if (typeof item.map_url === "string" && item.map_url.startsWith("https://")) {
        const link = document.createElement("a")
        link.href = item.map_url
        link.target = "_blank"
        link.rel = "noopener noreferrer"
        link.textContent = "在 Google Maps 開啟"
        root.appendChild(link)
      }
      return root
    }

    export default async function (component) {
      const { data, parentElement, setStateValue } = component
      const mapElement = parentElement.querySelector("#google-map")
      const searchElement = parentElement.querySelector("#map-search")
      const statusElement = parentElement.querySelector("#map-status")
      if (!mapElement || !searchElement || !statusElement) return

      if (!data?.apiKey) {
        statusElement.textContent = "尚未設定前端 Google Maps API Key"
        return
      }
      try {
        await loadGoogleMaps(data.apiKey)
        const [{ Map, InfoWindow }, { AdvancedMarkerElement }] = await Promise.all([
          google.maps.importLibrary("maps"),
          google.maps.importLibrary("marker"),
        ])
        const center = {
          lat: Number(data.latitude) || 24.7957,
          lng: Number(data.longitude) || 120.9967,
        }
        mapElement.replaceChildren()
        searchElement.replaceChildren()
        const map = new Map(mapElement, {
          center,
          zoom: data.mode === "picker" ? 15 : 13,
          mapId: data.mapId || "DEMO_MAP_ID",
          mapTypeControl: false,
          streetViewControl: false,
          fullscreenControl: true,
        })

        if (data.mode === "picker") {
          statusElement.textContent = "搜尋地址、點一下地圖，或拖曳標記來選擇位置。"
          const marker = new AdvancedMarkerElement({
            map,
            position: center,
            gmpDraggable: true,
            title: "搜尋中心（可拖曳）",
          })
          const updateSelection = (position, label, zoom = null) => {
            const value = numericPosition(position)
            if (!value) return
            marker.position = { lat: value.latitude, lng: value.longitude }
            map.setCenter({ lat: value.latitude, lng: value.longitude })
            if (zoom !== null) map.setZoom(zoom)
            statusElement.textContent = `${label}：${value.latitude.toFixed(6)}, ${value.longitude.toFixed(6)}`
            setStateValue("position", { ...value, label })
          }
          map.addListener("click", (event) => updateSelection(event.latLng, "地圖點選位置"))
          marker.addListener("dragend", () => updateSelection(marker.position, "標記位置"))

          try {
            const { PlaceAutocompleteElement } = await google.maps.importLibrary("places")
            const autocomplete = new PlaceAutocompleteElement()
            autocomplete.setAttribute("placeholder", "搜尋地址、地標或店家")
            searchElement.appendChild(autocomplete)
            const handlePlaceSelection = async (event) => {
              const prediction = event.placePrediction
              const place = event.place || prediction?.toPlace()
              if (!place) {
                statusElement.textContent = "無法讀取這個搜尋結果，請改用地圖點選。"
                return
              }
              await place.fetchFields({ fields: ["displayName", "formattedAddress", "location"] })
              if (!place.location) {
                statusElement.textContent = "這個搜尋結果沒有座標，請改用地圖點選。"
                return
              }
              updateSelection(
                place.location,
                place.formattedAddress || place.displayName || "地址搜尋結果",
                16,
              )
            }
            // weekly 版使用 gmp-select；同時兼容較早期的 gmp-placeselect。
            autocomplete.addEventListener("gmp-select", handlePlaceSelection)
            autocomplete.addEventListener("gmp-placeselect", handlePlaceSelection)
          } catch (error) {
            statusElement.textContent = `地址搜尋暫時無法使用：${error.message}`
          }
          return
        }

        statusElement.textContent = "點選餐廳標記可查看資訊。"
        const bounds = new google.maps.LatLngBounds()
        const infoWindow = new InfoWindow()
        for (const item of data.markers || []) {
          const position = { lat: Number(item.latitude), lng: Number(item.longitude) }
          if (!Number.isFinite(position.lat) || !Number.isFinite(position.lng)) continue
          const marker = new AdvancedMarkerElement({
            map,
            position,
            title: item.name || "餐廳",
            gmpClickable: true,
          })
          bounds.extend(position)
          marker.addEventListener("gmp-click", () => {
            infoWindow.setContent(restaurantInfoNode(item))
            infoWindow.open({ map, anchor: marker })
          })
        }
        if (!bounds.isEmpty()) map.fitBounds(bounds, 56)
      } catch (error) {
        statusElement.textContent = `Google Maps 載入失敗：${error.message}`
      }
    }
    """,
)


def google_map_picker(
    api_key: str,
    latitude: float,
    longitude: float,
    *,
    map_id: str = "",
    key: str,
) -> dict[str, object] | None:
    """顯示可搜尋、點選及拖曳的 Google 地圖並回傳座標。"""
    result: Any = _GOOGLE_MAP_COMPONENT(
        key=key,
        data={
            "mode": "picker",
            "apiKey": api_key,
            "mapId": map_id,
            "latitude": latitude,
            "longitude": longitude,
        },
        on_position_change=lambda: None,
        height=490,
    )
    position = getattr(result, "position", None)
    return dict(position) if position else None


def google_restaurant_map(
    api_key: str,
    restaurants: list[dict[str, object]],
    *,
    latitude: float,
    longitude: float,
    map_id: str = "",
    key: str,
) -> None:
    """使用 Google Maps 顯示候選餐廳標記與資訊視窗。"""
    _GOOGLE_MAP_COMPONENT(
        key=key,
        data={
            "mode": "restaurants",
            "apiKey": api_key,
            "mapId": map_id,
            "latitude": latitude,
            "longitude": longitude,
            "markers": restaurants,
        },
        height=455,
    )
