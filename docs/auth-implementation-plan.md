# LARM連携の認証と本運用前修正の実装計画

作成日：2026年10月24日。目的は、LARM上でDocling Desk本体を起動し、EumenesのbackendとUIから`LARM_API_TOKEN`だけで使えるようにすることと、本運用前評価のP0/P1を解消すること。LARMへのサービス登録、Eumenes側の中継・lease管理、データ移行、処理中のlease延長はこの計画の対象外とし、LARMで起動してから決める。

## 1. 前提とした既存契約

- Eumenesは`resolveLarmToken()`で`LARM_API_TOKEN`、空なら`LARM_CONTROL_TOKEN`を読み、backend内だけで使う。
- `LARM_API_TOKEN`だけで動く既存例はOCR・物体認識。保存済みgateway URLへ`Authorization: Bearer <token>`を直接送り、lease APIは使わない（`eumenes/docs/ocr.md`）。
- ExcalidrawはLocal Servicesのleaseに専用consumer・app tokenを使い、`LARM_API_TOKEN`と同じ値を拒否する（`eumenes/api/application/server-media.ts`）。Docling DeskはOCR方式の認証に合わせ、UIの埋め込みだけExcalidrawを参考にする。

## 2. 認証の仕様

| 起動時の環境 | 動作 |
| --- | --- |
| `LARM_API_TOKEN`または`LARM_CONTROL_TOKEN`があり、`DOCLING_AUTH_MODE`が未指定 | `larm`認証を自動で有効にする |
| トークンなし | 従来どおり`none`（ループバックのみ） |
| `DOCLING_AUTH_MODE`を明示 | 明示値を優先する。トークンがあるのに`none`なら起動ログで警告する |

- Bearerは定数時間で比較する。24文字未満のトークンでは起動しない。値はログ・診断・応答へ出さない。
- 正しいBearerの要求だけ、`X-Forwarded-Prefix`を配信パスの接頭辞として採用し、Host・Origin検査を通す。ブラウザーはBearerを付けられないため、DNS rebindingとCSRFの防御は維持される。Bearerで中継する経路では`DOCLING_ROOT_PATH`と`DOCLING_ALLOWED_HOSTS`の追加設定は不要。ブラウザーがCookieで別のHostやhttpsの入口から直接開く場合だけ、その入口を`DOCLING_ALLOWED_HOSTS`・`DOCLING_ALLOWED_ORIGINS`に加える。チケット発行時の接頭辞はセッションへ署名付きで記録する。
- ブラウザー用には使い捨てチケットを交換してセッションCookieを発行する。
  - 端末で起動したときは、ログイン済みで開けるURLを1回表示する（10分・使い捨て）。
  - Eumenes等はBearer付き`POST /auth/ui-tickets`で60秒・使い捨てのURLを得る。
  - Cookieの署名鍵は`runtime/keys/`へ自動生成し、トークンの指紋と組み合わせる。トークンを変えると既存セッションは無効になる。
  - 最後の利用から14日で失効し、利用中は延長する。Cookieで来た状態変更要求にはOriginを必須にする。
- 全応答に`frame-ancestors`を付け、同一origin以外からの埋め込みを拒否する。
- 認証なしのままループバック以外のHostを許可した構成は起動を拒否する（`DOCLING_AUTH_ALLOW_INSECURE=1`で明示的に許可）。
- 既定ポートは28765に変更する。Dockerのホスト側も28765に揃える。過去の検証記録は書き換えない。

## 3. 本運用前評価のP0/P1

| 区分 | 項目 |
| --- | --- |
| 正しさ | 変換ジョブの例外でrunningのまま固着しない。起動時のBlob復元失敗・1資料の移行不整合で全体を止めない。async処理内のスレッドロック・同期書き込みをスレッドへ移す |
| 入力上限 | PDFページ数の既定上限とアップロード経路での検査。ZIPのエントリ単位・展開後サイズの検査。xlsx解析をdefusedxmlへ。LibreOfficeのタイムアウト時にプロセスグループごと停止 |
| 画面 | アプリ全体のエラー境界。外部エディタのセッション失効時に未保存の本文を消さない。埋め込み時のpostMessage送信先を限定 |
| 独立ナレッジAPI | answerジョブの保持期限、クライアント別のキュー枠、500のログ |
| 依存・配布 | Python lockのハッシュ固定と`--require-hashes`、frontendの脆弱な推移依存の更新、CIのAction固定と監査 |
| 運用 | composeの環境変数とsecrets、資源制限と権限の削減、Docker手順の誤り（smoke、バックアップ） |

## 4. 検証

- 自動検出の各組み合わせ、Bearerの200/401、チケットの再利用・期限切れ拒否、トークン変更時のセッション失効、接頭辞付きの画面配信を固定試験で確認する。
- 全体のruff・ty・pytest、frontendとknowledge-apiのtest・typecheck・lintを最後に1回実行する。
- LARM上の起動とEumenesからの実接続は未検証として残し、LARMで引き継ぐ。
