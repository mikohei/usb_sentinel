[日本語版](qr-payload.ja.md)

# QR payload specification (file v:3, normal QR v:2, detail QR v:3) and the SQLite mapping

This document defines the content of the QR codes that usb_sentinel produces, and how they map to `result.json` and the SQLite database on the Pi. Its purpose is to let the developer of the reader app (Flutter) work without reading the backend code.

| Term | Meaning |
|---|---|
| backend | The inspection program that runs in a Docker container on the Pi (`backend/`) |
| frontend | The program that shows results and QR codes on the Pi's 3.5-inch display (`frontend/`) |
| reader app | The Flutter app that reads QR codes and links them to case information (the reader of this specification) |

## 1. Purpose and the three kinds of QR

| Kind | Location | `v` | Characters | Limit | Use |
|---|---|---|---|---|---|
| File QR | `result-qr.png` in the results directory | `3` | UTF-8 | 2953 bytes, error correction L | The full text to put on the transfer medium. Includes numbers, reason sentences, and signatures |
| Normal QR | Screen (frontend) | `2` | ASCII | 150 bytes, error correction M | Identifies the inspection. Verdict, serial, start time, digest, step states |
| Detail QR | Screen (frontend), 3 codes shown with page turning | `3` | ASCII | 150 bytes each, error correction M | Numbers a person uses to judge (capacity, speed, inventory). The 3 codes are combined into one record |

The reader app first parses the content as JSON and branches on `v`. If `v` is `2`, it is a normal QR. If `v` is `3` and the `p` key exists, it is a detail QR. If `v` is `3` and `p` does not exist, it is a file QR. An unknown `v` is treated as "unsupported format", and the raw string is saved.

QR codes that point to the same inspection are matched by three values: the start time, the serial, and the digest of `result.json` (Section 7). The digest is 64 digits in the file QR, the first 16 digits in the normal QR, and the first 12 digits in the detail QR; the shorter ones are prefixes of the longer ones.

## 2. Common rules

- The format is a single JSON object. `json.dumps(..., separators=(",", ":"))` removes the spaces after separators.
- Screen QR codes (normal and detail) are ASCII only (`ensure_ascii=True`). A non-ASCII serial is replaced with `?`. The file QR is UTF-8 (`ensure_ascii=False`), and the reason sentences are in English, so they are almost all ASCII. Non-ASCII characters that come from a serial or from the output of an external command go in as UTF-8 as they are.
- An item with no value is written as `null`, and the key is not omitted. The reader app can tell "no value" from "missed reading". The only exception is `k` in the normal and detail QR: it is `1` when `s` is a kernel name, and the key itself is omitted when `s` is a serial.
- All times are UTC. The file QR and `result.json` use ISO 8601 (`2025-06-01T12:00:00.123456+00:00`), and the screen QR uses the 16 characters `YYYYmmddTHHMMSSZ` (`20250601T120000Z`).
- Capacity is in bytes (decimal). Only the detail QR uses MB (10^6 bytes, rounded down), to fit in 150 bytes; the precision is 1MB (0.003% for 32GB). The exact byte values are in `result.json` and SQLite.
- Speed is in MB/s (10^6 bytes/second).
- Ratios (such as `lost_ratio`) are decimals rounded to 4 places.
- Codes for verdicts, step states, classes, and so on are looked up in the code tables in Section 6. A value not in a table is treated as "unknown", and the raw value is kept.

## 3. File QR v:3 (`result-qr.png`)

It goes in `result-qr.png` in the results directory (`<timestamp>-<serial>/`). All keys of v:1 remain, and the items added in phase 2 are included. `v` is `3` (`2` is used by the normal QR).

### 3.1 Key table

| Key | Type | Source (`result.json`) | Change from v:1 |
|---|---|---|---|
| `v` | int | — | `1`→`3` |
| `verdict` | str | `verdict` (the verdict name as a string; Section 6.1) | — |
| `mode` | str | `mode`: `scan_only` / `full`, the mode this inspection ran in after the host ceiling (Section 6.9). Never cut by the shrinking rules | Added (inspection-mode toggle) |
| `serial` | str | `device.serial` | — |
| `kernel_name` | str | `device.kernel_name` (such as `sda`) | Added |
| `port_path` | str \| null | `device.port_path` (such as `1-1.3`) | Added |
| `started_at`, `finished_at` | str | Same names | Added |
| `elapsed_s` | float \| null | Same name (total seconds of the inspection, 2 decimal places) | Added |
| `steps` | dict | `steps[].name → status` (Section 6.2) | — |
| `clamav_db` | str \| null | `steps[malware].details.database.db_version` | — |
| `clamav_db_date` | str \| null | The first 10 characters of `…database.db_date` (`YYYY-MM-DD`) | Added |
| `digest` | str | SHA-256 of `result.json` (64 digits) | — |
| `malware_count` | int | `len(steps[malware].details.detections)` | — |
| `signatures` | list[str] | Detected signature names with duplicates removed | — |
| `advertised_over` | bool | `advertised_check.exceeded` (the non-destructive check of the advertised capacity). `false` if `nominal_bytes` is not set | Added |
| `capacity` | dict \| null | `nominal_bytes`, `advertised_bytes`, `actual_bytes`, `kind`, `lost_ratio`, `counterfeit_reasons`, and `probe_verdict_line` of `steps[capacity].details`. `null` if there is no capacity step, or its status is neither PASS nor FAIL (SKIPPED or ERROR). Even when the dict exists, individual values can be `null` | Added |
| `link_speed_mbps` | int \| null | `device.link_speed_mbps` (USB link speed, Mbps) | Added |
| `device_class` | str \| null | `steps[speed].details.device_class` (the key from Section 6.4) | Added |
| `speed` | dict \| null | `read_mb_s`, `write_mb_s`, `threshold_read_mb_s`, `threshold_write_mb_s`, `basis_read`, `basis_write`, `severity`. `null` if there is no fio measurement | Added |
| `reason_codes` | list[str] | Same name (Section 6.8) | Added |
| `reasons` | list[str] | Same name (reason sentences in English; see Section 11) | Added |
| `hash_inventory_digest` | str \| null | `steps[hash_inventory].details.digest` (SHA-256 of `hash-inventory.tsv`, 64 digits) | Added |
| `hash_inventory_lines` | int \| null | `…details.lines` (the number of rows in the inventory) | Added |
| `truncated` | bool | `true` when something was cut. `false` when nothing was cut (the key is always present) | — |

Contents of `capacity`:

| Key | Type | Meaning |
|---|---|---|
| `nominal_bytes` | int \| null | The configured nominal capacity (the value on the product page). `null` if not set |
| `advertised_bytes` | int | The advertised capacity reported by lsblk |
| `actual_bytes` | int \| null | The measured capacity |
| `kind` | str \| null | `ok` / `short` / `defect` / `counterfeit` / `unverified` (Section 6.5) |
| `lost_ratio` | float \| null | The ratio of sectors lost in f3read. `null` in f3probe mode |
| `counterfeit_reasons` | list[str] \| null | Reason codes for counterfeit suspicion (Section 6.7). `[]` if there is no suspicion |
| `probe_verdict_line` | str \| null | The `Good news:` / `Bad news:` line of f3probe. `null` in f3write mode |

Contents of `speed`:

| Key | Type | Meaning |
|---|---|---|
| `read_mb_s`, `write_mb_s` | float \| null | Values measured by fio |
| `threshold_read_mb_s`, `threshold_write_mb_s` | float \| null | The thresholds for the verdict (reference value × `pass_ratio`). `null` if not judged |
| `basis_read`, `basis_write` | str \| null | The basis of the reference. `nominal` (nominal value) / `class` (class minimum) / `none` |
| `severity` | str \| null | The severity when FAIL. `severe` / `minor`. `null` when not FAIL |

### 3.2 Size limit and the cutting order

The limit is 2953 bytes (the maximum of QR byte mode, error correction L). A typical inspection (5 steps, 1 reason, a 12-character serial) fits in about 1.2KB. When the content exceeds the limit, it is shrunk in the following order; after each stage the size is recomputed, and the process stops as soon as it is at or below the limit. If anything is cut, `truncated` is set to `true`.

1. Cut `signatures` to 3 entries.
2. Cut each element of `reasons` to 120 characters, and append `…` to the end of the cut elements.
3. Reduce `signatures` one by one down to 0 entries.
4. Empty `reasons` (`reason_codes` remains, so the reasons can still be traced by code).
5. Cut `serial` to 128 characters.
6. Set `capacity.probe_verdict_line` to `null`.
7. If it still exceeds the limit, no QR is generated and an error is raised (the inspection itself has completed up to `result.json`).

When `truncated` is `true`, the reader app shows that signatures and reason sentences are partial, and prompts the user to check `result.json` for the full text. `malware_count` is never cut, so the detection count is always correct.

### 3.3 Differences from v:1

- `v` changed from `1` to `3`.
- Added keys: `kernel_name`, `port_path`, `started_at`, `finished_at`, `elapsed_s`, `clamav_db_date`, `advertised_over`, `capacity`, `link_speed_mbps`, `device_class`, `speed`, `reason_codes`, `reasons`, `hash_inventory_digest`, `hash_inventory_lines`.
- `truncated` is output as `false` even when nothing is cut (v:1 output `true` only when something was cut).
- `Counterfeit Suspected` was added to `verdict`.
- The meaning and format of the existing keys (`verdict`, `serial`, `steps`, `clamav_db`, `digest`, `malware_count`, `signatures`) are unchanged.

## 4. Normal QR v:2 (screen)

The identification QR that the frontend shows on the screen. 150 bytes or less, ASCII only, error correction M, QR version 8 or lower.

```json
{"v":2,"r":"I","s":"ABC123456789","t":"20250601T120000Z","d":"0123456789abcdef","st":"m:P,h:P,c:S,s:S","n":0}
```

| Key | Meaning | Value | Max bytes (value only) |
|---|---|---|---|
| `v` | Format version | Fixed at `2` | 1 |
| `r` | Verdict | `N`=No Defect, `n`=No Defect (Scan Only), `M`=Malware Detected, `C`=Counterfeit Suspected, `S`=Spec Mismatch, `D`=Defective, `I`=Inconclusive. A verdict not in the mapping is `?` | 1 |
| `s` | Serial | The first 32 characters of `device.serial` in `result.json`, converted to ASCII (non-ASCII becomes `?`). If it is empty, put `device.kernel_name` here and set `k` to `1` | 32 |
| `k` | Indicates that `s` is a kernel name | `1`. The key itself is omitted when `s` is a serial | 1 |
| `t` | Inspection start time (UTC) | The 16 characters of `started_at` converted to `YYYYmmddTHHMMSSZ`. Matches the first 16 characters of the results directory name | 16 |
| `d` | First 16 digits of the SHA-256 of `result.json` | Lowercase hex | 16 |
| `st` | Step states | `name:state` separated by commas. The names are `m`=malware, `h`=hash_inventory, `c`=capacity, `s`=speed, `u`=unmount, and the first 3 characters for others. The states are `P`=pass, `F`=fail, `E`=error, `S`=skipped, `N`=not_implemented | About 30 |
| `n` | Number of malware detections | The number of elements in `steps[malware].details.detections`. `0` if absent | 4 |

- If the payload exceeds 150 bytes, `s` is cut to 16 characters, and if it still exceeds, `st` is dropped. After ASCII conversion `s` is at most 32 bytes, so this is reached only if keys are added in the future.
- The step order is the order of the `steps` array in `result.json`.
- `C` (Counterfeit Suspected) in `r` was added in phase 2. A frontend older than that shows `?`.

## 5. Detail QR v:3 (screen, 3 codes)

The frontend shows these as the pages after the normal QR. As in the medicine notebook QR (the JAHIS standard), each code carries a common header (version, code number / total, identifier), and the codes are combined regardless of reading order. The number of codes is fixed at 3 (2 codes cannot hold the capacity and speed numbers). The constraints are the same as for the normal QR: each code is 150 bytes or less, ASCII only, error correction M, and QR version 8 or lower.

### 5.1 Common header (all codes, placed first)

| Key | Type | Value |
|---|---|---|
| `v` | int | `3` |
| `p` | str | `"1/3"`, `"2/3"`, `"3/3"` |
| `d` | str | The first 12 digits of the SHA-256 of `result.json`. Matches the first 12 digits of `d` (16 digits) of the normal QR |

### 5.2 Code 1 (identification and verdict)

| Key | Type | Source | Max bytes |
|---|---|---|---|
| `t` | str | Start time `YYYYmmddTHHMMSSZ` (same as the normal QR) | 16 |
| `s` | str | The serial converted to ASCII, 32 characters (same as the normal QR) | 32 |
| `k` | int | `1` if `s` is a kernel name; otherwise the key is omitted | 1 |
| `r` | str | Verdict code (`N`/`n`/`M`/`C`/`S`/`D`/`I`) | 1 |
| `e` | int | `elapsed_s` rounded to whole seconds | 6 |
| `m` | int | Number of malware detections | 4 |
| `dv` | int \| null | ClamAV DB version (`db_version` as an integer; `null` on failure) | 6 |
| `md` | str \| null | Inspection mode (Section 6.9): `s` = `scan_only`, `f` = `full`, `null` when `result.json` has no `mode` (written before the toggle) | 1 |

Byte budget of code 1: with the header, a 32-character serial, `k`, six-digit `e`, four-digit `m` and six-digit `dv`, the worst case was 137 bytes; `md` adds `,"md":"s"` (10 bytes), so the worst case is now 147 bytes, still under the 150-byte limit without cutting `s`.

### 5.3 Code 2 (capacity and device)

| Key | Type | Source | Unit / format |
|---|---|---|---|
| `cn` | int \| null | `advertised_check.nominal_bytes` (the configured nominal capacity; present even when the capacity step is SKIPPED) | MB (10^6 bytes, rounded down) |
| `ca` | int | `device.advertised_bytes` (advertised capacity) | MB |
| `cx` | int \| null | `steps[capacity].details.actual_bytes` (measured capacity) | MB |
| `ck` | str | One-character capacity classification (Section 6.5). `-`=ok, `S`=short, `D`=defect, `C`=counterfeit, `U`=unverified, `?`=the step is neither PASS nor FAIL | 1 |
| `co` | int \| null | Non-destructive check of the advertised capacity. `1`=the advertised capacity exceeds nominal × `over_ratio`, `0`=no excess, `null`=`nominal_bytes` is not set. The value is present even when the capacity step is SKIPPED | 1 |
| `cl` | float \| null | `lost_ratio` (4 decimal places) | Ratio |
| `ls` | int \| null | `device.link_speed_mbps` | Mbps |
| `dc` | str \| null | Two-character code of `device_class` (Section 6.4) | 2 |
| `hn` | int \| null | `steps[hash_inventory].details.lines` (the number of inventory rows) | Rows |

An excess in capacity is included in the `C` of `ck`; check `counterfeit_reasons` in `result.json` for the breakdown.

### 5.4 Code 3 (speed, DB, and inventory)

| Key | Type | Source | Unit |
|---|---|---|---|
| `sr`, `sw` | float \| null | `steps[speed].details.read.mb_s`, `write.mb_s` (1 decimal place) | MB/s |
| `tr`, `tw` | float \| null | `threshold_read_mb_s`, `threshold_write_mb_s` (1 decimal place) | MB/s |
| `sb` | str | Basis of the verdict. `n`=nominal, `c`=class, `-`=none. When read and write differ, read takes priority; check `result.json` for write | 1 |
| `sv` | str | Severity. `s`=severe, `m`=minor, `-`=none | 1 |
| `db` | str \| null | ClamAV DB date `YYYYMMDD` | 8 |
| `hd` | str \| null | The first 16 digits of `steps[hash_inventory].details.digest` | 16 |

The reasons for the verdict are not given a separate key. They can be reconstructed from `ck` (capacity), `sv` (speed), `m` (malware), and `st` (step states) of the normal QR.

### 5.5 Cutting rules

- If code 1 exceeds 150 bytes, `s` is cut to 16 characters (same as the normal QR). If it still exceeds, only that code becomes "QR generation failed", and the other codes are displayed.
- Codes 2 and 3 fit in 150 bytes even in the worst case, so they have no cutting rules.
- An item with no value is written as `null`. The only key that is omitted is `k` in code 1.

### 5.6 Combining procedure and verification

1. Collect the QR codes with `v == 3` and a `p` key.
2. Put those with the same `d` in one group. Codes with a different `d` belong to different inspections and are not mixed. Even when the same sample is inspected twice, `d` differs for each `result.json`.
3. When the denominator of `p` in the group is 3 and the numerators `1`, `2`, and `3` are all present, combine them.
4. If the same `p` is read twice, confirm that the contents match, and prompt the user to read again if they differ.
5. If the normal QR (v:2) has also been read, confirm that the first 12 digits of its `d` match the `d` of the detail QR.

### 5.7 Recommendations for the reading UI

- When all 3 codes have been read, combine them automatically, and show progress for each code, such as "1/3 read".
- When a code with a different `d` is read, warn "This is the QR of a different inspection" and let the user choose whether to discard the current group.
- Let the user read normal QR and detail QR in either order. An operation is also possible in which the normal QR alone identifies the inspection and the numbers are added later from the file QR.
- Do not show the numbers as raw codes; convert them to Japanese (or English) with the tables in Section 6 for display.

### 5.8 Frontend display controls (supplement)

This is not part of the payload specification; it supplements how the frontend presents the detail QR. It does not affect the implementation of the reader app.

- Switching between the normal QR (v:2) and the detail QR (v:3) is done by tapping the QR area (the right side of the screen) or pressing `Q`. Switching from the normal QR shows code 1 of the detail QR.
- While a detail QR is shown, the ◀▶ buttons and the arrow keys turn pages (1/3→2/3→3/3→1/3; ◀ goes in reverse). Ports are switched by tapping a tile or pressing `1` to `4`.
- The info strip shows "Detail n/3" (the Japanese display shows the equivalent text in Japanese) and `d` (12 digits).
- The screen language (Japanese / English) does not affect the payload. `d` does not change with the language either.
- If code 1 still does not fit in 150 bytes after the cutting rules (Section 5.5) are applied, "QR generation failed" is shown for that code only, and the other codes are displayed as they are.
- The English tile labels are shortened forms that fit the width (160px, 18px bold): `Malware Detected` is shown as "Malware", `Counterfeit Suspected` as "Counterfeit", and `Spec Mismatch` as "Mismatch". The reader app may use the full names of Section 6.1 (`r` in the payload does not change).

## 6. Code tables

### 6.1 Verdict (`r`, `verdict`, `verdict_code`)

| Code | `verdict` string | Japanese label (romanized) | Meaning |
|---|---|---|---|
| `N` | `No Defect` | Fuguai nashi | All steps PASS |
| `n` | `No Defect (Scan Only)` | Ijo nashi (kensa nomi) | Scan-only mode; every step except capacity and speed PASS, and those two SKIPPED only for the scan-only reason |
| `M` | `Malware Detected` | Marueya kenchi | Detected by ClamAV. Takes priority over other failures |
| `C` | `Counterfeit Suspected` | Gisou utagai | Any of: the advertised or real capacity exceeds 107% of the nominal capacity, f3probe `Bad news`, 50% or more lost. This is a suspicion, not a conclusion |
| `S` | `Spec Mismatch` | Shiyou fuitchi | Insufficient capacity or insufficient speed |
| `D` | `Defective` | Furyou | The capacity is sufficient, but f3read detected lost, corrupted, changed, or overwritten sectors |
| `I` | `Inconclusive` | Hantei fuka | ERROR, SKIPPED, or unimplemented steps remain |

The priority is `M` > `C` > `D` > `S` > `N` > `n`, and if any ERROR or SKIPPED remains the verdict is `I`.

Request to the frontend (phase 2): show `Counterfeit Suspected` as the Japanese label for "counterfeit suspected" in Japanese display and as "Counterfeit Suspected" in English display, with the dark magenta `#AD1457`, which can be told apart from the red `#B71C1C` for Malware Detected and the purple `#6A1B9A` for Defective. Using the same colors in the reader app keeps it consistent with the screen.

### 6.2 Step state (state in `st`, values of `steps`, `step_*` columns)

| Code | Value in `result.json` | Meaning |
|---|---|---|
| `P` | `pass` | Passed |
| `F` | `fail` | Failed (affects the verdict) |
| `E` | `error` | Could not run, or timed out (`Inconclusive`) |
| `S` | `skipped` | Not run (after a malware detection, with `allow_destructive = false`, and so on) |
| `N` | `not_implemented` | Not implemented |

### 6.3 Step names (names in `st`, abbreviations in `reason_codes`)

| Code | Step name | Content |
|---|---|---|
| `m` | `malware` | ClamAV scan |
| `h` | `hash_inventory` | SHA-256 hash inventory (`hash-inventory.tsv`) |
| `c` | `capacity` | Capacity verification (f3write + f3read, or f3probe) |
| `s` | `speed` | Speed verification (fio) |
| `u` | `unmount` | Unmount |

In `st` of the normal QR, a name not in the table uses its first 3 characters, and in `reason_codes` it uses its first character.

### 6.4 Device class (`dc`, `device_class`)

| 2-character code | `device_class` | Class | Minimum read / write (MB/s) |
|---|---|---|---|
| `h5` | `hdd_5400` | HDD 5400rpm | 80 / 80 |
| `h7` | `hdd_7200` | HDD 7200rpm | 120 / 120 |
| `ss` | `sata_ssd` | SATA SSD | 400 / 400 |
| `ns` | `nvme_ssd` | NVMe / USB3.2 SSD | 500 / 500 |
| `s1` | `sd_c10` | SD Class10 / U1 | 25 / 10 |
| `s3` | `sd_u3` | SD U3 / V30 | 90 / 30 |
| `u2` | `usb2_flash` | USB2.0 flash | 15 / 5 |
| `u3` | `usb3_flash` | USB3.0 flash | 50 / 15 |

`device_class_basis` (`result.json` and SQLite) is the origin of the class, and is one of `config` (the configured value), `link_speed` (inferred from the USB link speed: `usb2_flash` at 480Mbps and `usb3_flash` at 5000Mbps or more), and `unknown`.

### 6.5 Capacity classification (`ck`, `kind`, `capacity_kind`)

| `ck` | `kind` | Meaning |
|---|---|---|
| `-` | `ok` | Passed |
| `S` | `short` | Insufficient capacity (the real capacity is below 93% of the reference) |
| `D` | `defect` | The capacity is sufficient, but there are bad sectors |
| `C` | `counterfeit` | Counterfeit suspected (the breakdown is in `counterfeit_reasons`) |
| `U` | `unverified` | The verified area does not reach the threshold (a nearly full sample in f3write mode. ERROR) |
| `?` | — | The capacity step is neither PASS nor FAIL (SKIPPED, ERROR, or not implemented), so there is no `kind` |

### 6.6 Excess over the advertised capacity (`co`, `advertised_over`)

| `co` | `advertised_over` | Meaning |
|---|---|---|
| `1` | `true` | The advertised capacity from lsblk exceeded nominal × `over_ratio` (default 1.07) |
| `0` | `false` | It did not exceed |
| `null` | `false` | `nominal_bytes` is not set, so no comparison was made |

This check is made right at the start of the inspection, before any mounting or scanning, and is recorded even with `allow_destructive = false` and even when a malware detection stops the inspection. `advertised_over` of the file QR is `false` even when `nominal_bytes` is not set, so to tell "not set" apart, look at whether `capacity.nominal_bytes` (or `cn` of the detail QR) is `null`.

### 6.7 Speed basis and severity (`sb`, `sv`), counterfeit reasons (`counterfeit_reasons`)

| `sb` | `basis_read` / `basis_write` | Meaning |
|---|---|---|
| `n` | `nominal` | The configured nominal speed was used as the reference |
| `c` | `class` | The minimum for the device class was used as the reference |
| `-` | `none` | There is no reference, so it is not judged |

| `sv` | `severity` | Meaning |
|---|---|---|
| `s` | `severe` | The measured value is below 50% of the reference (the summary starts with `Speed shortfall (severe)`) |
| `m` | `minor` | 50% or more and below 80% (`Speed shortfall (minor)`) |
| `-` | `null` | Not FAIL |

| Value of `counterfeit_reasons` | Meaning |
|---|---|
| `over_advertised` | The advertised capacity (lsblk) exceeded nominal × `over_ratio` |
| `over_actual` | The measured capacity exceeded nominal × `over_ratio` |
| `probe_bad_news` | f3probe reported `Bad news:` |
| `lost_ratio` | f3read lost 50% or more of the sectors |

Several can be set at the same time. The order is fixed (`over_advertised` → `over_actual` → `probe_bad_news` → `lost_ratio`).

### 6.8 Verdict reason codes (`reason_codes`)

The form is `<step abbreviation>:<reason>`. The order follows the order of `steps` in `result.json`, and `c:over`, which comes from the non-destructive check, is placed first.

| Code | Meaning |
|---|---|
| `m:found` | Malware detected (malware FAIL) |
| `c:over` | The advertised or measured capacity exceeded nominal × `over_ratio` (counted once even if both the non-destructive check and the capacity step set it) |
| `c:probe` | f3probe `Bad news` |
| `c:lost` | 50% or more lost sectors |
| `c:short` | Insufficient capacity |
| `c:defect` | Bad sectors (`Defective`) |
| `s:severe` | Insufficient speed, far below |
| `s:minor` | Insufficient speed, slightly below |
| `<abbreviation>:timeout` | That step timed out (`m:timeout`, `c:timeout`, `s:timeout`, `h:timeout`) |
| `<abbreviation>:error` | That step is ERROR (other than a timeout) |
| `<abbreviation>:skipped` | That step is SKIPPED |
| `<abbreviation>:not_implemented` | That step is not implemented |

Example: an inspection with `nominal_bytes` set, `allow_destructive = false`, and an advertised capacity in excess gives `["c:over", "c:skipped", "s:skipped"]`.

### 6.9 Inspection mode (`md`, `mode`)

| `md` | `mode` (`result.json`, file QR, SQLite) | Meaning |
|---|---|---|
| `s` | `scan_only` | ClamAV scan and hash inventory only; capacity and speed `SKIPPED` ("Inspection mode is scan_only"); the drive keeps its data |
| `f` | `full` | The host allowed erasing and the display was in "Scan + erase" when the drive was inserted; capacity and speed ran (or failed for another reason) |
| `null` | absent | `result.json` written before the toggle existed |

The mode is the effective one after the host ceiling (`allow_destructive = false` forces `scan_only`), so `f` means the destructive steps were reachable, not that they wrote.

## 7. Mapping to `result.json`

The rows are arranged by the keys of the detail QR. `…details` is the `details` of the relevant step in `steps[]`.

| Item | Detail QR v:3 | Normal QR v:2 | File QR v:3 | `result.json` | SQLite column | Unit |
|---|---|---|---|---|---|---|
| Format version | `v`=3 | `v`=2 | `v`=3 | — | — | — |
| Code number | `p` | — | — | — | — | — |
| Digest | `d` (12 digits) | `d` (16 digits) | `digest` (64 digits) | (SHA-256 of the file) | `result_digest` | hex |
| Start time | `t` | `t` | `started_at` | `started_at` | `started_at`, `started_compact` | UTC |
| End time | — | — | `finished_at` | `finished_at` | `finished_at` | UTC |
| Elapsed time | `e` | — | `elapsed_s` | `elapsed_s` | `elapsed_s` | Seconds |
| Serial | `s`, `k` | `s`, `k` | `serial`, `kernel_name` | `device.serial`, `device.kernel_name` | `serial`, `kernel_name` | — |
| Port path | — | — | `port_path` | `device.port_path` | `port_path` | — |
| Verdict | `r` | `r` | `verdict` | `verdict` | `verdict`, `verdict_code` | — |
| Inspection mode | `md` (1 character) | — | `mode` | `mode` | `mode` | — |
| Step states | — | `st` | `steps` | `steps[].status` | `step_*` | — |
| Malware detection count | `m` | `n` | `malware_count` | `len(steps[malware].details.detections)` | `malware_count` | Count |
| ClamAV DB version | `dv` | — | `clamav_db` | `…details.database.db_version` | `clamav_db_version` | — |
| ClamAV DB date | `db` | — | `clamav_db_date` | `…details.database.db_date` | `clamav_db_date` | Date |
| Nominal capacity | `cn` | — | `capacity.nominal_bytes` | `advertised_check.nominal_bytes` (always present), `steps[capacity].details.nominal_bytes` (when the capacity step ran) | `nominal_bytes` | QR: MB, others: bytes |
| Advertised capacity | `ca` | — | `capacity.advertised_bytes` | `device.advertised_bytes` | `advertised_bytes` | Same as above |
| Measured capacity | `cx` | — | `capacity.actual_bytes` | `…details.actual_bytes` | `actual_bytes` | Same as above |
| Capacity classification | `ck` | — | `capacity.kind` | `…details.kind` | `capacity_kind` | — |
| Excess over the advertised capacity (non-destructive) | `co` | — | `advertised_over` | `advertised_check.exceeded` | `advertised_over` | 0/1 |
| Lost ratio | `cl` | — | `capacity.lost_ratio` | `…details.lost_ratio` | `lost_ratio` | Ratio |
| Counterfeit reasons | (`ck`=`C`) | — | `capacity.counterfeit_reasons` | `…details.counterfeit_reasons` | `counterfeit_reasons` | — |
| f3probe verdict line | — | — | `capacity.probe_verdict_line` | `…details.probe_verdict_line` | `probe_verdict_line` | — |
| Link speed | `ls` | — | `link_speed_mbps` | `device.link_speed_mbps` | `link_speed_mbps` | Mbps |
| Device class | `dc` (2 characters) | — | `device_class` | `steps[speed].details.device_class` | `device_class` | — |
| Speed read / write | `sr`, `sw` | — | `speed.read_mb_s`, `write_mb_s` | `…details.read.mb_s`, `write.mb_s` | `read_mb_s`, `write_mb_s` | MB/s |
| Threshold read / write | `tr`, `tw` | — | `speed.threshold_*_mb_s` | `…details.threshold_*_mb_s` | `threshold_*_mb_s` | MB/s |
| Basis of the verdict | `sb` (read takes priority) | — | `speed.basis_read`, `basis_write` | `…details.basis_read`, `basis_write` | `speed_basis_read`, `speed_basis_write` | — |
| Severity | `sv` | — | `speed.severity` | `…details.severity` | `speed_severity` | — |
| Verdict reason codes | (`ck`, `sv`, `m`) | — | `reason_codes` | `reason_codes` | `reason_codes` | — |
| Verdict reasons (sentences) | — | — | `reasons` | `reasons` | `reasons` | — |
| Inventory digest | `hd` (16 digits) | — | `hash_inventory_digest` (64 digits) | `steps[hash_inventory].details.digest` | `hash_inventory_digest` | hex |
| Inventory row count | `hn` | — | `hash_inventory_lines` | `…details.lines` | `hash_inventory_lines` | Rows |
| Results directory | — | — | — | `output_dir` | `output_dir`, `inspection_id` | — |
| State | — | — | — | — | `state` | — |

## 8. Mapping to SQLite (on the Pi)

For each inspection, the backend writes one row to the `inspections` table of `/var/lib/usb-sentinel/db/inspections.sqlite`. It inserts the row with `state = 'started'` at the start, and after writing `result.json` it fills in all columns and updates the row to `state = 'completed'`. `result.json` is the source of truth, and the DB is an index for listing and searching. The schema version is managed with `PRAGMA user_version` (currently `2`), and later versions only add columns (no deletions or renames). Version 2 adds the `mode` column with `ALTER TABLE inspections ADD COLUMN mode TEXT`; the migration runs inside `BEGIN IMMEDIATE` so two containers opening the DB at once do not both apply it.

### 8.1 Columns

| Column | Type | Source |
|---|---|---|
| `inspection_id` | TEXT primary key | The results directory name (`<timestamp>-<serial>`) |
| `state` | TEXT | `started` / `completed` |
| `verdict`, `verdict_code` | TEXT | The verdict name and its 1-character code (filled in on completion; NULL on a started row) |
| `mode` | TEXT | `scan_only` / `full` (Section 6.9). Set on the started row already. NULL for rows written before `user_version` 2 |
| `serial`, `kernel_name`, `port_path`, `vendor`, `model` | TEXT | `device.*` of `result.json` |
| `started_at` | TEXT | ISO 8601 (UTC) |
| `started_compact` | TEXT | `YYYYmmddTHHMMSSZ` (same as `t` of the normal and detail QR) |
| `finished_at` | TEXT | ISO 8601. NULL on a started row |
| `elapsed_s` | REAL | Elapsed seconds |
| `result_digest` | TEXT | SHA-256 of `result.json` (64 digits) |
| `step_malware`, `step_hash_inventory`, `step_capacity`, `step_speed`, `step_unmount` | TEXT | The state of each step (such as `pass`). NULL for a step that does not exist |
| `malware_count` | INTEGER | Detection count |
| `clamav_db_version`, `clamav_db_date` | TEXT | DB version and date (`YYYY-MM-DD`) |
| `clamav_scanned_files` | INTEGER | `Scanned files` in the clamscan summary |
| `clamav_scan_time_s` | REAL | `Time` in the clamscan summary |
| `nominal_bytes`, `advertised_bytes`, `actual_bytes` | INTEGER | Nominal (`advertised_check.nominal_bytes`), advertised, and measured (bytes) |
| `advertised_over` | INTEGER | Excess found by the non-destructive check (1/0) |
| `capacity_kind` | TEXT | `ok` / `short` / `defect` / `counterfeit` / `unverified` |
| `counterfeit_reasons` | TEXT | JSON array. The union, without duplicates, of `over_advertised` from the non-destructive check and the reasons from the capacity step |
| `lost_ratio` | REAL | Lost ratio |
| `probe_verdict_line` | TEXT | The verdict line of f3probe |
| `link_speed_mbps` | INTEGER | USB link speed |
| `device_class`, `device_class_basis` | TEXT | The class and its origin |
| `read_mb_s`, `write_mb_s` | REAL | Measured speed |
| `threshold_read_mb_s`, `threshold_write_mb_s` | REAL | Thresholds |
| `speed_basis_read`, `speed_basis_write` | TEXT | Basis of the verdict |
| `speed_severity` | TEXT | `severe` / `minor` |
| `reason_codes`, `reasons` | TEXT | JSON arrays (`reasons` does not escape non-ASCII characters) |
| `hash_inventory_digest` | TEXT | SHA-256 of the inventory (64 digits) |
| `hash_inventory_lines` | INTEGER | Number of inventory rows |
| `output_dir` | TEXT | Absolute path of the results directory |
| `warnings` | TEXT | JSON array. The warnings of each step concatenated in step order, followed by the warnings of the whole inspection |
| `created_at`, `updated_at` | TEXT | Row creation and update time (UTC) |

Indexes exist on `started_at`, `(serial, started_at)`, `(port_path, started_at)`, `state`, and `result_digest`.

### 8.2 Query examples for reading

The frontend and other readers open the DB read-only (`sqlite3.connect("file:...?mode=ro", uri=True)`, `PRAGMA query_only = ON`, `busy_timeout = 1000`).

```sql
-- Latest per port (since the connection time)
SELECT inspection_id, state, verdict, serial, kernel_name, port_path, started_at,
       finished_at, step_malware, step_hash_inventory, step_capacity, step_speed,
       malware_count, advertised_over, result_digest, output_dir
FROM inspections
WHERE port_path = ? AND started_at >= ?
ORDER BY started_at DESC LIMIT 1;

-- Started but not completed (the USB drive may have been disconnected)
SELECT inspection_id, serial, port_path, started_at
FROM inspections WHERE state = 'started' AND started_at < ?;

-- History of the same serial
SELECT started_at, verdict, capacity_kind, speed_severity
FROM inspections WHERE serial = ? ORDER BY started_at DESC;

-- Find an inspection from the QR digest (the first 12 digits or more)
SELECT inspection_id, verdict, serial, started_at, output_dir
FROM inspections WHERE result_digest LIKE ? || '%';
```

A row with `state = 'started'` means the inspection is running, or that it ended without writing `result.json` (for example the USB drive was disconnected). If `result.json` exists but the row is still `started`, recording the completion failed, and `SQLite record failed (completion)` remains in the journal.

## 9. Recommended schema on the reader app (Flutter) side

Keep the columns of the Pi side (Section 8.1) as they are, and add the case information as columns. The Pi side holds no case information at all, so linking to the product is completed on the reader app side.

- The primary key is `result_digest`. You get 64 digits from the file QR, 16 digits from the normal QR, and 12 digits from the detail QR. Using the first 12 digits as the primary key is acceptable (the probability of an accidental collision among 10,000 records is about 2×10^-7). If you later read a longer digest, overwrite the key and extend it.
- Use `started_compact` (16 digits) and `serial` as auxiliary keys, for matching when the digest could not be read.
- Split `nominal_bytes` into two columns: `nominal_bytes_pi` (the value set in `config.toml` on the Pi; `cn` of the QR / `capacity.nominal_bytes`) and `nominal_bytes_product` (the value from the product page entered in the reader app). The Pi setting is one per host, so it may contain a value that does not fit a different product inspected at the same time.
- Examples of case information to add: product ID, brand, product name, price, product page URL, inspector, photo file name, entry date and time, and remarks.
- Saving the raw string of each QR (`raw_file_qr`, `raw_normal_qr`, `raw_detail_qr_1` to `3`) lets you reinterpret it later even if the format changes.
- The numbers in the detail QR are in MB, so when matching them with the Pi-side columns (bytes), either multiply by 10^6 before saving or keep them in separate columns (`*_mb`).

## 10. Verification procedure

Confirm that the carried-out result has not been tampered with.

1. Verify `result.json`. On a PC connected to the network, take `sha256sum result.json` and compare it with `digest` (64 digits) of the file QR. For the normal QR, compare `sha256sum result.json | cut -c1-16` with `d`; for the detail QR, compare `cut -c1-12` with `d`. `result.json` is hashed as the exact bytes written by the backend, so compute it on the file as it is, without reformatting.
2. Verify `hash-inventory.tsv`. Compare `sha256sum hash-inventory.tsv` with `hash_inventory_digest` (64 digits) of the file QR or `hd` (first 16 digits) of code 3 of the detail QR. Check the row count with `wc -l` against `hash_inventory_lines` / `hn`.
3. If 1 and 2 match, the values inside `result.json` (detection count, capacity, speed) are the same as when they were written to the QR. If they do not match, suspect that a file was swapped or a QR was misread, and read the QR again.
4. If `truncated` of the file QR is `true`, check `result.json` for the full text of signatures and reason sentences.

## 11. Change history and compatibility

| Version | Target | Change |
|---|---|---|
| File QR v:1 | `result-qr.png` | First version. `verdict`, `serial`, `steps`, `clamav_db`, `digest`, `malware_count`, `signatures`, and `truncated` only when something was cut |
| Normal QR v:2 | Screen | First version. `r` is `N`/`M`/`S`/`D`/`I` |
| File QR v:3 | `result-qr.png` | Phase 2. The keys added in Section 3.3. `truncated` is always output. `Counterfeit Suspected` in `verdict` |
| Normal QR v:2 | Screen | Phase 2. Added `C` (Counterfeit Suspected) to `r`. The format is unchanged |
| Detail QR v:3 | Screen | New in phase 2. Fixed at 3 codes |
| SQLite `user_version` 1 | `inspections.sqlite` | New in phase 2 |
| File QR v:3 | `result-qr.png` | Inspection-mode toggle. Added `mode` (`scan_only` / `full`) after `verdict`. `v` stays `3` |
| Detail QR v:3 code 1 | Screen | Inspection-mode toggle. Added `md` (`s` / `f` / `null`) after `dv`; worst case 147 bytes |
| SQLite `user_version` 2 | `inspections.sqlite` | Inspection-mode toggle. Column `mode`. Deploy the backend and the frontend together: an old frontend refuses `user_version` 2 and falls back to scanning the results directory |

- The reader app branches on `v` and should also be able to read file QR v:1 (results directories from before phase 2 remain). v:1 may not have the `truncated` key; if it is absent, interpret it as `false`.
- When keys are added to file QR v:3 in the future, the meaning of existing keys will not change. The reader app ignores unknown keys and treats a missing known key as `null`.
- Verdict name strings (such as `Counterfeit Suspected`) are values of the backend's `Verdict`, and a change is reported in accordance with `backend/AGENTS.md`. The reader app treats a value not in the table in Section 6.1 as "unknown" and keeps the raw value.
- SQLite only adds columns in later versions. Readers refer to columns by name and do not depend on column position.
- The backend now writes `summary`, `reasons`, `warnings`, and the `result.txt` headings in English. The keys, codes, and verdict names do not change, and the QR versions stay the same. Results written before this change (old rows in SQLite and `result.json` files already taken out) keep their Japanese text. The reader app uses `reason_codes` as the main source for display and treats the sentences only as supplementary information.
