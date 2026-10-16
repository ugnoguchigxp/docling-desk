# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "request_document_view",
  "arguments": {
    "source_id": "11111111-1111-4111-8111-111111111111",
    "source_revision": "11111111-1111-4111-8111-111111111111",
    "evidence_revision": "11111111-1111-4111-8111-111111111111"
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。検索・根拠の版と任意のlocationを渡す。PDFのpage・PPTXのslide・XLSXのsheet番号は1始まり。Word等のdocumentは1。embed_urlは資格情報を含まないが、開くだけでは認証されない。status=connection_requiredなら画面のコードを利用者から受け取りconnect_document_viewへ進む。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
