# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "connect_document_view",
  "arguments": {
    "code": "12ab34cd"
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。先にrequest_document_viewの閲覧画面を開く。利用者が提供した8桁のコードだけを承認する。承認後の短期セッションは画面が受け取り、モデルへ渡さない。再起動・期限切れ・改版・権限変更では再接続する。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
