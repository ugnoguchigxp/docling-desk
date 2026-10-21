# R13 変換中の排他区間の短縮 設計確認

[実装計画のR13](implementation-plan.md)の手順1「設計確認」。実装前に書き、手順2〜6の各条件と受入条件を末尾で自己点検する。判定は「実装可（go）」。

## 現状

- 変換の呼出し元は `api/uploads.py` の `run_job` だけ。ローカル資料は `publish_upload` が原本・`content/documents/<id>/manifest.json`（SHA-256）・`runtime/documents/<id>/job.json`（queued）・ライブラリ登録を作ってから、枠を持つ1スレッドのexecutorへ投入する。再抽出の呼出し元はない。
- `convert_job` は `exclusive_write(data, "convert")` を計算全体で保持する。同じ排他を取る図の保存、ライブラリ操作、索引更新、アップロード公開、バックアップ（wait=0で拒否）が変換時間だけ待つ。
- 変換は `derived/documents/<id>/` へ直接 `document.json` など多数のファイルとプレビューのディレクトリを書き、最後に `save_job`（`manifest.json`、次に `job.json`）で成功を公開する。
- 成果物の読み手は `job.json` の状態（success/partial）でゲートされる。例外は `GET /api/jobs/{id}/assets/...`（状態を見ずフォルダー内のファイルを返す）だけで、計算中の書きかけファイルを返しうる。
- `knowledge/worker.py` の抽出は別プロセスで `convert_job` を呼ぶが、出力先は試行ごとに一意の `sources/<id>/<rev>/attempts/<job>-<run>`（アプリのデータルートとは別の木、`storage.managed()` は偽）。

## 公開方式の結論

**世代単位は導入しない。** 理由は次の2点で、どちらかが崩れたら世代単位が必要になる。

1. 変換が作るのは、まだ公開されていない新規資料の最初で唯一の世代であり、公開済み成果物を置換しない。再抽出の経路は存在しない。
2. 全ての読み手（preview/translation/explanation/viewer/document_reads/知識索引の `_sync_documents`）は `job.json` の状態で成果物を読むか決める。`job.json` の置換は単一ファイルの原子的な切替で、既存の公開順序（成果物 → `manifest.json` → `job.json`、Blob同期も `job.json` と manifest を後に回す）をそのまま再利用する。

したがって「一時領域で完成させた成果物を、`job.json` が未公開（running）の間に資料フォルダーへ移し、最後に `job.json` を切り替える」。移動は同一ファイルシステム内のrename（`os.replace`）で、ディレクトリは丸ごとの名前替え。管理下の資料（`derived/documents/<id>`）に対し、既に success/partial の資料への変換は拒否する（上書き公開が必要になる再抽出は、その時点で世代単位を設計する）。

assets API が公開前の移動済みファイルを返す点は、rename済みの完全なファイルだけが見え、書きかけは見えなくなる（現状より改善）。

保存形式は変えない（Job、manifest、job.json、成果物の中身・名前は同じ）。追加するのは再生成可能な一時物だけ。

| 追加物 | 場所 | 内容 | 寿命 |
| --- | --- | --- | --- |
| 活動ファイル | `runtime/activity/<attempt_id>.lock` | 所有プロセスが `flock(LOCK_EX)` を保持。中のJSON（pid、目的、資料ID、開始時刻）は診断用で判定に使わない | 変換の公開確定または失敗確定まで。クラッシュ時はカーネルが解放 |
| 作業領域 | `runtime/conversions/<attempt_id>/` | 原本のコピーと変換出力 | 公開確定または失敗確定後に削除。残留は起動時と次の変換開始時に削除 |
| 試行記録 | `runtime/documents/<id>/attempt.json` | 現在の試行ID、原本SHA-256、公開時に移す名前の一覧 | 公開確定後に削除。資料削除で一緒に消える |

`runtime/` はBlob同期の対象外（`job.json` と `explanation.sqlite` のみ）。バックアップは `runtime/activity` と `runtime/conversions` を載せない（試行記録は小さな実行時ファイルとして載るが、復元後は起動時に失効扱いで掃除する）。

## 時系列

登場人物: A=入力確定、B=計算、C=公開、D=後処理。lockは `exclusive_write(data, "convert")`。

| 段階 | 実行 | lock | 書く場所 | 状態 |
| --- | --- | --- | --- | --- |
| 受付 | `publish_upload` が原本、manifest、queuedのjob.json、ライブラリ登録を作り、枠付きで投入 | exclusive + library.LOCK（既存） | content、derived、runtime | queued |
| A 入力確定 | 試行ID（uuid4）を採番。資料フォルダー・job.jsonが現存しstateがqueued（または生存試行のないrunning）、原本が通常ファイルで symlink でないことを確認。原本の SHA-256 を manifest から読み、stat（サイズ・mtime）とともに固定。活動ファイルを作り flock を取得。`attempt.json` を書き、`job.json` を running にする | exclusive（短い） | runtime | running |
| B 計算 | 作業領域へ原本をコピーしつつSHA-256を再計算し固定値と照合。`_convert_job` を作業領域に対して実行（Docling、OCR、プレビュー、RAG）。OCR台帳とキャッシュは従来通り実際の資料フォルダー下 `ocr-private` に置く（成否不明の再送禁止の記録を失わないため）。OCR呼出しの `live` は実際の資料フォルダーの原本の存在で判定 | なし（活動のflockのみ） | `runtime/conversions/<attempt>` | running |
| C 公開 | 再確認（下記）→ `attempt.json` に移す名前を記録 → 成果物をrenameで資料フォルダーへ → `manifest.json` → `job.json`（success/partial/failed）。失敗した変換は成果物を移さず failed の `job.json` だけを書く | exclusive（短い。時間は成果物の数に比例し、変換時間に比例しない） | derived、runtime | success/partial/failed |
| D 後処理 | `attempt.json` を削除、作業領域を削除、Blob通知（`notify()`）、活動ファイルを閉じて削除 | notify は lock の外 | - | 確定済み |

公開時の再確認（Cで lock 内）:

1. 資料フォルダーと `job.json` が存在する（削除されていない）。
2. `job.json` の状態が running（回復処理や取消が failed にしていない）。
3. `attempt.json` の試行IDが自分と同じ（旧試行の遅延完了でない）。
4. 原本が同じパス・通常ファイル・symlinkでなく、stat（サイズ・mtime）と manifest の SHA-256 が固定値と一致する。

1か3が崩れたら何も書かず作業領域を捨てる（呼出し側のメモリ上の job だけ failed にする）。2が崩れた場合も同様。4が崩れたら、自分が現在の試行なので failed（原本が変更されたため公開しない）を記録する。

## 各境界で何が観測するか

| 観測者 | A前 | A〜C（計算中） | C内 | C後 |
| --- | --- | --- | --- | --- |
| 図の保存、ライブラリ操作、索引更新、新規アップロード | queued | **ブロックされない**（A/Cの短い区間だけ待つ） | 待つ | 完了後 |
| 資料一覧・状態取得 | queued | running | 成果物が一部移動済みでも job.json は running | success/partial/failed |
| 読み手（preview/translation/explanation/viewer/document_reads/知識索引） | 状態でゲート。読まない | 読まない（成果物は資料フォルダーになく、状態も未成功） | 知識索引は同じ exclusive を取るので待つ。他は状態で読まない | 完全な1世代 |
| assets API | 何もない | 何もない（作業領域は対象外） | rename済みの完全なファイルのみ | 完全な1世代 |
| 削除（ライブラリ） | 状態が queued/running のため409で拒否（既存の契約） | 同左 | exclusive で待つ | 通常の削除 |
| 複製 | 同上 409 | 同上 | 待つ | 完全な1世代を複製 |
| バックアップ | lock 内で活動を走査。生存した活動がなければ取得 | **活動のflockが取れず拒否** | lock で拒否 | 活動終了後は取得できる |
| 復元 | 空領域へ。アクティビティは載らない | - | - | job.json が success のものだけが成果物として現れる |
| Blob同期 | `job.json` と manifest は成果物の後に送る（既存の順序） | 作業領域は対象外 | 成果物の一部と running の job.json が観測されうる | `notify()` 後の次の同期で success の job.json が最後に送られる |
| 起動時回復 | 失効した活動・作業領域・試行記録を掃除。公開途中で死んだ試行の移動済み成果物を削除。running の job は既存処理が failed にする | 同左 | 同左 | 何もしない |

## ロック取得順

1. `exclusive_write(data, ...)`（スレッドのguard → `.write-lock` の flock。他者の処理が終わるまで待つ。バックアップは wait=0 で取得し、取れなければ拒否）。
2. 活動のflock（`runtime/activity/<attempt>.lock`）。A では exclusive の内側で、新規の一意なファイルに非ブロッキングで取る。バックアップは exclusive を保持したまま既存の活動ファイルへ `LOCK_NB` を試す。どちらも待たない。
3. `library.LOCK` などの下位ロック。変換自身は取らない。

活動の取得・走査は待たないので、exclusive 保持者が活動を待つ経路はなく、循環しない。活動の登録は必ず exclusive の内側なので、バックアップの走査と登録は同時に起きない。B の間に保持するのは活動のflockだけで、exclusive は保持しない。C は exclusive を取り直す（活動は保持したまま。活動を保持している側が exclusive を待つだけで、exclusive 保持者は活動を待たない）。

## 活動管理の契約（R14の契約への対応）

1. 単位: (データルート, 試行ID) ごとに1ファイル。
2. 範囲: A（exclusive 内）で登録し、B と C の間保持し、公開確定または失敗確定の後に閉じる。停止要求では閉じない。`finally` で1回だけ閉じる。
3. 生存の根拠: ファイルの `flock`。pid確認、メモリ上の件数、`SlotPool.in_use` は使わない。バックアップは exclusive を wait=0 で取り、活動ディレクトリの各ファイルに `LOCK_NB` を試す。取れなければ進行中として拒否、取れれば所有者は終了済みとして削除する。
4. 別プロセス: 登録は `convert_job` の内側で、アップロードを処理するアプリのプロセスでも、データルート配下の資料を変換する別プロセスでも同じ。`knowledge/worker.py` の抽出先は別の木（管理下でない私的な試行フォルダー）で、公開先も他の書込みも共有しないため、従来通り私的フォルダー全体の排他で実行し、活動は登録しない（バックアップの対象はこの木にない）。
5. 起動時: `storage.migrate_layout`（`app.py` が exclusive 内で呼ぶ）の末尾で失効した活動ファイル・作業領域・試行記録を掃除する。変換開始時にも同じ掃除を軽く行うので、掃除が呼ばれなくてもバックアップが永久に拒否されることはない（拒否の根拠はflockだけ）。
6. 非対応: `fcntl` が使えないOSは既存のバックアップ排他と同じく対象外。ディレクトリ同期は `atomic_files.sync_directory` を使い、使えない環境ではスキップされる。fork した子が活動ファイルのfdを継承して生き残る場合は残留に見えうる（既存の `.write-lock` と同じ前提）。

## 公開途中のクラッシュ回復

試行記録に、これから移す名前（`manifest.json` を含む）を移動の前に記録する。公開途中で死んだ試行は、起動時の掃除で次を行う。

- 試行の活動が失効している。
- `job.json` が success/partial で `manifest.json` がある → 公開済み。試行記録だけを削除。
- そうでなければ、記録された名前を資料フォルダーから削除し（パスは資料フォルダー内に限定、symlinkは辿らない）、試行記録を削除。`job.json` の running → failed は既存の起動処理が行う。

公開前に死んだ試行（計算中）は、作業領域と試行記録と活動ファイルを削除する。資料フォルダーには `ocr-private` 以外の成果物がない。

## 移行・切り戻し

- 保存形式の変更なし。移行は不要。旧コードは `runtime/activity`、`runtime/conversions`、`attempt.json` を読まず、残っていても無害（資料削除は `runtime/documents/<id>` を丸ごと消す）。
- 切り戻し: `convert_job` の管理下資料の分岐を外し、従来の `with exclusive_write(...): _convert_job(...)` に戻す。バックアップの活動走査と除外を外す。残った一時物は削除するか放置してよい。
- 変換中に旧コードへ戻した場合の挙動: 旧コードは長い排他を取るので、新コードの公開区間とは排他を介して直列になる。計算中のものは旧コードのバックアップに対して保護されない（活動を見ないため）。切り戻しは変換の実行中でない時に行う。

## 手順2〜6と受入条件の自己点検

| 条件 | 設計 | 判定 |
| --- | --- | --- |
| 手順1 時系列・観測境界・ロック順 | 上記3節 | 満たす |
| 手順2 原本ID・SHA-256・試行IDを入力時点で固定 | A で manifest の SHA-256、stat、試行IDを固定し、作業領域コピーのSHA-256と照合 | 満たす |
| 手順2 重い変換は対象外の一時領域、グローバル書込み排他を占有しない | B は `runtime/conversions` と flock のみ | 満たす |
| 手順2 原本が消えたり変わったりした後は公開しない | C の再確認 1・4 | 満たす |
| 手順3 公開方式の確定 | 既存の不変性（新規資料の最初の世代）と `job.json` 切替を再利用。世代単位は不要で、必要になる条件を明記 | 満たす |
| 手順3 flat置換だけの非原子性 | 公開済みを置換せず、読み手は `job.json` でゲート。assets API は完全なファイルのみ | 満たす |
| 手順4 形式影響・旧reader・段階移行・中断回復・戻し方 | 形式変更なし。中断回復と戻し方を記載 | 満たす |
| 手順5 公開前後だけ共有排他、現在の原本・実行世代・削除・取消を再確認 | A と C のみ。再確認 1〜4 | 満たす |
| 手順5 job完了は公開成功の後、Blob通知は公開確定後 | C の最後に job.json、D で notify | 満たす |
| 手順6 計算中のバックアップは拒否を維持、書込みlockとは別の最小の活動管理、クラッシュ後に永久拒否しない | flock 活動ファイル。LOCK_NB 走査。kill 後に取得できることを実プロセスで検証 | 満たす |
| 受入 別の図の保存が長い変換中に完了し、待機が変換時間に比例しない | 変換を待機点で止めて測定 | 検証する |
| 受入 削除・差替え・旧試行の遅延完了・公開境界kill・backup/restore・Blob通知で世代が混ざらない | C の再確認、移動前の記録と起動時回復、publication rank | 検証する |

阻害条件: 見つからなかった。ただし次の残余を報告に含める。

- 起動時の job 回復（`app.py`）は、別プロセスで生存中の変換があっても running を failed にする。活動の生存確認を使えば避けられるが `app.py` は本項目の変更範囲外なので、後続の統合事項とする。公開側は試行記録と状態の照合で、この場合も旧試行の公開を拒否する。
- Blob同期（derived を有効にした場合）で、公開の瞬間に走査と `job.json` の読込みが重なると、success の `job.json` が未走査の成果物より先に送られうる。`job.json` の mtime による猶予と、公開直後の `notify()` による再走査で自己修復する短い窓として残る。
