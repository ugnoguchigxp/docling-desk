import { useCallback, useEffect, useRef, useState } from "react";
import {
  Button,
  Dialog,
  DialogActions,
  DialogHeading,
  StatusMessage,
  TextField,
} from "../components/ui";
import { sourceEdit } from "../editor/source-edit";
import { errorText, request } from "../lib/api";
import { withBase } from "../lib/base";
import {
  firstMarkdownImage,
  isWikiImage,
  firstPlateImage,
} from "../editor/raw-markdown";
import { WikiPlate } from "../editor/WikiPlate";
import type { EditorDocument } from "../editor/WikiPlateEditor";
import type { ArticleInfo, WikiDraft } from "./wiki-draft";
import "./wiki-editor.css";

export { newWikiDraft } from "./wiki-draft";
export type { ArticleInfo, WikiDraft } from "./wiki-draft";

export function WikiEditor({
  initial,
  namespaces,
  onSaved,
  onClose,
}: {
  initial: WikiDraft;
  namespaces: string[];
  onSaved: (article: WikiDraft) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(initial);
  const [keyword, setKeyword] = useState("");
  const [mode, setMode] = useState<"rich" | "source" | "preview">("rich");
  const [generation, setGeneration] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editorBusy, setEditorBusy] = useState(false);
  const editorChange = useCallback(
    (body: string, editor_document: EditorDocument) => {
      setDraft((current) => ({ ...current, body, editor_document }));
    },
    [],
  );
  const [discard, setDiscard] = useState(false);
  const lock = useRef(false);
  const dirty =
    JSON.stringify(draft) !== JSON.stringify(initial) || !!keyword.trim();
  const firstImage = draft.editor_document
    ? firstPlateImage(draft.editor_document.value)
    : firstMarkdownImage(draft.body);
  const image =
    firstImage &&
    (isWikiImage(firstImage.url) || /^https?:\/\//i.test(firstImage.url))
      ? {
          ...firstImage,
          alt: firstImage.alt.slice(0, 1000),
          caption: firstImage.caption.slice(0, 2000),
          credit:
            draft.article_info.image.url === firstImage.url
              ? draft.article_info.image.credit
              : "",
        }
      : { url: "", alt: "", caption: "", credit: "" };
  const info = { ...draft.article_info, image };
  const update = (value: Partial<WikiDraft>) =>
    setDraft((current) => ({ ...current, ...value }));
  const updateInfo = (value: Partial<ArticleInfo>) =>
    update({ article_info: { ...info, ...value } });
  useEffect(() => {
    const before = (event: BeforeUnloadEvent) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", before);
    return () => window.removeEventListener("beforeunload", before);
  }, [dirty]);
  const addKeyword = () => {
    if (!keyword.trim()) return;
    updateInfo({ keywords: [...new Set([...info.keywords, keyword.trim()])] });
    setKeyword("");
  };
  async function save() {
    if (lock.current || editorBusy) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      const saved = await request<WikiDraft>(
        `/api/wiki/sources/${encodeURIComponent(draft.id)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...draft,
            id: undefined,
            article_info: {
              ...info,
              keywords: [
                ...new Set([
                  ...info.keywords,
                  ...(keyword.trim() ? [keyword.trim()] : []),
                ]),
              ],
            },
          }),
        },
      );
      onSaved(saved);
    } catch (e) {
      setError(errorText(e));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return (
    <form
      className="wiki-editor"
      aria-label="Wiki記事の編集"
      onSubmit={(e) => {
        e.preventDefault();
        if (
          (e.nativeEvent as SubmitEvent).submitter?.getAttribute(
            "data-wiki-save",
          ) === "true"
        )
          void save();
      }}
    >
      <header className="wiki-editor-bar">
        <Button
          disabled={busy || editorBusy}
          onClick={() => (dirty ? setDiscard(true) : onClose())}
        >
          ← 閲覧に戻る
        </Button>
        <span>
          {draft.namespace} / {draft.path || "新しい記事"}
        </span>
        <small>
          {dirty
            ? "未保存の変更があります"
            : initial.etag
              ? "保存済み"
              : "新しい記事 · 未保存"}
        </small>
        <Button
          disabled={busy || editorBusy}
          aria-pressed={mode === "preview"}
          onClick={() => setMode(mode === "preview" ? "rich" : "preview")}
        >
          プレビュー
        </Button>
        <Button
          className="primary"
          type="submit"
          data-wiki-save="true"
          disabled={busy || editorBusy}
        >
          {busy ? "保存中…" : "Wikiに保存"}
        </Button>
        <Button disabled title="ブログ投稿は今後対応予定です">
          投稿設定 · 今後対応
        </Button>
      </header>
      <StatusMessage className="wiki-editor-error">{error}</StatusMessage>
      <div className="wiki-editor-layout">
        <section className="wiki-writing" aria-label="記事の原稿">
          <fieldset disabled={busy}>
            <div className="wiki-editor-tabs" aria-label="編集方法">
              <Button
                disabled={editorBusy}
                aria-pressed={mode === "rich"}
                onClick={() => {
                  setMode("rich");
                  setGeneration((v) => v + 1);
                }}
              >
                見たまま
              </Button>
              <Button
                disabled={editorBusy}
                aria-pressed={mode === "source"}
                onClick={() => setMode("source")}
              >
                Markdown
              </Button>
            </div>
            <TextField
              className="wiki-title-input"
              aria-label="記事タイトル"
              placeholder="記事のタイトル"
              required
              maxLength={200}
              value={draft.title}
              onChange={(e) => update({ title: e.target.value })}
            />
            <TextField
              className="wiki-subtitle-input"
              aria-label="概要・副題"
              placeholder="概要・副題（任意）"
              maxLength={1000}
              value={info.subtitle}
              onChange={(e) => updateInfo({ subtitle: e.target.value })}
            />
            {mode === "source" ? (
              <textarea
                className="wiki-source-editor"
                aria-label="Markdownソース"
                value={draft.body}
                onChange={(e) =>
                  update({
                    body: sourceEdit(draft.body, e.target.value),
                    editor_document: null,
                  })
                }
              />
            ) : (
              <WikiPlate
                key={`${generation}:${mode === "preview"}`}
                initial={draft.body}
                onChange={editorChange}
                document={draft.editor_document}
                onBusy={setEditorBusy}
                preview={mode === "preview"}
                readOnly={busy || mode === "preview"}
              />
            )}
          </fieldset>
          <footer>
            <span>
              Markdown原文を保持
              {mode === "preview"
                ? " · 編集プレビュー（保護ブロックは原文表示）"
                : ""}
            </span>
            <span>{[...draft.body].length.toLocaleString()}文字</span>
          </footer>
        </section>
        <aside className="wiki-article-info" aria-label="記事情報">
          <h2>記事情報</h2>
          <p>共通の候補。将来、投稿先で調整できます。</p>
          <fieldset disabled={busy || editorBusy}>
            <label>キーワード</label>
            <div className="wiki-keywords">
              {info.keywords.map((word) => (
                <Button
                  key={word}
                  aria-label={`${word}を削除`}
                  onClick={() =>
                    updateInfo({
                      keywords: info.keywords.filter((w) => w !== word),
                    })
                  }
                >
                  {word} ×
                </Button>
              ))}
            </div>
            <div className="wiki-keyword-entry">
              <TextField
                aria-label="キーワードを追加"
                placeholder="キーワードを追加"
                value={keyword}
                maxLength={100}
                onChange={(e) => setKeyword(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    addKeyword();
                  }
                }}
              />
              <Button
                onClick={addKeyword}
                disabled={!keyword.trim() || info.keywords.length >= 20}
              >
                追加
              </Button>
            </div>
            <label>
              言語
              <TextField
                aria-label="記事の言語"
                value={draft.language}
                maxLength={32}
                required
                placeholder="ja"
                onChange={(e) => update({ language: e.target.value })}
              />
            </label>
            <h3>代表画像</h3>
            <p>
              本文の最初の画像を使用します。本文ツールバーの画像ボタンから挿入できます。
            </p>
            {isWikiImage(image.url) && (
              <img
                className="wiki-cover-preview"
                src={withBase(image.url)}
                alt={image.alt}
              />
            )}
            {image.url ? (
              <>
                <p>{image.alt || "代替テキスト未設定"}</p>
                <label>
                  画像の出典
                  <TextField
                    value={image.credit}
                    maxLength={2000}
                    onChange={(e) =>
                      updateInfo({
                        image: { ...image, credit: e.target.value },
                      })
                    }
                  />
                </label>
              </>
            ) : (
              <p>
                {firstImage
                  ? "本文の最初の画像は参照できません。本文から画像ファイルを追加してください。"
                  : "本文に画像はありません。"}
              </p>
            )}
            <label>
              元記事の公開URL（任意）
              <TextField
                aria-label="元記事の公開URL"
                type="url"
                placeholder="https://…"
                maxLength={2048}
                value={info.original_url}
                onChange={(e) => updateInfo({ original_url: e.target.value })}
              />
            </label>
            <p>対応する投稿先でcanonicalの候補に使用します。</p>
            <h3>Wikiの配置</h3>
            <label>
              分類
              <TextField
                required
                list="wiki-editor-namespaces"
                maxLength={100}
                value={draft.namespace}
                readOnly={!!initial.etag}
                onChange={(e) => update({ namespace: e.target.value })}
              />
            </label>
            <datalist id="wiki-editor-namespaces">
              {namespaces.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
            <label>
              記事パス
              <TextField
                required
                placeholder="writing/new-article.md"
                maxLength={512}
                value={draft.path}
                readOnly={!!initial.etag}
                onChange={(e) => update({ path: e.target.value })}
              />
            </label>
          </fieldset>
          <div className="wiki-future-posting">
            <h3>将来の投稿先</h3>
            <p>Zenn · Blogger · Qiita · Medium</p>
            <p>
              ここでは記事情報を保存します。外部への投稿は今後対応予定です。
            </p>
          </div>
        </aside>
      </div>
      <Dialog
        open={discard}
        onClose={() => setDiscard(false)}
        aria-labelledby="wikiDiscardTitle"
      >
        <DialogHeading
          id="wikiDiscardTitle"
          title="編集を閉じる"
          onClose={() => setDiscard(false)}
        />
        <p>保存していない内容を破棄して、閲覧に戻りますか？</p>
        <DialogActions>
          <Button onClick={() => setDiscard(false)}>編集を続ける</Button>
          <Button className="danger" onClick={onClose}>
            破棄して戻る
          </Button>
        </DialogActions>
      </Dialog>
    </form>
  );
}
