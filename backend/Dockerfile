FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       clamav clamav-freshclam f3 fio util-linux udev \
       dosfstools exfatprogs ntfs-3g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md uv.lock ./
COPY usb_sentinel ./usb_sentinel
# Install only runtime dependencies using the committed lock.
RUN python -m pip install --no-cache-dir uv==0.11.7 \
    && uv sync --frozen --no-dev --no-editable \
    && mkdir -p /run/usb-sentinel/mnt /var/lib/usb-sentinel/results

ENTRYPOINT ["/app/.venv/bin/python", "-m", "usb_sentinel"]
CMD ["selftest"]
