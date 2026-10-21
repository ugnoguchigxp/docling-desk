import { useMemo, useState, type ReactNode } from "react";
import { Icon } from "../components/Icons";
import { Button, StatusMessage } from "../components/ui";
import type { Item, Job, Library as Snapshot } from "../lib/types";
import "./media-library.css";
import { FeatureStatus } from "./FeatureStatus";
import { DiagramDialog } from "./library/DiagramDialog";
import {
  filterEntries,
  listEntries,
  toItems,
  type Layout,
  type Scope,
} from "./library/entries";
import { FilterBar } from "./library/FilterBar";
import { foldersHoldingDiagram } from "./library/folders";
import { GalleryView } from "./library/GalleryView";
import { LibraryBar } from "./library/LibraryBar";
import { ListView } from "./library/ListView";
import { LocationBar } from "./library/LocationBar";
import { OperationDialog } from "./library/OperationDialog";
import { SelectionToolbar } from "./library/SelectionToolbar";
import { useDiagramCreation } from "./library/useDiagramCreation";
import { useLibraryFilter } from "./library/useLibraryFilter";
import { useLibraryOperations } from "./library/useLibraryOperations";
import { useLibraryView } from "./library/useLibraryView";
import { useOperationLock } from "./library/useOperationLock";
import { useSelection } from "./library/useSelection";
import type { ItemViewModel } from "./library/view-model";

/**
 * The library screen. The snapshot comes from Query (App); folder, scope and
 * layout come from the route. This component only joins the pieces: the list
 * and the gallery read one view model, and dialogs keep their own input.
 */
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
  scope?: Scope;
  layout?: Layout;
  onScope?: (scope: Scope) => void;
  onLayout?: (layout: Layout) => void;
}) {
  const view = useLibraryView({
      folder,
      scope: requestedScope,
      layout: requestedLayout,
      onScope,
      onLayout,
    }),
    { scope, layout } = view,
    {
      filter,
      setQuery,
      setType,
      setSort,
      clear: clearFilter,
    } = useLibraryFilter(folder),
    items = useMemo(() => toItems(data), [data]),
    entries = listEntries(items, data.folders, scope, folder),
    visible = filterEntries(entries, filter),
    selection = useSelection(visible, folder),
    diagramFolders = useMemo(() => foldersHoldingDiagram(data), [data]),
    holdsDiagram = (item: Item) =>
      item.kind === "file" ? !!item.excalidraw : diagramFolders.has(item.id),
    [failure, setFailure] = useState(""),
    lock = useOperationLock(),
    operations = useLibraryOperations({
      folder,
      selected: selection.selected,
      holdsDiagram,
      lock,
      setMessage,
      onFailure: setFailure,
      clearSelection: selection.clear,
    }),
    diagram = useDiagramCreation({ folder, lock, setMessage, onJob }),
    model: ItemViewModel = {
      folders: data.folders,
      scope,
      items: visible,
      selected: selection.selected,
      busy: lock.busy,
      isChecked: selection.isChecked,
      toggle: selection.toggle,
      selectAll: selection.selectAll,
      clearSelection: selection.clear,
      open(item) {
        if (lock.busy) return;
        if (item.kind === "folder") onFolder(item.id);
        else onJob(item);
      },
      start: operations.start,
      holdsDiagram,
      dropProps: operations.dropProps,
      beginDrag: operations.beginDrag,
      endDrag: operations.endDrag,
    };
  return (
    <>
      <section id="library" aria-label="資料一覧" hidden={hidden}>
        <LibraryBar
          navigation={navigation}
          scope={scope}
          query={filter.query}
          onQuery={setQuery}
          onNewFolder={() => operations.start("create")}
          onNewDiagram={diagram.start}
          onUpload={onUpload}
        />
        <div className="library-content">
          <LocationBar
            folders={data.folders}
            folder={folder}
            scope={scope}
            layout={layout}
            onFolder={onFolder}
            onScope={(next) => {
              clearFilter();
              selection.clear();
              view.changeScope(next);
            }}
            onLayout={view.changeLayout}
            dropProps={operations.dropProps}
          />
          <SelectionToolbar model={model} />
          <FilterBar
            shown={visible.length}
            total={entries.length}
            filter={filter}
            onType={setType}
            onSort={setSort}
          />
          <StatusMessage
            id="message"
            hidden={!message && !lock.busy}
            kind={
              message
                ? message === failure
                  ? "error"
                  : undefined
                : lock.busy
                  ? "running"
                  : undefined
            }
          >
            {message || "処理しています…"}
          </StatusMessage>
          <div className={layout === "gallery" ? "gallery-list" : "file-list"}>
            {layout === "gallery" ? (
              <GalleryView model={model} />
            ) : (
              <ListView model={model} />
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
          <FeatureStatus />
        </div>
      </section>
      <DiagramDialog
        target={diagram.target}
        folders={data.folders}
        busy={lock.busy}
        onSubmit={(name, onError) => void diagram.submit(name, onError)}
        onClose={diagram.cancel}
      />
      <OperationDialog
        pending={operations.pending}
        folders={data.folders}
        busy={lock.busy}
        onSubmit={(input, onError) => void operations.submit(input, onError)}
        onClose={operations.cancel}
      />
    </>
  );
}
