# Docling Desk の改修検証と再評価

[全体計画](README.md)の20件は、部分検証、結合検証、通し検証の順に確かめる。合成入力・固定provider・隔離した保存領域を標準にする。実資料、実モデル、実Azure、Linux実機・Docker、初見の操作確認は、それぞれ条件と結果を別に記録する。

## 対象版と実施記録

R19aで、Git情報を持つ隔離コピーに未コミット変更も含めて固定する。利用者の原本・秘密情報・通常稼働中のデータ・生成物をコピー対象へ混ぜない。引き継ぐ環境変数は必要なruntimeとテスト設定に限定し、クラウド資格情報や通常環境の接続設定を外す。環境変数を記録するときも秘密の値は保存しない。既存依存やモデルを参照した検証と、新規導入の検証を区別する。ソース一覧、配布bundle、lockfiles、Docling revision、model manifest、入力データのハッシュを記録する。

実装着手後の証拠は `qa/refactoring/<source-digest>/<run>/` に保存する。これは今後の実行時に作る出力先で、今回の計画作成では検証済み出力を作らない。bulk出力をGitへ追加せず、追試に必要な小さな合成fixtureと契約だけを管理する。

各実行はコマンド、cwd、終了状態、OS・runtime・依存、model・provider設定、入力、結果、外部要求数、対象外を残す。キーや本文をそのままログへ記録しない。別の機能による同時負荷がある測定は正式な性能比較に使わない。

実施台帳は次の列を持つ。R01〜R20の20行を作り、R19の前半・後半とR20の一覧採取・確定は同じ行の工程欄で分ける。

| 項目 | 記録内容 |
| --- | --- |
| 状態 | 未着手、進行中、検証待ち、完了、阻害あり |
| 担当と対象版 | 実装・確認の担当、commitとソース識別値 |
| 契約と差分 | 維持する条件、意図した変更、必要な文書更新 |
| 検証証拠 | 部分・結合・通しの実行記録と受入条件の対応 |
| 残作業 | 未確認、環境不足、関連作業、次に変える条件 |
| 切り戻し | 使う差分または互換reader・バックアップ、必要な確認 |

## 基準測定の準備

G0で次の条件を固定する。値は検証設計の条件であり、達成済みの実績ではない。

| 領域 | 固定する条件 |
| --- | --- |
| 画面 | 1440×900、1024×768、390×844。browser版、倍率、font、言語、同じ合成入力 |
| 文書 | PDF、PPTX、XLSX、DOCX、Markdown、TXT、TeX、画像、draw.io、Excalidraw。正常・部分成功・未設定・失敗を含む |
| 一覧の取得 | 同じ件数・処理状態で60秒待機。可視、非表示、処理中、通信断、復帰、別クライアント更新 |
| 画面読込 | 空cacheでの新規browser contextと温かいcontextを分ける。OS全体が冷えているとは呼ばない |
| 保存と競合 | 図の基準版、変換の原本hashと試行、遅延位置、削除・差し替え・backup・終了を差し込む境界 |
| 検索 | 既存の合成100文書・10000チャンク・1024次元、既存の実HTTP小規模シナリオ。1000文書・8並列は別系列 |
| 意味品質 | ケース、正解根拠、答えがない条件、provider版・設定、prompt、予算を固定。ケースのreview_statusを確認 |
| 配布 | macOSのソース環境、空のLinux Docker状態、新しいwheel配置、非root、保存領域、model取得と通常時の通信 |

原本の表示領域とアプリ外枠を分けて比較する。R01〜R03による外枠の意図した差は、配置・用語・共通値の仕様で説明する。旧画像を一括で新画像に置換して合格にしない。原本内の文字位置、画像、ページ、表、倍率、選択範囲は既存の保持条件を使う。

既存の `pnpm test:visual` は保存済みfixtureと移行時の旧画面基準を前提にする。専用コピーに必要な入力がある場合に、出力先を分けて実行する。入力不足なら原本表示のvisual確認を未実施として残し、データを無断で通常環境から取らない。合成の操作・配置確認は標準のbrowser laneに追加する。古いUIで不可能な図編集などを旧UIとの画素比較へ押し込めない。

## 部分検証

変更した責務に関連する既存試験を先に使う。新しい検査は、保存・認可・取消・出典・操作回復など実害のある契約を対象にする。単純なコード移動、色の置換、実装と同じ条件をなぞるためだけのunit testは追加しない。

以下のパスとコマンドは現行の実行入口。実装による試験ファイルの移動は、強いassertionを保って更新する。すべて隔離コピーで、クラウド設定を読み込まない環境にして実行する。

| 対象 | 最初に実行する主な検査 |
| --- | --- |
| R01〜R05 | frontendのApp、共通UI、route、DocumentViewer、Viewerと該当featureのunit。Wiki・operations・printing・diagramの対象browser操作 |
| R06〜R08 | Knowledge、Library、Upload、formats、contracts、DocumentViewer。Pythonのupload security・text documents・drawio・diagram API等 |
| R09 | api・diagrams・base・contracts・mediaのunit、401・409・412・428・204・非JSON・取消の境界 |
| R10 R11 | Translation・Explanation・Knowledge・Library・PreviewCopy・Tablesと新しい対象の遅延・応答逆転・復帰。要求数と更新遅延 |
| R12 | `tests/test_translation_store.py`、`test_explanation.py`、`test_content_layout.py`、`test_diagram_store.py`、`test_quality_recovery.py`、`test_cov_backup.py` |
| R13 R14 | quality recovery・storage admission・distribution HTTP・upload security・translation queue/service・explanation・diagram store。追加の実プロセス境界検査 |
| R15 | APIのediting・fault-editing・api・admission・writer、OpenAPI一致、Pythonのdocument editor gateway・MCP連携 |
| R16 | explanation・cov_explanation_web・knowledge answers・semantic quality evaluator、送信と採用ID・token・呼出し数の契約 |
| R17 | 変更したeditor・diagram・viewerのunitと機能browser、build/export、frontend delivery、package、初回・温状態の計測 |
| R18 | health/deploy/env/models・distribution HTTPと起動時の診断、設定不足からの復旧browser、新規導入 |
| R19 R20 | verification入口のunit、distribution evidence、package、model integrity、inventory・告知・配布実体、CIの実行結果 |

frontendの部分実行例は `pnpm --dir frontend exec vitest run src/lib/api.test.ts src/lib/diagrams.test.ts`。Pythonの例は `DOCLING_ENV_FILE=/dev/null .venv/bin/python -m pytest tests/test_content_layout.py tests/test_quality_recovery.py -q`。Bunの例は `cd knowledge-api` で `bun test tests/editing.test.ts tests/fault-editing.test.ts`。実行範囲は変更した契約に合わせ、毎回すべての検査を重複実行しない。

## 結合検証の代表シナリオ

| ID | 操作と障害 | 確認すること | 主な対象 |
| --- | --- | --- | --- |
| V01 | 一覧から追加、フォルダー移動、一覧・ギャラリー切替、削除 | 対象・保存先・選択・更新が一致し、失敗から戻れる | R01 R03 R07〜R11 |
| V02 | Wiki閲覧、編集保存、検索、RAG、出典から本文・原本、戻る | 検索語・結果・位置・版を維持し、記事更新・削除後の古い根拠を拒否 | R01 R04〜R06 R09〜R11 |
| V03 | PDF・Officeの閲覧、表・構造・RAG、翻訳、解説、印刷 | ページ・シート・倍率・元の表示を保ち、共通外枠で原本を壊さない | R01〜R05 R08 R17 |
| V04 | 図を作成、描画・文字、保存、再読込、取込、原本DL、複製拒否 | 実canvasを操作し、受信原本・版・下書き・冪等性を保持 | R07 R09 R11 R12 R19a |
| V05 | 保存中の画面移動、通信断、応答順逆転、再接続 | 他の対象へ通知・フォーカス・データを誤反映せず、未確認保存を再送しない | R04 R09〜R11 R14 |
| V06 | 変換を待機点で止め、別の図を保存。その後変換を再開 | 全体排他待ちを短縮し、生成物・jobの公開順と世代を保持 | R12〜R14 |
| V07 | 変換中の削除・差し替え、公開直前直後のkill、再起動 | 原本改変・削除後の復活・旧試行の公開・混在した成果物がない | R12〜R14 |
| V08 | 書込み中backup拒否、停止後backup、空領域へrestore、検索・表示 | 原本・版・索引・削除状態が対応し、復元時に課金要求を送らない | R12〜R14 R20 |
| V09 | APIで本文・下書き・添付、確定前の権限失効・版競合・kill/replay | 認可・ETag・冪等性・公開契約、HTTP/MCPの一致 | R09 R15 |
| V10 | 多言語・言い換え・AI指示の説明・無関係な語一致のWeb候補 | LLMへ候補を渡し意味で採用。送信方針・SSRF・予算・ツール禁止を保持 | R16 |
| V11 | 空環境、モデル不在、Office・TeX・クラウド未設定、設定後再確認 | 利用できる機能と不足が一致し、既存health契約と秘密保持を維持 | R04 R08 R18 |
| V12 | 埋め込みで複数文書、root path、失効、狭い画面、ホスト再接続 | アプリ外枠・操作が混入せず、instanceごとの取得先と版を保持 | R01 R02 R05 R08 R09 R17 |

V06〜V08では、画面の成功表示だけでなく原本・manifest・job・DB・公開結果を照合する。公開境界でのプロセス終了は使い捨てデータにだけ行う。R14の枠は値を直接書き換えず、受付・拒否・復帰の実操作から確認する。

## 工程ごとの通し検証

G0は現状の失敗と成功を確定するために全体を実行する。G1・G2は影響する部分と結合を確認し、G3終了時に画面系、G4終了時に保存・API・復旧、G6終了時に配布を通す。G7は全項目が受入可能な同じ固定版の最終確認とする。未実装機能を先に通して発生する想定内の失敗を、新しい不具合として追わない。

Pythonの通しは次の入口を使う。

```sh
DOCLING_ENV_FILE=/dev/null .venv/bin/python -m pytest tests -q
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check src tests scripts
DOCLING_ENV_FILE=/dev/null .venv/bin/python -m docling_desk.operations.verify --with-js
```

既存verifyはPython全体・lint・JS等を含むため、個別実行済みの同じ検査を無目的に繰り返さない。R19で入口を整理した後は、同じ版の既存結果を参照できる記録を使うか、verifyを一度の通し実行として選ぶ。format checkが既存差分で失敗した場合も、対象外を一括整形しない。

画面とAPIは現在の入口を維持する。

```sh
pnpm --dir frontend test:run
pnpm --dir frontend typecheck
pnpm --dir frontend lint
pnpm --dir frontend build
pnpm --dir frontend export:static
DOCLING_ENV_FILE=/dev/null UI_SYNTHETIC=1 pnpm --dir frontend test:e2e:synthetic
```

`knowledge-api/` では `bun test`、`bun run typecheck`、`bun run lint`、`bun run contract:check` を実行する。実HTTP/MCPの取込・差し替え・読取り・編集の試験はPython側の既存live harnessを使い、実providerへの接続と混同しない。

buildとexportは完了を待ってから、その生成物を使うPython・browser・package検査を開始する。試験中に対象コード・bundle・fixture・設定を編集しない。並行作業の通常配信物を参照せず、browserの専用port・保存領域・report directory・固定bundleを指定する。

配布候補では `.venv/bin/python scripts/verify_package.py` によるsdist→wheel→チェックアウト外の起動・取込・検索・削除を実行する。Linuxの空cache・空volumeからDockerを構築し、非root、health、PDF・PPTX・XLSX・DOCX・Markdown・TXTと追加形式の対応範囲、HTTP/MCP接続、更新・復元を確認する。Docker未実行・実Azure未実行は、そのまま記録する。

## 性能と意味品質の判定

R10の要求数・検知時間、R13の別資料保存の待ち時間、R17の転送量・表示待ち時間を、同じ条件で前後比較する。R13は制御した変換待機を使う機構確認と、実変換中の体感時間を分ける。機構確認では、変換が待機点を解放する前に別資料の保存が完了することを要求する。

検索の既存入口は次のとおり。実行時のoutputは対象版別の証拠領域へ指定する。

```sh
DOCLING_ENV_FILE=/dev/null .venv/bin/python -m docling_desk.evaluation.search_bench --documents 100 --chunks-each 100 --dimensions 1024 --repeats 30 --output /tmp/search-benchmark.json
DOCLING_ENV_FILE=/dev/null .venv/bin/python scripts/benchmark_user_path.py --repeats 30 --output /tmp/user-path.json
DOCLING_ENV_FILE=/dev/null .venv/bin/python scripts/evaluate_semantic_quality.py --output /tmp/semantic-quality.json
```

これらの小規模合成結果だけで、1000文書・8並列・変換・印刷・UI・実意味検索の性能合格としない。[既存の利用条件](../technical-quality/usage-conditions.md)と、proposed扱いのシナリオにある予算の違いはG0で整理し、測定前にどの条件を採用するか記録する。予算未定の変換・印刷は初回分布を採取した後、最適化に入る前に予算を決める。

待ち時間のp50・p95・最大、メモリ、成功・空・失敗・取消・誤根拠、外部要求、cache状態を分ける。画面の測定は各条件30標本以上を基本にするが、回数だけで分布の安定性を主張しない。同一版の測定を他のbuildや重い検証と同時に走らせず、悪化が測定ばらつき以内か確認する。

R16の意味評価は、固定ケースの契約検証と実モデル評価を分ける。現在の下書き20ケースは正解根拠・質問・答えの有無を確認してから採用する。関連語を含む無関係文、別言語で正しい説明、引用中の指示、公開規格、送信してはいけない文字列を含む入力を使う。個別ケースの名前や語句を製品コードへ追加して通さない。

実接続で使うprovider、認証、送信許可、対象資料、料金・回数・時間の上限を実行前に設定する。未設定のまま実モデル検証へ進まない。fakeの合格を意味品質と呼ばず、実モデルが未実施なら当該品質の評点を保留する。

## 一体感と導入負担の確認

改修前後で同じ代表作業を行う。資料を追加して根拠を確認、Wiki編集後に検索、検索から原本へ移動して戻る、図の保存と再開、設定不足から復旧、の5作業を使う。G0で資料と課題文を固定し、対象画面の操作に詳しくない利用者による確認機会を用意する。

初期の確認は可能なら3名程度で行い、人数を確保できなければ実人数と経験を記録する。人数自体を合格条件にせず、全員の完了・支援・誤操作・詰まった場所・所要時間・説明負担を残す。前後で別の人を使う場合と、同じ人が学習した場合の違いも明記する。

受入条件は、主要操作の位置と用語が規則どおりで、手順書の個別指示に頼らず完了・復旧できること。データ損失につながる誤操作がなく、改修による重大な操作退行がないこと。少人数の結果を普及率・継続利用・市場需要の実績へ拡張しない。利用者による確認ができなければ操作の自動試験は残し、利用効果は未確認とする。

## 最終判定と切り戻し

各R項目は、受入条件、維持すべき契約、関連差分、必要な実行、切り戻しを確認して完了とする。新しい共通機構が一つの具体例だけに依存していないか、LLMの意味判断を条件分岐や辞書で代替していないかを差分で点検する。担当外・無関係な変更も含めて確認するが、勝手に修正しない。

取消後公開・認可逸脱・データ損失・復元不能が出た工程は先へ進めない。UIの切り戻しは対応ソースとbundleを一組で戻す。既存のlegacy UI切替は図編集等の全機能を代替する保証がないため、共通の復旧手段とはしない。保存形式が変わる変更はR13で定めた互換readerか移行前バックアップを使う。

G7ではrubric 1.1.0、full/deep、desktopを維持し、技術30項目と価値・将来性30項目の実績台帳を別に作る。前回のCurrent台帳を上書きせず、新しい対象版と受入証拠を持つRe-evaluatedとして計算する。技術の正式点は全適用項目が評価できる場合だけ、将来性は採点に必要な実利用の証拠がある場合だけ示す。

20件の完了数、合格したテスト件数、コード削減量を点数へ換算しない。構造・操作・性能・復旧・配布・利用効果のどの条件が改善したかで判定する。80点を超えなければ、実際の評点と不足する証拠・能力を示し、目標達成として終了しない。
