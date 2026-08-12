"""使用 Open WebUI Chat Completions API 分析偏好並解說候選餐廳。"""

from __future__ import annotations

import json
from typing import Any

import requests
from pydantic import BaseModel, ValidationError

from .models import Restaurant


DEFAULT_OPEN_WEBUI_MODEL = "gpt-oss:20b"


class CandidateAdvice(BaseModel):
    """單間候選餐廳的結構化 AI 建議。"""

    place_id: str
    advice: str


class PreferenceAnalysis(BaseModel):
    """模型一次回傳的偏好摘要與所有候選解說。"""

    preference_summary: str
    candidates: list[CandidateAdvice]


class AIAdvisorError(RuntimeError):
    """Open WebUI 無法完成分析時，轉成 UI 可安全顯示的錯誤。"""


class AIAdvisor:
    """把匿名化歷史與候選資料交給 Open WebUI 模型分析。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = DEFAULT_OPEN_WEBUI_MODEL,
        *,
        session: Any | None = None,
        timeout: float = 60.0,
    ) -> None:
        clean_base_url = base_url.strip().rstrip("/")
        if not clean_base_url:
            raise ValueError("尚未設定 OPEN_WEBUI_BASE_URL。")
        if not api_key.strip() and session is None:
            raise ValueError("尚未設定 OPEN_WEBUI_API_KEY。")

        # 同時接受 https://host 與 https://host/api 兩種設定方式。
        if clean_base_url.endswith("/api"):
            self.endpoint = f"{clean_base_url}/chat/completions"
        else:
            self.endpoint = f"{clean_base_url}/api/chat/completions"
        self.api_key = api_key.strip()
        self.model = model.strip() or DEFAULT_OPEN_WEBUI_MODEL
        self.session = session or requests.Session()
        self.timeout = timeout

    @staticmethod
    def _candidate_payload(restaurants: list[Restaurant]) -> list[dict[str, object]]:
        return [
            {
                "place_id": item.place_id,
                "name": item.name,
                "cuisine": item.cuisine_label,
                "distance_km": item.distance_km,
                "google_rating": item.google_rating,
                "google_rating_count": item.google_rating_count,
                "open_now": item.open_now,
            }
            for item in restaurants
        ]

    @staticmethod
    def _message_text(content: object) -> str:
        """兼容一般字串與 OpenAI-compatible 的分段文字內容。"""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(parts).strip()
        return ""

    @staticmethod
    def _parse_analysis(content: str) -> PreferenceAnalysis:
        """容忍模型加入 Markdown code fence，但仍用 Pydantic驗證欄位。"""
        clean = content.strip()
        if clean.startswith("```"):
            lines = clean.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            clean = "\n".join(lines).strip()

        # 部分模型會在 JSON 前後加一句說明，只取最外層物件。
        start = clean.find("{")
        end = clean.rfind("}")
        if start >= 0 and end > start:
            clean = clean[start : end + 1]
        try:
            return PreferenceAnalysis.model_validate(json.loads(clean))
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise AIAdvisorError("AI 回覆格式不正確，請重新推薦一次。") from exc

    def analyze(
        self,
        history: dict[str, list[dict[str, object]]],
        restaurants: list[Restaurant],
    ) -> PreferenceAnalysis:
        """分析歷史紀錄；不傳姓名、定位或完整地址。"""
        if not restaurants:
            return PreferenceAnalysis(
                preference_summary="目前沒有候選餐廳可供分析。",
                candidates=[],
            )

        payload = {
            "past_history": history,
            "candidate_restaurants": self._candidate_payload(restaurants),
        }
        system_prompt = (
            "你是餐廳挑選助理。請使用繁體中文，根據提供的喜歡、不喜歡與 1 到 5 星紀錄"
            "歸納偏好，並為每一間候選餐廳提供一到兩句、具體但簡短的挑選建議。"
            "只能使用輸入資料，不可虛構餐點、價位、營業資訊或其他未提供的事實。"
            "若歷史不足，要明確說明並改依候選餐廳的距離、類型與公開評分比較。"
            "只回傳一個 JSON 物件，不要 Markdown 或額外說明。格式必須是："
            '{"preference_summary":"摘要","candidates":['
            '{"place_id":"輸入的ID","advice":"一到兩句建議"}]}。'
            "candidates 必須涵蓋輸入中的每個 place_id，且不可加入不存在的 place_id。"
        )
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            "stream": False,
            "temperature": 0.2,
        }
        try:
            response = self.session.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=request_body,
                timeout=self.timeout,
            )
            response.raise_for_status()
            response_data = response.json()
            content = response_data["choices"][0]["message"]["content"]
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
            raise AIAdvisorError(
                "無法從 Open WebUI 取得分析，請檢查網址、API Key 與模型名稱。"
            ) from exc

        analysis = self._parse_analysis(self._message_text(content))
        valid_ids = {item.place_id for item in restaurants}
        unique: dict[str, CandidateAdvice] = {}
        for item in analysis.candidates:
            if item.place_id in valid_ids and item.advice.strip():
                unique[item.place_id] = CandidateAdvice(
                    place_id=item.place_id,
                    advice=item.advice.strip(),
                )

        # 即使模型漏掉候選項目，UI 仍會為每間餐廳保留一段安全的預設說明。
        completed = [
            unique.get(
                restaurant.place_id,
                CandidateAdvice(
                    place_id=restaurant.place_id,
                    advice="目前紀錄不足以判斷是否符合你的口味，可先比較距離與 Google 評分。",
                ),
            )
            for restaurant in restaurants
        ]
        return PreferenceAnalysis(
            preference_summary=analysis.preference_summary.strip(),
            candidates=completed,
        )
