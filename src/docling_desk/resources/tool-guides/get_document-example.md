# 最小の呼び出し例

次は合成資料による形の例。ID・etag・ハッシュ・検索参照は、実際には先行操作の応答から取得する。キーは初回に記録し再送で保持する。

```json
{
  "name": "get_document",
  "arguments": {
    "arguments": {
      "source_id": "11111111-1111-4111-8111-111111111111"
    }
  }
}
```

成功応答はstructuredContent、未提供ならJSON本文を読み、schemasの出力契約と照合する。source_idは一覧・検索から取得。content_kind=original_markdownは原文、extracted_textはPDF・Office等の抽出本文。Markdownの原文・source_revision・etag・sha256を取得して下書きを作る。取得した文書etagを作成要求のsource_etagへ渡す。原本バイナリを返す操作ではない。

失敗時はisErrorまたはHTTP statusとcodeを確認し、recoveryの手順へ進む。
