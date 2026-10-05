"""Shared provenance contracts without OCR transport or RAG dependencies."""

from docling_core.types.doc import ContentLayer, DoclingDocument

OCR_META = "docling_ocr__evidence"


def evidence_for(item) -> dict | None:
    if item.meta:
        return (item.meta.model_extra or {}).get(OCR_META)
    return None


def picture_refs(doc: DoclingDocument) -> set[str]:
    """Exclude pictures, their subtrees, and attached captions/footnotes in every RAG view."""
    refs: set[str] = set()
    for picture in doc.pictures:
        refs.update(
            item.self_ref
            for item, _ in doc.iterate_items(
                root=picture,
                with_groups=True,
                traverse_pictures=True,
                included_content_layers=set(ContentLayer),
            )
        )
        refs.update(r.cref for r in [*picture.captions, *picture.footnotes])
    return refs
