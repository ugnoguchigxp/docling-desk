# 通信断と競合からの復旧

1. 保存操作のキー・操作種別・対象・要求内容を送信前に保持する。
2. 応答だけを失ったらget_document_operationへ同じkeyを渡す。commit等のtarget_idはdraft_id、metadataはsource_id。新規作成はtarget_id省略。
3. 確定結果が返れば保存版とjob_idを保持し、同じ保存を作り直さない。
4. operation_unknownは保持期限や利用者の違いもあり得る。未実行と判断せず、現在の文書・下書きを確認する。
5. 412は最新etag、409はcodeと原文・sha256を確認。元の入力を維持してLLMが編集内容を見直す。
6. 保存後の索引失敗はget_document_jobで段階を確認し、再試行可能な現在版の失敗ジョブだけretry_document_jobを使う。

認証失効・scope外はホスト側の設定と利用者対応を確認し、ツール引数で権限を広げない。同じ原因の再送を無限に繰り返さない。
