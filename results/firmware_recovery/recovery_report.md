# Stage 1 Firmware Recovery Report

## Result

Recovery Path B succeeded. The original v2 smoke firmware pair was recovered from the active app partitions of the two connected ESP32-S3 boards. The v2 12/12 smoke result remains the applicable smoke evidence because both extracted canonical images match the recorded smoke SHA256 values exactly.

## Artifact search

Path A searched the repository and configured archive locations and indexed 14 `.bin` files in `artifact_inventory.csv`. Neither expected firmware SHA256 was present. The current `build/` app binaries are different hashes and were not used.

## Device identity and partition evidence

- Sensor: MAC `80:65:99:a7:a8:a4`, `/dev/cu.usbmodem1401` at recovery.
- Gateway: MAC `c0:4e:30:31:42:9c`, `/dev/cu.usbmodem1101` at recovery.
- Both identify as ESP32-S3.
- Device partition tables were read with esptool and decoded with ESP-IDF `gen_esp32part.py`; the table hashes match the generated reference tables.
- Each actual table contains one app partition (`factory`) and no OTA app slots. App offsets and sizes in `device_partition_map.json` come from the decoded device tables.
- App images were read from the derived active app partitions. esptool's ESP32-S3 image parser supplied the image body length and appended digest; ESP image checksum and embedded digest validated before SHA256 comparison.

## Exact image recovery

| Role | Extracted size | SHA256 | v2 smoke expected | Match |
|---|---:|---|---|---|
| Sensor | 285,568 bytes | `b3cecd078351cc6fe659b0087db05a67a4ca35ab610125a0f0381887281221b4` | same | YES |
| Gateway | 254,256 bytes | `6f69dffed65e2636b371401db45a483f951189e029b538c2f587d331428936ac` | same | YES |

Archived read-only binaries:

- `artifacts/frozen_v2_smoke/`
- `artifacts/final_stage1_firmware/`

The existing smoke manifest reports 12 planned, 12 passed, 0 failed runs. All 12 run manifests carry the exact same firmware hashes. Retained logs show the last firmware flash before smoke; the diagnostic and v2 smoke manifests record flash skipped afterward. Device dumps independently confirm the active app images now match those hashes.

## Frozen study checks

- Algorithm spec SHA256: `ab07fd9cac09acb53a6335e398c2e9f9d32f916bf5da34696ca69d349ce0d522`
- Default config SHA256: `687f92948fdb49e06079f2df9c2a3bc03c39a776b90f13b639f6be36f6dbf234`
- Trace implementation SHA256: `e05f9dbb5f9c250459cc37e89e56349030d10a774914ceb326b5c86c21009614`
- Fault implementation SHA256: `db1995b06d165af614c16ec661919f017e6e788b92759b86095365294a97a660`
- Firmware source matches the v2 smoke build revision; no firmware source was changed during recovery or Stage 1 runner preparation.

## Host runner corrections before Stage 1

The Stage 1 runner now shuffles all four strategies within each condition using seed `4170411`, computes all 40 EventGuard reference budgets before the first hardware run, and reads only `artifacts/final_stage1_firmware/`. It refuses Stage 1 without `--skip-flash`, checks the recovered device/image/algorithm hashes, and persists a failed run attempt instead of retrying after a START attempt. Per-sample output includes ground truth, predicted importance, link state before/after, and DATA/ACK copy outcomes.

The old order generator placed EventGuard first in every condition; that host orchestration issue is corrected without changing firmware or algorithm files. Validation: 44 unit tests pass; a saved v2 smoke run was reparsed with zero sample divergences and zero parser issues.

## Stage 1 status at report creation

Stage 1 has not started yet. No Stage 1 `START` command or formal Stage 1 data exists. Path C rebuild and a new smoke are unnecessary because the recovered images match the original smoke images exactly.
