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

## For operators

This section collects what an operator needs day to day. Details are linked.

### 1. Install or update

Connect the LAN, then run in a terminal on the device:

```bash
cd ~/usb_sentinel && git pull && sudo INSTALL_YES=1 bash scripts/install-all.sh && sudo reboot
```

Running it again is safe. An armed kiosk stays armed, and an old frontend venv is rebuilt.
Optional settings go before `bash`, for example `sudo INSTALL_YES=1 INSTALL_KIOSK=0 bash ...`.

| Setting | Effect |
|---|---|
| `INSTALL_SSH=1` | Key-only SSH for maintenance. Password login over SSH is turned off. See [SSH for maintenance](#ssh-for-maintenance-optional) |
| `INSTALL_ALLOW_ERASE=0` | Scan-only station. "Scan + erase" cannot be used |
| `INSTALL_KIOSK=0` | Do not start the app full screen at login. See [Opting out of arming](#opting-out-of-arming) |
| `INSTALL_PORTS=0` | Do not write the Raspberry Pi 4 port table |
| `INSTALL_VIDEO_HINT=1` | Add the 480x320 boot hint. See [Screen size](#screen-size) |

### 2. Port map (Raspberry Pi 4)

On a Raspberry Pi 4 the installer writes this table into `~/.config/usb-sentinel/display.toml`.
Positions are seen facing the USB ports. It was measured with a USB 2.0 stick on a Pi 4 Model B
Rev 1.5; the `2-x` paths (USB 3.0 sticks) are not verified yet.

| Position | Colour | sysfs paths | Tile |
|---|---|---|---|
| Left top | Black (USB 2.0) | `1-1.3` | P1 |
| Right top | Blue (USB 3.0) | `1-1.1`, `2-1` | P2 |
| Left bottom | Black (USB 2.0) | `1-1.4` | P3 |
| Right bottom | Blue (USB 3.0) | `1-1.2`, `2-2` | P4 |

On other hardware, measure the ports with `calibrate`. See
[Optional: port calibration](#optional-port-calibration).

### 3. Daily use

Plug in a USB drive. The inspection starts by itself and the tile shows the verdict.

| Key | Action |
|---|---|
| `1`-`4` | Select a tile |
| Arrow keys | Previous or next tile |
| `M` | Switch between "Scan only" and "Scan + erase" ("Scan + erase" asks for a 4-digit code) |
| `Q` | Switch the QR code (normal / detail) |
| `L` | Switch the language (日本語 / English) |
| `F5` | Reload the results |

Plug the keyboard in before power-on. A keyboard plugged in later locks erase mode until the
next reboot (a guard against BadUSB devices).

| Verdict | Screen (Japanese) | Meaning |
|---|---|---|
| No Defect | 異常なし | Every check passed, including capacity and speed |
| No Defect (Scan Only) | 異常なし（検査のみ） | Malware and the other checks passed; capacity and speed were not tested (scan only) |
| Malware Detected | マルウェア検知 | ClamAV found malware |
| Counterfeit Suspected | 偽装疑い | The capacity looks faked |
| Spec Mismatch | 仕様不一致 | Capacity or speed is too low |
| Defective | 不良 | Data gets corrupted |
| Inconclusive | 判定不能 | A check failed to run or was skipped for another reason |

See [Verdict classification](#verdict-classification) for the rules.

### 4. Maintenance access

- From another PC on the LAN: `ssh amzn@<host>.local`. For password login install the server with
  `sudo apt-get install -y openssh-server`. For key login run `ssh-copy-id amzn@<host>.local`
  from the PC, or use `INSTALL_SSH=1`.
- On the device: Ctrl+Alt+F3 opens the text console (large font). Ctrl+Alt+F1 returns.

### 5. Recovery

```bash
pkill -f usb-sentinel-display                    # stop the app until the next login
sudo bash ~/usb_sentinel/scripts/kiosk-disable.sh  # turn the kiosk off
sudo bash ~/usb_sentinel/scripts/kiosk-enable.sh   # turn it on again (then reboot)
```

The app log at login is `~/.local/state/usb-sentinel-display/autostart.log`. To see why the last
result was Inconclusive, list its steps:

```bash
latest=$(ls -td /var/lib/usb-sentinel/results/*/ | head -n 1)
python3 -c "import json,sys; r=json.load(open(sys.argv[1])); print(r['verdict']); [print(s['name'], s['status'], s.get('summary','')) for s in r['steps']]" "${latest}result.json"
```

See [Recovery](#recovery) for details.

### 6. ClamAV update

Whenever the LAN is connected, update the virus definitions:

```bash
sudo bash ~/usb_sentinel/scripts/update-clamav.sh
```

See [2. Prepare the ClamAV definition DB](#2-prepare-the-clamav-definition-db).

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
Step 7  Classify the verdict (No Defect / No Defect (Scan Only) / Counterfeit Suspected / Spec Mismatch / Defective / Malware Detected / Inconclusive)
```

### Safety design

- ClamAV runs first. Once ClamAV has run the scan, the hash inventory is created regardless of the result (detection, error, or timeout), as evidence for later comparison. If ClamAV has not opened any file (clamscan is missing, or there is no definition DB), no inventory is created either. The tool never proceeds to the destructive inspections (f3 / fio). An inventory created after a timeout or a scan failure (exit code 2) only reads the bytes of files that ClamAV did not open, under `ro,noexec,nosuid,nodev`; it neither executes nor interprets them.
- Read-only mount. The mount for scanning and hash computation uses `ro,noexec,nosuid,nodev`, leaving no room for an infected file to run on the host.
- Destructive inspections are blocked in several ways. No write is made if any of the following holds: malware is FAIL or ERROR, `allow_destructive = false`, the inspection mode is `scan_only` (the display's mode button was not switched to "Scan + erase" when the drive was inserted), or a mount remains. The host side passes the device read-only unless all of `USB_SENTINEL_DEVICE_ACCESS=rw`, `allow_destructive = true` and the display mode `full` hold.
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
  install-all.sh           One-shot setup of a single Pi (Docker, uv, display app, ClamAV DB; arms the kiosk at the end)
  kiosk-enable.sh          Arms the kiosk later when the installer was told not to
  kiosk-disable.sh         Recovery: disables autostart, stops the app, resets the lockdown
  boot-video-hint.sh       Optional cmdline.txt video= hint (sourced by install-all.sh, INSTALL_VIDEO_HINT=1)
  ssh-access.sh            Opt-in key-only SSH for maintenance (sourced by install-all.sh, INSTALL_SSH=1)
  install-docker.sh        Docker setup (called from install-all.sh; can also be used alone)
  run-docker.sh            Starts an inspection container per USB drive
  update-clamav.sh         Fetches and verifies the ClamAV definition DB (use it to refresh later)
  clamav-db.sh             Shared fetch/verify functions (sourced by install-all.sh and update-clamav.sh)
  erase-setting.sh         Shared setter for the two erase keys (sourced by install-docker.sh)
  keyfile.sh               Shared idempotent "KEY = value" line setter
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

Two steps. The station is ready for people who do not know the technology inside.

1. On a Pi that is connected to the network, clone the repository and run one command:
   ```bash
   sudo INSTALL_YES=1 bash scripts/install-all.sh
   # With maintenance SSH (recommended; see below)
   sudo INSTALL_YES=1 INSTALL_SSH=1 SSH_AUTHORIZED_KEY="ssh-ed25519 AAAA... you@laptop" bash scripts/install-all.sh
   # When the display user is someone other than the user running sudo
   sudo INSTALL_YES=1 DISPLAY_USER=kiosk bash scripts/install-all.sh
   ```
   It installs Docker, uv, the display app and the ClamAV definition DB, turns on automatic
   login, writes the autostart entry and, in its last step, arms the kiosk (autostart enabled,
   GNOME lockdown applied). It also installs the Japanese font and enables erasing on the
   host (see "3. Plug in a USB drive"). The run is unattended and takes about 15 minutes.
   For a scan-only station that never erases, add `INSTALL_ALLOW_ERASE=0`.
2. `sudo reboot`. From now on the app starts full screen every time the Pi is powered on.

Without `INSTALL_YES=1` the script asks two questions up front, before anything is changed:
whether to run the official uv installer (SHA-256 shown; skipped when uv is already installed)
and whether to arm the kiosk at the end (`[Y/n]`, Enter means yes). Fetching the ClamAV
definition DB (freshclam) is not a question: it always runs, retries up to 3 times, and the
install stops with the re-run command when `main`, `daily` and `bytecode` are still missing.
At the end it prints the kiosk state, the escape routes, the optional steps and a "checklist
before unplugging the LAN". Re-running it keeps existing settings, except the two erase lines
(`allow_destructive` in `config.toml`, `USB_SENTINEL_DEVICE_ACCESS` in `docker.env`), which
follow `INSTALL_ALLOW_ERASE` (the previous file is kept as `.bak`).

#### Controls (mode switch)

Press `M` to switch the inspection mode (or tap the mode button at the bottom left). From scan
only a dialog shows a 4-digit code; enter it with keys `1`-`4` or the on-screen buttons. In
"Scan + erase", `M` returns to scan only at once. The same key works in the kiosk (`--kiosk`).

#### Japanese font and text console

The installer installs `fonts-noto-cjk` and runs `fc-cache -f`. The app uses
`Noto Sans CJK JP`, else the first installed fallback (`Noto Sans JP`, `IPAGothic`, ...). When
none is found, the info strip shows "No Japanese font (install fonts-noto-cjk)" (in ASCII,
because Japanese would render as boxes). Tk must also be built with Xft. uv's own Python has a
Tk without Xft, so step 5 builds `frontend/.venv` on the system Python (`/usr/bin/python3`) with
apt's `python3-tk`, whose Tk uses Xft. A venv left by an earlier run on uv's Python is removed and
rebuilt, so re-running `sudo INSTALL_YES=1 bash scripts/install-all.sh` fixes an old device.
Step 5 prints `Tk font system: xft` when it is fine, and a boxed warning when it is not. The check
needs no display; on the device you can repeat it with (a `libXft` line, or a count above 0,
means Xft is present):
```bash
so=$(frontend/.venv/bin/python -c "import _tkinter; print(_tkinter.__file__)")
ldd "$so" | grep libXft; grep -c XftDrawString "$so"
```

To make the text console (Ctrl+Alt+F3) readable on the small screen, the installer sets
`FONTFACE="Terminus"` and `FONTSIZE="16x32"` in `/etc/default/console-setup` (the old file is
kept as `.bak`). It applies during the install or after a reboot; `sudo setupcon` applies it at
once. The console cannot show Japanese, so the locale is unchanged. Use
`INSTALL_CONSOLE_FONT=0` to keep the current font.

#### Optional: port calibration

On a Raspberry Pi 4 the installer writes the port table (see [Port map](#2-port-map-raspberry-pi-4));
this step is only for other hardware. Uncalibrated ports show `P?` with the kernel name and keep working. To give them names, log in over
SSH or press Ctrl+Alt+F3 for the text console, run `usb-sentinel-display calibrate` (the exact
command is printed at the end of the install) and write the `[[ports]]` table into
`~/.config/usb-sentinel/display.toml`; the app reads it at its next start. See
[frontend/README.md](frontend/README.md), "Port calibration".

#### Opting out of arming

Answer `n` to the kiosk question, or run with `INSTALL_KIOSK=0` (it beats `INSTALL_YES=1`). The
display user then lands on the normal GNOME desktop with the autostart entry written but hidden.
Arm later with `sudo bash scripts/kiosk-enable.sh` and reboot.

#### Recovery

Once armed, the app takes focus back from every window and GNOME has no shortcuts left. Press
Ctrl+Alt+F3 for the text console, or, when SSH was enabled, plug in the LAN and log in with
`ssh <admin>@<ip>`. Then run `pkill -f usb-sentinel-display` to stop the app and
`sudo bash scripts/kiosk-disable.sh` to turn autostart off and reset the lockdown. Ctrl+Alt+F1
returns to the desktop. A crash at login is logged in
`~/.local/state/usb-sentinel-display/autostart.log`. If `INSTALL_VIDEO_HINT=1` (below) left the
console blank, restore the boot line with `sudo cp /boot/firmware/cmdline.txt.bak /boot/firmware/cmdline.txt`.

#### Screen size

The app scales its 480x320 design to the screen (letterboxed with black bars). No resolution
setting is needed. `INSTALL_VIDEO_HINT=1` adds the boot hint `video=HDMI-A-1:480x320@60` to
`/boot/firmware/cmdline.txt` for a sharper text console and GDM greeter; it is off by default
because the panel's acceptance of that mode is unverified. The installer keeps
`cmdline.txt.bak` for undoing it.

Installing the touch panel driver is not automated; the script lists it at the end.

#### SSH for maintenance (optional)

The 3.5-inch panel is hard to read and the armed kiosk covers the screen, so debugging is
easier from a laptop over the wired LAN. `install-all.sh` does not install an SSH server by
default; opt in with `INSTALL_SSH=1` and your public key:

```bash
sudo INSTALL_SSH=1 SSH_AUTHORIZED_KEY="ssh-ed25519 AAAA... you@laptop" bash scripts/install-all.sh
# or
sudo INSTALL_SSH=1 SSH_AUTHORIZED_KEY_FILE=/path/to/id_ed25519.pub bash scripts/install-all.sh
```

The step installs `openssh-server`, writes `/etc/ssh/sshd_config.d/90-usb-sentinel.conf`
(public key only, no password or root login, `AllowUsers` limited to the user running sudo, not
the display user), checks it with `sshd -t`, enables the service and opens 22/tcp in ufw only when
ufw is already active. Without a key the script stops; it never falls back to password login. At
the end it prints the LAN addresses and the `ssh <admin>@<ip>` command. sshd stays enabled after
the LAN is unplugged, which is harmless; `sudo systemctl disable --now ssh` turns it off.

Steps 1 to 3 below explain what `install-all.sh` does internally, and are used to redo individual parts.

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
`/etc/usb-sentinel/docker.env`. A reinstall keeps the existing settings except the two erase
lines, which follow `INSTALL_ALLOW_ERASE` (default `1`).
When you change the image, also update `USB_SENTINEL_IMAGE` in `docker.env`.
The old udev rule for direct Python execution is removed during installation, so nothing starts twice.
Re-plug any USB drive that was already connected before the installation.

### 2. Prepare the ClamAV definition DB

The DB is stored on the host and passed to the inspection container read-only.
`install-all.sh` fetches it. To refresh it later, plug in the LAN and run (safe to repeat):

```bash
sudo bash scripts/update-clamav.sh
```

The script runs freshclam in the container up to 3 times and checks that `main`, `daily` and
`bytecode` (`.cvd` or `.cld`) exist and are non-empty in `/var/lib/clamav`; otherwise it
prints the reason and exits 1. Do not update it during a USB inspection.

Without the DB the malware step is `ERROR` with the reason
`malware: ClamAV signature database missing (run scripts/update-clamav.sh)`, and the verdict
is `Inconclusive`.

To check by hand:

```bash

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

Destructive inspections (capacity and speed) run only when three conditions hold at the
moment the drive is inserted. The installer meets the administrator conditions 1 and 2 by
default; the operator chooses condition 3 with `M` or the mode button.

1. `USB_SENTINEL_DEVICE_ACCESS=rw` in `/etc/usb-sentinel/docker.env` (the container gets the
   device with `:rw`);
2. `allow_destructive = true` in `/etc/usb-sentinel/config.toml` (this host MAY erase);
3. the display's mode button (bottom left) shows "Scan + erase". Switching it on opens a
   dialog that shows a 4-digit code; enter it with keys `1`-`4` or the on-screen buttons
   within 30 s. The code is shown only on the screen, so a keyboard alone cannot turn erase
   mode on.

The first two are the administrator's ceiling (enabled by the installer by default, disabled
with `INSTALL_ALLOW_ERASE=0`), the third is the operator's choice per
session. `run-docker.sh` reads the display's mode file (`/run/usb-sentinel-control/mode`) once
at insertion and passes `--mode full` together with `:rw` only when all three hold; otherwise
the device stays read-only and the container runs `--mode scan_only`. With `allow_destructive
= false`, or with the display in scan only, the capacity and speed steps are `SKIPPED`
(reason `Inspection mode is scan_only` in the second case). Behaviour change: with
`allow_destructive = true` and `docker.env` still `ro`, the steps are now also `SKIPPED`
"Inspection mode is scan_only" instead of `ERROR` (insufficient permission), because the host
never requests `full` without `rw`. When every other step passed, these two skip reasons
give `No Defect (Scan Only)` (shown as 異常なし（検査のみ）, teal), with the reason
`Scan only: capacity and speed not tested`. If another step failed or errored, or the capacity
and speed steps were skipped for another reason (for example a missing ClamAV DB:
`malware: ClamAV signature database missing ...`), the verdict stays `Inconclusive`. Full
`No Defect` needs "Scan + erase". A manual run
(`usb-sentinel inspect`) erases only with `--mode full` (default `scan_only`).

The mode is fixed when a drive is inserted. Switching the display back to scan only does not
stop an inspection already running in erase mode; the strip says "Inserted drives may still be
erased; remove them". Every start of the display and every boot reset the mode to scan only
(`/run` is a tmpfs, and the host honours `full` only while the stamp is at most 20 s old). If
you disable switching with `control_dir = ""` in `display.toml`, wait 20 s after restarting the
display before inserting a drive. The mode is written to `result.json` (`mode`), `result.txt`
(`Mode:` line), the file QR and the SQLite index (`mode` column, `user_version` 2); update the
backend and the frontend together, because an old frontend rejects `user_version` 2 and falls
back to scanning the results directory (see [doc/qr-payload.md](doc/qr-payload.md)).

On every run `scripts/install-docker.sh` sets only the `allow_destructive` and
`USB_SENTINEL_DEVICE_ACCESS` lines from `INSTALL_ALLOW_ERASE` (other lines unchanged, previous
file kept as `.bak`), so re-installing a host that still had `ro` enables erasing. To make a
scan-only station:

```bash
sudo INSTALL_ALLOW_ERASE=0 bash scripts/install-docker.sh

# Confirm the change (takes effect the next time a USB drive is plugged in)
grep USB_SENTINEL_DEVICE_ACCESS /etc/usb-sentinel/docker.env
grep allow_destructive /etc/usb-sentinel/config.toml
```

Either one disabled returns the device to read-only, and the display's button becomes
"Scan only (locked)".

### Hardening against BadUSB

A USB device that presents itself as a keyboard (BadUSB) could type at the kiosk. Three
measures limit what it can do; none of them replaces the air gap.

**GNOME lockdown (applied by `install-all.sh` step 10 or `scripts/kiosk-enable.sh`).** `scripts/kiosk-lockdown.sh` clears
every shortcut in `org.gnome.desktop.wm.keybindings`, `org.gnome.shell.keybindings`,
`org.gnome.mutter.keybindings`, `org.gnome.mutter.wayland.keybindings` and
`org.gnome.settings-daemon.plugins.media-keys` except `switch-to-session-1..12`
(Ctrl+Alt+F1..F12, the password-protected text console that stays the administrator's way in),
clears the Ubuntu Dock hot keys, disables the run dialog, logout, user switching, the bare
Super key, hot corners, the accessibility toggles and the power key, and leaves one workspace
and an empty dock. To undo it run `sudo bash scripts/kiosk-disable.sh` (`kiosk_unlock` in
`scripts/kiosk-lockdown.sh` resets every key the lockdown set; the key list lives there).

**Per-boot input baseline.** The first start of the display after boot records the input
devices present (`/sys/class/input/inputN`) in `/run/usb-sentinel-control/input-baseline`. Any
input device that appears later locks erase mode ("New input device seen; reboot to allow
erase") and turns it off if it was on. Start the station with only its own keyboard and touch
panel attached; if you plug in a keyboard on purpose, reboot to unlock erase mode.

**Strike limit.** Every attempt to turn erase mode on is charged to
`~/.local/state/usb-sentinel-display/erase-strikes` before the code is shown. After 3 wrong
codes in total, erase mode stays locked ("3 wrong codes; admin must reset to allow erase").
To reset it, as the display user run `rm ~/.local/state/usb-sentinel-display/erase-strikes`
and restart the display. One wrong code also starts a 60 s cooldown.

**Optional: usbguard (not run by any script).** To block composite devices (storage plus HID)
at the kernel level, install usbguard with the station's own keyboard and touch panel attached:

```bash
sudo apt install usbguard
sudo sh -c 'usbguard generate-policy > /etc/usbguard/rules.conf'   # devices attached now
# append, then restart usbguard:
allow with-interface match-all { 08:*:* }
allow with-interface match-all { 09:*:* }
```

`match-all` means every interface of the device must be in the listed set (mass storage `08`,
hub `09`), so a drive that also exposes a keyboard interface is blocked; drives exposing other
interface classes (card readers with extra functions, for example) are blocked too. Check the
operator name against `man usbguard-rules.conf` of the Ubuntu release you run.

**Residual risk.** A keyboard that still finds a way out of the kiosk window acts as the display
user, who is root-equivalent through the `docker` group. This risk exists without the mode
toggle as well; usbguard is the complete fix.

#### Checks on the Raspberry Pi

Items that only the real station can confirm:

1. `/run/usb-sentinel-control` exists after boot as `root:usb-sentinel 2770` (systemd-tmpfiles)
   and the display user's session has the group active (`id`).
2. With the ceiling on and the display in scan only, an inserted drive keeps its data, the
   capacity and speed steps are `SKIPPED` and the tile shows the `Scan` tag.
3. After entering the code, a clean disposable drive is erased and its tile shows `Erased`;
   the detail QR page 1 contains `md: "f"`.
4. A second USB keyboard plugged in after boot locks erase mode and turns it off; logging out
   and letting autologin restart the display keeps the lock (baseline in `/run`).
5. After a reboot the button shows "Mode: Scan only" and the mode file is gone.
6. One wrong code survives a logout and a reboot in `~/.local/state/usb-sentinel-display/erase-strikes`.
7. After the installer armed the kiosk (or after `kiosk-enable.sh`), Super, Super+1..9, Shift+Super+1, Ctrl+Super+1, Super+A/S/V,
   Super+Page Down, Ctrl+Alt+arrows, Shift+Ctrl+Alt+arrows, Alt+F1, Alt+F2, Alt+Space,
   Alt+Tab, Alt+Esc, Alt+F4, Ctrl+Alt+T, Ctrl+Alt+Del, Ctrl+Alt+Tab, Print and the power key do
   nothing visible; Ctrl+Alt+F3 still opens the text console;
   `gsettings list-recursively org.gnome.desktop.wm.keybindings` shows only empty lists.
8. With erase mode on and a drive being scanned, switching back shows "Inserted drives may
   still be erased; remove them".

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

- Disable GNOME automount. This is required on the Desktop edition; if it stays enabled, `run-docker.sh` refuses the inspection
  as a host-mounted disk.
- Stop sleep and the screensaver (`gsettings`).
- Add the display user to the `usb-sentinel` group and log in again. `install-docker.sh` creates the
  results root as `root:usb-sentinel` with `0750`, so group members can read it. The SQLite index
  directory (`2770`) is also readable by the same group.
- Install `fonts-noto-cjk`.
- Register `frontend/deploy/usb-sentinel-display.desktop` for XDG autostart (`~/.config/autostart/`).
  `install-all.sh` writes it and arms it in its last step unless told otherwise; `kiosk-disable.sh` hides it again.

## Carrying out results (across the air gap)

`result-qr.png` contains a compact summary (`v:3`). Its content is the verdict, serial, start and end times, elapsed time, the state of each step, the ClamAV DB version and date, the SHA-256 digest of `result.json`, the malware detection count and signatures, the excess over the advertised capacity (`advertised_over`), capacity numbers (nominal / advertised / measured, classification, loss ratio, counterfeit reason), the USB link speed, the device class, speed numbers (measured / threshold, basis, severity), the reasons for the verdict (codes and sentences), and the digest and row count of the hash inventory. The list of keys is in [doc/qr-payload.md](doc/qr-payload.md).

A QR code holds at most 2953 bytes in byte mode, so the full text does not fit. When the content exceeds that, it is shrunk in this order: cut signatures to 3, cut each reason sentence to 120 characters, reduce signatures to 0, empty the reason sentences (`reason_codes` stays), cut the serial to 128 characters, and drop `capacity.probe_verdict_line`. If anything is cut, `truncated: true` is set. A typical inspection comes to about 1.2KB, and cutting happens only when there are very many signatures.

On a PC connected to the network, compare `sha256sum result.json` with `digest` in the QR, and `sha256sum hash-inventory.tsv` with `hash_inventory_digest` in the QR. The former detects tampering with the result file, and the latter detects tampering with the inventory file.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | `No Defect` |
| 1 | The verdict is not a full `No Defect` (`No Defect (Scan Only)` / `Malware Detected` / `Counterfeit Suspected` / `Spec Mismatch` / `Defective` / `Inconclusive`) |
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
| `No Defect (Scan Only)` | Scan-only mode: malware and the other steps Pass, and capacity and speed are `SKIPPED` only because of `allow_destructive = false` or `Inspection mode is scan_only` |

The priority is `Malware Detected` > `Counterfeit Suspected` > `Defective` > `Spec Mismatch` > `No Defect` > `No Defect (Scan Only)`, and if any other ERROR or SKIPPED remains the verdict is `Inconclusive`. The basis for a verdict is kept in `reasons` (sentences) and `reason_codes` (`m:found`, `c:over`, `c:probe`, `c:lost`, `c:short`, `c:defect`, `s:severe`, `s:minor`, and `<step abbreviation>:timeout` / `error` / `skipped`) in `result.json`.

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
