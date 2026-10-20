# docling-desk 図表ファイル管理とDiagram Design統合の実装計画

2026年10月20日。状態はOpusレビュー済み・指摘反映済みで、利用者による修正方針の確認待ち。レビュー結果は第13節に記録する。本書の作成は製品コードの実装、コミット、プッシュ、他チャットへのレビュー依頼を含まない。

`.excalidraw`をPDFやWordと同じく資料一覧の独立したファイルとして扱う。新規作成、取込、フォルダ管理、GUI編集、保存、再読込、書出し、版管理をdocling-desk単独で完成させ、その後にDiagram DesignによるAI生成と再デザイン、編集可能なAI操作、HTTP/MCP公開を追加する。

編集用の正本にはExcalidraw JSONを使う。Diagram DesignのHTML/SVGは、生成元の版に関連づけた成果物として保存する。任意SVGからExcalidrawの編集図形への無損失変換は前提にしない。

## 1 目的と受入範囲

### 必須の利用体験

1. 資料一覧で空の図表を作るか、外部で作った`.excalidraw`を取り込む。
2. 通常の資料と同じフォルダへ置き、一覧、名前検索、形式フィルター、プレビューで見つける。
3. 図形、ノード、接続線、文字、画像をGUIで編集し、UndoとRedoを使う。
4. 保存し、画面を閉じる。アプリ再起動後にも図形として再編集する。
5. 名前変更、移動、削除、原本ダウンロードを行う。図表の複製は初期範囲に含めない。
6. 保存した版を一覧で確認し、過去版を新しい版として復元する。
7. SVG、WebP（非可逆）またはJPEG、静的HTMLを書き出す。`.excalidraw`原本は外部Excalidrawでも再編集する。
8. 自然言語から図表を生成し、または既存図表をAIで変更する。変更を確認して保存し、その後も手動編集する。
9. Diagram Designで別の見た目へ仕上げ、生成元の図と生成物を対応づけて保存する。

1〜7はAIの設定や外部LLMへの接続なしで利用できることを必須にする。Wiki内への埋め込みだけで完了とはしない。

### 対象外

- Eumenes本体の変更、Eumenes固有データ型・会話処理の持込み。
- 既存WikiとHono Knowledge APIの保存領域統合、既存文書全体の移行。
- 新しいキャンバスエディターの導入、全図表形式を統一する大型フレームワーク。
- 任意SVGの完全な逆変換、全45種類の図表をExcalidrawで同じ見た目に再現する保証。
- リアルタイム共同編集、自動マージ、保存前の入力を跨ぐUndo履歴の永続化。
- SharePoint相当の組織・共有・資料単位ACL、ごみ箱、本番配備。外部公開時のscope認可は第5段階で実装する。
- draw.ioのGUI編集。既存の閲覧機能は維持し、今回の編集基盤とは混同しない。

## 2 調査根拠と既存機能

調査したdocling-deskのHEADは`26bdbc71b61895065ced45d95e22fce8b0ac5214`。作業ツリーには未コミットの別作業があり、draw.io閲覧等の実装が調査中にも更新されている。以下のファイルは存在と責務の根拠であり、実装開始時の固定ベースラインはG0で取り直す。

Diagram Designはコミット`f2f4d49ea8d632f17cf196ba26599277a93f767b`を調査した。Skill定義のmetadata.versionは2.6、本文のvisual-type guideは45種類。上流の変更を自動追従せず、このコミットを導入候補としてレビューする。

| 領域 | 確認した実装 | 利用する機構と不足 |
| --- | --- | --- |
| 資料一覧 | [Library.tsx](../frontend/src/features/Library.tsx)、[library.py](../src/docling_desk/documents/library.py) | フォルダ、一覧、名前検索、移動、複製、名前変更、物理削除。図表の新規作成と内容更新は追加が必要 |
| 原本保存 | [storage.py](../src/docling_desk/storage.py) | 資料IDによる保存、原本manifest、パス検証。汎用の版一覧・復元はない |
| 保存領域 | [content-storage.md](content-storage.md)、[blob_mirror.py](../src/docling_desk/blob_mirror.py) | content、derived、runtime、cache、Blobミラー。図表の版公開順序と復旧を追加検証する |
| Excalidraw | [excalidraw-node.tsx](../frontend/src/plate/ui/excalidraw-node.tsx)、[excalidraw-kit.tsx](../frontend/src/plate/components/editor/plugins/excalidraw-kit.tsx) | Wikiに実キャンバスを登録し、serializeAsJSONとrestoreで図形・files・保存可能な状態を保持する |
| Plate保存 | [WikiPlateEditor.tsx](../frontend/src/editor/WikiPlateEditor.tsx)、[editor_document.py](../src/docling_desk/knowledge/editor_document.py) | Markdownとネイティブ文書を保持する。図表だけの保存先には流用しない |
| ローカルWiki競合 | [files.py](../src/docling_desk/knowledge/files.py)、[authoring.py](../src/docling_desk/knowledge/authoring.py) | 不変本文、manifest公開、ETag比較の実装パターン。Wiki専用パスとMarkdown契約は流用しない |
| AI実行 | [editor_ai.py](../src/docling_desk/knowledge/editor_ai.py)、[codex.py](../src/docling_desk/explanation/codex.py)、[provider.py](../src/docling_desk/explanation/provider.py) | 構造化応答、一時環境、キャンセル、使用量。記事用の指示・スキーマ・予算は図表用に定義する |
| Hono | [editing.ts](../knowledge-api/src/editing.ts)、[store.ts](../knowledge-api/src/store.ts)、[auth.ts](../knowledge-api/src/auth.ts) | 下書き、版、冪等性、scope認可。ローカル資料とは独立し、下書き確定はoriginal.mdに固定されている |
| Python processor | [knowledge/worker.py](../src/docling_desk/knowledge/worker.py) | Hono用の非公開文書処理。Knowledge APIのSQLiteは開かない。ローカル図表原本のWriterに転用しない |
| HTTPとMCP仲介 | [document_tools.py](../src/docling_desk/viewer/document_tools.py)、[mcp.py](../src/docling_desk/viewer/mcp.py) | 固定された操作、利用者認証、API転送。図表契約の追加が必要 |
| 認証と表示隔離 | [auth.py](../src/docling_desk/web/auth.py)、[security.py](../src/docling_desk/web/security.py) | 任意JWT認証、Origin検査、CSP。ローカルLibraryには資料単位ACLがない |
| draw.io閲覧 | [documents/diagrams.py](../src/docling_desk/documents/diagrams.py)、[preview/diagrams.py](../src/docling_desk/preview/diagrams.py)、[DiagramViewer.tsx](../frontend/src/features/DiagramViewer.tsx) | 作業中の閲覧専用資産。HTML/SVGもmxfile等のdraw.io埋込データを要求し、通常の生成HTML/SVGと区別が必要 |

### Diagram Designの入出力

上流の根拠は[Skill定義](https://github.com/cathrynlavery/diagram-design/blob/f2f4d49ea8d632f17cf196ba26599277a93f767b/skills/diagram-design/SKILL.md)、[スクリプト群](https://github.com/cathrynlavery/diagram-design/tree/f2f4d49ea8d632f17cf196ba26599277a93f767b/skills/diagram-design/scripts)、[Excalidraw取込仕様](https://github.com/cathrynlavery/diagram-design/blob/f2f4d49ea8d632f17cf196ba26599277a93f767b/skills/diagram-design/references/import-excalidraw.md)、[書出し仕様](https://github.com/cathrynlavery/diagram-design/blob/f2f4d49ea8d632f17cf196ba26599277a93f767b/skills/diagram-design/references/export.md)。

| 機構 | 契約と制約 |
| --- | --- |
| 生成 | LLM向けの指示、参照文書、HTMLテンプレート。完成したGUI、生成サーバー、自然言語生成CLIではない |
| excalidraw_extract.py | .excalidraw等のJSONからダイジェストまたはJSON IR。入力16 MiB、10,000要素、2,000ノード、5,000接続の上限 |
| drawio_extract.py | XMLとdraw.io情報を埋め込んだPNG/SVGを解析。32 MiB入力、64 MiB展開、100ページ、ページあたり10,000セルの上限 |
| mermaid_extract.py | MermaidまたはMarkdown中のブロックを解析。flowchart、sequenceDiagram、stateDiagram-v2、erDiagramが対象 |
| export_svg.py | HTML中の最初の図表SVGを取り出し、CSSと参照IDを調整。標準Pythonのみ。サニタイザーではない |
| export_png.py | HTML中の最初のSVGをブラウザーで描画。Python PlaywrightとChromiumが必要。scaleは1〜4 |
| self_check.py | アクセシビリティ、外部参照、実行可能属性等の規約を検査。任意HTMLの安全化を代替しない |

CLIの引数はアプリが固定して組み立てる。formatやdetail等のSkill上の選択肢を、そのまますべての抽出スクリプトが受け付けるCLI引数とは扱わない。抽出結果の包み方はscene、pages、diagramsと異なり、小さな接続処理で正規化する。

## 3 採用する設計と代替案

| 判断 | 採用案 | 理由と代替案 |
| --- | --- | --- |
| GUI | 導入済みExcalidraw 0.18.1 | 新しいエディターは不要。PlateはWiki側のホストとして維持する |
| 初期の正本 | 標準のExcalidrawシーンJSON | ID、文字、接続、座標、画像を同じ形式で往復できる。HTML/SVGは図形編集の正本にしない |
| 意味構造 | シーンに関連づける補助情報 | 必要ならcustomData等の拡張領域に意味IDを置く。AIが付与・解釈し、コードが語句から役割を推定しない |
| 独立DiagramSpec | 当初は採用しない | 自由図形からの逆反映と種類別rendererの増加を避ける。必要性と失敗事例を示せる場合だけ再検討する |
| 複数形式 | 形式ごとに正本を1つ指定 | 将来HTML/SVGを原本とする資料を扱えても、編集方式と可逆性を明示する。同じ版に複数の独立した正本を置かない |
| 保存責任 | ローカル図表はPython本体 | Library、storage、backup、Blobを使う。既存Hono文書の正本方針は変更しない |
| 外部公開 | Honoを認可付きアダプターとして拡張 | 同じ図表の原本・版をHonoへ再保存しない。Markdown用draftsへJSONを詰めない |
| LLM | 既存構造化Providerを最初に利用 | Skill対応ホストのインストールを必須にせず、必要な指示をアプリが選んで渡す |

既存の[外部文書基盤計画](external-document-platform-implementation-plan.md)は、外部Markdown等の正本をHonoに置く。本計画がPythonを正本とするのは新設するローカル図表だけであり、既存文書全体の保存責任を変更しない。第5段階ではこの違いを公開ルートと認可契約に明記する。

### 情報を保持できる範囲

Excalidraw JSONをそのまま保存・復元する経路と、AIによる再デザインは別に扱う。

| 経路 | 保持の方針 |
| --- | --- |
| GUIから保存して再読込 | 要素ID、テキスト、接続binding、座標、画像files、対応する拡張フィールドを保持。未知フィールドを黙って削除しない |
| シーンへのAI部分編集 | 対象外要素は値を保持。再配置を依頼しない限り対象外の座標を変更しない |
| Excalidrawから抽出IR | bound textはラベルへ折り込む。元の文字要素ID、手描き線、画像本体、リンク、詳細な文字装飾、接続線の座標列は完全には残らない |
| IRからDiagram Design生成物 | 指示により統合・省略・ラベル変更・再配置がある。IDと全情報の保持を保証しない。変更・欠落の報告を保存する |
| 任意SVGからExcalidraw | 画像として配置する経路のみを初期対応とする。内部の図形・文字・接続が編集可能になったとは表示しない |
| Excalidrawシーン埋込SVG | 標準payloadから元シーンを復元できる場合がある。SVG表現自体を編集した変更の逆反映ではない。取込対応は初期必須にしない |

AIによる編集可能な生成は、Diagram Designの指針を参照しながらExcalidraw向け構造化データを出す独自の出力契約である。上流の標準HTML/SVG出力をそのままGUIへ変換する機能として宣伝しない。

## 4 画面とファイル形式

### 資料一覧

既存のフォルダ、一覧とギャラリー、選択、名前変更、移動、削除を使う。「図表を作成」から名前と保存先を選び、空のシーンを登録する。最初の保存前から安定した資料IDを持たせ、既存の名前衝突規則を適用する。

標準の取込・書出し拡張子は`.excalidraw`とする。任意の`.json`を一括で図表判定せず、形式と内容の契約で識別する。`.excalidraw.json`、シーン埋込SVG/PNG等の追加対応は能力表示と専用試験を揃えてから公開する。

draw.ioのHTML/SVG判定と、新設する生成HTML/SVGの判定を拡張子だけで混ぜない。検証済みの形式情報を登録し、閲覧・編集方法はその形式情報に従う。意味判断のための語句分岐は追加しない。

一覧検索は既存の名前検索から開始する。第3段階までに図中の文字・利用者の説明を既存全文検索へ接続する。確定した現在版だけを索引に載せ、下書きや古い成果物を最新版として返さない。意味検索は既存Provider設定がある場合だけ利用する。

### 編集画面

キャンバス、資料名、保存状態、保存、原本ダウンロード、書出しを備える。第2段階で下書き・版一覧、第3・4段階でAI操作を追加する。未保存変更を画面遷移や競合時に破棄しない。

既存ラッパーからExcalidraw接続を小さなDiagramCanvasへ切り出す。独立図表とWikiの両方で利用し、Wikiの既存シリアライズ・Markdown拡張を保つ。初期データが一度だけ評価される現行実装を踏まえ、AI変更はImperative APIから適用する。

Excalidraw側のUndoとPlate側の文書Undoが二重に動かないよう、キーボード入力とフォーカスを確認する。AI変更一式は1回のUndoで取り消せるようにする。再読込後は保存したシーンから開始し、以前のUndoスタックは復元しない。

### 複製と削除

図表の複製は初期範囲に含めない（2026年10月20日決定）。既存の資料コピーは`original.*`等を前提にしており、版・下書き・成果物を正しく扱えないため、図表への複製要求は画面で選べなくし、APIでも明示エラーで拒否する。黙って不完全なコピーを作らない。必要になった時は、現在版と採用済み成果物だけを新しい資料ID・初版としてコピーする案から再検討する。

削除は既存資料と同じ物理削除で、対象の版・下書き・成果物・キャッシュも削除する。過去版復元は残存資料の操作であり、削除済み資料のごみ箱復元ではない。生成中の削除は既存の実行中処理検査へ追加し、初期は409で拒否する。遅延したジョブ結果も、資料の存在と実行世代を確認して削除後に復活させない。

## 5 保存と公開の契約

### 保存先

以下は配置案。DATAは設定されたデータルートで、リポジトリ内固定パスではない。ファイル名と公開順序はG0の障害注入設計で確定する。

```text
DATA/
  content/
    documents/<diagram_id>/
      manifest.json                 既存の原本manifestをversion 2へ拡張
      revisions/<revision_id>/
        original.excalidraw
        revision.json
      drafts/<draft_id>/
        versions/<number>/original.excalidraw
      renditions/<rendition_id>/
        original.html
        diagram.svg
        generation.json
      operations/<operation_id>.json
    manifests/library.json
  derived/documents/<diagram_id>/   一覧登録に必須のフォルダー
    search-text.txt
  runtime/documents/<diagram_id>/
    job.json                        Blob同期対象
    diagram-jobs.json               ローカル専用。Blob同期しない
  cache/documents/<diagram_id>/
    thumbnails/
```

`content/documents/<id>/manifest.json`は既に`storage.record_original`が原本manifest（version 1: id、filename、created、key、sha256）として書いている。図表用に同名の別ファイルを新設せず、このmanifestをversion 2へ拡張する。`key`は現在版の`revisions/<revision_id>/original.excalidraw`を指し、`sha256`は現在版の原本ハッシュとする。追加フィールドで現在版、公開済み版、下書きの現在番号、採用済み成果物を参照する。version 1を読む既存処理（backupの原本整合性検査、移行、Blob復元）がversion 2を拒否・誤読しないことをG0で確認する。

`storage.original_file(folder, suffix)`は`content/documents/<id>/original<suffix>`を固定で返し、説明・翻訳・原本配信等の多数の呼出し元が使う。図表ではmanifestの`key`から現在版を解決する経路を`storage`に1か所追加し、呼出し元ごとに版ディレクトリを探す分岐を書かない。図表に`original.excalidraw`の写しを置いて二つ目の正本を作る案は採らない。

revision.json等の不変データを書き終えてからmanifestを原子的に置き換える。未参照の途中ファイルは公開しない。Blobミラーの公開順位（通常ファイル→`manifest.json`→`job.json`→`manifests/`）は本計画の公開順序と一致しているため、そのまま利用する。

操作結果はmanifestに埋め込まず、`operations/<operation_id>.json`として不変に書いてからmanifestの公開で参照する。manifestを保存ごとに肥大化させず、期限切れ結果の削除をmanifest更新と分けられる。

表示名・配置の正本は既存library.jsonとする。既存の`storage.document_folders`は`runtime/documents/<id>/job.json`と`derived/documents/<id>/`の両方がある資料だけを一覧に出すため、図表の新規作成でも両方を作る。job.jsonは既存一覧との接続と処理状態に使い、図表の現在版を決める正本にしない。図表を文書変換したことにする架空の処理結果や二つ目の独立ファイル一覧は作らない。旧形式（`DATA/<id>/`直下）の資料フォルダーには図表を作らない。

Blobミラーがruntimeから同期するのは`job.json`と`explanation.sqlite`だけである。AIジョブ状態`diagram-jobs.json`は同期せず、Blob復元後のジョブは存在しないか中断として扱う。

下書きもcontentに保持し、バックアップとBlobミラーの対象にする。索引だけの消失で編集内容を失わない。下書きは文書と同じアクセス範囲であり、作成者だけの私有下書きという新しい権限モデルは導入しない。

Excalidrawの画像はfilesにbase64で入るため、下書き更新と版ごとに全量を書くと容量とBlob転送が版数に比例して増える。資料ごとの有効な下書きは1件、下書きのversionsは最新3件までとする（2026年10月20日決定）。自動保存は入力停止から2秒後、最短15秒間隔で、差分がある時だけ書く。確定版は版ごとに標準`.excalidraw`全量を保存する（同日決定）。版間で画像を共有する内部形式は`.excalidraw`の標準性と復元を複雑にするため初期は採らず、P2で実測した容量を基に再判断する。

採用して保存したLLM生成HTML/SVGは非決定的な成果物なのでcontentに置く。プレビュー・サムネイルだけを再生成可能なcacheにする。成果物は生成元revision_idとSHA-256を持ち、古い版由来のものを現在版のプレビューへ自動流用しない。

### 原本互換性

標準のtype、version、elements、appState、filesを持つ`.excalidraw`を出力する。アプリの版・生成履歴は外側のmanifestへ置き、独自envelopeを`.excalidraw`原本として出さない。

取込時の原本バイトは初版として保持する。編集保存では公式serializerの標準化を許すが、ID、文字、接続、画像、対応可能な未知フィールドの保持を差分で検査する。新しい要素型や破壊的な互換性差は読み取り専用または明示エラーにし、黙って再保存しない。

`storage.original_file`だけでなく`source_folder`、`record_original`、原本配信、複製、移行、backupの原本整合性検査も確認する。版ディレクトリ導入後に現在版が旧originalファイルへ戻ったり、キャッシュから古い原本を返したりしないことを試験する。PDF等の既存原本は移行しない。

### 版と競合

| 識別子 | 用途 |
| --- | --- |
| diagram_id | 既存資料IDと同じ32桁のUUID hex。名前・フォルダ変更でも維持 |
| revision_id | 保存した不変版。親版、原本ハッシュ、作成日時・主体、形式バージョンを持つ |
| source_etag | 現在版と公開世代を検査する不透明な値 |
| draft_idとdraft_version | 編集中の下書きと、その更新番号 |
| draft_etag | 下書き更新と確定の比較条件 |
| rendition_id | 生成成果物。生成元版、用途、能力、欠落情報を持つ |
| operation_idとidempotency_key | 再送による重複作成の防止と結果確認 |

Excalidraw要素のversion、資料の版、HTTP ETag、Blob ETagを相互に代用しない。下書き更新はdraft_etagを、確定はdraft_etagと基準source_etagの両方を、同じ排他区間で検査する。競合時は入力を保持し、自動上書き・自動マージ・基準版だけの付け替えを行わない。

過去版復元は対象版の内容から新しい版を作り、restored_fromを記録する。過去版を更新しない。名前と配置は現在の管理情報を保ち、原本・版に属する情報の復元と分ける。

第1段階から不変版と現在版参照の最小構造を使う。第2段階で下書き・履歴UIを追加し、第1段階で単一原本を上書きして後から全面移行する実装を避ける。

### 確定と障害回復

既存のexclusive_writeと資料操作のロック順を使い、保存、資料操作、バックアップを協調させる。排他制御を通らない別Writerを追加しない。

1. 認証、対象、基準版、サイズ、構造、参照、保存容量を検査する。
2. 一時領域へ不変ペイロードと版情報を書き、必要なflushを行って確定位置へ置く。
3. 排他区間で基準版を再確認し、manifestに現在版・下書き状態・操作結果をまとめて公開する。
4. 一覧への登録と処理表示を整合させ、保存成功と検索・プレビュー処理の状態を分けて返す。

新規作成では原本manifest、library登録、job登録の複数ファイルを扱うため、単なるreplaceだけで全体を原子的とは主張しない。G0で既存登録方式を確認し、資料作成の中断を検出できる記録と再開・後片付け手順を定める。読取りでは公開完了した資料だけを表示する。成功応答前の強制終了、公開後のjob更新失敗、再送を必須試験にする。一般用途のトランザクション基盤は作らない。

更新の操作結果は、`operations/`配下の結果ファイルを参照するmanifestの公開と一緒に確定する。初期の新規作成は呼出し元が資料IDと冪等性キーを送信前に確保し、再送でも維持する。保存済みの同じキー・要求ハッシュなら元の結果を返し、内容違いは409にする。同じ操作の成功結果は、古くなったETagやIf-None-Matchの新規条件で拒否する前に照合する。キーから権限を作らず、結果再取得時も現在の権限を検査する。操作結果の保持期間案は7日間とし、G0でサイズ上限と期限切れ後の挙動を固定する。

Blob同期では不変版、資料manifest、表示用manifestの依存順序を守る。ローカル復元・Blob復元でruntimeのjob.jsonが欠けても、図表のcontentから一覧接続を再構築できるようにする。バックアップの秘密ファイル名除外規則が、管理下の原本や添付を落とさないことも確認する。

## 6 HTTPと将来のMCP契約

以下は実装案。ローカルの入口は`/api/diagrams`、将来の認可付き公開は`/api/v1/diagrams`とし、既存Markdown操作と分ける。具体的なスキーマはG0と各段階の契約試験で固定する。

| 操作 | HTTP案 | 条件 |
| --- | --- | --- |
| 一覧とファイル操作 | 既存/api/library、/api/library/operations | 既存フォルダと32桁資料IDを使用 |
| 空の図または取込内容で作成 | PUT /api/diagrams/{id} | If-None-Match: *、冪等性キー、事前確保したID、名前・保存先・標準シーン |
| 情報と原本 | GET /api/diagrams/{id}、GET .../{id}/content | ETag、現在版、ハッシュ、形式、編集能力。任意の過去版は版一覧経由で明示指定 |
| 第1段階の保存 | POST .../{id}/revisions | If-Matchと基準版、標準シーン、冪等性キー。不変版を作る |
| 下書き作成と取得 | POST .../{id}/drafts、GET .../{id}/drafts/{draft_id} | 基準版・source_etag。下書きは資料に属する |
| 下書き更新 | PUT .../{id}/drafts/{draft_id} | If-Matchはdraft_etag。部分更新も基準ハッシュとIDで検証 |
| 下書き確定 | POST .../{id}/drafts/{draft_id}/commit | draft_etagとsource_etagの両方を検査 |
| 版一覧と復元 | GET .../{id}/revisions、POST .../{id}/restore | ページング。復元は現在ETag付きで新しい版を作成 |
| 書出し | GET .../{id}/exports/{format} | 対象版を固定し、対応形式だけ許可。利用者のブラウザーでのローカル生成も同じ版に対応させる |
| AI生成と再デザイン | POST .../{id}/generations | 操作種別、基準版または下書き番号、選択ID、指示、種類・デザイン指定、冪等性キー |
| ジョブと採用 | GET .../{id}/generations/{job_id}、POST .../{id}/generations/{job_id}/apply | 永続ジョブ。apply時にも基準版・下書きETagと結果ハッシュを検査 |
| 成果物取得 | GET .../{id}/renditions/{rendition_id} | 認可、生成元版、表示用と原本ダウンロードの区別 |
| 操作結果照会 | GET .../{id}/operations/{operation_id} | 操作主体と現在権限を検証。再送キーによる確認方法も契約に含める |

第2段階のGUIは下書きと確定へ接続し、第1段階の保存ルートも同じ版確定関数を使う。別々の保存機構を残さない。外部自動化に新しい人手承認規則は追加せず、GUIでは利用者の保存・適用操作に従う。

更新条件なしは428、古いETagは412、参照不整合やキー衝突は409、非対応形式は415、容量は413、不正内容は422を基本にする。429・503等の失敗時は入力と操作キーを保持する。プレビューや索引の失敗で、成功した原本保存を失敗扱いに戻さない。

ローカルのシーン送受信上限と、Hono・MCPのJSONメッセージ上限を分ける。現行の外部JSON上限2 MiBを無条件に拡大せず、大きいシーンはHTTPのバイナリ送受信・既存transfer方式の図表対応で渡し、MCPには資料ID・版・ハッシュと操作結果を返す。base64やJSONエスケープ後の実サイズで検査し、図表16 MiBの上限がそのままMCPへ送信可能な意味にならないよう能力に明記する。第5段階で部分編集にも収まらない場合のエラーとHTTPへの引渡しを試験し、独自分割転送基盤は追加しない。

第5段階ではHonoがBearer、actor、action、scopeを検査し、Pythonの非公開の図表操作へ固定ルートで委譲する。Pythonは信頼できる接続から渡された主体・許可範囲と資料のscopeを検証し、モデルやブラウザーが自己申告した権限を使わない。公開対象には明示したscopeを付け、未設定のローカル図表は自動公開しない。原本・全版・成果物・ジョブ・検索にも同じ認可を適用する。

既存processorは別サービスなので、図表のWriterをそちらへ移さない。Honoの資格情報だけでローカル画面の任意APIを呼べる構成にもせず、公開アダプターと非公開業務操作の接続・ネットワーク境界を第5段階の開始条件として確定する。MCPは同じHTTP業務操作を呼び、ストレージを直接編集しない。

## 7 AI生成とAI編集

### 実行方式

既存Providerの構造化応答、一時作業領域、プロセス取消、使用量取得を利用する。アプリが固定した上流資源の必要部分を指示として渡し、LLMには図表の意味、種類、構成、配置、変更対象を判断させる。上流の初回ブランド確認や型選択は設定画面と生成要求へ反映し、毎回同じ確認を繰り返さない。共有Skillファイルや利用者ホームのprofileをジョブから書き換えない。

現在のCodex Providerはツール使用を許さない。Python補助処理はアプリが呼ぶ。LLMが出力したコード、コマンド、URL、ファイルパスをそのまま実行しない。記事用managerのcurrentや取消状態が単一処理を前提にしている点をG0で確認し、図表と記事で取消対象を取り違えない実行単位にする。既存実行部品を利用するためだけに記事用スキーマへ図表を押し込まない。

ローカルLLMは将来のProvider追加として扱う。Skill資源は利用できるが、現状のOpenAI接続実装をモデル名の変更だけでローカル対応とはしない。

### 二つの生成契約

| 操作 | 入力と結果 |
| --- | --- |
| 編集可能な生成・部分編集 | 現在シーン、選択ID、基準版、保持条件から、add/update/deleteと対象ID、変更フィールドを持つ構造化操作を返す |
| Diagram Design再デザイン | 抽出した内容と種類・デザイン・サイズ・詳細度・読者指定からHTML/SVGと欠落・統合の報告を返す |

AI操作の構造検証では重複ID、未知ID、削除後の参照切れ、対象外更新、不正数値を拒否し、全件適用または全件不適用にする。意味判断を役割名辞書やユーザー発話の正規表現で代替しない。新規図形の補完には公式APIを使い、convertToExcalidrawElementsは`regenerateIds: false`で呼んで既定のID再生成を避ける。AI変更一式は1回の`updateScene`で`captureUpdate: CaptureUpdateAction.IMMEDIATELY`として適用し、1回のUndo単位にする。0.18.1でのAPI名と挙動はP1の実ブラウザー試験で確認する。文字と接続のbinding、既存version、Undo取込方法を試験する。

生成要求は基準のシーンを固定する。待機中にGUI編集が進んだ場合は古い結果を自動適用せず、結果を残して競合を表示する。再デザインで省略されたノードやラベルは、元シーンを削除する根拠にしない。

### 予算と処理状態

指示全文、style guide、flowchart参照、templateの合計はo200k_baseで14,538トークンだった。取込仕様とoutput specの追加は7,697、semantic patternsの追加は3,361。必要な参照だけを渡し、全文資源を常時すべて積まない。

既存Explanationの入力1万トークン予算をそのまま使うと不足しうる。complete_structured直呼びが予算を自動強制するとも扱わない。図表の予算を実行前と実行後に検査する。

初期の推奨値は入力総量32,000トークン、構造化応答100,000バイト、1回180秒、1操作最大2回・全体600秒、図表AI実行1件・待機2件とする。内訳は上流指示・参照で約15,000、シーン要約で最大約12,000、利用者指示と出力契約で残りを想定する。実測保証値ではなく、G0でモデル能力・実際のフレーミング・既存実行資源と照合して確定する。

シーン要約は要素あたり約25トークン（ID、種類、文字、座標、寸法、binding）を見込み、AIへ全体を渡せるのは約500要素までとする。それを超える図表では選択範囲を必須にし、全体の自動切り詰めはしない。構造化応答100,000バイトは1回で約200〜300要素の追加・更新に相当する。上限超過を切り詰めて成功させず、保存済み原本は保持する。失敗による再試行も使用量へ計上する。

queued、running、succeeded、failed、cancelledを永続状態で管理し、原本保存とは区別する。再起動時に外部LLM要求を無条件で再送しない。結果不明の実行はinterrupted相当として利用者に示し、同じ課金要求の重複を避ける。画面内だけの進捗状態を正本にしない。

## 8 安全性と依存関係

### 入力と表示

- JSONの総容量、要素数、入れ子、数値の有限性、座標と画像寸法、画像MIME・実体、参照関係、ID一意性を検証する。復元APIの補完はサーバー検証の代用にしない。
- files内の画像はPNG、JPEG、WebP、GIFの許可リストとし、宣言MIMEと実体の一致を確認する。Excalidrawが受け付ける`image/svg+xml`は、第3段階で決める安全化を通すまで取込・保存時に拒否する。
- 要素の`link`は`http`、`https`、`mailto`等の許可スキームだけを保存・書出しに通す。Excalidraw画面内の対策に依存せず、サーバー保存と静的HTML・SVG書出しでも検査する。
- HTML/SVGは、DOM/XMLとCSSを扱う許可リスト型の安全化を行う。イベント、script、foreignObject、危険なURL、外部CSS・画像・フォント・埋込、過剰なフィルター等を制御する。正規表現だけのサニタイズは採用しない。
- 生成物をアプリDOMへ直接挿入しない。スクリプト権限とsame-origin権限を与えないiframeと、専用CSPで表示する。既存ビューアー用allow-scriptsをそのまま渡さない。
- 初期は静的HTML/SVGに限定する。上流のアニメーション制御scriptは許可しない。system fontsか同梱フォントを使い、外部通信を不要にする。
- プレビューとダウンロードを別の応答として扱い、Content-Type、Content-Disposition、nosniff、CSPを設定する。隔離iframeでのローカルフォント参照やCORSも確認する。

安全化ライブラリは第3段階の開始前に、SVG、CSS、URL、名前空間、Python互換性、ライセンス、回帰試験で比較して1つに決め、版を固定する。既存のHTMLパーサーだけで任意SVG/CSSを安全化できるとは判断しない。適切な構成が確認できるまで生成物の能動表示は公開しない。

### 実行境界

ラスター画像の書出しは非可逆のWebPを既定、JPEGを選択肢とし、容量を最小にする方針とする（2026年10月20日決定）。PNGは出力しない。WebPは品質0.85、JPEGは品質0.85で透過を白背景に合成する。可逆で小さい出力が必要な場合はSVGを使う。文字の輪郭に圧縮ノイズが出やすいため、既定のscaleは2とする。

ラスター化は利用者のブラウザーで行い、ExcalidrawのexportToCanvasと、安全化済みSVGを隔離した描画からcanvasへ描く経路を使う。サーバー側のexport_png.py、Python Playwright、Chromiumは初期に導入しない。これにより配布容量の大きい追加依存とサーバー側のOS隔離が不要になる。ブラウザーごとのWebPエンコード対応はG0で確認し、未対応ならJPEGへ切り替えたことを表示する。生の生成HTMLはラスター化に使わない。

図表内リンクやブランドURLから秘密付きネットワークへ接続しない。ブランド取込を追加する場合は別の明示操作とし、SSRF、リダイレクト、到達先、応答容量の契約を先に定義する。初期は手動設定した色・書体を利用できればよい。

第1段階の入力上限の推奨値は、標準シーン全体16 MiB（画像filesのbase64を含む）、画像1枚4 MiB、10,000要素、入れ子100、有限座標の絶対値1,000,000以内とする。画像1枚の上限はExcalidraw既定の取込上限に合わせる。ラスター書出しは1辺4,096px、総画素16メガピクセル以内、処理30秒とする。Safari等のcanvas上限を超えない値であり、非可逆圧縮でも数MB以内に収まる。上限値はG0で対応Mac/Linux環境とブラウザーで確認して固定する。上流extractorの2,000ノード等の別上限は、GUI編集上限とは分けて能力・エラーに表示する。

### 配布

Excalidrawとローカルフォントは既存依存・静的配布を使う。ラスター書出しはブラウザーで行うため、Python PlaywrightとChromiumは追加しない。サーバー側でのラスター生成が必要になった時に別途判断する。

Diagram DesignのMIT著作権・許諾文を資源とともに保持し、NOTICEへ追加する。テンプレート、フォント、アイコンなど採用する資産の条件も確認する。[上流LICENSE](https://github.com/cathrynlavery/diagram-design/blob/f2f4d49ea8d632f17cf196ba26599277a93f767b/LICENSE)

## 9 変更予定ファイル

パスはリポジトリルートからの相対パス。既存の別作業を確認してから変更し、ファイル名の衝突を避ける。

| 分類 | パス | 作業 |
| --- | --- | --- |
| 変更 | frontend/src/features/Library.tsx、Upload.tsx、Viewer.tsx | 新規図表、形式表示、取込と編集導線 |
| 変更 | frontend/src/viewer/DocumentViewer.tsx | 図表プレビューと編集画面への接続 |
| 変更 | frontend/src/lib/api.ts、types.ts、contracts.ts、formats.ts、route.ts | API、型、形式、画面遷移 |
| 新規 | frontend/src/diagram/DiagramCanvas.tsxと同階層のテスト | 共用キャンバス、公式serializer、AI変更、書出し |
| 新規 | frontend/src/features/DiagramEditor.tsxと同階層のテスト | 保存、競合、下書き、履歴、AI変更案 |
| 変更 | frontend/src/plate/ui/excalidraw-node.tsx | 共通Canvasへ接続。Wikiのdata形式を維持 |
| 確認と必要時変更 | frontend/src/editor/plate-browser.ts、frontend/scripts/export-static.mjs | フォント、資源パス、配布 |
| 新規 | src/docling_desk/api/diagrams.py | 図表HTTPルート |
| 新規 | src/docling_desk/documents/diagram_models.py、diagram_store.py、diagram_generation.py | 契約、保存・版、AI実行 |
| 新規 | src/docling_desk/preview/diagram_artifacts.py | 安全化と生成物表示。既存preview/diagrams.pyと区別 |
| 変更 | src/docling_desk/storage.py、app.py | manifest version 2、現在版の原本解決、一覧登録、復旧、ルート・実行管理 |
| 変更 | src/docling_desk/documents/library.py、conversion.py、api/uploads.py、api/assets.py | 図表登録、操作、原本配信、形式判定 |
| 変更 | src/docling_desk/knowledge/store.py | 確定版の文字・説明だけを検索へ反映 |
| 変更 | src/docling_desk/web/security.py、blob_mirror.py、operations/backup.py | 安全境界、公開順序、復元・バックアップ |
| 新規 | src/docling_desk/resources/diagram-design/ | 固定した上流指示・必要資源・LICENSE。上流全体の無条件同梱はしない |
| 変更 | pyproject.toml、requirements-lock.txt、NOTICE.md、MANIFEST.in、Dockerfile | 採用した追加依存と資源だけ反映 |
| 新規 | tests/test_diagram_store.py、test_diagram_api.py、test_diagram_security.py、test_diagram_generation.py、tests/fixtures/diagrams/ | 保存・競合・安全性・生成契約・合成資料 |
| 変更または新規 | frontend/e2e/operations.spec.ts、wiki.spec.ts、diagram.spec.ts、playwright.config.ts、frontend/package.json | ファイル操作、Wiki回帰、図表受入。`test:e2e:synthetic`は現在wiki、printing、operationsだけを実行するため、図表projectを追加する |
| 第5段階 | knowledge-api/src/diagrams.ts、app.ts、auth.ts、contracts.ts | 公開アダプターと既存認可への接続。diagrams.tsのみ新規案 |
| 第5段階 | src/docling_desk/viewer/document_tools.py、mcp.py、tool_contract.py、tool_guides.py | 固定業務操作とガイド |
| 第5段階 | contracts/diagram-api.openapi.json、mcp-tools.json、対応する契約検証スクリプトとガイド | 新しいHTTP契約、MCP契約、schema整合。既存契約への参照も追加 |

## 10 段階的な実装と完了条件

### G0 実装前レビューと契約固定

依存は本計画のレビュー。Opusの指摘は対象節、根拠、影響、修正案とともに記録する。利用者がレビュー結果と修正方針を確認するまで製品実装を開始しない。

必要作業は、既存未コミット作業の範囲確認、現行コードへの再照合、保存公開・障害回復手順、APIスキーマ、サイズ・性能予算、対象環境、追加依存候補、検証用fixtureの固定。別作業の不具合は証拠と影響を報告し、本計画の実装理由で修正しない。

部分検証では既存serializerとスキーマを、結合検証では既存Wiki描画保存・資料操作を確認する。現行の未コミットdraw.io取込の基準を採る場合は、その作業の検証状況と境界を確認する。

完了条件は、第12節の実装開始前の論点が決定され、現行の関連ベースラインと失敗の担当範囲が記録されていること。

### P1 独立Excalidrawファイルの作成と手動編集

依存はG0。資料登録、形式識別、共通Canvas、保存・競合検出、最小の不変版、原本ダウンロード、SVG/WebP/JPEG/静的HTML書出しを実装する。AI、Python Playwright、外部APIを必須にしない。図表への複製要求の拒否も実装する。

部分試験は形式・シーン検証、ファイルパス、公式serializerの往復、原本参照、画像・文字・bindingの保持。結合試験は一覧→作成・取込→編集→保存→再表示と、フォルダ操作。通し試験は外部ファイルを取り込み、図形・日本語・接続・画像を編集してアプリ再起動後に再編集し、ダウンロードした原本を外部Excalidrawで開く。

完了条件は、AIなしで通常の資料と同様に管理できること、Undo/Redoが使えること、書出しが閲覧できること、競合・切断時に入力を保持すること、既存Wiki描画が退行しないこと。

### P2 下書きと版管理

依存はP1。下書き、履歴一覧、過去版閲覧・復元、二重ETag、操作結果照会を追加する。既存の削除・backup・Blob処理を、版・下書き・成果物を含めて整える。

部分試験は版作成、復元、ETag、再送とキー衝突。結合試験は2画面での競合、公開前後の強制終了、contentからの一覧復旧、runtime/cacheの消失。通し試験は複数版の保存→再起動→過去版復元→バックアップ・Blob復旧→復旧後の再編集。

完了条件は、復元後も履歴が残ること、中断と再送で二重確定しないこと、原本・現在版・一覧・索引が矛盾しないこと。Blob実検証に必要な環境はエミュレーター等で準備し、スキップを成功としない。

### P3 Diagram DesignによるAI生成と再デザイン

依存はP2と安全化・隔離実行の検証。固定資源、型・デザイン指定、HTML/SVG生成、欠落報告、ジョブ、予算、表示・保存を実装する。生成物のラスター書出しはP1と同じブラウザー経路を使い、安全化済みSVGだけを入力にする。

部分試験は固定応答Providerによる生成契約、危険なHTML/SVG、上流抽出の対応範囲と上限、使用量。結合試験は失敗・切断・取消・再起動・古い生成元の扱い、安全化前のHTMLがブラウザーへ渡らないこと。通し試験は自然言語→生成→プレビュー→保存→再表示→書出し、および既存図からの再デザイン。

完了条件は、生成物が元の版に結びつくこと、欠落・変更内容と編集可能性が表示されること、外部通信・ファイルアクセスの拒否が実環境で確認されること。LLM未設定でもP1・P2が動作すること。

### P4 編集可能なAI生成と部分編集

依存はP1・P2の編集・版契約とP3の実行管理。Excalidraw構造化出力、対象IDの変更、レビュー表示、適用・取消、Undo統合を実装する。生成SVGの逆変換には依存しない。

部分試験はID保持、変更対象、binding、未知ID・未知要素、公式APIによる補完。結合試験はAI待機中の手動編集、遅延応答、1回のUndo、保存競合。通し試験はAI生成→手動移動・文字変更→AI部分編集→Undo/Redo→保存→再読込を繰り返し、対象外要素と意味上の関係を比較する。

完了条件は、対応範囲を明示した上でID・接続・文字・画像・編集可能性を保持すること、対象外変更を防ぐこと。固定テストだけで完了せず、語句・対象・場面を変えた実LLM評価を行い、専用分岐なしで同じ契約が使えることを確認する。

### P5 HTTPとMCP公開とEumenes接続

依存はP1〜P4の単独機能完成。Honoアダプター、信頼できる主体とscope、非公開Writer接続、HTTP/OpenAPI、MCP、ツールガイドを実装する。Eumenesは汎用APIのクライアントとして接続する。

部分試験は契約、認証、action、scope、冪等性。結合試験はHTTPとMCPで同じ版と競合結果が見えること、権限変更・失効が全経路に反映されること。通し試験は外部作成→GUI編集→外部取得→過去版復元。範囲外の本文・版・成果物・ジョブ・検索件数を返さないことも確認する。

完了条件は、図表正本の複製なし、認可迂回なし、Eumenes固有コードの持込みなしで操作できること。P5の完了を、ローカル単独機能の受入に先行させない。

## 11 検証と進め方

### 必須の回帰条件

| 試験対象 | 確認する性質 |
| --- | --- |
| 原本往復 | 標準シーン、画像、group/frame、bound text、接続、customData、未知フィールド、日本語、改行を保持 |
| GUIとAI反復 | ID、接続、対象外の文字・座標・画像、Undo単位、AI応答待ちの競合を検証 |
| 保存中断 | ペイロード保存前後、manifest公開前後、library/job反映前後に失敗を注入し、成功・未公開・復旧を区別 |
| 再送 | 同一要求の再送は同じ結果、異なる内容は衝突、失効後は結果本文を返さない |
| ファイル操作 | フォルダ配下、同名、名前変更、図表の複製拒否、実行中拒否、部分削除失敗を検証 |
| 旧機能 | PDF等の原本取得、資料操作、画像、Wiki内ExcalidrawとMDX保持、draw.io閲覧を回帰検証 |
| 安全性 | script、イベント、CSS URL、名前空間、foreignObject、外部資源、埋込、圧縮展開、巨大画像・座標、深いJSONを拒否・制限 |
| 実行分離 | 私有ファイル読取りと外部通信が失敗し、資格情報が子プロセスへ渡らないことを確認 |
| 配布 | 静的資産、ローカルフォント、固定した上流資源・ライセンス、wheel/Dockerの内容を確認 |
| 復旧 | backupとBlobから復元し、runtime/cache欠落でも原本・履歴・下書き・生成物を保持 |

### 実行入口

以下は実装後の検証入口であり、未作成テストは各段階で追加する。新しいテストprojectや契約ファイルを追加した場合は、同じ段階でCIと検証コマンドへ登録する。

```sh
DOCLING_ENV_FILE=/dev/null .venv/bin/python -m pytest tests/test_diagram_store.py tests/test_diagram_api.py -q
pnpm --dir frontend test:run
pnpm --dir frontend typecheck
pnpm --dir frontend lint
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
DOCLING_ENV_FILE=/dev/null .venv/bin/python -m pytest tests -q
pnpm --dir frontend test:e2e:synthetic
pnpm --dir frontend build
pnpm --dir frontend export:static
.venv/bin/python scripts/verify_package.py
```

第3・4段階で生成・安全性試験、第5段階でknowledge-apiのtest、typecheck、lint、contract:check、integrationとMCP検証を追加する。実LLM、実ブラウザー隔離、Blob、Linux/macOS等の必要環境を試験計画に含め、実行していない条件を合格に含めない。

部分→結合→通しの順で実行する。変更がない計画書作成では製品全体の試験やビルドは実施せず、文書の根拠・リンク・整合性を確認する。実装開始時には関連試験のベースラインを取り直す。

### 調査で確認した試験結果

2026年10月20日の事前調査では、既存preserved-markdown.test.tsの5件、上流verify-excalidraw-import.pyの検証、test-export-svg-standalone.pyの133件が成功した。Excalidrawのサンプル抽出は10ノード・6接続となり、手描き線1、画像本体1、リンク1、削除済み要素1が欠落として計上された。export_svg.pyがscriptとonclickを残す入力に対し、self_check.pyは拒否した。

LLM生成の品質・時間・費用、Python版PNG出力、AIとGUIの反復はこの結果に含まない。調査用のNode直接読込と一時ブラウザー環境ではExcalidraw API反復検証を完了できなかったため、保存・復元の実ブラウザー往復をP1の必須試験に残す。

### 作業管理と停止条件

各段階の開始時に目的、変更範囲、依存、残作業、完了条件を更新する。同じ目的の仮説・実施・結果確認が3ラウンド続いた時、新しい根拠のない同一失敗、検証中の編集、計画逸脱がある時は、次の編集・再実行前に進め方を評価し、証拠と次の判断を現在の会話へ短く報告する。正常な長時間実行は時間だけで停止しない。

担当外の不具合は調査と報告までに留める。要件やテスト期待値を弱めず、続行の根拠がなければ差分、未解決事項、続行条件を報告して停止する。完了時は無関係な変更も含む差分を点検し、語句・個別シナリオの分岐でLLM判断を代替していないことを確認する。

## 12 Opusへのレビュー観点と開始前の決定事項

レビューは文面の校正より、実装の矛盾、保存破損、権限迂回、要件漏れ、検証不足を優先する。本書から参照する現行ソースも確認し、既存未コミット作業と提案を区別する。

| 優先 | 論点 | 確認する判断 |
| --- | --- | --- |
| 必須 | 独立ファイル要件 | .excalidrawがWiki埋込に閉じず、一覧・外部互換・保存再編集までP1に含まれているか |
| 必須 | 保存と障害回復 | manifest、library、job、操作結果の公開順、ロック順、再送、再起動の契約が実装可能か。G0で具体的な中断記録を確定できるか |
| 必須 | 現行storage互換 | revision配下導入がsource_folder、原本配信、複製、移行、backup、Blobを壊さないか |
| 必須 | 正本責任 | Python図表と既存Hono文書の境界が明確か。P5のアダプターで原本と権限の二重管理が生じないか |
| 必須 | AI編集 | 上流の再デザインと編集可能な構造化変更を区別しているか。対象外変更、ID再生成、binding破損を検出できるか |
| 必須 | 生成物の安全性 | sanitizer、CSP、iframe、ブラウザー内ラスター化で境界を守れるか。追加依存の選定条件が十分か |
| 必須 | 段階と試験 | P1から最小の版構造を導入する案とP2の追加範囲が過大でないか。外部往復・実LLM・実隔離試験が完了条件に残っているか |
| 決定済み | 複製 | 初期範囲に含めない。図表への複製要求は拒否する |
| 決定済み | 下書き | 有効な下書き1件、versions最新3件 |
| 決定済み | 画像の版間重複 | 版ごとに標準`.excalidraw`全量を保存し、P2の実測で再判断する |
| 決定済み | ラスター書出し | 非可逆WebPを既定、JPEGを選択肢とし、PNGは出さない。ブラウザーで生成し、Python Playwrightを導入しない |
| 決定 | 制限値 | 第7節・第8節の推奨値（シーン16 MiB、画像1枚4 MiB、10,000要素、AI全体入力約500要素、書出し16メガピクセル、AI 32,000トークン・180秒）でよいか |
| 決定 | 追加依存 | HTML/SVG/CSSの安全化方式と、必要資産・配布容量 |
| 確認 | 不要な実装 | 独立IR、大型抽象層、型別renderer、意味判断の辞書・正規表現が増えていないか |

指摘は「重要度、対象節またはファイル、再現条件・根拠、影響、最小の修正案」の形で残す。実装開始を妨げる指摘と改善提案を分け、判断を要する項目は選択肢と推奨を添える。レビュー自体は実装・コミット・プッシュの許可として扱わない。

## 13 Opusレビュー結果

2026年10月20日。本書の参照ファイルはすべて実在を確認した。Excalidraw 0.18.1、`MAX_JSON`=2 MiB、`exclusive_write`、`original_file`・`source_folder`・`record_original`、`test:e2e:synthetic`等の記述も現行コードと一致する。全体として目的、対象外、段階、完了条件の構成は妥当で、大型抽象層や意味判断の辞書は含まれていない。以下の指摘は本書へ反映済み。

| 重要度 | 対象 | 根拠 | 影響 | 反映した修正 |
| --- | --- | --- | --- | --- |
| 着手阻害 | 5 保存先 | `storage.record_original`が既に`content/documents/<id>/manifest.json`を原本manifest version 1として書く | 図表manifestと同名衝突し、backup整合性検査・移行・Blob復元が誤読する | 既存manifestをversion 2へ拡張し、`key`で現在版を指す方針に変更 |
| 着手阻害 | 5 保存先、9 | `storage.original_file`は`original<suffix>`を固定で返し、説明・翻訳・原本配信等が直接使う | revisions導入で原本が見つからないか、呼出し元ごとの分岐が増える | manifestの`key`から解決する経路を`storage`に1か所追加。写しによる二重正本は採らない |
| 着手阻害 | 5 保存先 | `storage.document_folders`は`runtime/.../job.json`と`derived/documents/<id>/`の両方を要求 | 新規図表が一覧に出ない、またはBlob復元後に消える | 両方を作ることを契約に明記。旧形式フォルダーには作らない |
| 重要 | 5 下書き | Excalidrawの画像はfilesにbase64で入る | 下書き・版ごとの全量保存で容量とBlob転送が膨らむ | 下書き1件・versions最新3件・差分時のみ保存の初期案と、P2実測後の再判断を追加 |
| 重要 | 5 確定 | 操作結果をmanifestへ7日分まとめる案 | manifestの肥大化と保存ごとのBlob転送 | `operations/<operation_id>.json`へ分離し、manifestから参照 |
| 重要 | 8 入力 | Excalidrawはfilesに`image/svg+xml`、要素に`link`を持てる | 未安全化SVGや危険なURLが保存・書出し経由で流れる | 画像MIME許可リスト、SVG画像の初期拒否、linkスキーム検査を追加 |
| 改善 | 5 保存先 | Blobミラーはruntimeから`job.json`と`explanation.sqlite`だけを同期 | AIジョブ状態の復元可否が曖昧 | `diagram-jobs.json`は同期せず、復元後は中断扱いと明記 |
| 改善 | 7 AI編集 | Undo単位とID維持の実現方法が抽象的 | 実装時に既定のID再生成やUndo分割が起きやすい | `regenerateIds: false`と`captureUpdate`による1回適用を明記し、P1で実ブラウザー確認 |
| 改善 | 9 変更予定 | `test:e2e:synthetic`はwiki、printing、operationsのみ | 図表e2eを追加してもCIで実行されない | `frontend/package.json`を変更対象に追加 |

## 14 残作業

- [x] 既存コードと対象OSSの調査。
- [x] 独立したExcalidrawファイルを必須範囲へ追加。
- [x] 保存、競合、安全境界、変更箇所、段階と試験を計画化。
- [x] Opusによる実装前レビュー。
- [x] レビュー指摘の本書への反映。
- [x] 開始前の決定事項（第12節）の確定。上限値は推奨値で実装し、実環境での確認は残す。
- [x] P1・P2の実装（契約は[図表 P1・P2 実装契約](diagram-p1-p2-contract.md)）。単体・結合試験、型検査、lint、build、パッケージ検証は成功。Playwright e2e（`--project=diagram`）と実ブラウザーでの往復確認は未実施。
- [ ] P1・P2のe2eと実ブラウザー確認、外部Excalidrawでの原本再編集。
- [ ] 図表本文の全文検索への接続（第4節。P3までに実施）。
- [ ] 利用者による実装着手の指示。
- [ ] G0のベースラインと保存・障害回復契約の固定。
- [ ] P1からP4の実装とdocling-desk単独での受入。
- [ ] P5の汎用HTTP/MCP公開とEumenes接続。
