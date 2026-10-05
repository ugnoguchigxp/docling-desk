# 共通画面部品

資料一覧・文書閲覧・表・翻訳・解説は、このディレクトリの部品を組み合わせて作る。同じ役割の部品を機能側へコピーしない。既存のID、class、標準HTML属性とrefを渡し、現在のCSSに合わせる。共通部品から `features/`、資料API、業務状態を参照しない。

| 部品                                    | 利用先                         | 担当                                                                   |
| --------------------------------------- | ------------------------------ | ---------------------------------------------------------------------- |
| Icon / IconDefinitions                  | 一覧、閲覧、追加、フォルダー   | 現行SVGの描画                                                          |
| Button / IconButton / ActionLink        | 全機能                         | 標準属性、操作名、disabled、ref、ボタンのtype                          |
| TextField / SelectField / CheckboxField | 一覧、追加、翻訳、表           | 標準入力属性とチェックボックスの部分選択                               |
| Toolbar                                 | 一覧、閲覧、表                 | 既存の配置を保持するheader/div                                         |
| Tabs                                    | 文書閲覧                       | ARIAと矢印/Home/End操作。文書面の保持は利用側が管理                    |
| Dialog / DialogHeading / DialogActions  | 追加、資料操作、翻訳、表の拡大 | native dialog、Escape、処理中の閉じる制限、フォーカス復帰              |
| DisclosureMenu / Popup                  | 資料行、保存、情報、表         | native detailsまたはポップアップ、外側クリック、Escape、キーボード操作 |
| Breadcrumbs                             | 一覧と保存先選択               | 現在地、経路、移動・ドロップcallback                                   |
| StatusMessage                           | 全機能                         | status/alertのテキスト表示                                             |
| ContentCard / MetadataDisclosure        | 構造、RAG、解説                | 本文と詳細情報。文書の文字列はReactのテキストとして描画                |
| SidePanel                               | 翻訳・解説                     | 見出しと閉じる操作。幅や重なり方は既存CSSが管理                        |
| DocumentFrame                           | PDF・PPTX・XLSX・DOCX          | iframeの属性・ref。形式別のsandboxとsrcは利用側が指定                  |

共通化のためにDOMのラッパーや余白を追加しない。`Toolbar` を挟むときも、既存flexの直接の子と兄弟関係を保つ。スタイルの違いは既存classと必要なpropsで表す。共有CSSの整理、resetや別テーマの導入は文書レイアウト比較と別の変更にする。

API取得・保存版の選択・生成要求・フォルダーの操作判断は `features/` が管理する。サーバー状態の正本はTanStack Queryの取得結果とし、共通部品に処理状態を複製しない。表専用のSVGと選択処理は `features/TableIcons.tsx` と `features/table-model.ts` に置く。

原本プレビューのコピーは `features/PreviewCopy.tsx` が選択文字を受け取り、共通の `IconButton` と `Dialog` を使う。Wordの表示倍率は `features/Word.tsx` が共通ボタンで操作する。文書内の信頼済みスクリプトは選択文字と寸法を通知し、React側で送信元を照合する。

部品変更時は `pnpm lint`、`pnpm test:run`、`pnpm test:e2e`、`pnpm test:visual` を実行する。新しい画面パーツを追加する場合はこの台帳へ用途を追記する。

`FeatureBoundary` は表など遅延読込する機能の失敗をその機能内に留め、資料への移動と原本表示を保つ。フォールバックの文面と再読込操作は利用側が指定する。コピー完了・失敗の通知は `lib/async-scope.ts` の共通判定を使い、ページ・タブの変更や画面の破棄後に前の結果を表示しない。

プリントプレビューは `features/PrintPreview.tsx` が共通の `Dialog`、`DialogHeading`、入力・ボタン・`DocumentFrame` を組み合わせる。印刷用の文書はスクリプトを実行しない専用の `public/print.html` に構成し、元の閲覧フレームのsandboxは変更しない。範囲・用紙・言語の判断と読み込みの中断は機能側が担当する。

## 独立した文書ビューアー

`../viewer/DocumentViewer.tsx`は、通常画面とiframe専用入口で共有する閲覧部品です。`ViewerSource`で取得先をインスタンスごとに指定し、翻訳・解説・印刷などの通常画面の操作は`extensions`として外側から渡します。資料一覧、アップロード、Wiki、チャットのDOMには依存しません。埋め込みでは生成・保存・印刷の操作を渡さず、文字選択の質問だけをホストの接続部品へ通知します。

DOMのIDと形式別CSSを再利用するため、同一Reactルートへの複数配置は対象外です。iframeごとの複数配置を使ってください。6形式のsandbox表示、出典位置、表示タブ、狭い画面、再接続、通常画面の操作回帰の記録は`qa/viewer-embedding/`に保存します。
