"""Download the existing YOLO11n NCNN model and verify its exact content."""

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

BASE = "https://raw.githubusercontent.com/nihui/ncnn-assets/master/models/"
FILES = {
    "yolo11n.ncnn.param": "3e928cc8b0718835af46d04c01266705c8a430159d0e48739549a873e7294ec1",
    "yolo11n.ncnn.bin": "2e1b06fa4210739a71d0bf3132e2f6560e46281e8e071292f2857b3bda726285",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("models/yolo11n"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, expected in FILES.items():
        destination = args.out / name
        if not destination.exists() or hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
            with urllib.request.urlopen(BASE + name, timeout=30) as response:
                content = response.read()
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError(f"Unexpected model checksum: {name}")
            destination.write_bytes(content)
        manifest.append({"file": name, "source": BASE + name, "sha256": expected,
                         "bytes": destination.stat().st_size})
    (args.out / "manifest.json").write_text(json.dumps({
        "model": "YOLO11n COCO NCNN", "files": manifest,
        "weights_license": "Ultralytics AGPL-3.0 terms apply; see docs/camera-third-party.md",
    }, indent=2), encoding="utf-8")
    print(f"Verified model ready: {args.out}")


if __name__ == "__main__":
    main()
