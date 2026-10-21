import { useState } from "react";
import {
  Button,
  Dialog,
  DialogActions,
  DialogHeading,
  StatusMessage,
  TextField,
} from "../../components/ui";
import { isWikiArticle, isWikiImportFile } from "../../lib/formats";

/** Pick Markdown / CSV files (or a folder) to import. The request is the owner's. */
export function WikiImportDialog({
  open,
  busy,
  message,
  files,
  onFiles,
  onClose,
  onImport,
}: {
  open: boolean;
  busy: boolean;
  message: string;
  files: File[];
  onFiles: (files: File[]) => void;
  onClose: () => void;
  onImport: (namespace: string) => void;
}) {
  const [namespace, setNamespace] = useState("Wiki");
  return (
    <Dialog
      id="wikiImport"
      className="library-dialog"
      aria-labelledby="wikiImportTitle"
      open={open}
      busy={busy}
      onClose={onClose}
    >
      <DialogHeading
        id="wikiImportTitle"
        title="Markdown記事を取り込む"
        onClose={onClose}
        disabled={busy}
      />
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onImport(namespace);
        }}
      >
        <label>
          分類名
          <TextField
            value={namespace}
            required
            maxLength={100}
            onChange={(e) => setNamespace(e.target.value)}
          />
        </label>
        <p>
          UTF-8の .md / .markdown 記事と .csv 目次を取り込めます。
          wiki-manifest.json または pages.jsonl
          を一緒に選ぶと言語・翻訳状態も取り込みます。
          同じ分類・パスの記事は更新されます。
        </p>
        <label>
          ファイルを選択
          <input
            aria-label="WikiのMarkdownファイル"
            type="file"
            accept=".md,.markdown,.csv,.json,.jsonl"
            multiple
            onChange={(e) => onFiles([...(e.target.files || [])])}
          />
        </label>
        <label>
          フォルダーを選択
          <input
            aria-label="Wikiのフォルダー"
            type="file"
            multiple
            ref={(node) => {
              node?.setAttribute("webkitdirectory", "");
            }}
            onChange={(e) =>
              onFiles(
                [...(e.target.files || [])].filter((f) =>
                  isWikiImportFile(f.name),
                ),
              )
            }
          />
        </label>
        <p>{files.length}件を選択</p>
        <StatusMessage>{message}</StatusMessage>
        <DialogActions>
          <Button disabled={busy} onClick={onClose}>
            閉じる
          </Button>
          <Button
            type="submit"
            className="primary"
            disabled={busy || !files.some((f) => isWikiArticle(f.name))}
          >
            {busy ? "取り込み中…" : "取り込む"}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

export function WikiDeleteDialog({
  open,
  busy,
  message,
  title,
  onClose,
  onDelete,
}: {
  open: boolean;
  busy: boolean;
  message: string;
  /** The article about to be deleted, so the dialog names it. */
  title?: string;
  onClose: () => void;
  onDelete: () => void;
}) {
  return (
    <Dialog
      className="library-dialog"
      aria-labelledby="wikiDeleteTitle"
      open={open}
      busy={busy}
      onClose={onClose}
    >
      <DialogHeading
        id="wikiDeleteTitle"
        title="記事を削除"
        onClose={onClose}
        disabled={busy}
      />
      <p>
        {title ? `「${title}」` : "この記事"}をWikiと検索対象から削除します。
      </p>
      <StatusMessage>{message}</StatusMessage>
      <DialogActions>
        <Button disabled={busy} onClick={onClose}>
          閉じる
        </Button>
        <Button className="danger" disabled={busy} onClick={onDelete}>
          削除する
        </Button>
      </DialogActions>
    </Dialog>
  );
}
