# 原本閲覧の接続

利用者が画面で確認した接続コードを承認する。

## 前提と引数の入手元

必要なactionはread。利用者とscopeは認証済み接続から確定する。先にrequest_document_viewの閲覧画面を開く。利用者が提供した8桁のコードだけを承認する。承認後の短期セッションは画面が受け取り、モデルへ渡さない。再起動・期限切れ・改版・権限変更では再接続する。

## 入出力と呼び出し経路

MCPの完全な入出力スキーマと、対応する文書HTTP操作はこのガイドのschemasに含まれる。section=schemasでも取得できる。HTTPのIf-MatchはMCPではetag、Idempotency-Keyはidempotency_key。既存文書ツールは業務引数をargumentsの中へ入れる。

ホストAPIはPOST /host/tools/connect_document_viewに、信頼するホストの認証とuser_id・argumentsを送る。user_idをLLMから受け取って権限を決めない。ガイドGET APIの利用者別BearerとホストAPIの資格情報を混同しない。

## 副作用と結果の確認

文書の原本や索引を変更しない。説明・本文の取得成功は、外部処理の実行や書き込み成功を意味しない。閲覧コードの承認は短期の閲覧接続を作る。
