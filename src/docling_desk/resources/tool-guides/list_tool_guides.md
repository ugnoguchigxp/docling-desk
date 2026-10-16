# 利用ガイドの一覧

全体説明・ツール・作業手順の一覧を取得する。初回はget_tool_guide(guide_id="overview")を読む。

## 前提と引数の入手元

必要なactionはread。利用者とscopeは認証済み接続から確定する。kindはoverview・tool・workflow。tool_nameは完全一致のツール名。guidesの要約から必要な説明を選ぶ。next_cursorがあれば同じ絞り込みで続ける。

## 入出力と呼び出し経路

MCPの完全な入出力スキーマと、対応する文書HTTP操作はこのガイドのschemasに含まれる。section=schemasでも取得できる。HTTPのIf-MatchはMCPではetag、Idempotency-Keyはidempotency_key。既存文書ツールは業務引数をargumentsの中へ入れる。

ホストAPIはPOST /host/tools/list_tool_guidesに、信頼するホストの認証とuser_id・argumentsを送る。user_idをLLMから受け取って権限を決めない。ガイドGET APIの利用者別BearerとホストAPIの資格情報を混同しない。

## 副作用と結果の確認

文書の原本や索引を変更しない。説明・本文の取得成功は、外部処理の実行や書き込み成功を意味しない。閲覧コードの承認は短期の閲覧接続を作る。
