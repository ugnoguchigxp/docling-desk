import type { EditorDocument } from "../editor/WikiPlateEditor";

/** The draft shape and its starting value, kept apart from the editor body so the Wiki screen can name them without loading the editor. */
export interface ArticleInfo {
  subtitle: string;
  keywords: string[];
  original_url: string;
  image: { url: string; alt: string; caption: string; credit: string };
}
export interface WikiDraft {
  id: string;
  namespace: string;
  path: string;
  title: string;
  body: string;
  language: string;
  article_info: ArticleInfo;
  etag: string | null;
  editor_document?: EditorDocument | null;
}
export function newWikiDraft(namespace = "Wiki"): WikiDraft {
  return {
    id: `wiki-${crypto.randomUUID().replaceAll("-", "")}`,
    namespace,
    path: "",
    title: "",
    body: "",
    language: "ja",
    etag: null,
    article_info: {
      subtitle: "",
      keywords: [],
      original_url: "",
      image: { url: "", alt: "", caption: "", credit: "" },
    },
  };
}
