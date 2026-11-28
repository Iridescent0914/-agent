"""S2 PDF preparation and strictly checked Qwen question extraction."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
import os
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable, Literal
from xml.dom import minidom
from xml.parsers.expat import ExpatError

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pypdf import PdfReader, PdfWriter

from app.core.config import Settings, get_settings
from app.services.question_import import DOCX_CONVERSION_VERSION, ImportProblem, _path_for

PROMPT_VERSION = "question-import-s2-v3"
COMBINED_PROMPT_VERSION = "question-import-combined-v9"
FLASH_MODEL = "qwen3.8-flash"


class ModelOption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str
    text: str


class ModelFigure(BaseModel):
    """A source image or a region to render, within one model PDF page."""
    model_config = ConfigDict(extra="forbid", strict=True)
    page: int = Field(ge=1)
    asset_id: str | None = None
    bbox: list[float] | None = None  # left, top, right, bottom on a 0..999 page grid


class ModelSubquestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str
    text: str = Field(min_length=1)


class ModelNumberedSubquestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    number: str
    text: str = Field(min_length=1)


class ModelLabeledSubquestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    label: str
    text: str = Field(min_length=1)


class ModelOriginalNumberSubquestion(BaseModel):
    """Accept the parent question field names when the model reuses them for a subquestion."""
    model_config = ConfigDict(extra="forbid", strict=True)
    original_number: str
    question_text: str = Field(min_length=1)


class ModelQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    original_number: str | None = None
    question_text: str = Field(min_length=1)
    options: list[ModelOption] = Field(default_factory=list)
    subquestions: list[str | ModelSubquestion | ModelNumberedSubquestion | ModelLabeledSubquestion | ModelOriginalNumberSubquestion] = Field(default_factory=list)
    math_fragments: list[str] = Field(default_factory=list)
    diagram_clues: list[str] = Field(default_factory=list)
    figures: list[ModelFigure] = Field(default_factory=list)
    figure_status: Literal["no_figure", "complete", "uncertain"] = "uncertain"
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    uncertain_fragments: list[str] = Field(default_factory=list)


class ModelUnresolved(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    page: int = Field(ge=1)
    text: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ModelSourceAnswerSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    page: int = Field(ge=1)
    evidence: str = Field(min_length=1)


class ModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    complete: bool
    questions: list[ModelQuestion]
    unresolved_fragments: list[ModelUnresolved] = Field(default_factory=list)
    source_answer_signals: list[ModelSourceAnswerSignal] = Field(default_factory=list)


class ModelSourceAnswer(BaseModel):
    """An answer transcribed from the source, with no inferred question ownership."""
    model_config = ConfigDict(extra="forbid", strict=True)
    original_number: str | None = None
    subquestion_number: str | None = None
    answer_text: str = Field(min_length=1)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    uncertain_fragments: list[str] = Field(default_factory=list)
    question_excerpt: str | None = None
    answer_form: str | None = None


class CombinedModelOutput(ModelOutput):
    source_answers: list[ModelSourceAnswer]


def pdf_page_roles(pdf: Path | bytes) -> list[str]:
    """Classify only unambiguous whole pages; preserve in-page transitions."""
    reader = PdfReader(BytesIO(pdf) if isinstance(pdf, bytes) else str(pdf))
    role = "question"
    roles: list[str] = []
    for page in reader.pages:
        lines = [re.sub(r"\s+", "", line) for line in (page.extract_text() or "").splitlines()]
        lines = [line for line in lines if line]
        transitions = []
        for index, line in enumerate(lines):
            if re.fullmatch(r"(?:参考答案(?:与试题解析|与解析|及解析)?|答案(?:与解析|及解析)?|试题解析)", line):
                transitions.append((index, "answer"))
            elif re.fullmatch(r"(?:考点卡片|知识点卡片|知识点总结)", line):
                transitions.append((index, "reference"))
            elif re.fullmatch(r"(?:题目部分|试题部分|练习题部分)", line):
                transitions.append((index, "question"))
        if transitions:
            # A heading after preceding content may split the page. Keep every
            # candidate on that page until it can be checked more precisely.
            roles.append(transitions[0][1] if len(transitions) == 1 and transitions[0][0] == 0
                         else "mixed")
            role = transitions[-1][1]
        else:
            roles.append(role)
    return roles


@dataclass(frozen=True)
class PageUnit:
    index: int
    first_page: int
    last_page: int
    core_first_page: int
    core_last_page: int

    @property
    def page_count(self) -> int:
        return self.last_page - self.first_page + 1


def plan_units(page_count: int, *, pages_per_unit: int, overlap: int) -> list[PageUnit]:
    if page_count < 1 or pages_per_unit < 1 or overlap < 0 or overlap >= pages_per_unit:
        raise ImportProblem("INVALID_S2_CONFIG", "PDF 分段参数无效")
    units = []
    for index, core_start in enumerate(range(1, page_count + 1, pages_per_unit)):
        core_end = min(page_count, core_start + pages_per_unit - 1)
        if units and core_end <= units[-1].last_page:
            break
        units.append(PageUnit(index, core_start,
                              min(page_count, core_end + overlap), core_start, core_end))
    if units[-1].core_last_page < page_count:
        last = units[-1]
        units[-1] = PageUnit(last.index, last.first_page, last.last_page,
                             last.core_first_page, page_count)
    return units


def _normalize_docx_math_runs(source: Path, target: Path) -> int:
    """Move OMML nested in a Word run to paragraph level for LibreOffice.

    Some Word-produced files put m:oMath inside w:r. Word renders these, but
    LibreOffice silently drops them even when libreoffice-math is installed.
    The original upload is never changed; only the temporary conversion input
    is rewritten. Preserve ZIP members and XML prefixes via minidom.
    """
    word_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    math_ns = "http://schemas.openxmlformats.org/officeDocument/2006/math"
    moved = 0
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as normalized:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename == "word/document.xml":
                document = minidom.parseString(data)
                for math in list(document.getElementsByTagNameNS(math_ns, "oMath")):
                    run = math.parentNode
                    if run.namespaceURI != word_ns or run.localName != "r":
                        continue
                    paragraph = run.parentNode
                    if paragraph.namespaceURI != word_ns or paragraph.localName != "p":
                        raise ImportProblem("DOCX_CONVERSION_FAILED",
                                            "DOCX 公式位于不支持的嵌套结构中，请检查原文件")
                    properties = next((node for node in run.childNodes
                                       if node.nodeType == node.ELEMENT_NODE
                                       and node.namespaceURI == word_ns
                                       and node.localName == "rPr"), None)
                    replacement = []
                    segment = []

                    def flush_segment() -> None:
                        if not segment:
                            return
                        new_run = run.cloneNode(False)
                        if properties is not None:
                            new_run.appendChild(properties.cloneNode(True))
                        for node in segment:
                            new_run.appendChild(node)
                        replacement.append(new_run)
                        segment.clear()

                    for child in list(run.childNodes):
                        if child is properties or child.nodeType != child.ELEMENT_NODE:
                            continue
                        if child.namespaceURI == math_ns and child.localName == "oMath":
                            flush_segment()
                            replacement.append(child)
                            moved += 1
                        else:
                            segment.append(child)
                    flush_segment()
                    for node in replacement:
                        paragraph.insertBefore(node, run)
                    paragraph.removeChild(run)
                data = document.toxml(encoding="utf-8")
            normalized.writestr(info, data)
    return moved


def prepare_pdf(batch_id: uuid.UUID, file: dict, *, settings: Settings | None = None) -> tuple[Path, int, list[str]]:
    settings = settings or get_settings()
    source = _path_for(batch_id, uuid.UUID(file["file_id"]))
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != file["sha256"]:
        raise ImportProblem("FILE_EXPIRED", "原文件不存在或校验失败")
    if file["media_type"] == "application/pdf":
        pdf = source
        warnings: list[str] = []
    elif file["media_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        pdf = source.with_name(source.name + f".{DOCX_CONVERSION_VERSION}.converted.pdf")
        warnings = ["DOCX 转换结果须对照原件人工检查公式和图形"]
        with zipfile.ZipFile(source) as archive:
            source_media_count = sum(name.startswith("word/media/") and not name.endswith("/")
                                     for name in archive.namelist())
        if not pdf.is_file():
            with tempfile.TemporaryDirectory(prefix="s2-lo-") as temp_dir:
                temp = Path(temp_dir)
                docx = temp / "source.docx"
                try:
                    _normalize_docx_math_runs(source, docx)
                except ImportProblem:
                    raise
                except (zipfile.BadZipFile, ExpatError, OSError) as exc:
                    raise ImportProblem("DOCX_CONVERSION_FAILED", "DOCX 公式结构无法解析或转换") from exc
                try:
                    result = subprocess.run(
                        ["libreoffice", "-env:UserInstallation=file://" + (temp / "profile").as_posix(),
                         "--headless", "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(temp), str(docx)],
                        capture_output=True, text=True, timeout=120, check=False,
                    )
                except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                    raise ImportProblem("DOCX_CONVERSION_FAILED", "LibreOffice 不可用或转换超时") from exc
                converted = temp / "source.pdf"
                if result.returncode != 0 or not converted.is_file():
                    raise ImportProblem("DOCX_CONVERSION_FAILED", "DOCX 转 PDF 失败")
                tmp_target = pdf.with_name(pdf.name + ".tmp")
                shutil.copyfile(converted, tmp_target)
                os.replace(tmp_target, pdf)
    else:
        raise ImportProblem("INVALID_FILE", "文件类型不受支持")
    try:
        reader = PdfReader(str(pdf), strict=True)
        if reader.is_encrypted:
            raise ValueError("encrypted")
        pages = len(reader.pages)
        if pages < 1 or pages > settings.question_import_max_pdf_pages:
            raise ImportProblem("FILE_LIMIT_EXCEEDED", "转换后的 PDF 页数超过试点上限", 413)
        if any(page.get_contents() is None for page in reader.pages):
            warnings.append("PDF 包含空白内容页；请核对该页是否有遗漏题目")
        if file["media_type"] != "application/pdf" and source_media_count:
            pdf_image_count = sum(len(page.images) for page in reader.pages)
            if pdf_image_count == 0:
                raise ImportProblem("DOCX_CONVERSION_FAILED", "DOCX 含图片，但转换后的 PDF 未检测到图片")
    except ImportProblem:
        raise
    except Exception as exc:
        raise ImportProblem("DOCX_CONVERSION_FAILED", "转换后的 PDF 无法读取") from exc
    return pdf, pages, warnings


def pdf_unit_bytes(pdf: Path, unit: PageUnit) -> bytes:
    reader = PdfReader(str(pdf))
    if unit.first_page < 1 or unit.last_page > len(reader.pages):
        raise ImportProblem("INVALID_PAGE", "处理页段超出 PDF 范围")
    writer = PdfWriter()
    for page in range(unit.first_page - 1, unit.last_page):
        writer.add_page(reader.pages[page])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def parse_model_output(value: object, unit: PageUnit, *, include_answers: bool = False) -> ModelOutput | CombinedModelOutput:
    if isinstance(value, list):
        detail = "空数组 []" if not value else "数组"
        raise ImportProblem("INVALID_MODEL_OUTPUT", f"模型返回{detail}，需要包含 complete、questions 的 JSON 对象")
    try:
        output = (CombinedModelOutput if include_answers else ModelOutput).model_validate(value)
    except ValidationError as exc:
        location = []
        for part in exc.errors()[0]["loc"]:
            if part == "str" or (isinstance(part, str) and part.startswith("Model")):
                break
            if isinstance(part, int):
                location.append(f"[{part}]")
            else:
                location.append(("." if location else "") + part)
        detail = f"（位置：{''.join(location)}）" if location else ""
        raise ImportProblem("INVALID_MODEL_OUTPUT", f"题目识别模型返回格式不符合要求{detail}") from exc
    if not output.complete:
        reason = next((fragment.reason for fragment in output.unresolved_fragments if fragment.reason), None)
        detail = f"：{reason[:100]}" if reason else ""
        raise ImportProblem("INCOMPLETE_MODEL_OUTPUT", "模型报告页段提取不完整" + detail)
    for question in output.questions:
        if question.page_start > question.page_end or question.page_end > unit.page_count:
            raise ImportProblem("INVALID_MODEL_PAGE", "模型题目页码超出处理页段")
    if any(fragment.page > unit.page_count for fragment in output.unresolved_fragments):
        raise ImportProblem("INVALID_MODEL_PAGE", "待处理片段页码超出处理页段")
    if any(signal.page > unit.page_count for signal in output.source_answer_signals):
        raise ImportProblem("INVALID_MODEL_PAGE", "资料答案线索页码超出处理页段")
    if include_answers and any(answer.page_start > answer.page_end or answer.page_end > unit.page_count
                               for answer in output.source_answers):
        raise ImportProblem("INVALID_MODEL_PAGE", "资料答案页码超出处理页段")
    return output


def candidate_from_question(file_id: str, question: ModelQuestion, unit: PageUnit,
                            *, prompt_version: str = PROMPT_VERSION) -> dict:
    page_start = unit.first_page + question.page_start - 1
    page_end = unit.first_page + question.page_end - 1
    text = question.question_text.strip()
    subquestions = [subquestion_text(value) for value in question.subquestions]
    if subquestions:
        text += "\n" + "\n".join(subquestions)
    warnings = []
    if question.diagram_clues:
        warnings.append("题图尚未裁剪；请对照原页复核")
    if question.uncertain_fragments:
        warnings.append("模型报告不确定片段；请人工复核")
    return {
        "schema_version": "import-candidate-v1",
        "source": {"file_id": file_id, "original_number": question.original_number,
                   "page_start": page_start, "page_end": page_end,
                   "evidence": [{"file_id": file_id, "page": page_start, "block_id": None,
                                 "quote": question.question_text[:300]}]},
        "content": {"question_text": text,
                    "options": [option.model_dump() for option in question.options],
                    "solution": None, "asset_ids": []},
        "tags": {"question_type": "unknown", "difficulty": None, "grade": None,
                 "chapter_code": None, "section": None, "main_kp_code": None,
                 "core_literacies": [], "is_required": False},
        "answers": [], "selected_answer_id": None,
        "answer_match_status": "not_applicable", "precheck_status": "not_run",
        "label_status": "needs_confirmation", "teacher_decision": "pending",
        "commit_intent": "library_only", "duplicate_status": "none",
        "field_confidence": {}, "field_evidence": {}, "warnings": warnings,
        "extraction": {"prompt_version": prompt_version, "subquestions": subquestions,
                       "math_fragments": question.math_fragments,
                       "diagram_clues": question.diagram_clues,
                           "uncertain_fragments": question.uncertain_fragments},
    }


def subquestion_text(value: str | ModelSubquestion | ModelNumberedSubquestion | ModelLabeledSubquestion | ModelOriginalNumberSubquestion) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, ModelSubquestion):
        number = value.key
    elif isinstance(value, ModelNumberedSubquestion):
        number = value.number
    elif isinstance(value, ModelLabeledSubquestion):
        number = value.label
    else:
        return (value.original_number + value.question_text).strip()
    return (number + value.text).strip()


def candidate_key(payload: dict) -> str:
    source = payload["source"]
    content = payload["content"]
    value = json.dumps([source["file_id"], source["page_start"],
                        source["original_number"], content["question_text"], content["options"]],
                       ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _comparison_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).lower().replace("−", "-")
    text = re.sub(r"\\(?:left|right)\b", "", text)
    text = re.sub(r"\\pm\b", "±", text)
    text = re.sub(r"\^\{\\circ\}", "°", text)
    text = re.sub(r"\\circ\b", "°", text)
    text = re.sub(r"\\times\b", "×", text)
    text = re.sub(r"\\(?:dfrac|tfrac|frac)\{([^{}]+)\}\{([^{}]+)\}", r"\1/\2", text)
    return re.sub(r"[\s\$\\{}|*_，。,.：:；;（）()\[\]【】]+", "", text)


def _numeric_values(value: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", unicodedata.normalize("NFKC", value)))


def _question_number(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").strip().lower()
    text = re.sub(r"^第\s*", "", text)
    return re.sub(r"\s*题?[.．、:：\s]*$", "", text)


def overlap_candidate_is_duplicate(existing: dict, incoming: dict, *, boundary_page: int) -> bool:
    """Only merge strongly supported duplicate interpretations around one S2 page boundary."""
    old_source, new_source = existing["source"], incoming["source"]
    if old_source["file_id"] != new_source["file_id"]:
        return False
    old_start, new_start = old_source["page_start"], new_source["page_start"]
    if not isinstance(old_start, int) or not isinstance(new_start, int):
        return False
    if abs(old_start - new_start) > 1 or (boundary_page not in {old_start, new_start}
                                               and old_start != new_start):
        return False
    old_number = _question_number(old_source.get("original_number"))
    new_number = _question_number(new_source.get("original_number"))
    if bool(old_number) != bool(new_number) or (old_number and old_number != new_number):
        return False
    old_text = _comparison_text(existing["content"]["question_text"])
    new_text = _comparison_text(incoming["content"]["question_text"])
    if not old_text or not new_text:
        return False
    old_numbers = re.findall(r"\d+(?:\.\d+)?", old_text)
    new_numbers = re.findall(r"\d+(?:\.\d+)?", new_text)
    similarity = SequenceMatcher(None, old_text, new_text, autojunk=False).ratio()
    old_options = existing["content"].get("options") or []
    new_options = incoming["content"].get("options") or []
    if bool(old_options) != bool(new_options):
        return False
    if old_options and new_options:
        if [option["key"] for option in old_options] != [option["key"] for option in new_options]:
            return False
        old_option_text = _comparison_text(" ".join(option["text"] for option in old_options))
        new_option_text = _comparison_text(" ".join(option["text"] for option in new_options))
        if old_option_text != new_option_text:
            return False
    if old_numbers == new_numbers and similarity >= (0.86 if old_number else 0.95):
        return True
    if not old_number or old_start != new_start:
        return False
    old_values = _numeric_values(existing["content"]["question_text"])
    new_values = _numeric_values(incoming["content"]["question_text"])
    if old_options:
        return bool(old_values == new_values and similarity >= 0.45)
    if old_values == new_values and similarity >= 0.60:
        return True
    shorter, longer = sorted((old_text, new_text), key=len)
    return bool(len(shorter) >= 8 and shorter in longer
                and (old_values.issubset(new_values) or new_values.issubset(old_values)))


def resolved_overlap_option(fragment: dict, *, boundary_page: int, previous: list[dict]) -> bool:
    """An orphan option on the shared page is already attached to the previous-page question."""
    if fragment["page"] != boundary_page:
        return False
    match = re.match(r"^\s*([A-Za-z])\s*[.．、:：]\s*(.+?)\s*$", fragment["text"])
    if not match:
        return False
    key, text = match.group(1).upper(), _comparison_text(match.group(2))
    if not text:
        return False
    for payload in previous:
        source = payload["source"]
        if source.get("page_start") != boundary_page - 1:
            continue
        for option in payload["content"].get("options") or []:
            if option["key"].upper() == key and _comparison_text(option["text"]) == text:
                return True
    return False


def estimate_cost(usage: dict, *, settings: Settings | None = None) -> float | None:
    settings = settings or get_settings()
    try:
        input_tokens = int(usage["prompt_tokens"])
        output_tokens = int(usage["completion_tokens"])
        pages = int(usage["x_tools"]["pdf_page_parser"]["count"])
        if min(input_tokens, output_tokens, pages) < 0:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return round(pages * settings.question_import_s2_pdf_page_price_cny
                 + input_tokens * settings.question_import_s2_input_million_price_cny / 1_000_000
                 + output_tokens * settings.question_import_s2_output_million_price_cny / 1_000_000, 6)


class QwenPdfProvider:
    def __init__(self, *, settings: Settings | None = None, transport: httpx.BaseTransport | None = None):
        self.settings = settings or get_settings()
        if self.settings.question_import_s2_model != FLASH_MODEL:
            raise ImportProblem("S2_MODEL_NOT_SUPPORTED", "题目识别当前仅支持 Flash 模型", 409)
        if not self.settings.question_import_s2_api_key or not self.settings.question_import_s2_base_url.startswith("https://"):
            raise ImportProblem("S2_NOT_CONFIGURED", "百炼密钥或 HTTPS 工作空间地址未配置", 409)
        self.transport = transport

    def extract(self, pdf_data: bytes, *, page_count: int, core_page_count: int | None = None,
                capture_response: Callable[[dict, float], None] | None = None,
                include_answers: bool = False,
                page_roles: list[str] | None = None,
                embedded_images: list[dict] | None = None,
                page_previews: list[bytes] | None = None) -> tuple[ModelOutput | CombinedModelOutput, dict, float]:
        core_page_count = page_count if core_page_count is None else core_page_count
        if not 1 <= core_page_count <= page_count:
            raise ImportProblem("INVALID_S2_CONFIG", "PDF 主体页数无效")
        overlap_count = page_count - core_page_count
        page_scope = (
            f"本 PDF 前 {core_page_count} 页是主体页，最后 {overlap_count} 页是重叠页。"
            "只提取题干起始于主体页的题；重叠页只用于补全主体页开始的跨页题。"
            "不要输出重叠页中新起的题，也不要因这些题在本 PDF 中尚未结束而把 complete 设为 false。"
            if overlap_count else f"本 PDF 共 {core_page_count} 页，全部是主体页。"
        )
        prompt = (
            "你是数学试题转录员。按原文逐题提取题目，输出 JSON。"
            + page_scope
            + "页码使用本 PDF 内从 1 开始的页码。保留公式原样，子题逐条列出；图形仅记录线索，不猜测图中文字。"
            "答案和解析不要混入题干。无法辨认或无法判断归属的内容放入 unresolved_fragments。"
            "不要凭空补题、补选项或补页码。只有主体页中起始的题都已完整提取，complete 才为 true；"
            "若主体页中起始的题延续到本 PDF 之外，complete 必须为 false。"
            "输出对象字段：complete 布尔值；questions 数组，每题包含 original_number、question_text、"
            "options（key/text 数组）、subquestions、math_fragments、diagram_clues、page_start、page_end、"
            "uncertain_fragments；不要输出区域坐标；"
            "unresolved_fragments 数组，每项包含 page、text、reason。"
            "如果发现原文中有资料答案或解析区域，不论上传者选择了哪种文件场景，"
            "在 source_answer_signals 中逐条给出 page 和可核对的 evidence；没有则为空数组。"
            "这只用于提示场景可能选错，不改变上传者选择，也不把答案混入题干。"
        )
        if include_answers:
            roles = page_roles if page_roles is not None else pdf_page_roles(pdf_data)
            if len(roles) != page_count:
                raise ImportProblem("INVALID_S2_CONFIG", "PDF 章节页数与处理页段不一致")
            role_labels = {"question": "题目章节", "answer": "答案章节",
                           "reference": "参考资料章节", "mixed": "页内混排"}
            role_text = "、".join(f"第{i}页={role_labels[role]}" for i, role in enumerate(roles, 1))
            prompt += (
                    f"可搜索文字中的章节标题提示：{role_text}。标题只提示答案或资料的来源，不能决定是否提取题目。"
                    "逐页识别能够完整转录、可独立使用的题目；即使文件开头就是‘参考答案与试题解析’，"
                    "其中完整重述的题目也放入 questions。题目区和答案区都按同一标准处理，"
                    "不要根据是否在其他页出现过同题自行去重，跨页段重复留给整批去重。"
                    "解析步骤、只有题号或残缺题干的内容、目录、知识讲解和示范解法不作为题目。"
                    "原文明确给出的答案另放入 source_answers，即使同页也同时提取；不要把答案混进题干。"
                    "主体页开始的题若需要下一页才能完整转录，可以利用重叠页补全；"
                    "不要把仅在重叠页新开始的题单独输出。"
                    "无法完整辨认的题目或答案放入 unresolved_fragments。"
                    "题图需和相应 questions 对应；缺少自动提取的题图时给出裁剪坐标。"
            )
            prompt += (
                        "逐页先定位每道独立题目的题干起点，再判断其后的小题、选项、图注及题干续文归属。"
                        "主体页的任意位置都可能承接上一页段尚未结束的题；编号可能采用圆圈数字、"
                        "括号数字、普通数字、字母、罗马数字或其他形式，也可能没有编号。"
                        "若根据题干、问法、排版和编号层级可判断某段只是前一题的续文，"
                        "而该题的独立题干起点不在本 PDF 内，则这段续文不作为新题；"
                        "不要因本 PDF 缺少它的前序小题、选项或题干而放入 unresolved_fragments，"
                        "也不要仅因此将 complete 设为 false。不要自行补写缺失的前文。"
                        "不得仅凭编号的形式、大小或是否从页首开始判断为续文；"
                        "若主体页内确有完整的独立新题起点，即使编号重复或接续前页编号，也须提取。"
                        "独立题的原题号按原文填入 original_number；属于同一题的小题编号保留在 subquestions，"
                        "不要把小题编号改写成独立题号，也不要重排题号。"
                        "继续检查同页其余内容；只有主体页真正开始的新题本身无法完整转录时才报告不完整。"
                        "若不确定某段是否为独立新题，但它能完整转录，应放入 questions 供后续核对。"
                        "答案章节中完整重述、能独立使用且在主体页开始的题仍按题目提取；"
                        "不得仅因它与前面页段的题目相似而丢弃。"
            )
            prompt += (
                "本文件同时含题目与答案。一次阅读中另行提取原文明确给出的标准答案，放入 source_answers 数组；"
                "只提取答案起始于主体页的内容，重叠页只用于补全跨页答案。"
                "每项包含 original_number、subquestion_number、answer_text、page_start、page_end、"
                "uncertain_fragments、question_excerpt、answer_form。题号或小题号无法辨认时填 null，不按位置猜配。"
                "不要把解题步骤、解析、模型自行计算的结果当成资料答案；看不清的内容放入"
                "unresolved_fragments。题目和答案分别输出，不要决定两者的对应关系。"
            )
        if embedded_images is not None:
            if page_previews is None or len(page_previews) != page_count:
                raise ImportProblem("INVALID_S2_CONFIG", "题图原页预览数量与 PDF 页段不符")
            prompt += (
                "同时检查每道题需要的图。下面的 embedded_images 是程序从本页段 PDF 自动提取的位图，"
                "其中 page 是本 PDF 内页码，bbox 为原页宽高归一化到 0..999 的坐标。"
                "每道题在 figures 中逐张列出所需题图：只有位图本身就是该题独立、完整的题图时，"
                "才填写其 asset_id，bbox=null；整页扫描图、含多道题的拼图不能直接作为单题题图。"
                "若没有这样的现成位图，或位图缺少必要线条、文字标注，则 asset_id=null，"
                "填写完整题图 bbox=[左,上,右,下]。"
                "坐标以随 PDF 按顺序发送的原页预览图为准，范围 0..999；只裁题图及必要标注，不包含整道题干。"
                "page 使用本 PDF 内页码；图跨页时按页分别列出。"
                "figure_status 为 no_figure、complete、uncertain：没有题图时填 no_figure 且 figures=[]；"
                "只有所有需要的图都列出时填 complete；"
                "看不清、无法可靠定位或归属时填 uncertain，不要猜坐标或题目归属。"
                + "不要把普通行内公式、装饰图或仅用于解析步骤的插图当作独立题图；"
                "答案章节中的完整题目若需要题图，也须按相同标准对应或定位。"
                + json.dumps({"embedded_images": embedded_images}, ensure_ascii=False)
            )
        output_model = CombinedModelOutput if include_answers else ModelOutput
        content = [{"type": "file", "file": {"file_data": "data:application/pdf;base64," + base64.b64encode(pdf_data).decode("ascii"),
                                             "filename": "question-pages.pdf"}}]
        if page_previews is not None:
            content.extend({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}}
                           for png in page_previews)
        content.append({"type": "text", "text": prompt})
        payload = {
            "model": self.settings.question_import_s2_model,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "s2_page_questions_answers" if include_answers else "s2_page_questions",
                "strict": True, "schema": output_model.model_json_schema(),
            }},
            "include_tool_usage": True,
            "max_tokens": self.settings.question_import_s2_max_output_tokens,
        }
        start = time.monotonic()
        try:
            with httpx.Client(timeout=self.settings.question_import_s2_timeout_seconds, transport=self.transport) as client:
                response = client.post(self.settings.question_import_s2_base_url.rstrip("/") + "/chat/completions",
                                       headers={"Authorization": "Bearer " + self.settings.question_import_s2_api_key},
                                       json=payload)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ImportProblem("MODEL_REQUEST_FAILED", "百炼请求失败；请检查模型、地域、配额及网络") from exc
        elapsed = time.monotonic() - start
        body = discard_model_regions(body)
        if capture_response is not None:
            capture_response(body, elapsed)
        output, meta = decode_qwen_response(body, page_count=page_count, elapsed=elapsed,
                                            model=self.settings.question_import_s2_model,
                                            include_answers=include_answers)
        return output, meta, elapsed


def discard_model_regions(body: dict) -> dict:
    """Ignore unsolicited coordinates before validation or private response capture."""
    try:
        content = body["choices"][0]["message"]["content"]
        raw = json.loads(content)
        questions = raw.get("questions") if isinstance(raw, dict) else None
        if not isinstance(questions, list) or not any(
            isinstance(question, dict) and "region" in question for question in questions
        ):
            return body
        cleaned = copy.deepcopy(body)
        cleaned_raw = json.loads(cleaned["choices"][0]["message"]["content"])
        for question in cleaned_raw["questions"]:
            if isinstance(question, dict):
                question.pop("region", None)
        cleaned["choices"][0]["message"]["content"] = json.dumps(cleaned_raw, ensure_ascii=False)
        return cleaned
    except (KeyError, IndexError, TypeError, ValueError):
        return body


def decode_qwen_response(body: dict, *, page_count: int, elapsed: float, model: str,
                         include_answers: bool = False) -> tuple[ModelOutput | CombinedModelOutput, dict]:
    """Validate a response, including one captured before a worker crash."""
    try:
        choice = discard_model_regions(body)["choices"][0]
        finish_reason = choice["finish_reason"]
        raw = json.loads(choice["message"]["content"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ImportProblem("INVALID_MODEL_OUTPUT", "百炼未返回有效 JSON 题目数据") from exc
    if finish_reason != "stop":
        raise ImportProblem("MODEL_OUTPUT_TRUNCATED", "模型输出截断或未正常结束")
    unit = PageUnit(0, 1, page_count, 1, page_count)
    output = parse_model_output(raw, unit, include_answers=include_answers)
    usage = body.get("usage") or {}
    return output, {"usage": usage, "elapsed_seconds": round(elapsed, 3),
                    "model": model,
                    "prompt_version": COMBINED_PROMPT_VERSION if include_answers else PROMPT_VERSION,
                    "response_id": body.get("id")}

