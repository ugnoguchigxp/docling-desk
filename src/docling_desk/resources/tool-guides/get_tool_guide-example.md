# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "get_tool_guide",
  "arguments": {
    "guide_id": "overview"
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。guide_idは一覧またはDescriptionから取得。sectionはsection_idsから選ぶ。schema用sectionはschemas。next_cursorがあれば同じguide_id・sectionで続きを読む。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
