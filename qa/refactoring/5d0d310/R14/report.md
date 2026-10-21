# R14 バックグラウンド実行の共通部分 報告

対象版: 5d0d310 の上の未コミット変更。状態: 実装済み、対象試験は成功、全体試験は呼出し側で実施。

## 共通部品 `src/docling_desk/execution.py`

- `SlotPool(size)`: 非ブロッキングの受付枠。`claim()` は枠を `SlotLease` として返し、満杯なら None。`acquire(blocking=False)` と `release()` は既存試験の互換用の生の操作。`in_use` はこのプロセスの件数であり、受付上限と試験にだけ使う。
- `SlotLease`: 状態は claimed -> submitted -> released、または claimed -> released。解放は `release()` だけが行い、一度しか効かない（戻り値は実際に返した呼出しだけ True）。
  - `submit(executor, fn, ...)`: executor が拒否したら枠を返して例外を伝え、受理済みを残さない。成功後は fn の終了（正常・例外）か、待機中のfutureが取り消された時に返る。解放済みの lease への submit は `LeaseReleased`。
  - `abandon()`: 未submitの枠だけ返す。リクエスト処理の全終了経路（取消含む）で呼ぶ。submit済みは実行側の所有で触らない。
  - `request_stop(cancel)`: 停止要求。`stop_requested` を立て、cancel フックを呼び、待機中ならfutureを取り消す（その場合だけ枠が戻る）。実行中なら枠は戻さず、fn が返るまで保持する。
- 単一の状態enum、自動retry、状態保存は持たない。再開・中断・成否不明は各Managerが保持する。

## 機能別の終了表

「枠」は SlotPool の枠。lease 導入後の動作を示す。（旧）は変更前に問題があった箇所。

| 機能 | 枠取得 | submit失敗 | 開始 | 正常終了 | 例外 | 待機中取消 | 実行中停止 | close | 同一プロセス再起動 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| アップロード変換（app.py / api/uploads.py。executor 1、枠3） | 変換が要る形式だけ満杯なら429。画像・図は枠を使わない | 500。資料フォルダを巻き戻し枠を返す。受理済みは残らない | `run_job` が `convert_job` を開始。OCR の成否不明方針は `documents/` 側 | `run_job` 終了で枠が戻る | `convert_job` の例外でも戻る。ジョブ失敗の記録は conversion 側 | lifespan終了の `shutdown(cancel_futures=True)` で取り消されたfutureの完了通知が枠を返す（旧: 破棄されるプールに任せた） | 停止を要求する経路なし。終了まで枠を保持 | 待たない。実行中の変換は継続し枠はそれが終わるまで残る | lifespan ごとに新しい executor と SlotPool。次の起動時に queued/running の job.json を failed にする既存処理は維持 |
| 上記の登録後の失敗 | - | submit の後に失敗しうる処理を置かない（旧: submit後の `decorate` 失敗で巻き戻し、実行側と二重解放）。公開応答の再読込が失敗しても受理済みなので 202 を返す | - | - | 想定外の例外（KeyError等）でも `abandon()` で枠を返す（旧: OSError/RuntimeError/ValueError/HTTPException以外は漏れた） | - | - | - | - |
| 翻訳（translation/service.py。worker 1、枠3） | `LOCK` 内で、provider の preflight と終了中判定の後に取得。満杯は `queue_full` | 各単位を failed(storage) にし枠を返す。再送なし | `_run` が `running` に更新後 provider を呼ぶ | `_run` 終了で戻る | 単位ごとに failed、認証等は残りを failed にして中断。枠は `_run` 終了で戻る | `close()` は `cancel_futures=False`。待機中も走り、interrupted を保存して終了 | `stopping` と provider.cancel。provider が戻るまで枠を保持し、戻った結果は interrupted にする | stopping を立て executor を shutdown(wait=False) | 起動時の `interrupt_pending`（既存）で残った進行中を interrupted にする |
| 解説（explanation/service.py。executor 1、枠3） | `LIBRARY_LOCK`+`LOCK` 内で preflight・容量確認の後に取得。満杯は `queue_full`（resume_pending は待って再試行） | state を「受付失敗」に更新し枠を返して例外を伝える | `_run` が現在 provider を登録して実行 | `publish` が attempt 照合に成功した時だけ公開、枠は `_run` 終了で戻る | 現在の attempt が active の時だけ failed/interrupted を保存。枠は戻る | `close()` は `shutdown(wait=False)`。待機中は走って interrupted を保存 | `stop` と provider/web の cancel。終了まで枠を保持。旧 attempt は公開直前の attempt 照合で拒否 | stop 設定、cancel、shutdown(wait=False) | `explanation_recover` と `resume_pending` が interrupted のものだけ再開（既存の再開方針） |
| 解説の受付途中の失敗 | - | - | - | - | チェックポイント読込を `try` の外で行っていたため、取得後の読込失敗で枠が漏れた（旧）。`try` 内に移し lease を返す | - | - | - | - |
| Wiki AI 編集（knowledge/editor_ai.py。解説Managerの executor と枠を共有） | 解説Managerの枠。満杯は429、停止中は503 | preflight/生成準備/submit の失敗で枠を返し、503または元の例外 | `complete` が provider を `manager.current` に登録して実行 | future 完了で戻る | 同左（SSEでエラー表示） | ブラウザ切断で `request_stop(provider.cancel)`。待機中ならfutureが取り消され枠が戻り provider は開始しない | cancel は要求のみ。実行中の provider が戻るまで枠を保持 | 解説 `close()` に従う。`stop` が立てば中断エラー | 解説Managerの再作成に従う |

## 設計判断

- executor の差し替え試験（`SimpleNamespace(submit=lambda fn, *a: fn(*a))` や Mock）と互換にするため、lease.submit は executor.submit の戻りが Future でなくても動く。既存の `app.state.worker`, `app.state.slots`, `manager.worker`, `manager.executor`, `manager.slots` の属性名は維持。
- 公開前の旧試行拒否と停止確認は機能側（解説の `publish` の attempt 照合、翻訳の `_change` の attempt/source_hash 照合）に残した。
- OCR の成否不明（`documents/`・`knowledge/worker.py` の ledger）、解説の再開、翻訳の中断はそれぞれ別方針のまま。共通化したのは枠の所有と解放だけ。

## R13 向け 活動の寿命（契約）

R13 は変換の計算中に全体書込み排他を持たない。そのため、バックアップ拒否の根拠は排他とは別の「進行中の変換」の記録になる。R14 はこの記録自体は実装しない（R14 の枠は同一プロセス内の受付上限であり、根拠にならない）。次を R13 の設計確認で満たすこと。

1. 単位: 活動は (データルート, 実行試行 attempt_id) ごとに一つ。対象の原本ID・SHA-256・種別（convert など）を記録する。
2. 範囲: 入力確定の排他区間の内で登録し、計算・一時成果物作成・公開の間ずっと保持し、公開確定（または失敗・取消確定）の後で終える。通知（Blob）は公開確定後なので活動の外でよいが、公開より前に終えてはならない。
3. 生存の根拠はプロセス間で効く仕組みにする。メモリ上の件数、`SlotPool.in_use`、pid 生存確認だけでは使わない。推奨は、既存の `operations/backup.py` の `exclusive_write` と同じ `flock` を使い、活動ごとに専用ファイル（例: データルート内の活動ディレクトリに `<attempt_id>.lock`）を作り、所有プロセスが `LOCK_EX` を保持する方式。バックアップは書込み排他を `wait=0` で取った状態で活動ディレクトリを走査し、各ファイルに `LOCK_NB` を試みる。取得できなければ進行中として拒否する。取得できれば所有者は終了済みなので残留として無視（削除）する。これによりクラッシュ後も永久拒否にならない。ファイル内の JSON（pid、目的、attempt_id、開始時刻）は診断用で、判定には使わない。
4. 一度だけ終える: 活動の終了は lease と同じ形（finally で一回だけ、二回目は何もしない）にする。例外・取消・停止要求のどれでも、実際に計算が止まり公開可否が確定するまで終えない。停止要求だけで終えてはならない（非協力的な provider や子プロセスが書き込む可能性がある）。
5. 別プロセスの経路: 変換は app プロセス（アップロード）と `knowledge/worker.py`（別プロセス、`convert_job` を直接呼ぶ）の両方で走る。したがって活動の登録は `convert_job` の内側（共通の入口）で行い、呼出し元のプロセスに依存させない。
6. 起動時: 起動時の job 回復（queued/running を failed にする）と同じ箇所で、残留した活動記録（flock が取れるもの）を片付ける。
7. 非対応: `fcntl` が使えないOSは既存のバックアップ排他と同じく対象外。同期が使えない場合の扱いは R13 の設計確認で明記する。
8. 受入観測: 活動を保持したまま別プロセスから backup が拒否されること、所有プロセスを kill した後に backup が成功すること（kill は使い捨てデータだけ）。

## 検証

- 新規 `tests/test_execution_slots.py`（17件）: 共通部品の単体（例外、submit失敗、待機中取消、実行中の停止要求、abandon、解放済みへのsubmit）、アップロード（submit失敗の巻き戻しと枠、想定外例外、登録後失敗で巻き戻さず二重解放しない、lifespan終了で待機中の枠が戻り再起動で新しいプール）、翻訳（submit失敗で受付が残らず provider 呼出し0回、停止要求後も非協力的 provider が戻るまで枠を保持し再送なし、例外後の枠）、解説（submit失敗、取得後のチェックポイント読込失敗、停止要求後も provider が戻るまで保持し旧 attempt が公開・上書きできない、上限）。
- `tests/test_library.py::test_upload_into_folder_and_duplicate_name`: worker のダミーが旧 `submit(fn, path, job, slots)` 形に依存していたため、`convert_job` を差し替えて worker をインライン実行するダミーにし、枠が戻ること（`in_use == 0`）の検証を加えた。
- 対象試験: test_execution_slots / translation_queue / translation_service / quality_recovery / storage_admission / wiki_editor_ai / upload_security / explanation / distribution_http / library / drawio / word / tex_preview / text_documents / media_library / translation_api の合計で成功。ruff check と ruff format --check も成功。
- 全体試験（参考）: 1053 passed、2 failed。1件は上記の test_library（修正済み）、もう1件 `tests/test_cov_backup.py::test_verify_commands` は他作業者の `operations/distribution_evidence.py` の変更によるもので R14 の範囲外。

## 未実施・依頼

- 活動記録の実装は R13 に委ねる（上記契約）。
- 旧版へ戻す場合は各Managerの接続（`SlotPool.claim`/`lease.submit`）を `BoundedSemaphore`/`executor.submit` に戻す。保存形式の変更はない。
