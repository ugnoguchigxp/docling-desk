"""Persist Plate features which have no lossless Markdown representation."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class EditorDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    value: list[dict[str, JsonValue]] = Field(min_length=1, max_length=20000)
    discussions: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def valid_document(self):
        if (
            len(json.dumps(self.model_dump(), ensure_ascii=False, allow_nan=False).encode())
            > 8 * 1024 * 1024
        ):
            raise ValueError("編集状態は8 MiB以内にしてください。")
        count = 0

        def visit(nodes, depth=0):
            nonlocal count
            if depth > 100:
                raise ValueError("記事の入れ子が深すぎます。")
            for node in nodes:
                count += 1
                if count > 100000 or not isinstance(node, dict):
                    raise ValueError("編集状態のノードが不正です。")
                if "url" in node and not isinstance(node["url"], str):
                    raise ValueError("リンク・メディアの参照が不正です。")
                if node.get("type") == "img":
                    if not isinstance(node.get("url"), str) or (
                        "alt" in node and not isinstance(node["alt"], str)
                    ):
                        raise ValueError("画像の参照・代替テキストが不正です。")
                    if "caption" in node and (
                        not isinstance(node["caption"], list)
                        or any(
                            not isinstance(c, dict) or not isinstance(c.get("text"), str)
                            for c in node["caption"]
                        )
                    ):
                        raise ValueError("画像のキャプションが不正です。")
                if "text" in node:
                    if not isinstance(node["text"], str) or "children" in node:
                        raise ValueError("編集状態のテキストが不正です。")
                elif (
                    isinstance(node.get("type"), str)
                    and isinstance(node.get("children"), list)
                    and node["children"]
                ):
                    visit(node["children"], depth + 1)
                else:
                    raise ValueError("編集状態の要素が不正です。")

        if any("text" in node for node in self.value):
            raise ValueError("記事の最上位には段落などのブロックが必要です。")
        visit(self.value)
        for discussion in self.discussions:
            comments = discussion.get("comments")
            if not isinstance(comments, list):
                raise ValueError("コメントの編集状態が不正です。")
            for comment in comments:
                if not isinstance(comment, dict) or not isinstance(
                    comment.get("contentRich"), list
                ):
                    raise ValueError("コメント本文が不正です。")
                visit(comment["contentRich"])
        return self
