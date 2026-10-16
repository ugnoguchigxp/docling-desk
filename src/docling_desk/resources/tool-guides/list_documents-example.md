# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "list_documents",
  "arguments": {
    "arguments": {
      "offset": 0,
      "limit": 20
    }
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。source_idと表示情報を取得。offset・limitでページを分ける。本文や版はget_documentで取得する。デスクトップ画面の資料一覧とは保存先が別である。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
