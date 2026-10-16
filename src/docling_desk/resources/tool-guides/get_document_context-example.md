# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "get_document_context",
  "arguments": {
    "retrieval_id": "11111111-1111-4111-8111-111111111111",
    "references": [
      {
        "source_id": "11111111-1111-4111-8111-111111111111",
        "source_revision": "11111111-1111-4111-8111-111111111111",
        "evidence_revision": "11111111-1111-4111-8111-111111111111",
        "context_id": "11111111-1111-4111-8111-111111111111"
      }
    ],
    "max_chars": 80000
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。直前の検索で返ったretrieval_idとreferencesをそのまま使用。検索参照の有効期間は10分。contextsの本文、locator、要素参照と版を引用に使う。上限超過では参照数を減らす。期限切れや古い版は再検索する。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
