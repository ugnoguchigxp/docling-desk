# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "search_documents",
  "arguments": {
    "query": "在庫照合",
    "scope": {
      "collection_ids": [
        "documents"
      ]
    },
    "mode": "text",
    "limit": 10
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。先にlist_document_scopesで範囲を取得。modeはtext・semantic・hybrid。resultsのretrieval_idに属するsource_id・source_revision・evidence_revision・context_idを保持する。短い抜粋だけで結論を作らずget_document_contextへ進む。effective_modeとdegraded_reasonsで意味検索の縮退を確認。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
