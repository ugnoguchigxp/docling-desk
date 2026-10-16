# 下書きの確定

共有下書きを文書の新しい保存版として確定する。

## 前提と引数の入手元

必要なactionはwrite。利用者とscopeは認証済み接続から確定する。etagは最新の下書きetag、source_etagは基準文書のetag。新規文書ではsource_etag:nullを明示。両方の競合を検査する。保存結果のsource_id・source_revision・source_etag・sha256・job_idを保持。索引失敗で保存済み本文は取り消されない。

## 入出力と呼び出し経路

MCPの完全な入出力スキーマと、対応する文書HTTP操作はこのガイドのschemasに含まれる。section=schemasでも取得できる。HTTPのIf-MatchはMCPではetag、Idempotency-Keyはidempotency_key。既存文書ツールは業務引数をargumentsの中へ入れる。

ホストAPIはPOST /host/tools/commit_document_draftに、信頼するホストの認証とuser_id・argumentsを送る。user_idをLLMから受け取って権限を決めない。ガイドGET APIの利用者別BearerとホストAPIの資格情報を混同しない。

## 副作用と結果の確認

この操作は保存または実行状態を変更する。対象と基準版を取得してから実行する。保存成功と検索反映を分けて確認する。
