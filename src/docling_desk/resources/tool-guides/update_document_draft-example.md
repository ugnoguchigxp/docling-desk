# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "update_document_draft",
  "arguments": {
    "arguments": {
      "draft_id": "11111111-1111-4111-8111-111111111111",
      "etag": "\"draft-1\"",
      "idempotency_key": "synthetic-operation-key",
      "body": "# 合成資料\n\n更新本文。\n"
    }
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。get_document_draftのetagが必要。bodyは原文。metadata・attachmentsは変更する場合に指定。更新後のetag・sha256を次へ渡す。確定済み下書きは変更せず、新しい下書きを作る。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
