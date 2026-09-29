#!/usr/bin/env python3
"""Milestone 2.5 Investigation A: Empirical nettop identity and lifecycle fields.

Tests candidate nettop fields:
- uuid, epid, euuid, vuuid, closed_tcp, closed_udp
Across:
- while process is alive (open socket vs closed socket)
- after process exits (terminal rows)
- repeated launches of same executable
- different executables (curl, nc, python)
- simultaneous instances of same executable
- process reappearance / restart
- PID reuse simulation and safety evaluation
"""

import ctypes
import json
import os
import pathlib
import pty
import select
import socket
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / ".validation" / "milestone2_5"
OUT.mkdir(parents=True, exist_ok=True)

libc = ctypes.CDLL(None)

class proc_bsdinfo(ctypes.Structure):
    _fields_ = [
        ('pbi_flags', ctypes.c_uint32),
        ('pbi_status', ctypes.c_uint32),
        ('pbi_xstatus', ctypes.c_uint32),
        ('pbi_pid', ctypes.c_uint32),
        ('pbi_ppid', ctypes.c_uint32),
        ('pbi_uid', ctypes.c_uint32),
        ('pbi_gid', ctypes.c_uint32),
        ('pbi_ruid', ctypes.c_uint32),
        ('pbi_rgid', ctypes.c_uint32),
        ('pbi_svuid', ctypes.c_uint32),
        ('pbi_svgid', ctypes.c_uint32),
        ('rfu_1', ctypes.c_uint32),
        ('pbi_comm', ctypes.c_char * 16),
        ('pbi_name', ctypes.c_char * 32),
        ('pbi_nfiles', ctypes.c_uint32),
        ('pbi_pgid', ctypes.c_uint32),
        ('pbi_pjobc', ctypes.c_uint32),
        ('pbi_e_tdev', ctypes.c_uint32),
        ('pbi_e_tpgid', ctypes.c_uint32),
        ('pbi_nice', ctypes.c_int32),
        ('pbi_start_tvsec', ctypes.c_uint64),
        ('pbi_start_tvusec', ctypes.c_uint64),
    ]

def get_proc_birth(pid: int) -> int:
    info = proc_bsdinfo()
    res = libc.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    if res == ctypes.sizeof(info) and info.pbi_start_tvsec > 0:
        return info.pbi_start_tvsec * 1000000 + info.pbi_start_tvusec
    return 0

def get_proc_path(pid: int) -> str:
    buf = ctypes.create_string_buffer(4096)
    res = libc.proc_pidpath(pid, buf, 4096)
    if res > 0:
        return buf.value.decode('utf-8', errors='replace')
    return ""

def get_bundle_id(path_str: str) -> str:
    if not path_str:
        return ""
    p = pathlib.Path(path_str)
    for parent in [p] + list(p.parents):
        if parent.suffix == ".app":
            plist_path = parent / "Contents" / "Info.plist"
            if plist_path.is_file():
                try:
                    import plistlib
                    with plist_path.open('rb') as f:
                        data = plistlib.load(f)
                        return data.get('CFBundleIdentifier', '')
                except Exception:
                    pass
    return ""

class NettopCapture:
    def __init__(self, summary_only=True, interval=1):
        self.summary_only = summary_only
        self.interval = interval
        self.lines = []
        self.frames = []
        self.current_frame = None
        self.lock = threading.Lock()
        self.master, slave = pty.openpty()
        
        args = [
            "/usr/bin/nettop",
            "-L", "0",
            "-s", str(interval),
            "-n", "-x",
        ]
        if summary_only:
            args.append("-P")
        args += ["-J", "bytes_in,bytes_out,uuid,epid,euuid,vuuid,closed_tcp,closed_udp"]

        self.proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=slave,
            stderr=subprocess.PIPE,
            env={**os.environ, "LC_ALL": "C"}
        )
        os.close(slave)
        self.running = True
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

    def _reader(self):
        buf = b""
        while self.running:
            try:
                r, _, _ = select.select([self.master], [], [], 0.2)
                if not r:
                    continue
                chunk = os.read(self.master, 65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line_b, buf = buf.split(b"\n", 1)
                    line = line_b.decode("utf-8", errors="replace").rstrip("\r")
                    now = time.monotonic()
                    with self.lock:
                        self.lines.append((now, line))
                        if line == ",bytes_in,bytes_out,uuid,epid,euuid,vuuid,closed_tcp,closed_udp,":
                            if self.current_frame is not None:
                                self.frames.append(self.current_frame)
                            self.current_frame = {"at": now, "rows": []}
                        elif self.current_frame is not None and line:
                            parts = line.split(",")
                            # Expected 10 columns (first is row label, last is trailing comma)
                            if len(parts) >= 9:
                                row_info = {
                                    "label": parts[0],
                                    "bytes_in": parts[1],
                                    "bytes_out": parts[2],
                                    "uuid": parts[3],
                                    "epid": parts[4],
                                    "euuid": parts[5],
                                    "vuuid": parts[6],
                                    "closed_tcp": parts[7],
                                    "closed_udp": parts[8],
                                }
                                self.current_frame["rows"].append(row_info)
            except OSError:
                break

    def stop(self):
        self.running = False
        self.proc.terminate()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        os.close(self.master)
        self.thread.join(timeout=2)

    def find_rows_for_pid(self, pid: int, ports: list = ()):
        with self.lock:
            result = []
            for idx, frame in enumerate(self.frames):
                matching = []
                for row in frame["rows"]:
                    lbl = row["label"]
                    # If process summary, lbl ends with .PID
                    if lbl.endswith(f".{pid}"):
                        matching.append({"type": "process", "frame_idx": idx, "at": frame["at"], **row})
                    # If connection row, check epid or label or port
                    elif row.get("epid") == str(pid) or f".{pid}" in lbl or any(f":{p}" in lbl for p in ports):
                        matching.append({"type": "connection", "frame_idx": idx, "at": frame["at"], **row})
                if matching:
                    result.append({"frame_idx": idx, "at": frame["at"], "matches": matching})
            return result

def run_investigation():
    print("=" * 70)
    print("Milestone 2.5 Investigation A: Nettop Process Identity & Lifecycle")
    print("=" * 70)

    # Start loopback server
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(('127.0.0.1', 0))
    srv.listen(128)
    server_port = srv.getsockname()[1]

    def server_loop():
        while True:
            try:
                conn, _ = srv.accept()
                def handler(c):
                    try:
                        req = b""
                        while True:
                            chunk = c.recv(1024)
                            if not chunk: break
                            req += chunk
                            if b"\r\n\r\n" in req or b"\n\n" in req or len(req) >= 500:
                                break
                        # Send 1024 bytes back
                        resp = b"HTTP/1.1 200 OK\r\nContent-Length: 1024\r\nConnection: close\r\n\r\n" + (b"X" * 1024)
                        c.sendall(resp)
                    except Exception:
                        pass
                    finally:
                        try: c.close()
                        except: pass
                threading.Thread(target=handler, args=(conn,), daemon=True).start()
            except OSError:
                break

    srv_thread = threading.Thread(target=server_loop, daemon=True)
    srv_thread.start()

    evidence = {
        "metadata": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "os_version": subprocess.check_output(["sw_vers", "-productVersion"], text=True).strip(),
            "build_version": subprocess.check_output(["sw_vers", "-buildVersion"], text=True).strip(),
            "server_port": server_port,
        },
        "scenarios": {}
    }

    # -------------------------------------------------------------------------
    # Scenario 1 & 2: Process summary mode (-P) vs Detailed connection mode
    # Test curl, nc, python
    # -------------------------------------------------------------------------
    print("\n--- Running Scenario 1: Executable Comparison in -P Mode ---")
    cap_p = NettopCapture(summary_only=True, interval=1)
    time.sleep(2)  # Settle nettop

    targets = [
        ("curl", ["/usr/bin/curl", "-s", f"http://127.0.0.1:{server_port}/"]),
        ("nc", ["/usr/bin/nc", "127.0.0.1", str(server_port)]),
        ("python", [sys.executable, "-c", f"""
import socket, time
s = socket.create_connection(('127.0.0.1', {server_port}))
s.sendall(b'GET / HTTP/1.0\\r\\n\\r\\n')
s.recv(2048)
# hold socket open for 2s
time.sleep(2)
s.close()
# hold process alive for 2s after socket close
time.sleep(2)
"""]),
    ]

    scenario_1_results = []
    for name, cmd in targets:
        print(f"Launching {name} ({cmd[0]})...")
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE if name == "nc" else None, stdout=subprocess.PIPE)
        pid = p.pid
        birth = get_proc_birth(pid)
        path = get_proc_path(pid)
        bundle_id = get_bundle_id(path)

        if name == "nc":
            # Send HTTP request via nc
            time.sleep(1)
            try:
                p.stdin.write(b"GET / HTTP/1.0\r\n\r\n")
                p.stdin.flush()
            except Exception:
                pass

        # Wait for exit
        p.communicate(timeout=15)
        exit_time = time.monotonic()
        print(f"  {name} exited (PID {pid}, birth={birth}, path={path})")
        time.sleep(3)  # Allow terminal frames to be captured

        # Query captured frames for this PID
        rows = cap_p.find_rows_for_pid(pid)
        scenario_1_results.append({
            "name": name,
            "pid": pid,
            "birth_us": birth,
            "executable_path": path,
            "bundle_id": bundle_id,
            "exit_time": exit_time,
            "observations_in_summary_mode": rows,
        })

    cap_p.stop()
    evidence["scenarios"]["scenario_1_process_summary_mode"] = scenario_1_results

    # -------------------------------------------------------------------------
    # Scenario 2: Connection Detail Mode (without -P)
    # Observe uuid, epid, euuid, vuuid on socket rows
    # -------------------------------------------------------------------------
    print("\n--- Running Scenario 2: Detailed Connection Mode (without -P) ---")
    cap_conn = NettopCapture(summary_only=False, interval=1)
    time.sleep(2)

    scenario_2_results = []
    for name, cmd in targets:
        print(f"Launching {name} for connection-mode inspection...")
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE if name == "nc" else None, stdout=subprocess.PIPE)
        pid = p.pid
        birth = get_proc_birth(pid)
        path = get_proc_path(pid)
        bundle_id = get_bundle_id(path)

        if name == "nc":
            time.sleep(1)
            try:
                p.stdin.write(b"GET / HTTP/1.0\r\n\r\n")
                p.stdin.flush()
            except Exception:
                pass

        p.communicate(timeout=15)
        exit_time = time.monotonic()
        time.sleep(3)

        rows = cap_conn.find_rows_for_pid(pid)
        # Also grab any connection rows with local ephemeral port or server port
        scenario_2_results.append({
            "name": name,
            "pid": pid,
            "birth_us": birth,
            "executable_path": path,
            "bundle_id": bundle_id,
            "exit_time": exit_time,
            "observations_in_conn_mode": rows,
        })

    cap_conn.stop()
    evidence["scenarios"]["scenario_2_connection_mode"] = scenario_2_results

    # -------------------------------------------------------------------------
    # Scenario 3: Repeated launches of same executable (python x2)
    # Check if UUIDs stay identical or change
    # -------------------------------------------------------------------------
    print("\n--- Running Scenario 3: Repeated Launches of Same Executable ---")
    cap_rep = NettopCapture(summary_only=False, interval=1)
    time.sleep(2)

    repeated_results = []
    for i in range(2):
        client_code = f"""
import socket, time
s = socket.create_connection(('127.0.0.1', {server_port}))
print(s.getsockname()[1], flush=True)
s.sendall(b'GET / HTTP/1.0\\r\\n\\r\\n')
s.recv(2048)
time.sleep(2.5)
s.close()
"""
        p = subprocess.Popen([sys.executable, "-c", client_code], stdout=subprocess.PIPE, text=True)
        pid = p.pid
        birth = get_proc_birth(pid)
        path = get_proc_path(pid)
        port_line = p.stdout.readline().strip()
        client_port = int(port_line) if port_line.isdigit() else 0
        p.communicate(timeout=10)
        time.sleep(2)
        rows = cap_rep.find_rows_for_pid(pid, ports=[client_port] if client_port else [])
        repeated_results.append({
            "launch_index": i + 1,
            "pid": pid,
            "birth_us": birth,
            "path": path,
            "client_port": client_port,
            "rows": rows,
        })

    cap_rep.stop()
    evidence["scenarios"]["scenario_3_repeated_launches"] = repeated_results

    # -------------------------------------------------------------------------
    # Scenario 4: Simultaneous instances of same executable
    # Check if concurrent instances have distinct or identical UUIDs
    # -------------------------------------------------------------------------
    print("\n--- Running Scenario 4: Simultaneous Instances of Same Executable ---")
    cap_sim = NettopCapture(summary_only=False, interval=1)
    time.sleep(2)

    client_code = f"""
import socket, time
s = socket.create_connection(('127.0.0.1', {server_port}))
print(s.getsockname()[1], flush=True)
s.sendall(b'GET / HTTP/1.0\\r\\n\\r\\n')
s.recv(2048)
time.sleep(3.5)
s.close()
"""
    p1 = subprocess.Popen([sys.executable, "-c", client_code], stdout=subprocess.PIPE, text=True)
    p2 = subprocess.Popen([sys.executable, "-c", client_code], stdout=subprocess.PIPE, text=True)
    pid1, pid2 = p1.pid, p2.pid
    birth1, birth2 = get_proc_birth(pid1), get_proc_birth(pid2)
    port1_line = p1.stdout.readline().strip()
    port2_line = p2.stdout.readline().strip()
    client_port1 = int(port1_line) if port1_line.isdigit() else 0
    client_port2 = int(port2_line) if port2_line.isdigit() else 0

    p1.communicate(timeout=10)
    p2.communicate(timeout=10)
    time.sleep(2)

    cap_sim.stop()
    evidence["scenarios"]["scenario_4_simultaneous_instances"] = {
        "instance_1": {"pid": pid1, "birth_us": birth1, "client_port": client_port1, "rows": cap_sim.find_rows_for_pid(pid1, ports=[client_port1] if client_port1 else [])},
        "instance_2": {"pid": pid2, "birth_us": birth2, "client_port": client_port2, "rows": cap_sim.find_rows_for_pid(pid2, ports=[client_port2] if client_port2 else [])},
    }

    # -------------------------------------------------------------------------
    # Scenario 5: Process Restart / Reappearance
    # -------------------------------------------------------------------------
    print("\n--- Running Scenario 5: Process Reappearance ---")
    cap_reappear = NettopCapture(summary_only=True, interval=1)
    time.sleep(2)

    p_first = subprocess.Popen([sys.executable, "-c", client_code], stdout=subprocess.PIPE)
    pid_first = p_first.pid
    birth_first = get_proc_birth(pid_first)
    p_first.communicate(timeout=10)
    time.sleep(2)

    p_second = subprocess.Popen([sys.executable, "-c", client_code], stdout=subprocess.PIPE)
    pid_second = p_second.pid
    birth_second = get_proc_birth(pid_second)
    p_second.communicate(timeout=10)
    time.sleep(2)

    cap_reappear.stop()
    evidence["scenarios"]["scenario_5_reappearance"] = {
        "first_run": {"pid": pid_first, "birth_us": birth_first, "rows": cap_reappear.find_rows_for_pid(pid_first)},
        "second_run": {"pid": pid_second, "birth_us": birth_second, "rows": cap_reappear.find_rows_for_pid(pid_second)},
    }

    # Clean up server
    srv.close()

    # Save complete raw evidence
    evidence_path = OUT / "identity_investigation.json"
    evidence_path.write_text(json.dumps(evidence, indent=2))
    print(f"\nSaved raw evidence to {evidence_path}")

    # Print summary analysis
    print("\n" + "=" * 70)
    print("ANALYSIS & EMPIRICAL FINDINGS:")
    print("=" * 70)

    # Check summary mode rows:
    summary_uuids = []
    for sc in scenario_1_results:
        for obs in sc["observations_in_summary_mode"]:
            for m in obs["matches"]:
                summary_uuids.append((m.get("uuid"), m.get("epid"), m.get("euuid"), m.get("closed_tcp"), m.get("closed_udp")))
    print(f"1. In Process Summary Mode (-P):")
    print(f"   Total process observations inspected: {len(summary_uuids)}")
    print(f"   Are uuid/epid/euuid/vuuid ever present? {any(u[0] or u[1] or u[2] for u in summary_uuids)}")
    print(f"   Sample values for (uuid, epid, euuid, closed_tcp, closed_udp):")
    for s in summary_uuids[:5]:
        print(f"     {s}")

    # Check connection mode rows:
    print(f"\n2. In Connection Detail Mode (without -P):")
    for sc in scenario_2_results:
        print(f"   Target {sc['name']} (PID {sc['pid']}):")
        for obs in sc["observations_in_conn_mode"]:
            for m in obs["matches"]:
                print(f"     Type: {m.get('type')}, Label: {m.get('label')}")
                print(f"       uuid: {m.get('uuid')}")
                print(f"       epid: {m.get('epid')}")
                print(f"       euuid: {m.get('euuid')}")
                print(f"       vuuid: {m.get('vuuid')}")
                print(f"       closed_tcp: {m.get('closed_tcp')}, closed_udp: {m.get('closed_udp')}")

    # Check repeated launches
    print(f"\n3. Across Repeated Launches of Same Executable:")
    rep = evidence["scenarios"]["scenario_3_repeated_launches"]
    for item in rep:
        print(f"   Launch {item['launch_index']}: PID={item['pid']}, birth={item['birth_us']}")
        for obs in item["rows"]:
            for m in obs["matches"]:
                if m.get("type") == "connection":
                    print(f"     Connection uuid={m.get('uuid')}, euuid={m.get('euuid')}")

    # Check simultaneous instances
    print(f"\n4. Across Simultaneous Instances of Same Executable:")
    sim = evidence["scenarios"]["scenario_4_simultaneous_instances"]
    for inst_name in ("instance_1", "instance_2"):
        inst = sim[inst_name]
        print(f"   {inst_name}: PID={inst['pid']}, birth={inst['birth_us']}")
        for obs in inst["rows"]:
            for m in obs["matches"]:
                if m.get("type") == "connection":
                    print(f"     Connection uuid={m.get('uuid')}, euuid={m.get('euuid')}")

    print("=" * 70)

if __name__ == "__main__":
    run_investigation()
