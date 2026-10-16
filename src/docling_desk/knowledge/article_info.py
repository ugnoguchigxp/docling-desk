"""Common article information, independent of any external publishing service."""

import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


def is_wiki_image(value: str) -> bool:
    return bool(re.fullmatch(r"/api/wiki/images/[a-f0-9]{64}\.(png|jpg|gif|webp)", value))


def public_url(value: str) -> str:
    if not value:
        return value
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("URLはHTTPまたはHTTPSで指定してください。")
    if any(ord(c) < 33 for c in value):
        raise ValueError("URLに空白や制御文字は使用できません。")
    return value


class ArticleImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(default="", max_length=2048)
    alt: str = Field(default="", max_length=1000)
    caption: str = Field(default="", max_length=2000)
    credit: str = Field(default="", max_length=2000)

    @field_validator("url")
    @classmethod
    def image_url(cls, value):
        # The only relative URL allowed here is an immutable, locally uploaded image.
        if is_wiki_image(value):
            return value
        return public_url(value)


class ArticleInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subtitle: str = Field(default="", max_length=1000)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    original_url: str = Field(default="", max_length=2048)
    image: ArticleImage = Field(default_factory=ArticleImage)

    @field_validator("keywords")
    @classmethod
    def keywords_valid(cls, values):
        if any(
            not value.strip() or len(value) > 100 or any(ord(c) < 32 for c in value)
            for value in values
        ):
            raise ValueError("キーワードは1件100文字以内で指定してください。")
        return list(dict.fromkeys(value.strip() for value in values))

    @field_validator("original_url")
    @classmethod
    def original_url_valid(cls, value):
        return public_url(value)
