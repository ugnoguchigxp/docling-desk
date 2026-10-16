# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "connect_document_editor",
  "arguments": {
    "code": "12ab34cd"
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。write権限と利用者が提供した編集用コードが必要。閲覧セッションから編集権限を作らない。同じdraft_idを人とAIが編集できるが、古いetagの保存は拒否される。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
