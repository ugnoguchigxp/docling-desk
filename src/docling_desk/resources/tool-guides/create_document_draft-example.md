# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "create_document_draft",
  "arguments": {
    "arguments": {
      "body": "# 合成資料\n\n本文。\n",
      "metadata": {
        "title": "合成資料",
        "collection_id": "documents",
        "source_kind": "wiki",
        "language": "ja"
      },
      "idempotency_key": "synthetic-operation-key"
    }
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。新規はbody・metadata、既存はget_documentのsource_id・source_revisionと、文書etagをsource_etagへ渡す。既存のbody省略時は原文と添付参照を引き継ぐ。返ったdraft_id・etag・sha256を保持。PDF・Office原本の編集には使わない。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
