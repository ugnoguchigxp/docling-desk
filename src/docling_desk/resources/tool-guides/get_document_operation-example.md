# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "get_document_operation",
  "arguments": {
    "arguments": {
      "operation": "commit",
      "target_id": "11111111-1111-4111-8111-111111111111",
      "key": "synthetic-operation-key"
    }
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。operationと最初に送ったキーを指定。commit・update・edit・attachmentのtarget_idはdraft_id、metadataはsource_id。新規create・draftはtarget_id省略、既存文書からのdraftはsource_id。同じclient・actorが必要。保持は7日。operation_unknownを未実行と決めつけない。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
