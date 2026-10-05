# Azure OCRの設定手順書

作成日・公式資料確認日：2026年10月3日。

[Azure OCR対応計画書](../azure-ocr-implementation-plan.md)に沿って、Azure Document Intelligenceのリソース、認証、通信、予算を設定する手順です。Azure Portalでの操作を中心に、アプリの実装前でも実施できる接続確認を含めています。

**Azure OCRの実装は完了しています。** PDFとOffice埋め込み画像に対応し、設定がそろうとアップロード画面でAzure Readを選べます。ナレッジAPI構成は受付・結果取得の永続状態を管理します。2026年10月4日に、macOSとLinux amd64コンテナから実Azure OCRを使う合成資料の検証を行いました。Azure VM実機でのネットワーク・マネージドID認証は未確認です。

## 手順の進め方

| 順序 | 作業 | 実施時期 |
|---|---|---|
| 1 | 作成先、料金プラン、接続方法を決める | 今すぐ準備可能 |
| 2 | Document Intelligenceを作成する | 今すぐ実施可能 |
| 3 | 接続先と検証用キーを確認する | 今すぐ実施可能 |
| 4 | VMのマネージドIDとアクセス権を設定する | 配置先VMがある場合 |
| 5 | 通信範囲を設定する | 接続確認前 |
| 6 | 予算と運用条件を設定する | 接続確認前 |
| 7 | 合成画像でAzureへの接続を確認する | アプリ実装前でも可能 |
| 8 | アプリへ設定を渡して検証する | OCR実装後 |
| 9 | 設定記録を残して段階導入する | 検証完了後 |

本番の配置先は、OCR計画に合わせて**Azure VM上のDocker**を想定します。[Linux配置資料](linux-docker.md)にあるContainer Apps案を採用する場合は、VM用の認証確認をそのまま使わず、Container AppsのマネージドIDと通信経路で検証してください。

## 1 作成前に決める項目

次の表を埋めてから作成します。名前と通知閾値は本書の例であり、組織の命名規則・予算に合わせて変更してください。

| 項目 | 設定例または判断方法 |
|---|---|
| Azureテナント・サブスクリプション | 利用部署の契約。作成前にPortal右上のディレクトリを確認 |
| リソースグループ | 検証用 `rg-docling-ocr-dev`、本番用は別グループ |
| リソース名 | `di-docling-ocr-dev-<識別子>`。カスタムサブドメインは利用可能な名前を指定 |
| リージョン | 例：Japan East。作成画面で利用可能か確認し、資料の保存地域要件に合わせる |
| 料金プラン | 少量の接続確認はF0。本番の候補はS0 |
| 認証 | ローカル検証は専用APIキー。本番VMはマネージドID |
| 通信 | 検証は送信元IP制限。本番の候補はプライベートエンドポイント |
| 月額予算・通知先 | 担当者が金額と通知先を決定。50・80・100%での通知を例とする |
| 利用資料 | まずは架空の日本語・英語・数字を含む画像1枚 |

操作する担当者には、リソース作成・設定変更の権限が必要です。マネージドIDへのロール割り当てには、対象範囲でロールを割り当てられる権限（Role Based Access Control Administratorなど）が別途必要です。Contributorだけではロール割り当てはできません。

本構成では`prebuilt-read`を使います。Azure OpenAIのデプロイ作成、カスタムモデルの学習、OCR入力を置くBlob Storageの作成は、このReadへの画像バイト送信には必要ありません。アプリの資料保存先は別に用意します。

### F0とS0の選択

| 項目 | F0 | S0 |
|---|---|---|
| 1要求の入力サイズ上限 | 4 MB | 500 MB |
| PDF等の1要求での分析ページ上限 | 最初の2ページ | 最大2,000ページ |
| 分析POSTの既定上限 | 1回/秒 | 15回/秒 |
| 結果取得GETの既定上限 | 1回/秒 | 50回/秒 |

これらはAzure側の上限です。アプリの原本上限は計画どおり50 MiB・PDF100ページとし、AzureへはOCR対象ページをPNGで一枚ずつ送ります。F0でも、各PNGが4 MBを超えれば送れません。画像の幅・高さは各50〜10,000ピクセルの範囲で検査します。F0の「最初の2ページ」は1要求への制約であり、月間の無料利用枠とは別です。[サービス制限](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/service-limits?view=doc-intel-4.0.0)、[Read入力要件](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/read?view=doc-intel-4.0.0#input-requirements)。

## 2 Document Intelligenceを作成する

1. [Azure Portal](https://portal.azure.com/)へサインインします。
2. 「リソースの作成」で **Document Intelligence** を検索し、作成画面を開きます。旧名称のForm Recognizerが表示される場合があります。
3. サブスクリプション、リソースグループ、リージョン、名前、料金レベルを入力します。
4. Entra認証を使うため、**Document Intelligenceの単一サービスリソース**を選びます。本書では複数サービス共通のリソースを前提にしません。
5. ネットワークの選択欄があれば、手順5で決めた接続方法を設定します。実際の接続確認前に通信制限を完成させます。
6. 「確認および作成」で設定と料金レベルを確認し、「作成」を選びます。
7. デプロイ完了後、「リソースに移動」を選びます。
8. 「概要」「プロパティ」でリソースID、リージョン、料金レベルを記録します。

完了条件：作成したリソースの状態が正常で、予定したリージョンと料金レベルになっていること。既存の同名リソースを利用する場合も、この確認を行います。[リソース作成の公式手順](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/how-to-guides/create-document-intelligence-resource?view=doc-intel-4.0.0)。

## 3 接続先と検証用キーを確認する

1. 作成したDocument Intelligenceで「キーとエンドポイント」を開きます。
2. エンドポイントをコピーします。本番のEntra認証では、次のようなリソース固有のカスタムサブドメインが必要です。

   ```text
   https://<リソース固有名>.cognitiveservices.azure.com/
   ```

3. `https://<region>.api.cognitive.microsoft.com/`のような地域共通の接続先になっている場合は、カスタムサブドメインを設定してから進みます。設定欄はリソースの「概要」「プロパティ」等で確認し、URLを推測して書き換えず、Portalが示す値を使います。
4. ローカルでキー認証を検証する場合だけ、Key 1またはKey 2を取得し、組織の秘密情報管理先へ保存します。キーをこの手順書、Git、チャット、コンテナイメージへ書き込まないでください。
5. キー認証が組織のポリシーで無効なら、キー検証は省略し、VMのマネージドIDで手順7を行います。

接続先はAzure OpenAI用の`*.openai.azure.com`と別です。Azure OpenAIのキーやデプロイ名をOCR設定へ流用しません。カスタムサブドメインはEntra認証の要件です。[Document Intelligence v4 SDKの認証](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/sdk-overview-v4-0?view=doc-intel-4.0.0)。

## 4 本番VMのマネージドIDと権限を設定する

マネージドIDは、APIキーをアプリへ持たせず、VM自身のIDでAzureへ接続する仕組みです。**IDを有効にするのは、アプリを動かすVMです。** Document Intelligence自身のIDを有効にするだけでは、VMからのOCR要求に権限は付きません。

### システム割り当てIDを使う場合

1. Azure Portalで、アプリを動かすVMを開きます。
2. 「ID」または「Identity」から「システム割り当て」を開きます。
3. 状態を「オン」にして保存します。
4. 表示されたオブジェクトID（principal ID）を記録します。
5. **Document Intelligenceリソース側**の「アクセス制御（IAM）」を開きます。
6. 「追加」→「ロールの割り当ての追加」を選びます。
7. ロールに **Cognitive Services User** を選びます。
8. 割り当て先を「マネージドID」にし、対象のサブスクリプションとVMを選びます。
9. 「レビューと割り当て」で保存します。
10. ロール一覧で、対象VMのIDに、**このDocument Intelligenceリソースの範囲**で割り当てられたことを確認します。

このロールはv4 SDK資料で案内されるデータアクセス用のロールです。OwnerやContributorの管理権限だけをOCR実行権限の代わりにしません。Cognitive Services Userにはキー一覧取得や広いデータ操作も含まれるため、サブスクリプション全体へ付与せず、このリソースに限定します。より狭いカスタムロールが必要な環境では、分析・結果取得・必要な削除の操作権限を確認して作成し、同じ接続試験を通します。[ロール定義](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/ai-machine-learning#cognitive-services-user)。

### ユーザー割り当てIDを使う場合

既存の共通IDを使う運用なら、Portalの「マネージドID」で対象IDを確認し、VMの「ID」→「ユーザー割り当て」から関連付けます。そのIDに対して、上記と同じDocument Intelligenceリソースのロールを付与します。

アプリ設定に使うのは**クライアントID**です。ロール割り当て対象のオブジェクトIDと取り違えないでください。システム割り当てIDを選ぶ場合、OCR用クライアントIDは未設定にします。[マネージドIDの設定とPython認証](https://learn.microsoft.com/en-us/azure/developer/python/sdk/authentication/system-assigned-managed-identity)。

## 5 接続できるネットワークを設定する

認証と通信許可は両方必要です。IDが正しくても、ファイアウォールやDNSが合っていなければ接続できません。

### ローカル検証で送信元IPを制限する場合

1. Document Intelligenceの「ネットワーク」を開きます。
2. パブリックアクセスを「選択したネットワークとプライベートエンドポイント」相当の設定にします。Portalの表記は表示言語やリソースにより異なります。
3. 接続確認を実行するPCの**インターネット側の送信元IP**を追加して保存します。PCの`192.168.*`等のLAN内アドレスではありません。
4. VMからも同じ公開エンドポイントへ接続する場合、VMの外向き通信で使われる固定IP（NAT Gateway等）も許可します。VMの受信用IPと一致するとは限りません。
5. VPNや社内プロキシを使う場合、その経路での送信元IPを確認します。

検証終了後は、一時的なPCの許可IPを削除します。[Document Intelligenceのネットワーク設定](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/authentication/managed-identities-secured-access?view=doc-intel-4.0.0)。

### 本番でプライベートエンドポイントを使う場合

1. Document Intelligenceの「ネットワーク」→「プライベートエンドポイント接続」で追加を選びます。
2. アプリのVMから到達できるVNetとサブネットを選びます。対象サブリソースは`account`です。
3. プライベートDNSとの統合を有効にします。`*.cognitiveservices.azure.com`用のゾーンは`privatelink.cognitiveservices.azure.com`です。
4. そのDNSゾーンをVMのVNetへリンクします。独自DNSを使う場合は、AzureのプライベートDNSへ解決を転送する設定も必要です。
5. 接続が承認済みであることを確認します。
6. VMとアプリのコンテナの両方で、通常のエンドポイント名がプライベートエンドポイントのIPへ解決され、TCP 443で通信できることを確認します。
7. この経路で手順7の分析が成功してから、パブリックアクセスを無効にし、同じ分析を再確認します。

アプリの接続URLは通常の`https://<リソース固有名>.cognitiveservices.azure.com/`を使います。プライベートIPや`privatelink`の名前へ置き換えません。ローカルPCからはVPN等でVNetへ到達できる経路とDNSが必要です。一般のCloud Shellから接続できることを前提にしません。[プライベートDNSの値](https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-dns)、[DNS統合方法](https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-dns-integration)。

VMのマネージドIDは、コンテナからもVMのID取得先`169.254.169.254`へ到達する必要があります。この取得先はプロキシを経由させません。VM上で成功しても、コンテナ内で成功したとは扱わないでください。[VMのIMDS仕様](https://learn.microsoft.com/en-us/azure/virtual-machines/instance-metadata-service?tabs=linux)。

## 6 予算とデータの運用条件を設定する

### 予算通知

1. サブスクリプションまたはリソースグループの「コストの管理」→「予算」を開きます。
2. OCR対象のリソースグループを選びます。ほかのサービスも同居する場合は、対象リソースをフィルターして範囲を確認します。
3. 月次の予算、開始日・終了日、通知先、通知閾値を設定します。
4. 「コスト分析」で、OCRの利用量を追跡できる表示を確認します。利用直後には料金の反映に時間がかかる場合があります。

**Azureの予算通知だけでは分析は自動停止しません。** アプリ側で新規分析を止める枚数・金額相当の上限は実装後に別途設定します。料金は作成リージョンと契約通貨を選んで[Document Intelligence料金表](https://azure.microsoft.com/en-us/pricing/details/document-intelligence/)で確認します。見積もりは原本件数だけでなく、実際の送信画像枚数、再分析、検証分を含めます。[予算の公式手順](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets)。

### 資料の送信と保持

このアプリの初期計画では、OCR対象のあるページを**ページ全体のPNG**として送ります。採用する文字が一部分でも、同じページ内のほかの領域もAzureへ送られます。利用可能な資料と送信地域を、この方式を前提に決めてください。

Microsoftの公開資料では、入力データと分析結果は処理完了後24時間保持され、その後自動削除されます。早期削除にはDelete Analyze Result APIが案内されています。これはPortalで保持時間を短く設定する機能とは別です。アプリへの削除API組み込みは未実装のため、ローカルで資料を消しただけでAzure側も即時削除されたとは扱いません。[データの保持と削除](https://learn.microsoft.com/en-us/azure/foundry/responsible-ai/document-intelligence/data-privacy-security)。

早期削除を運用する場合は、ローカルへの結果保存を確認してから、既知のモデル・操作IDに対する削除を実施します。APIの成功応答は204です。保持要件がある環境では、契約条件と削除確認方法も記録します。[Delete Analyze Result仕様](https://learn.microsoft.com/en-us/rest/api/aiservices/document-models/delete-analyze-result?view=rest-aiservices-v4.0+%282024-11-30%29)。

## 7 アプリ実装前の接続確認

### 合成画像を用意する

個人情報や実資料を含まないPNGを一枚作ります。白い背景に、例えば次の文字を十分な大きさで配置します。F0でも送れるよう、4 MB未満にしてください。

```text
OCR接続確認
Invoice TEST 001
合計 12345円
2026年10月3日
```

画像寸法、ファイルサイズ、期待する文字を記録します。PDFを直接送る確認と、アプリ計画のPNG送信の確認を混同しないよう、この段階ではPNGを使います。

### Portalから目視で確認する

1. [Document Intelligence Studio](https://documentintelligence.ai.azure.com/)を開きます。
2. 作成したDocument Intelligenceリソースへ接続します。Studioを操作する人の権限とVMの権限は別です。Entra認証でStudioを使う場合は、その利用者にも対象リソースでCognitive Services Userが必要です。
3. Read／読み取りの画面を選びます。Layoutや請求書モデルを選ばないよう、表示されるモデルを確認します。
4. API版を選択できる場合は`2024-11-30`を選び、合成PNGを一回分析します。Studioでこの版を確認できない場合、次のREST確認で版を固定します。
5. 日本語・英語・金額が読み取られ、JSONにページ、単語、位置情報が含まれることを確認します。

Studioの成功は、Studio利用者の接続確認です。本番VMやコンテナの認証確認は、次の手順で別に実施します。[Studioの利用と権限](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/quickstarts/get-started-studio?view=doc-intel-4.0.0)。

### 計画と同じAPIを確認する

技術担当者向けの独立した接続試験です。Python 3の標準機能を使い、アプリへは変更を加えません。**実行するとPNGをAzureへ送信し、S0では課金対象になり得ます。** スクリプトには分析POSTの自動再送を入れていません。

ローカルPCまたはVM上のターミナルで、次の非秘密の値を設定します。キー認証は`api_key`、VM内でのマネージドID認証は`managed_identity`です。

```sh
export OCR_TEST_ENDPOINT='https://<実際のリソース固有名>.cognitiveservices.azure.com/'
export OCR_TEST_AUTH='api_key'
export OCR_TEST_IMAGE='/absolute/path/ocr-test.png'
# ユーザー割り当てIDの場合だけ設定する
# export OCR_TEST_CLIENT_ID='<そのIDのクライアントID>'
```

続けて以下を実行します。キー認証では、実行中にキーを非表示で入力します。端末が対話入力できる環境を使ってください。VMでは`OCR_TEST_AUTH=managed_identity`に変更します。コンテナでも同じ値を明示して、同じ試験を実行します。マネージドID用のコードは**VM上のコンテナ専用**です。

```sh
python3 - <<'PY'
import getpass
import json
import os
import tempfile
import time
from pathlib import Path
from urllib import error, parse, request

class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

endpoint = os.environ['OCR_TEST_ENDPOINT'].rstrip('/')
base = parse.urlsplit(endpoint)
if (base.scheme != 'https' or base.username or base.password
        or base.port not in (None, 443) or base.path not in ('', '/')
        or base.query or base.fragment or not base.hostname
        or not base.hostname.endswith('.cognitiveservices.azure.com')):
    raise SystemExit('Portalのリソース固有エンドポイントを指定してください')
image = Path(os.environ['OCR_TEST_IMAGE']).read_bytes()
if not image.startswith(b'\x89PNG\r\n\x1a\n') or len(image) >= 4_000_000:
    raise SystemExit('この試験には4 MB未満のPNGを使ってください')
out = Path(tempfile.mkdtemp(prefix='azure-ocr-check-'))
print('検証記録の保存先:', out, flush=True)
http = request.build_opener(NoRedirect())
auth = os.environ['OCR_TEST_AUTH']
if auth == 'api_key':
    headers = {'Ocp-Apim-Subscription-Key': getpass.getpass('APIキー: ')}
elif auth == 'managed_identity':
    params = {'api-version': '2018-02-01',
              'resource': 'https://cognitiveservices.azure.com/'}
    if os.environ.get('OCR_TEST_CLIENT_ID'):
        params['client_id'] = os.environ['OCR_TEST_CLIENT_ID']
    imds = request.build_opener(request.ProxyHandler({}), NoRedirect())
    req = request.Request('http://169.254.169.254/metadata/identity/oauth2/token?'
                          + parse.urlencode(params), headers={'Metadata': 'true'})
    with imds.open(req, timeout=15) as res:
        token = json.load(res)['access_token']
    headers = {'Authorization': 'Bearer ' + token}
else:
    raise SystemExit('認証方式はapi_keyまたはmanaged_identityです')
path = '/documentintelligence/documentModels/prebuilt-read/analyzeResults/'
req = request.Request(endpoint + '/documentintelligence/documentModels/'
                      'prebuilt-read:analyze?api-version=2024-11-30',
                      data=image, headers={**headers, 'Content-Type': 'image/png'})
try:
    with http.open(req, timeout=15) as res:
        if res.status != 202:
            raise SystemExit('受付は202ではありません。再送せず状態を確認してください')
        operation = res.headers.get('Operation-Location', '')
except (error.URLError, TimeoutError) as exc:
    code = getattr(exc, 'code', '通信エラー')
    raise SystemExit(f'分析POST失敗: {code}。受付不明の場合は自動再送しないでください')
# 取得が止まっても、同じ操作を手動で再開できるようURLを先に保存する
(out / 'operation.txt').write_text(operation, encoding='utf-8')
op = parse.urlsplit(operation)
if (op.scheme != 'https' or op.hostname != base.hostname
        or op.port not in (None, 443) or op.username or op.password
        or not op.path.startswith(path) or op.fragment
        or parse.parse_qs(op.query).get('api-version') != ['2024-11-30']):
    raise SystemExit('操作URLが想定外です。資格情報を送らず停止しました')
deadline = time.monotonic() + 180
while time.monotonic() < deadline:
    req = request.Request(operation, headers=headers)
    with http.open(req, timeout=min(15, max(1, deadline - time.monotonic()))) as res:
        result = json.load(res)
        wait = res.headers.get('Retry-After', '5')
    state = result.get('status')
    print('状態:', state, flush=True)
    if state == 'succeeded':
        (out / 'result.json').write_text(json.dumps(result, ensure_ascii=False),
                                       encoding='utf-8')
        print('成功。result.jsonの単語・座標・期待文字を確認してください')
        break
    if state not in ('notStarted', 'running'):
        raise SystemExit('分析失敗または想定外の状態です。再POSTせず記録してください')
    if not wait.isdecimal():
        raise SystemExit('日時形式のRetry-Afterです。指定時刻以降に同じ操作を取得してください')
    delay = max(5, int(wait))
    if delay >= deadline - time.monotonic():
        raise SystemExit('待機期限です。分析POSTをやり直さず、保存した操作URLを確認してください')
    time.sleep(delay)
else:
    raise SystemExit('取得期限です。保存した操作URLから結果取得を再開してください')
PY
```

成功の判定は、`status=succeeded`、`analyzeResult.modelId=prebuilt-read`、`analyzeResult.apiVersion=2024-11-30`、PNG一枚に対応するページ情報、期待文字と単語の座標が存在することです。この試験は言語を固定しません。機能追加オプションや検索可能PDFも要求しません。[バイナリ分析REST仕様](https://learn.microsoft.com/en-us/rest/api/aiservices/document-models/analyze-document-from-stream?view=rest-aiservices-v4.0+%282024-11-30%29)、[VMのトークン取得仕様](https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/how-to-use-vm-token)。

GET中のHTTPエラーや期限超過では、この簡易試験は停止します。`operation.txt`があれば、そのURLを検査し、同じ資格情報でGETを再開してください。429では`Retry-After`に従います。**結果を取得するためにスクリプト全体を再実行すると、新しい分析POSTになります。** POSTの応答が失われて操作URLがない場合は受付不明として記録し、重複課金の可能性を理解したうえで別試行にします。

コンテナ内での試験には、対話端末と合成PNGの配置が必要です。上の独立試験で使う`OCR_TEST_*`はComposeから自動では渡しません。アプリ用のエンドポイント・APIキーは手順8に従って渡します。コンテナ内の端末で上記の値を設定してから実行してください。VMでの成功・コンテナでの成功をそれぞれ記録します。検証結果は一時ディレクトリへ保存されるため、コンテナを再作成する前に必要な記録を保護された保存先へ移します。

## 8 実装後のアプリ設定

OCRプラグインはDockerfileに組み込んでいます。ローカル環境ではREADMEのインストール手順を実行し、プロジェクト直下の`.env`へ以下の設定を記入します。`./run.sh`とPythonアプリ・processorの直接起動で自動的に読み込み、既に指定されている環境変数を優先します。変更後は再起動します。ナレッジAPIのDocker構成でもVM上のプロジェクト直下の`.env`をprocessorへ渡します。processor専用の`worker.env`に同じ項目があればそちらを優先します。

### ローカルの設定

```dotenv
DOCLING_AZURE_OCR_ENDPOINT=https://<実際のリソース固有名>.cognitiveservices.azure.com/
AZURE_DOCUMENT_INTELLIGENCE_API_KEY=<APIキー>
```

必要な設定はこの2項目です。APIキーが設定されていればキー認証を使います。OCR方式はアップロード画面で選び、既定はローカルOCRです。接続設定を記入しただけではAzureへ送信しません。

API版`2024-11-30`、モデル`prebuilt-read`、プロファイル`read-v1`、F0相当の画像制限、期限1,800秒、上限100送信は`packages/docling-azure-ocr/src/docling_azure_ocr/config.py`の定数です。これらを環境変数で指定する必要はありません。

### 本番VMの設定

システム割り当てマネージドIDを使うVMではエンドポイントだけを設定し、APIキーを設定しません。ユーザー割り当てIDを使う場合だけ`DOCLING_AZURE_OCR_CLIENT_ID=<そのIDのクライアントID>`を追加します。実装は`ManagedIdentityCredential`を明示し、Azure CLIの認証へ切り替えません。

アプリのEntraトークン取得scopeは`https://cognitiveservices.azure.com/.default`です。上の独立試験では、IMDSの`resource`引数を使うため末尾が`/`になっています。

デモ用ComposeはOCR設定を`environment`で受け渡します。ナレッジAPIのprocessorはプロジェクト直下の`.env`と`worker.env`から設定を受け取ります。設定同期で手動追記したOCR設定を消しません。キー方式では秘密値もその環境へ注入します。Composeの`.env`は値の展開元であり、未列挙の変数が自動でコンテナへ渡されるわけではありません。通常のVM上のDockerには、Container Appsと同じSecret参照の仕組みが自動で付くわけでもありません。キー方式を採るなら、秘密値を受け取る方法まで配置構成として完成させます。

ジョブの完了を確認してからコンテナを再作成し、秘密値を表示せず、endpoint・provider・認証方式・API版・モデルが反映されたことを確認します。`/health/ready`の成功だけではAzure OCRの成功と判定せず、合成PDFの抽出まで実施します。

### アプリ側の受け入れ確認

- エンドポイント未設定時、およびローカルOCR選択時は既存のローカルOCRが動き、外部送信されない。
- Azure選択後、画像だけの合成PDFから期待する文字が取れ、引用位置が原本と一致する。
- 同じ保存済み抽出の再索引で、新しい分析POSTが発生しない。
- Azureの失敗を画面で成功扱いせず、ローカルOCRへ無通知で切り替えない。
- 再起動後に既知の操作IDから取得を再開し、受付不明は自動再送しない。
- 設定した原本・画像・待機時間・送信上限を超えた場合、処理が止まる。

これらの実装条件は[Azure OCR対応計画書](../azure-ocr-implementation-plan.md)に従います。

## 9 設定記録と導入完了の確認

次の記録を実装担当者へ渡します。秘密値は記載せず、管理先の名前だけを記録してください。

| 記録項目 | 記入欄 |
|---|---|
| 設定担当者・設定日 | |
| テナント・サブスクリプションID | |
| リソースグループ・Document IntelligenceリソースID | |
| リージョン・F0またはS0 | |
| エンドポイント | |
| 認証方式・VM名・IDのオブジェクトID | |
| ユーザー割り当てIDのクライアントID | 該当する場合のみ |
| ロール名と割り当て範囲 | |
| 許可IPまたはVNet・サブネット・プライベートDNS | |
| 月額予算・通知先・停止上限の担当者 | |
| APIキーの管理先 | キー方式の場合のみ。キー自体は書かない |
| 入力・結果の保持と早期削除方針 | |
| 合成画像・検証日時・モデル・API版・操作ID | |
| ローカル／VM／コンテナの検証結果 | 各々、成功・失敗・未実施を記録 |
| アプリのプロファイルID・実装版 | 実装後に記入 |

導入完了は次のように分けて判定します。

- **Azureの準備完了**：リソース、認証権限、ネットワーク、予算が設定され、予定した実行環境からReadの分析と結果取得が成功した。
- **アプリの導入完了**：OCR実装が完成し、手順8の合成PDFによる受け入れ確認が通った。

最初は新規の少量資料から始めます。既存の完了資料を一括でAzureへ再送しません。

## 接続できないときの確認

| 症状 | 確認する点と対応 |
|---|---|
| 401 | キーとリソースの組み合わせ、トークン対象・期限、認証方式を確認。キー方式ならローカル認証が無効でないか確認 |
| 403 | VMのIDへのデータアクセスロールとリソース範囲、送信元IP、パブリックアクセス・Private Endpoint設定を確認。割り当て直後なら反映を待つ |
| VMでは成功し、コンテナでは失敗 | コンテナ内のDNS、443への経路、IMDSへの到達、プロキシ除外、ユーザー割り当てIDの選択を確認 |
| 名前解決失敗・接続タイムアウト | エンドポイント、プライベートDNSのVNetリンク・独自DNS転送、NSG・Firewall・プロキシを確認 |
| 400・413など | PNG形式、寸法、F0/S0の容量、モデルとAPI版を確認 |
| 429 | 同じリソースを使うほかの処理も確認し、`Retry-After`に従って待つ。F0のGET制限にも注意 |
| 404・結果取得不能 | 操作URLとAPI版、24時間の保持期限、早期削除の有無を確認。再分析の前に保存済みの成功結果を確認 |
| POST後に応答がない | 受付不明として記録。無条件の再POSTは行わない |
| 抽出できたが数字・位置が違う | 接続成功と品質合格を分け、画像の解像度と原本の座標変換を実装担当者が確認 |
| `.env`を変えても反映されない | OCRが実装済みか、Composeの環境変数受け渡しと再作成を確認 |

エラーの記録には日時、HTTP状態、Azure要求ID、操作ID、認証方式を残します。キー、トークン、実資料の本文は通常ログへ残しません。

## 一時停止と資格情報の更新

導入後にAzure OCRを止める場合は、新規分析を停止して既知の操作IDと完了結果を保存します。新規処理をローカルOCRへ戻し、Azureを無効にする場合はエンドポイントを空欄にしてコンテナを再作成します。稼働中ジョブを環境変数変更だけで中断できるとは考えず、受付済み処理の状態を確認します。

キー方式を使っている場合は、利用中でないキーを更新し、秘密情報管理先と注入設定を切り替え、接続確認後に旧キーを更新します。キー更新中に結果取得が必要な処理が残らないよう、先に新規分析を止めて取得を終えます。マネージドID方式はAPIキー更新作業を必要としません。

検証用リソースを削除する場合は、取得待ちがないこと、必要な記録とローカル結果が保存されたことを確認します。共通リソースを含むリソースグループ全体を、検証終了の後始末として削除しないでください。
