"""Fixed A4 pagination; the PDF and slot geometry come from the same plan."""
from collections import defaultdict
from itertools import groupby
from uuid import uuid4

import pymupdf as fitz

LAYOUT_VERSION = "answer-card-v3"
RENDERER_VERSION = f"answer-card-fixed-v2-pymupdf-{fitz.VersionBind}"
WIDTH, HEIGHT = 595.276, 841.89
LEFT, RIGHT, TOP, BOTTOM = 40.0, 555.276, 150.0, 780.0
MODULES = ["required", "personalized", "history"]
MODULE_NAMES = {"required": "必做题", "personalized": "个性化", "history": "历史回顾",
                "weekly_summary": "周总结", "unclassified": "未分类题目"}


def _blocks(inventory, assignment_type):
    modules = ["weekly_summary"] if assignment_type == "weekly_summary" else list(MODULES)
    modules += [m for m in ("weekly_summary", "unclassified")
                if m not in modules and any(s.source_module == m for s in inventory.slots)]
    for index, module in enumerate(modules):
        title = f"{['一', '二', '三', '四', '五'][index]}、{MODULE_NAMES[module]}"
        yield {"kind": "module", "height": 32, "text": title}
        selected = [s for s in inventory.slots if s.source_module == module]
        if not selected:
            yield {"kind": "empty", "height": 26, "text": f"本次无{MODULE_NAMES[module]}。"}
        questions = defaultdict(list)
        for slot in selected:
            questions[slot.order_no].append(slot)
        for order, slots in questions.items():
            yield {"kind": "question", "height": 26, "text": f"第 {order} 题 · {len(slots)} 个作答位"}
            subquestions = defaultdict(list)
            for slot in slots:
                subquestions[slot.subquestion_no].append(slot)
            for subquestion, blanks in subquestions.items():
                chunk = []
                for slot in blanks:
                    if slot.blocker == "MANUAL_SLOT":
                        if chunk:
                            yield _row(order, subquestion, chunk, len(blanks))
                            chunk = []
                        yield _row(order, subquestion, [slot], len(blanks), manual=True)
                    else:
                        chunk.append(slot)
                        if len(chunk) == 3:
                            yield _row(order, subquestion, chunk, len(blanks))
                            chunk = []
                if chunk:
                    yield _row(order, subquestion, chunk, len(blanks))


def _row(order, subquestion, slots, blank_count, *, manual=False):
    return {"kind": "row", "height": 118 if manual else 72, "order_no": order,
            "subquestion_no": subquestion, "slots": slots, "blank_count": blank_count, "manual": manual}


def _paginate(inventory, assignment_type):
    pages, page, y = [], [], TOP
    module, question = None, None
    for block in _blocks(inventory, assignment_type):
        # Keep a heading with at least one writing row; continuations repeat
        # their module/question context rather than orphaning a title.
        reserve = block["height"] + ({"module": 26 + 118, "question": 118}.get(block["kind"], 0))
        if y + reserve > BOTTOM and page:
            pages.append(page)
            if len(pages) >= 100:
                raise ValueError("答题卡超过100页，请减少本次题量")
            page, y = [], TOP
            if block["kind"] != "module" and module:
                page.append({**module, "y": y})
                y += module["height"]
            if block["kind"] == "row" and question:
                page.append({**question, "y": y})
                y += question["height"]
        if block["kind"] == "module":
            module, question = block, None
        elif block["kind"] == "question":
            question = block
        if y + block["height"] > BOTTOM:
            raise ValueError("答题卡单行高度超出页面范围")
        page.append({**block, "y": y})
        y += block["height"]
    if page:
        pages.append(page)
    return pages


def _text(page, rect, text, size=11):
    # All text uses a built-in CJK font; no host font or network dependencies.
    area = fitz.Rect(rect)
    max_lines = max(1, int(area.height / (size * 1.3)))
    lines, current = [], ""
    for char in str(text):
        length = sum(fitz.get_text_length(c, fontname="helv" if ord(c) < 128 else "china-s", fontsize=size)
                     for c in current + char)
        if char == "\n" or length > area.width:
            lines.append(current)
            current = "" if char == "\n" else char
        else:
            current += char
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-2] + "…"
    for index, line in enumerate(lines):
        x = area.x0
        for ascii_run, characters in groupby(line, key=lambda char: ord(char) < 128):
            run = "".join(characters)
            font = "helv" if ascii_run else "china-s"
            page.insert_text((x, area.y0 + size + index * size * 1.3), run, fontname=font, fontsize=size)
            x += fitz.get_text_length(run, fontname=font, fontsize=size)


def render_answer_card(inventory, *, assignment_title, student_code, assignment_type,
                       artifact_id, artifact_code, revision_no, source_sha256):
    pages = _paginate(inventory, assignment_type)
    manifest = {"schema_version": "stage6-answer-card-manifest-v1", "artifact_id": str(artifact_id),
                "artifact_code": artifact_code, "revision_no": revision_no,
                "paper_instance_id": inventory.paper_instance_id, "instance_code": inventory.instance_code,
                "version_no": inventory.version_no, "layout_version": LAYOUT_VERSION,
                "renderer_version": RENDERER_VERSION, "source_sha256": source_sha256,
                "source_inventory_sha256": inventory.inventory_sha256,
                "page_size_pt": [WIDTH, HEIGHT], "total_pages": len(pages), "pages": []}
    with fitz.open() as document:
        for number, blocks in enumerate(pages, 1):
            page = document.new_page(width=WIDTH, height=HEIGHT)
            _text(page, (LEFT, 32, RIGHT, 61), "答题卡（答题区域）", 20)
            for x, y, dx, dy in [(24, 24, 12, 12), (WIDTH - 24, 24, -12, 12),
                                  (24, 787, 12, -12), (WIDTH - 24, 787, -12, -12)]:
                page.draw_polyline([(x + dx, y), (x, y), (x, y + dy)], width=1.5)
            _text(page, (LEFT, 64, RIGHT, 93), assignment_title, 11)
            _text(page, (LEFT, 94, RIGHT, 110), f"学生编号：{student_code}", 10)
            _text(page, (LEFT, 112, RIGHT, 128), f"试卷短码：{inventory.instance_code}    版本：v{inventory.version_no}", 10)
            _text(page, (LEFT, 126, RIGHT, 145), "请按试卷题号，在对应小题和空位作答。", 10)
            page.draw_line((LEFT, 146), (RIGHT, 146), width=1)
            page_manifest = {"page_no": number, "page_token": uuid4().hex, "slots": []}
            for block in blocks:
                y = block["y"]
                if block["kind"] != "row":
                    if block["kind"] == "module":
                        page.draw_rect(fitz.Rect(LEFT, y + 2, RIGHT, y + 27), width=.6)
                    _text(page, (LEFT + 6, y + 5, RIGHT - 4, y + block["height"]), block["text"],
                          12 if block["kind"] == "module" else 10)
                    continue
                _text(page, (LEFT, y + 4, LEFT + 78, y + 43),
                      f"第 {block['order_no']} 题\n（{block['subquestion_no']}）", 10)
                width = (RIGHT - LEFT - 90 - 24) / 3
                for column, slot in enumerate(block["slots"]):
                    x = LEFT + 90 + column * (width + 12)
                    right = RIGHT if block["manual"] else x + width
                    bottom = y + block["height"] - 8
                    label = f"第{slot.blank_no}空" if block["blank_count"] > 1 else ""
                    hint = "作答区" if block["manual"] else "判断结果" if slot.answer_format == "true_false_mark" else ""
                    if label or hint:
                        _text(page, (x, y + 3, right, y + 21), " · ".join(filter(None, [label, hint])), 9)
                    bbox = [round(x, 3), round(y + 23, 3), round(right, 3), round(bottom, 3)]
                    if block["manual"]:
                        page.draw_rect(fitz.Rect(bbox), width=.5)
                    else:
                        page.draw_line((x, bottom), (right, bottom), width=.7)
                    page_manifest["slots"].append({**slot.model_dump(), "slot_token": uuid4().hex,
                                                   "writing_bbox_pt": bbox})
            _text(page, (LEFT, 798, RIGHT, 818),
                  f"产物 {artifact_code} · r{revision_no}    第 {number} / {len(pages)} 页    {LAYOUT_VERSION}", 9)
            manifest["pages"].append(page_manifest)
        document.set_metadata({"title": "答题卡", "creator": RENDERER_VERSION})
        pdf = document.tobytes(garbage=4, deflate=True, no_new_id=True)
    if len(pdf) > 8 * 1024 * 1024:
        raise ValueError("答题卡PDF超过8MB，请减少本次题量")
    actual = [slot["slot_key"] for page in manifest["pages"] for slot in page["slots"]]
    if len(actual) != len(set(actual)) or set(actual) != {s.slot_key for s in inventory.slots}:
        raise ValueError("生成的答题卡页清单未完整覆盖作答空位")
    return pdf, manifest
