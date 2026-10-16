# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "list_tool_guides",
  "arguments": {
    "kind": "workflow",
    "limit": 20
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。kindはoverview・tool・workflow。tool_nameは完全一致のツール名。guidesの要約から必要な説明を選ぶ。next_cursorがあれば同じ絞り込みで続ける。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
