# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "update_document_metadata",
  "arguments": {
    "arguments": {
      "source_id": "11111111-1111-4111-8111-111111111111",
      "title": "合成資料",
      "collection_id": "documents",
      "source_kind": "wiki",
      "language": "ja",
      "etag": "\"source-1\"",
      "idempotency_key": "synthetic-operation-key"
    }
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。最新source_etagをetagに指定。metadataの中へ包まず、title・collection_id等を直接渡す。frontmatterのtitleは自動変更しない。scope変更は変更前後のwrite権限が必要。更新後のetagを次の操作に使う。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
