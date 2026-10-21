import { ApiError, id, type Principal, parse } from "../contracts";
import { applyEdits } from "./patch";
import type { DocumentEditing } from "./service";

/** Viewer-resource conversion: manifest, attachment JSON and escaped HTML document view. */
export async function markdownView(
  s: DocumentEditing,
  p: Principal,
  input: {
    source_id: string;
    source_revision: string;
    evidence_revision: string;
    resource: string;
    prefix: string;
    original_range?: { start: number; end: number; sha256: string };
  },
) {
  const data = await s.body(p, input.source_id, input.source_revision);
  if (data.evidence_revision !== input.evidence_revision)
    throw new ApiError(409, "evidence_changed");
  if (input.original_range && input.original_range.sha256 !== data.sha256)
    throw new ApiError(409, "source_changed");
  if (input.resource === "manifest")
    return {
      job: {
        id: data.source_id,
        filename: "document.md",
        state: "success",
        pages: 1,
        preview: "original.md",
        slide_layout: false,
      },
      kind: "document",
      units: 1,
    };
  const attachments = s.store.db
    .query<{ attachment_id: string }, string>(
      "SELECT attachment_id FROM revision_attachments WHERE revision_id=?",
    )
    .all(data.source_revision)
    .map((row) => s.attachment(p, data.source_id, row.attachment_id));
  if (input.resource.startsWith("attachment/")) {
    const aid = parse(id, input.resource.slice("attachment/".length));
    if (!attachments.some((a) => a.attachment_id === aid))
      throw new ApiError(404, "attachment_not_found");
    const { attachment, bytes } = await s.attachmentBytes(p, data.source_id, aid);
    if (bytes.length > 1024 * 1024) throw new ApiError(413, "attachment_too_large_for_json");
    return {
      media_type: ["image/png", "image/jpeg", "image/gif", "image/webp"].includes(
        attachment.media_type,
      )
        ? attachment.media_type
        : "application/octet-stream",
      body_base64: bytes.toString("base64"),
    };
  }
  const escapeHTML = (value: string) =>
    value
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  if (input.resource.split("?")[0] !== "view/document")
    throw new ApiError(404, "viewer_resource_unavailable");
  let content = escapeHTML(data.body);
  if (input.original_range) {
    const r = input.original_range;
    applyEdits(data.body, {
      base_sha256: data.sha256,
      edits: [
        {
          start: r.start,
          end: r.end,
          expected: Buffer.from(data.body).subarray(r.start, r.end).toString("utf8"),
          replacement: "",
        },
      ],
    });
    const bytes = Buffer.from(data.body);
    content =
      escapeHTML(bytes.subarray(0, r.start).toString()) +
      '<mark id="citation">' +
      escapeHTML(bytes.subarray(r.start, r.end).toString()) +
      "</mark>" +
      escapeHTML(bytes.subarray(r.end).toString());
  }
  const attachmentHTML = attachments.length
    ? "<section><h2>添付ファイル</h2>" +
      attachments
        .map((a) => {
          const filename = escapeHTML(a.filename);
          const url = input.prefix + "attachment/" + a.attachment_id;
          if (a.size > 1024 * 1024)
            return "<p>" + filename + " — 1 MiBを超える添付はHTTP APIから取得してください。</p>";
          const image = ["image/png", "image/jpeg", "image/gif", "image/webp"].includes(
            a.media_type,
          );
          return (
            '<p><a href="' +
            url +
            '">' +
            filename +
            "</a></p>" +
            (image
              ? '<img src="' +
                url +
                '" alt="' +
                filename +
                '" style="max-width:100%;max-height:400px">'
              : "")
          );
        })
        .join("") +
      "</section>"
    : "";
  const html =
    '<!doctype html><html lang="ja"><meta charset="utf-8"><title>' +
    escapeHTML(data.metadata.title) +
    "</title><body><h1>" +
    escapeHTML(data.metadata.title) +
    '</h1><pre style="white-space:pre-wrap;overflow-wrap:anywhere">' +
    content +
    "</pre>" +
    attachmentHTML +
    "</body></html>";
  return {
    media_type: "text/html; charset=utf-8",
    body_base64: Buffer.from(html).toString("base64"),
  };
}
