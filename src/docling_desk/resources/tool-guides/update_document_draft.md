# 下書き全文の更新

共有下書きの原文を全文更新する。

## 前提と引数の入手元

必要なactionはwrite。利用者とscopeは認証済み接続から確定する。get_document_draftのetagが必要。bodyは原文。metadata・attachmentsは変更する場合に指定。更新後のetag・sha256を次へ渡す。確定済み下書きは変更せず、新しい下書きを作る。

## 入出力と呼び出し経路

MCPの完全な入出力スキーマと、対応する文書HTTP操作はこのガイドのschemasに含まれる。section=schemasでも取得できる。HTTPのIf-MatchはMCPではetag、Idempotency-Keyはidempotency_key。既存文書ツールは業務引数をargumentsの中へ入れる。

ホストAPIはPOST /host/tools/update_document_draftに、信頼するホストの認証とuser_id・argumentsを送る。user_idをLLMから受け取って権限を決めない。ガイドGET APIの利用者別BearerとホストAPIの資格情報を混同しない。

## 副作用と結果の確認

この操作は保存または実行状態を変更する。対象と基準版を取得してから実行する。保存成功と検索反映を分けて確認する。
