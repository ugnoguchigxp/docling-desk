import type { ReactNode } from "react";
import { Icon } from "../../components/Icons";
import { Button, TextField, Toolbar } from "../../components/ui";
import type { Scope } from "./entries";

/** Header: navigation, search, and the actions that add something. */
export function LibraryBar({
  navigation,
  scope,
  query,
  onQuery,
  onNewFolder,
  onNewDiagram,
  onUpload,
}: {
  navigation?: ReactNode;
  scope: Scope;
  query: string;
  onQuery: (text: string) => void;
  onNewFolder: () => void;
  onNewDiagram: () => void;
  onUpload: () => void;
}) {
  return (
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
          onChange={(e) => onQuery(e.target.value)}
        />
      </label>
      <Button id="newFolder" onClick={onNewFolder}>
        <Icon name="folder" />
        新しいフォルダー
      </Button>
      <Button id="newDiagram" onClick={onNewDiagram}>
        <Icon name="file-plus" />
        図表を作成
      </Button>
      <Button id="addFile" className="primary" onClick={onUpload}>
        <Icon name="plus" />
        資料を追加
      </Button>
    </Toolbar>
  );
}
