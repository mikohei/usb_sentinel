[日本語版](README.ja.md)

# Display frontend (USB Sentinel Display)

A tkinter application that shows the inspection status of four ports and the QR code of the selected port on a 3.5-inch HDMI touch display (480x320) connected to a Raspberry Pi 4. It does not touch `backend/`. Every second it reads the following four sources on the host and builds the screen from them.

- `/var/lib/usb-sentinel/results/<timestamp>-<serial>/result.json` (the inspection result; this is the source of truth)
- `/var/lib/usb-sentinel/db/inspections.sqlite` (the backend's SQLite index; opened read-only; without it the app works by scanning the results directory alone)
- USB disk information under `/sys/block/` (which disk is plugged into which port)
- The state of the inspection unit obtained with `systemctl show` (whether an inspection is running)

There is no HTTP API and no resident server. The network is not used. The on-screen text can be switched between English and Japanese (English by default).

## Screen layout

```text
x:   0        160                          480
y0   +--------+----------------------------+
     | P1 tile|                            |
y64  +--------+                            |
     | P2 tile|      QR canvas             |
y128 +--------+      320x296 (white)       |
     | P3 tile|                            |
y192 +--------+                            |
     | P4 tile|                            |
y256 +--+---+-+                            |
     | ◀|Lang|▶|                            |
y288 +--+---+-+                            |
     | Mode   |                            |
y296 |        +----------------------------+
     |        | Info strip 320x24          |
y320 +--------+----------------------------+
```

- The left 160px holds four port tiles (160x64 each) and two 32 px rows of buttons. The navigation row (y256 to 288) has ◀ (x0 to 53), language (x53 to 107), and ▶ (x107 to 160); the mode row (y288 to 320) is one button that shows the inspection mode ("Mode: Scan only", "Mode: Scan + erase", "Scan only (locked)" or "Stopping erase…"). Each tile shows the port name and verdict on line 1, the serial on line 2, and the state and time on line 3. On a finished tile, line 2 also carries a right-aligned tag: `Scan` (the drive was only scanned) or `Erased` (the capacity or speed check wrote to it); the serial is shortened to make room. The selected tile has a white border. The language button shows the name of the language to switch to ("EN" while Japanese is shown; the word for "Japanese" written in Japanese while English is shown).
- The right 320px shows the QR code of the selected port (white background, up to 294px). Tapping switches between the normal QR and the detail QR (3 codes). The info strip below it shows the port name, the first part of the serial, the first 16 digits of the digest (while a detail QR is shown, "Detail n/3" and 12 digits), or a warning message.

## Display setup

These steps assume Ubuntu Desktop (Pi 4, GNOME). `scripts/install-all.sh` performs steps 3-8 and 10 (with the autostart entry disabled); steps 1, 2 and 9 are always manual, and `scripts/kiosk-enable.sh` is the final step. The backend Docker setup (the root `README.md`) must already be installed.

1. **Fix the resolution at 480x320.** At resolutions higher than 480x320, the LCD downscales the image and the QR code blurs. Ubuntu on the Pi 4 runs on the KMS driver, so the setting takes effect in three separate layers. The resolution of the GNOME session is decided by mutter, so use (c) as the main setting and use (b) alongside it for the console.
   - (a) `/boot/firmware/config.txt` (firmware layer. It probably has no effect under KMS. Remove it if it does nothing.)
     ```ini
     hdmi_group=2
     hdmi_mode=87
     hdmi_cvt=480 320 60 6 0 0 0
     hdmi_drive=2
     ```
   - (b) Add the kernel argument `video=HDMI-A-1:480x320@60` to `/boot/firmware/cmdline.txt` (for the console and the initial mode).
   - (c) **GNOME session**: choose 480x320 in "Settings → Displays". If it is not in the list, write `~/.config/monitors.xml` as shown below. Use `HDMI-1` for `connector`, and copy values such as `vendor` from the file that the Settings app generates once you select a different resolution there. To apply it to the GDM login screen as well, run `sudo cp ~/.config/monitors.xml /var/lib/gdm3/.config/monitors.xml` and change the owner to `gdm:gdm`.
     ```xml
     <monitors version="2">
       <configuration>
         <logicalmonitor>
           <x>0</x><y>0</y><scale>1</scale><primary>yes</primary>
           <monitor>
             <monitorspec><connector>HDMI-1</connector><vendor>...</vendor><product>...</product><serial>...</serial></monitorspec>
             <mode><width>480</width><height>320</height><rate>60</rate></mode>
           </monitor>
         </logicalmonitor>
       </configuration>
     </monitors>
     ```
   - **How to check**: when you start the app in step 11, neither standard error nor the info strip should show the warning "Resolution WxH (expected 480x320)". If it appears, (c) above is not taking effect.
   - If 480x320 cannot be selected by any means, one option is to select 960x640, which the LCD can downscale at an integer ratio, and implement `--scale 2` so the app draws at 2x. This is not implemented at present.
2. **Install the touch driver.** Follow the vendor's instructions (https://osoyoo.com/ja/?p=56145). The vendor's note says "touch is not supported on Raspberry Pi OS bookworm", and operation on Ubuntu has not been verified. Even if the driver does not work, every operation in this app can be done from the keyboard (the "Controls" section). This project does not automatically download or run scripts from external sites.
3. **Install a Japanese font.**
   ```bash
   sudo apt-get install -y fonts-noto-cjk
   ```
4. **Turn on automatic login.** Use "Settings → Users → Automatic Login", or write `AutomaticLoginEnable=true` and `AutomaticLogin=<user>` in `/etc/gdm3/custom.conf`.
5. **Disable automount.** On the Desktop edition GNOME mounts USB drives automatically, and `run-docker.sh` refuses to inspect them with "host-mounted disk refused", so this step is required.
   ```bash
   gsettings set org.gnome.desktop.media-handling automount false
   gsettings set org.gnome.desktop.media-handling automount-open false
   ```
6. **Stop sleep and the screensaver.** Do not use `xset`, because this is a Wayland session.
   ```bash
   gsettings set org.gnome.desktop.session idle-delay 0
   gsettings set org.gnome.desktop.screensaver lock-enabled false
   gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
   gsettings set org.gnome.desktop.screensaver idle-activation-enabled false
   ```
7. **Make the results directory readable.** `scripts/install-docker.sh` creates the results root `/var/lib/usb-sentinel/results` as `root:usb-sentinel` with mode `0750`. Add the display user to the `usb-sentinel` group and log in again. Do not grant write permission (this prevents tampering with the evidence).
   ```bash
   sudo usermod -aG usb-sentinel <user>
   # Takes effect after logging in again
   ```
   On a machine set up with an older `install-docker.sh` (one that created the results root with `0700`), set the group and permissions by hand.
   ```bash
   sudo groupadd --system usb-sentinel
   sudo chgrp usb-sentinel /var/lib/usb-sentinel/results
   sudo chmod 0750 /var/lib/usb-sentinel/results
   ```
   If the screen shows "No permission to read results dir", suspect this step first.

   The same group can also read the SQLite index (`/var/lib/usb-sentinel/db`, `2770`). The backend writes in WAL mode, so even a read-only reader must be able to create the `-shm` file, which requires group write permission on the directory (`2770` provides it). The app never writes to the DB file itself. If the user is not in the group, the app does not use the index and falls back to scanning the results directory as before (the "Reading the SQLite index" section).
8. **Fetch uv and the dependencies.** Do this while connected to the network.
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   cd frontend
   uv sync --frozen
   uv run python -c "import tkinter"
   ```
   If `import tkinter` fails, switch to a setup that runs with the apt packages `python3`, `python3-tk`, and `python3-qrcode`. In that case, change `Exec` in the `.desktop` file of step 10 to `/usr/bin/python3 -m usb_sentinel_display run --kiosk` and point `PYTHONPATH` at `frontend/`.

   **Synchronize dependencies only in this step.** `Exec` in `deploy/usb-sentinel-display.desktop` starts the app with `uv run --offline --frozen --no-sync`. Without these options, `uv run` checks the lock and the environment for consistency on every start, and tries to resolve or sync if there is a difference. On a Pi with no network connection that causes an error and the app fails to start, so nothing is resolved at startup. This is the same policy by which the backend aligns its environment with `uv sync --frozen`.
9. **Calibrate the ports.** As described in the "Port calibration" section, run `uv run usb-sentinel-display calibrate` and write the port path of each port to `~/.config/usb-sentinel/display.toml`.
10. **Register autostart.** Replace `<user>` and the project path in `deploy/usb-sentinel-display.desktop`, and copy it to `~/.config/autostart/`.
    ```bash
    mkdir -p ~/.config/autostart
    sed "s|<user>|$USER|g" deploy/usb-sentinel-display.desktop > ~/.config/autostart/usb-sentinel-display.desktop
    ```
    If you placed the project somewhere other than `/home/<user>/usb_sentinel`, also fix the `--project` path.
    `install-all.sh` writes this file and appends `Hidden=true`, so nothing starts at login until `sudo bash ../scripts/kiosk-enable.sh` removes that line. The entry waits 5 seconds (`X-GNOME-Autostart-Delay=5`) and writes the app's output to `~/.local/state/usb-sentinel-display/autostart.log`.
11. **Check operation, then arm the kiosk.** Start the app manually, plug in a USB drive and check the display.
    ```bash
    uv run usb-sentinel-display run --windowed
    ```
    Press `Ctrl+Q` to quit. Then run `sudo bash ../scripts/kiosk-enable.sh` and reboot; the autostarted full-screen app shows four tiles.
    In `--kiosk` mode there is no `Ctrl+Q`. Recover from the text console (Ctrl+Alt+F3) or SSH with `pkill -f usb-sentinel-display`, or undo autostart and the lockdown with `sudo bash scripts/kiosk-disable.sh`.

## Port calibration

Neither the results directory name nor `result.json` contains physical port information. The app therefore derives the physical port from the sysfs USB topology (the port path). Port paths differ from machine to machine, and even for the same physical port they differ between a USB 2.0 device and a USB 3.0 device (on the Pi 4, USB 2.0 appears as `1-1.1` to `1-1.4` and USB 3.0 as `2-1`, `2-2`, and so on).

1. Start the calibration. No GUI appears.
   ```bash
   cd frontend
   uv run usb-sentinel-display calibrate
   ```
2. Plug USB drives into the ports one at a time. Each time you plug one in, one line is printed in the following format.
   ```text
   sda 1-1.3 ABC123456789
   ```
   From the left, these are the kernel name, the port path, and the serial. Plug in a USB 2.0 drive and a USB 3.0 drive in turn, and note both port paths for each port. Press `Ctrl+C` to finish.
3. Write `[[ports]]` in `~/.config/usb-sentinel/display.toml`. The entries go to tiles 1 to 4 in order of appearance.
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

A disk whose port path is not in the mapping is shown as `P?` on a free tile, with its kernel name appended. If the mapping is empty, every disk is shown as `P?`.

## Configuration file

Place it at `~/.config/usb-sentinel/display.toml`. Without it, the app runs with defaults. `deploy/display.example.toml` has an example with all keys.

| Key | Type | Default | Meaning and validation |
|---|---|---|---|
| `results_dir` | string | `/var/lib/usb-sentinel/results` | The results root. The app starts even if it is missing; it shows a message on the info strip and retries every second |
| `sys_block` | string | `/sys/block` | The sysfs list of block devices. The app starts even if it is missing |
| `unit_template` | string | `usb-sentinel-docker@{kernel}.service` | Template for the inspection unit name. The app does not start unless it contains `{kernel}` |
| `poll_interval_s` | number | `1.0` | Update interval in seconds. `0.2` to `10` |
| `retain_minutes` | integer | `60` | How long to keep showing a result after removal, in minutes. `1` or more |
| `font_family` | string | `Noto Sans CJK JP` | If it is missing, the app prints a warning and uses Tk's default font |
| `language` | string | `en` | The language of the screen at startup (`en`/`ja`). A language chosen with the on-screen language button or the `L` key is recorded in the state file (the "Controls" section) and takes precedence over this setting. Any other value is a startup error |
| `sqlite_path` | string | `/var/lib/usb-sentinel/db/inspections.sqlite` | The backend's SQLite index. Opened read-only. The empty string `""` disables it and uses only the results directory scan. If the file is missing or cannot be opened, the app falls back to the scan automatically (the "Reading the SQLite index" section) |
| `control_dir` | string | `/run/usb-sentinel-control` | Directory of the inspection-mode file read by `run-docker.sh` at each insertion. The only place outside the state directory the app writes to. Must be absolute and outside `results_dir` and the `sqlite_path` directory. `""` disables switching (the button shows "Scan only (locked)") |
| `host_config_dir` | string | `/etc/usb-sentinel` | Directory with the backend `config.toml` and `docker.env`. Read only, to learn whether the administrator allows erasing (`allow_destructive = true` and `USB_SENTINEL_DEVICE_ACCESS=rw`). Otherwise the button is locked |
| `sys_class_input` | string | `/sys/class/input` | sysfs list of input devices. The first start after boot records the devices present; a new one locks erase mode until reboot |
| `[[ports]]` | `label` string, `paths` string array | empty | Tiles 1 to 4 in order of appearance. `label` is 1 to 4 printable ASCII characters. A duplicate `label`, a duplicate `paths` entry, or 5 or more entries is a startup error |

A wrong type or an unknown key makes the app print `Configuration error: ...` to standard error at startup and stop with exit code 1.

Command-line values override the configuration file. `--config` is a common option, so place it before the subcommand.

```bash
uv run usb-sentinel-display --config /path/to/display.toml run --results-dir /path/to/results --sys-block /path/to/sys_block
```

The options of `run` are `--results-dir`, `--sys-block`, `--windowed` or `--kiosk` (mutually exclusive), and `--no-systemctl` (does not call `systemctl`; for development machines. Because the unit state cannot be obtained, a disk that has a `started` row in the SQLite index stays "Scanning" until a result appears).
The options of `calibrate` are `--sys-block` and `--interval` (the check interval in seconds; the default is `1.0`).

## Controls

| Action | Touch | Keyboard |
|---|---|---|
| Select a port | Tap a tile | `1` to `4` |
| Move backward or forward | Tap ◀ ▶ | `←` `→` (`↑` `↓` do the same). With the normal QR shown, this moves between ports. With a detail QR shown, it turns pages (1/3→2/3→3/3→1/3; ◀ goes in reverse). To change ports while a detail QR is shown, tap a tile or press `1` to `4` |
| Switch language | The button at the bottom center left (shows the name of the language to switch to) | `L` |
| Switch between detail QR and normal QR | Tap the QR area (right side) | `Q`. From the normal QR to the first detail QR; from a detail QR back to the normal QR |
| Force reload | None | `F5`. Discards the cache of loaded `result.json` files and the "Broken" records, and reads everything again on the next update |
| Toggle the inspection mode | Tap the mode button (bottom left, lowest row) | `M`. From scan only it opens the code dialog; from "Scan + erase" it returns to scan only at once |
| Enter the code (dialog open) | Tap the four buttons `1` to `4` | `1` to `4` (the numeric keypad also works). The dialog shows `Code: 3 1 4 2`-style digits; enter them in order |
| Cancel the dialog | Tap "Cancel" (top right) | `Esc`. Closes the dialog without counting a wrong code |
| Quit | None | `Ctrl+Q` (disabled with `--kiosk`) |

Touch arrives as a mouse click, so even where touch does not work, every operation can be done from the keyboard alone (a USB numeric keypad also works). The QR mode (normal, or which of the detail codes) is kept when you switch ports.

While the code dialog is open, every other key and tap (port selection, arrows, `L`, `Q`, `F5`) is ignored. The dialog closes by itself after 30 s without a complete code. The code is random per attempt and shown only on the screen, so a device that only types (a BadUSB keyboard) cannot turn erase mode on. Erase mode needs the host to allow erasing (`host_config_dir`), a usable `control_dir`, a display user in the `usb-sentinel` group (log in again after adding it), no input device added since boot (reboot after plugging a keyboard on purpose) and fewer than 3 wrong codes in total. Every attempt is counted in the strike file `$XDG_STATE_HOME/usb-sentinel-display/erase-strikes` (next to the language file) before the code is shown; the administrator clears it with `rm`. The mode is written to `control_dir/mode` as `<mode> <epoch>` with a heartbeat every 5 s; it is reset to scan only at every start and exit and never restored from the previous run or derived from results. Drives inserted while the button showed "Scan + erase" keep that mode until they are removed.

Each time you switch the language, it is recorded in the state file, and at the next startup it takes precedence over `language` in the configuration file. The file is `$XDG_STATE_HOME/usb-sentinel-display/language`, or `~/.local/state/usb-sentinel-display/language` if `XDG_STATE_HOME` is not set. If it cannot be written, the app prints one warning to standard error and the display still switches. Nothing is written to the results directory.

There are three startup modes.

- No option: full screen. `Ctrl+Q` quits.
- `--kiosk`: full screen with `Ctrl+Q` disabled. The `.desktop` file starts the app in this mode. To stop it, run `pkill -f usb-sentinel-display` from another terminal (SSH, for example). On SIGTERM the app exits at its next update.
- `--windowed`: shows a 480x320 window. For development machines.

To restart after changing the configuration file:

1. Stop the app with `pkill -f usb-sentinel-display`.
2. If it runs through autostart (`.desktop`), log in again. If you had started it manually, run `uv run usb-sentinel-display run` again.

## On-screen QR code

The on-screen QR code uses a shortened format (`v:2`) that is separate from `result-qr.png`. To make it easy to read on the LCD, it is limited to 150 bytes, error correction level M, and QR version 8 or lower. `result-qr.png` (`v:1`, up to 2953 bytes, error correction L, with the signature list) remains as it is. Readers tell them apart by `v`.

```json
{"v":2,"r":"I","s":"ABC123456789","t":"20250601T120000Z","d":"0123456789abcdef","st":"m:P,h:P,c:S,s:S","n":0}
```

| Key | Meaning | Value |
|---|---|---|
| `v` | Format version | Fixed at `2` |
| `r` | Verdict | `N`=No Defect, `M`=Malware Detected, `C`=Counterfeit Suspected, `S`=Spec Mismatch, `D`=Defective, `I`=Inconclusive, `?`=unknown |
| `s` | Serial | The first 32 characters of `device.serial` in `result.json`, converted to ASCII. Non-ASCII characters become `?`. If it is empty, the kernel name is put here and `k` is added |
| `k` | Indicates that `s` is a kernel name | `1`. When `s` is a serial, the key itself is absent |
| `t` | Inspection start time (UTC) | `YYYYmmddTHHMMSSZ`. Matches the first 16 characters of the results directory name |
| `d` | First 16 digits of the SHA-256 of `result.json` | Lowercase hex |
| `st` | Step states | Comma-separated `name:state`. The names are `m`=malware, `h`=hash_inventory, `c`=capacity, `s`=speed, `u`=unmount, and the first 3 characters for others. The states are `P`=pass, `F`=fail, `E`=error, `S`=skipped, `N`=not_implemented |
| `n` | Number of malware detections | The number of elements in `details.detections` of the malware step |

### Detail QR (v:3, 3 codes)

This QR code carries numbers such as capacity and speed as material a person can read. Switch between it and the normal QR by tapping the QR area or pressing `Q`, and turn pages with ◀▶ (the same splitting method as the QR codes in a medicine notebook). The authoritative specification is Section 5 of `doc/qr-payload.md`; only the main points are given here.

| Code | Content |
|---|---|
| 1/3 | Identification and verdict (start time, serial, verdict code, elapsed seconds, detection count, ClamAV DB version) |
| 2/3 | Capacity and device (nominal, reported, and measured capacity, capacity classification, excess over the reported capacity, loss rate, link speed, device class, ledger row count) |
| 3/3 | Speed, DB, and ledger (read and write speeds and thresholds, basis of the verdict, severity, ClamAV DB date, ledger digest) |

- All three codes start with `v`=`3`, `p` (`1/3` to `3/3`), and `d` (the first 12 digits of the SHA-256 of `result.json`). The reading order does not matter; combine the three codes that share the same `d`. The procedure for combining them is in Section 5.6 of `doc/qr-payload.md`.
- The constraints are the same as for the normal QR: each code is 150 bytes or less, ASCII only, error correction M, and QR version 8 or lower. If the first code exceeds 150 bytes, the serial is cut to 16 characters. Only a code that still does not fit shows "QR generation failed"; the other codes are displayed.
- An item without a value in `result.json` becomes `null`. All three codes are generated even for results written by the phase 1 backend.
- The screen language does not affect the payload.

### Verifying against an exported result

`result.json` is hashed as the raw bytes of the file, so run the following on the PC that receives the file and compare the output with `d` in the on-screen QR.

```bash
sha256sum result.json | cut -c1-16   # d of the normal QR (16 digits)
sha256sum result.json | cut -c1-12   # d of the detail QR (12 digits)
```

If the 16 digits (12 digits for the detail QR) match, it is the same `result.json`. If you need full verification, use the 64-digit `digest` in `result-qr.png`.

## Meaning of the display

Line 1 of a tile shows the port name and the verdict label, and the background color is the verdict color. The display does not rely on color alone: it also shows a text label and the one-character code of the QR. The labels switch with the screen language. The English labels are shortened forms that fit the tile width (160px, 18px bold); `Spec Mismatch` becomes `Mismatch`.

| Verdict | Background color | Label (Japanese, romanized) | Label (English) | Code |
|---|---|---|---|---|
| No Defect | `#2E7D32` (green) | Ijo nashi | No Defect | N |
| Malware Detected | `#B71C1C` (red) | Marueya kenchi | Malware | M |
| Counterfeit Suspected | `#AD1457` (dark magenta) | Gisou utagai | Counterfeit | C |
| Spec Mismatch | `#E65100` (orange) | Shiyou fuitchi | Mismatch | S |
| Defective | `#6A1B9A` (purple) | Furyou | Defective | D |
| Inconclusive | `#5D4037` (brown) | Hantei fuka | Inconclusive | I |
| Unknown verdict | `#424242` (dark gray) | Hantei fumei | Unknown | ? |

Before a verdict appears and after the disk is removed, the tile shows one of the following state texts (the Japanese UI shows the Japanese equivalent).

| State | Condition | Display |
|---|---|---|
| Not connected | There is no disk on the port and no retained result | "No disk". Gray |
| Waiting to start | A disk is present, the unit has not started, there is no result, and less than 20 seconds have passed since connection | "Starting". Blue |
| Scanning | The inspection unit is `active` or `activating`. Or the SQLite index has a `started` row and `systemctl` does not report the unit's state | "Scanning (N min)". Blue. The trailing "…" moves every second |
| Done | A result is linked and `result.json` could be read | The verdict color and verdict label, and "Done HH:MM" on line 3 |
| No result | A disk is present, the unit is `inactive`, `failed`, or unknown, there is no result, and 20 seconds or more have passed since connection. Even if the SQLite index has a `started` row, a stopped unit skips the waiting state and goes straight to this state (the USB drive may have been disconnected) | "No result". Gray. The info strip shows a message that begins with `journalctl -u <unit name>`. Either the safety guard refused the inspection (exit code 2) or the unit failed to start |
| Broken | `result.json` has been unreadable for 10 seconds (truncated JSON, missing required keys). Or the `result_digest` in the SQLite index does not match the SHA-256 of `result.json` (immediately) | "Broken". Gray |
| Removed | The disk disappeared after completion | The verdict color is darkened, and line 3 shows "Removed HH:MM". The QR code stays displayed. It is retained for `retain_minutes` or until the next disk is plugged in |

The border of a Malware Detected tile alternates between bright red and the background color every second. No sound is played.

## Reading the SQLite index

The backend phase 2 writes one row per inspection to the `inspections` table of `/var/lib/usb-sentinel/db/inspections.sqlite` (`state='started'` at the start, and `state='completed'` after `result.json` is written). The frontend reads this DB as an index. The source of truth remains `result.json` as before, and values from the DB are never shown on the screen.

**What it reads.** Candidates are the rows that started after the connection time of the connected disk (a lower bound on `started_at`, one SELECT statement with placeholders). For each row, the app reads `results_dir/<inspection_id>/result.json` as before, recomputes the SHA-256, and compares it with `result_digest`. A match gives a completed result; a mismatch immediately gives "Broken". A row with `state='started'` whose `result.json` does not exist yet is used to decide between "Scanning" and "No result" (the state table in "Meaning of the display"). Only an `inspection_id` that passes the results directory name format (`<timestamp>-<serial>`) is used, and the `output_dir` column is not used.

**How it opens the DB.** It connects with a `file:...?mode=ro` URI and sets `PRAGMA query_only = ON` and `PRAGMA busy_timeout = 1000`. The only SQL is SELECT, and it never writes to the DB. If `PRAGMA user_version` is greater than the expected value (`2`), it does not read the DB. Version 2 added the `mode` column (`scan_only` / `full`; NULL on rows written before the upgrade); the frontend does not read it, the tile tag comes from `result.json`.

**Fallback.** In the following cases the app does not use the index and switches to the results directory scan as before. The display does not stop, and the info strip shows the reason.

| Info strip (Japanese/English) | Condition |
|---|---|
| The Japanese text meaning "No SQLite index; scanning results dir" / No SQLite index; scanning results dir | The file at `sqlite_path` is missing, or the DB is empty with `user_version` 0 |
| The Japanese text meaning "SQLite index unreadable; scanning results dir" / SQLite index unreadable; scanning results dir | The connection, a PRAGMA, or the SELECT failed. For example a corrupted file, a missing table, no permission, or being unable to create the WAL `-shm` file |
| The Japanese text meaning "SQLite index schema is newer; scanning results dir" / SQLite index schema is newer; scanning results dir | `user_version` is greater than `2`. Update the frontend |

When a message about the results directory or sysfs is present (such as "Results directory not found"), that message takes priority and the SQLite message is not shown.

**Permissions.** The display user must belong to the `usb-sentinel` group and be able to read `/var/lib/usb-sentinel/db` (`2770`). The backend writes in WAL mode, so even a read-only reader must create the `-shm` file, which requires group write permission on the directory. The app never writes to the DB file itself. If the user is not in the group, the result is "SQLite index unreadable" and the app falls back to the scan (step 7 of "Display setup").

**Handling of `started` rows.** When a row with `state='started'` is linked to a connected disk (matched by port path, or by kernel name if there is none), the app shows "Scanning" while `systemctl` does not report the unit's state. If the unit is `inactive` or `failed`, it skips the waiting state even right after connection and shows "No result". If `result.json` exists, the inspection is treated as completed even when the row is still `started` (a failure to record completion).

**Results with no row in the index.** While the index is readable, only the rows of the index are candidates, and scan results are not mixed in. An inspection for which the backend failed to record the start (the `warnings` in `result.json` and the backend's standard output (journal) keep the message `SQLite record failed (start)`) has no row in the index even if `result.json` exists, so it is not displayed, and the disk goes from the waiting state to "No result". In this case, check the backend's journal and, if necessary, turn off the index with `sqlite_path = ""` to go back to the scan.

## Deploying together with the backend

The `Counterfeit Suspected` verdict, the numbers in the detail QR (`v:3`), and the SQLite index are written by backend phase 2. Update the frontend to phase 2 at the same time as the backend. If an old frontend runs against a new backend, `Counterfeit Suspected` gets the verdict code `?` and the label "Unknown", and the SQLite index is not read. Conversely, with a new frontend and an old backend, the numbers in the detail QR become `null`, and the info strip keeps showing the message that there is no SQLite index (`sqlite_path = ""` removes it).

The inspection-mode toggle raises the SQLite schema to `user_version` 2 (`mode` column) and adds `mode` to `result.json` and `md` to detail QR page 1. Update the backend, `scripts/run-docker.sh`, `scripts/install-docker.sh` (tmpfiles entry for `/run/usb-sentinel-control`) and the frontend together: an old frontend against the new backend rejects `user_version` 2 and falls back to scanning the results directory ("SQLite index schema is newer"); a new frontend against an old backend shows no tag on the tiles and `md: null`, and the mode button has no effect on the host.

## Troubleshooting

When the app does not appear after login, read `~/.local/state/usb-sentinel-display/autostart.log` first (the autostart entry redirects the app's output there). If the file is missing, the entry still has `Hidden=true`: run `sudo bash scripts/kiosk-enable.sh`.

The cause and remedy for each message on the info strip.

| Message | Cause | Remedy |
|---|---|---|
| No permission to read results dir (README step 7: usb-sentinel group, re-login) | The display user is not in the `usb-sentinel` group, or has not logged in again | Step 7 of "Display setup". Check the groups with `id` |
| Results directory not found | The directory in `results_dir` does not exist | Check the backend installation (`install-docker.sh`). On a development machine, point to it with `--results-dir` |
| sysfs not found (check sys_block setting) | The directory in `sys_block` does not exist | Check `sys_block` in the settings. On a development machine (macOS), point to a fake tree with `--sys-block` |
| No permission to read sysfs | `sys_block` is not readable | Check the directory permissions |
| A message starting with `journalctl -u usb-sentinel-docker@sdX.service` | A disk is present, but the inspection unit is not running and there is no result | Use `journalctl -u usb-sentinel-docker@sdX.service` to see whether the inspection was refused (exit code 2) or failed to start. With automount enabled, the inspection is refused (step 5) |
| Broken | `result.json` has been unreadable for 10 seconds or more | Check the contents with `cat result.json`. If the backend is still writing, wait. Reload with `F5` |
| Internal error: <exception name> | An exception occurred during an update. The previous display is kept | Look at the stack trace in standard error (`~/.local/state/usb-sentinel-display/autostart.log` for autostart) |
| Internal error persists; check stderr | The same exception occurred for 30 seconds in a row | Same as above |
| Resolution WxH (expected 480x320) | The resolution is not fixed at 480x320 | Step 1 of "Display setup" |
| +N more | 5 or more disks are connected | Only the first 4 are shown as tiles |
| No SQLite index / SQLite index unreadable / SQLite index schema is newer; scanning results dir (the SQLite message keeps appearing) | The index cannot be read and the app has fallen back to the scan. The display continues | Check whether the display user is in the `usb-sentinel` group (`id`), whether the backend has created the DB at the `sqlite_path` path (`ls -l /var/lib/usb-sentinel/db`), and whether the directory is `2770`. On a machine without backend phase 2 or on a development machine, disable the index with `sqlite_path = ""` and the message goes away. Fix "newer schema" by updating the frontend |

The messages correspond in the same order in English (the table in the "Reading the SQLite index" section).

Messages that appear only on standard error. Each line starts with a level and a logger name, such as `WARNING usb_sentinel_display.libs.ui.app:` or `ERROR usb_sentinel_display.libs.cli:`.

| Message | Cause | Remedy |
|---|---|---|
| Failed to save language: <path> (<exception>) | The language record location (`$XDG_STATE_HOME/usb-sentinel-display/language` or `~/.local/state/…`) cannot be written. The display still switches, and the warning appears only once | Check the directory permissions. Even without the record, the app starts with `language` from the configuration file |
| Could not record erase-mode attempt: <reason> | The strike file next to the language file cannot be written. Erase mode stays locked ("Cannot use the wrong-code count; erase locked") | Check the permissions of `~/.local/state/usb-sentinel-display/` |

Messages of the mode button (shown in the info strip for 5 s after pressing it).

| Message | Cause | Remedy |
|---|---|---|
| Erasing is off on this host (admin settings) | `allow_destructive = true` and `USB_SENTINEL_DEVICE_ACCESS=rw` are not both set in `host_config_dir` | Root README, "3. Plug in a USB drive" |
| Cannot read /etc/usb-sentinel settings | `config.toml` or `docker.env` is missing or unreadable | Check `host_config_dir` and the file modes (0644) |
| Mode control dir unusable (group, re-login) | `control_dir` is missing, a symlink, or not writable | Check `/run/usb-sentinel-control` (`root:usb-sentinel 2770`, created by `install-docker.sh`) and that the display user is in the group and has logged in again |
| Mode switching off (control_dir is empty) | `control_dir = ""` | Intended; set the key to re-enable |
| New input device seen; reboot to allow erase | An input device appeared after the first start of this boot | Unplug it and reboot |
| Cannot watch input devices; erase locked | `sys_class_input` cannot be listed | Check the setting and that `/sys/class/input` is readable |
| Wrong code; try again in 60 s / 3 wrong codes; admin must reset to allow erase | A wrong code; three wrong codes in total | Wait; or `rm ~/.local/state/usb-sentinel-display/erase-strikes` and restart |
| Could not turn erase off; it stops within 20 s | The mode file could not be rewritten | The host stops honouring `full` after 20 s; check `control_dir` |
| Inserted drives may still be erased; remove them | Erase mode was turned off while a drive was still being inspected | Remove the drives to stop their inspection |

Problems with the tile display.

| Symptom | Cause | Remedy |
|---|---|---|
| The port name is `P?` with the kernel name appended | The port path is not in the mapping | The "Port calibration" section. If the mapping is empty, every disk is `P?` |
| Japanese text appears as tofu (□), or the startup message `Font Noto Sans CJK JP not found; using ...` appears | The font is not installed | Step 3 |
| The app does not go full screen or does not start | The path in the `.desktop` file is wrong, or `uv sync` has not been run | Run `Exec` from `~/.config/autostart/usb-sentinel-display.desktop` directly in a terminal and look at standard error |

## Development

Run every command in `frontend/`. Development and testing are completed on macOS. sysfs is replaced with a fake tree in `tmp_path`, and `systemctl` with `FakeRunner`.

```bash
cd frontend
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
uv run python -m usb_sentinel_display --help
```

Every command must exit with code 0. ty exits with code 1 even for a single warning.

- Nothing outside `ui/` imports `tkinter`. The logic layer (directly under `libs/`) passes `pytest` without a GUI. `ui/i18n.py` and `ui/strip.py` also do not import `tkinter`.
- UI text is kept in two sets, ja and en, in `ui/i18n.py`, and the GUI layer refers to it by key. The QR payload (`qr_payload.py`) does not depend on the language.
- The tests for the SQLite index (`store.py`) are in `tests/test_store.py`, which uses a string copy of the backend's schema v1. It does not `import` the backend.
- Smoke tests that need `tk.Tk()` carry `@pytest.mark.gui`. You can select them with `uv run pytest -m gui`. They are skipped automatically where `DISPLAY` is not set.

### Checking the screen

Create a fake sysfs tree and sample results, and check the screen in a window.

```bash
# Create a fake tree and sample results in /tmp/usb-sentinel-fixtures (--scene a|b|c).
# Also creates control/, host/ (ceiling on), input/input0/ and state/ for the mode button;
# --ceiling off locks the button, --strikes 3 pre-fills the wrong-code count.
uv run python scripts/make-fixtures.py /tmp/usb-sentinel-fixtures --scene a

# Start in a window
uv run usb-sentinel-display --config /tmp/usb-sentinel-fixtures/display.toml run --windowed --no-systemctl

# Take a screenshot after 12 seconds (uses macOS screencapture)
uv run python scripts/dev-screenshot.py --fixtures /tmp/usb-sentinel-fixtures --scene a --select 1 --out /tmp/scene-a.png

# Mode scenes: --mode-scene none|scan_only|confirm|full|locked|lockout, --code 3142 fixes the dialog code.
uv run python scripts/dev-screenshot.py --fixtures /tmp/usb-sentinel-fixtures --mode-scene confirm --out /tmp/confirm.png
```

Scene A has four disks (No Defect, Malware Detected, scanning, broken result), and scene B has two disks (Spec Mismatch, Defective) with P3 and P4 not connected. `dev-screenshot.py` fakes the output of `systemctl` to show "Scanning", points `XDG_STATE_HOME` at `<fixtures>/state` and uses the sibling modules `scripts/fixtures_mode.py` and `scripts/dev_mode_scenes.py`. On a Retina screen `screencapture` writes 960x640; shrink with `sips -z 320 480 <png>`. With `--geometry 640x400` you can check the resolution warning, and with `--results-dir /nonexistent` you can check "Results directory not found". The `display.toml` written by `make-fixtures.py` disables the index with `sqlite_path = ""`, so no SQLite message appears on a development machine.

## On-device checks

Items that cannot be checked on the development machine (macOS) and must be verified on the Pi (aarch64, Ubuntu Desktop).

1. `import tkinter` works with uv-managed Python 3.13. If it does not, switch to the apt setup in step 8.
2. Which layer of the resolution fix takes effect. Which of (a), (b), and (c) in step 1 produces 480x320. Judge by the absence of the app's resolution warning.
3. The touch driver works on Ubuntu, taps arrive as `<Button-1>`, and the selection changes with the tiles and ◀▶. The system can be operated from the keyboard even if it does not work.
4. The `st_mtime` of the port-path directory in sysfs (for example the real target of `/sys/bus/usb/devices/1-1.3`) becomes the time the USB drive was plugged in. Compare before and after insertion with `stat`. If it does not, the app falls back to the system boot time, so every result written after boot becomes a candidate (results from before boot are not picked up).
5. When several units are passed to `systemctl show -p Id -p ActiveState`, the output is blocks that begin with `Id=`, separated by blank lines.
6. Whether an inspection refusal (exit code 2) can be told apart from "No result". If `systemctl show -p ExecMainStatus` remains available after the unit exits, the display of "inspection refused" can be separated.
7. The permissions of the results directories and files created by the container are `0755`/`0644`, and a display user in the `usb-sentinel` group can read `result.json`.
8. Line spacing in Noto Sans CJK JP. When the sum of the 18px bold line and two 14px lines exceeds 58px, the layout switches to two lines (the time goes to the end of line 2 and the serial is shortened). How that looks.
9. With `--kiosk`, `Ctrl+Q` does not work, and the app exits at the next update after `pkill -f usb-sentinel-display`. On a development machine, exit by SIGTERM (exit code 0) was confirmed with `--windowed`.
10. Keeping focus in full screen (reacquiring it after `<FocusOut>`) and hiding the cursor.
11. The `journalctl -u usb-sentinel-docker@sdX.service` message on the info strip (about 50 characters) fits in 320px. If it does not fit, the right side is cut off.
12. Scan the QR code with a smartphone and confirm that `sha256sum result.json | cut -c1-16` matches `d`. If scanning is not stable, one option is to raise error correction to Q (in that case, lower the limit to 108 bytes).
13. The three detail QR codes (maximum QR version 8, 285px) are readable on the LCD.
14. The verdict labels fit the tile width in Noto Sans CJK JP. Run `uv run pytest -q -m gui -k verdict_labels_fit_tile` once on the device. In measurements on the development machine, the Japanese label for Malware Detected (141px, limit 144px) has the least margin.
15. With a display user in the `usb-sentinel` group, `inspections.sqlite` opens with `mode=ro` and the info strip shows no SQLite message. If it does, check the permissions (`2770`) of `/var/lib/usb-sentinel/db`.
16. The mode button and the code dialog (18 px bold code, four 104x108 px buttons) are readable and tappable on the panel; the Japanese strings fit with Noto Sans CJK JP. The mode file written by the display is accepted by `run-docker.sh` (`journalctl` shows `notice: display requested full inspection`). See the root README, "Checks on the Raspberry Pi".
