# 最小の呼び出し例

合成値による形の例。IDとetagは実際の先行操作の応答から取得し、再送キーは最初のものを保持する。

```json
{
  "name": "replace_document_source",
  "arguments": {
    "arguments": {
      "transfer_id": "22222222222222222222222222222222",
      "source_id": "11111111-1111-4111-8111-111111111111",
      "etag": "\"source-1\"",
      "idempotency_key": "synthetic-replace-1"
    }
  }
}
```

成功時はstructuredContentの文書参照・状態またはjob_idを保持する。失敗時はisErrorとerror.code・error.statusを確認し、recoveryへ進む。
