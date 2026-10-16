# 画像リソース連携仕様 v1

2026-10-16。資料一覧の画像とローカルWikiのMarkdownを接続する実装仕様。画像生成とBlogへの投稿処理は含めない。

## Markdownへ渡す値

画像詳細のコピー欄は、読み取り専用テキストとコピーアイコンだけ。標準クリップボードの `text/plain` に次の1行を書き込む。

```markdown
![画像の説明](/api/wiki/images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png)
```

- 参照先は既存Wikiの不変画像URL `/api/wiki/images/<原本SHA-256>.<png|jpg|gif|webp>`。名前・フォルダーを変更しても変わらない。
- コピー結果にホスト名、認証情報、JSON、HTML、画像バイナリ、コードフェンスを加えない。別タブのMarkdownへテキストとして貼り付ける。
- 代替テキストの `\`、`[`、`]` をMarkdownとしてエスケープする。改行はスペースにする。空の代替テキストは `![](...)` として保持する。
- 配置先prefixはコピー結果へ含めず、既存の `withBase` とWikiレンダラーが表示時に適用する。
- コピーは属性の保存や記事の編集・公開を行わない。属性への永続保存は「属性を保存」で行う。
- 自動コピー拒否時は記法欄を選択し、手動コピーを案内する。失敗を成功と表示しない。

前案の `docling-asset:UUID` は採用しない。実装時点でWiki側の画像URL・Plate表示・Markdown保存が存在するため再利用した。独立ナレッジAPIの `knowledge-attachment:` は従来の文書所属の添付のままであり、本仕様で権限や所属を拡張しない。

## 保存と保持

`/api/upload` はPNG・JPEG・GIF・WebPを受け付ける。1ファイル50 MiB、4000万画素まで。実データを検証し、拡張子との不一致、破損、過大な復号サイズを拒否する。原本は変換せず、単独画像にOCR・文書抽出を実行しない。

既存の資料保存機構に原本とJobを登録する。配置と管理属性は `content/manifests/library.json`、画像の不変属性はJobと成果物manifestへ保持する。サムネイルはアップロード完了前に生成し、`cache/documents/{job_id}/thumbnails/image-{sha256}.webp` へ保存する。最大480×360px・32,000バイト以下の再生成可能な表示用キャッシュで、独立した資料・Jobとして登録せず、検索・件数・Markdown参照の対象にも含めない。生成に失敗したアップロードは登録せず、原本とキャッシュを片付ける。既存画像やキャッシュ消失時に限り、サムネイル取得時に再生成する。

コピー先の記事を壊さないよう、既存の `content/wiki/images/` にも原本の不変スナップショットを保持する。同じハッシュは再利用する。資料一覧の登録を削除しても、このWiki画像原本は削除しない。Wiki画像の削除機能を追加する場合は、下書き・過去版の参照と保持を含む別の設計が必要。

コピー元タブを閉じた後、資料の移動・名前変更後、資料一覧の登録削除後もWiki画像を取得できる。別ホストやBlogへそのまま転送できる公開URLではない。外部への画像配置とURL変換は投稿担当の責務。

## 属性

`contract_version` は `docling.media/1`。`asset_id` は資料一覧の登録IDをハイフン付きUUIDにしたもの。同じ原本をコピーした登録は別のasset_idだが、原本ハッシュとreferenceは共通になる。

| フィールド | 意味 |
| --- | --- |
| `asset_id` | 資料一覧の画像登録ID。UUID |
| `reference` | Markdownへ保存する既存Wiki画像URL |
| `sha256` | 原本バイトのSHA-256、小文字64桁 |
| `media_type`、`byte_size` | 検証済みMIME型と原本バイト長 |
| `width`、`height`、`animated` | EXIF表示方向を反映した寸法、アニメーションの有無 |
| `original_filename`、`display_name` | 原本名と変更可能な表示名。拡張子は変更不可 |
| `folder_id`、`folder_path` | 配置。folder_pathは表示用のフォルダー名列 |
| `description`、`tags` | 管理・検索用の説明とタグ |
| `default_alt`、`default_credit`、`source_url` | 説明・出典表記の候補、出典URL。未設定は空文字 |
| `created_at`、`updated_at` | UTC日時文字列 |
| `metadata_etag` | 属性更新の競合検出用の不透明な文字列 |
| `thumbnail` | 表示用の入口。本文・投稿に原本の代わりとして使わない |

同じreferenceの複数登録で表示名・説明等が異なる場合がある。記事のaltは本文を正本とし、登録の属性変更で既存記事を書き換えない。記事ごとのキャプションや出典上書きはMD担当が管理する。

## ローカル画像API

既存アプリと同じ認証・origin制約の下で提供する。独立APIや外部サービス向けの認証を新設したものではない。

| 操作 | 入口 |
| --- | --- |
| 検索 | `GET /api/v1/media` |
| 属性取得 | `GET /api/v1/media/{asset_id}` |
| 属性更新 | `PATCH /api/v1/media/{asset_id}`、`If-Match: metadata_etag` |
| サムネイル | `GET /api/v1/media/{asset_id}/thumbnail` |
| 登録の原本取得 | `GET /api/v1/media/{asset_id}/content` |
| MD用不変原本 | `GET /api/wiki/images/{sha256}.{ext}`（既存） |

検索引数は `q`、`reference`、`folder_id`、`recursive`、`media_type`、`sort`、`cursor`、`limit`。qは名前・原本名・説明・タグのNFC正規化と大小文字を無視した部分一致。referenceは貼付した画像URLとの完全一致。

folder_id省略は全フォルダー、rootはルート直下。recursive=trueは指定フォルダー配下を含む。sortはcreated_desc、created_asc、name_asc。limitは1〜100、既定48。応答はcontract_version、items、next_cursor。cursorは検索条件に結び付き、条件変更時は破棄する。

PATCHはdisplay_name、description、tags、default_alt、default_credit、source_urlを送る。タグは最大30個、各100文字まで。説明4000文字、代替テキスト1000文字、出典表記2000文字、出典URL2048文字まで。If-Matchなしは428、古い属性は412。競合で入力を自動上書きしない。

サムネイルは画像全体を保って最大480×360、32 KB以内。透過画像は白背景、アニメーションは先頭フレームを使う。生成失敗時も一覧の名前・形式を表示し、原本バイトはそのまま保持する。

## MD担当が使う手順

1. 貼付された標準Markdownを既存パーサーで読む。隠しJSONやタブ間通信を要求しない。
2. 既存Wikiの画像URLは既存の表示・保存機構を使う。本文のaltを保持する。
3. 原本属性が必要なら `/api/v1/media?reference=<画像URL>` で取得する。登録ごとの名前・タグ・出典候補は参考情報とする。
4. 外部出力では画像URLから原本を取得し、投稿先の配置手段に合わせて派生本文のURLを変換する。内部MD正本は変更しない。

Plateからの画像ドロップ・貼り付け・ファイル選択は、既存 `/api/upload` の `folder_id` に資料一覧ルートの `upload` フォルダーIDを指定し、成功したJobの `image.reference` をその位置へ挿入する。フォルダーは初回に作成し、並行作成時は既存フォルダーを再取得する。アップロード済み画像は記事の破棄時も資料として保持する。既存のWiki専用画像をさかのぼって資料登録する処理は行わない。

対象は資料一覧から追加された独立画像と、上記Plate経由の新規画像。過去のWiki専用画像の逆登録、PDFからの画像抽出、画像意味検索、Blogへの送信・公開は含めない。

Plate画像ボタンの「資料一覧から選ぶ」では、本仕様の検索APIをフォルダー指定なしで利用し、サムネイル・フォルダー名・寸法を表示する。検索条件が変わったらページングを先頭に戻す。画像の決定時は属性取得APIで最新の登録を確認し、`reference`と`default_alt`を本文の標準画像ノードへコピーする。原本や資料登録を複製しない。以後の資料側の属性変更で、挿入済み記事の代替テキストは変更しない。
