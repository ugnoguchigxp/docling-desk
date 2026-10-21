# 参照元

従来システムの翻訳・Wiki処理を参照し、Pythonとして実装しています。Notion取り込みのPython処理は保存先・資料群設定・呼び出し方法を変更して利用しています。参照元は非公開のため、コミットやファイルハッシュは公開していません(`integrations/wiki/reference-assets.json` を参照。このファイルはリポジトリにあるだけで、wheel・sdist・Dockerイメージには含まれません)。

従来システムが利用したcontextStillとhono-standardのMITライセンスを `licenses/` に保持しています。wiki_batch はBunと従来システムのAPIサーバーを実行時に使いません。別経路の `knowledge-api`(Bun、Hono、zod)の告知は、リポジトリ直下の `NOTICE.md` に記載します。このディレクトリの `licenses/` は、wheel と Dockerイメージの両方に含まれます。
