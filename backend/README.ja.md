[English](README.md)

# USB Sentinel バックエンド

USB ドライブの検査を実行する Python パッケージと、その Docker イメージ。
検査フローと Raspberry Pi 4 / Ubuntu 26系への導入手順は
[プロジェクトの README](../README.ja.md) を参照。

## 開発

このディレクトリで実行する。

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run ty check
uv run python -m usb_sentinel --help
```

`uv run python -m usb_sentinel selftest` で依存コマンドと ClamAV 定義 DB を確認できる。
macOS 開発環境では Linux 用の依存コマンドが不足するため、終了コード 3 になる。

## Docker

リポジトリのルートからビルドする。

```bash
docker build -t usb-sentinel:local backend
```

USB 自動検査のホスト側設定はルートの `deploy/` と `scripts/` にある。
Ubuntu ホストでは、ルートから `sudo bash scripts/install-docker.sh` で登録する。
