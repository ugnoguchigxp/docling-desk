# 保存版の添付取得

確定済み文書の添付を取得する。

## 前提と引数の入手元

必要なactionはread。利用者とscopeは認証済み接続から確定する。文書と添付の組を一覧から取得する。返るmedia_type・サイズ・ハッシュを確認。大きな添付のバイナリ経路はホストの認証付きHTTPで取得し、モデルへ資格情報を渡さない。

## 入出力と呼び出し経路

MCPの完全な入出力スキーマと、対応する文書HTTP操作はこのガイドのschemasに含まれる。section=schemasでも取得できる。HTTPのIf-MatchはMCPではetag、Idempotency-Keyはidempotency_key。既存文書ツールは業務引数をargumentsの中へ入れる。

ホストAPIはPOST /host/tools/get_document_attachmentに、信頼するホストの認証とuser_id・argumentsを送る。user_idをLLMから受け取って権限を決めない。ガイドGET APIの利用者別BearerとホストAPIの資格情報を混同しない。

## 副作用と結果の確認

文書の原本や索引を変更しない。説明・本文の取得成功は、外部処理の実行や書き込み成功を意味しない。閲覧コードの承認は短期の閲覧接続を作る。
