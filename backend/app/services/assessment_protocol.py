"""Only this small whitelist crosses the future equivalence provider boundary."""
from __future__ import annotations

import hashlib
import json
from typing import Protocol

from app.schemas.assessment import (EquivalenceRequest, EquivalenceResponse, ContextualEquivalenceRequest,
                                    ContextualEquivalenceResponse, VisionRequest, VisionResponse)

PROMPT_VERSION = "stage6-contextual-fake-v2"
POLICY_VERSION = "stage6-contextual-candidates-v3"
FAKE_MODEL = "fake-stage6-v1"


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def input_hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_equivalence_request(request):
    schema = ContextualEquivalenceRequest if request.get("schema_version") == "stage6-equivalence-request-v2" else EquivalenceRequest
    return schema.model_validate(request)


def response_schema(request):
    return ContextualEquivalenceResponse if request.get("schema_version") == "stage6-equivalence-request-v2" else EquivalenceResponse


def parse_response(raw: str, request: dict) -> dict:
    expected = parse_equivalence_request(request)
    value = json.loads(raw, object_pairs_hook=_unique_object)
    result = response_schema(request).model_validate(value)
    if result.request_id != expected.request_id:
        raise ValueError("request_id mismatch")
    return result.model_dump()


class EquivalenceAdapter(Protocol):
    def invoke(self, request: dict, *, timeout_seconds: int) -> str: ...


class FakeEquivalenceAdapter:
    """Synthetic transport check, never pretend to judge mathematical quality."""
    def invoke(self, request: dict, *, timeout_seconds: int) -> str:
        payload = parse_equivalence_request(request)
        return canonical_json({
            "schema_version": "stage6-equivalence-response-v2" if isinstance(payload, ContextualEquivalenceRequest) else "stage6-equivalence-response-v1",
            "request_id": payload.request_id,
            "verdict": "uncertain", "reason_code": "unsupported_answer",
            "explanation": "模拟批改仅验证调用链路，未执行数学等价判断。",
        })


def parse_vision_response(raw: str, request: dict) -> dict:
    expected = VisionRequest.model_validate(request)
    result = VisionResponse.model_validate(json.loads(raw, object_pairs_hook=_unique_object))
    if result.request_id != expected.request_id or result.page_token != expected.page_token:
        raise ValueError("vision request/page mismatch")
    tokens = [answer.slot_token for answer in result.answers]
    if len(tokens) != len(set(tokens)) or set(tokens) != {slot.slot_token for slot in expected.slots}:
        raise ValueError("SLOT_SET_MISMATCH")
    # Preserve student text. A readable choice must already match the actual
    # permitted alphabet; no answer key or guessed default ABCD is consulted.
    specs = {slot.slot_token: slot for slot in expected.slots}
    for answer in result.answers:
        slot = specs[answer.slot_token]
        if (slot.answer_format == "choice_letter" and answer.recognition_status == "readable"
                and answer.answer not in slot.allowed_option_labels):
            raise ValueError("choice outside allowed labels")
    return result.model_dump()


class FakeVisionAdapter:
    """S3 protocol harness only: no image is uploaded, read or claimed correct."""
    def invoke(self, request: dict, *, timeout_seconds: int) -> str:
        payload = VisionRequest.model_validate(request)
        return canonical_json({
            "schema_version": "stage6-vision-response-v1", "request_id": payload.request_id,
            "page_token": payload.page_token,
            "observed_identity": {"instance_code": None, "version_no": None, "page_no": None, "total_pages": None},
            "answers": [{"slot_token": slot.slot_token, "recognition_status": "illegible", "answer": None, "confidence": None}
                        for slot in payload.slots],
        })
