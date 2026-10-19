[日本語版](README.ja.md)

# usb_sentinel

An inspection tool for Linux that runs automatically when you plug in a USB drive.

Previously, inspectors ran H2testw, CrystalDiskMark, BitDefender, and similar tools by hand on a Windows PC, and each sample took several hours. This tool runs a command-based automatic inspection on a Raspberry Pi 4 (Ubuntu) with no network connection, and inspects four ports at the same time. The inspector only has to plug in the USB drive.

| Inspection item | Tool used |
|---|---|
| Malware | ClamAV |
| Capacity | f3write / f3read / f3probe |
| Speed | fio |
| OS | Ubuntu Linux |

## Inspection sequence

```
Step 0  Detect USB insertion (host udev → systemd → Python backend in Docker)
Step 1  ★ Malware scan (ClamAV) ← run first to ensure safety
          → Even on a detection, the Step 2 inventory is still created. Step 4 and later are SKIPPED.
Step 2  Create the hash inventory (SHA-256 → hash-inventory.tsv)
Step 3  Unmount (prerequisite for the destructive inspections that follow)
Step 4  Capacity verification (f3write + f3read / f3probe)
Step 5  Speed verification (fio)
Step 6  Write results (result.json / result.txt / result-qr.png), record one row in SQLite
Step 7  Classify the verdict (No Defect / Counterfeit Suspected / Spec Mismatch / Defective / Malware Detected / Inconclusive)
```

### Safety design

- ClamAV runs first. Once ClamAV has run the scan, the hash inventory is created regardless of the result (detection, error, or timeout), as evidence for later comparison. If ClamAV has not opened any file (clamscan is missing, or there is no definition DB), no inventory is created either. The tool never proceeds to the destructive inspections (f3 / fio). An inventory created after a timeout or a scan failure (exit code 2) only reads the bytes of files that ClamAV did not open, under `ro,noexec,nosuid,nodev`; it neither executes nor interprets them.
- Read-only mount. The mount for scanning and hash computation uses `ro,noexec,nosuid,nodev`, leaving no room for an infected file to run on the host.
- Destructive inspections are blocked in several ways. No write is made if any of the following holds: malware is FAIL or ERROR, `allow_destructive = false`, or a mount remains.
- "No detection" is not confused with "clean". A clamscan exit code other than 0/1, a timeout, a mix of unreadable files, and a missing filesystem are all treated as ERROR. While any step remains unimplemented, the verdict is never `No Defect`.
- Safety guard. A device that does not satisfy all of the following is refused before the inspection starts (exit code 2): it is on the USB bus, it is removable, it is not the root disk, and its capacity is non-zero.

## Layout

```
backend/
  usb_sentinel/
    __main__.py            Entry point for python -m usb_sentinel
    libs/
      __init__.py          Public API
      cli.py               inspect / selftest, exit code definitions
      config.py            Loads /etc/usb-sentinel/config.toml
      models.py            Device information, step results, verdicts
      runner.py            External command execution and FakeRunner
      device.py            Device identification and the safety guard
      mounter.py           Read-only mount
      hashing.py           SHA-256 hash inventory
      orchestrator.py      Inspection sequence and verdict classification
      speed_floor.py       Industry minimum speed per device class (outside steps/; referenced by config.py)
      steps/
        malware.py         ClamAV scan
        capacity.py        Entry point of capacity verification (f3write + f3read / f3probe) and verdict writing
        capacity_write.py  f3write mode of capacity verification (rw mount, then f3write → f3read)
        capacity_fs.py     rw mount for capacity verification and the f3 parser
        capacity_probe.py  f3probe mode
        capacity_judge.py  Capacity verdict (±7% band, counterfeit suspicion, non-destructive check of the advertised capacity)
        speed.py           Entry point and verdict of speed verification (fio)
        speed_fio.py       Builds the fio argv and parses its JSON output
        speed_judge.py     Speed reference values, severity, and warning messages
        common.py          Parts shared by both steps (remaining time, permission checks, lsblk queries, command execution and failure messages)
      report/
        json_report.py     result.json and the SHA-256 digest
        text.py            result.txt / console output
        qr.py              result-qr.png (v:3)
      store/
        schema.py          SQLite schema and migrations
        sqlite_row.py      Builds one inspections row from the same values as result.json
        sqlite_store.py    Records one row per inspection (start and completion)
  tests/                   Tests with external commands mocked
  pyproject.toml           Python dependencies and development tool settings
  uv.lock                  Python dependency lock file
  .python-version          Python version
  Dockerfile               Runtime environment with Python, ClamAV, f3, and fio
  .dockerignore            Docker build exclusions
  README.md                Backend development instructions
frontend/
  usb_sentinel_display/
    __main__.py            Entry point for python -m usb_sentinel_display
    libs/
      __init__.py          Public API
      cli.py               run / calibrate, configuration overrides
      config.py            Loads and validates ~/.config/usb-sentinel/display.toml
      models.py            Slot state, attached disk, result summary, mapping table for the QR
      sysfs.py             Enumerates /sys/block, extracts port path, serial, and connection time
      result_file.py       Parses result directory names, converts times, summarizes result.json
      results.py           Scans the results directory and matches it against the SQLite index (ResultStore)
      result_detail.py     Builds the material for the detail QR (ResultDetail) from steps[*].details of result.json
      store.py             Read-only index of inspections.sqlite (SqliteIndex)
      units.py             Calls systemctl show and parses its output, Runner
      mapper.py            Builds the 4 slots from disks, results, and unit states
      monitor.py           The 1-second update body and exception rules
      qr_payload.py        On-screen QR payload (normal QR v:2, detail QR v:3 in 3 codes)
      qr_render.py         Builds a PPM image from the QR matrix
      ui/
        theme.py           Verdict colors, dimensions, fonts
        i18n.py            ja/en text and language recording
        strip.py           Info strip text
        tiles.py           Drawing of the 4 tiles and the ◀ / language / ▶ buttons
        qr_view.py         QR canvas (normal QR / detail QR) and the info strip
        keys.py            Key bindings (1 to 4, arrows, F5, L, Q, Ctrl+Q)
        app.py             tk root, full screen, resolution check, SIGTERM
  tests/                   Tests with external dependencies replaced by a fake sysfs tree and FakeRunner
  scripts/
    make-fixtures.py       Fake sysfs tree and sample results for development
    dev-screenshot.py      Takes screenshots on the development machine
  deploy/
    usb-sentinel-display.desktop  For XDG autostart
    display.example.toml   All keys of the display settings
  pyproject.toml           Python dependencies and development tool settings
  uv.lock                  Python dependency lock file
  .python-version          Python version
  README.md                Setup and development instructions for the display frontend
  AGENTS.md                Conventions for the display frontend
doc/
  qr-payload.md            QR payloads (file v:3, normal QR v:2, detail QR v:3) and the SQLite specification
deploy/
  99-usb-sentinel-docker.rules  udev rules for Docker (host side)
  usb-sentinel-docker@.service systemd unit that starts Docker (host side)
  config.example.toml      All configuration keys
scripts/
  install-all.sh           One-shot setup of a single Pi (Docker, uv, display app, autostart, ClamAV DB)
  install-docker.sh        Docker setup (called from install-all.sh; can also be used alone)
  run-docker.sh            Starts an inspection container per USB drive
README.md                  Overview and setup of the whole project
LICENSE                    License of the whole project
```

All external commands (clamscan / f3 / fio / mount / lsblk / udevadm / findmnt) are hidden behind `CommandRunner`, so Linux-only behavior can be tested on a macOS development machine.

### Why not run a script directly from udev

udev kills the child processes of an event handler after 180 seconds by default. f3write / f3read take tens of minutes to several hours depending on capacity, so they would always be killed midway. The udev rule therefore only requests the start of `usb-sentinel@%k.service` through `ENV{SYSTEMD_WANTS}`, and leaves the execution to a systemd template unit with `TimeoutStartSec=0`. Because it is a template, `usb-sentinel@sda` to `@sdd` run in parallel as independent units, and the logs of each port can be separated with `journalctl -u usb-sentinel@sda`.

## Docker setup (Raspberry Pi 4 / Ubuntu 26 series)

The target is 64-bit (arm64) Ubuntu Server, systemd, and a rootful Docker Engine.
macOS is for Python development and testing; the automatic USB inspection runs on the Pi.
The Debian inside the image and the Ubuntu on the host are separate environments.
Building on the Pi produces an arm64 image.

```text
USB connected
  → udev on the Ubuntu host
  → usb-sentinel-docker@sda.service
  → docker run (an independent inspection container per USB drive)
  → python -m usb_sentinel inspect /dev/sda
  → results saved to /var/lib/usb-sentinel/results on the host
  → one row recorded in /var/lib/usb-sentinel/db/inspections.sqlite on the host
```

Python is started as a backend registered with systemd, once per connection event.
No HTTP API or resident web server is used. When four USB drives are plugged in,
each disk runs in its own systemd unit and container.

#### SQLite records

`run-docker.sh` bind-mounts the host's `/var/lib/usb-sentinel/db` to the same path in the container,
and the backend writes one row to `inspections.sqlite` per inspection (`state = 'started'` at the start,
updated to `state = 'completed'` after `result.json` is written). `install-docker.sh` creates this directory as
`root:usb-sentinel` with `2770` (setgid), so the DB itself and the WAL files `-wal` and `-shm` can be read
by the `usb-sentinel` group. `result.json` is the source of truth; the DB is an index for listing and searching.
If recording fails, the inspection continues and the exit code does not change (a failure at the start is left in `warnings` of `result.json`,
and a failure at completion is left in the journal's stderr as `WARNING usb_sentinel.libs.cli: SQLite record failed (completion): ...`).
Diagnostic messages on stderr start with a level and a logger name, such as `ERROR usb_sentinel.libs.cli: Inspection refused: ...`. Standard output carries only the command output (the `selftest` lines and the inspection result text).
The meaning of the columns is in Section 8 of [doc/qr-payload.md](doc/qr-payload.md).

If the DB is corrupted, confirm that no inspection is running, and then move `inspections.sqlite` and `-wal` and `-shm`
aside under another name. An empty DB is recreated at the next inspection. Results from before the move are not
lost, because all of them remain in `result.json`. The `reindex` subcommand, which rebuilds the DB from `result.json`, is planned for phase 3.

### 0. One-shot setup (recommended)

Run this once on a Pi that is connected to the network, after cloning the repository.
It performs everything together: Docker, uv, the display app, automatic login, autostart, and fetching the ClamAV definition DB.
At the end it prints the remaining manual work and a "checklist before unplugging the LAN". Re-running it keeps existing settings.

```bash
sudo bash scripts/install-all.sh
# When the display user is someone other than the user running sudo
sudo DISPLAY_USER=kiosk bash scripts/install-all.sh
```

After fetching the official uv installer, the script shows its SHA-256 and asks for confirmation before running it (`INSTALL_YES=1` skips this).
Installing the touch panel driver, setting the 480x320 screen resolution, port calibration, and
enabling the capacity and speed inspections (which erase the sample) are not automated; the script describes them as steps at the end.
Steps 1 to 3 explain what `install-all.sh` does internally, and are used to redo individual parts.

### 1. Install Docker and the services

Run this on the Pi while it is connected to the network. The following is an example that uses Ubuntu packages.
If Docker Engine is already installed, the apt steps are not needed.

```bash
sudo apt-get update
sudo apt-get install -y docker.io
sudo systemctl enable --now docker
sudo docker info

# Run in the project root (builds the image and registers udev/systemd)
sudo bash scripts/install-docker.sh
```

Settings go in `/etc/usb-sentinel/config.toml`, and startup settings such as the image go in
`/etc/usb-sentinel/docker.env`. A reinstall keeps the existing settings.
When you change the image, also update `USB_SENTINEL_IMAGE` in `docker.env`.
The old udev rule for direct Python execution is removed during installation, so nothing starts twice.
Re-plug any USB drive that was already connected before the installation.

### 2. Prepare the ClamAV definition DB

The DB is stored on the host and passed to the inspection container read-only.
Fetch and update it with the following while connected to the network,
and then bring it into the isolated environment. Do not update it during a USB inspection.

```bash
sudo docker run --rm --entrypoint freshclam \
  --mount type=bind,src=/var/lib/clamav,dst=/var/lib/clamav \
  usb-sentinel:local --user=root --stdout

sudo docker run --rm --network none \
  --mount type=bind,src=/var/lib/clamav,dst=/var/lib/clamav,readonly \
  usb-sentinel:local selftest
```

If the DB has not been fetched or a dependent command is missing, `selftest` returns exit code 3.
In the isolated environment, no network connection is needed at startup as long as the image and the DB are ready.

### 3. Plug in a USB drive and check operation

```bash
sudo journalctl -f -u 'usb-sentinel-docker@*'
sudo docker ps
sudo ls /var/lib/usb-sentinel/results/

# Logs of a specific USB drive
sudo journalctl -u usb-sentinel-docker@sda.service

# Run manually through the same path as autostart (limited to unmounted samples)
sudo systemctl start usb-sentinel-docker@sda.service
```

A disk that is already mounted on the host is refused. Operate this on a dedicated
Ubuntu Server inspection machine with GUI automount disabled. The tool does not automatically unmount a system disk.
If the drive is removed during an inspection, systemd also stops the container. Reconnecting starts a new inspection.

The container basically runs with `--network none`, a read-only root filesystem, and
`--cap-drop ALL`, and receives only the target disk and its partitions with
`--device ...:r` (the default) or `:rw`. `/sys` and `/run/udev` are passed read-only and used
for the USB check. Only the results directory is writable on the host.
`SYS_ADMIN` is added for mount and `DAC_OVERRIDE` for reading files,
and the container's AppArmor restriction is lifted. Mounting takes place inside the container's namespace.
`--privileged` and sharing the Docker socket with the container are not used.

To enable the destructive inspections (capacity and speed), set both
`USB_SENTINEL_DEVICE_ACCESS=rw` in `/etc/usb-sentinel/docker.env` and `allow_destructive = true` in `config.toml`.
If only one of them is set, the device is still passed read-only. With `allow_destructive = false`,
both steps are `SKIPPED`; if only `docker.env` stays at `ro`, they are `ERROR` (insufficient permission),
and in both cases the overall verdict is `Inconclusive`. Setting `rw` erases the contents of the sample.

`scripts/install-docker.sh` keeps the existing `docker.env` on reinstall, so a `docker.env`
created by an earlier installation does not contain `USB_SENTINEL_DEVICE_ACCESS` (`run-docker.sh` treats
an undefined value as `ro`). To enable it, append it by hand.

```bash
# Append the write permission to docker.env (existing environment)
echo 'USB_SENTINEL_DEVICE_ACCESS=rw' | sudo tee -a /etc/usb-sentinel/docker.env

# Set allow_destructive in config.toml to true
sudo sed -i 's/^allow_destructive = false/allow_destructive = true/' /etc/usb-sentinel/config.toml

# Confirm the change (takes effect the next time a USB drive is plugged in)
grep USB_SENTINEL_DEVICE_ACCESS /etc/usb-sentinel/docker.env
grep allow_destructive /etc/usb-sentinel/config.toml
```

To revert, set the value in `docker.env` to `ro` or set `allow_destructive = false`.
Either one alone returns the device to read-only.

### Updating and stopping

```bash
# After updating the source, rebuild while no inspection is running
sudo docker build -t usb-sentinel:local backend

# Disable autostart
sudo rm /etc/udev/rules.d/99-usb-sentinel-docker.rules
sudo udevadm control --reload

# Stop running inspections
sudo systemctl stop 'usb-sentinel-docker@*.service'
```

## Display frontend (3.5-inch touch display)

A 480x320 touch display connected to the Pi shows the inspection status of four ports and the QR code of the selected port.
It is a tkinter app (`frontend/`) that runs on the host. It only reads the backend's output and takes no part in the inspection itself.
It reads only `result.json`, `inspections.sqlite` (read-only), `/sys/block`, and `systemctl show`. There is no HTTP API or resident server.

The on-screen QR uses a shortened format (`v:2`, up to 150 bytes, error correction M) that is separate from `result-qr.png`,
and carries the first 16 digits of the SHA-256 of `result.json`. On the PC that receives the file, compare it with
`sha256sum result.json | cut -c1-16`. `result-qr.png` (`v:3`) remains as it is.

In addition to the normal QR, a detail QR (`v:3`, always 3 codes, 150 bytes or less each) that includes the capacity and speed numbers
is shown with page turning (the same method as the QR codes in a medicine notebook). The three codes are combined by the common `d` (the first 12 digits of the digest),
and the reading order does not matter. The specifications of the three formats (file QR, normal QR, and detail QR) and the recommended
schema for the reader app are collected in [doc/qr-payload.md](doc/qr-payload.md).

The screen text can be switched between English and Japanese (English by default). Switch with the button at the bottom center left or the `L` key.
The chosen language is recorded in `$XDG_STATE_HOME/usb-sentinel-display/language` (or `~/.local/state/…` if that is not set)
and takes precedence from the next start. Switching between the normal QR and the detail QR is done by tapping the QR area or pressing `Q`, and while a detail QR is shown,
◀▶ turn the pages. The verdict `Counterfeit Suspected` (counterfeit suspected, code `C`, `#AD1457`) is deployed
together with backend phase 2.

The SQLite index written by the backend (`/var/lib/usb-sentinel/db/inspections.sqlite`) is opened read-only with `mode=ro`
and used only for the list of candidates and for deciding "Scanning". The source of truth is `result.json`; if its SHA-256 does not match
`result_digest` in the index, the result is shown as "Broken". If the index is missing, cannot be opened, or has a newer schema version, the app falls back to the previous
scan of the results directory and shows the reason on the info strip. A setup that does not use the index at all is also possible with `sqlite_path = ""`.

For setup, port calibration, settings, controls, and troubleshooting, see
[frontend/README.md](frontend/README.md). The main host settings required to enable it are as follows.

- Fix the screen resolution at 480x320 (GNOME settings or `~/.config/monitors.xml`).
- Disable GNOME automount. This is required on the Desktop edition; if it stays enabled, `run-docker.sh` refuses the inspection
  as a host-mounted disk.
- Stop sleep and the screensaver (`gsettings`).
- Add the display user to the `usb-sentinel` group and log in again. `install-docker.sh` creates the
  results root as `root:usb-sentinel` with `0750`, so group members can read it. The SQLite index
  directory (`2770`) is also readable by the same group.
- Install `fonts-noto-cjk`.
- Register `frontend/deploy/usb-sentinel-display.desktop` for XDG autostart (`~/.config/autostart/`).

## Carrying out results (across the air gap)

`result-qr.png` contains a compact summary (`v:3`). Its content is the verdict, serial, start and end times, elapsed time, the state of each step, the ClamAV DB version and date, the SHA-256 digest of `result.json`, the malware detection count and signatures, the excess over the advertised capacity (`advertised_over`), capacity numbers (nominal / advertised / measured, classification, loss ratio, counterfeit reason), the USB link speed, the device class, speed numbers (measured / threshold, basis, severity), the reasons for the verdict (codes and sentences), and the digest and row count of the hash inventory. The list of keys is in [doc/qr-payload.md](doc/qr-payload.md).

A QR code holds at most 2953 bytes in byte mode, so the full text does not fit. When the content exceeds that, it is shrunk in this order: cut signatures to 3, cut each reason sentence to 120 characters, reduce signatures to 0, empty the reason sentences (`reason_codes` stays), cut the serial to 128 characters, and drop `capacity.probe_verdict_line`. If anything is cut, `truncated: true` is set. A typical inspection comes to about 1.2KB, and cutting happens only when there are very many signatures.

On a PC connected to the network, compare `sha256sum result.json` with `digest` in the QR, and `sha256sum hash-inventory.tsv` with `hash_inventory_digest` in the QR. The former detects tampering with the result file, and the latter detects tampering with the inventory file.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | `No Defect` |
| 1 | The verdict is not clean (`Malware Detected` / `Counterfeit Suspected` / `Spec Mismatch` / `Defective` / `Inconclusive`) |
| 2 | Dangerous device (the inspection was refused) |
| 3 | Runtime error (a dependent command is missing, and so on) |

## Verdict classification

| Verdict | Condition |
|---|---|
| `Malware Detected` | Malware was detected (takes priority over other failures) |
| `Counterfeit Suspected` | Suspected capacity counterfeiting. Any of: (1) the advertised or real capacity exceeds 107% of the nominal capacity (`nominal_bytes`), (2) f3probe reports `Bad news`, (3) f3read finds 50% or more lost sectors. This is a suspicion, not a conclusion |
| `Defective` | The capacity is as advertised, but f3read detected lost, corrupted, changed, or overwritten sectors |
| `Spec Mismatch` | Any step is FAIL (insufficient capacity, insufficient speed) |
| `Inconclusive` | ERROR, or SKIPPED / unimplemented steps remain |
| `No Defect` | All steps Pass |

The priority is `Malware Detected` > `Counterfeit Suspected` > `Defective` > `Spec Mismatch` > `No Defect`, and if any ERROR or SKIPPED remains the verdict is `Inconclusive`. The basis for a verdict is kept in `reasons` (sentences) and `reason_codes` (`m:found`, `c:over`, `c:probe`, `c:lost`, `c:short`, `c:defect`, `s:severe`, `s:minor`, and `<step abbreviation>:timeout` / `error` / `skipped`) in `result.json`.

`Counterfeit Suspected` is treated as a signal to escalate, because f3probe can output `Bad news` even for a genuine product with many bad blocks, and mixing up GB and GiB can push a value over 107%. The nominal, advertised, and measured values and their ratios are kept in `result.json`, the QR, and SQLite, so a person makes the final decision.

#### Capacity

Insufficient capacity is always `Spec Mismatch`, and `Defective` is used only when the capacity is at or above the threshold. In f3probe mode (`use_probe = true`), information on non-OK sectors is not available, so the verdict is never `Defective`.

When `[capacity] nominal_bytes` is set to the nominal capacity from the product page in bytes, the denominator of the verdict changes from the advertised capacity (the lsblk value) to the nominal capacity, and the verdict uses a ±7% band: below 93% is insufficient capacity and above 107% (`over_ratio = 1.07`) is counterfeit suspected. If it is not set, only the previous 93% check against the advertised capacity is made, and excess is not judged.

- `nominal_bytes` is the decimal notation converted directly to bytes (32GB → `32000000000`). A GiB notation (32GiB = `34359738368`) is 1.0737 times the GB value, so mixing them up turns a genuine product into a counterfeit suspect. If many genuine products are advertised in GiB, relax it to `over_ratio = 1.10`.
- Non-destructive check of the advertised capacity. If `nominal_bytes` is set, right at the start of the inspection, before any mounting or scanning, only the advertised capacity from lsblk is compared with the nominal capacity and recorded in `advertised_check` of `result.json`. It is recorded even with `allow_destructive = false` and even when a malware detection stops the inspection, and an excess gives `Counterfeit Suspected`. It writes nothing and does not affect the condition (gate) for proceeding to the destructive inspections.
- When the capacity step has run, the measured capacity is judged with the same band. If f3read reports 50% or more lost sectors (`lost_ratio`), it is counterfeit suspected. This is because defects in genuine products stay at a few percent or less.
- `config.toml` is one per host, so when you inspect different products on the four ports at the same time, the same nominal value applies to all of them. Manage the nominal value for each case on the reader app side.

#### Speed

Speed is compared with `nominal_read_mb_s` and `nominal_write_mb_s` (MB/s) in `[speed]`. When no nominal value is set, the verdict uses the industry minimum for the device class (`[speed] device_class`). If a nominal value is set, it takes priority. If only one side is set, only that side is judged and the unset side is only recorded as a measured value (the summary shows `(not judged)` for that side).

| `device_class` | Class | Read (MB/s) | Write (MB/s) |
|---|---|---|---|
| `hdd_5400` | HDD 5400rpm | 80 | 80 |
| `hdd_7200` | HDD 7200rpm | 120 | 120 |
| `sata_ssd` | SATA SSD | 400 | 400 |
| `nvme_ssd` | NVMe / USB3.2 SSD | 500 | 500 |
| `sd_c10` | SD Class10 / U1 | 25 | 10 |
| `sd_u3` | SD U3 / V30 | 90 | 30 |
| `usb2_flash` | USB2.0 flash | 15 | 5 |
| `usb3_flash` | USB3.0 flash | 50 | 15 |

- If `device_class` is not set, the tool infers `usb2_flash` (480Mbps) or `usb3_flash` (5000Mbps or more) from the sysfs USB link speed. When it infers, it always leaves `Device class not set; judged with USB flash defaults ...` in `warnings`. HDD, SSD, and SD cannot be told apart from the link speed, so set `device_class` in operations that handle them.
- Use `nvme_ssd` when the host has USB3.2 Gen2 (10Gbps) or faster. The USB3 ports of the Raspberry Pi 4 are Gen1 (5Gbps) and top out at about 400 to 450MB/s in practice, so a 500MB/s minimum cannot be judged. In this case, set `nominal_*`. When the reference value is close to the effective upper limit of the link speed, `warnings` gets a `Reference ... exceeds the effective upper limit of a ...Mbps link ...` warning.
- Samples on a USB1.1 link (12Mbps or 1.5Mbps) have no verdict criterion. If neither `device_class` nor a nominal value is set, the speed step is `SKIPPED`, and `USB1.1 link (...Mbps) has no verdict criterion; ...` is left in `warnings`.
- If neither is set and the link speed cannot be read, the speed step is `SKIPPED` and the overall verdict is `Inconclusive`.
- Severity of insufficient speed. A measured value below 50% of the reference is `severe` (the summary starts with `Speed shortfall (severe)`), 50 to 79% is `minor` (`Speed shortfall (minor)`), and 80% or more (`pass_ratio`) is PASS. It appears in `details.severity` and in `s:severe` / `s:minor` of `reason_codes`.

### Nominal speed

In `[speed]` of `/etc/usb-sentinel/config.toml`, set the manufacturer's published values in MB/s (10^6 bytes/second).

```toml
[speed]
nominal_read_mb_s = 90.0    # Nominal read speed. If unset, not judged
nominal_write_mb_s = 20.0   # Nominal write speed. If unset, not judged
pass_ratio = 0.80           # PASS at nominal value × pass_ratio or more
# device_class = "usb3_flash"  # Class used when there is no nominal value. Choose a key from the table above
```

Measured values are recorded in `steps.speed.details.read.mb_s` and `write.mb_s` of `result.json`, and thresholds in `threshold_read_mb_s` and `threshold_write_mb_s`. The basis of the verdict (`basis_read` and `basis_write`: `nominal` / `class` / `none`), the determined class (`device_class`) and its origin (`device_class_basis`: `config` / `link_speed` / `unknown`), and the severity (`severity`) are in the same `details`.

### `result.txt`

`result.txt` is `result.json` in a human-readable form. It starts with three lines, `Verdict:`, `Device:`, and `Advertised capacity:` (the capacity lsblk reports). Then it shows `Link speed:`, `Elapsed:`, `Advertised capacity exceeded:`, and `Warning:` lines, and after the line of each step it shows `Nominal (product):`, `Kind:`, and `Severity:`. Items added in phase 2 are omitted together with their line when there is no value (a step that ended in ERROR has none), so you can judge missing data by the presence of the line.

### Limits of the f3write mode

The default (`use_probe = false`) verifies free space with f3write + f3read and estimates the real capacity by adding the used amount (`statvfs`). Inspect the following samples with `use_probe = true` (f3probe).

- (a) A nearly full sample. The verified area does not reach the threshold, so the result is `ERROR` (the used amount alone never makes a sample pass).
- (b) An NTFS sample. It cannot be mounted rw in the Docker setup (`ntfs-3g` uses FUSE, but `run-docker.sh` does not pass `/dev/fuse`).
- (c) A counterfeit product usually becomes `Spec Mismatch`, but if the rewinding writes break the FAT area, the output of f3read cannot be parsed and the result is `ERROR` (`Inconclusive`). A sample that over-reports its used amount also becomes `Inconclusive`. Re-inspect a sample whose summary starts with `Could not parse f3read output` or `Verified area ... is below threshold ...` with `use_probe = true`.

f3probe writes to the device directly without going through the filesystem, so it is not affected by the above. In either mode the contents of the sample are erased.

## Development

```bash
cd backend
uv sync --frozen
uv run pytest -q
uv run ruff check . && uv run ty check
uv run python -m usb_sentinel selftest   # It is normal for dependent commands to be reported as MISS
```

## Open items

The capacity (f3) and speed (fio) steps are implemented, but the following are waiting for calibration on the real device.

1. fio parameters (`block_size` / `queue_depth` / `duration_s`): the defaults of 1M / 8 / 30 seconds are provisional. Calibration against CrystalDiskMark numbers is needed
2. Source of the nominal speed and how it is configured: who sets `nominal_read_mb_s` / `nominal_write_mb_s` for each sample, and how
3. Whether adding the used area to the real capacity is appropriate: in f3write mode, the sample's reported value (`statvfs`) is added
4. Offline update procedure for the ClamAV definition DB: how to bring it into the isolated environment, and how often
5. Confirmation on a real Raspberry Pi 4: agreement between the real output of f3read / f3probe / fio and the parsers, writing inside the container with Docker `rw`, and the behavior of `mount -w` (see the on-device checks in `.agents/tasks/f3-fio-steps/final-report.md` for details)

The on-device checks added in phase 2 (hash inventory separation, ±7% band, link speed, SQLite, QR v:3) are as follows.

1. Whether `/sys/bus/usb/devices/<port_path>/speed` can be read from inside the container (`/sys` is a read-only bind mount). If it cannot, `link_speed_mbps` is `null`, and for a sample without `device_class`, speed is `SKIPPED`.
2. SQLite WAL on the bind mount. That `database is locked` does not appear with 4 containers writing at the same time (that `busy_timeout` absorbs it), that `ls -l` on a real Linux machine shows `-wal` and `-shm` created with the same mode as the main file (`usb-sentinel` group, `0660`), and that the display user can read them with `mode=ro`.
3. The real output of the clamscan summary lines (key names, the format of `Time:`, the unit of `Data scanned:`).
4. The actual wording of the f3probe `Bad news` line (the three types: limbo / wraparound / chain).
5. The value of the USB link speed. That a USB2.0 device plugged into a USB3.2 Gen2 port returns `480`, and a USB3.0 device gives `5000`. The USB3 ports of the Raspberry Pi 4 go up to Gen1 (5000).
6. Verification of excess capacity. Whether there are genuine products whose real capacity exceeds the nominal one (GiB advertising). If there are, review the default of `over_ratio`.
7. Consistency between `elapsed_s` and the clamscan `Time:` (`elapsed_s` includes mounting and the version check, so it is larger).
8. Inventory creation with an infected sample (EICAR). That `hash-inventory.tsv` is created after a clamscan FAIL and `details.digest` matches `sha256sum`. Conversely, that `hash-inventory.tsv` is not created with the definition DB temporarily removed.

The verdict formula "real capacity ≥ 93% of the nominal capacity" is implemented in `evaluate()` of [capacity_judge.py](backend/usb_sentinel/libs/steps/capacity_judge.py) (re-exported from `capacity.py`).
