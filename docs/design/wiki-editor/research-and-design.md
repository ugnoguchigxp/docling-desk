> 実装追補（2026-10-16）：代表画像は独立したURL欄ではなく、Plate本文の最初の画像から自動選択する。ツールバーはPlate公式EditorKitの標準フル構成へ変更した。現在の利用方法と保存仕様は[Wiki](../../wiki.md)を参照。

# Wiki編集と複数媒体投稿の画面設計

2026年10月16日時点の設計案。Wikiの記事をPlateで執筆し、将来Zenn、Blogger、Qiita、Mediumへ展開するための入力情報と画面構成を定める。外部投稿の実装・アカウント接続・実際の投稿は今回の対象外。

一つの本文を持ちながら、共通の記事情報、媒体ごとの調整、投稿した版と結果を分けて管理する。Wikiの保存と外部への送信は別操作にする。前案の「投稿先のチェック欄」だけでは、媒体固有の設定、連携方法の違い、再投稿による重複を扱えない。

## 媒体別に必要な項目と制約

以下は公式の投稿手段に基づく整理。「入力候補」はすべて必須という意味ではない。

| 媒体 | 本文以外の入力候補 | 投稿経路と設計上の条件 |
| --- | --- | --- |
| Zenn | タイトル、絵文字、記事種別tech/idea、topics、slug、公開設定、必要時に公開日時とPublication | GitHub連携用のMarkdownとfrontmatterを生成。slugは12〜50文字の英小文字・数字・ハイフン・アンダースコア。予約日時はJST。公開日時は設定後の変更に制約がある。[CLI公式](https://zenn.dev/zenn/articles/zenn-cli-guide) |
| Blogger | 投稿先ブログ、タイトル、labels、下書きか公開か、公開日時 | blogIdを選択し、本文をHTMLへ変換。作成時のisDraftと、その後のpublish操作を区別する。[作成API](https://developers.google.com/blogger/docs/3.0/reference/posts/insert)、[Posts](https://developers.google.com/blogger/docs/3.0/reference/posts)、[公開API](https://developers.google.com/blogger/docs/3.0/reference/posts/publish) |
| Qiita | タイトル、タグ、必要時のタグのversions、全体公開か限定共有か、Organization。詳細項目としてスライド表示、X共有、キャンペーン参加 | 公開APIはMarkdownのbody・title・tagsを要求する。タグは1〜5個。Organizationと限定共有の併用はできない。[API](https://qiita.com/api/v2/docs#post-apiv2items)、[タグ](https://help.qiita.com/ja/articles/qiita-tag)、[限定共有](https://help.qiita.com/ja/articles/qiita-private-article) |
| Medium | タイトル、副題、一覧用タイトル・副題、topics、featured imageと焦点、投稿先Publication、元記事URL、ライセンス。対象者のみ収益化設定や通知 | 新しいAPI連携・トークン発行を停止。Web取り込み・手動仕上げを基本とする。記事画像からfeatured imageを選ぶ。現行Webのtopicsは最大5個。[連携制限](https://help.medium.com/hc/en-us/articles/213480228-API-Importing)、[執筆と公開](https://help.medium.com/hc/en-us/articles/225168768-Writing-and-publishing-your-first-story)、[topics](https://help.medium.com/hc/en-us/articles/214741038-Using-topics)、[画像](https://help.medium.com/hc/en-us/articles/215680047-Setting-a-featured-image-on-your-post) |

### 公開状態を同じ名前にまとめない

Qiitaの限定共有はURLを知る人が読める投稿であり、下書きではない。全体公開した記事を限定共有へ戻すこともできない。公開APIの投稿契約に下書き保存・予約日時の項目は見当たらないため、この連携案ではローカルで保持し、送信時に全体公開か限定共有を選ぶ。予約機能を追加する場合は、Qiita本体の機能と区別した別の設計が必要。[限定共有](https://help.qiita.com/ja/articles/qiita-private-article)、[API](https://qiita.com/api/v2/docs#post-apiv2items)

ZennはGitHubへの反映とZennでのデプロイ確認が別段階になる。Publicationの個人アカウント経由ではpublication_name、Publication Proの連携リポジトリでは著者ごとのディレクトリや設定済みブランチを考慮する。接続設定で方式を保存し、執筆のたびに入力させない。[アカウント連携](https://zenn.dev/zenn/articles/connect-to-github)、[Publication](https://zenn.dev/zenn/articles/how-to-use-publication)、[Publicationリポジトリ](https://zenn.dev/zenn/articles/connect-to-github-publication)

Mediumの旧API仕様は現行Webの仕様として流用しない。旧APIではタグは先頭3件、public/draft/unlisted、canonicalUrl、license、notifyFollowersなどが定義されるが、API自体はサポート終了と明記されている。既存トークンを使う経路は別途実接続で確認し、標準の新規接続として提示しない。[旧API](https://github.com/Medium/medium-api-docs)、[現在の方針](https://help.medium.com/hc/en-us/articles/213480228-API-Importing)

### 元記事URLと掲載用の画像

共通情報には「元記事の公開URL」を任意で持たせる。ローカルWikiのlocalhost URLを外部の元記事URLにしない。Mediumではcanonical linkとして設定できる。一方、Zenn・Qiita・Bloggerの確認した投稿契約に同等の入力欄があるとは断定せず、非対応経路では自動設定済みと表示しない。[Medium canonical](https://help.medium.com/hc/en-us/articles/360033930293-Set-a-canonical-link)

代表画像は共通の候補として保持し、全媒体の独立したカバー画像欄へそのまま送れるとは扱わない。Mediumの一覧画像は本文に含まれる画像から指定する。ZennのGitHub連携画像は形式と3MB上限がある。ローカル添付やWiki内リンクは外部から利用できる形へ書き換える必要がある。[Medium画像](https://help.medium.com/hc/en-us/articles/215680047-Setting-a-featured-image-on-your-post)、[Zenn画像](https://zenn.dev/zenn/articles/deploy-github-images)

## 提案する情報の持ち方

Bloggerのパーマリンクと検索向け説明は、サイト側で仕上げる項目として扱う。現行ヘルプはカスタムパーマリンクと画像のalt/titleを案内しており、公式の検索設定の説明では個別記事のSearch Descriptionも紹介される。確認したPosts APIの公開フィールドにこれらを一律で書き込めるとは扱わない。[現行ヘルプ](https://support.google.com/blogger/answer/41373?hl=en)、[公式の検索設定説明](https://blogger.googleblog.com/2012/03/customize-your-search-preferences.html)

Mediumでは本文のタイトル・副題と、一覧やSNSで使うタイトル・副題を別に調整できる。ライセンスと予約公開もWeb側に設定があり、今回の手動仕上げの設計へ含める。[表示タイトルと副題](https://help.medium.com/hc/en-us/articles/214895188-Custom-titles-subtitles)、[ライセンス](https://help.medium.com/hc/en-us/articles/214741758-Set-a-content-license)、[予約公開](https://help.medium.com/hc/en-us/articles/216650227-Schedule-to-publish)

| 分類 | 持つ情報 | 画面への反映 |
| --- | --- | --- |
| 共通の記事情報 | タイトル、Markdown原文、任意の概要または副題、キーワード候補、言語、画像と代替テキスト・キャプション・出典、元記事の公開URL | Plate編集画面の右側。概要・言語・代表画像はアプリ側の管理項目であり、全媒体に同名の送信欄があるという意味ではない |
| Wiki内の配置 | 分類、記事パス、下書き・Wiki保存版 | 「Wikiの配置」にまとめる。Wiki保存時に投稿先の設定を必須にしない |
| 投稿先ごとの設定 | 使用する接続、対象ブログ・Publication・Organization・リポジトリ、タイトル上書き、タグ対応、固有の表示設定、公開範囲、対応する予約日時 | 投稿設定画面で媒体ごとに編集。共通値の継承と上書きを明示する。未対応項目を空欄や無言の無視にしない |
| 投稿先との対応情報 | 外部記事IDまたはslug、公開URL、送信した本文・設定・添付の版、相手側の更新情報、最終確認時刻、送信結果とエラー | システムが保存して「新規作成」と「既存記事の更新」を区別。利用者にIDの手入力を常に求めない |
| 接続設定 | アカウント、権限、接続方式、既定のブログ・リポジトリ・Publication、秘密情報への参照 | 記事と分けて管理。記事内にアクセストークンの入力欄を置かない |

これは画面と将来の保存契約の提案であり、DB追加の実装指示ではない。既存のMarkdown原文、下書き、保存版、ETag、添付機構を再利用する。既存の未知frontmatterや保護対象ブロックを全体再変換で失わない。媒体向けのHTMLやfrontmatterは送信対象の派生物として生成し、Wikiの正本へ上書きしない。

## 画面構成

### Plateで記事を書く画面

Wiki上部の右端に「アップロード」「文書にプラス」のアイコンボタンを隣接配置する。可視テキストは置かず、ツールチップ・読み上げ名・フォーカス表示で操作を識別する。

本文領域はPlateの見たまま編集とMarkdownソースの切替を持つ。タイトルと任意の副題を本文と分け、右側に共通キーワード、代表画像候補、画像情報、元記事の公開URL、Wiki配置を置く。画像の説明は画像選択時に編集する。

主操作は「Wikiに保存」。将来の「投稿設定」は別の入口にし、現在の実装では利用可能な外部送信と誤認させない。画像案では「将来機能の設計案」と明示する。

### 投稿先ごとの設定と変換を確認する画面

共通の記事情報と対象保存版を上部に示し、Zenn、Blogger、Qiita、Mediumを横並びで比較する。媒体ごとに接続方法、実際に必要な固有項目、選べる公開状態、送信方法を示す。ブログ・アカウント等はダミーの接続済み状態にしない。

共通タイトル・タグからの継承と媒体別の変更を分ける。Mediumだけ英語版にしたい場合は、言語ラベルの変更では翻訳せず、関連する別の記事版を選ぶ設計にする。

Zennは絵文字・種別・slug・topics・Publicationと予約JST、Bloggerはブログ・labels・下書きと予約、Qiitaはタグ・Organization・公開範囲、Mediumは副題・topics・本文画像からのfeatured image・canonical・Publication・ライセンスを扱う。Mediumの仕上げと予約はMedium画面で行う。

表示プレビューと「変換・画像の確認」を用意する。Zenn固有ブロック、数式、図、埋め込み、表、コード、画像、Wiki内リンクなどは変換先ごとの可否を確認する。本文を黙って削除・変換したことにしない。独自記法の意味判断や代替案の提案はLLMに委ね、コードは仕様上の制約と変換結果を検証する。

### 投稿後と失敗時の表示

媒体ごとに「未送信」「処理中」「下書き」「予約」「公開」「結果未確認」「失敗」「原稿に変更あり」を、実際に取得できた状態として表示する。一つの「公開済み」バッジで全媒体を表さない。

外部送信に使用した保存版を固定し、途中の編集は次回の更新候補として保持する。送信結果が不明な場合は結果照会へ進み、新しい記事を無条件に作り直さない。成功済みの媒体を含めた全件再投稿はしない。公開記事の更新時は本文・設定・画像の差分を表示し、相手側で直接編集されている場合も確認する。これらは将来実装の要件で、今回動作した機能ではない。

## デザインと将来実装の検証

- アイコンだけでもツールチップと読み上げ名でアップロード・新規作成を識別でき、キーボードで操作できる。
- タイトル・本文以外の共通情報と、媒体別の上書きが混ざらずに確認できる。
- Wiki保存と外部送信が別で、未実装の投稿操作を実行可能に見せない。
- Qiitaの限定共有を下書きと表示しない。Mediumの新規API接続を案内しない。
- 予約・canonical・代表画像を非対応媒体にも一律で適用できるように見せない。
- 未接続、画像未配置、変換未確認、部分成功、結果不明、外部編集との競合、原稿の更新残りを区別できる。
- 製品実装時は、原文保持とメタデータ保存の部分検証、APIと保存の結合検証、Wiki新規作成から再読込する通し検証の順に進める。外部送信は今回の検証対象に含めない。

## 生成画像

- 執筆画面：`wiki-editor-research-v2.png`
- 将来の投稿設定画面：`publishing-settings-research-v2.png`
- 生成指示：`prompt-editor-v2.txt`、`prompt-publishing-v2.txt`
- 投稿設定画面のナビゲーション修正指示：`prompt-publishing-v2-navigation-edit.txt`

前案は履歴として保持し、この設計と画像を後続の判断材料にする。

