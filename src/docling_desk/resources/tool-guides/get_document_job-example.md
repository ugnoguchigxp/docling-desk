# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "get_document_job",
  "arguments": {
    "arguments": {
      "job_id": "11111111-1111-4111-8111-111111111111"
    }
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。登録・確定・再試行が返したjob_idを使用。stateと失敗code、source_revisionを確認。completedだけで意味検索準備完了と判断せず、文書のfts_ready・semantic_readyも区別する。短い間隔で無制限に繰り返さない。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
