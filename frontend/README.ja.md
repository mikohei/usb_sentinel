[English](README.md)

# 表示フロントエンド（USB Sentinel Display）

Raspberry Pi 4に接続した3.5インチHDMIタッチディスプレイに、4ポート分の検査状態と選択中ポートのQRコードを表示するtkinterアプリケーションである。画面は480x320で設計し、実際の画面に合わせて拡大する。`backend/`には手を入れず、ホスト上で次の4つを1秒周期で読んで画面を組み立てる。

- `/var/lib/usb-sentinel/results/<timestamp>-<serial>/result.json`（検査結果。これが正本）
- `/var/lib/usb-sentinel/db/inspections.sqlite`（backendのSQLite索引。読み取り専用。無ければ結果ディレクトリの走査だけで動く）
- `/sys/block/`配下のUSBディスク情報（どのポートに何が挿さっているか）
- `systemctl show`で得る検査ユニットの状態（検査中かどうか）

HTTP APIや常駐サーバーは無い。ネットワークも使わない。画面の文言は英語と日本語を切り替えられる（既定は英語）。

## 画面構成

```text
x:   0        160                          480
y0   +--------+----------------------------+
     | P1タイル|                            |
y64  +--------+                            |
     | P2タイル|      QRキャンバス            |
y128 +--------+      320x296（白地）        |
     | P3タイル|                            |
y192 +--------+                            |
     | P4タイル|                            |
y256 +--+---+-+                            |
     | ◀|言語|▶|                            |
y288 +--+---+-+                            |
     | モード  |                            |
y296 |        +----------------------------+
     |        | 情報ストリップ320x24         |
y320 +--------+----------------------------+
```

- 左160pxに4ポートのタイル（各160x64）と、32px幅のボタン行が2段。上段（y256〜288）は◀（x0〜53）・言語（x53〜107）・▶（x107〜160）、下段（y288〜320）は検査モードを示すボタン1つ（「モード: 検査のみ」「モード: 検査＋消去」「検査のみ（消去不可）」「消去を停止中…」）。タイルは1行目にポート名と判定、2行目にシリアル、3行目に状態と時刻を出す。完了したタイルの2行目右端にはタグ（`検査のみ`＝スキャンだけ、`消去済`＝容量か速度の検査で書き込んだ）が付き、シリアルはその分短くなる。選択中のタイルには白い枠が付く。言語ボタンには切替先の名前（英語表示中は「日本語」、日本語表示中は「EN」）が出る。
- 右320pxに選択中ポートのQRコード（白地、最大294px）。通常QRと詳細QR（3枚）をタップで切り替える。その下の情報ストリップにポート名、シリアル先頭、ダイジェスト先頭16桁（詳細QR中は「詳細n/3」と12桁）、または警告文を出す。

## ディスプレイの設置

Ubuntu Desktop（Pi 4、GNOME）を前提にした手順である。`scripts/install-all.sh`は、タッチドライバ（手順2）とポートのキャリブレーション（手順9）を除く以下のすべてを行い、最後の手順でキオスクを有効化する。番号付きの手順は、一部を手でやり直すときに使う。backendのDocker構成（ルートの`README.ja.md`）はインストーラの手順4（`scripts/install-docker.sh`）が行う。

1. **より鮮明な表示（任意）。** アプリは480x320の設計を画面に合わせて拡大する（800x600なら1.67倍で余白は黒、960x640なら2倍）。`install-all.sh`は`INSTALL_VIDEO_HINT=1`を付けると`/boot/firmware/cmdline.txt`に`video=HDMI-A-1:480x320@60`を追加できる。これはテキストコンソールとGDMのログイン画面にだけ効き、アプリには影響しない。最も鮮明にするには、「設定→ディスプレイ」に480x320か960x640があればそれを選ぶ。この手順は必須ではない。
2. **タッチドライバ**を入れる。ベンダー手順（https://osoyoo.com/ja/?p=56145）に従う。ベンダーの注記では「Raspberry Pi OS bookwormではタッチ非対応」とあり、Ubuntuでの動作は未検証である。ドライバが動かなくても本アプリはキーボードで全操作できる（「操作」節）。外部サイトのスクリプトを本プロジェクトから自動取得・実行はしない。
3. **日本語フォント**を入れる。
   ```bash
   sudo apt-get install -y fonts-noto-cjk
   ```
4. **自動ログイン**をオンにする。「設定→ユーザー→自動ログイン」、または`/etc/gdm3/custom.conf`に`AutomaticLoginEnable=true`と`AutomaticLogin=<user>`を書く。
5. **自動マウントを無効にする。** Desktop版ではGNOMEがUSBを自動マウントし、`run-docker.sh`が「host-mounted disk refused」で検査を拒否するため必須である。
   ```bash
   gsettings set org.gnome.desktop.media-handling automount false
   gsettings set org.gnome.desktop.media-handling automount-open false
   ```
6. **スリープとスクリーンセーバーを止める。** Waylandセッションなので`xset`は使わない。
   ```bash
   gsettings set org.gnome.desktop.session idle-delay 0
   gsettings set org.gnome.desktop.screensaver lock-enabled false
   gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
   gsettings set org.gnome.desktop.screensaver idle-activation-enabled false
   ```
7. **結果ディレクトリを読めるようにする。** `scripts/install-docker.sh`は結果ルート`/var/lib/usb-sentinel/results`を`root:usb-sentinel`の`0750`で作る。表示ユーザーを`usb-sentinel`グループに加えて再ログインする。書き込み権限は与えない（証跡の改変を防ぐ）。
   ```bash
   sudo usermod -aG usb-sentinel <user>
   # 再ログイン後に有効
   ```
   旧版の`install-docker.sh`（結果ルートを`0700`で作っていた版）で導入した機では、グループと権限を手で設定する。
   ```bash
   sudo groupadd --system usb-sentinel
   sudo chgrp usb-sentinel /var/lib/usb-sentinel/results
   sudo chmod 0750 /var/lib/usb-sentinel/results
   ```
   画面に「結果ディレクトリを読む権限がない」が出たら、まずこの手順を疑う。

   同じグループでSQLite索引（`/var/lib/usb-sentinel/db`、`2770`）も読める。backendはWALモードで書くため、読み取り専用で開く側も`-shm`ファイルを作れる必要があり、ディレクトリにグループの書き込み権限が要る（`2770`で満たす）。DBファイル自体には書かない。グループに入っていなければ索引を使わず、従来の結果ディレクトリの走査にフォールバックする（「SQLite索引の読み取り」節）。
8. **uvと依存を取り込む。** ネットワーク接続中に行う。
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   cd frontend
   uv sync --frozen --python /usr/bin/python3 --python-preference only-system
   uv run python -c "import tkinter"
   ```
   venvはシステムのPythonで作る。tkinterをaptの`python3-tk`から使うためである（日本語表示に要るXft対応のTk）。`import tkinter`が通らなければ`python3-tk`を入れる。手順10の`.desktop`の`Exec`は変えない。

   **依存の同期はこの手順でだけ行う。** `deploy/usb-sentinel-display.desktop`の`Exec`は`uv run --offline --frozen --no-sync`で起動する。これらが無いと`uv run`は起動ごとにロックと環境の整合を確認し、差分があれば解決や同期を試みる。ネットワーク非接続のPiではそれがエラーになって起動が止まるので、起動時には何も解決しない。backendが`uv sync --frozen`で環境を揃えるのと同じ方針である。
9. **ポートをキャリブレーションする。** 「ポートのキャリブレーション」節のとおり`uv run usb-sentinel-display calibrate`を実行し、各ポートのポートパスを`~/.config/usb-sentinel/display.toml`に書く。
10. **自動起動を登録する。** `deploy/usb-sentinel-display.desktop`の`<user>`とプロジェクトのパスを書き換え、`~/.config/autostart/`にコピーする。
    ```bash
    mkdir -p ~/.config/autostart
    sed "s|<user>|$USER|g" deploy/usb-sentinel-display.desktop > ~/.config/autostart/usb-sentinel-display.desktop
    ```
    プロジェクトを`/home/<user>/usb_sentinel`以外に置いた場合は`--project`のパスも直す。
    `install-all.sh`は手順6でこのファイルを`Hidden=true`付きで書き、手順10でキオスクを有効化するときにその行を外す。有効化しない指示（`INSTALL_KIOSK=0`または`n`）をしたときだけ、あとで`sudo bash ../scripts/kiosk-enable.sh`がその行を外す。項目は5秒待ってから起動し（`X-GNOME-Autostart-Delay=5`）、アプリの出力を`~/.local/state/usb-sentinel-display/autostart.log`に書く。
11. **動作を確認する。** 手動で起動し、USBを挿して表示を確かめる。
    ```bash
    uv run usb-sentinel-display run --windowed
    ```
    `Ctrl+Q`で終了し、`sudo reboot`で再起動する。自動起動した全画面に4つのタイルが出る（インストーラでキオスクを有効化しなかった場合は、先に`sudo bash ../scripts/kiosk-enable.sh`を実行する）。
    `--kiosk`では`Ctrl+Q`が使えない。復旧はテキストコンソール（Ctrl+Alt+F3）またはSSHから`pkill -f usb-sentinel-display`で行う。自動起動とロックダウンを戻すには`sudo bash scripts/kiosk-disable.sh`を実行する。

## ポートのキャリブレーション

結果ディレクトリ名や`result.json`には物理ポートの情報が無い。そこでsysfsのUSBトポロジー（ポートパス）から物理ポートを求める。ポートパスは個体ごとに違い、同じ物理ポートでもUSB 2.0のデバイスとUSB 3.0のデバイスで変わる（Pi 4ではUSB 2.0は`1-1.1`〜`1-1.4`、USB 3.0は`2-1`、`2-2`のように現れる）。

1. キャリブレーションを起動する。GUIは出ない。
   ```bash
   cd frontend
   uv run usb-sentinel-display calibrate
   ```
2. USBメモリを1本ずつ各ポートに挿す。挿すたびに次の形式で1行印字される。
   ```text
   sda 1-1.3 ABC123456789
   ```
   左からカーネル名、ポートパス、シリアルである。USB 2.0のメモリとUSB 3.0のメモリを順に挿し、ポートごとに両方のポートパスを控える。`Ctrl+C`で終了する。
3. `~/.config/usb-sentinel/display.toml`に`[[ports]]`を書く。出現順にタイルの1〜4へ入る。
   ```toml
   [[ports]]
   label = "P1"
   paths = ["1-1.1", "2-1"]

   [[ports]]
   label = "P2"
   paths = ["1-1.2", "2-2"]

   [[ports]]
   label = "P3"
   paths = ["1-1.3"]

   [[ports]]
   label = "P4"
   paths = ["1-1.4"]
   ```

対応表に無いポートパスのディスクは、空いているタイルに`P?`として表示され、カーネル名が添えられる。対応表が空なら全部が`P?`になる。

## 設定ファイル

`~/.config/usb-sentinel/display.toml`に置く。無ければ既定値で動く。全キーの例は`deploy/display.example.toml`にある。

| キー | 型 | 既定 | 意味と検証 |
|---|---|---|---|
| `results_dir` | 文字列 | `/var/lib/usb-sentinel/results` | 結果ルート。無くても起動し、情報ストリップに文言を出して毎秒再試行する |
| `sys_block` | 文字列 | `/sys/block` | sysfsのブロックデバイス一覧。無くても起動する |
| `unit_template` | 文字列 | `usb-sentinel-docker@{kernel}.service` | 検査ユニット名の雛形。`{kernel}`を含まないと起動しない |
| `poll_interval_s` | 数値 | `1.0` | 更新周期（秒）。`0.2`〜`10` |
| `retain_minutes` | 整数 | `60` | 抜去後に結果を表示し続ける時間（分）。`1`以上 |
| `font_family` | 文字列 | `Noto Sans CJK JP` | 無ければ`Noto Sans CJK JP`、`Noto Sans JP`、`Noto Sans CJK SC`、`IPAGothic`、`IPAexGothic`、`TakaoGothic`、`VL Gothic`の順に探し、選んだフォント名をログに出す。どれも無ければTkの既定フォントを使い、情報ストリップに「No Japanese font (install fonts-noto-cjk)」と出す |
| `language` | 文字列 | `en` | 起動時の画面の言語（`en`／`ja`）。画面の言語ボタンかキー`L`で切り替えた言語は状態ファイル（「操作」節）に記録され、設定より優先される。それ以外の値は起動エラー |
| `sqlite_path` | 文字列 | `/var/lib/usb-sentinel/db/inspections.sqlite` | backendのSQLite索引。読み取り専用で開く。空文字`""`で無効化し、結果ディレクトリの走査だけにする。無い・開けないときは自動で走査にフォールバックする（「SQLite索引の読み取り」節） |
| `control_dir` | 文字列 | `/run/usb-sentinel-control` | `run-docker.sh`が挿入ごとに読む検査モードファイルの置き場所。状態ディレクトリ以外でアプリが書く唯一の場所。絶対パスで、`results_dir`と`sqlite_path`のディレクトリの外にあること。`""`で切替を無効にする（ボタンは「検査のみ（消去不可）」） |
| `host_config_dir` | 文字列 | `/etc/usb-sentinel` | backendの`config.toml`と`docker.env`があるディレクトリ。管理者が消去を許しているか（`allow_destructive = true`と`USB_SENTINEL_DEVICE_ACCESS=rw`）を読むだけ。そうでなければボタンはロックされる |
| `sys_class_input` | 文字列 | `/sys/class/input` | 入力機器のsysfs一覧。ブート後の最初の起動でその時点の機器を記録し、後から現れた機器は再起動まで消去モードをロックする |
| `[[ports]]` | `label`文字列、`paths`文字列配列 | 空 | 出現順にタイル1〜4。`label`は1〜4文字の印字可能ASCII。`label`重複、`paths`重複、5件以上は起動エラー |

型違いや未知のキーは起動時に`Configuration error: ...`を標準エラーに出して終了コード1で止まる。

コマンドラインの値は設定ファイルを上書きする。`--config`は共通オプションなのでサブコマンドの前に置く。

```bash
uv run usb-sentinel-display --config /path/to/display.toml run --results-dir /path/to/results --sys-block /path/to/sys_block
```

`run`のオプションは`--results-dir`、`--sys-block`、`--windowed`または`--kiosk`（排他）、`--scale <倍率>`（「操作」節を参照）、`--no-systemctl`（`systemctl`を呼ばない。開発機用。ユニットの状態が取れないため、SQLite索引に`started`行があるディスクは結果が出るまで「検査中」のまま表示される）。
`calibrate`のオプションは`--sys-block`と`--interval`（確認間隔の秒数。既定は`1.0`）。

## 操作

| 操作 | タッチ | キーボード |
|---|---|---|
| ポート選択 | タイルをタップ | `1`〜`4` |
| 前後へ切り替え | ◀ ▶をタップ | `←` `→`（`↑` `↓`も同じ）。通常QR表示中はポートの前後。詳細QR表示中はページ送り（1/3→2/3→3/3→1/3。◀は逆順）。詳細QR中のポート切替はタイルのタップか`1`〜`4` |
| 言語切替 | 左下中央のボタン（切替先の言語名を表示） | `L` |
| 詳細QRと通常QRの切替 | QR領域（右側）をタップ | `Q`。通常QR→詳細QRの1枚目、詳細QR中→通常QR |
| 強制再読み込み | なし | `F5`。読み込み済みの`result.json`のキャッシュと「結果破損」の記録を捨て、次の更新で全部読み直す |
| 検査モードの切替 | モードボタン（左下の最下段）をタップ | `M`（`--kiosk`でも使える）。検査のみからはコードのダイアログが開く。「検査＋消去」からはすぐに検査のみへ戻る。導入スクリプトがホスト側の消去許可を既定で有効にするため、操作者は`M`で切り替えるだけでよい |
| コード入力（ダイアログ中） | `1`〜`4`の4つのボタンをタップ | `1`〜`4`（テンキーでも可）。ダイアログに`コード: 3 1 4 2`のように出る数字を順に入れる |
| ダイアログを閉じる | 右上の「やめる」をタップ | `Esc`。誤入力に数えずに閉じる |
| 終了 | なし | `Ctrl+Q`（`--kiosk`では無効） |

タッチはマウスのクリックとして届くので、タッチが動かない環境でもキーボード（USBテンキーでも可）だけで全操作ができる。QRのモード（通常／詳細の何枚目か）はポートを切り替えても保たれる。

コードのダイアログが開いている間、他のキーとタップ（ポート選択、矢印、`L`、`Q`、`F5`）は無視される。コードがそろわないまま30秒たつとダイアログは自動で閉じる。コードは試行ごとにランダムで画面にしか出ないため、打鍵しかできない機器（BadUSBのキーボード）では消去モードにできない。消去モードにするには、ホストが消去を許していること（`host_config_dir`）、`control_dir`が使えること、表示ユーザーが`usb-sentinel`グループに入っていること（追加後は再ログイン）、ブート後に入力機器が増えていないこと（意図してキーボードを挿したら再起動）、累計の誤入力が3回未満であることが必要である。試行は毎回、コードを表示する前に言語ファイルの隣の`$XDG_STATE_HOME/usb-sentinel-display/erase-strikes`に記録され、管理者が`rm`で消す。モードは`control_dir/mode`に`<mode> <epoch>`として書き、5秒ごとに打刻を更新する。起動時と終了時に検査のみへ戻し、前回の状態の復元や結果からの推定はしない。「検査＋消去」の間に挿したドライブは抜くまでそのモードのままである。

言語は切り替えるたびに状態ファイルに記録され、次回の起動では設定ファイルの`language`より優先される。記録先は`$XDG_STATE_HOME/usb-sentinel-display/language`、`XDG_STATE_HOME`が無ければ`~/.local/state/usb-sentinel-display/language`である。書けないときは標準エラーに警告を1回出すだけで、表示は切り替わる。結果ディレクトリには何も書かない。

起動モードは3つあり、拡大の指定が1つある。全画面では480x320の設計を画面に合わせて拡大し、余白を黒で埋める。

- 指定なし: 全画面。`Ctrl+Q`で終了できる。
- `--kiosk`: 全画面で`Ctrl+Q`を無効にする。`.desktop`はこのモードで起動する。停止は別の端末（SSHなど）から`pkill -f usb-sentinel-display`で行う。アプリはSIGTERMを受けると次の更新で終了する。
- `--windowed`: 480x320のウィンドウで表示する（`--scale`を付けるとその倍率）。開発機用。
- `--scale <倍率>`: 画面に合わせる代わりに、480x320の設計を固定の倍率（0.25〜8）で描く。開発用。`--windowed --scale 2`は960x640のウィンドウを開く。範囲外の値は終了コード2で止まる。

設定ファイルを変えた後の再起動手順は次のとおり。

1. `pkill -f usb-sentinel-display`で停止する。
2. 自動起動（`.desktop`）で動かしているなら再ログインする。手動で起動していたなら再度`uv run usb-sentinel-display run`を実行する。

## 画面用QRコード

画面のQRは`result-qr.png`とは別の短縮形式（`v:2`）である。LCD上で読みやすくするため、上限150バイト、誤り訂正レベルM、QRバージョン8以下に収める。`result-qr.png`（`v:1`、上限2953バイト、誤り訂正L、シグネチャ一覧つき）はそのまま残る。読み取り側は`v`で区別する。

```json
{"v":2,"r":"I","s":"ABC123456789","t":"20250601T120000Z","d":"0123456789abcdef","st":"m:P,h:P,c:S,s:S","n":0}
```

| キー | 意味 | 値 |
|---|---|---|
| `v` | 形式バージョン | `2`固定 |
| `r` | 判定 | `N`=No Defect、`n`=No Defect (Scan Only)、`M`=Malware Detected、`C`=Counterfeit Suspected、`S`=Spec Mismatch、`D`=Defective、`I`=Inconclusive、`?`=未知 |
| `s` | シリアル | `result.json`の`device.serial`をASCII化した先頭32文字。非ASCII文字は`?`になる。空ならカーネル名を入れ`k`を付ける |
| `k` | `s`がカーネル名であることを示す | `1`。シリアルのときはキー自体が無い |
| `t` | 検査開始時刻（UTC） | `YYYYmmddTHHMMSSZ`。結果ディレクトリ名の先頭16文字と一致する |
| `d` | `result.json`のSHA-256先頭16桁 | 小文字hex |
| `st` | ステップ状態 | `名前:状態`のカンマ区切り。名前は`m`=malware、`h`=hash_inventory、`c`=capacity、`s`=speed、`u`=unmount、その他は先頭3文字。状態は`P`=pass、`F`=fail、`E`=error、`S`=skipped、`N`=not_implemented |
| `n` | マルウェア検知数 | malwareステップの`details.detections`の要素数 |

### 詳細QR（v:3、3枚）

容量・速度などの数値を人が読める材料として持ち出すためのQRである。QR領域のタップかキー`Q`で通常QRと切り替え、◀▶でページを送る（お薬手帳のQRと同じ分割方式）。仕様の正本は`doc/qr-payload.ja.md`第5節で、ここでは要点だけ書く。

| 枚 | 内容 |
|---|---|
| 1/3 | 同定と判定（開始時刻、シリアル、判定コード、所要秒、検知数、ClamAV DBバージョン） |
| 2/3 | 容量とデバイス（公称・申告・実測容量、容量の区別、申告超過、消失率、リンク速度、デバイスクラス、台帳行数） |
| 3/3 | 速度とDB・台帳（読み書き速度と閾値、判定根拠、重大度、ClamAV DB日付、台帳ダイジェスト） |

- 3枚とも先頭に`v`=`3`、`p`（`1/3`〜`3/3`）、`d`（`result.json`のSHA-256先頭12桁）を持つ。読み取り順は問わず、`d`が同じ3枚を結合する。結合の手順は`doc/qr-payload.ja.md`第5.6節。
- 制約は通常QRと同じで、各枚150バイト以下、ASCIIのみ、誤り訂正M、QRバージョン8以下。1枚目が150バイトを超えるときはシリアルを16文字に切る。それでも収まらない枚だけ「QR生成失敗」と出し、他の枚は表示する。
- `result.json`に値が無い項目は`null`になる。第1期のbackendが書いた結果でも3枚とも生成される。
- 画面の言語はペイロードに影響しない。

### 持ち出した結果との照合

`result.json`はファイルのバイト列をそのままハッシュしているので、持ち出し側のPCで次を実行し、画面のQRの`d`と比べる。

```bash
sha256sum result.json | cut -c1-16   # 通常QRのd（16桁）
sha256sum result.json | cut -c1-12   # 詳細QRのd（12桁）
```

16桁（詳細QRでは12桁）が一致すれば同じ`result.json`である。完全な検証が必要なら`result-qr.png`の64桁の`digest`を使う。

## 表示の意味

タイルの1行目はポート名と判定ラベル、背景色は判定色である。色だけに頼らず、文字ラベルとQRの1文字コードを併記する。ラベルは画面の言語で切り替わる。英語ラベルはタイル幅（160px、18px太字）に収めるための短縮形で、`Spec Mismatch`は`Mismatch`になる。日本語ラベル「異常なし（検査のみ）」はタイル幅を超えるため、この判定だけ1行目を13px太字で描く。

| 判定 | 背景色 | ラベル（日本語） | ラベル（英語） | コード |
|---|---|---|---|---|
| No Defect | `#2E7D32`（緑） | 異常なし | No Defect | N |
| No Defect (Scan Only) | `#00695C`（青緑） | 異常なし（検査のみ） | Scan OK | n |
| Malware Detected | `#B71C1C`（赤） | マルウェア検知 | Malware | M |
| Counterfeit Suspected | `#AD1457`（濃いマゼンタ） | 偽装疑い | Counterfeit | C |
| Spec Mismatch | `#E65100`（橙） | 仕様不一致 | Mismatch | S |
| Defective | `#6A1B9A`（紫） | 不良 | Defective | D |
| Inconclusive | `#5D4037`（茶） | 判定不能 | Inconclusive | I |
| 未知の判定 | `#424242`（濃灰） | 判定不明 | Unknown | ? |

判定が出る前と抜去後は、次の状態文言になる（括弧内は英語表示）。

| 状態 | 条件 | 表示 |
|---|---|---|
| 未接続 | ポートにディスクが無く、保持中の結果も無い | 「未接続」（No disk）。灰色 |
| 起動待ち | ディスクあり、ユニット未起動、結果なし、接続から20秒未満 | 「起動待ち」（Starting）。青 |
| 検査中 | 検査ユニットが`active`または`activating`。またはSQLite索引に`started`行があり、`systemctl`がユニットの状態を答えない | 「検査中（N分）」（Scanning (N min)）。青。末尾の「…」が1秒ごとに動く |
| 完了 | 結果が結び付き`result.json`が読めた | 判定色と判定ラベル、3行目に「完了HH:MM」（Done HH:MM） |
| 結果なし | ディスクあり、ユニットが`inactive`／`failed`／不明、結果なし、接続から20秒以上。SQLite索引に`started`行があってもユニットが停止していれば起動待ちを飛ばしてこの状態になる（USB切断の疑い） | 「結果なし」（No result）。灰色。情報ストリップに`journalctl -u <ユニット名>`で始まる案内が出る。安全ガードによる検査拒否（終了コード2）か起動失敗 |
| 結果破損 | `result.json`が10秒間読めない（切れたJSON、必須キー欠落）。またはSQLite索引の`result_digest`と`result.json`のSHA-256が一致しない（即時） | 「結果破損」（Broken）。灰色 |
| 抜去 | 完了後にディスクが消えた | 判定色を暗くし、3行目に「抜去HH:MM」（Removed HH:MM）。QRは引き続き表示する。保持は`retain_minutes`か次のディスクが挿されるまで |

Malware Detectedのタイルは枠を1秒ごとに明赤と背景色で交互に塗る。音は出さない。

## SQLite索引の読み取り

backend第2期は検査1件ごとに`/var/lib/usb-sentinel/db/inspections.sqlite`の`inspections`テーブルへ1行を書く（開始時に`state='started'`、`result.json`を書いた後に`state='completed'`）。frontendはこのDBを索引として読む。正本は従来どおり`result.json`で、DBの値を画面に出すことはない。

**何を読むか。** 接続中のディスクの接続時刻以降に始まった行（`started_at`の下限、プレースホルダ付きのSELECT 1文）を候補にする。行ごとに`results_dir/<inspection_id>/result.json`を従来どおり読み、SHA-256を再計算して`result_digest`と比べる。一致すれば完了結果、不一致なら即「結果破損」にする。`state='started'`で`result.json`がまだ無い行は「検査中」か「結果なし」の材料にする（「表示の意味」の状態表）。`inspection_id`は結果ディレクトリ名の形式（`<timestamp>-<serial>`）を通ったものだけを使い、`output_dir`列は使わない。

**候補が複数あるとき。** 同じポートの候補が複数あれば、ディレクトリ名のシリアルが一致するものを優先し、その中に「マルウェア検知」があればそれを選ぶ。残りが並べばディレクトリ名の大きいものにする。1回の挿入に結果は通常1件だが、時計が戻ると前のセッションの結果も新しく見えて候補に入る。そのとき検知を古い結果で隠さないためである。時計の巻き戻しはルートREADMEの「安全設計」を参照。

**どう開くか。** `file:...?mode=ro`のURIで接続し、`PRAGMA query_only = ON`と`PRAGMA busy_timeout = 1000`を設定する。SQLはSELECTだけで、DBには書かない。`PRAGMA user_version`が想定（`2`）より大きければ読まない。版2で`mode`列（`scan_only`／`full`。更新前の行はNULL）が増えたが、frontendは読まない。タイルのタグは`result.json`から作る。

**フォールバック。** 次の場合は索引を使わず、従来の結果ディレクトリの走査に切り替える。表示は止まらず、情報ストリップに理由を出す。

| 情報ストリップ（日本語／英語） | 条件 |
|---|---|
| SQLite索引が無いため結果ディレクトリを走査／No SQLite index; scanning results dir | `sqlite_path`のファイルが無い、または`user_version`が0の空のDB |
| SQLite索引を開けないため結果ディレクトリを走査／SQLite index unreadable; scanning results dir | 接続・PRAGMA・SELECTのいずれかが失敗した。壊れたファイル、テーブル無し、権限なし、WALの`-shm`を作れないなど |
| SQLite索引が新しい形式のため結果ディレクトリを走査／SQLite index schema is newer; scanning results dir | `user_version`が`2`より大きい。frontendを更新する |

結果ディレクトリやsysfsの文言（「結果ディレクトリが見つからない」など）があるときはそちらが優先され、SQLiteの文言は出ない。

**権限。** 表示ユーザーが`usb-sentinel`グループに入り、`/var/lib/usb-sentinel/db`（`2770`）を読めること。backendはWALモードで書くので、読み取り専用で開く側も`-shm`ファイルを作る必要があり、ディレクトリにグループの書き込み権限が要る。DBファイル自体には書かない。グループに入っていなければ「SQLite索引を開けない」になり、走査にフォールバックする（「ディスプレイの設置」手順7）。

**`started`行の扱い。** `state='started'`の行が接続中のディスク（ポートパス、無ければカーネル名で突き合わせる）に結び付くと、`systemctl`がユニットの状態を答えない間は「検査中」にする。ユニットが`inactive`／`failed`なら接続直後でも起動待ちを飛ばして「結果なし」にする。`result.json`があれば`started`のままでも完了として扱う（完了記録の失敗）。

**索引に行が無い結果。** 索引が読めるときは索引の行だけを候補にし、走査結果は混ぜない。backendが開始記録に失敗した検査（`result.json`の`warnings`とbackendの標準出力（journal）に`SQLite record failed (start)`が残る）は、`result.json`があっても索引に行が無いため表示されず、ディスクは起動待ちの後に「結果なし」になる。この場合はbackendのjournalを確認し、必要なら`sqlite_path = ""`で索引を切って走査に戻す。

## backendとの同時配備

`Counterfeit Suspected`の判定、詳細QR（`v:3`）の数値、SQLite索引はbackend第2期が書く。frontendもbackendと同時に第2期へ更新する。古いfrontendのまま新しいbackendを動かすと、`Counterfeit Suspected`は判定コード`?`・ラベル「判定不明」になり、SQLite索引は読まれない。逆に新しいfrontendと古いbackendでは、詳細QRの数値が`null`になり、SQLite索引が無い旨の文言が情報ストリップに出続ける（`sqlite_path = ""`で消せる）。

検査モード切替でSQLiteのスキーマは`user_version` 2（`mode`列）になり、`result.json`に`mode`、詳細QRの1枚目に`md`が増えた。backend、`scripts/run-docker.sh`、`scripts/install-docker.sh`（`/run/usb-sentinel-control`のtmpfiles設定）、frontendは同時に更新する。古いfrontendと新しいbackendでは`user_version` 2を拒否して結果ディレクトリの走査にフォールバックする（「SQLite索引が新しい形式」）。新しいfrontendと古いbackendではタイルにタグが出ず、`md`は`null`になり、モードボタンはホストに効かない。

## トラブルシュート

ログイン後にアプリが出ないときは、まず`~/.local/state/usb-sentinel-display/autostart.log`を読む（自動起動の項目がアプリの出力をここに書く）。ファイルが無ければ項目に`Hidden=true`が残っている。インストーラでキオスクを有効化しなかった状態である。`sudo bash scripts/kiosk-enable.sh`を実行する。

情報ストリップの文言ごとの原因と対処。

| 文言 | 原因 | 対処 |
|---|---|---|
| 結果ディレクトリを読む権限がない（README手順7。usb-sentinelグループと再ログインを確認） | 表示ユーザーが`usb-sentinel`グループに入っていない、または再ログインしていない | 「ディスプレイの設置」手順7。`id`でグループを確認する |
| 結果ディレクトリが見つからない | `results_dir`のディレクトリが無い | backendの導入（`install-docker.sh`）を確認する。開発機では`--results-dir`で指す |
| sysfsが見つからない（sys_blockの設定を確認） | `sys_block`のディレクトリが無い | 設定の`sys_block`を確認する。開発機（macOS）では`--sys-block`で偽ツリーを指す |
| sysfsを読む権限がない | `sys_block`が読めない | ディレクトリの権限を確認する |
| `journalctl -u usb-sentinel-docker@sdX.service`で始まる案内 | ディスクはあるが検査ユニットが動いておらず結果も無い | `journalctl -u usb-sentinel-docker@sdX.service`で検査拒否（終了コード2）か起動失敗かを見る。自動マウントが有効だと拒否される（手順5） |
| 結果破損 | `result.json`が10秒以上読めない | `cat result.json`で内容を確認する。backendが書き込み中なら待つ。`F5`で再読み込み |
| 内部エラー: <例外名> | 更新処理で例外が出た。前回の表示は保持される | 標準エラー出力（自動起動なら`~/.local/state/usb-sentinel-display/autostart.log`）のスタックトレースを見る |
| 内部エラーが継続中。標準エラー出力を確認 | 同じ例外が30秒連続した | 同上 |
| 画面解像度WxH（480x320未満） | 画面が設計より小さい | 480x320以上のモードを選ぶ（「設定→ディスプレイ」） |
| 他N台 | 5台以上接続している | タイルは先頭4台だけ表示する |
| SQLite索引が無い／開けない／新しい形式のため結果ディレクトリを走査（SQLiteの文言が出続ける） | 索引が読めず走査にフォールバックしている。表示は続く | 表示ユーザーが`usb-sentinel`グループに入っているか（`id`）、`sqlite_path`のパスにbackendがDBを作っているか（`ls -l /var/lib/usb-sentinel/db`）、ディレクトリが`2770`かを確認する。backend第2期を導入していない機や開発機では`sqlite_path = ""`で索引を無効にすれば文言は消える。「新しい形式」はfrontendの更新で直す |

文言は英語表示でも同じ順で対応する（「SQLite索引の読み取り」節の表）。

標準エラー出力にだけ出るもの。各行は`WARNING usb_sentinel_display.libs.ui.app:`や`ERROR usb_sentinel_display.libs.cli:`のように、重大度とロガー名で始まる。

| 文言 | 原因 | 対処 |
|---|---|---|
| Failed to save language: <パス> (<例外>) | 言語の記録先（`$XDG_STATE_HOME/usb-sentinel-display/language`か`~/.local/state/…`）に書けない。表示の切替自体は行われ、警告は1回だけ出る | ディレクトリの権限を確認する。記録しなくても起動時は設定ファイルの`language`で動く |
| Could not record erase-mode attempt: <理由> | 言語ファイルの隣の誤入力ファイルに書けない。消去モードはロックされたまま（「誤入力回数を記録できないため消去不可」） | `~/.local/state/usb-sentinel-display/`の権限を確認する |

モードボタンの文言（押した後5秒間、情報ストリップに出る）。

| 文言 | 原因 | 対処 |
|---|---|---|
| この機器では消去が無効（管理者設定） | `host_config_dir`の`allow_destructive = true`と`USB_SENTINEL_DEVICE_ACCESS=rw`がそろっていない | ルートREADME「3. USBを挿して実行確認」 |
| /etc/usb-sentinelの設定を読めない | `config.toml`か`docker.env`が無いか読めない | `host_config_dir`とファイルのモード（0644）を確認する |
| モード制御を使えない（グループ、再ログイン） | `control_dir`が無い、シンボリックリンク、または書けない | `/run/usb-sentinel-control`（`root:usb-sentinel 2770`。`install-docker.sh`が作る）と、表示ユーザーがグループに入って再ログインしたことを確認する |
| モード切替は無効（control_dir） | `control_dir = ""` | 意図どおり。有効にするにはキーを設定する |
| 新しい入力機器を検出。再起動まで消去不可 | このブートの最初の起動より後に入力機器が現れた | 抜いて再起動する |
| 入力機器を監視できないため消去不可 | `sys_class_input`を列挙できない | 設定と`/sys/class/input`が読めることを確認する |
| コード誤り。60秒後に再試行／コード誤り3回。管理者の解除まで消去不可 | コードの誤入力。累計3回 | 待つ。または`rm ~/.local/state/usb-sentinel-display/erase-strikes`して再起動する |
| 消去を解除できない。20秒以内に自動解除 | モードファイルを書き換えられなかった | ホストは20秒で`full`を採用しなくなる。`control_dir`を確認する |
| 挿入済みのドライブは消去されうる。抜いてください | 検査中のドライブがあるまま消去モードを解除した | ドライブを抜けば検査は止まる |

タイルの表示に関するもの。

| 症状 | 原因 | 対処 |
|---|---|---|
| ポート名が`P?`でカーネル名が添えられる | ポートパスが対応表に無い | 「ポートのキャリブレーション」節。対応表が空なら全部`P?`になる |
| 日本語が豆腐（□）になる、または起動時に`Font Noto Sans CJK JP not found; using ...`が出る | フォント未導入 | 手順3 |
| 全画面にならない、起動しない | `.desktop`のパスが違う、`uv sync`未実行 | `~/.config/autostart/usb-sentinel-display.desktop`の`Exec`を端末で直接実行して標準エラーを見る |

## 開発

コマンドはすべて`frontend/`で実行する。macOSで開発とテストが完結する。sysfsは`tmp_path`の偽ツリー、`systemctl`は`FakeRunner`で差し替える。

```bash
cd frontend
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
uv run python -m usb_sentinel_display --help
```

すべて終了コード0であること。tyは警告1件でも終了コード1になる。

- `ui/`以外は`tkinter`をimportしない。ロジック層（`libs/`直下）はGUIなしで`pytest`が通る。`ui/i18n.py`と`ui/strip.py`も`tkinter`を読まない。
- UIの文言は`ui/i18n.py`にja／enの2組で持ち、GUI層はキーで参照する。QRペイロード（`qr_payload.py`）は言語に依存しない。
- SQLite索引（`store.py`）のテストは`tests/test_store.py`で、backendのスキーマv1を文字列でコピーして使う。backendを`import`しない。
- `tk.Tk()`を必要とする煙テストは`@pytest.mark.gui`が付いている。`uv run pytest -m gui`で選べる。`DISPLAY`が無い環境では自動でskipする。

### 画面の確認

偽のsysfsツリーとサンプル結果を作り、ウィンドウ表示で確認する。

```bash
# 偽ツリーとサンプル結果を/tmp/usb-sentinel-fixturesに作る（--scene a|b|c）。
# モードボタン用にcontrol/、host/（上限オン）、input/input0/、state/も作る。
# --ceiling offでボタンをロック、--strikes 3で誤入力回数を先に入れる。
uv run python scripts/make-fixtures.py /tmp/usb-sentinel-fixtures --scene a

# ウィンドウで起動する
uv run usb-sentinel-display --config /tmp/usb-sentinel-fixtures/display.toml run --windowed --no-systemctl

# 12秒後にスクリーンショットを撮る（macOSのscreencaptureを使う）
uv run python scripts/dev-screenshot.py --fixtures /tmp/usb-sentinel-fixtures --scene a --select 1 --out /tmp/scene-a.png

# モードのシーン: --mode-scene none|scan_only|confirm|full|locked|lockout。--code 3142でダイアログのコードを固定する。
uv run python scripts/dev-screenshot.py --fixtures /tmp/usb-sentinel-fixtures --mode-scene confirm --out /tmp/confirm.png
```

シーンAは4台（異常なし、マルウェア検知、検査中、結果破損）、シーンBは2台（仕様不一致、不良）とP3・P4の未接続である。`dev-screenshot.py`は`systemctl`の出力を偽装して「検査中」を出し、`XDG_STATE_HOME`を`<fixtures>/state`に向け、同じディレクトリの`scripts/fixtures_mode.py`と`scripts/dev_mode_scenes.py`を使う。Retina画面では`screencapture`が960x640で書くので、`sips -z 320 480 <png>`で縮める。`--geometry 400x300`で480x320未満の警告、`--results-dir /nonexistent`で「結果ディレクトリが見つからない」を確認できる。`make-fixtures.py`が書く`display.toml`は`sqlite_path = ""`で索引を無効にしているので、開発機でSQLiteの文言は出ない。

## 実機確認項目

開発機（macOS）では確認できず、Pi（aarch64、Ubuntu Desktop）で確かめる事項。

1. システムのPython 3.14とaptの`python3-tk`で`import tkinter`が通ること（手順8）。
2. パネルの実際のモードでの拡大表示。黒い余白が出て、タイルとQRが読め、QRをスマートフォンで読み取れること。
3. タッチドライバがUbuntuで動き、タップが`<Button-1>`として届いてタイルと◀▶で選択が変わること。動かなくてもキーボードで運用できる。
4. sysfsのポートパスのディレクトリ（例`/sys/bus/usb/devices/1-1.3`の実体）の`st_mtime`がUSBを挿した時刻になること。`stat`で挿入前後を比べる。ならない場合はシステム起動時刻へフォールバックするため、起動後に書かれた結果はすべて候補になる（起動前の結果は拾わない）。
5. `systemctl show -p Id -p ActiveState`に複数ユニットを渡したときの出力が、`Id=`で始まるブロックの空行区切りであること。
6. 検査拒否（終了コード2）を「結果なし」と区別できるか。`systemctl show -p ExecMainStatus`が終了後も残れば「検査拒否」の表示を分けられる。
7. コンテナが作る結果ディレクトリとファイルの権限が`0755`／`0644`で、`usb-sentinel`グループの表示ユーザーが`result.json`を読めること。
8. Noto Sans CJK JPでの行送り。18px太字と14px×2行の合計が58pxを超えると2行構成（時刻を2行目末尾へ、シリアルを短縮）に切り替わる。その見え方。
9. `--kiosk`で`Ctrl+Q`が効かず、`pkill -f usb-sentinel-display`で次の更新に終了すること。開発機では`--windowed`でSIGTERM終了（終了コード0）まで確認した。
10. 全画面時のフォーカス維持（`<FocusOut>`後の再取得）とカーソル非表示。
11. 情報ストリップの`journalctl -u usb-sentinel-docker@sdX.service`の案内（約50文字）が320pxに収まること。収まらなければ右側が切れる。
12. スマートフォンでQRを読み取り、`sha256sum result.json | cut -c1-16`と`d`が一致すること。読み取りが安定しなければ誤り訂正をQに上げる案がある（その場合は上限を108バイトに下げる）。
13. 詳細QRの3枚（最大QRバージョン8、285px）がLCD上で読めること。
14. Noto Sans CJK JPで判定ラベルがタイル幅に収まること。`uv run pytest -q -m gui -k verdict_labels_fit_tile`を実機で1回走らせる。開発機の計測では「マルウェア検知」（141px、上限144px）の余裕が最も少ない。
15. `usb-sentinel`グループの表示ユーザーで`inspections.sqlite`が`mode=ro`で開け、情報ストリップにSQLiteの文言が出ないこと。出る場合は`/var/lib/usb-sentinel/db`の権限（`2770`）を確認する。
16. モードボタンとコードのダイアログ（18px太字のコード、104x108pxのボタン4つ）がパネルで読めてタップできること。日本語の文言がNoto Sans CJK JPで収まること。ディスプレイが書くモードファイルを`run-docker.sh`が受け付けること（`journalctl`に`notice: display requested full inspection`が出る）。ルートREADME「Raspberry Piでの確認項目」も参照。
