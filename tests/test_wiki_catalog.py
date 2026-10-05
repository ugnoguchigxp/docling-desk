import json

from test_local_knowledge import article, search
from test_local_knowledge import client as client


def test_frontmatter_csv_links_manifest_fallback_and_preparation_exclusion(client):
    original_path, ja_path = "original/R-1.md", "ja/R-1.md"
    page = {
        "key": "requirements/R-1",
        "id": "R-1",
        "collection": "requirements",
        "original_path": original_path,
        "ja_path": ja_path,
        "title_original": "Original",
        "source_hash": "a" * 64,
        "translation_status": "untranslated",
    }
    response = client.post(
        "/api/wiki/import",
        data={"namespace": "要求"},
        files=[
            (
                "files",
                (
                    original_path,
                    b"---\ntitle: Original\n---\n# Original\n\nEvidence only\n\n[index](index.csv)",
                ),
            ),
            ("files", (ja_path, b"# Not published\nPlaceholderUnique")),
            (
                "files",
                ("original/index.csv", b"id,title,page_path\nR-1,<script>unsafe</script>,R-1.md\n"),
            ),
            ("manifest", ("pages.jsonl", json.dumps(page).encode())),
        ],
    )
    assert response.status_code == 201, response.text
    by_path = {s["path"]: s for s in response.json()["articles"]}
    original = client.get("/api/wiki/sources/" + by_path[original_path]["id"]).json()
    assert original["body"].startswith("# Original")
    assert by_path["original/index.csv"]["id"] in original["html"]
    translated = client.get("/api/wiki/sources/" + by_path[ja_path]["id"]).json()
    assert translated["fallback_original"] == by_path[original_path]["id"]
    assert "Evidence only" in translated["html"] and "PlaceholderUnique" not in translated["html"]
    assert (
        "Evidence only"
        in client.get("/api/wiki/sources/" + by_path[ja_path]["id"] + "/markdown").text
    )
    csv = client.get("/api/wiki/sources/" + by_path["original/index.csv"]["id"]).json()
    assert "<table>" in csv["html"] and "<script>" not in csv["html"]
    assert by_path[original_path]["id"] in csv["html"]
    assert search(client, "PlaceholderUnique")["output"]["results"] == []
    assert search(client, "unsafe")["output"]["results"] == []
    page["translation_status"] = "translated"
    result = client.post(
        "/api/wiki/import",
        data={"namespace": "要求"},
        files=[
            ("files", (ja_path, b"# Published\nEvidence only")),
            ("manifest", ("pages.jsonl", json.dumps(page).encode())),
        ],
    )
    assert result.status_code == 201
    assert (
        client.get("/api/wiki/sources/" + by_path[ja_path]["id"]).json()["fallback_original"]
        is None
    )
    assert len(search(client, "Evidence only")["output"]["results"]) == 1


def test_glossary_expansion_is_case_sensitive(client):
    article(
        client,
        "avg.md",
        "# AVG\n\n| Property | Value |\n|---|---|\n| Meaning | Average value |",
        "glossary",
    )
    target = article(
        client, "description.md", "# Measurement\n\nAverage value is recorded.", "requirements"
    )
    result = search(client, "AVG")
    assert target in {h["source_id"] for h in result["output"]["results"]}
    lower = search(client, "avg")
    assert not lower["output"]["results"]


def test_bad_frontmatter_and_csv_are_rejected_atomically(client):
    for name, body in (
        ("bad.md", "---\ntitle: [invalid\n---\nBody"),
        ("bad.csv", "a,a\n1,2"),
        ("bad.md", "---\ntranslation_status: nonsense\n---\nBody"),
        ("bad.md", "---\ntranslation_status: [translated]\n---\nBody"),
        ("bad.md", "---\ntranslation_status: 1\n---\nBody"),
        ("bad.md", "---\ntranslation_status:\n  state: translated\n---\nBody"),
    ):
        response = client.post(
            "/api/wiki/import",
            files=[("files", ("good.md", b"# Good")), ("files", (name, body.encode()))],
        )
        assert response.status_code == 422
    assert client.get("/api/wiki/catalog").json()["articles"] == []
    response = client.post(
        "/api/wiki/import",
        files=[
            ("files", ("good.md", b"# Good")),
            (
                "manifest",
                (
                    "wiki-manifest.json",
                    json.dumps(
                        {"articles": {"good.md": {"translation_status": ["translated"]}}}
                    ).encode(),
                ),
            ),
        ],
    )
    assert response.status_code == 422
    assert client.get("/api/wiki/catalog").json()["articles"] == []


def test_updated_frontmatter_replaces_old_state_without_manifest(client):
    sid = article(
        client,
        "ja.md",
        "---\nlanguage: ja\ntranslation_status: untranslated\n---\n# Test\nPublishedTerm",
    )
    assert search(client, "PublishedTerm")["output"]["results"] == []
    assert (
        article(
            client,
            "ja.md",
            "---\nlanguage: ja\ntranslation_status: translated\n---\n# Test\nPublishedTerm",
        )
        == sid
    )
    assert len(search(client, "PublishedTerm")["output"]["results"]) == 1


def test_links_cross_namespaces_only_inside_the_same_workspace():
    from docling_desk.knowledge.content import render

    articles = [
        {
            "id": "one",
            "namespace": "requirements",
            "path": "wiki/requirements/main.md",
            "workspace": "same",
        },
        {
            "id": "two",
            "namespace": "glossary",
            "path": "wiki/glossary/term.md",
            "workspace": "same",
        },
        {"id": "other", "namespace": "private", "path": "wiki/other.md", "workspace": "other"},
    ]
    value = render(
        "[term](../glossary/term.md) [other](../other.md)",
        "requirements",
        "wiki/requirements/main.md",
        articles,
        [],
    )
    assert "source=two" in value
    assert "source=other" not in value and 'aria-disabled="true"' in value
