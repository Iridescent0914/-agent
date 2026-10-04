from __future__ import annotations

import base64
import json
import re
from typing import Any, Protocol
import unicodedata

import httpx

from app.core.config import get_settings
from app.schemas.recognition import VisionExtraction

PROMPT_VERSION = "m6-answer-reader-v2"
RECOGNITION_SCHEMA_VERSION = "m6-recognition-v2"


class VisionServiceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class VisionService(Protocol):
    def extract_answers(self, *, image_bytes: bytes, content_type: str,
                        expected_items: list[dict[str, Any]]) -> dict[str, Any]: ...


class FakeVisionService:
    """Deterministic development adapter; not valid for real-image acceptance."""

    def extract_answers(self, *, image_bytes: bytes, content_type: str,
                        expected_items: list[dict[str, Any]]) -> dict[str, Any]:
        del image_bytes, content_type
        return {
            "schema_version": RECOGNITION_SCHEMA_VERSION,
            "answers": [{
                "order_no": item["order_no"], "answer": None,
                "recognition_status": "illegible", "confidence": 0.0,
            } for item in expected_items],
        }


class OpenAICompatibleVisionService:
    """Configurable adapter for providers exposing a chat/completions endpoint."""

    def __init__(self) -> None:
        self.settings = get_settings()
        if not self.settings.vision_api_base_url:
            raise VisionServiceError("vision_disabled", "真实视觉模型 API 地址尚未配置")

    def extract_answers(self, *, image_bytes: bytes, content_type: str,
                        expected_items: list[dict[str, Any]]) -> dict[str, Any]:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        instruction = build_extraction_prompt(expected_items)
        headers = {"Content-Type": "application/json"}
        if self.settings.vision_api_key:
            headers["Authorization"] = f"Bearer {self.settings.vision_api_key}"
        try:
            response = httpx.post(
                self.settings.vision_api_base_url.rstrip("/") + "/chat/completions",
                headers=headers,
                json={
                    "model": self.settings.vision_model, "temperature": 0,
                    "max_tokens": self.settings.vision_max_tokens,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": instruction},
                        {"type": "image_url", "image_url": {
                            "url": f"data:{content_type};base64,{encoded}"
                        }},
                    ]}],
                },
                timeout=self.settings.vision_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise VisionServiceError("vision_timeout", "视觉模型调用超时") from exc
        except httpx.HTTPStatusError as exc:
            raise VisionServiceError("vision_provider_error", "视觉模型调用失败") from exc
        except httpx.RequestError as exc:
            raise VisionServiceError(
                "vision_network_error", "vision provider network request failed"
            ) from exc
        try:
            content = response.json()["choices"][0]["message"]["content"]
            return _load_json_content(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise VisionServiceError("vision_invalid_json", "视觉模型未返回合法 JSON") from exc


def build_extraction_prompt(expected_items: list[dict[str, Any]]) -> str:
    """Build an answer-only prompt from student-safe slot specifications."""
    schema_example = {
        "schema_version": RECOGNITION_SCHEMA_VERSION,
        "answers": [{
            "order_no": 1,
            "answer": "A",
            "recognition_status": "readable",
            "confidence": 0.95,
        }],
    }
    return (
        "你是答题区抄录器，只抄读学生实际书写内容。禁止解题、补全、猜测或判断对错。\n"
        "仅输出一个 JSON 对象，不要输出 Markdown、代码围栏或解释。\n"
        f"输出 schema_version 必须是 {RECOGNITION_SCHEMA_VERSION}。"
        "recognition_status 只能是 readable、blank、missing、illegible、ambiguous。\n"
        "readable 表示可明确抄录；blank 表示该槽位明确留白；missing 表示图片没有对应题号；"
        "illegible 表示字迹不可辨；ambiguous 表示涂改、多解或不符合题目允许格式。\n"
        "answer_format=choice_letter 时，只返回一个大写拉丁字母。若字母不在 "
        "allowed_option_labels 中，必须标为 ambiguous；禁止默认选项只有 ABCD。\n"
        "blank_ids 多于一个时，answer 必须为 "
        '{"kind":"group","items":[{"item_id":"1","answer":"A"}]}，'
        "并完整覆盖每个 blank_id。不要在输出中复述 allowed_option_labels。\n"
        f"结构示例：{json.dumps(schema_example, ensure_ascii=False)}\n"
        "本次槽位规格：" + json.dumps(expected_items, ensure_ascii=False)
    )


def _load_json_content(content: Any) -> dict[str, Any]:
    if not isinstance(content, str):
        raise TypeError("vision content must be a JSON string")
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise TypeError("vision JSON must be an object")
    return payload


def get_vision_service() -> VisionService:
    settings = get_settings()
    if settings.vision_provider == "fake":
        return FakeVisionService()
    if not settings.vision_enabled:
        raise VisionServiceError("vision_disabled", "视觉模型功能尚未启用")
    return OpenAICompatibleVisionService()


def _choice_letter(value: Any, allowed: set[str]) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value).strip().upper()
    if len(normalized) != 1 or not ("A" <= normalized <= "Z"):
        return None
    if allowed and normalized not in allowed:
        return None
    return normalized


def _validate_choice_answer(value: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    if value["recognition_status"] != "readable":
        return value
    allowed = {
        str(label).strip().upper()
        for label in spec.get("allowed_option_labels", [])
        if isinstance(label, str) and len(str(label).strip()) == 1
    }
    blank_ids = [str(item) for item in spec.get("blank_ids", ["answer"])]
    answer = value["answer"]
    if len(blank_ids) == 1:
        normalized = _choice_letter(answer, allowed)
        if normalized is not None:
            return {**value, "answer": normalized}
        return {**value, "recognition_status": "ambiguous"}

    if not isinstance(answer, dict) or answer.get("kind") != "group":
        return {**value, "recognition_status": "ambiguous"}
    items = answer.get("items")
    if not isinstance(items, list):
        return {**value, "recognition_status": "ambiguous"}
    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("item_id"), str):
            return {**value, "recognition_status": "ambiguous"}
        item_id = item["item_id"]
        if item_id in by_id:
            return {**value, "recognition_status": "ambiguous"}
        by_id[item_id] = item
    if set(by_id) != set(blank_ids):
        return {**value, "recognition_status": "ambiguous"}
    normalized_items = []
    for item_id in blank_ids:
        normalized = _choice_letter(by_id[item_id].get("answer"), allowed)
        if normalized is None:
            return {**value, "recognition_status": "ambiguous"}
        normalized_items.append({"item_id": item_id, "answer": normalized})
    return {
        **value,
        "answer": {"kind": "group", "items": normalized_items},
    }


def parse_vision_output(
    payload: dict[str, Any],
    expected: set[int] | list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    if isinstance(expected, set):
        specs = {order: {"order_no": order} for order in expected}
    else:
        specs = {int(item["order_no"]): item for item in expected}
    expected_orders = set(specs)
    try:
        extraction = VisionExtraction.model_validate(payload)
    except Exception as exc:
        raise VisionServiceError("vision_schema_error", str(exc)) from exc
    by_order = {item.order_no: item.model_dump(mode="json") for item in extraction.answers}
    unknown = set(by_order) - expected_orders
    if unknown:
        raise VisionServiceError("vision_schema_error", f"识别结果包含未知题号：{sorted(unknown)}")
    for order in expected_orders - set(by_order):
        by_order[order] = {
            "order_no": order, "answer": None,
            "recognition_status": "missing", "confidence": None,
        }
    for order, value in list(by_order.items()):
        if specs[order].get("answer_format") == "choice_letter":
            by_order[order] = _validate_choice_answer(value, specs[order])
    return by_order
