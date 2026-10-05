import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Icon } from "../components/Icons";
import {
  Button,
  Dialog,
  DialogHeading,
  StatusMessage,
  TextField,
  SelectField,
} from "../components/ui";
import { errorText, object, request } from "../lib/api";
import { isSupported, supportedAccept, supportedLabel } from "../lib/formats";
export function useUpload(
  destination: string | null,
  path: string,
  onMessage: (text: string) => void,
  enabled = true,
) {
  const cache = useQueryClient(),
    [ocrProvider, setOcrProvider] = useState("local"),
    [opened, setOpened] = useState(false),
    [busy, setBusy] = useState(false),
    [dialogMessage, setDialogMessage] = useState(""),
    [drop, setDrop] = useState(false),
    fixed = useRef({ destination, path }),
    lock = useRef(false),
    latest = useRef({ destination, path, opened });
  const ocr = useQuery({
    queryKey: ["ocr-capabilities"],
    queryFn: () => request<{ azure_available: boolean }>("/api/ocr"),
  });
  latest.current = { destination, path, opened };
  const form = useRef<HTMLFormElement>(null);
  const open = () => {
    fixed.current = { destination, path };
    setDialogMessage("");
    setOpened(true);
  };
  const send = useCallback(
    async (
      files: File[],
      target: string | null,
      fromDialog: boolean,
      rejected: string[] = [],
    ) => {
      if (lock.current) {
        onMessage(
          "アップロード中です。終了してから、もう一度ドロップしてください。",
        );
        return;
      }
      if (!files.length && !rejected.length) return;
      lock.current = true;
      setBusy(true);
      let added = 0;
      const failures = [...rejected];
      try {
        for (const [index, file] of files.entries()) {
          const status = `アップロード中… ${index + 1} / ${files.length}件`;
          if (fromDialog) setDialogMessage(status);
          else onMessage(status);
          if (!isSupported(file.name)) {
            failures.push(`${file.name}：${supportedLabel}を選んでください。`);
            continue;
          }
          if (file.size > 50 * 1024 * 1024) {
            failures.push(`${file.name}：上限50 MiBを超えています。`);
            continue;
          }
          const body = new FormData();
          body.set("file", file);
          body.set("ocr_provider", ocrProvider);
          if (target) body.set("folder_id", target);
          try {
            await request("/api/upload", { method: "POST", body });
            added++;
          } catch (e) {
            failures.push(`${file.name}：${errorText(e)}`);
          }
        }
        await cache.invalidateQueries({ queryKey: ["library"] });
        const result = [
          added
            ? `${added}件の資料を追加しました。抽出状況は資料一覧で確認できます。`
            : "資料を追加できませんでした。",
          ...failures,
        ].join("\n");
        onMessage(result);
        if (fromDialog) {
          if (failures.length) setDialogMessage(result);
          else {
            setOpened(false);
            form.current?.reset();
          }
        }
      } finally {
        lock.current = false;
        setBusy(false);
      }
    },
    [cache, onMessage, ocrProvider],
  );
  useEffect(() => {
    if (!enabled) return;
    let depth = 0,
      lastDrop = 0,
      lastDrag = 0;
    const hide = () => {
        depth = 0;
        setDrop(false);
      },
      isFile = (e: DragEvent) => !!e.dataTransfer?.types.includes("Files"),
      show = () => {
        lastDrag = Date.now();
        setDrop(true);
      };
    const enter = (e: DragEvent) => {
        if (isFile(e)) {
          e.preventDefault();
          depth++;
          show();
        }
      },
      over = (e: DragEvent) => {
        if (isFile(e)) {
          e.preventDefault();
          if (e.dataTransfer)
            e.dataTransfer.dropEffect = lock.current ? "none" : "copy";
          show();
        }
      },
      leave = (e: DragEvent) => {
        if (isFile(e)) {
          depth = Math.max(0, depth - 1);
          if (
            !depth ||
            e.clientX <= 0 ||
            e.clientY <= 0 ||
            e.clientX >= innerWidth ||
            e.clientY >= innerHeight
          )
            hide();
        }
      },
      dropped = (e: DragEvent) => {
        if (!isFile(e)) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        lastDrop = Date.now();
        hide();
        const files: File[] = [],
          rejected: string[] = [];
        const items = Array.from(e.dataTransfer?.items || []).filter(
          (i) => i.kind === "file",
        );
        if (items.length) {
          for (const i of items) {
            const entry = i.webkitGetAsEntry?.();
            if (entry?.isDirectory) {
              rejected.push(
                `${entry.name}：フォルダーではなく、ファイルをドロップしてください。`,
              );
              continue;
            }
            const file = i.getAsFile();
            if (file) files.push(file);
          }
        } else files.push(...Array.from(e.dataTransfer?.files || []));
        void send(
          files,
          latest.current.opened
            ? fixed.current.destination
            : latest.current.destination,
          latest.current.opened,
          rejected,
        );
      };
    const message = (e: MessageEvent<unknown>) => {
      const d = e.data;
      if (
        !object(d) ||
        d.type !== "docling-file-drop" ||
        !Array.from(document.querySelectorAll("iframe")).some(
          (f) => f.contentWindow === e.source,
        )
      )
        return;
      if (
        d.action === "leave" &&
        typeof d.time === "number" &&
        d.time >= lastDrag
      )
        hide();
      if (
        d.action === "enter" &&
        typeof d.time === "number" &&
        d.time > lastDrop
      )
        show();
      if (
        d.action === "drop" &&
        Array.isArray(d.files) &&
        d.files.every((f) => f instanceof File) &&
        Array.isArray(d.rejected) &&
        d.rejected.every((r) => typeof r === "string")
      ) {
        lastDrop = Date.now();
        hide();
        void send(
          d.files,
          latest.current.opened
            ? fixed.current.destination
            : latest.current.destination,
          latest.current.opened,
          d.rejected,
        );
      }
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") hide();
    };
    window.addEventListener("dragenter", enter, true);
    window.addEventListener("dragover", over, true);
    window.addEventListener("dragleave", leave, true);
    window.addEventListener("drop", dropped, true);
    window.addEventListener("message", message);
    window.addEventListener("dragend", hide, true);
    window.addEventListener("blur", hide);
    document.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("dragenter", enter, true);
      window.removeEventListener("dragover", over, true);
      window.removeEventListener("dragleave", leave, true);
      window.removeEventListener("drop", dropped, true);
      window.removeEventListener("message", message);
      window.removeEventListener("dragend", hide, true);
      window.removeEventListener("blur", hide);
      document.removeEventListener("keydown", key);
    };
  }, [send, enabled]);
  const close = () => {
    if (!lock.current) setOpened(false);
  };
  const ui = (
    <>
      <div
        id="fileDropOverlay"
        className="file-drop-overlay"
        hidden={!drop || !enabled}
      >
        <div className="file-drop-card">
          <Icon name="plus" />
          <strong id="fileDropTitle">
            {busy
              ? "アップロード中です。完了までお待ちください"
              : "ここにドロップして抽出を開始"}
          </strong>
          <p className="upload-hint">
            {ocrProvider === "azure_read"
              ? "Azure OCR：対象PDFページ全体とOfficeの埋め込み画像を外部送信します。"
              : ocrProvider === "disabled"
                ? "OCRなし"
                : "ローカルOCR"}
          </p>
          <p id="fileDropLocation">{`保存先：${opened ? fixed.current.path : path}`}</p>
          <span>
            PDF・PowerPoint・Excel・Word・Markdown・テキスト · 複数ファイル対応
          </span>
        </div>
      </div>
      <Dialog
        id="uploadDialog"
        aria-labelledby="uploadTitle"
        open={opened}
        onClose={close}
        busy={busy}
      >
        <form
          id="upload"
          ref={form}
          onSubmit={(e) => {
            e.preventDefault();
            const field = form.current?.elements.namedItem("file");
            if (field instanceof HTMLInputElement)
              void send(
                Array.from(field.files || []),
                fixed.current.destination,
                true,
              );
          }}
        >
          <DialogHeading
            id="uploadTitle"
            title="資料を追加"
            onClose={close}
            disabled={busy}
          />
          <p id="uploadLocation" className="upload-hint">
            {`保存先：${fixed.current.path}`}
          </p>
          <label className="picker">
            PDF・PowerPoint・Excel・Word・Markdown・テキスト
            <TextField
              type="file"
              name="file"
              accept={supportedAccept}
              multiple
              required
            />
          </label>
          <p className="upload-hint">
            1ファイル50 MiBまで · PDFは最大100ページ · 待機込み3件まで
          </p>
          <label className="upload-hint">
            画像から文字を読み取る方法
            <SelectField
              aria-label="画像から文字を読み取る方法"
              value={ocrProvider}
              disabled={busy}
              onChange={(e) => setOcrProvider(e.target.value)}
            >
              <option value="local">ローカル OCR</option>
              <option value="azure_read" disabled={!ocr.data?.azure_available}>
                Azure OCR
              </option>
              <option value="disabled">OCRを使わない</option>
            </SelectField>
          </label>
          <p className="upload-hint">
            {ocrProvider === "azure_read"
              ? "PDFは対象ページ全体、Officeは埋め込み画像全体をAzureへ送ります。画像内の文字をRAGへ含めます。"
              : ocrProvider === "local"
                ? "画像内の文字をこの環境で読み取り、RAGへ含めます。"
                : "画像内の文字はRAGへ含めません。"}
          </p>
          <StatusMessage id="uploadMessage" hidden={!dialogMessage}>
            {dialogMessage}
          </StatusMessage>
          <Button className="primary" type="submit" disabled={busy}>
            抽出を開始
          </Button>
        </form>
      </Dialog>
    </>
  );
  return { open, ui };
}
