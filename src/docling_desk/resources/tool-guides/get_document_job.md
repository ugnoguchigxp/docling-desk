# 文書処理の状態確認

抽出・削除等の永続ジョブを確認する。

## 前提と引数の入手元

必要なactionはread。利用者とscopeは認証済み接続から確定する。登録・確定・再試行が返したjob_idを使用。stateと失敗code、source_revisionを確認。completedだけで意味検索準備完了と判断せず、文書のfts_ready・semantic_readyも区別する。短い間隔で無制限に繰り返さない。

## 入出力と呼び出し経路

MCPの完全な入出力スキーマと、対応する文書HTTP操作はこのガイドのschemasに含まれる。section=schemasでも取得できる。HTTPのIf-MatchはMCPではetag、Idempotency-Keyはidempotency_key。既存文書ツールは業務引数をargumentsの中へ入れる。

ホストAPIはPOST /host/tools/get_document_jobに、信頼するホストの認証とuser_id・argumentsを送る。user_idをLLMから受け取って権限を決めない。ガイドGET APIの利用者別BearerとホストAPIの資格情報を混同しない。

## 副作用と結果の確認

文書の原本や索引を変更しない。説明・本文の取得成功は、外部処理の実行や書き込み成功を意味しない。閲覧コードの承認は短期の閲覧接続を作る。
