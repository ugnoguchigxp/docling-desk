# MCPによる文書構造・範囲・表・ページ画像の取得

独立ナレッジAPIに取り込んだ文書の保存済み抽出結果を、read権限で取得する。新たなOCR・再抽出は行わない。デスクトップ資料・Wikiの保存先との接続は、既存の外部APIと同様に別の作業となる。

## 操作と対応API

| MCPツール | 業務API（POST） | 主な指定と返却値 |
| --- | --- | --- |
| `get_document_outline` | `/api/v1/document-reads/outline` | `offset`・`limit`で抽出順の要素、見出し、親子関係、実在する単位番号を取得 |
| `get_document_section` | `/api/v1/document-reads/section` | `selector`で節、要素の両端、ページ等の番号範囲を指定。本文と出典を取得 |
| `list_document_tables` | `/api/v1/document-reads/tables` | 表ID、caption、行列数、見出し行、結合有無、出典を一覧 |
| `get_document_table` | `/api/v1/document-reads/table` | 表IDと行列範囲からグリッド・元セル構造を取得。CSVも指定可能 |
| `get_document_page_image` | `/api/v1/document-reads/page-image` | `unit`でPDFページ・PPTXスライド画像を取得 |

すべての入力に`source_id`・`source_revision`・`evidence_revision`が必要。同じ3つの参照を結果にも返す。`get_document_status`から現在版を得て、outlineや表一覧で得たID・単位番号を次の操作へ渡す。任意のファイルパス、URL、利用者IDは業務引数として受け付けない。

MCPの業務引数は`arguments`の中に包む。ホストAPIは`POST /host/tools/<tool_name>`へ`user_id`と業務引数を渡す。業務APIの認証は従来どおりservice tokenと署名済みactor assertionを使い、MCPの利用者別トークンと混同しない。[接続設定](external-document-platform.md)と[公開契約](../contracts/knowledge-api.openapi.json)を参照。

## 取得の順序と範囲

`get_tool_guide(guide_id="workflow.read-document")`で一連の手順を取得できる。5つのツールのDescriptionにも個別ガイドIDがある。ガイドの`schemas`でHTTPとMCPの完全な入出力契約を確認できる。

1. `get_document_status`で版・処理状態を確認する。準備中なら`get_document_job`を使う。
2. `get_document_outline`の続き（`next_offset`）を取り、LLMが目的に沿って範囲を選ぶ。
3. `get_document_section`で本文、必要なら表一覧→表取得、PDF・PPTXなら画像取得を行う。
4. 改版・再抽出で409となったら、現在版とoutline・表一覧を取り直す。

outlineはDoclingの`title`をレベル0、`section_header`をその見出しレベルとして公開する。他要素のlevelはDoclingツリーの深さ。文書に存在しない節を生成しない。PDF・PPTX・XLSXは実在するページ・スライド・シートを`units`に返す。DOCX・Markdown・テキストは物理ページを設定せず、節・要素範囲を使う。

sectionのselectorは、`kind=section`と見出し`ref`、`kind=elements`と`start_ref`・`end_ref`、または`kind=units`と`start`・`end`。両端を含む。節範囲は次の同じか上位の見出しの手前まで、単位範囲は交差する要素全体を返す。出典を失うような部分文字列への切断はしない。

一覧のlimitは最大200、既定100。単位範囲は最大100、本文は最大1000要素・80000文字（Unicodeコードポイント、既定40000文字）、JSON応答は約1.9MB。上限超過は413となるため、範囲を分割する。

表の行・列offsetは0始まり。1回の取得は最大100行×50列。`next_row_offset`と`next_column_offset`で各方向の続きが分かる。CSVも同じ矩形だけを返す。結合セルは元の開始・終了座標とspanを保ち、取得矩形と交差するものを返す。文字列グリッドは結合セルの左上に値を置く。大きな表でも指定矩形だけを確保し、一覧のために全セルを展開しない。

## 画像と形式の制約

MCPでは`content`にメタデータのtextとネイティブなimage、`structuredContent`に版・単位・MIME・寸法を返す。HTTP業務API・ホストAPIでは`body_base64`も返す。PNGに見せかけたJSONや、認証なしで開ける保存先URLは返さない。

PDFは既存PDF描画を使い、長辺1536px以内・1MiB以内のWebPを返す。PPTXは既存スライドプレビューのサムネイルを再利用するため、おおむね長辺240px。細部の文字や表は本文・表ツールと照合する。PPTXは従来のOS別プレビュー環境が必要で、取得時にキャッシュ描画を行う場合がある。

DOCX・XLSX・Markdown・テキストのページ画像は422 `page_image_unsupported`。構造成果物が未公開・欠落した場合は409、processor機能が利用できない場合は503として区別する。表がない場合は空の一覧で成功する。失敗だけを理由に自動的に再抽出しない。

公開済みの空のMarkdownは、構造・表一覧をtotal=0で取得できる。既存の空文書処理は抽出器を呼ばないため、この場合は公開状態と原本ハッシュを確認する。範囲取得は422 `invalid_document_range`、存在しない表は404 `document_table_not_found`となる。

標準MCPクライアントで画像受信まで検証する。実Eumenesの画像受信・表示と、Eumenes固有の接続設定は別途確認が必要。

## 実行境界と検証

gatewayは既存の利用者別MCP認証、ホスト認証、署名済みactorを再利用する。業務APIはread action・scope・現在の原本版・根拠版と、公開済み抽出attemptを確認してprocessorへ渡し、I/O後にも確認する。削除・改版・再抽出・権限失効が途中で起きた場合は内容を返さない。ブラウザーの短期編集セッションから追加ツールは呼べない。

processorは公開済みattemptの`document.json`を読み、共通の表投影と既存PDF・スライド描画を使う。`structure_ready`で構造成果物の公開を明示し、既存の抽出attempt対応表を使う。認証方式・SQLiteスキーマ・LLMの意味判断は変更しない。分岐は外部契約のselector・形式・境界検証に限定する。

```sh
DOCLING_ENV_FILE=/dev/null .venv/bin/python -m pytest -q \
  tests/test_document_reads.py tests/test_document_ingestion_live.py \
  tests/test_tool_guides.py tests/test_document_editor_gateway.py
(cd knowledge-api && bun test && bun run typecheck && bun run lint && bun run contract:check)
DOCLING_ENV_FILE=/dev/null .venv/bin/python -m docling_desk.operations.tool_guides
```

通し試験は実Streamable HTTPクライアント→gateway→Bun/SQLite→Python processorを隔離環境で起動し、Markdown取り込み・構造・本文範囲・表・CSV・再抽出による旧参照失効と、実PDF取り込み・指定ページ本文・画像受信を確認する。外部OCR・Embedding・回答生成は呼ばない。PDFモデルは既存のローカル配布モデルを使う。

本文範囲に含む表は1表5000セルまで。これを超える表は全体展開の前に413となるため、表一覧とget_document_tableで行列範囲を分けて取得する。
