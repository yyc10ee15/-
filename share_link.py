"""手機友善的多人房間分享與複製按鈕。"""

from __future__ import annotations

import streamlit as st


_SHARE_COMPONENT = st.components.v2.component(
    "restaurant_selector_v3_share_link",
    html="""
    <div class="share-actions">
      <button id="share-button" type="button">分享邀請連結</button>
      <button id="copy-button" type="button" class="secondary">複製連結</button>
      <div id="share-status"></div>
    </div>
    """,
    css="""
    .share-actions {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.6rem;
      color: var(--st-text-color);
      font-family: var(--st-font);
    }
    button {
      min-height: 2.5rem;
      border: 1px solid var(--st-primary-color);
      border-radius: var(--st-button-border-radius, 0.55rem);
      padding: 0.55rem 0.9rem;
      background: var(--st-primary-color);
      color: white;
      cursor: pointer;
      font-weight: 600;
    }
    button.secondary {
      background: transparent;
      color: var(--st-primary-color);
    }
    #share-status {
      flex-basis: 100%;
      min-height: 1.15rem;
      font-size: 0.86rem;
    }
    """,
    js="""
    async function copyText(parentElement, text) {
      if (navigator.clipboard && globalThis.isSecureContext) {
        await navigator.clipboard.writeText(text)
        return
      }
      const temporary = document.createElement("textarea")
      temporary.value = text
      temporary.setAttribute("readonly", "")
      temporary.style.position = "fixed"
      temporary.style.opacity = "0"
      parentElement.appendChild(temporary)
      temporary.select()
      const copied = document.execCommand("copy")
      temporary.remove()
      if (!copied) throw new Error("瀏覽器不允許自動複製")
    }

    export default function (component) {
      const { data, parentElement } = component
      const shareButton = parentElement.querySelector("#share-button")
      const copyButton = parentElement.querySelector("#copy-button")
      const status = parentElement.querySelector("#share-status")
      if (!shareButton || !copyButton || !status) return

      const url = data?.url || ""
      const title = data?.title || "一起投票選餐廳"
      shareButton.disabled = !url
      copyButton.disabled = !url

      shareButton.onclick = async () => {
        try {
          if (navigator.share) {
            await navigator.share({ title, text: data?.text || title, url })
            status.textContent = "已開啟手機分享選單。"
          } else {
            await copyText(parentElement, url)
            status.textContent = "此裝置沒有分享選單，已改為複製連結。"
          }
        } catch (error) {
          if (error?.name !== "AbortError") status.textContent = `分享失敗：${error.message}`
        }
      }

      copyButton.onclick = async () => {
        try {
          await copyText(parentElement, url)
          status.textContent = "已複製邀請連結。"
        } catch (error) {
          status.textContent = `無法自動複製，請長按下方連結：${error.message}`
        }
      }
    }
    """,
)


def share_room_link(url: str, room_title: str, *, key: str) -> None:
    """顯示手機原生分享與一鍵複製按鈕。"""
    _SHARE_COMPONENT(
        key=key,
        data={
            "url": url,
            "title": f"一起投票：{room_title}",
            "text": "點開連結加入餐廳投票房間。",
        },
        height=88,
    )
