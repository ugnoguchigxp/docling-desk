# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "add_document_attachment",
  "arguments": {
    "arguments": {
      "draft_id": "11111111-1111-4111-8111-111111111111",
      "etag": "\"draft-1\"",
      "idempotency_key": "synthetic-operation-key",
      "filename": "synthetic.txt",
      "media_type": "text/plain",
      "body_base64": "b2s="
    }
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。現在の下書きetagと再送キーが必要。小さいバイナリをbody_base64へ渡す。gatewayのJSON受付上限は2 MiBであり、base64分の増加も含む。返るattachment_id・sha256と新しい下書きetagを保持。本文には添付参照を使い、一時URLや認証情報を埋め込まない。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
