"""Format-aware context parents and bounded retrieval children, with exact source refs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from docling_core.transforms.chunker.doc_chunk import DocChunk
from docling_core.transforms.chunker.hierarchical_chunker import (
    ChunkingDocSerializer,
    ChunkingSerializerProvider,
    HierarchicalChunker,
)
from docling_core.types.doc import (
    DocItem,
    DoclingDocument,
    PictureItem,
    TableItem,
    TextItem,
)
from pydantic import BaseModel, Field

from docling_desk.documents.evidence import OCR_META, evidence_for, picture_refs
from docling_desk.documents.pagination import sheet_names
from docling_desk.documents.tables import TableView, project_tables

RagKind = Literal["slide", "sheet", "section", "table_rows", "text_part", "unlocated", "docling"]

DEFAULT_TARGET_CHARS = 2000  # A demo character budget, not a tokenizer/model limit.
DEFAULT_TOLERANCE_CHARS = 500


class RagRelation(BaseModel):
    ref: str
    kind: Literal["table"]
    captions: list[str] = Field(default_factory=list)
    header_rows: int = 0
    row_range: list[int] = Field(default_factory=list)


class RagChunk(BaseModel):
    id: str
    source: str
    source_sha256: str
    text: str
    headings: list[str] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)
    pages: list[int] = Field(default_factory=list)
    provenance: list[dict[str, object]] = Field(default_factory=list)
    kind: RagKind = "docling"
    unit: str = ""
    parent_id: str | None = None
    context_refs: list[str] = Field(default_factory=list)
    row_range: list[int] = Field(default_factory=list)
    relations: list[RagRelation] = Field(default_factory=list)
    ocr_evidence: list[dict[str, object]] = Field(default_factory=list)
    text_chars: int = 0
    oversize: bool = False


class RagPolicy(BaseModel):
    version: str = "context-v4"
    image_content: Literal["excluded", "ocr_text"] = "excluded"
    embedding_text_field: Literal["text"] = "text"
    strategy: str
    target_chars: int
    tolerance_chars: int = 0
    split_threshold_chars: int
    context_chunks: int
    search_chunks: int
    docling_chunks: int
    oversized_contexts: int
    oversized_search_chunks: int
    retrieval: str = "Search rag-index.jsonl; expand its parent_id from rag.jsonl before interpreting the result."
    limit_note: str = "文字数は暫定の目安です。埋め込みモデルのトークン上限は未接続のため検証していません。長い単独行・段落を黙って切り捨てません。"


@dataclass
class Block:
    text: str
    items: list[DocItem]
    table: TableView | None = None
    caption: str = ""


def unique_items(items: list[DocItem]) -> list[DocItem]:
    return list({item.self_ref: item for item in items}.values())


def chunk_record(
    *,
    id: str,
    filename: str,
    checksum: str,
    text: str,
    items: list[DocItem],
    target: int,
    kind: RagKind = "docling",
    unit: str = "",
    headings: list[str] | None = None,
    parent_id: str | None = None,
    context_refs: list[str] | None = None,
    row_range: list[int] | None = None,
) -> RagChunk:
    items = unique_items(items)
    relations = [
        RagRelation(
            ref=i.self_ref,
            kind="table",
            captions=[r.cref for r in i.captions],
        )
        for i in items
        if isinstance(i, TableItem)
    ]
    return RagChunk(
        id=id,
        source=filename,
        source_sha256=checksum,
        text=text,
        refs=[i.self_ref for i in items],
        pages=sorted({p.page_no for i in items for p in i.prov}),
        provenance=[p.model_dump(mode="json") for i in items for p in i.prov],
        relations=relations,
        ocr_evidence=[
            {"text_ref": i.self_ref, **evidence} for i in items if (evidence := evidence_for(i))
        ],
        text_chars=len(text),
        oversize=len(text) > target,
        kind=kind,
        unit=unit,
        headings=headings or [],
        parent_id=parent_id,
        context_refs=context_refs or [],
        row_range=row_range or [],
    )


class TextTableSerializer(ChunkingDocSerializer):
    excluded_picture_refs: set[str] = Field(default_factory=set)

    def get_excluded_refs(self, **kwargs: Any) -> set[str]:
        return super().get_excluded_refs(**kwargs) | self.excluded_picture_refs


class TextTableSerializerProvider(ChunkingSerializerProvider):
    def get_serializer(self, doc: DoclingDocument) -> TextTableSerializer:
        serializer = TextTableSerializer(doc=doc, excluded_picture_refs=picture_refs(doc))
        serializer.params.blocked_meta_names.add(OCR_META)
        return serializer


def native_chunks(
    doc: DoclingDocument, filename: str, checksum: str, job_id: str, target: int
) -> list[RagChunk]:
    result = []
    chunker = HierarchicalChunker(serializer_provider=TextTableSerializerProvider())
    for index, chunk in enumerate(chunker.chunk(doc)):
        if not isinstance(chunk, DocChunk):
            raise TypeError("Expected a DocChunk from HierarchicalChunker")
        result.append(
            chunk_record(
                id=f"{job_id}:docling:{index}",
                filename=filename,
                checksum=checksum,
                text=chunk.text,
                items=chunk.meta.doc_items,
                target=target,
                headings=chunk.meta.headings or [],
            )
        )
    represented = {ref for c in result for ref in c.refs}
    # Images nested in table cells may be absent from the table serializer. Preserve their OCR.
    for item, _ in doc.iterate_items(traverse_pictures=True):
        if isinstance(item, TextItem) and evidence_for(item) and item.self_ref not in represented:
            result.append(
                chunk_record(
                    id=f"{job_id}:docling:ocr:{len(result)}",
                    filename=filename,
                    checksum=checksum,
                    text=item.text,
                    items=[item],
                    target=target,
                )
            )
    return result


def source_blocks(doc: DoclingDocument, filename: str) -> dict[int | None, list[Block]]:
    result: dict[int | None, list[Block]] = {number: [] for number in sorted(doc.pages)}
    views = {t.ref: t for t in project_tables(doc, filename)}
    excluded = picture_refs(doc)
    for item, _ in doc.iterate_items():
        if not isinstance(item, (TextItem, TableItem)) or item.self_ref in excluded:
            continue
        ancestor = item.parent.resolve(doc) if item.parent else None
        contained = False
        while ancestor is not None:
            if isinstance(ancestor, (TableItem, PictureItem)):
                contained = True
                break
            ancestor = ancestor.parent.resolve(doc) if ancestor.parent else None
        if contained and not evidence_for(item):
            continue
        page = item.prov[0].page_no if item.prov else None
        if page not in doc.pages:
            page = None
        items: list[DocItem] = [item]
        if isinstance(item, TableItem):
            for child, _ in doc.iterate_items(root=item):
                if isinstance(child, DocItem) and child.self_ref not in excluded:
                    items.append(child)
            for ref in item.captions:
                caption = ref.resolve(doc)
                if isinstance(caption, DocItem) and caption.self_ref not in excluded:
                    items.append(caption)
        if isinstance(item, TextItem):
            text = item.text
        else:
            view = views[item.self_ref]
            text = table_heading(view, item.caption_text(doc)) + "\n".join(
                row_line(row) for row in view.rows[view.header_rows :]
            )
        result.setdefault(page, []).append(
            Block(
                text,
                unique_items(items),
                views.get(item.self_ref),
                item.caption_text(doc) if isinstance(item, TableItem) else "",
            )
        )
    return result


def escaped_cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


def row_line(row: list[str]) -> str:
    return "\t".join(escaped_cell(value) for value in row)


def table_heading(table: TableView, caption: str = "") -> str:
    names = [
        " / ".join(table.rows[r][c] for r in range(table.header_rows) if table.rows[r][c])
        for c in range(table.columns)
    ]
    return (caption + "\n" if caption else "") + (
        "\t".join(escaped_cell(n) for n in names) + "\n" if any(names) else ""
    )


def table_parts(
    block: Block, prefix: str, target: int, tolerance: int = 0
) -> list[tuple[str, list[int]]]:
    """Repeat actual column headings; keep physical row ranges in metadata only."""
    table = block.table
    if table is None:
        return [(block.text, [])]
    headers = table.header_rows
    heading = table_heading(table, block.caption)
    if headers == len(table.rows):
        return [(block.text, [])]
    result = []
    current: list[str] = []
    start = headers + 1
    for row_no, row in enumerate(table.rows[headers:], start=headers + 1):
        line = row_line(row)
        if current and len(prefix + heading + "\n".join([*current, line])) > target:
            result.append((heading + "\n".join(current), [start, row_no - 1]))
            current, start = [], row_no
        current.append(line)
    if current:
        result.append((heading + "\n".join(current), [start, len(table.rows)]))
    if len(result) > 1 and len(result[-1][0]) - len(heading) <= tolerance:
        previous, previous_rows = result[-2]
        tail, tail_rows = result[-1]
        merged = previous + "\n" + tail[len(heading) :]
        if len(prefix + merged) <= target + tolerance:
            result[-2:] = [(merged, [previous_rows[0], tail_rows[1]])]
    return result


def build_context_chunks(
    doc: DoclingDocument,
    filename: str,
    checksum: str,
    job_id: str,
    target: int | None = None,
    tolerance: int | None = None,
) -> tuple[list[RagChunk], list[RagChunk], list[RagChunk], RagPolicy]:
    suffix = Path(filename).suffix.lower()
    # The new sheet policy leaves the existing slide/PDF budgets unchanged.
    if target is None:
        target = DEFAULT_TARGET_CHARS if suffix == ".xlsx" else 4000
    if tolerance is None:
        tolerance = DEFAULT_TOLERANCE_CHARS if suffix == ".xlsx" else 0
    if target < 256:
        raise ValueError("The character budget must be at least 256.")
    if tolerance < 0:
        raise ValueError("The character tolerance must not be negative.")
    threshold = target + tolerance
    native = native_chunks(doc, filename, checksum, job_id, target)
    parents: list[RagChunk] = []
    children: list[RagChunk] = []
    if suffix not in {".pptx", ".xlsx"}:
        parents = [
            c.model_copy(
                update={
                    "id": f"{job_id}:{i}",
                    "kind": "section",
                    "unit": " › ".join(c.headings) or f"本文区分 {i + 1}",
                }
            )
            for i, c in enumerate(native)
        ]
        # PDF's previous native section boundaries are retained; oversized ones are explicit.
        children = [
            c.model_copy(update={"id": c.id + ":search", "parent_id": c.id}) for c in parents
        ]
        strategy = "Docling hierarchical sections (PDF unchanged)"
    else:
        names = sheet_names(doc) if suffix == ".xlsx" else {}
        for page, blocks in source_blocks(doc, filename).items():
            if not blocks:
                continue  # A picture-only or empty unit has no searchable text/table content.
            kind = "unlocated" if page is None else "slide" if suffix == ".pptx" else "sheet"
            unit = (
                "位置未取得の要素"
                if page is None
                else f"スライド {page}"
                if suffix == ".pptx"
                else f"シート {page}" + (f" · {names[page]}" if page in names else "")
            )
            title = next(
                (
                    b.text.splitlines()[0]
                    for b in blocks
                    if b.table is None and isinstance(b.items[0], TextItem)
                ),
                "",
            )
            headings = [names[page]] if page in names else [title] if title else []
            all_items = [item for b in blocks for item in b.items]
            items_by_ref = {item.self_ref: item for item in all_items}
            text = "\n\n".join([unit, *[b.text for b in blocks]])
            parent = chunk_record(
                id=f"{job_id}:{kind}:{page if page is not None else 'unknown'}",
                filename=filename,
                checksum=checksum,
                text=text,
                items=all_items,
                target=threshold,
                kind=kind,
                unit=unit,
                headings=headings,
            )
            if page is not None:
                parent.pages = [page]
            parents.append(parent)
            if len(text) <= threshold:
                children.append(
                    parent.model_copy(
                        update={"id": parent.id + ":search:0", "parent_id": parent.id}
                    )
                )
                continue
            # Small prose/one-column title tables provide local context to every split.
            context_blocks = [
                b
                for b in blocks
                if (b.table is None and isinstance(b.items[0], TextItem) and len(b.text) <= 500)
                or (
                    b.table is not None
                    and len(b.table.rows) <= 3
                    and b.table.columns <= 3
                    and len(b.text) <= 500
                )
            ]
            context = "\n".join(b.text for b in context_blocks)
            if len(context) > 700:
                # Repeat only short whole blocks; an atomic long title must not fill every chunk.
                context_blocks = [b for b in context_blocks if b.text.splitlines()[0] == title]
                context = "\n".join(b.text for b in context_blocks)
            prefix = (
                "\n".join([unit, *[h for h in headings if len(h) <= 500], context]).rstrip()
                + "\n\n"
            )
            context_refs = [i.self_ref for b in context_blocks for i in b.items]
            packed: list[Block] = []

            def append_child(
                content: str,
                items: list[DocItem],
                child_kind: RagKind,
                row_range: list[int] | None = None,
            ) -> None:
                child = chunk_record(
                    id=f"{parent.id}:search:{sum(c.parent_id == parent.id for c in children)}",
                    filename=filename,
                    checksum=checksum,
                    text=prefix + content,
                    items=items,
                    target=threshold,
                    kind=child_kind,
                    unit=unit,
                    headings=headings,
                    parent_id=parent.id,
                    context_refs=context_refs,
                    row_range=row_range or [],
                )
                child.pages = list(parent.pages)
                children.append(child)

            def flush(*, merge_tail: bool = False) -> None:
                if packed:
                    content = "\n\n".join(b.text for b in packed)
                    items = [i for b in packed for i in b.items]
                    if (
                        merge_tail
                        and children
                        and children[-1].parent_id == parent.id
                        and children[-1].kind == "text_part"
                        and len(content) <= tolerance
                        and len(children[-1].text + "\n\n" + content) <= threshold
                    ):
                        previous = children.pop()
                        content = previous.text[len(prefix) :] + "\n\n" + content
                        items = [*[items_by_ref[ref] for ref in previous.refs], *items]
                    append_child(
                        content,
                        items,
                        "text_part",
                    )
                    packed.clear()

            retrieval_blocks = []
            for block in blocks:
                if (
                    suffix == ".xlsx"
                    and block.table is None
                    and isinstance(block.items[0], TextItem)
                    and len(prefix + block.text) > threshold
                ):
                    retrieval_blocks.extend(
                        Block(paragraph, block.items) for paragraph in block.text.split("\n\n")
                    )
                else:
                    retrieval_blocks.append(block)
            for block in retrieval_blocks:
                if block.table is not None and len(prefix + block.text) > threshold:
                    flush()
                    for part, rows in table_parts(block, prefix, target, tolerance):
                        append_child(part, block.items, "table_rows", rows)
                else:
                    if (
                        packed
                        and len(prefix + "\n\n".join([*(b.text for b in packed), block.text]))
                        > target
                    ):
                        flush()
                    packed.append(block)
            flush(merge_tail=True)
        strategy = (
            "1 slide per context"
            if suffix == ".pptx"
            else "1 sheet per context; bounded table-row retrieval children"
        )
    # Source metadata stays outside the text used for embeddings, including unsplit tables.
    views = {table.ref: table for table in project_tables(doc, filename)}
    for chunk in [*parents, *children, *native]:
        for relation in chunk.relations:
            view = views[relation.ref]
            relation.header_rows = view.header_rows
            relation.row_range = (
                list(chunk.row_range)
                if chunk.kind == "table_rows" and chunk.row_range
                else [view.header_rows + 1, len(view.rows)]
                if view.header_rows < len(view.rows)
                else []
            )
    policy = RagPolicy(
        version="context-v5-ocr" if any(c.ocr_evidence for c in parents) else "context-v4",
        image_content="ocr_text" if any(c.ocr_evidence for c in parents) else "excluded",
        strategy=strategy,
        target_chars=target,
        tolerance_chars=tolerance,
        split_threshold_chars=threshold,
        context_chunks=len(parents),
        search_chunks=len(children),
        docling_chunks=len(native),
        oversized_contexts=sum(c.oversize for c in parents),
        oversized_search_chunks=sum(c.oversize for c in children),
    )
    return parents, children, native, policy


def export_rag(
    doc: DoclingDocument, filename: str, job_id: str, source: Path, folder: Path
) -> RagPolicy:
    with source.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    parents, children, native, policy = build_context_chunks(doc, filename, checksum, job_id)
    for name, chunks in [
        ("rag.jsonl", parents),
        ("rag-index.jsonl", children),
        ("rag-docling.jsonl", native),
    ]:
        temporary = folder / (name + ".tmp")
        temporary.write_text("".join(c.model_dump_json() + "\n" for c in chunks), encoding="utf-8")
        temporary.replace(folder / name)
    (folder / "rag-policy.json").write_text(policy.model_dump_json(indent=2), encoding="utf-8")
    return policy
