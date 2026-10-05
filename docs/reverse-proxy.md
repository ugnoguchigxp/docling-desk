# リバースプロキシ配下での公開

別のWebアプリと同じホストの下に、このアプリをサブパス（例：`/assessment/`）で載せるための設定です。TLSと外部公開はnginxなどが終端し、このアプリはループバックまたは内部ネットワークだけで待ち受けます。アプリ自体は認証なしでも動きますが、ホストアプリのログインを共有して閲覧を制限できます。

## 構成

```text
ブラウザー ── https://example.com/assessment/ ──▶ nginx ──▶ 127.0.0.1:18765（このアプリ）
                         └── https://example.com/ ──▶ 別アプリ
```

nginxは接頭辞を取り除いて転送します。アプリ側のルートは変えず、画面・原本ビューア・スクリプトが出力するURLにだけ接頭辞を付けます。保存済みの文書や翻訳元には接頭辞を書き込みません。

## 環境変数

| 環境変数 | 内容 |
|---|---|
| `DOCLING_ROOT_PATH` | プロキシの接頭辞。`/assessment` のように先頭だけ `/` を付ける。未設定なら従来どおりルート直下 |
| `DOCLING_ALLOWED_HOSTS` | 公開FQDNを追加する |
| `DOCLING_ALLOWED_ORIGINS` | `https://公開FQDN` を指定する。nginxは `Host` を書き換えず転送する |
| `DOCLING_AUTH_MODE` | `none`（既定）または `jwt` |
| `DOCLING_AUTH_JWT_SECRET` / `DOCLING_AUTH_JWT_SECRET_FILE` | `jwt` で必須。ホストアプリがトークンの署名に使う共有シークレット（32バイト以上）。ファイル指定を推奨 |
| `DOCLING_AUTH_COOKIE` | トークンを読むCookie名。既定 `mplm_access_token` |
| `DOCLING_AUTH_TOKEN_TYPE` | `type` クレームの期待値。既定 `access`。空にすると検査しない |
| `DOCLING_AUTH_ISSUER` / `DOCLING_AUTH_AUDIENCE` | 任意。指定すると `iss` / `aud` を検査する |
| `DOCLING_AUTH_USER_CLAIM` | 利用者を表すクレーム。既定 `userId` |
| `DOCLING_AUTH_LOGIN_URL` | 未ログイン時の転送先。画面の読み込みは戻り先を付けて転送し、APIは401にこのURLと戻り先の形式を添える |
| `DOCLING_AUTH_RETURN_PARAM` | 戻り先を渡すクエリ名。既定 `next` |
| `DOCLING_AUTH_RETURN_FORMAT` | 戻り先の形式。`url`（絶対URL、既定）または `path`（`/assessment/?…` のようなサイト内パス） |
| `DOCLING_AUTH_LEEWAY_SECONDS` | 時計のずれの許容。既定30 |
| `DOCLING_STORAGE` | `local`（既定）または `azure-blob`。Blobの設定は[コンテンツとローカル状態の保存](content-storage.md#blobミラー)を参照 |

`jwt` モードでは、HS256の署名・`exp`・（設定した場合）`nbf`・`type`・`iss`・`aud` を確認します。Cookieまたは `Authorization: Bearer` のトークンを受け付けます。`/health/` と `/static/` は認証なしです。アクセストークンの期限が切れると、再ログイン後に元の画面へ戻ります。戻り先は、初回アクセスの転送と、画面操作中のAPIの401で同じ設定（名前・形式）を使い、クエリを含む元の画面（例：`/assessment/?mode=wiki&source=…`）を指します。このアプリはトークンの更新をしません。

Cookieはホスト単位で送られるため、ホストアプリと同じホスト名・`Path=/` で配信してください。別のサブドメインでは届きません。

## nginxの例

```nginx
location /assessment/ {
    proxy_pass http://127.0.0.1:18765/;      # 末尾の / で接頭辞を取り除く
    proxy_set_header Host $host;             # Originの照合に使う
    proxy_set_header X-Forwarded-Proto https;
    client_max_body_size 55m;                # 上限50 MiB＋余裕
    proxy_read_timeout 300s;
}
location = /assessment { return 301 /assessment/; }
```

起動時に `DOCLING_ROOT_PATH=/assessment` を設定します。ヘルスチェックはnginxを通さず、`Host: localhost` でコンテナの `/health/live` を呼んでください。

## 制約

- 旧UI（`DOCLING_LEGACY_UI=1`）と外部ビューアの埋め込み（`/viewer/`）は、接頭辞付きの公開に対応していません。
- 相対リダイレクトを返す経路は接頭辞を補いません。通常の画面操作では発生しません。
