# AGENTS.md（frontend）

`frontend/`はUSB Sentinelの検査結果を3.5インチタッチディスプレイに表示するPythonパッケージ`usb_sentinel_display`を置くディレクトリである。コードを変更するエージェントは、ルートの`AGENTS.md`に加えて本ファイルの規約に従う。`backend/`とは独立したパッケージで、`backend/`のコードをimportしない。

## 作業の前提

- コマンドはすべて`frontend/`で実行する。ルートからは動かない。
- Pythonは3.13。パッケージ管理はuvを使い、`pip`や`venv`を直接使わない。
- 依存を追加する前に、所属組織のパッケージ利用承認の手続きに従って確認する。追加後は`uv lock`で`uv.lock`を更新し、`uv lock --check`が通ることを確かめる。現在の外部依存は`qrcode`だけで、`[pil]` extra（Pillow）は入れない。
- 実行環境はLinux（sysfs、systemd）だが、開発とテストはmacOSで完結させる。sysfsは`tmp_path`に作る偽ツリー、`systemctl`は`FakeRunner`で差し替える。

## 変更後に必ず実行する検証

```bash
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
uv run python -m usb_sentinel_display --help
```

すべて終了コード0で終わること。1つでも失敗したままにしない。

- 型チェッカーはtyである。mypyは使わない。設定は`pyproject.toml`の`[tool.ty.rules]`にある。
- tyは警告が1件あるだけで終了コード1を返す。警告も放置しない。
- `ruff format`は変更したファイルだけに適用する。無関係なファイルは整形しない。

## コーディング規約

- PEP 8に従い、ruffの設定（`line-length = 100`）を守る。
- すべての関数に型ヒントを付ける。ruffの`ANN`ルールが未注釈を検出する。
- コメントとdocstringは英語で書く。UIに出す文言は`ui/i18n.py`に日本語と英語の両方で持つ。実行時に出力する文字列（`summary`、`reasons`、例外メッセージなど）は既存の言語のまま変えない。
- `except:`（bare except）は使わない。具体的な例外クラスを指定する。
- パッケージ側（`usb_sentinel_display/`）で`assert`を使わない。ruffの`S101`が検出する。テストと`scripts/`では許可している。
- 型チェッカーの指摘は、まず型注釈を直して解消する。抑制コメントを使う場合は、その行だけに`# ty: ignore[<ルール名>]`を付け、理由を日本語で添える。ルール指定なしの`# ty: ignore`、ルールの全体無効化、ファイル除外で指摘を隠さない。
- 1ファイルは300行以内を目安にする。
- UIの文言は`ui/i18n.py`にja／enの2組で持ち、GUI層はキーで参照する。`libs/`直下に日本語のUI文言リテラルを置かない。QRペイロード（`qr_payload.py`）は言語に依存せず、`i18n.py`をimportしない。

## 層の分離と安全性に関わるコード

このツールはマルウェアの疑いがあるUSBドライブの情報を扱う。次の規則を守り、変更時はテストで挙動を確かめて報告に明記する。

- `libs/ui/`だけが`tkinter`をimportする。`libs/`直下のモジュールは`tkinter`をimportしない。ロジック層はmacOSでもCIでも`pytest`で動く。
- 外部コマンド（`systemctl`）は`libs/units.py`の`Runner`に集約する。argvはリストで固定し、文字列の結合でコマンドを組まない。
- USB由来の文字列（シリアル、カーネル名、`result.json`の`vendor`／`model`）を信頼しない。パス結合やargvに直接使わない。表示には`sysfs.sanitize_text()`を通した値だけを使う。
- カーネル名は`re.fullmatch(r"sd[a-z]{1,3}", name)`を通ったものだけ扱う。`sysfs.py`で弾き、`units.unit_name()`でも再検証する。
- `result.json`は読み取り専用で開く。結果ディレクトリに書き込まない。
- `store.py`はSQLiteを`mode=ro`のURIと`PRAGMA query_only = ON`で開き、SELECT以外のSQLを持たない。`inspection_id`は`parse_dir_name()`を通ったものだけを結果ルートと結合し、`output_dir`列は使わない。DBは索引で正本は`result.json`とし、SHA-256が`result_digest`と合わなければ「結果破損」にする。DBが無い・開けない・スキーマ版が新しいときは結果ディレクトリの走査にフォールバックする。
- `ui/i18n.py`と`ui/strip.py`は`tkinter`をimportしない。`libs/`直下から`ui/`をimportしてよいのは`cli.py`が`ui/i18n.py`を読む1箇所だけ。

## 構成

```text
usb_sentinel_display/
  __main__.py            python -m usb_sentinel_displayのエントリポイント
  libs/
    __init__.py          公開API（main）
    cli.py               run / calibrateの引数解析
    config.py            display.tomlの読み込みと検証
    models.py            SlotState、AttachedDisk、ResultSummary、ResultRecord、Slot、対応表
    sysfs.py             /sys/blockの列挙、ポートパス・シリアル・接続時刻の抽出、sanitize_text()
    result_file.py       結果ディレクトリ名の解析、時刻の変換、result.jsonの要約（parse_summary）
    results.py           ResultStore（結果ディレクトリの走査とSQLite索引の突き合わせ）。result_file.pyの名前を再エクスポート
    result_detail.py     result.jsonのsteps[*].detailsからResultDetail（詳細QRの材料）を組む
    store.py             inspections.sqliteの読み取り専用索引SqliteIndex（SELECTのみ、mode=ro＋query_only）
    units.py             systemctl showの1回呼び出しと出力解析、Runner
    mapper.py            ディスク・結果・ユニット状態からSlot4つを組む。started行の結び付け
    qr_payload.py        画面用QRペイロード。通常QR（v2）と詳細QR（v3、3枚）の生成と上限処理
    qr_render.py         qrcodeの行列からPPMバイト列を作る
    monitor.py           tick本体（列挙→結果→systemctl→mapper）、例外規則、情報ストリップの通知キー
    ui/                  tkinterに依存する描画層。ここだけがtkinterをimportする
      theme.py           寸法定数、判定色、フォント解決、行構成（2行／3行）の判定
      i18n.py            ja／enの文言と判定ラベル、言語の記録（XDG状態ディレクトリ）。tkinterを読まない
      strip.py           情報ストリップの文言を組む純関数。tkinterを読まない
      tiles.py           タイル4つと◀／言語／▶のCanvas描画、タップ位置の判定、表示文字列の純関数
      qr_view.py         QRキャンバス（PhotoImage、通常QR／詳細QR 1〜3）と情報ストリップ
      keys.py            キーバインド（1〜4、矢印、F5、L、Q、kioskでないときだけCtrl+Q）
      app.py             tkルート、全画面／ウィンドウ、解像度チェック、SIGTERM、tickの結線、run_app()
tests/                   偽sysfsツリーとFakeRunnerで外部依存を差し替えたテスト
  conftest.py            fake_sysfsフィクスチャ、FakeRunner、make_result()／sample_result()／phase2_result()
  test_i18n.py           ja／enのキー集合の一致、判定ラベル、言語の記録と優先順
  test_store.py          SqliteIndex。backendのスキーマv1を文字列でコピーし、権限なし・壊れたファイル・版違いの理由キーを確かめる
  test_store_readonly.py SqliteIndexが読み取り専用である証明（バイト列不変、mode=ro、query_only）
  test_gui_smoke.py      @pytest.mark.gui。QrView／TilesView／キー束縛。tk.Tk()が作れない環境では自動skip
  test_gui_app.py        @pytest.mark.gui。Appのtick結線・QRモード・言語切替。材料はtest_gui_smokeから借りる
  （300行を超えたテストはtest_collect.py／test_result_detail.py／test_qr_detail.py／test_qr_detail_limits.py／test_mapper_update.py／test_monitor_flow.pyに分け、材料は元のファイルからimportする）
scripts/                 開発用スクリプト（パッケージには含めない。assertを許可）
  make-fixtures.py       偽sysfsツリーとサンプル結果、display.tomlを出力先に作る
  dev-screenshot.py      Appをウィンドウで起動しscreencaptureで480x320を撮る（macOS用）
deploy/
  usb-sentinel-display.desktop   XDG autostart用。--offline --frozen --no-syncと--kiosk付き
  display.example.toml           設定の全キーと日本語コメント
```

- 新しいモジュールは`libs/`配下に置く。`usb_sentinel_display/`直下に増やさない。
- `scripts/`は開発機でだけ動かす。本番のPiでは使わない。

## テスト

- 新機能はテストを先に書く（Red → Green → Refactor）。
- テストは`tests/`に置き、ファイル名は`test_`で始める。
- `systemctl`は`tests/conftest.py`の`FakeRunner`で差し替える。実際の`systemctl`をテストから呼ばない。
- sysfsと結果ディレクトリは`tmp_path`に作る。`tests/fixtures/`に結果ディレクトリを置かない（ルートの`.gitignore`が`results/`を無視する）。
- `tk.Tk()`を必要とするテストは`@pytest.mark.gui`を付け、`TclError`なら`pytest.skip`する。
- 境界値と異常系（権限なし、ディレクトリ不在、切れたJSON、タイムアウト）のテストを含める。
- SQLiteのテストは`backend/`をimportせず、`tests/test_store.py`の`CREATE_TABLE_V1`（backendの`schema.py` v1の文字列コピー）で`tmp_path`にDBを作る。backendがスキーマ版を上げたらこのコピーと`EXPECTED_SCHEMA_VERSION`も更新する。

## やらないこと

- コミット、プッシュ、ブランチ作成は、ユーザーの明示的な指示があるときだけ行う。
- `rm`などの削除コマンドは、対象と影響を説明して確認を取ってから実行する。
- `backend/`のコードを変更しない。
