# AGENTS.md（backend）

`backend/`はUSBドライブ検査のPythonパッケージ`usb_sentinel`と、そのDockerイメージの定義を置くディレクトリである。コードを変更するエージェントは、ルートの`AGENTS.md`に加えて本ファイルの規約に従う。

## 作業の前提

- コマンドはすべて`backend/`で実行する。ルートからは動かない。
- Pythonは3.13。パッケージ管理はuvを使い、`pip`や`venv`を直接使わない。
- 依存を追加する前に、所属組織のパッケージ利用承認の手続きに従って確認する。追加後は`uv lock`で`uv.lock`を更新し、`uv lock --check`が通ることを確かめる。
- 検査対象はLinux専用の挙動（udev、mount、clamscan、f3、fio）だが、開発とテストはmacOSで完結させる。外部コマンドは`libs/runner.py`の`CommandRunner`に集約し、テストでは`FakeRunner`で差し替える。

## 変更後に必ず実行する検証

```bash
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
uv run python -m usb_sentinel --help
```

すべて終了コード0で終わること。1つでも失敗したままにしない。

- 型チェッカーはtyである。mypyは使わない。設定は`pyproject.toml`の`[tool.ty.rules]`にある。
- tyは警告が1件あるだけで終了コード1を返す。警告も放置しない。
- `ruff format`は変更したファイルだけに適用する。無関係なファイルは整形しない。

## コーディング規約

- PEP 8に従い、ruffの設定（`line-length = 100`）を守る。
- すべての関数に型ヒントを付ける。ruffの`ANN`ルールが未注釈を検出する。
- コメントとdocstringは日本語で書く。英単語・数字と日本語の間にスペースを入れない。
- `except:`（bare except）は使わない。具体的な例外クラスを指定する。
- パッケージ側（`usb_sentinel/`）で`assert`を使わない。ruffの`S101`が検出する。テストでは許可している。
- 型チェッカーの指摘は、まず型注釈を直して解消する。抑制コメントを使う場合は、その行だけに`# ty: ignore[<ルール名>]`を付け、理由を日本語で添える。ルール指定なしの`# ty: ignore`、ルールの全体無効化、ファイル除外で指摘を隠さない。
- 1ファイルは300行以内を目安にする。

## 安全性に関わるコード

このツールはマルウェアの疑いがあるUSBドライブを扱う。次の箇所を変更するときは、挙動が変わらないことをテストで確かめ、変更内容を報告に明記する。

- `libs/runner.py`: subprocessの呼び出し。argvはリストで固定し、文字列の結合でコマンドを組まない。
- `libs/mounter.py`: マウントは`ro,noexec,nosuid,nodev`で行う。このオプションを外さない。
- `libs/device.py`: USBバス経由・リムーバブル・ルートディスクでない・容量が非ゼロ、を満たさないデバイスを拒否する安全ガード。条件を緩めない。
- `libs/orchestrator.py`: マルウェア検知時は破壊的検査に進まない（ハッシュ台帳はclamscan実行後なら作る。clamscanが実行されていなければ台帳も作らない）。破壊的検査（f3、fio）に進む条件`permitted`の式を緩めない。
- `libs/hashing.py`: `ro,noexec,nosuid,nodev`マウントの内側で、clamscanがスキャンを実行した後にのみ動く。バイト列を読むだけで内容を解釈しない。時間上限の`Budget`を外さない。
- `libs/store/`: SQLiteへの記録は検査の成否に影響させない。例外を外へ出さず、理由文字列を返して検査を続ける。
- `libs/steps/capacity_fs.py`: rwマウントは`noexec,nosuid,nodev`を外さない。`-w`は容量検査専用で、`mounter.py`に持ち込まない。mount成功後は例外経路でも`.h2w`削除→umountの順で必ず後片付けする。`mount -w`と`umount`に`timeout_s`を渡さないのは`mounter.py`と同方針の意図的な判断で、タイムアウトを付けるなら`timed_out`をマウント状態不明として扱い必ずumountを試みる形にしないとカーネル側で成功したmountが残る。
- `libs/steps/speed.py`: `run()`冒頭の`is_mounted`確認は自衛であり、orchestratorのゲートの代替ではない。読み取りfioの`--readonly`を外さない。

## 構成

```text
usb_sentinel/
  __main__.py            python -m usb_sentinelのエントリポイント
  libs/
    __init__.py          公開API
    cli.py               inspect / selftest、終了コード定義
    config.py            /etc/usb-sentinel/config.tomlの読み込み
    models.py            デバイス情報・ステップ結果・判定（Verdict）
    runner.py            外部コマンド実行とFakeRunner
    device.py            デバイス識別と安全ガード
    mounter.py           read-onlyマウント
    hashing.py           SHA-256ハッシュ台帳
    orchestrator.py      検査シーケンスと判定分類
    speed_floor.py       デバイス種別ごとの業界最低速度（steps/の外。config.pyから参照するため
                         標準ライブラリ以外をimportしない）
    steps/               malware（ClamAV）、capacity（f3write+f3read / f3probe）、speed（fio）、
                         common（Budget・権限判定・lsblk問い合わせ・コマンド実行と失敗文言）、
                         capacity_fs（rwマウント・f3パーサ）、capacity_write（f3writeモードの実行）、
                         capacity_probe（f3probe）、
                         capacity_judge（±7%帯・偽装疑い・申告容量の非破壊チェック）、
                         speed_fio（fioのargvとパーサ）、speed_judge（参照値・重大度・警告文）
    report/              result.json、result.txt、result-qr.png（v:3）
    store/               SQLiteへの記録（schema、sqlite_row、sqlite_store、NullStore）
tests/                   FakeRunnerで外部コマンドをモックしたテスト
                         （300行を超えたテストはtest_report_v3.py／test_report_qr_shrink.py／
                         test_report_text_v3.py／test_speed_reference.py／test_speed_errors.py／
                         test_orchestrator_destructive.py／test_orchestrator_wiring.py／
                         test_store_record.py／test_store_row.py／test_reason_codes.py／
                         test_malware_step.pyに分け、材料は元のファイルからimportする）
```

- 新しいモジュールは`libs/`配下に置く。`usb_sentinel/`直下に増やさない。
- 判定名（`Verdict`）の値は`result.json`とQRコードに出力される。変更すると外部ツールとの互換性が壊れるため、変更時は報告に明記する。第2期で`Counterfeit Suspected`（容量偽装の疑い。公称の107%超・f3probeの`Bad news`・消失50%以上）を追加した。frontendの対応表に無い値は`?`／「判定不明」表示になるため、frontendと同時に配備する。

## テスト

- 新機能はテストを先に書く（Red → Green → Refactor）。
- テストは`tests/`に置き、ファイル名は`test_`で始める。
- 外部コマンドは`FakeRunner`で差し替える。実際の`mount`や`clamscan`をテストから呼ばない。
- 境界値と異常系（タイムアウト、終了コードが0/1以外、ファイルシステム不在）のテストを含める。

## Docker

- Dockerfileは`uv sync --frozen --no-dev`で同期する。開発用の依存（ruff、ty、pytest）はイメージに入らない。
- `uv.lock`を更新したら、aarch64（Raspberry Pi 4）向けのwheelが含まれているか確認する。
- ビルドはリポジトリのルートから`docker build -t usb-sentinel:local backend`で行う。

## やらないこと

- コミット、プッシュ、ブランチ作成は、ユーザーの明示的な指示があるときだけ行う。
- `rm`などの削除コマンドは、対象と影響を説明して確認を取ってから実行する。
