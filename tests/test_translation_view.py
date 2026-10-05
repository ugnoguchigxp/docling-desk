import json

import pytest
from lxml import html

from docling_desk.translation.source import source_map, template_for
from docling_desk.translation.view import apply_text


def structure(raw):
    return [(node.tag, dict(node.attrib)) for node in html.fromstring(raw).iter()]


@pytest.mark.parametrize("kind", ["slide", "sheet"])
def test_translation_changes_only_text_and_escapes_html(translation_document, kind):
    folder = translation_document(kind)
    unit = source_map(folder)["units"][0]
    raw = template_for(folder, json.loads((folder / "job.json").read_text()), 1)
    translated = apply_text(
        raw, unit, {s["id"]: '<script>alert("x")</script> Long text' for s in unit["segments"]}
    )
    assert structure(raw) == structure(translated)
    assert "&lt;script&gt;" in translated
    assert "<script>alert" not in translated
    with pytest.raises(ValueError):
        apply_text(raw, unit, {})
    broken = dict(unit, segments=[dict(unit["segments"][0], source_text="changed")])
    with pytest.raises(ValueError):
        apply_text(raw, broken, {broken["segments"][0]["id"]: "other"})
