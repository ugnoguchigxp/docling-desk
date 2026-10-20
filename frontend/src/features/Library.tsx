import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type HTMLAttributes,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Icon } from "../components/Icons";
import {
  ActionLink,
  Breadcrumbs,
  Button,
  CheckboxField,
  Dialog,
  DialogActions,
  DialogHeading,
  DisclosureMenu,
  SelectField,
  StatusMessage,
  TextField,
  Toolbar,
} from "../components/ui";
import { errorText, originalUrl, post } from "../lib/api";
import { withBase } from "../lib/base";
import { thumbnailUrl } from "../lib/media";
import "./media-library.css";
import { isDocument, matchesFormat } from "../lib/formats";
import {
  isDone,
  itemKey,
  itemName,
  labels,
  type Folder,
  type Item,
  type ItemRef,
  type Library as Snapshot,
  type Job,
} from "../lib/types";
export function chain(folders: Folder[], id: string | null) {
  const result: Folder[] = [],
    seen = new Set<string>();
  while (id && !seen.has(id)) {
    seen.add(id);
    const f = folders.find((f) => f.id === id);
    if (!f) break;
    result.unshift(f);
    id = f.parent_id;
  }
  return result;
}
export function folderPath(folders: Folder[], id: string | null) {
  return ["資料一覧", ...chain(folders, id).map((f) => f.name)].join(" / ");
}
function Thumbnail({ job }: { job: Job }) {
  const [failed, setFailed] = useState(false);
  const source = thumbnailUrl(job);
  return source && !failed ? (
    <img src={source} alt="" loading="lazy" onError={() => setFailed(true)} />
  ) : (
    <span className="gallery-file-kind">
      {(job.original_filename || job.filename).split(".").pop()?.toUpperCase()}
    </span>
  );
}
type Operation = "create" | "rename" | "move" | "copy";
interface Pending {
  action: Operation;
  items: Item[];
  parent: string | null;
}
export function Library({
  data,
  folder,
  onFolder,
  onJob,
  onUpload,
  message,
  setMessage,
  hidden,
  navigation,
  scope: requestedScope,
  layout: requestedLayout,
  onScope,
  onLayout,
}: {
  data: Snapshot;
  folder: string | null;
  onFolder: (id: string | null) => void;
  onJob: (job: Job) => void;
  onUpload: () => void;
  message: string;
  setMessage: (text: string) => void;
  hidden: boolean;
  navigation?: ReactNode;
  scope?: "folder" | "images";
  layout?: "list" | "gallery";
  onScope?: (scope: "folder" | "images") => void;
  onLayout?: (layout: "list" | "gallery") => void;
}) {
  const cache = useQueryClient(),
    [query, setQuery] = useState(""),
    [type, setType] = useState(""),
    [sort, setSort] = useState("recent"),
    [checked, setChecked] = useState(new Set<string>()),
    [pending, setPending] = useState<Pending | null>(null),
    [destination, setDestination] = useState<string | null>(null),
    [name, setName] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [localScope, setLocalScope] = useState<"folder" | "images">("folder"),
    [preferences, setPreferences] = useState<
      Record<string, "list" | "gallery">
    >(() => {
      try {
        const saved: unknown = JSON.parse(
          localStorage.getItem("docling-library-views") || "{}",
        );
        const result: Record<string, "list" | "gallery"> = {};
        if (saved && typeof saved === "object" && !Array.isArray(saved)) {
          for (const [key, value] of Object.entries(saved)) {
            if (value === "list" || value === "gallery") result[key] = value;
          }
        }
        return result;
      } catch {
        return {};
      }
    });
  const scope = requestedScope || localScope;
  const preferenceKey = `${scope}:${folder || "root"}`;
  const layout =
    requestedLayout ||
    preferences[preferenceKey] ||
    (scope === "images" ? "gallery" : "list");
  function changeLayout(next: "list" | "gallery") {
    const updated = { ...preferences, [preferenceKey]: next };
    setPreferences(updated);
    try {
      localStorage.setItem("docling-library-views", JSON.stringify(updated));
    } catch {
      /* Browser storage is optional. */
    }
    onLayout?.(next);
  }
  function changeScope(next: "folder" | "images") {
    setLocalScope(next);
    setQuery("");
    setType("");
    setChecked(new Set());
    onScope?.(next);
  }
  const operationBusy = useRef(false),
    dragged = useRef<ItemRef[] | null>(null);
  const items = useMemo<Item[]>(
    () => [
      ...data.folders.map((f) => ({ ...f, kind: "folder" as const })),
      ...data.jobs.map((j) => ({ ...j, kind: "file" as const })),
    ],
    [data],
  );
  const entries = items.filter((i) =>
    scope === "images"
      ? i.kind === "file" &&
        i.image &&
        (!folder ||
          i.folder_id === folder ||
          chain(data.folders, i.folder_id).some((f) => f.id === folder))
      : (i.kind === "folder" ? i.parent_id : i.folder_id) === folder,
  );
  const visible = entries
    .filter(
      (i) =>
        [
          itemName(i),
          ...(i.kind === "file" && i.image
            ? [
                i.original_filename || "",
                i.image_metadata?.description || "",
                ...(i.image_metadata?.tags || []),
              ]
            : []),
        ]
          .join(" ")
          .normalize("NFC")
          .toLocaleLowerCase("ja")
          .includes(query.trim().normalize("NFC").toLocaleLowerCase("ja")) &&
        (!type ||
          i.kind === "folder" ||
          (type === "drawio"
            ? !!i.diagram
            : matchesFormat(i.original_filename || i.filename, type))),
    )
    .sort(
      (a, b) =>
        (a.kind === b.kind ? 0 : a.kind === "folder" ? -1 : 1) ||
        (sort === "name"
          ? itemName(a).localeCompare(itemName(b), "ja")
          : sort === "oldest"
            ? a.created - b.created
            : b.created - a.created) ||
        a.id.localeCompare(b.id),
    );
  const selected = visible.filter((i) => checked.has(itemKey(i)));
  useEffect(() => {
    setChecked(new Set());
    setQuery("");
  }, [folder]);
  const excluded = (id: string | null) =>
    pending?.items.some(
      (i) =>
        i.kind === "folder" &&
        (i.id === id || chain(data.folders, id).some((p) => p.id === i.id)),
    );
  const toggle = (item: Item, value: boolean) =>
    setChecked((previous) => {
      const next = new Set(previous);
      if (value) next.add(itemKey(item));
      else next.delete(itemKey(item));
      return next;
    });
  async function perform(
    request: {
      action: string;
      items?: ItemRef[];
      name?: string;
      destination?: string | null;
      parent?: string | null;
    },
    success: string,
    dialog = false,
  ) {
    if (operationBusy.current) return;
    operationBusy.current = true;
    setBusy(true);
    setError("");
    try {
      await post(
        request.action === "create"
          ? "/api/folders"
          : "/api/library/operations",
        request.action === "create"
          ? { name: request.name, parent_id: request.parent }
          : request,
      );
      setChecked(new Set());
      if (dialog) setPending(null);
      setMessage(success);
    } catch (e) {
      if (dialog) setError(errorText(e));
      else setMessage(errorText(e));
    } finally {
      await cache.invalidateQueries({ queryKey: ["library"] });
      operationBusy.current = false;
      setBusy(false);
    }
  }
  function start(action: Operation | "delete", targets = selected) {
    if (operationBusy.current || (!targets.length && action !== "create"))
      return;
    if (action === "delete") {
      void perform(
        { action, items: targets.map(({ kind, id }) => ({ kind, id })) },
        "削除しました。",
      );
      return;
    }
    setPending({ action, items: targets, parent: folder });
    setDestination(folder);
    setName(action === "rename" ? itemName(targets[0]) : "");
    setError("");
  }
  function dropProps(id: string | null): HTMLAttributes<HTMLElement> {
    return {
      onDragOver: (e) => {
        if (dragged.current && !busy) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "move";
          e.currentTarget.classList.add("drop-target");
        }
      },
      onDragLeave: (e) => e.currentTarget.classList.remove("drop-target"),
      onDrop: (e) => {
        if (e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        e.stopPropagation();
        e.currentTarget.classList.remove("drop-target");
        const targets = dragged.current;
        dragged.current = null;
        if (targets && !busy)
          void perform(
            { action: "move", items: targets, destination: id },
            "移動しました。",
          );
      },
    };
  }
  const crumbs = (id: string | null) => [
    { id: null, name: "資料一覧" },
    ...chain(data.folders, id),
  ];
  const titles = {
    create: "新しいフォルダー",
    rename: "名前を変更",
    move: "移動先を選択",
    copy: "コピー先を選択",
  };
  const naming = pending && ["create", "rename"].includes(pending.action);
  const date = new Intl.DateTimeFormat("ja-JP", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
  function open(item: Item) {
    if (busy) return;
    if (item.kind === "folder") onFolder(item.id);
    else onJob(item);
  }
  function actions(item: Item) {
    return (
      <DisclosureMenu
        className="item-menu"
        label={`${itemName(item)}の操作`}
        icon="more"
      >
        <Button onClick={() => open(item)}>開く</Button>
        <Button onClick={() => start("rename", [item])}>名前を変更</Button>
        <Button onClick={() => start("move", [item])}>移動先</Button>
        <Button onClick={() => start("copy", [item])}>コピー先</Button>
        {item.kind === "file" && (
          <ActionLink href={withBase(originalUrl(item))}>原本を保存</ActionLink>
        )}
        <Button className="danger" onClick={() => start("delete", [item])}>
          削除
        </Button>
      </DisclosureMenu>
    );
  }
  return (
    <>
      <section id="library" aria-label="資料一覧" hidden={hidden}>
        <Toolbar as="header" className="library-bar">
          {navigation}
          <label className="file-search">
            <Icon name="search" />
            <TextField
              id="fileSearch"
              type="search"
              aria-label={
                scope === "images" ? "画像を検索" : "このフォルダー内を検索"
              }
              placeholder={
                scope === "images"
                  ? "画像名・説明・タグを検索"
                  : "このフォルダー内を検索"
              }
              autoComplete="off"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <Button id="newFolder" onClick={() => start("create")}>
            <Icon name="folder" />
            新しいフォルダー
          </Button>
          <Button id="addFile" className="primary" onClick={onUpload}>
            <Icon name="plus" />
            資料を追加
          </Button>
        </Toolbar>
        <div className="library-content">
          <div className="library-location-bar">
            {scope === "images" ? (
              <label className="image-folder-filter">
                保存先
                <SelectField
                  aria-label="画像の保存先"
                  value={folder || ""}
                  onChange={(e) => onFolder(e.target.value || null)}
                >
                  <option value="">すべてのフォルダー</option>
                  {data.folders.map((f) => (
                    <option key={f.id} value={f.id}>
                      {chain(data.folders, f.id)
                        .map((p) => p.name)
                        .join(" / ")}
                      （配下を含む）
                    </option>
                  ))}
                </SelectField>
              </label>
            ) : (
              <Breadcrumbs
                id="breadcrumbs"
                aria-label="現在のフォルダー"
                items={crumbs(folder)}
                onSelect={onFolder}
                dropProps={(id) => dropProps(id)}
              />
            )}
            <div className="library-view-controls">
              <div
                className="library-view-bar"
                role="group"
                aria-label="資料の表示対象"
              >
                <Button
                  aria-pressed={scope === "folder"}
                  onClick={() => changeScope("folder")}
                >
                  すべての資料
                </Button>
                <Button
                  aria-pressed={scope === "images"}
                  onClick={() => changeScope("images")}
                >
                  画像を探す
                </Button>
              </div>
              <div
                role="group"
                aria-label="一覧の表示形式"
                className="library-layout-toggle"
              >
                <Button
                  aria-pressed={layout === "list"}
                  onClick={() => changeLayout("list")}
                >
                  リスト
                </Button>
                <Button
                  aria-pressed={layout === "gallery"}
                  onClick={() => changeLayout("gallery")}
                >
                  ギャラリー
                </Button>
              </div>
            </div>
          </div>
          <Toolbar
            id="selectionToolbar"
            className="selection-toolbar"
            hidden={!selected.length}
          >
            <span id="selectionCount">{selected.length}件選択</span>
            <Button
              id="selectionClear"
              disabled={busy}
              onClick={() => setChecked(new Set())}
            >
              選択解除
            </Button>
            <Button
              id="selectionRename"
              disabled={busy || selected.length !== 1}
              onClick={() => start("rename")}
            >
              名前を変更
            </Button>
            <Button
              id="selectionMove"
              disabled={busy}
              onClick={() => start("move")}
            >
              移動先
            </Button>
            <Button
              id="selectionCopy"
              disabled={busy}
              onClick={() => start("copy")}
            >
              コピー先
            </Button>
            <Button
              id="selectionDownload"
              disabled={
                busy || selected.length !== 1 || selected[0]?.kind !== "file"
              }
              onClick={() => {
                const i = selected[0];
                if (i?.kind === "file") {
                  const link = document.createElement("a");
                  link.href = withBase(originalUrl(i));
                  link.click();
                }
              }}
            >
              原本を保存
            </Button>
            <Button
              id="selectionDelete"
              className="danger"
              disabled={busy}
              onClick={() => start("delete")}
            >
              削除
            </Button>
          </Toolbar>
          <div className="library-tools">
            <span id="fileCount" role="status">
              {visible.length}件
              {visible.length !== entries.length && ` / ${entries.length}件`}
            </span>
            <div className="list-filters">
              <SelectField
                id="fileType"
                aria-label="ファイルの種類"
                value={type}
                onChange={(e) => setType(e.target.value)}
              >
                <option value="">すべての種類</option>
                <option value="pdf">PDF</option>
                <option value="pptx">PowerPoint</option>
                <option value="xlsx">Excel</option>
                <option value="docx">Word</option>
                <option value="md">Markdown</option>
                <option value="txt">テキスト</option>
                <option value="tex">TeX</option>
                <option value="drawio">draw.io</option>
                <option value="image">画像</option>
                <option value="png">PNG</option>
                <option value="jpg">JPEG</option>
                <option value="gif">GIF</option>
                <option value="webp">WebP</option>
              </SelectField>
              <SelectField
                id="fileSort"
                aria-label="並べ替え"
                value={sort}
                onChange={(e) => setSort(e.target.value)}
              >
                <option value="recent">追加日時：新しい順</option>
                <option value="oldest">追加日時：古い順</option>
                <option value="name">ファイル名順</option>
              </SelectField>
            </div>
          </div>
          <StatusMessage id="message" hidden={!message}>
            {message}
          </StatusMessage>
          <div className={layout === "gallery" ? "gallery-list" : "file-list"}>
            {layout === "gallery" ? (
              <ul className="library-gallery" aria-label="保存済みの資料">
                {visible.map((item) => (
                  <li
                    key={itemKey(item)}
                    className={checked.has(itemKey(item)) ? "selected" : ""}
                    {...(item.kind === "folder" ? dropProps(item.id) : {})}
                  >
                    <div className="gallery-item-tools">
                      <CheckboxField
                        aria-label={`${itemName(item)}を選択`}
                        checked={checked.has(itemKey(item))}
                        disabled={busy}
                        onChange={(e) => toggle(item, e.target.checked)}
                      />
                      {actions(item)}
                    </div>
                    <Button
                      className="gallery-open"
                      aria-label={`${itemName(item)}を開く`}
                      onClick={() => open(item)}
                      disabled={busy}
                    >
                      <span className="gallery-thumbnail">
                        {item.kind === "folder" ? (
                          <Icon name="folder" />
                        ) : (
                          <Thumbnail job={item} />
                        )}
                      </span>
                      <span className="gallery-name">{itemName(item)}</span>
                      <span className="gallery-meta">
                        {item.kind === "folder"
                          ? "フォルダー"
                          : item.image
                            ? `${item.image.width} × ${item.image.height} · ${item.image.media_type.split("/")[1].toUpperCase()}`
                            : item.diagram
                              ? "閲覧可能"
                              : labels[item.state]}
                      </span>
                      {scope === "images" && item.kind === "file" && (
                        <span className="gallery-meta">
                          {folderPath(data.folders, item.folder_id)}
                        </span>
                      )}
                    </Button>
                  </li>
                ))}
              </ul>
            ) : (
              <table aria-label="保存済みの資料">
                <thead>
                  <tr>
                    <th scope="col" className="select-column">
                      <CheckboxField
                        id="selectAll"
                        aria-label="表示中の項目をすべて選択"
                        disabled={!visible.length || busy}
                        checked={
                          !!visible.length && selected.length === visible.length
                        }
                        indeterminate={
                          !!selected.length &&
                          selected.length !== visible.length
                        }
                        onChange={(e) =>
                          setChecked(
                            e.target.checked
                              ? new Set(visible.map(itemKey))
                              : new Set(),
                          )
                        }
                      />
                    </th>
                    <th scope="col" className="name-column">
                      名前
                    </th>
                    <th scope="col" className="state-column">
                      状態
                    </th>
                    <th scope="col" className="pages-column">
                      ページ
                    </th>
                    <th scope="col" className="date-column">
                      追加日時
                    </th>
                    <th scope="col" className="actions-column">
                      <span className="sr-only">操作</span>
                    </th>
                  </tr>
                </thead>
                <tbody id="jobs">
                  {visible.map((item) => (
                    <tr
                      key={itemKey(item)}
                      data-key={itemKey(item)}
                      className={checked.has(itemKey(item)) ? "selected" : ""}
                      {...(item.kind === "folder" ? dropProps(item.id) : {})}
                      onClick={(e) => {
                        if (busy) return;
                        if (e.ctrlKey || e.metaKey || e.shiftKey)
                          toggle(item, !checked.has(itemKey(item)));
                        else open(item);
                      }}
                      onContextMenu={(e) => {
                        e.preventDefault();
                        document
                          .querySelectorAll<HTMLDetailsElement>(".item-menu")
                          .forEach((n) => {
                            n.open = false;
                          });
                        const n =
                          e.currentTarget.querySelector<HTMLDetailsElement>(
                            ".item-menu",
                          );
                        if (n) n.open = true;
                      }}
                    >
                      <td onClick={(e) => e.stopPropagation()}>
                        <CheckboxField
                          aria-label={`${itemName(item)}を選択`}
                          checked={checked.has(itemKey(item))}
                          onChange={(e) => toggle(item, e.target.checked)}
                          disabled={busy}
                        />
                      </td>
                      <td>
                        <Button
                          className="file-open"
                          aria-label={`${itemName(item)}を開く`}
                          draggable
                          onDragStart={(e) => {
                            if (busy) {
                              e.preventDefault();
                              return;
                            }
                            dragged.current = checked.has(itemKey(item))
                              ? selected.map(({ kind, id }) => ({ kind, id }))
                              : [{ kind: item.kind, id: item.id }];
                            e.dataTransfer.setData(
                              "text/plain",
                              itemName(item),
                            );
                            e.dataTransfer.effectAllowed = "move";
                          }}
                          onDragEnd={() => {
                            dragged.current = null;
                            document
                              .querySelectorAll(".drop-target")
                              .forEach((n) =>
                                n.classList.remove("drop-target"),
                              );
                          }}
                        >
                          {item.kind === "folder" ? (
                            <Icon name="folder" className="folder-kind" />
                          ) : (
                            <span
                              className={`file-kind ${item.filename.split(".").pop()?.toLowerCase()}`}
                              aria-hidden="true"
                            >
                              {item.filename.split(".").pop()?.toUpperCase()}
                            </span>
                          )}
                          <span className="file-name" title={itemName(item)}>
                            {itemName(item)}
                          </span>
                        </Button>
                      </td>
                      <td>
                        <span
                          className={
                            item.kind === "file"
                              ? `file-state ${item.state}`
                              : ""
                          }
                        >
                          {item.kind === "folder"
                            ? "フォルダー"
                            : item.image
                              ? "画像"
                              : item.diagram
                                ? "閲覧可能"
                                : labels[item.state]}
                        </span>
                        {item.kind === "file" &&
                          Object.entries(item.translations || {}).map(
                            ([lang, c]) => (
                              <span className="translation-summary" key={lang}>
                                {lang === "en" ? "英訳" : "日本語訳"} {c.saved}/
                                {isDocument(
                                  item.original_filename || item.filename,
                                )
                                  ? 1
                                  : item.pages || "—"}
                                {c.active
                                  ? " · 翻訳中"
                                  : c.failed
                                    ? " · 再実行可"
                                    : ""}
                              </span>
                            ),
                          )}
                      </td>
                      <td>
                        {item.kind === "folder"
                          ? "—"
                          : item.image
                            ? "—"
                            : isDocument(
                                  item.original_filename || item.filename,
                                ) && isDone(item)
                              ? "文書全体"
                              : item.pages || "—"}
                      </td>
                      <td
                        title={new Date(item.created * 1000).toLocaleString(
                          "ja-JP",
                        )}
                      >
                        {date.format(new Date(item.created * 1000))}
                      </td>
                      <td>{actions(item)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div id="libraryEmpty" hidden={!!visible.length}>
              <Icon name="folder" />
              <p id="emptyText">
                {entries.length
                  ? "一致する項目がありません。"
                  : scope === "images"
                    ? "画像はまだありません。"
                    : "このフォルダーは空です。"}
              </p>
              <Button
                id="emptyAdd"
                hidden={!!entries.length}
                onClick={onUpload}
              >
                資料を追加
              </Button>
            </div>
          </div>
          <p id="libraryHint" className="library-hint">
            ファイルを画面へドロップして追加できます。画像は保存後すぐに閲覧できます。資料の操作はチェックボックス・「…」から。
          </p>
        </div>
      </section>
      <Dialog
        id="libraryDialog"
        className="library-dialog"
        aria-labelledby="libraryDialogTitle"
        open={!!pending}
        busy={busy}
        onClose={() => {
          if (!busy) setPending(null);
        }}
      >
        <form
          id="libraryForm"
          onSubmit={(e) => {
            e.preventDefault();
            if (pending)
              void perform(
                {
                  action: pending.action,
                  items: pending.items.map(({ kind, id }) => ({ kind, id })),
                  name,
                  destination,
                  parent: pending.parent,
                },
                {
                  create: "フォルダーを作成しました。",
                  rename: "名前を変更しました。",
                  move: "移動しました。",
                  copy: "コピーしました。",
                }[pending.action],
                true,
              );
          }}
        >
          <DialogHeading
            id="libraryDialogTitle"
            title={pending ? titles[pending.action] : ""}
            onClose={() => setPending(null)}
            disabled={busy}
          />
          <p id="operationDescription">
            {pending?.items.length
              ? pending.items.length === 1
                ? itemName(pending.items[0])
                : `${pending.items.length}件の項目`
              : `作成先：${folderPath(data.folders, pending?.parent ?? null)}`}
          </p>
          <label id="operationNameLabel" hidden={!naming}>
            名前
            <TextField
              id="operationName"
              type="text"
              maxLength={200}
              autoComplete="off"
              required={!!naming}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <div id="destinationBrowser" hidden={!!naming}>
            <Breadcrumbs
              id="destinationBreadcrumbs"
              aria-label="移動先のフォルダー"
              items={crumbs(destination)}
              onSelect={setDestination}
            />
            <div id="destinationFolders">
              {data.folders
                .filter((f) => f.parent_id === destination)
                .sort((a, b) => a.name.localeCompare(b.name, "ja"))
                .map((f) => (
                  <Button
                    key={f.id}
                    className="destination-folder"
                    disabled={busy || excluded(f.id)}
                    onClick={() => setDestination(f.id)}
                  >
                    <Icon name="folder" />
                    {f.name}
                    <Icon name="next" />
                  </Button>
                ))}
            </div>
            <p id="destinationHint">
              {data.folders.some((f) => f.parent_id === destination)
                ? "フォルダーを開いて保存先を選んでください。"
                : "このフォルダーに下位フォルダーはありません。"}
            </p>
          </div>
          <StatusMessage id="operationError" role="alert" hidden={!error}>
            {error}
          </StatusMessage>
          <DialogActions>
            <Button
              id="cancelOperation"
              onClick={() => setPending(null)}
              disabled={busy}
            >
              キャンセル
            </Button>
            <Button
              id="submitOperation"
              type="submit"
              className="primary"
              disabled={busy || (!naming && excluded(destination))}
            >
              {pending
                ? {
                    create: "作成",
                    rename: "保存",
                    move: "ここに移動",
                    copy: "ここにコピー",
                  }[pending.action]
                : ""}
            </Button>
          </DialogActions>
        </form>
      </Dialog>
    </>
  );
}
