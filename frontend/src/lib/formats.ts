export const supportedAccept =
  ".pdf,.pptx,.xlsx,.docx,.md,.markdown,.txt,.text,.png,.jpg,.jpeg,.gif,.webp";
export const supportedLabel = "PDF / PPTX / XLSX / DOCX / Markdown / テキスト / 画像";
export const isDocument = (name: string) =>
  /\.(docx|md|markdown|txt|text)$/i.test(name);
export const isSupported = (name: string) =>
  /\.(pdf|pptx|xlsx|docx|md|markdown|txt|text|png|jpg|jpeg|gif|webp)$/i.test(name);
export function matchesFormat(name: string, format: string) {
  if (format === "image") return /\.(png|jpg|jpeg|gif|webp)$/i.test(name);
  if (format === "jpg") return /\.(jpg|jpeg)$/i.test(name);
  if (format === "md") return /\.(md|markdown)$/i.test(name);
  if (format === "txt") return /\.(txt|text)$/i.test(name);
  return name.toLowerCase().endsWith("." + format);
}
