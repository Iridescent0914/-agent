from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TranscriptionAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision_no: int = Field(ge=0, strict=True)
    reviewer: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=2, max_length=500)


class MergeTranscription(TranscriptionAction):
    expected_collection_revision: int = Field(ge=0, strict=True)
    expected_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class TranscriptionUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot_key: str = Field(min_length=1, max_length=200)
    recognition_status: Literal["readable", "blank", "missing", "illegible", "ambiguous"]
    answer: str | None = Field(max_length=16000)

    @model_validator(mode="after")
    def valid_answer(self):
        if self.recognition_status == "readable":
            if self.answer is None or not self.answer.strip():
                raise ValueError("清晰作答必须填写答案")
        elif self.answer is not None:
            raise ValueError("留白或待核对状态的答案必须为空")
        return self


class SaveTranscription(TranscriptionAction):
    expected_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    units: list[TranscriptionUnit] = Field(min_length=1, max_length=10000)
    confirmed_page_nos: list[Annotated[int, Field(strict=True, ge=1, le=100)]] = Field(default_factory=list, max_length=100)


class ConfirmTranscription(TranscriptionAction):
    expected_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    transcription_confirmed: Literal[True]

    @model_validator(mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if isinstance(value, dict) and value.get("transcription_confirmed") is not True:
            raise ValueError("必须明确确认已核对整卷答案")
        return value
