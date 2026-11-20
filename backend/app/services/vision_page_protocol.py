"""No expected identity, references, student IDs or stems cross this boundary."""
import hashlib
import json
from typing import Protocol

from app.schemas.vision_pages import PageVisionRequest, PageVisionResponse
from app.services.assessment_protocol import _unique_object, canonical_json

PROMPT_VERSION = "stage6-vision-page-v2-offline-1"
POLICY_VERSION = "stage6-vision-page-binding-v1"
FAKE_MODEL = "fake-stage6-vision-page-v2"
MAX_RESPONSE_CHARS = 262144


def parse_page_response(raw, request):
    if not isinstance(raw, str) or len(raw) > MAX_RESPONSE_CHARS:
        raise ValueError("MODEL_SCHEMA_INVALID")
    expected = PageVisionRequest.model_validate(request)
    result = PageVisionResponse.model_validate(json.loads(raw, object_pairs_hook=_unique_object))
    if result.request_id != expected.request_id or result.page_token != expected.page_token:
        raise ValueError("REQUEST_PAGE_MISMATCH")
    answers = {a.slot_token: a for a in result.answers}
    if len(answers) != len(result.answers) or set(answers) != {s.slot_token for s in expected.slots}:
        raise ValueError("SLOT_SET_MISMATCH")
    for slot in expected.slots:
        answer = answers[slot.slot_token]
        if (slot.answer_format == "choice_letter" and answer.recognition_status == "readable"
                and answer.answer not in slot.allowed_option_labels):
            raise ValueError("CHOICE_LABEL_INVALID")
    return result.model_dump()


def identity_check(parsed, expected):
    observed = parsed["observed_identity"]
    mismatched = [key for key, value in observed.items() if value is not None and value != expected[key]]
    unknown = [key for key, value in observed.items() if value is None]
    return {"status": "mismatch" if mismatched else "unconfirmed" if unknown else "verified",
            "mismatched_fields": mismatched, "unknown_fields": unknown}


class PageVisionAdapter(Protocol):
    def invoke(self, request: dict, *, image: bytes, image_sha256: str, timeout_seconds: int) -> str: ...


class FakePageVisionAdapter:
    """Transport harness only. Does not read pixels or invent observed identity."""
    def invoke(self, request, *, image, image_sha256, timeout_seconds):
        payload = PageVisionRequest.model_validate(request)
        if not image or hashlib.sha256(image).hexdigest() != image_sha256:
            raise ValueError("IMAGE_INTEGRITY_ERROR")
        return canonical_json({
            "schema_version": "stage6-vision-response-v2", "request_id": payload.request_id,
            "page_token": payload.page_token,
            "observed_identity": {key: None for key in (
                "artifact_code", "artifact_revision_no", "instance_code", "version_no",
                "layout_version", "page_no", "total_pages")},
            "answers": [{"slot_token": slot.slot_token, "recognition_status": "illegible",
                         "answer": None, "confidence": None} for slot in payload.slots],
        })
