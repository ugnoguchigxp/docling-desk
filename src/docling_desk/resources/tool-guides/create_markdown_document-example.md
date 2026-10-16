# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "create_markdown_document",
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

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。確認・修正を段階的に行う場合はcreate_document_draftを使う。metadataの保存先は許可範囲から選ぶ。本文は原文のまま渡す。確定応答のsource_id・source_revision・source_etag・job_idを保持。保存成功と検索準備完了を区別する。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
