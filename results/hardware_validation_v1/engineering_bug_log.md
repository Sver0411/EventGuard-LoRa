# Hardware validation engineering bug log

## Smoke attempt 1: serial parser field offset

- Condition: EVENTGUARD, RANDOM_COPY 0%, seed 31.
- Firmware image hashes: Sensor `ed005d22ed2c5f44c33d9fa52fc5077dd2b68ce0f8030f991540e4ba51f6115d`; Gateway `83492c7972d8f8fb2d5b2dff20cdfc3872db919dfb433031c23f0805be4f25dd`.
- Observation: both boards completed all 54 samples. Sensor logged 112 DATA copies and 112 accepted ACKs; Gateway logged 54 deliveries, 58 duplicates, 112 ACK transmissions, no CRC or injection drops.
- Bug: new host analysis read the `SAMPLE` serial fields at indices 2/4/5 instead of 3/5/6, so it treated `GOOD` as a copy count and marked the run failed.
- Fix: parser field indices corrected in commit `13860fb`. The saved raw log was replayed offline: 54 sample records, zero sample differences, zero issues. The affected condition was rerun from RESET; it was not counted as completed on the failed attempt.
- Scope: host parser only. Core algorithm, fault calendar, trace, and firmware source did not change; firmware image hashes remained the same.

Future failed or incomplete rerun attempts are copied into `raw/attempts/` before replacement.
