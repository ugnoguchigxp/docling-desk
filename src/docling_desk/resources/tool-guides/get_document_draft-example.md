# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "get_document_draft",
  "arguments": {
    "arguments": {
      "draft_id": "11111111-1111-4111-8111-111111111111"
    }
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。一覧・作成結果のdraft_idを使用。body・etag・sha256・base_revision・base_etag・attachments・stateを確認。更新前には最新値を読み、基準となる原文を保つ。確定要求のsource_etagには基準文書のbase_etagを渡し、下書きのetagとは区別する。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
