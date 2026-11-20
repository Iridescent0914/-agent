"""Read-only inventory and offline multipage checks. No uploads or model calls.

Trial page assignments must be checked against the actual printed pages by a
human. They are deliberately not a production/frozen answer-card-v3 manifest.
"""
from collections import Counter
from uuid import uuid4

from sqlalchemy import select

from app.models import PaperInstance, PaperInstanceItem
from app.schemas.assessment import VisionRequest
from app.schemas.vision_preparation import (
    PageAssignment, PreparationSlot, TrialPage, TrialPageResult,
    VisionInventory, VisionTrialPlan,
)
from app.services.assessment_protocol import input_hash, parse_vision_response
from app.services.print_view import answer_subquestions, print_source_module
from app.services.recognition_workflow import (
    _question_text_option_labels, _structured_option_labels,
)


def _inventory_hash(inventory):
    return input_hash(inventory.model_dump(exclude={"inventory_sha256"}))


def inventory_from_snapshots(paper, items) -> VisionInventory:
    if paper.status != "published" or not paper.instance_code or paper.invalidated_at is not None:
        raise ValueError("仅可为当前有效的已发布试卷准备视觉识别")
    slots = []
    orders, item_ids = set(), set()
    for item in sorted(items, key=lambda value: value.order_no):
        if (item.paper_instance_id != paper.id or type(item.order_no) is not int
                or item.order_no < 1 or item.order_no in orders or item.id in item_ids):
            raise ValueError("试卷题项归属、题号或编号无效")
        orders.add(item.order_no)
        item_ids.add(item.id)
        snapshot = item.question_snapshot or {}
        question_type = str(snapshot.get("question_type") or "fill_blank")
        rows = answer_subquestions(item.answer_snapshot, question_type)
        labels = None
        if question_type == "choice":
            # Local parsing only: external metadata contains letters, never
            # the options/question text, answer values, rules or aliases.
            labels = sorted(_structured_option_labels(item.options_snapshot)
                            or _question_text_option_labels(snapshot)) or None
        for row in rows:
            for blank in row.blanks:
                blocker = "MANUAL_SLOT" if blank.kind == "manual" else None
                if blank.kind == "choice" and not labels:
                    blocker = "CHOICE_LABELS_MISSING"
                slots.append(PreparationSlot(
                    slot_key=f"{item.id}:{blank.item_id}", paper_instance_item_id=str(item.id),
                    item_id=blank.item_id, order_no=item.order_no,
                    subquestion_no=row.subquestion_no, blank_no=blank.blank_no,
                    source_module=print_source_module(item.selection_audit),
                    answer_format={"choice": "choice_letter", "true_false": "true_false_mark",
                                   "fill_blank": "math_or_text"}.get(blank.kind),
                    allowed_option_labels=labels if blank.kind == "choice" else None,
                    blocker=blocker,
                ))
    inventory = VisionInventory(
        paper_instance_id=str(paper.id), instance_code=paper.instance_code,
        version_no=paper.version_no, slots=slots, inventory_sha256="0" * 64,
    )
    inventory.inventory_sha256 = _inventory_hash(inventory)
    return inventory


def build_inventory(db, paper_id) -> VisionInventory:
    paper = db.get(PaperInstance, paper_id)
    if paper is None:
        raise LookupError("试卷不存在")
    items = list(db.scalars(select(PaperInstanceItem).where(
        PaperInstanceItem.paper_instance_id == paper.id,
    ).order_by(PaperInstanceItem.order_no)))
    return inventory_from_snapshots(paper, items)


def _check_inventory(inventory):
    if inventory.inventory_sha256 != _inventory_hash(inventory):
        raise ValueError("逐空清单已变更，须重新准备页面计划")
    keys = [slot.slot_key for slot in inventory.slots]
    coordinates = [(s.order_no, s.subquestion_no, s.blank_no) for s in inventory.slots]
    if len(set(keys)) != len(keys) or len(set(coordinates)) != len(coordinates):
        raise ValueError("逐空清单包含重复空位")
    if any(slot.blocker for slot in inventory.slots):
        raise ValueError("存在人工判分空位或缺失选择字母，须先处理，不可静默跳过")


def build_trial_plan(inventory: VisionInventory, assignments: list[PageAssignment]) -> VisionTrialPlan:
    _check_inventory(inventory)
    if not assignments or len(assignments) > 100:
        raise ValueError("页面数量须在1至100之间")
    page_numbers = [p.page_no for p in assignments]
    if sorted(page_numbers) != list(range(1, len(assignments) + 1)):
        raise ValueError("页码必须从1连续编号且不重复")
    assigned = [key for page in assignments for key in page.slot_keys]
    specs = {slot.slot_key: slot for slot in inventory.slots}
    if len(assigned) != len(set(assigned)) or set(assigned) != set(specs):
        raise ValueError("页面空位必须完整覆盖试卷，不能遗漏、重复或新增")
    pages = []
    for assignment in sorted(assignments, key=lambda p: p.page_no):
        bindings, slots = {}, []
        for key in assignment.slot_keys:
            spec = specs[key]
            token = uuid4().hex
            bindings[token] = key
            slot = {"slot_token": token, "order_no": spec.order_no,
                    "subquestion_no": spec.subquestion_no, "blank_no": spec.blank_no,
                    "answer_format": spec.answer_format}
            if spec.answer_format == "choice_letter":
                slot["allowed_option_labels"] = spec.allowed_option_labels
            slots.append(slot)
        request = VisionRequest.model_validate({
            "schema_version": "stage6-vision-request-v1", "request_id": str(uuid4()),
            "page_token": uuid4().hex, "slots": slots,
        })
        pages.append(TrialPage(page_no=assignment.page_no, request=request, slot_bindings=bindings))
    # Detach from the caller's mutable inventory. Save/reload this full plan
    # during offline evaluation; rebuilding allocates new request/slot tokens.
    return VisionTrialPlan(inventory=inventory.model_copy(deep=True), pages=pages)


def _check_plan(plan):
    _check_inventory(plan.inventory)
    specs = {slot.slot_key: slot for slot in plan.inventory.slots}
    assigned = []
    tokens, request_ids, page_tokens = set(), set(), set()
    if sorted(page.page_no for page in plan.pages) != list(range(1, len(plan.pages) + 1)):
        raise ValueError("页面计划的页码无效")
    for page in plan.pages:
        request = VisionRequest.model_validate(page.provider_payload())
        if request.request_id in request_ids or request.page_token in page_tokens:
            raise ValueError("页面请求编号重复")
        request_ids.add(request.request_id)
        page_tokens.add(request.page_token)
        if set(page.slot_bindings) != {s.slot_token for s in request.slots}:
            raise ValueError("页面令牌映射不完整")
        for slot in request.slots:
            key = page.slot_bindings[slot.slot_token]
            if slot.slot_token in tokens or key not in specs:
                raise ValueError("页面令牌或空位映射无效")
            tokens.add(slot.slot_token)
            spec = specs[key]
            if ((slot.order_no, slot.subquestion_no, slot.blank_no, slot.answer_format, slot.allowed_option_labels)
                    != (spec.order_no, spec.subquestion_no, spec.blank_no, spec.answer_format, spec.allowed_option_labels)):
                raise ValueError("页面规格与逐空清单不一致")
            assigned.append(key)
    if len(assigned) != len(set(assigned)) or set(assigned) != set(specs):
        raise ValueError("页面空位覆盖不完整或重复")


def merge_trial_results(plan: VisionTrialPlan, results: list[TrialPageResult]) -> dict:
    """Validate prepared responses; missing pages NEVER become blank answers.

    A ready capture means structural completeness only. All transcriptions
    still require teacher confirmation before the existing grading workflow.
    """
    _check_plan(plan)
    pages = {p.page_no: p for p in plan.pages}
    counts = Counter(result.page_no for result in results)
    hashes = Counter(result.image_sha256 for result in results)
    missing_pages = sorted(set(pages) - set(counts))
    specs = {slot.slot_key: slot for slot in plan.inventory.slots}
    issues, units, confirmations = [], [], []
    for result in results:
        number = result.page_no
        if number not in pages:
            issues.append({"page_no": number, "code": "UNEXPECTED_PAGE"})
            continue
        if counts[number] > 1:
            issues.append({"page_no": number, "code": "DUPLICATE_PAGE"})
            continue
        if hashes[result.image_sha256] > 1:
            issues.append({"page_no": number, "code": "DUPLICATE_IMAGE"})
            continue
        page = pages[number]
        try:
            parsed = parse_vision_response(result.raw_response, page.provider_payload())
        except ValueError:
            issues.append({"page_no": number, "code": "INVALID_PAGE_RESPONSE"})
            continue
        identity = parsed["observed_identity"]
        expected = {"instance_code": plan.inventory.instance_code, "version_no": plan.inventory.version_no,
                    "page_no": number, "total_pages": len(pages)}
        if any(value is not None and value != expected[key] for key, value in identity.items()):
            issues.append({"page_no": number, "code": "PAGE_IDENTITY_MISMATCH"})
            continue
        unknown = [key for key, value in identity.items() if value is None]
        if unknown and result.identity_confirmation is None:
            issues.append({"page_no": number, "code": "PAGE_IDENTITY_UNCONFIRMED"})
            continue
        if unknown:
            confirmations.append({"page_no": number, "fields": unknown,
                                  **result.identity_confirmation.model_dump()})
        for answer in parsed["answers"]:
            spec = specs[page.slot_bindings[answer["slot_token"]]]
            units.append({**spec.model_dump(), "page_no": number, "image_sha256": result.image_sha256,
                          "recognition_status": answer["recognition_status"],
                          "answer": answer["answer"], "confidence": answer["confidence"]})
            if answer["recognition_status"] not in {"readable", "blank"}:
                issues.append({"page_no": number, "slot_key": spec.slot_key, "code": "SLOT_NEEDS_REVIEW"})
    status = "needs_review" if issues else "collecting" if missing_pages else "ready"
    return {"purpose": "manual_internal_trial", "capture_status": status,
            "inventory_sha256": plan.inventory.inventory_sha256, "missing_pages": missing_pages,
            "expected_unit_count": len(specs), "received_unit_count": len(units),
            "units": sorted(units, key=lambda u: (u["order_no"], u["subquestion_no"], u["blank_no"])),
            "issues": issues, "identity_confirmations": confirmations,
            "can_submit": False, "requires_transcription_confirmation": True}
