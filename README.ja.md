[English](README.md)

# usb_sentinel

USBドライブを挿すだけで検査が自動実行される、Linux向けの検査ツールである。

従来はWindows PCでH2testw / CrystalDiskMark / BitDefenderなどを手作業で実行しており、1検体あたり数時間かかっていた。本ツールではネットワーク非接続のRaspberry Pi 4（Ubuntu）上でコマンドベースの自動検査を行い、4ポートで同時に検査する。検査員はUSBを挿すだけでよい。

| 検査項目 | 使用するツール |
|---|---|
| マルウェア | ClamAV |
| 容量 | f3write / f3read / f3probe |
| 速度 | fio |
| OS | Ubuntu Linux |

## 検査シーケンス

```
Step 0  USB挿入検知（ホストudev → systemd → DockerのPythonバックエンド）
Step 1  ★ マルウェアスキャン（ClamAV）← 最初に実施して安全を確保する
          → 検知してもStep 2の台帳は作る。Step 4以降はSKIPPED。
Step 2  ハッシュ台帳の作成（SHA-256 → hash-inventory.tsv）
Step 3  アンマウント（以降の破壊的検査の前提）
Step 4  容量検証（f3write + f3read / f3probe）
Step 5  速度検証（fio）
Step 6  結果出力（result.json / result.txt / result-qr.png）、SQLiteへ1行記録
Step 7  判定分類（No Defect / Counterfeit Suspected / Spec Mismatch / Defective / Malware Detected / Inconclusive）
```

### 安全設計

- ClamAVを最初に走らせる。ClamAVがスキャンを実行した後は、結果（検知・エラー・タイムアウト）にかかわらずハッシュ台帳を作成する（照合用の証跡）。ClamAVがファイルを開いていない状態（clamscan不在、定義DBなし）では台帳も作らない。破壊的検査（f3 / fio）へは絶対に進まない。タイムアウトやスキャン失敗（終了コード2）の後に作る台帳は、ClamAVが開いていないファイルも`ro,noexec,nosuid,nodev`の下でバイト列として読むだけで、実行も解釈もしない。
- read-onlyマウント。スキャンとハッシュ計算のためのマウントは`ro,noexec,nosuid,nodev`で行い、感染ファイルがホスト側で実行される余地を残さない。
- 破壊的検査は多重にブロックする。マルウェアがFAILまたはERROR、`allow_destructive = false`、マウントが残っている、のいずれかであれば書き込みを行わない。
- 「検知なし」を「クリーン」と混同しない。clamscanの終了コードが0/1以外、タイムアウト、読み取り不能ファイルの混在、ファイルシステム不在は全てERRORとして扱う。未実装ステップが残る限り`No Defect`とは判定しない。
- 安全ガード。USBバス経由・リムーバブル・ルートディスクでない・容量が非ゼロ、を満たさないデバイスは検査開始前に拒否する（終了コード2）。

## 構成

```
backend/
  usb_sentinel/
    __main__.py            python -m usb_sentinelのエントリポイント
    libs/
      __init__.py          公開API
      cli.py               inspect / selftest、終了コード定義
      config.py            /etc/usb-sentinel/config.tomlの読み込み
      models.py            デバイス情報・ステップ結果・判定
      runner.py            外部コマンド実行とFakeRunner
      device.py            デバイス識別と安全ガード
      mounter.py           read-onlyマウント
      hashing.py           SHA-256ハッシュ台帳
      orchestrator.py      検査シーケンスと判定分類
      speed_floor.py       デバイス種別ごとの業界最低速度（steps/の外。config.pyが参照）
      steps/
        malware.py         ClamAVスキャン
        capacity.py        容量検証（f3write + f3read / f3probe）の入口と判定の書き込み
        capacity_write.py  容量検証のf3writeモード（rwマウントしてf3write→f3read）
        capacity_fs.py     容量検証用のrwマウントとf3パーサ
        capacity_probe.py  f3probeモード
        capacity_judge.py  容量の判定（±7%帯、偽装疑い、申告容量の非破壊チェック）
        speed.py           速度検証（fio）の入口と判定
        speed_fio.py       fioのargv組み立てとJSON出力のパーサ
        speed_judge.py     速度の参照値・重大度・警告文
        common.py          両ステップの共通部品（残り時間、権限判定、lsblk問い合わせ、コマンド実行と失敗文言）
      report/
        json_report.py     result.jsonとSHA-256ダイジェスト
        text.py            result.txt / コンソール表示
        qr.py              result-qr.png（v:3）
      store/
        schema.py          SQLiteのスキーマとマイグレーション
        sqlite_row.py      result.jsonと同じ値からinspectionsの1行を組む
        sqlite_store.py    検査1件につき1行の記録（開始・完了）
  tests/                   外部コマンドをモックしたテスト
  pyproject.toml           Python依存・開発ツールの設定
  uv.lock                  Python依存のロックファイル
  .python-version          Pythonバージョン
  Dockerfile               Python・ClamAV・f3・fioの実行環境
  .dockerignore            Dockerビルドの除外設定
  README.md                バックエンドの開発手順
frontend/
  usb_sentinel_display/
    __main__.py            python -m usb_sentinel_displayのエントリポイント
    libs/
      __init__.py          公開API
      cli.py               run / calibrate、設定の上書き
      config.py            ~/.config/usb-sentinel/display.tomlの読み込みと検証
      models.py            スロット状態・接続ディスク・結果サマリ、QR用の対応表
      sysfs.py             /sys/blockの列挙、ポートパス・シリアル・接続時刻の抽出
      result_file.py       結果ディレクトリ名の解析、時刻変換、result.jsonの要約
      results.py           結果ディレクトリの走査とSQLite索引の突き合わせ（ResultStore）
      result_detail.py     result.jsonのsteps[*].detailsから詳細QRの材料（ResultDetail）を組む
      store.py             inspections.sqliteの読み取り専用索引（SqliteIndex）
      units.py             systemctl showの呼び出しと出力解析、Runner
      mapper.py            ディスク・結果・ユニット状態から4スロットを組む
      monitor.py           1秒周期の更新本体と例外規則
      qr_payload.py        画面用QRペイロード（通常QR v:2、詳細QR v:3の3枚）
      qr_render.py         QR行列からPPM画像を作る
      ui/
        theme.py           判定色・寸法・フォント
        i18n.py            ja／enの文言と言語の記録
        strip.py           情報ストリップの文言
        tiles.py           タイル4つと◀／言語／▶の描画
        qr_view.py         QRキャンバス（通常QR／詳細QR）と情報ストリップ
        keys.py            キーバインド（1〜4、矢印、F5、L、Q、Ctrl+Q）
        app.py             tkルート、全画面、解像度チェック、SIGTERM
  tests/                   偽sysfsツリーとFakeRunnerで外部依存を差し替えたテスト
  scripts/
    make-fixtures.py       開発用の偽sysfsツリーとサンプル結果
    dev-screenshot.py      開発機でのスクリーンショット撮影
  deploy/
    usb-sentinel-display.desktop  XDG autostart用
    display.example.toml   表示設定の全キー
  pyproject.toml           Python依存・開発ツールの設定
  uv.lock                  Python依存のロックファイル
  .python-version          Pythonバージョン
  README.md                表示フロントエンドの導入手順と開発手順
  AGENTS.md                表示フロントエンドの規約
doc/
  qr-payload.md            QRペイロード（ファイル用v:3、通常QR v:2、詳細QR v:3）とSQLiteの仕様
deploy/
  99-usb-sentinel-docker.rules  Docker用udevルール（ホスト側）
  usb-sentinel-docker@.service Docker起動用systemdユニット（ホスト側）
  config.example.toml      設定の全キー
scripts/
  install-all.sh           Pi 1台への一括導入（Docker、uv、表示アプリ、自動起動、ClamAV DB）
  install-docker.sh        Docker構成の導入（install-all.shから呼ばれる。単独でも使える）
  run-docker.sh            USBごとに検査コンテナを起動
README.md                  プロジェクト全体の説明と導入手順
LICENSE                    プロジェクト全体のライセンス
```

外部コマンド（clamscan / f3 / fio / mount / lsblk / udevadm / findmnt）は全て`CommandRunner`の背後に隠してあるため、Linux専用の挙動をmacOSの開発機でテストできる。

### なぜudevから直接スクリプトを実行しないのか

udevはイベントハンドラの子プロセスを既定180秒でkillする。f3write / f3readは容量次第で数十分〜数時間かかるため、必ず途中で殺される。そこでudevルールは`ENV{SYSTEMD_WANTS}`で`usb-sentinel@%k.service`の起動を要求するだけにし、`TimeoutStartSec=0`のsystemdテンプレートユニットに実行を委ねている。テンプレートなので`usb-sentinel@sda`〜`@sdd`が独立ユニットとして並行実行され、ポートごとに`journalctl -u usb-sentinel@sda`でログを分離できる。

## Dockerセットアップ（Raspberry Pi 4 / Ubuntu 26系）

実行対象は64bit（arm64）のUbuntu Server、systemd、rootful Docker Engine。
macOSはPythonの開発・テスト用で、USB自動検査はPi上で動かす。
イメージ内のDebianと、ホストのUbuntuは別々の環境。
Pi上でビルドすればarm64用のイメージになる。

```text
USB接続
  → Ubuntuホストのudev
  → usb-sentinel-docker@sda.service
  → docker run（USBごとに独立した検査コンテナ）
  → python -m usb_sentinel inspect /dev/sda
  → ホストの/var/lib/usb-sentinel/resultsに結果を保存
  → ホストの/var/lib/usb-sentinel/db/inspections.sqliteに1行記録
```

Pythonはsystemdに登録したバックエンドとして、接続イベントごとに起動する。
HTTP APIや常駐Webサーバーは使用しない。USB 4本を挿した場合は、
ディスクごとに独立したsystemdユニットとコンテナで実行する。

#### SQLiteの記録

`run-docker.sh`はホストの`/var/lib/usb-sentinel/db`をコンテナの同じパスにbind mountし、
backendは検査1件につき`inspections.sqlite`へ1行を書く（開始時に`state = 'started'`、
`result.json`を書いた後に`state = 'completed'`へ更新）。`install-docker.sh`がこのディレクトリを
`root:usb-sentinel`の`2770`（setgid）で作るので、DB本体とWALの`-wal`・`-shm`は
`usb-sentinel`グループで読める。`result.json`が正本で、DBは一覧と検索のための索引である。
記録に失敗しても検査は続き、終了コードも変わらない（開始時の失敗は`result.json`の`warnings`、
完了時の失敗はjournalのstderrに`WARNING usb_sentinel.libs.cli: SQLite record failed (completion): ...`として残る）。
標準エラー出力の診断メッセージは、`ERROR usb_sentinel.libs.cli: Inspection refused: ...`のように重大度とロガー名で始まる。標準出力にはコマンドの出力（`selftest`の各行と検査結果の本文）だけが出る。
列の意味は[doc/qr-payload.ja.md](doc/qr-payload.ja.md)の第8節にある。

DBが壊れたときは、検査が走っていないことを確認してから`inspections.sqlite`と`-wal`・`-shm`を
別名に退避する。次の検査で空のDBが作り直される。退避前の結果は`result.json`が全て残っているので
失われない。`result.json`からDBを作り直す`reindex`サブコマンドは第3期で扱う。

### 0. 一括導入（推奨）

ネットワーク接続中のPi上で、リポジトリをcloneしたあと1回だけ実行する。
Docker、uv、表示アプリ、自動ログイン、自動起動、ClamAV定義DBの取得までをまとめて行い、
最後に残る手作業と「LANを外す前のチェックリスト」を表示する。再実行しても既存の設定は保持する。

```bash
sudo bash scripts/install-all.sh
# 表示ユーザーをsudoの実行者以外にする場合
sudo DISPLAY_USER=kiosk bash scripts/install-all.sh
```

uvの公式インストーラは取得後にSHA-256を表示して実行前に確認を求める（`INSTALL_YES=1`で省略できる）。
タッチパネルのドライバ導入、画面解像度480x320の設定、ポートのキャリブレーション、
容量・速度検査の有効化（検体を消去する）は自動化せず、終了時に手順として案内する。
手順1〜3は`install-all.sh`が内部で行う内容の説明で、個別にやり直すときに使う。

### 1. Dockerとサービスの導入

ネットワーク接続中にPi上で実行する。以下はUbuntuのパッケージを使う例。
Docker Engine導入済みの場合、aptの手順は不要。

```bash
sudo apt-get update
sudo apt-get install -y docker.io
sudo systemctl enable --now docker
sudo docker info

# プロジェクトのルートで実行（イメージのビルドとudev/systemd登録）
sudo bash scripts/install-docker.sh
```

設定は`/etc/usb-sentinel/config.toml`、イメージなどの起動設定は
`/etc/usb-sentinel/docker.env`に置く。再導入時は既存設定を保持する。
イメージを変更する場合は`docker.env`の`USB_SENTINEL_IMAGE`も合わせて更新する。
従来の直接Python実行用udevルールは導入時に削除するので、二重起動しない。
導入前から接続していたUSBは、導入後に挿し直す。

### 2. ClamAV定義DBの準備

DBはホストに保存し、検査コンテナへ読み取り専用で渡す。
ネットワーク接続中に以下で取得・更新してから隔離環境へ持ち込む。
USB検査中は更新しない。

```bash
sudo docker run --rm --entrypoint freshclam \
  --mount type=bind,src=/var/lib/clamav,dst=/var/lib/clamav \
  usb-sentinel:local --user=root --stdout

sudo docker run --rm --network none \
  --mount type=bind,src=/var/lib/clamav,dst=/var/lib/clamav,readonly \
  usb-sentinel:local selftest
```

DB未取得や依存コマンド不足の場合、`selftest`は終了コード3を返す。
隔離環境ではイメージとDBが準備済みなら起動時のネットワーク接続は不要。

### 3. USBを挿して実行確認

```bash
sudo journalctl -f -u 'usb-sentinel-docker@*'
sudo docker ps
sudo ls /var/lib/usb-sentinel/results/

# 特定USBのログ
sudo journalctl -u usb-sentinel-docker@sda.service

# 自動起動と同じ経路で手動実行（未マウントの検体に限定）
sudo systemctl start usb-sentinel-docker@sda.service
```

ホストでマウント済みのディスクは拒否する。GUIの自動マウントを無効にした
専用のUbuntu Server検査機で運用する。システムディスクの自動アンマウントは行わない。
検査中の抜去時はsystemdがコンテナも停止する。再接続すれば新しい検査を開始する。

コンテナは`--network none`、ルートファイルシステム読み取り専用、
`--cap-drop ALL`を基本とし、対象ディスクとパーティションだけを
`--device ...:r`（既定）または`:rw`で渡す。`/sys`と`/run/udev`は読み取り専用で渡し、
USB判定に利用する。結果ディレクトリだけをホストへ書き込む。
mountのため`SYS_ADMIN`、ファイル読み取りのため`DAC_OVERRIDE`を付け、
コンテナのAppArmor制限を解除する。マウントはコンテナの名前空間内で行う。
`--privileged`やDockerソケットのコンテナ共有は使わない。

破壊的検査（容量・速度）を有効にするには、`/etc/usb-sentinel/docker.env`で
`USB_SENTINEL_DEVICE_ACCESS=rw`、`config.toml`で`allow_destructive = true`の両方を設定する。
どちらか一方だけではデバイスは読み取り専用のまま渡される。`allow_destructive = false`なら
両ステップは`SKIPPED`、`docker.env`だけ`ro`のままなら`ERROR`（権限不足）になり、
いずれも総合判定は`Inconclusive`になる。`rw`にすると検体の内容は消去される。

`scripts/install-docker.sh`は再導入時に既存の`docker.env`を保持するため、以前の導入で
作られた`docker.env`に`USB_SENTINEL_DEVICE_ACCESS`は含まれない（`run-docker.sh`は
未定義を`ro`と扱う）。有効にするには手で追記する。

```bash
# docker.envに書き込み許可を追記（既存環境）
echo 'USB_SENTINEL_DEVICE_ACCESS=rw' | sudo tee -a /etc/usb-sentinel/docker.env

# config.tomlのallow_destructiveをtrueにする
sudo sed -i 's/^allow_destructive = false/allow_destructive = true/' /etc/usb-sentinel/config.toml

# 反映確認（次にUSBを挿したときから有効）
grep USB_SENTINEL_DEVICE_ACCESS /etc/usb-sentinel/docker.env
grep allow_destructive /etc/usb-sentinel/config.toml
```

元に戻すには`docker.env`の値を`ro`にするか、`allow_destructive = false`にする。
どちらか一方で読み取り専用に戻る。

### 更新・停止

```bash
# ソース更新後、検査していないタイミングで再ビルド
sudo docker build -t usb-sentinel:local backend

# 自動起動を解除
sudo rm /etc/udev/rules.d/99-usb-sentinel-docker.rules
sudo udevadm control --reload

# 実行中の検査を停止
sudo systemctl stop 'usb-sentinel-docker@*.service'
```

## 表示フロントエンド（3.5インチタッチディスプレイ）

Piに接続した480x320のタッチディスプレイに、4ポートの検査状態と選択中ポートのQRコードを表示する。
ホスト上で動くtkinterアプリ（`frontend/`）で、backendの出力を読むだけで検査処理には関与しない。
読むのは`result.json`、`inspections.sqlite`（読み取り専用）、`/sys/block`、`systemctl show`だけで、HTTP APIや常駐サーバーは無い。

画面用のQRは`result-qr.png`とは別の短縮形式（`v:2`、上限150バイト、誤り訂正M）で、
`result.json`のSHA-256先頭16桁を載せる。持ち出し側のPCで
`sha256sum result.json | cut -c1-16`と突き合わせる。`result-qr.png`（`v:3`）はそのまま残る。

通常QRに加えて、容量・速度の数値まで入れた詳細QR（`v:3`、3枚固定、各150バイト以下）を
ページ送りで表示する（お薬手帳のQRと同じ方式）。3枚は共通の`d`（ダイジェスト先頭12桁）で結合し、
読み取り順は問わない。ファイル用QR、通常QR、詳細QRの3形式の仕様と、読み取りアプリ側の
スキーマ推奨は[doc/qr-payload.ja.md](doc/qr-payload.ja.md)にまとめてある。

画面の文言は英語と日本語を切り替えられる（既定は英語）。左下中央のボタンかキー`L`で切り替え、
選んだ言語は`$XDG_STATE_HOME/usb-sentinel-display/language`（無ければ`~/.local/state/…`）に
記録されて次回以降も優先される。通常QRと詳細QRの切替はQR領域のタップかキー`Q`、詳細QR表示中は
◀▶がページ送りになる。判定`Counterfeit Suspected`（偽装疑い、コード`C`、`#AD1457`）はbackend第2期と
同時に配備する。

backendが書くSQLite索引（`/var/lib/usb-sentinel/db/inspections.sqlite`）は`mode=ro`で読み取り専用に開き、
候補の一覧と「検査中」の判断にだけ使う。正本は`result.json`で、SHA-256が索引の`result_digest`と
合わなければ「結果破損」にする。索引が無い・開けない・スキーマ版が新しいときは従来の結果ディレクトリの
走査にフォールバックし、情報ストリップに理由を出す。`sqlite_path = ""`で索引を使わない構成にもできる。

導入手順、ポートのキャリブレーション、設定、操作、トラブルシュートは
[frontend/README.ja.md](frontend/README.ja.md)を参照。有効化に必要なホスト設定の要点は次のとおり。

- 画面解像度を480x320に固定する（GNOMEの設定または`~/.config/monitors.xml`）。
- GNOMEの自動マウントを無効にする。Desktop版では必須で、有効のままだと`run-docker.sh`が
  host-mounted diskとして検査を拒否する。
- スリープとスクリーンセーバーを止める（`gsettings`）。
- 表示ユーザーを`usb-sentinel`グループに追加して再ログインする。`install-docker.sh`が
  結果ルートを`root:usb-sentinel`の`0750`で作るため、グループに入れば読める。SQLite索引の
  ディレクトリ（`2770`）も同じグループで読める。
- `fonts-noto-cjk`を導入する。
- `frontend/deploy/usb-sentinel-display.desktop`をXDG autostart（`~/.config/autostart/`）に登録する。

## 結果の持ち出し（air gap越し）

`result-qr.png`にコンパクトサマリ（`v:3`）を入れてある。内容は判定・シリアル・開始／終了時刻・所要時間・各ステップの状態・ClamAV DBバージョンと日付・`result.json`のSHA-256ダイジェスト・マルウェア検知数とシグネチャ・申告容量の超過（`advertised_over`）・容量の数値（公称／申告／実測、区別、消失比率、偽装理由）・USBリンク速度・デバイス種別・速度の数値（実測／閾値、根拠、重大度）・判定理由（コードと文）・ハッシュ台帳のダイジェストと行数である。キーの一覧は[doc/qr-payload.ja.md](doc/qr-payload.ja.md)にある。

QRはバイトモードで最大2953バイトしか入らないため全文は入らない。超えるときは、シグネチャを3件に切る → 理由文を各120文字に切る → シグネチャを0件まで減らす → 理由文を空にする（`reason_codes`は残す）→ シリアルを128文字に切る → `capacity.probe_verdict_line`を落とす、の順で縮め、どこかで削ったら`truncated: true`を立てる。典型的な検査では1.2KB前後で、削られるのはシグネチャが大量のときだけである。

ネットワーク接続PCでは、`sha256sum result.json`とQRの`digest`、`sha256sum hash-inventory.tsv`とQRの`hash_inventory_digest`を突き合わせる。前者で結果ファイルの、後者で台帳ファイルの改変を検出できる。

## 終了コード

| コード | 意味 |
|---|---|
| 0 | `No Defect` |
| 1 | 判定がcleanでない（`Malware Detected` / `Counterfeit Suspected` / `Spec Mismatch` / `Defective` / `Inconclusive`） |
| 2 | 危険なデバイス（検査を拒否した） |
| 3 | 実行エラー（依存コマンド不在など） |

## 判定分類

| 判定 | 条件 |
|---|---|
| `Malware Detected` | マルウェア検知（他の失敗より優先） |
| `Counterfeit Suspected` | 容量偽装の疑い。(1)申告容量または実容量が公称容量（`nominal_bytes`）の107%超、(2)f3probeが`Bad news`を報告、(3)f3readで消失セクタが50%以上、のいずれか。「疑い」であり断定ではない |
| `Defective` | 容量は申告どおりだが、f3readが消失・破損・変化・上書きされたセクタを検知した |
| `Spec Mismatch` | いずれかのステップがFAIL（容量不足、速度不足） |
| `Inconclusive` | ERROR、またはSKIPPED / 未実装ステップが残る |
| `No Defect` | 全ステップPass |

優先順位は`Malware Detected` > `Counterfeit Suspected` > `Defective` > `Spec Mismatch` > `No Defect`で、ERRORやSKIPPEDが残れば`Inconclusive`になる。判定の根拠は`result.json`の`reasons`（文）と`reason_codes`（`m:found`、`c:over`、`c:probe`、`c:lost`、`c:short`、`c:defect`、`s:severe`、`s:minor`、`<ステップ略号>:timeout`／`error`／`skipped`）に残る。

`Counterfeit Suspected`は、f3probeが不良ブロックの多い正規品にも`Bad news`を出し得ること、GB／GiBの混同で107%を超え得ることから、エスカレーションの合図として扱う。公称・申告・実測の各値とその比率が`result.json`、QR、SQLiteに残るので、人が最終判断する。

#### 容量

容量不足は常に`Spec Mismatch`で、`Defective`は容量が閾値以上のときだけ使う。f3probeモード（`use_probe = true`）では非OKセクタの情報が得られないため`Defective`にはならない。

`[capacity] nominal_bytes`に商品ページの公称容量をバイトで設定すると、判定の分母が申告容量（lsblkの値）から公称容量に変わり、93%未満を容量不足、107%超（`over_ratio = 1.07`）を偽装疑いとする±7%の帯で判定する。未設定なら従来どおり申告容量基準の93%判定だけを行い、超過は判定しない。

- `nominal_bytes`は10進表記をそのままバイトに直した値にする（32GB → `32000000000`）。GiB表記（32GiB = `34359738368`）はGBの1.0737倍なので、混同すると正規品が偽装疑いになる。GiB申告の正規品が多ければ`over_ratio = 1.10`に緩める。
- 申告容量の非破壊チェック。`nominal_bytes`が設定されていれば、マウントもスキャンもしていない検査開始直後にlsblkの申告容量だけを公称容量と比べ、`result.json`の`advertised_check`に記録する。`allow_destructive = false`でも、マルウェア検知で止まっても記録され、超過なら`Counterfeit Suspected`になる。書き込みは一切せず、破壊的検査に進む条件（ゲート）には影響しない。
- 容量ステップが走ったときは、実測容量も同じ帯で判定する。f3readの消失セクタが50%以上（`lost_ratio`）なら偽装疑いとする。正規品の不良は数%以下にとどまるためである。
- `config.toml`はホストで1つなので、4ポートで異なる商品を同時に検査する場合は全員に同じ公称値が当たる。ケースごとの公称値は読み取りアプリ側で管理する。

#### 速度

速度は`[speed]`の`nominal_read_mb_s`・`nominal_write_mb_s`（MB/s）と比較する。公称値が未設定のとき、デバイス種別（`[speed] device_class`）の業界最低値で判定する。公称値が設定されていればそれが優先される。片方だけ設定した場合は設定側だけを判定し、未設定側は測定値の記録のみ（summaryに`(not judged)`が付く）。

| `device_class` | 種別 | 読み取り（MB/s） | 書き込み（MB/s） |
|---|---|---|---|
| `hdd_5400` | HDD 5400rpm | 80 | 80 |
| `hdd_7200` | HDD 7200rpm | 120 | 120 |
| `sata_ssd` | SATA SSD | 400 | 400 |
| `nvme_ssd` | NVMe / USB3.2 SSD | 500 | 500 |
| `sd_c10` | SD Class10 / U1 | 25 | 10 |
| `sd_u3` | SD U3 / V30 | 90 | 30 |
| `usb2_flash` | USB2.0フラッシュ | 15 | 5 |
| `usb3_flash` | USB3.0フラッシュ | 50 | 15 |

- `device_class`が未設定なら、sysfsのUSBリンク速度から`usb2_flash`（480Mbps）または`usb3_flash`（5000Mbps以上）を推定する。推定したときは`warnings`に`Device class not set; judged with USB flash defaults ...`を必ず残す。HDD・SSD・SDはリンク速度から判別できないので、扱う運用では`device_class`を設定する。
- `nvme_ssd`はホストがUSB3.2 Gen2（10Gbps）以上のときに使う。Raspberry Pi 4のUSB3ポートはGen1（5Gbps）で実効約400〜450MB/sが上限のため、500MB/sの最低値では判定できない。この場合は`nominal_*`を設定する。基準値がリンク速度の実効上限に近いときは`warnings`に`Reference ... exceeds the effective upper limit of a ...Mbps link ...`の警告が出る。
- USB1.1リンク（12Mbps・1.5Mbps）の検体は判定基準を持たない。`device_class`も公称値も無ければ速度ステップは`SKIPPED`になり、`warnings`に`USB1.1 link (...Mbps) has no verdict criterion; ...`を残す。
- 両方未設定でリンク速度も読めなければ速度ステップは`SKIPPED`になり総合判定は`Inconclusive`。
- 速度不足の重大度。測定値が基準値の50%未満なら`severe`（summaryは`Speed shortfall (severe)`で始まる）、50〜79%なら`minor`（`Speed shortfall (minor)`）、80%以上（`pass_ratio`）でPASS。`details.severity`と`reason_codes`の`s:severe`／`s:minor`に出る。

### 速度の公称値

`/etc/usb-sentinel/config.toml`の`[speed]`に、メーカー公表値をMB/s（10^6バイト/秒）で設定する。

```toml
[speed]
nominal_read_mb_s = 90.0    # 読み取りの公称速度。未設定なら判定対象外
nominal_write_mb_s = 20.0   # 書き込みの公称速度。未設定なら判定対象外
pass_ratio = 0.80           # 公称値×pass_ratio以上でPASS
# device_class = "usb3_flash"  # 公称値が無いときに使う種別。上の表のキーから選ぶ
```

測定値は`result.json`の`steps.speed.details.read.mb_s`・`write.mb_s`に、閾値は`threshold_read_mb_s`・`threshold_write_mb_s`に記録される。判定の根拠（`basis_read`・`basis_write`: `nominal`／`class`／`none`）、決定した種別（`device_class`）とその由来（`device_class_basis`: `config`／`link_speed`／`unknown`）、重大度（`severity`）も同じ`details`に入る。

### `result.txt`

`result.txt`は`result.json`を人が読む形にしたもので、先頭に`Verdict:`、`Device:`、`Advertised capacity:`（lsblkの申告容量）の3行を出す。続いて`Link speed:`、`Elapsed:`、`Advertised capacity exceeded:`、`Warning:`の行を、各ステップの行の後に`Nominal (product):`、`Kind:`、`Severity:`を出す。第2期で足した項目は値が無ければ行ごと省く（ERRORで終わったステップには無い）ので、行の有無で欠損を判断できる。

### f3writeモードの制限

既定（`use_probe = false`）はf3write + f3readで空き領域を検証し、既使用量（`statvfs`）を加算して実容量を見積もる。次の検体は`use_probe = true`（f3probe）で検査する。

- (a) ほぼ満杯の検体。検証済み領域が閾値に届かないため`ERROR`になる（既使用量だけでは合格させない）。
- (b) NTFSの検体。Docker構成ではrwマウントできない（`ntfs-3g`はFUSEを使うが`run-docker.sh`は`/dev/fuse`を渡していない）。
- (c) 容量偽装品は多くの場合`Spec Mismatch`になるが、巻き戻し書き込みでFAT領域が壊れるとf3readの出力を解析できず`ERROR`（`Inconclusive`）になる。既使用量を過大に申告する検体も同様に`Inconclusive`になる。summaryが`Could not parse f3read output`または`Verified area ... is below threshold ...`で始まる検体は`use_probe = true`で再検査する。

f3probeはファイルシステムを介さずデバイスに直接書くため、上記の影響を受けない。いずれのモードでも検体の内容は消去される。

## 開発

```bash
cd backend
uv sync --frozen
uv run pytest -q
uv run ruff check . && uv run ty check
uv run python -m usb_sentinel selftest   # 依存コマンドがMISSと報告されるのが正常
```

## 未確定事項

容量（f3）と速度（fio）のステップは実装済みだが、次が実機キャリブレーション待ちである。

1. fioのパラメータ（`block_size` / `queue_depth` / `duration_s`）— 既定の1M / 8 / 30秒は暫定値。CrystalDiskMarkと数値を突き合わせるキャリブレーションが必要
2. 公称速度の入手元と設定運用— `nominal_read_mb_s` / `nominal_write_mb_s`を検体ごとに誰がどう設定するか
3. 既使用領域を実容量に加算する扱いの妥当性— f3writeモードでは検体の申告値（`statvfs`）を足している
4. ClamAV定義DBのオフライン更新手順— 隔離環境への持ち込み方法と頻度
5. Raspberry Pi 4での実機確認— f3read / f3probe / fioの実出力とパーサの一致、Docker `rw`でのコンテナ内書き込み、`mount -w`の挙動（詳細は`.agents/tasks/f3-fio-steps/final-report.md`の実機確認項目）

第2期（ハッシュ台帳の分離、±7%帯、リンク速度、SQLite、QR v:3）で加わった実機確認項目は次のとおり。

1. `/sys/bus/usb/devices/<port_path>/speed`がコンテナ内から読めるか（`/sys`は読み取り専用bind mount）。読めなければ`link_speed_mbps`は`null`になり、`device_class`未設定の検体は速度が`SKIPPED`になる。
2. bind mount上のSQLite WAL。4コンテナ同時書き込みで`database is locked`が出ないこと（`busy_timeout`で吸収されること）、Linux実機で`ls -l`により`-wal`・`-shm`が本体と同じモード（`usb-sentinel`グループ・`0660`）で作られること、表示ユーザーが`mode=ro`で読めること。
3. clamscanの集計行の実出力（キー名、`Time:`の形式、`Data scanned:`の単位）。
4. f3probeの`Bad news`行の実際の文言（limbo／wraparound／chainの3型）。
5. USBリンク速度の値。USB3.2 Gen2ポートに挿したUSB2.0デバイスで`480`が返ること、USB3.0デバイスで`5000`になること。Raspberry Pi 4のUSB3ポートはGen1（5000）まで。
6. 容量超過の検証。実容量が公称を超える正規品（GiB申告）があるか。あれば`over_ratio`の既定を見直す。
7. `elapsed_s`とclamscanの`Time:`の整合（`elapsed_s`はマウント・バージョン確認を含むので大きくなる）。
8. 感染検体（EICAR）での台帳作成。clamscan FAIL後に`hash-inventory.tsv`が作られ、`details.digest`が`sha256sum`と一致すること。逆に、定義DBを一時的に外した状態では`hash-inventory.tsv`が作られないこと。

判定式「実容量 ≥ 公称容量の93%」は[capacity_judge.py](backend/usb_sentinel/libs/steps/capacity_judge.py)の`evaluate()`に実装済みである（`capacity.py`から再公開）。
