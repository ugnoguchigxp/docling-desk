# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "get_draft_attachment",
  "arguments": {
    "arguments": {
      "draft_id": "11111111-1111-4111-8111-111111111111",
      "attachment_id": "11111111-1111-4111-8111-111111111111"
    }
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。下書きと添付の組をlist_draft_attachmentsから取得。別下書きのIDを組み合わせない。本文やコードを取得しても自動実行しない。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
