[English](qr-payload.md)

# QRペイロード仕様（ファイル用v:3・通常QR v:2・詳細QR v:3）とSQLiteの対応

usb_sentinelが出すQRコードの中身と、`result.json`・Pi側SQLiteとの対応を定める。読み取りアプリ（Flutter）の実装者がbackendのコードを読まずに済むことを目的にする。

| 記号 | 意味 |
|---|---|
| backend | Pi上のDockerコンテナで動く検査プログラム（`backend/`） |
| frontend | Piの3.5インチ画面に結果とQRを出すプログラム（`frontend/`） |
| 読み取りアプリ | QRを読んでケース情報と結び付けるFlutterアプリ（本仕様の読み手） |

## 1. 目的と3種のQRの使い分け

| 種類 | 置き場所 | `v` | 文字 | 上限 | 用途 |
|---|---|---|---|---|---|
| ファイル用QR | 結果ディレクトリの`result-qr.png` | `3` | UTF-8 | 2953バイト、誤り訂正L | 持ち出し媒体に入れる全文。数値・理由文・シグネチャまで入る |
| 通常QR | 画面（frontend） | `2` | ASCII | 150バイト、誤り訂正M | 検査の同定。判定・シリアル・開始時刻・ダイジェスト・ステップ状態 |
| 詳細QR | 画面（frontend）、3枚をページ送り | `3` | ASCII | 各150バイト、誤り訂正M | 人が判断する材料になる数値（容量・速度・台帳）。3枚を結合して1件にする |

読み取りアプリはまずJSONとして解釈し、`v`で分岐する。`v`が`2`なら通常QR、`3`で`p`キーがあれば詳細QR、`3`で`p`が無ければファイル用QRである。未知の`v`は「対応していない形式」として扱い、生の文字列を保存しておく。

同じ検査を指すQRは、開始時刻・シリアル・`result.json`のダイジェストの3つで突き合わせる（第7節）。ダイジェストはファイル用QRが64桁、通常QRが先頭16桁、詳細QRが先頭12桁で、短いものは長いものの接頭辞になる。

## 2. 共通規則

- 形式はJSONオブジェクト1つ。`json.dumps(..., separators=(",", ":"))`で区切り文字の後の空白を詰める。
- 画面用（通常QR・詳細QR）はASCIIだけ（`ensure_ascii=True`）。非ASCIIのシリアルは`?`に置き換える。ファイル用QRはUTF-8（`ensure_ascii=False`）で、理由文は英語なのでほぼASCIIである。シリアルや外部コマンドの出力に由来する非ASCIIの文字は、UTF-8のまま入る。
- 値が無い項目は`null`で出し、キーは省かない。読み取りアプリは「値が無い」と「読み落とし」を区別できる。例外は通常QR・詳細QRの`k`だけで、`s`がカーネル名のとき`1`、シリアルのときはキー自体を省く。
- 時刻はすべてUTC。ファイル用QRと`result.json`はISO 8601（`2025-06-01T12:00:00.123456+00:00`）、画面用は`YYYYmmddTHHMMSSZ`の16文字（`20250601T120000Z`）。
- 容量はバイト（10進）。詳細QRだけMB（10^6バイト、切り捨て）にする。150バイトに収めるためで、精度は1MB（32GBに対して0.003%）である。正確なバイト値は`result.json`とSQLiteにある。
- 速度はMB/s（10^6バイト/秒）。
- 比率（`lost_ratio`など）は小数で、小数4桁に丸める。
- 判定・ステップ状態・種別などの符号は第6節のコード表で引く。表に無い値は「不明」として扱い、生の値を残す。

## 3. ファイル用QR v:3（`result-qr.png`）

結果ディレクトリ（`<timestamp>-<serial>/`）の`result-qr.png`に入る。v:1のキーは全て残し、第2期で足した項目を加えた。`v`は`3`（`2`は通常QRが使っている）。

### 3.1 キー表

| キー | 型 | 由来（`result.json`） | v:1からの変更 |
|---|---|---|---|
| `v` | int | — | `1`→`3` |
| `verdict` | str | `verdict`（判定名の文字列。第6.1節） | — |
| `mode` | str | `mode`: `scan_only`／`full`。ホストの上限を適用した後に実際に走ったモード（第6.9節）。省略規則で削られない | 追加（検査モード切替） |
| `serial` | str | `device.serial` | — |
| `kernel_name` | str | `device.kernel_name`（`sda`など） | 追加 |
| `port_path` | str \| null | `device.port_path`（`1-1.3`など） | 追加 |
| `started_at`、`finished_at` | str | 同名 | 追加 |
| `elapsed_s` | float \| null | 同名（検査全体の所要秒数、小数2桁） | 追加 |
| `steps` | dict | `steps[].name → status`（第6.2節） | — |
| `clamav_db` | str \| null | `steps[malware].details.database.db_version` | — |
| `clamav_db_date` | str \| null | `…database.db_date`の先頭10文字（`YYYY-MM-DD`） | 追加 |
| `digest` | str | `result.json`のSHA-256（64桁） | — |
| `malware_count` | int | `len(steps[malware].details.detections)` | — |
| `signatures` | list[str] | 検知シグネチャ名の重複除去 | — |
| `advertised_over` | bool | `advertised_check.exceeded`（申告容量の非破壊チェック）。`nominal_bytes`未設定なら`false` | 追加 |
| `capacity` | dict \| null | `steps[capacity].details`の`nominal_bytes`、`advertised_bytes`、`actual_bytes`、`kind`、`lost_ratio`、`counterfeit_reasons`、`probe_verdict_line`。容量ステップが無いか、PASS／FAIL以外（SKIPPED・ERROR）なら`null`。辞書があっても個々の値が`null`のことがある | 追加 |
| `link_speed_mbps` | int \| null | `device.link_speed_mbps`（USBリンク速度、Mbps） | 追加 |
| `device_class` | str \| null | `steps[speed].details.device_class`（第6.4節のキー） | 追加 |
| `speed` | dict \| null | `read_mb_s`、`write_mb_s`、`threshold_read_mb_s`、`threshold_write_mb_s`、`basis_read`、`basis_write`、`severity`。fioの測定値が無ければ`null` | 追加 |
| `reason_codes` | list[str] | 同名（第6.8節） | 追加 |
| `reasons` | list[str] | 同名（英語の理由文。第11節を参照） | 追加 |
| `hash_inventory_digest` | str \| null | `steps[hash_inventory].details.digest`（`hash-inventory.tsv`のSHA-256、64桁） | 追加 |
| `hash_inventory_lines` | int \| null | `…details.lines`（台帳の行数） | 追加 |
| `truncated` | bool | 省略が起きたとき`true`。省略が無ければ`false`（常にキーを出す） | — |

`capacity`の中身:

| キー | 型 | 意味 |
|---|---|---|
| `nominal_bytes` | int \| null | 設定された公称容量（商品ページの値）。未設定なら`null` |
| `advertised_bytes` | int | lsblkが報告した申告容量 |
| `actual_bytes` | int \| null | 実測容量 |
| `kind` | str \| null | `ok` / `short` / `defect` / `counterfeit` / `unverified`（第6.5節） |
| `lost_ratio` | float \| null | f3readで消失したセクタの比率。f3probeモードは`null` |
| `counterfeit_reasons` | list[str] \| null | 偽装疑いの理由コード（第6.7節）。疑いが無ければ`[]` |
| `probe_verdict_line` | str \| null | f3probeの`Good news:`／`Bad news:`の行。f3writeモードは`null` |

`speed`の中身:

| キー | 型 | 意味 |
|---|---|---|
| `read_mb_s`、`write_mb_s` | float \| null | fioの実測値 |
| `threshold_read_mb_s`、`threshold_write_mb_s` | float \| null | 判定の閾値（基準値×`pass_ratio`）。判定対象外は`null` |
| `basis_read`、`basis_write` | str \| null | 基準の根拠。`nominal`（公称値）/ `class`（種別の最低値）/ `none` |
| `severity` | str \| null | FAILのときの重大度。`severe` / `minor`。FAIL以外は`null` |

### 3.2 サイズ上限と省略順序

上限は2953バイト（QRバイトモードの最大、誤り訂正L）。典型的な検査（5ステップ、理由1件、シリアル12文字）は1.2KB前後で収まる。超えるときは次の順で縮め、各段階の後に再計算して上限以下になった時点で止める。どこかで削ったら`truncated`を`true`にする。

1. `signatures`を3件に切る。
2. `reasons`の各要素を120文字に切り、切った要素の末尾に`…`を付ける。
3. `signatures`を1件ずつ0件まで減らす。
4. `reasons`を空にする（`reason_codes`は残るので理由はコードで追える）。
5. `serial`を128文字に切る。
6. `capacity.probe_verdict_line`を`null`にする。
7. それでも超えればQRを生成せずエラーにする（検査自体は`result.json`まで完了している）。

読み取りアプリは`truncated`が`true`なら「シグネチャ・理由文は一部」と表示し、全文は`result.json`で確認するよう促す。`malware_count`は削られないので検知数は常に正しい。

### 3.3 v:1との差分

- `v`が`1`から`3`になった。
- 追加キー: `kernel_name`、`port_path`、`started_at`、`finished_at`、`elapsed_s`、`clamav_db_date`、`advertised_over`、`capacity`、`link_speed_mbps`、`device_class`、`speed`、`reason_codes`、`reasons`、`hash_inventory_digest`、`hash_inventory_lines`。
- `truncated`は省略が無いときも`false`で出る（v:1は省略時だけ`true`を出した）。
- `verdict`に`Counterfeit Suspected`が増えた。
- 既存キー（`verdict`、`serial`、`steps`、`clamav_db`、`digest`、`malware_count`、`signatures`）の意味と形式は変わらない。

## 4. 通常QR v:2（画面）

frontendが画面に出す同定用のQR。150バイト以下、ASCIIのみ、誤り訂正M、QRバージョン8以下。

```json
{"v":2,"r":"I","s":"ABC123456789","t":"20250601T120000Z","d":"0123456789abcdef","st":"m:P,h:P,c:S,s:S","n":0}
```

| キー | 意味 | 値 | 最大バイト（値のみ） |
|---|---|---|---|
| `v` | 形式バージョン | `2`固定 | 1 |
| `r` | 判定 | `N`=No Defect、`M`=Malware Detected、`C`=Counterfeit Suspected、`S`=Spec Mismatch、`D`=Defective、`I`=Inconclusive。対応表に無い判定は`?` | 1 |
| `s` | シリアル | `result.json`の`device.serial`をASCII化（非ASCIIは`?`）した先頭32文字。空なら`device.kernel_name`を入れ`k`を`1`にする | 32 |
| `k` | `s`がカーネル名であることを示す | `1`。`s`がシリアルのときはキー自体を省く | 1 |
| `t` | 検査開始時刻（UTC） | `started_at`を`YYYYmmddTHHMMSSZ`に直した16文字。結果ディレクトリ名の先頭16文字と一致する | 16 |
| `d` | `result.json`のSHA-256先頭16桁 | 小文字hex | 16 |
| `st` | ステップ状態 | `名前:状態`をカンマ区切り。名前は`m`=malware、`h`=hash_inventory、`c`=capacity、`s`=speed、`u`=unmount、その他は先頭3文字。状態は`P`=pass、`F`=fail、`E`=error、`S`=skipped、`N`=not_implemented | 約30 |
| `n` | マルウェア検知数 | `steps[malware].details.detections`の要素数。無ければ`0` | 4 |

- 150バイトを超えたら`s`を16文字に切り、それでも超えたら`st`を落とす。ASCII化後の`s`は最大32バイトなので、ここに来るのは将来キーを増やした場合だけである。
- ステップ順は`result.json`の`steps`配列の順。
- `r`の`C`（Counterfeit Suspected）は第2期で加わった。これより前のfrontendは`?`を出す。

## 5. 詳細QR v:3（画面、3枚）

frontendが通常QRの次のページとして出す。お薬手帳のQR（JAHIS規格）と同じく、各枚に共通ヘッダー（版、枚目／枚数、識別子）を置き、読み取り順を問わず結合する。枚数は3で固定する（2枚では容量・速度の数値が入らない）。制約は通常QRと同じで、各枚150バイト以下、ASCIIのみ、誤り訂正M、QRバージョン8以下。

### 5.1 共通ヘッダー（全枚、先頭に置く）

| キー | 型 | 値 |
|---|---|---|
| `v` | int | `3` |
| `p` | str | `"1/3"`、`"2/3"`、`"3/3"` |
| `d` | str | `result.json`のSHA-256先頭12桁。通常QRの`d`（16桁）の先頭12桁と一致する |

### 5.2 1枚目（同定と判定）

| キー | 型 | 由来 | 最大バイト |
|---|---|---|---|
| `t` | str | 開始時刻`YYYYmmddTHHMMSSZ`（通常QRと同じ） | 16 |
| `s` | str | シリアルのASCII化、32文字（通常QRと同じ） | 32 |
| `k` | int | `s`がカーネル名なら`1`、それ以外はキーを省く | 1 |
| `r` | str | 判定コード（`N`/`M`/`C`/`S`/`D`/`I`） | 1 |
| `e` | int | `elapsed_s`を整数秒に丸めた値 | 6 |
| `m` | int | マルウェア検知数 | 4 |
| `dv` | int \| null | ClamAV DBバージョン（`db_version`を整数化。失敗なら`null`） | 6 |
| `md` | str \| null | 検査モード（第6.9節）。`s`＝`scan_only`、`f`＝`full`、`result.json`に`mode`が無ければ（切替導入前の結果）`null` | 1 |

1枚目のバイト予算: ヘッダー、32文字のシリアル、`k`、6桁の`e`、4桁の`m`、6桁の`dv`で最悪137バイトだった。`md`は`,"md":"s"`の10バイトを足すので最悪147バイトになり、`s`を切らずに上限150バイトに収まる。

### 5.3 2枚目（容量とデバイス）

| キー | 型 | 由来 | 単位・形式 |
|---|---|---|---|
| `cn` | int \| null | `advertised_check.nominal_bytes`（設定された公称容量。容量ステップがSKIPPEDでも入る） | MB（10^6バイト、切り捨て） |
| `ca` | int | `device.advertised_bytes`（申告容量） | MB |
| `cx` | int \| null | `steps[capacity].details.actual_bytes`（実測容量） | MB |
| `ck` | str | 容量の区別1文字（第6.5節）。`-`=ok、`S`=short、`D`=defect、`C`=counterfeit、`U`=unverified、`?`=ステップがPASS／FAIL以外 | 1 |
| `co` | int \| null | 申告容量の非破壊チェック。`1`=申告容量が公称×`over_ratio`を超過、`0`=超過なし、`null`=`nominal_bytes`未設定。容量ステップがSKIPPEDでも値が入る | 1 |
| `cl` | float \| null | `lost_ratio`（小数4桁） | 比率 |
| `ls` | int \| null | `device.link_speed_mbps` | Mbps |
| `dc` | str \| null | `device_class`の2文字コード（第6.4節） | 2 |
| `hn` | int \| null | `steps[hash_inventory].details.lines`（台帳の行数） | 行 |

容量超過は`ck`の`C`に含まれ、内訳は`result.json`の`counterfeit_reasons`で確認する。

### 5.4 3枚目（速度とDB、台帳）

| キー | 型 | 由来 | 単位 |
|---|---|---|---|
| `sr`、`sw` | float \| null | `steps[speed].details.read.mb_s`、`write.mb_s`（小数1桁） | MB/s |
| `tr`、`tw` | float \| null | `threshold_read_mb_s`、`threshold_write_mb_s`（小数1桁） | MB/s |
| `sb` | str | 判定根拠。`n`=nominal、`c`=class、`-`=none。読みと書きで異なるときは読みを優先し、書きは`result.json`で確認する | 1 |
| `sv` | str | 重大度。`s`=severe、`m`=minor、`-`=なし | 1 |
| `db` | str \| null | ClamAV DB日付`YYYYMMDD` | 8 |
| `hd` | str \| null | `steps[hash_inventory].details.digest`の先頭16桁 | 16 |

判定理由は別キーにしない。`ck`（容量）、`sv`（速度）、`m`（マルウェア）、通常QRの`st`（ステップ状態）で復元できる。

### 5.5 省略規則

- 1枚目が150バイトを超えたら`s`を16文字に切る（通常QRと同じ）。それでも超えればその枚だけ「QR生成失敗」になり、他の枚は表示される。
- 2枚目・3枚目は最悪値でも150バイトに収まるので省略規則を持たない。
- 値が無い項目は`null`で出す。キーを省くのは1枚目の`k`だけ。

### 5.6 結合の手順と検証

1. `v == 3`かつ`p`キーを持つQRを集める。
2. `d`が同じものを1グループにする。`d`が異なる枚は別の検査なので混ぜない。同じ検体を2回検査した場合でも`d`は`result.json`ごとに異なる。
3. グループ内で`p`の分母が3で、分子`1`・`2`・`3`が揃ったら結合する。
4. 同じ`p`を2回読んだら内容が一致することを確認し、違えば読み直しを促す。
5. 通常QR（v:2）も読んでいれば、その`d`の先頭12桁が詳細QRの`d`と一致することを確かめる。

### 5.7 読み取りUIの推奨

- 3枚読み終えたら自動で結合し、1枚ずつ「1/3 読み取り済み」のように進捗を出す。
- `d`が異なる枚を読んだら「別の検査のQRです」と警告し、現在のグループを捨てるか選ばせる。
- 通常QRと詳細QRを切り替えて読めるようにする。通常QRだけで同定して後からファイル用QRで数値を補う運用も可能である。
- 数値はコードのまま出さず、第6節の表で日本語（または英語）に直して表示する。

### 5.8 frontendの表示操作（補足）

ペイロードの仕様ではなく、frontendが詳細QRをどう見せるかの補足である。読み取りアプリの実装には影響しない。

- 通常QR（v:2）と詳細QR（v:3）の切替は、QR領域（画面右側）のタップかキー`Q`で行う。通常QRから切り替えると詳細QRの1枚目が出る。
- 詳細QR表示中は◀▶ボタンと矢印キーがページ送りになる（1/3→2/3→3/3→1/3。◀は逆順）。ポートの切替はタイルのタップかキー`1`〜`4`で行う。
- 情報ストリップには「詳細n/3」（英語表示では「Detail n/3」）と`d`（12桁）を出す。
- 画面の言語（日本語／英語）はペイロードに影響しない。`d`も言語で変わらない。
- 1枚目が省略規則（第5.5節）を適用しても150バイトに収まらないときは、その枚だけ「QR生成失敗」と表示し、他の枚はそのまま表示する。
- タイルの英語ラベルは幅（160px、18px太字）に収めるための短縮形で、`Malware Detected`は「Malware」、`Counterfeit Suspected`は「Counterfeit」、`Spec Mismatch`は「Mismatch」と出す。読み取りアプリは第6.1節のフル表記でよい（ペイロードの`r`は変わらない）。

## 6. コード表

### 6.1 判定（`r`、`verdict`、`verdict_code`）

| コード | `verdict`の文字列 | 日本語表示 | 意味 |
|---|---|---|---|
| `N` | `No Defect` | 不良なし | 全ステップPASS |
| `M` | `Malware Detected` | マルウェア検知 | ClamAVが検知。他の失敗より優先 |
| `C` | `Counterfeit Suspected` | 偽装疑い | 申告容量または実容量が公称の107%超、f3probeの`Bad news`、消失50%以上のいずれか。「疑い」であり断定ではない |
| `S` | `Spec Mismatch` | 仕様不一致 | 容量不足または速度不足 |
| `D` | `Defective` | 不良 | 容量は足りているがf3readが消失・破損・変化・上書きセクタを検知 |
| `I` | `Inconclusive` | 判定不能 | ERROR、SKIPPED、未実装のステップが残る |

優先順位は`M` > `C` > `D` > `S` > `N`で、ERRORやSKIPPEDが残れば`I`になる。

frontendへの要望（第2期）: `Counterfeit Suspected`の表示は日本語「偽装疑い」、英語表示は「Counterfeit Suspected」のまま、色はマルウェア検知の赤`#B71C1C`と不良の紫`#6A1B9A`と区別できる濃いマゼンタ系`#AD1457`を推奨する。読み取りアプリも同じ配色にすると画面と揃う。

### 6.2 ステップ状態（`st`の状態、`steps`の値、`step_*`列）

| コード | `result.json`の値 | 意味 |
|---|---|---|
| `P` | `pass` | 合格 |
| `F` | `fail` | 不合格（判定に効く） |
| `E` | `error` | 実行できなかった、またはタイムアウト（`Inconclusive`） |
| `S` | `skipped` | 実行しなかった（マルウェア検知後、`allow_destructive = false`など） |
| `N` | `not_implemented` | 未実装 |

### 6.3 ステップ名（`st`の名前、`reason_codes`の略号）

| コード | ステップ名 | 内容 |
|---|---|---|
| `m` | `malware` | ClamAVスキャン |
| `h` | `hash_inventory` | SHA-256ハッシュ台帳（`hash-inventory.tsv`） |
| `c` | `capacity` | 容量検証（f3write＋f3read、またはf3probe） |
| `s` | `speed` | 速度検証（fio） |
| `u` | `unmount` | アンマウント |

通常QRの`st`では表に無い名前は先頭3文字、`reason_codes`では先頭1文字を使う。

### 6.4 デバイス種別（`dc`、`device_class`）

| 2文字コード | `device_class` | 種別 | 最低値 読み／書き（MB/s） |
|---|---|---|---|
| `h5` | `hdd_5400` | HDD 5400rpm | 80 / 80 |
| `h7` | `hdd_7200` | HDD 7200rpm | 120 / 120 |
| `ss` | `sata_ssd` | SATA SSD | 400 / 400 |
| `ns` | `nvme_ssd` | NVMe / USB3.2 SSD | 500 / 500 |
| `s1` | `sd_c10` | SD Class10 / U1 | 25 / 10 |
| `s3` | `sd_u3` | SD U3 / V30 | 90 / 30 |
| `u2` | `usb2_flash` | USB2.0フラッシュ | 15 / 5 |
| `u3` | `usb3_flash` | USB3.0フラッシュ | 50 / 15 |

`device_class_basis`（`result.json`・SQLite）は種別の由来で、`config`（設定値）、`link_speed`（USBリンク速度から推定。480Mbpsで`usb2_flash`、5000Mbps以上で`usb3_flash`）、`unknown`のいずれか。

### 6.5 容量の区別（`ck`、`kind`、`capacity_kind`）

| `ck` | `kind` | 意味 |
|---|---|---|
| `-` | `ok` | 合格 |
| `S` | `short` | 容量不足（実容量が基準の93%未満） |
| `D` | `defect` | 容量は足りているがセクタ不良 |
| `C` | `counterfeit` | 偽装疑い（内訳は`counterfeit_reasons`） |
| `U` | `unverified` | 検証済み領域が閾値に届かない（f3writeモードでほぼ満杯の検体。ERROR） |
| `?` | — | 容量ステップがPASS／FAIL以外（SKIPPED・ERROR・未実装）で`kind`が無い |

### 6.6 申告容量の超過（`co`、`advertised_over`）

| `co` | `advertised_over` | 意味 |
|---|---|---|
| `1` | `true` | lsblkの申告容量が公称×`over_ratio`（既定1.07）を超えた |
| `0` | `false` | 超えていない |
| `null` | `false` | `nominal_bytes`が未設定で比べていない |

このチェックはマウントもスキャンもしていない検査開始直後に行い、`allow_destructive = false`でもマルウェア検知で止まっても記録される。ファイル用QRの`advertised_over`は`nominal_bytes`未設定でも`false`になるので、「未設定」を区別したいときは`capacity.nominal_bytes`（または詳細QRの`cn`）が`null`かどうかを見る。

### 6.7 速度の根拠と重大度（`sb`、`sv`）、偽装理由（`counterfeit_reasons`）

| `sb` | `basis_read`／`basis_write` | 意味 |
|---|---|---|
| `n` | `nominal` | 設定した公称速度を基準にした |
| `c` | `class` | デバイス種別の最低値を基準にした |
| `-` | `none` | 基準が無く判定対象外 |

| `sv` | `severity` | 意味 |
|---|---|---|
| `s` | `severe` | 測定値が基準値の50%未満（summaryは`Speed shortfall (severe)`で始まる） |
| `m` | `minor` | 50%以上80%未満（`Speed shortfall (minor)`） |
| `-` | `null` | FAILではない |

| `counterfeit_reasons`の値 | 意味 |
|---|---|
| `over_advertised` | 申告容量（lsblk）が公称×`over_ratio`を超えた |
| `over_actual` | 実測容量が公称×`over_ratio`を超えた |
| `probe_bad_news` | f3probeが`Bad news:`を報告した |
| `lost_ratio` | f3readの消失セクタが50%以上 |

複数が同時に立つことがある。順序は固定（`over_advertised` → `over_actual` → `probe_bad_news` → `lost_ratio`）。

### 6.8 判定理由コード（`reason_codes`）

`<ステップ略号>:<理由>`の形。順序は`result.json`の`steps`の順で、非破壊チェック由来の`c:over`は先頭に置く。

| コード | 意味 |
|---|---|
| `m:found` | マルウェア検知（malware FAIL） |
| `c:over` | 申告容量または実測容量が公称×`over_ratio`を超過（非破壊チェックと容量ステップの両方で立っても1回） |
| `c:probe` | f3probeの`Bad news` |
| `c:lost` | 消失セクタ50%以上 |
| `c:short` | 容量不足 |
| `c:defect` | セクタ不良（`Defective`） |
| `s:severe` | 速度不足、著しく下回る |
| `s:minor` | 速度不足、わずかに下回る |
| `<略号>:timeout` | そのステップがタイムアウト（`m:timeout`、`c:timeout`、`s:timeout`、`h:timeout`） |
| `<略号>:error` | そのステップがERROR（タイムアウト以外） |
| `<略号>:skipped` | そのステップがSKIPPED |
| `<略号>:not_implemented` | そのステップが未実装 |

例: `nominal_bytes`を設定し`allow_destructive = false`で申告容量が超過した検査は`["c:over", "c:skipped", "s:skipped"]`になる。

### 6.9 検査モード（`md`、`mode`）

| `md` | `mode`（`result.json`、ファイル用QR、SQLite） | 意味 |
|---|---|---|
| `s` | `scan_only` | ClamAVスキャンとハッシュ台帳だけ。容量・速度は`SKIPPED`（「Inspection mode is scan_only」）。ドライブのデータは残る |
| `f` | `full` | ホストが消去を許し、挿入時にディスプレイが「検査＋消去」だった。容量・速度が走った（または別の理由で失敗した） |
| `null` | 無し | 切替導入前に書かれた`result.json` |

モードはホストの上限を適用した後の値である（`allow_destructive = false`なら`scan_only`に強制）。`f`は破壊的ステップに到達できたことを示し、書き込んだことを保証しない。

## 7. `result.json`との対応

詳細QRのキーを基準に並べる。`…details`は`steps[]`のうち該当ステップの`details`。

| 項目 | 詳細QR v:3 | 通常QR v:2 | ファイル用QR v:3 | `result.json` | SQLite列 | 単位 |
|---|---|---|---|---|---|---|
| 形式版 | `v`=3 | `v`=2 | `v`=3 | — | — | — |
| 枚目 | `p` | — | — | — | — | — |
| ダイジェスト | `d`（12桁） | `d`（16桁） | `digest`（64桁） | （ファイルのSHA-256） | `result_digest` | hex |
| 開始時刻 | `t` | `t` | `started_at` | `started_at` | `started_at`、`started_compact` | UTC |
| 終了時刻 | — | — | `finished_at` | `finished_at` | `finished_at` | UTC |
| 所要時間 | `e` | — | `elapsed_s` | `elapsed_s` | `elapsed_s` | 秒 |
| シリアル | `s`、`k` | `s`、`k` | `serial`、`kernel_name` | `device.serial`、`device.kernel_name` | `serial`、`kernel_name` | — |
| ポートパス | — | — | `port_path` | `device.port_path` | `port_path` | — |
| 判定 | `r` | `r` | `verdict` | `verdict` | `verdict`、`verdict_code` | — |
| 検査モード | `md`（1文字） | — | `mode` | `mode` | `mode` | — |
| ステップ状態 | — | `st` | `steps` | `steps[].status` | `step_*` | — |
| マルウェア検知数 | `m` | `n` | `malware_count` | `len(steps[malware].details.detections)` | `malware_count` | 件 |
| ClamAV DB版 | `dv` | — | `clamav_db` | `…details.database.db_version` | `clamav_db_version` | — |
| ClamAV DB日付 | `db` | — | `clamav_db_date` | `…details.database.db_date` | `clamav_db_date` | 日 |
| 公称容量 | `cn` | — | `capacity.nominal_bytes` | `advertised_check.nominal_bytes`（常に入る）、`steps[capacity].details.nominal_bytes`（容量ステップが走ったとき） | `nominal_bytes` | QR: MB、他: バイト |
| 申告容量 | `ca` | — | `capacity.advertised_bytes` | `device.advertised_bytes` | `advertised_bytes` | 同上 |
| 実測容量 | `cx` | — | `capacity.actual_bytes` | `…details.actual_bytes` | `actual_bytes` | 同上 |
| 容量の区別 | `ck` | — | `capacity.kind` | `…details.kind` | `capacity_kind` | — |
| 申告容量の超過（非破壊） | `co` | — | `advertised_over` | `advertised_check.exceeded` | `advertised_over` | 0/1 |
| 消失比率 | `cl` | — | `capacity.lost_ratio` | `…details.lost_ratio` | `lost_ratio` | 比率 |
| 偽装理由 | （`ck`=`C`） | — | `capacity.counterfeit_reasons` | `…details.counterfeit_reasons` | `counterfeit_reasons` | — |
| f3probeの判定行 | — | — | `capacity.probe_verdict_line` | `…details.probe_verdict_line` | `probe_verdict_line` | — |
| リンク速度 | `ls` | — | `link_speed_mbps` | `device.link_speed_mbps` | `link_speed_mbps` | Mbps |
| デバイス種別 | `dc`（2文字） | — | `device_class` | `steps[speed].details.device_class` | `device_class` | — |
| 速度 読み／書き | `sr`、`sw` | — | `speed.read_mb_s`、`write_mb_s` | `…details.read.mb_s`、`write.mb_s` | `read_mb_s`、`write_mb_s` | MB/s |
| 閾値 読み／書き | `tr`、`tw` | — | `speed.threshold_*_mb_s` | `…details.threshold_*_mb_s` | `threshold_*_mb_s` | MB/s |
| 判定根拠 | `sb`（読みを優先） | — | `speed.basis_read`、`basis_write` | `…details.basis_read`、`basis_write` | `speed_basis_read`、`speed_basis_write` | — |
| 重大度 | `sv` | — | `speed.severity` | `…details.severity` | `speed_severity` | — |
| 判定理由コード | （`ck`、`sv`、`m`） | — | `reason_codes` | `reason_codes` | `reason_codes` | — |
| 判定理由（文） | — | — | `reasons` | `reasons` | `reasons` | — |
| 台帳ダイジェスト | `hd`（16桁） | — | `hash_inventory_digest`（64桁） | `steps[hash_inventory].details.digest` | `hash_inventory_digest` | hex |
| 台帳行数 | `hn` | — | `hash_inventory_lines` | `…details.lines` | `hash_inventory_lines` | 行 |
| 結果ディレクトリ | — | — | — | `output_dir` | `output_dir`、`inspection_id` | — |
| 状態 | — | — | — | — | `state` | — |

## 8. SQLite（Pi側）との対応

backendは検査1件につき`/var/lib/usb-sentinel/db/inspections.sqlite`の`inspections`テーブルへ1行を書く。開始時に`state = 'started'`で挿入し、`result.json`を書いた後に全列を埋めて`state = 'completed'`に更新する。`result.json`が正本で、DBは一覧・検索のための索引である。スキーマ版は`PRAGMA user_version`（現在`2`）で管理し、以降の版では列の追加だけを行う（削除・改名はしない）。版2は`ALTER TABLE inspections ADD COLUMN mode TEXT`で`mode`列を足す。移行は`BEGIN IMMEDIATE`の中で行い、2つのコンテナが同時にDBを開いても二重に適用されない。

### 8.1 列

| 列 | 型 | 由来 |
|---|---|---|
| `inspection_id` | TEXT 主キー | 結果ディレクトリ名（`<timestamp>-<serial>`） |
| `state` | TEXT | `started` / `completed` |
| `verdict`、`verdict_code` | TEXT | 判定名と1文字コード（完了時に入る。開始行はNULL） |
| `mode` | TEXT | `scan_only`／`full`（第6.9節）。開始行の時点で入る。`user_version` 2より前に書かれた行はNULL |
| `serial`、`kernel_name`、`port_path`、`vendor`、`model` | TEXT | `result.json`の`device.*` |
| `started_at` | TEXT | ISO 8601（UTC） |
| `started_compact` | TEXT | `YYYYmmddTHHMMSSZ`（通常QR・詳細QRの`t`と同じ） |
| `finished_at` | TEXT | ISO 8601。開始行はNULL |
| `elapsed_s` | REAL | 所要秒数 |
| `result_digest` | TEXT | `result.json`のSHA-256（64桁） |
| `step_malware`、`step_hash_inventory`、`step_capacity`、`step_speed`、`step_unmount` | TEXT | 各ステップの状態（`pass`など）。無いステップはNULL |
| `malware_count` | INTEGER | 検知数 |
| `clamav_db_version`、`clamav_db_date` | TEXT | DB版と日付（`YYYY-MM-DD`） |
| `clamav_scanned_files` | INTEGER | clamscan集計行の`Scanned files` |
| `clamav_scan_time_s` | REAL | clamscan集計行の`Time` |
| `nominal_bytes`、`advertised_bytes`、`actual_bytes` | INTEGER | 公称（`advertised_check.nominal_bytes`）・申告・実測（バイト） |
| `advertised_over` | INTEGER | 非破壊チェックの超過（1/0） |
| `capacity_kind` | TEXT | `ok` / `short` / `defect` / `counterfeit` / `unverified` |
| `counterfeit_reasons` | TEXT | JSON配列。非破壊チェックの`over_advertised`と容量ステップの理由を重複なしで和集合にしたもの |
| `lost_ratio` | REAL | 消失比率 |
| `probe_verdict_line` | TEXT | f3probeの判定行 |
| `link_speed_mbps` | INTEGER | USBリンク速度 |
| `device_class`、`device_class_basis` | TEXT | 種別とその由来 |
| `read_mb_s`、`write_mb_s` | REAL | 実測速度 |
| `threshold_read_mb_s`、`threshold_write_mb_s` | REAL | 閾値 |
| `speed_basis_read`、`speed_basis_write` | TEXT | 判定根拠 |
| `speed_severity` | TEXT | `severe` / `minor` |
| `reason_codes`、`reasons` | TEXT | JSON配列（`reasons`は非ASCIIの文字をエスケープしない） |
| `hash_inventory_digest` | TEXT | 台帳のSHA-256（64桁） |
| `hash_inventory_lines` | INTEGER | 台帳の行数 |
| `output_dir` | TEXT | 結果ディレクトリの絶対パス |
| `warnings` | TEXT | JSON配列。各ステップの警告をステップ順に連結し、検査全体の警告を続けたもの |
| `created_at`、`updated_at` | TEXT | 行の作成・更新時刻（UTC） |

索引は`started_at`、`(serial, started_at)`、`(port_path, started_at)`、`state`、`result_digest`に張ってある。

### 8.2 読み取りのクエリ例

frontendや他の読み取り側は読み取り専用で開く（`sqlite3.connect("file:...?mode=ro", uri=True)`、`PRAGMA query_only = ON`、`busy_timeout = 1000`）。

```sql
-- ポートごとの最新（接続時刻以降）
SELECT inspection_id, state, verdict, serial, kernel_name, port_path, started_at,
       finished_at, step_malware, step_hash_inventory, step_capacity, step_speed,
       malware_count, advertised_over, result_digest, output_dir
FROM inspections
WHERE port_path = ? AND started_at >= ?
ORDER BY started_at DESC LIMIT 1;

-- 開始したが未完了（USB切断の疑い）
SELECT inspection_id, serial, port_path, started_at
FROM inspections WHERE state = 'started' AND started_at < ?;

-- 同一シリアルの履歴
SELECT started_at, verdict, capacity_kind, speed_severity
FROM inspections WHERE serial = ? ORDER BY started_at DESC;

-- QRのダイジェスト（先頭12桁以上）から検査を探す
SELECT inspection_id, verdict, serial, started_at, output_dir
FROM inspections WHERE result_digest LIKE ? || '%';
```

`state = 'started'`の行は検査中か、`result.json`を書けずに終わった（USB切断など）ことを示す。`result.json`が存在するのに`started`のままなら完了記録に失敗しており、journalに`SQLite record failed (completion)`が残っている。

## 9. 読み取りアプリ（Flutter）側のスキーマ推奨

Pi側の列（第8.1節）をそのまま持ち、ケース情報を列として足す。Pi側はケース情報を一切持たないので、商品との結び付けは読み取りアプリ側で完結する。

- 主キーは`result_digest`。ファイル用QRなら64桁、通常QRなら16桁、詳細QRなら12桁が得られる。先頭12桁を主キーにしてよい（1万件で偶然一致する確率は約2×10^-7）。長い桁を後から読んだら上書きして伸ばす。
- `started_compact`（16桁）と`serial`を補助キーにし、ダイジェストが読めなかった場合の突き合わせに使う。
- `nominal_bytes`は2列に分ける。`nominal_bytes_pi`（Piの`config.toml`で設定した値。QRの`cn`／`capacity.nominal_bytes`）と`nominal_bytes_product`（読み取りアプリで入力した商品ページの値）。Piの設定はホストで1つなので、同時に検査した別商品には合わない値が入っていることがある。
- 足すケース情報の例: 商品ID、ブランド、商品名、価格、商品ページURL、調査員、写真ファイル名、入力日時、備考。
- QRごとの生文字列（`raw_file_qr`、`raw_normal_qr`、`raw_detail_qr_1`〜`3`）を保存しておくと、後で形式が変わっても再解釈できる。
- 詳細QRの数値はMB単位なので、Pi側の列（バイト）と合わせる場合は×10^6して保存するか、別列（`*_mb`）にする。

## 10. 検証手順

持ち出した結果が改変されていないことを確かめる。

1. `result.json`の検証。ネットワーク接続PCで`sha256sum result.json`を取り、ファイル用QRの`digest`（64桁）と比べる。通常QRなら`sha256sum result.json | cut -c1-16`と`d`、詳細QRなら`cut -c1-12`と`d`を比べる。`result.json`はbackendが書いたバイト列をそのままハッシュしているので、再整形せずファイルのまま計算する。
2. `hash-inventory.tsv`の検証。`sha256sum hash-inventory.tsv`と、ファイル用QRの`hash_inventory_digest`（64桁）または詳細QR 3枚目の`hd`（先頭16桁）を比べる。行数は`wc -l`と`hash_inventory_lines`／`hn`で確かめる。
3. 1と2が一致すれば、`result.json`の中の値（検知数、容量、速度）はQRに書かれた時点のものと同じである。一致しなければファイルの差し替えか読み取りミスを疑い、QRを読み直す。
4. ファイル用QRの`truncated`が`true`なら、シグネチャ・理由文の全文は`result.json`で確認する。

## 11. 変更履歴と互換性

| 版 | 対象 | 変更 |
|---|---|---|
| ファイル用QR v:1 | `result-qr.png` | 初版。`verdict`、`serial`、`steps`、`clamav_db`、`digest`、`malware_count`、`signatures`、省略時のみ`truncated` |
| 通常QR v:2 | 画面 | 初版。`r`は`N`/`M`/`S`/`D`/`I` |
| ファイル用QR v:3 | `result-qr.png` | 第2期。第3.3節の追加キー。`truncated`を常に出す。`verdict`に`Counterfeit Suspected` |
| 通常QR v:2 | 画面 | 第2期。`r`に`C`（Counterfeit Suspected）を追加。形式は不変 |
| 詳細QR v:3 | 画面 | 第2期で新設。3枚固定 |
| SQLite `user_version` 1 | `inspections.sqlite` | 第2期で新設 |
| ファイル用QR v:3 | `result-qr.png` | 検査モード切替。`verdict`の後に`mode`（`scan_only`／`full`）を追加。`v`は`3`のまま |
| 詳細QR v:3 1枚目 | 画面 | 検査モード切替。`dv`の後に`md`（`s`／`f`／`null`）を追加。最悪147バイト |
| SQLite `user_version` 2 | `inspections.sqlite` | 検査モード切替。`mode`列。backendとfrontendは同時に配備する。古いfrontendは`user_version` 2を拒否して結果ディレクトリの走査にフォールバックする |

- 読み取りアプリは`v`で分岐し、v:1のファイル用QRも読めるようにしておく（第2期より前の結果ディレクトリが残っている）。v:1には`truncated`キーが無いことがあるので、無ければ`false`と解釈する。
- ファイル用QR v:3は、将来キーを足すときも既存キーの意味を変えない。読み取りアプリは未知のキーを無視し、既知のキーが無ければ`null`扱いにする。
- 判定名の文字列（`Counterfeit Suspected`など）はbackendの`Verdict`の値で、変更時は`backend/AGENTS.md`に従って報告される。読み取りアプリは第6.1節の表に無い値を「不明」として扱い、生の値を残す。
- SQLiteは以降の版で列の追加だけを行う。読み取り側は列名で参照し、列の位置に依存しない。
- backendは`summary`、`reasons`、`warnings`、`result.txt`の見出しを英語で書くようになった。キー、コード、判定名は変わらず、QRの版も変えない。この変更より前の結果（SQLiteの過去の行や、持ち出し済みの`result.json`）は日本語のまま残る。読み取りアプリは表示に`reason_codes`を主に使い、文は補足として扱う。
