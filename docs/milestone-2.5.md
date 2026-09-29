# Milestone 2.5 — Short-lived process attribution and collection cadence report

Measured Sep 29, 2026 (local time, UTC+8). Environment: macOS 27.0 (26A428), Apple Silicon, ordinary logged-in user.
Production architecture: Rust core / Tauri 2 / React frontend.

---

## 1. Investigation A: Terminal-row & process identity fields

We investigated whether additional columns exposed by `/usr/bin/nettop` on macOS 27 can provide a safe identity to attribute terminal rows after process termination. The tested candidate fields were: `uuid`, `epid`, `euuid`, `vuuid`, `closed_tcp`, `closed_udp`.

### Empirical Findings:

1. **`uuid`**:
   - In `-P` (process summary mode): **Always blank (`""`)**. Nettop does not populate UUID on process summary rows.
   - In connection detail mode (without `-P`): Matches the **Mach-O `LC_UUID` of the binary image on disk**.
     - `/usr/bin/curl`'s `LC_UUID` is `5F8947B8-7A5D-34F6-AA86-8A620B17DA3D`; nettop emits `uuid 5F8947B8-7A5D-34F6-AA86-8A620B17DA3D`.
     - `/usr/bin/nc`'s `LC_UUID` is `0D997A5D-B1F5-3FFD-BEC5-9630A9BFD18B`; nettop emits `uuid 0D997A5D-B1F5-3FFD-BEC5-9630A9BFD18B`.
   - **Stability & Scope**: Remains 100% identical across repeated launches and concurrent instances of the same binary. It identifies the compiled file on disk, **not** a process instance or process birth.

2. **`epid`**:
   - In `-P` mode: **Always blank (`""`)**.
   - In connection detail mode: Effective process ID attributed by NetworkStatistics. For unprivileged client sockets, `epid` is `0` (kernel/system delegation). For listening/accepted sockets, it reflects the server PID. It does not identify the originating client process.

3. **`euuid`**:
   - In `-P` mode: **Always blank (`""`)**.
   - In connection detail mode: Mach-O `LC_UUID` of the effective PID (e.g., `4C4C4482-5555-3144-A12C-A5AB081B177E` when `epid=0`). Identical across all unprivileged outbound sockets.

4. **`vuuid`**:
   - In `-P` mode: **Always blank (`""`)**.
   - In connection detail mode: Vendor UUID (empty for standard BSD/CLI tools).

5. **`closed_tcp` and `closed_udp`**:
   - Present on process summary rows.
   - Represents the cumulative integer count of closed TCP/UDP connections observed for that process name and PID.
   - It is a flow lifecycle counter, **not** an identity.

6. **Terminal Row Behavior**:
   - When a process terminates, its connection rows are immediately destroyed.
   - Nettop retains a terminal process row (`process_name.PID`) for 2–3 seconds at 1s sampling with cumulative `bytes_in,bytes_out` and `closed_tcp,closed_udp`.
   - On this terminal row, **all UUID fields and `epid` are completely absent**.
   - There is NO birth time, instance UUID, or kernel sequence number.

### Attribution Safety Conclusions:

- **Safe terminal-row attribution is NOT possible via nettop fields.** NetTop fields cannot distinguish whether a lingering terminal row belongs to the exited process or to a newly spawned process that reused that PID.
- **Processes that start, transfer data, and exit entirely between observations CANNOT be attributed safely.** When nettop emits the row, the process is already dead (`proc_pidinfo` and `proc_pidpath` return ESRCH). Because nettop provides no birth timestamp, associating the counters with an unverified PID risks false attribution upon PID reuse.
- **Production Decision:** In accordance with the project rule, **no unsafe identity workaround was introduced**. Process identity correctness (`ProcessId = (pid, start_us)`) is strictly preserved.

---

## 2. Investigation B: Decoupling Collection Cadence from UI Presentation

We decoupled the collection cadence from the UI presentation interval:
- The UI presentation interval is fixed at **5.0 seconds** in `src/main.tsx`.
- The collector can sample nettop at **1s, 2s, or 5s** independently.
- The aggregator processes each nettop frame as it arrives and maintains monotonic session totals and live rates in memory.

### Churn Benchmark Comparison (720 total client processes, 3 rounds x 3 trials):

Exact reproducible workload from Milestone 2 (4 KiB / 1 MiB payloads; 0s, 1s, 3s, 7s post-transfer holds; seeds `928 + trial`):

| Collection Interval | Total Processes | Missed Processes (%) | Total Acknowledged Bytes | Observed Bytes | Byte Loss (%) | Byte Loss Range |
|---|---:|---:|---:|---:|---:|---:|
| **5 seconds** | 240 | 128 / 240 = **53.33%** | 126,320,640 B | 59,994,112 B | **52.51%** | 50.01% – 57.49% |
| **2 seconds** | 240 | 95 / 240 = **39.58%** | 126,320,640 B | 75,796,480 B | **40.00%** | 39.99% – 40.00% |
| **1 second** | 240 | 60 / 240 = **25.00%** | 126,320,640 B | 94,740,480 B | **25.00%** | 25.00% – 25.00% |

### Hold Duration Breakdown (60 clients per hold duration per interval):

| Post-Transfer Hold | 5s Process Miss / Byte Loss | 2s Process Miss / Byte Loss | 1s Process Miss / Byte Loss |
|---|---:|---:|---:|
| **0s (Immediate exit)** | 100.0% / 100.0% (0% captured) | 100.0% / 100.0% (0% captured) | **100.0% / 100.0% (0% captured)** |
| **1s hold** | 76.67% / 73.36% (26.64% captured) | 58.33% / 59.99% (40.01% captured) | **0.0% / 0.0% (100.0% captured)** |
| **3s hold** | 36.67% / 36.67% (63.33% captured) | 0.0% / 0.0% (100.0% captured) | **0.0% / 0.0% (100.0% captured)** |
| **7s hold** | 0.0% / 0.0% (100.0% captured) | 0.0% / 0.0% (100.0% captured) | **0.0% / 0.0% (100.0% captured)** |

### Performance Comparison (Core-Only, 60s Settled Windows, 1 Core = 100%):

| Cadence | Native CPU | nettop CPU | Combined Core CPU | Combined Mean RSS | Duration |
|---|---:|---:|---:|---:|---:|
| **5s collection** | 0.066% | 0.313% | **0.379%** | 4.96 MiB | 60.70s |
| **2s collection** | 0.066% | 0.609% | **0.675%** | 4.70 MiB | 60.75s |
| **1s collection** | 0.099% | 1.284% | **1.383%** | 5.66 MiB | 60.76s |

### Desktop Performance (Release Visible, 5s UI Refresh, 5s Collection):
- BandPeek Native: 0.263% CPU, 89.93 MiB RSS
- nettop: 0.329% CPU, 2.52 MiB RSS
- WebKit Helpers (GPU + WebContent + Networking): 0.922% CPU, 72.87 MiB RSS
- **Combined Desktop:** 1.515% CPU, 165.32 MiB mean RSS

---

## 3. Analysis & Cadence Recommendation

1. **Immediate-Exit Traffic:**
   Immediate-exit processes (lifetimes of 2–3 ms) remain **100% unobservable across all collection intervals (1s, 2s, 5s)**. A 1-second sampling rate cannot capture processes that exit within a few milliseconds.
2. **CPU Tradeoff:**
   1-second collection drives core CPU up from 0.38% to **1.38%** (a 3.65x increase), with nettop alone consuming 1.28% CPU. Combined with the desktop UI, this breaches BandPeek's lightweight budget (>2% CPU).
3. **Byte Capture Benefit:**
   1-second collection improves byte recovery for processes that stay alive for ~1 second (recovering 15.0 percentage points of bytes over 2s sampling).
4. **Recommendation:**
   - **Retain 5 seconds as the production default.** It keeps native + collector CPU at ~0.38% (tray total <0.6%) while maintaining correctness.
   - Keep the seam `Monitor::start_with_interval(interval)` and CLI flag `--interval` available for users or validation modes that choose to trade CPU for 1s short-burst capture.

---

## 4. Verification and Contract Status

- **11 Rust tests pass** (unittests for aggregation, parsing, lifecycle, identity).
- **Controlled transfer validation (`validate.py`)**: 0-byte difference between BandPeek and independent nettop across Node loopback and HTTPS downloads.
- **Lifecycle fault recovery (`m2_lifecycle.py`)**:
  - `kill`: collector SIGKILL cleanly recovered, generation 1 → 2, zero baseline rates, monotonic totals.
  - `stall`: collector SIGSTOP detected by sleep-inclusive continuous clock guard (>20s), recovered into generation 2.
- **TypeScript build & Vite bundle**: Pass.
- **Clippy on all targets**: Pass with zero warnings (`-D warnings`).
- **Rustfmt**: Pass.
- **macOS Accuracy Contract**: Updated to reflect that immediate-exit transfers remain unobservable at 1s, 2s, and 5s sampling. Ready for acceptance.
- **Readiness for SQLite / History**: With accuracy bounds and process attribution limits empirically proven and documented, BandPeek is ready to proceed to persistence.
