import type { DocumentReference } from "../reference";

/** Uses documented Open WebUI messages; never reaches into host DOM/storage. */
export function askHost(text: string, reference: DocumentReference) {
  const prompt = `この資料の箇所について説明してください。\n資料参照: ${JSON.stringify(reference)}${text ? `\n選択した本文:\n${text.slice(0, 10000)}` : ""}`;
  window.parent.postMessage({ type: "input:prompt", text: prompt }, "*");
  return prompt;
}
export function connectHost(code: string) {
  window.parent.postMessage(
    {
      type: "input:prompt",
      text: `Docling Deskの閲覧画面に表示された接続コード ${code} を connect_document_view ツールで承認してください。`,
    },
    "*",
  );
}
export function reportHeight() {
  window.parent.postMessage(
    {
      type: "iframe:height",
      height: Math.max(600, Math.min(1000, window.innerHeight || 760)),
    },
    "*",
  );
}
