# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "edit_document_draft",
  "arguments": {
    "arguments": {
      "draft_id": "11111111-1111-4111-8111-111111111111",
      "etag": "\"draft-1\"",
      "idempotency_key": "synthetic-operation-key",
      "base_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "edits": [
        {
          "expected": "本文。",
          "replacement": "更新本文。"
        }
      ]
    }
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。get_document_draftのetag・sha256・bodyが必要。base_sha256にsha256を指定。expectedは完全一致が1件だけの原文。範囲指定のstart・endは0始まりUTF-8バイト範囲[start,end)。JavaScriptのUTF-16添字ではない。複数編集は同じ基準本文に対して指定し、不一致・重複があれば全件を適用しない。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
