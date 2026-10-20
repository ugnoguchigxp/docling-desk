export const supportedAccept =
  ".pdf,.pptx,.xlsx,.docx,.md,.markdown,.txt,.text,.tex,.latex,.png,.jpg,.jpeg,.gif,.webp,.drawio,.dio,.xml,.svg,.html,.htm";
export const supportedLabel =
  "PDF / PPTX / XLSX / DOCX / Markdown / テキスト / TeX / 画像 / draw.io";
export const isDocument = (name: string) =>
  /\.(docx|md|markdown|txt|text|tex|latex)$/i.test(name);
export const isSupported = (name: string) =>
  /\.(pdf|pptx|xlsx|docx|md|markdown|txt|text|tex|latex|png|jpg|jpeg|gif|webp|drawio|dio|xml|svg|html|htm)$/i.test(
    name,
  );
export function matchesFormat(name: string, format: string) {
  if (format === "drawio")
    return (
      /\.(drawio|dio|xml|svg|html|htm)$/i.test(name) ||
      name.toLowerCase().endsWith(".drawio.png")
    );
  if (format === "image") return /\.(png|jpg|jpeg|gif|webp)$/i.test(name);
  if (format === "jpg") return /\.(jpg|jpeg)$/i.test(name);
  if (format === "md") return /\.(md|markdown)$/i.test(name);
  if (format === "tex") return isTex(name);
  if (format === "txt") return /\.(txt|text)$/i.test(name);
  return name.toLowerCase().endsWith("." + format);
}

export const isTex = (name: string) => /\.(tex|latex)$/i.test(name);
export const isSource = (name: string) => /\.(txt|text|tex|latex)$/i.test(name);
