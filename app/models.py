"""Small, explicit data shapes shared by the API and the workflow."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AnalyzeRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=12, pattern=r"^[A-Za-z][A-Za-z0-9.\-]{0,11}$")
    mode: Literal["demo", "live"] = "demo"

    @field_validator("ticker")
    @classmethod
    def normalize(cls, value: str) -> str:
        return value.upper()


class Source(BaseModel):
    id: str
    title: str
    url: str
    as_of: str
    kind: Literal["sample", "market", "filing"]
    excerpt: str


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(description="Short qualitative sentence. No digits, prices or percentages.")
    source_ids: list[str] = Field(description="One or more IDs from the supplied evidence, never URLs.")
    kind: Literal["fact", "interpretation", "question"]


class Brief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overview: list[Claim]
    strengths: list[Claim]
    risks: list[Claim]
    watch: list[Claim]
