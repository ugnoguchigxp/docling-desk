# 下書きの部分編集

基準本文と一致する箇所だけを一括編集する。

## 前提と引数の入手元

必要なactionはwrite。利用者とscopeは認証済み接続から確定する。get_document_draftのetag・sha256・bodyが必要。base_sha256にsha256を指定。expectedは完全一致が1件だけの原文。範囲指定のstart・endは0始まりUTF-8バイト範囲[start,end)。JavaScriptのUTF-16添字ではない。複数編集は同じ基準本文に対して指定し、不一致・重複があれば全件を適用しない。

## 入出力と呼び出し経路

MCPの完全な入出力スキーマと、対応する文書HTTP操作はこのガイドのschemasに含まれる。section=schemasでも取得できる。HTTPのIf-MatchはMCPではetag、Idempotency-Keyはidempotency_key。既存文書ツールは業務引数をargumentsの中へ入れる。

ホストAPIはPOST /host/tools/edit_document_draftに、信頼するホストの認証とuser_id・argumentsを送る。user_idをLLMから受け取って権限を決めない。ガイドGET APIの利用者別BearerとホストAPIの資格情報を混同しない。

## 副作用と結果の確認

この操作は保存または実行状態を変更する。対象と基準版を取得してから実行する。保存成功と検索反映を分けて確認する。
