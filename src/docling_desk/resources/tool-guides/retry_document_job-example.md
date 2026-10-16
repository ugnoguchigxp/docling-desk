# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "retry_document_job",
  "arguments": {
    "arguments": {
      "job_id": "11111111-1111-4111-8111-111111111111",
    "allow_ocr_resubmit": false
    }
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。get_document_jobで対象・現在版・失敗理由を確認する。write権限が必要。任意の成功済み処理を再実行する操作ではない。返ったjob_idを追跡。原本の再抽出はreextract_document、保存済み本文の再索引はreindex_documentを使う。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
