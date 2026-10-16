# 本文と保存版の取得

文書の本文・現在版・編集に必要な情報を取得する。

## 前提と引数の入手元

必要なactionはread。利用者とscopeは認証済み接続から確定する。source_idは一覧・検索から取得。content_kind=original_markdownは原文、extracted_textはPDF・Office等の抽出本文。Markdownの原文・source_revision・etag・sha256を取得して下書きを作る。取得した文書etagを作成要求のsource_etagへ渡す。原本バイナリを返す操作ではない。

## 入出力と呼び出し経路

MCPの完全な入出力スキーマと、対応する文書HTTP操作はこのガイドのschemasに含まれる。section=schemasでも取得できる。HTTPのIf-MatchはMCPではetag、Idempotency-Keyはidempotency_key。既存文書ツールは業務引数をargumentsの中へ入れる。

ホストAPIはPOST /host/tools/get_documentに、信頼するホストの認証とuser_id・argumentsを送る。user_idをLLMから受け取って権限を決めない。ガイドGET APIの利用者別BearerとホストAPIの資格情報を混同しない。

## 副作用と結果の確認

文書の原本や索引を変更しない。説明・本文の取得成功は、外部処理の実行や書き込み成功を意味しない。閲覧コードの承認は短期の閲覧接続を作る。
