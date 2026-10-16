# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "list_document_scopes",
  "arguments": {}
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。scopesとactionsを読み、検索先と保存先を決める。呼び出し元の利用者や権限を引数から指定しない。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
