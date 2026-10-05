# Linux・Docker検証記録

2026年10月3日、Docker Desktop上でLinux arm64とLinux amd64をビルド・実行した。amd64はApple Silicon上のエミュレーションであり、Azure実機の性能測定ではない。イメージID、実装のSHA256、資料ごとの結果は `verification.json` に記録している。

| 確認 | 結果 |
|---|---|
| 変更前のmacOSプロジェクトテスト | 276 passed |
| 最終macOSテスト | 284 passed、Linux実描画テスト1件skip |
| 最終Linux arm64テスト | 283 passed、macOS WebKit実描画テスト2件skip |
| 最終Linux amd64専用テスト | 9 passed。実LibreOffice変換を含む |
| 依存整合性 | コンテナ内 `pip check` 成功。PyTorch 2.14.1+cpu、CUDAなし |
| 合成資料のAPI疎通 | PDF / PPTX / XLSX / DOCX / MD / TXT / スキャンPDFの7件が両アーキテクチャでsuccess |
| ブラウザー | 日本語PPTX、サムネイル、スライド移動、XLSXのグラフとシート切り替えを確認 |
| OCR | 画像だけのPDFから英語と日本語を抽出。日本語の文字間空白を許容して本文を照合 |
| データ永続化 | コンテナ再作成後、12件の原本と抽出テキスト計24ファイルのSHA256が一致 |
| 失敗時 | LibreOffice未導入・タイムアウト・出力欠落をエラーとして確認 |
| Office境界条件 | 非表示PPTXスライド、非表示シート・行・列、結合セル、翻訳用C3参照、原本不変を実描画で確認 |
| 静的確認 | 変更したPythonのRuff・tyと `git diff --check` 成功 |

途中の検証で、Linux側の循環import、100バイト未満の正常なWebPの拒否、キャッシュの実際の描画方式と説明文の不一致、LibreOfficeの非表示列をまたぐ結合幅、非表示行をまたぐ翻訳セル対応を修正した。説明処理の既存テストが並行ビルド中に5秒待ちの制限で一度失敗したが、ビルド後の全件再実行では成功した。待ち時間やプロダクト側の解説処理は変更していない。

`pytest` だけではサブモジュールと過去QAのテストコピーまで収集するため、プロジェクトの回帰確認は明示的に `tests` を指定した。

再現コマンド:

```sh
# ホストの専用環境
.venv/bin/python -m pytest -q tests

# Linuxのプロジェクト回帰確認。fixtureと画面配布用スクリプトだけを読み取り専用で渡す
docker run --rm --entrypoint python \
  -v "$PWD/tests:/app/tests:ro" \
  -v "$PWD/frontend/scripts:/app/frontend/scripts:ro" \
  -v "$PWD/pyproject.toml:/app/pyproject.toml:ro" \
  -e DOCLING_DATA_DIR=/tmp/test-data \
  docling-desk:linux -m pytest -q -p no:cacheprovider tests

# Azure向けamd64版の実Office変換とLinux固有の回帰確認
docker run --rm --platform linux/amd64 --entrypoint python \
  -v "$PWD/tests:/app/tests:ro" \
  -v "$PWD/pyproject.toml:/app/pyproject.toml:ro" \
  -e DOCLING_DATA_DIR=/tmp/test-data \
  docling-desk:azure-amd64 -m pytest -q -p no:cacheprovider \
  tests/test_linux.py tests/test_linux_integration.py
```

変換APIの再確認は `docs/linux-docker.md` の `scripts/smoke_linux.py` 手順を使う。検証後もComposeのarm64コンテナを18765番ポートで稼働させ、amd64確認用の一時コンテナは停止・削除した。両アーキテクチャのイメージと検証データ用ボリュームは残している。

Azureクラウドでのデプロイ、Azure Filesのマウント、外部API資格情報を使った翻訳・解説は未確認。Microsoft OfficeとLibreOfficeの描画の完全一致は検証対象にしていない。
