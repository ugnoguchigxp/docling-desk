# 呼び出しと応答

合成値による形の例。3つの版参照と要素ID・表ID・単位番号は実際の先行応答から取得する。

```json
{
  "name": "get_document_page_image",
  "arguments": {
    "arguments": {
      "source_id": "11111111-1111-4111-8111-111111111111",
      "source_revision": "22222222-2222-4222-8222-222222222222",
      "evidence_revision": "33333333-3333-4333-8333-333333333333",
      "unit": 1
    }
  }
}
```

isErrorを先に確認する。成功時はstructuredContentの版と出典を保持する。画像ツールはcontentのimageも読む。
