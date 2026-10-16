# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "commit_document_draft",
  "arguments": {
    "arguments": {
      "draft_id": "11111111-1111-4111-8111-111111111111",
      "etag": "\"draft-1\"",
      "idempotency_key": "synthetic-operation-key",
      "source_etag": null
    }
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。etagは最新の下書きetag、source_etagは基準文書のetag。新規文書ではsource_etag:nullを明示。両方の競合を検査する。保存結果のsource_id・source_revision・source_etag・sha256・job_idを保持。索引失敗で保存済み本文は取り消されない。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
