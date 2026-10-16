# 取得できないとき

isErrorとerror.code・error.statusを確認する。HTTPではerror.codeとHTTP状態を見る。

- 401: ホスト側で認証を更新。403・404 source_not_found: 利用範囲や削除を確認し、他利用者の参照を借りない。
- 409 source_changed・evidence_changed: get_document_statusから3つの版参照を取り直し、outline・表一覧からIDも取り直す。古い結果と新しい結果を混ぜない。
- 409 document_read_not_ready・preview_unavailable、503 document_read_unavailable: 処理状態・job・processor接続を確認。成功した抽出成果物がない場合や旧データの場合は、必要に応じ利用者の意図に沿って再抽出する。取得失敗だけを理由に無条件に再抽出しない。
- 404 document_table_not_found、422 invalid_document_range: 同じ版の一覧でIDと有効な範囲を確認。逆順、範囲外、非見出しへのsection指定を修正する。
- 422 page_image_unsupported: 対応形式の制約。本文や表を読む。
- 413 document_read_too_large: 一覧のlimitまたは本文・表の範囲を小さくする。結果は無言で切り捨てられない。
- 429・503: 接続や混雑状態を確認して待つ。読み取りなので同じ版参照で再試行できるが、版が変わった場合は先頭から取得する。
