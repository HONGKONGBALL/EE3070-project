"""Observe the running Pi sample without changing its configuration."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--duration", type=float, default=1200)
    ap.add_argument("--interval", type=float, default=10)
    ap.add_argument("--out", type=Path, default=Path("logs/camera-stability.json"))
    args = ap.parse_args()
    args.out.parent.mkdir(exist_ok=True, parents=True)
    start = time.monotonic()
    report = {"started_at": time.time(), "duration_requested_s": args.duration, "samples": []}
    while True:
        elapsed = time.monotonic() - start
        sample = {"elapsed_s": round(elapsed, 2)}
        try:
            requested = time.monotonic()
            with urllib.request.urlopen("http://127.0.0.1:8000/status", timeout=3) as response:
                status = json.load(response)
            sample.update({k: status[k] for k in (
                "ready", "run_id", "frame_id", "fps", "inference_ms", "latency_p95_ms", "error")})
            sample["status_response_ms"] = round((time.monotonic() - requested) * 1000, 2)
            sample["audio"] = status["audio"]
            sample["throttled"] = subprocess.check_output(["vcgencmd", "get_throttled"], text=True).strip()
            sample["current_power_warning"] = bool(int(sample["throttled"].split("=")[-1], 16) & 0x5)
            sample["temperature"] = subprocess.check_output(["vcgencmd", "measure_temp"], text=True).strip()
            pid = subprocess.check_output(["systemctl", "--user", "show", "smart-cane-camera",
                                           "--property=MainPID", "--value"], text=True).strip()
            memory = Path(f"/proc/{pid}/status").read_text()
            sample["memory"] = [line for line in memory.splitlines()
                                if line.startswith(("VmRSS:", "VmHWM:"))]
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            sample["ready"], sample["error"] = False, str(exc)
        report["samples"].append(sample)
        report["elapsed_s"] = round(time.monotonic() - start, 2)
        frames = [s.get("frame_id", 0) for s in report["samples"]]
        report["passed_so_far"] = (all(s.get("ready") for s in report["samples"])
                                    and all(b > a for a, b in zip(frames, frames[1:]))
                                    and len({s.get("run_id") for s in report["samples"]}) == 1
                                    and all(s.get("fps", 0) >= 3 for s in report["samples"])
                                    and all((s.get("latency_p95_ms") or 99999) <= 1500
                                            for s in report["samples"]))
        report["power_warning_seen"] = any(s.get("current_power_warning") for s in report["samples"])
        temporary = args.out.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(args.out)
        print(json.dumps(sample), flush=True)
        if report["elapsed_s"] >= args.duration:
            break
        time.sleep(min(args.interval, args.duration - report["elapsed_s"]))
    snapshot = Path("captures")
    snapshot.mkdir(exist_ok=True)
    with urllib.request.urlopen("http://127.0.0.1:8000/capture", data=b"", timeout=3) as response:
        capture = json.load(response)
    report["capture"] = capture
    report["capture"]["sha256"] = hashlib.sha256(Path(capture["path"]).read_bytes()).hexdigest()
    report["finished_at"] = time.time()
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("COMPLETE " + json.dumps({k: report[k] for k in ("elapsed_s", "passed_so_far", "capture")}), flush=True)


if __name__ == "__main__":
    main()
