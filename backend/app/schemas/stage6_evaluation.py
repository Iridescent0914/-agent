"""Private offline annotations; these labels never authorize grading."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


AnnotationStatus = Literal["pending", "labelled", "disputed", "resolved"]
RecognitionStatus = Literal["readable", "blank", "missing", "illegible", "ambiguous"]


class UnitLabel(EvaluationModel):
    unit_key: str = Field(min_length=1)
    annotation_status: AnnotationStatus = "pending"
    recognition_status: RecognitionStatus | None = None
    answer: str | None = None

    @model_validator(mode="after")
    def completed(self):
        if self.annotation_status in {"labelled", "resolved"}:
            if self.recognition_status is None:
                raise ValueError("已标注空位须填写人工识别状态")
            if self.recognition_status == "readable" and not (self.answer or "").strip():
                raise ValueError("人工可辨空位须填写忠实抄录")
            if self.recognition_status != "readable" and self.answer is not None:
                raise ValueError("非可辨状态的人工答案须为null")
        return self


class SampleLabel(EvaluationModel):
    sample_id: str = Field(min_length=1)
    include: bool = Field(default=True, strict=True)
    source_group_id: str = Field(min_length=1)
    answerer_group_id: str | None = None
    split: Literal["development", "acceptance"] = "development"
    capture_tags: list[str] = Field(default_factory=list)
    reviewer_alias: str | None = None
    reviewed_at: datetime | None = None
    annotation_status: AnnotationStatus = "pending"
    gold_verdict: Literal["equivalent", "not_equivalent", "uncertain"] | None = None
    gold_reason: str | None = None
    identity_correct: bool | None = Field(default=None, strict=True)
    units: list[UnitLabel] = Field(default_factory=list)

    @model_validator(mode="after")
    def completed(self):
        reviewed = self.annotation_status in {"labelled", "resolved"} or any(
            u.annotation_status in {"labelled", "resolved"} for u in self.units)
        if reviewed and (not (self.reviewer_alias or "").strip() or self.reviewed_at is None
                         or self.reviewed_at.tzinfo is None):
            raise ValueError("人工标注须填写核对人代号及带时区的核对时间")
        if len({u.unit_key for u in self.units}) != len(self.units):
            raise ValueError("人工空位重复")
        return self


class EvaluationLabels(EvaluationModel):
    schema_version: Literal["stage6-eval-labels-v1"]
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_version: str = Field(min_length=1, max_length=100)
    samples: list[SampleLabel]

    @model_validator(mode="after")
    def unique_samples(self):
        if len({s.sample_id for s in self.samples}) != len(self.samples):
            raise ValueError("人工样本重复")
        return self
