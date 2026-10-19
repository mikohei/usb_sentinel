[日本語版](README.ja.md)

# USB Sentinel backend

The Python package that inspects USB drives, and its Docker image.
For the inspection flow and the setup on Raspberry Pi 4 / Ubuntu 26, see the
[project README](../README.md).

## Development

Run everything in this directory.

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run ty check
uv run python -m usb_sentinel --help
```

`uv run python -m usb_sentinel selftest` checks the required commands and the ClamAV
signature database. On a macOS development machine the Linux-only commands are
missing, so it exits with code 3.

## Docker

Build from the repository root.

```bash
docker build -t usb-sentinel:local backend
```

The host-side setup for automatic USB inspection lives in `deploy/` and `scripts/`
at the repository root. On an Ubuntu host, register it from the root with
`sudo bash scripts/install-docker.sh`.
