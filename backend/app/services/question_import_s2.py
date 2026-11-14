"""S2 PDF preparation and strictly checked Qwen question extraction."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
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
from typing import Callable

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pypdf import PdfReader, PdfWriter

from app.core.config import Settings, get_settings
from app.services.question_import import DOCX_CONVERSION_VERSION, ImportProblem, _path_for

PROMPT_VERSION = "question-import-s2-v2"
FLASH_MODEL = "qwen3.8-flash"


class ModelOption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str
    text: str


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
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    uncertain_fragments: list[str] = Field(default_factory=list)


class ModelUnresolved(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    page: int = Field(ge=1)
    text: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    complete: bool
    questions: list[ModelQuestion]
    unresolved_fragments: list[ModelUnresolved] = Field(default_factory=list)


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
    return units


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
                shutil.copyfile(source, docx)
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


def parse_model_output(value: object, unit: PageUnit) -> ModelOutput:
    if isinstance(value, list):
        detail = "空数组 []" if not value else "数组"
        raise ImportProblem("INVALID_MODEL_OUTPUT", f"模型返回{detail}，需要包含 complete、questions 的 JSON 对象")
    try:
        output = ModelOutput.model_validate(value)
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
        raise ImportProblem("INVALID_MODEL_OUTPUT", f"模型返回字段不符合 S2 契约{detail}") from exc
    if not output.complete:
        raise ImportProblem("INCOMPLETE_MODEL_OUTPUT", "模型报告页段提取不完整")
    for question in output.questions:
        if question.page_start > question.page_end or question.page_end > unit.page_count:
            raise ImportProblem("INVALID_MODEL_PAGE", "模型题目页码超出处理页段")
    if any(fragment.page > unit.page_count for fragment in output.unresolved_fragments):
        raise ImportProblem("INVALID_MODEL_PAGE", "待处理片段页码超出处理页段")
    return output


def candidate_from_question(file_id: str, question: ModelQuestion, unit: PageUnit) -> dict:
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
        "extraction": {"prompt_version": PROMPT_VERSION, "subquestions": subquestions,
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
            raise ImportProblem("S2_MODEL_NOT_SUPPORTED", "S2 当前仅支持 Flash 模型", 409)
        if not self.settings.question_import_s2_api_key or not self.settings.question_import_s2_base_url.startswith("https://"):
            raise ImportProblem("S2_NOT_CONFIGURED", "百炼密钥或 HTTPS 工作空间地址未配置", 409)
        self.transport = transport

    def extract(self, pdf_data: bytes, *, page_count: int,
                capture_response: Callable[[dict, float], None] | None = None) -> tuple[ModelOutput, dict, float]:
        prompt = (
            "你是数学试题转录员。按原文逐题提取本 PDF 的所有题目，输出 JSON。"
            "页码使用本 PDF 内从 1 开始的页码。保留公式原样，子题逐条列出；图形仅记录线索，不猜测图中文字。"
            "答案和解析不要混入题干。无法辨认或无法判断归属的内容放入 unresolved_fragments。"
            "不要凭空补题、补选项或补页码。只有完整检查所有页后 complete 才为 true。"
            "输出对象字段：complete 布尔值；questions 数组，每题包含 original_number、question_text、"
            "options（key/text 数组）、subquestions、math_fragments、diagram_clues、page_start、page_end、"
            "uncertain_fragments；不要输出区域坐标；"
            "unresolved_fragments 数组，每项包含 page、text、reason。"
        )
        payload = {
            "model": self.settings.question_import_s2_model,
            "messages": [{"role": "user", "content": [
                {"type": "file", "file": {"file_data": "data:application/pdf;base64," + base64.b64encode(pdf_data).decode("ascii"),
                                          "filename": "question-pages.pdf"}},
                {"type": "text", "text": prompt},
            ]}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "s2_page_questions", "strict": True,
                "schema": ModelOutput.model_json_schema(),
            }},
            "include_tool_usage": True,
            "max_tokens": 8192,
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
                                            model=self.settings.question_import_s2_model)
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


def decode_qwen_response(body: dict, *, page_count: int, elapsed: float, model: str) -> tuple[ModelOutput, dict]:
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
    output = parse_model_output(raw, unit)
    usage = body.get("usage") or {}
    return output, {"usage": usage, "elapsed_seconds": round(elapsed, 3),
                    "model": model,
                    "prompt_version": PROMPT_VERSION, "response_id": body.get("id")}

