"""Derive the featured-image candidate from Markdown document order."""

from markdown_it import MarkdownIt

from docling_desk.knowledge.article_info import ArticleImage, ArticleInfo
from docling_desk.knowledge.catalog import split_frontmatter


def body_images(body: str):
    body, _ = split_frontmatter(body.removeprefix("\ufeff"))
    tokens = MarkdownIt("commonmark", {"html": False}).enable("table").parse(body)

    def walk(tokens):
        for token in tokens:
            if token.type == "image":
                yield token
            elif token.children:
                yield from walk(token.children)

    return list(walk(tokens))


def document_images(document):
    if not document:
        return []

    def walk(nodes):
        for node in nodes:
            if node.get("type") == "img":
                yield {
                    "url": node.get("url", ""),
                    "alt": node["alt"]
                    if "alt" in node
                    else "".join(c.get("text", "") for c in node.get("caption", [])),
                    "caption": "".join(c.get("text", "") for c in node["caption"])
                    if "caption" in node
                    else node.get("title", ""),
                }
            elif "children" in node:
                yield from walk(node["children"])

    return list(walk(document["value"]))


def with_body_image(info: ArticleInfo, body: str, document=None) -> ArticleInfo:
    images = (
        document_images(document)
        if document
        else [
            {
                "url": token.attrGet("src") or "",
                "alt": token.content,
                "caption": token.attrGet("title") or "",
            }
            for token in body_images(body)
        ]
    )
    image = ArticleImage()
    if images:
        first = images[0]
        url = first["url"]
        try:
            # Unresolvable relative imports have no usable publishing candidate.
            ArticleImage.image_url(url)
        except ValueError:
            pass
        else:
            image = ArticleImage(
                url=url,
                alt=first["alt"][:1000],
                caption=first["caption"][:2000],
                credit=info.image.credit if info.image.url == url else "",
            )
    return info.model_copy(update={"image": image})
