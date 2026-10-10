#!/usr/bin/env python3
"""Stereo calibration for the two USB cameras with a printed chessboard.

    python tools/calibrate_stereo.py capture --left /dev/video0 --right /dev/video2 --n 25
    python tools/calibrate_stereo.py solve --cols 9 --rows 6 --square 0.025

capture: press SPACE to save a pair (hold the board still - the cameras are not
synchronised), q to quit. Pairs go to calib/pairs/.
solve:   writes calib/stereo.npz (K1 D1 K2 D2 R T size) used by cane.vision.stereo.
Lock focus/exposure with v4l2-ctl before capturing and keep the same settings.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAIRS = ROOT / "calib" / "pairs"


def capture(args) -> int:
    import cv2

    PAIRS.mkdir(parents=True, exist_ok=True)
    cl, cr = cv2.VideoCapture(args.left), cv2.VideoCapture(args.right)
    for c in (cl, cr):
        c.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        c.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    i = len(list(PAIRS.glob("left_*.png")))
    while i < args.n:
        ok1, l = cl.read()
        ok2, r = cr.read()
        if not (ok1 and ok2):
            print("camera read failed", file=sys.stderr)
            return 1
        cv2.imshow("left | right", cv2.hconcat([l, r]))
        k = cv2.waitKey(30) & 0xFF
        if k == ord("q"):
            break
        if k == ord(" "):
            cv2.imwrite(str(PAIRS / f"left_{i:02d}.png"), l)
            cv2.imwrite(str(PAIRS / f"right_{i:02d}.png"), r)
            i += 1
            print(f"saved pair {i}/{args.n}")
    return 0


def solve(args) -> int:
    import cv2
    import numpy as np

    pattern = (args.cols, args.rows)
    objp = np.zeros((args.cols * args.rows, 3), np.float32)
    objp[:, :2] = np.mgrid[0:args.cols, 0:args.rows].T.reshape(-1, 2) * args.square
    obj, il, ir = [], [], []
    size = None
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3)
    for lp in sorted(PAIRS.glob("left_*.png")):
        rp = lp.with_name(lp.name.replace("left_", "right_"))
        gl = cv2.imread(str(lp), cv2.IMREAD_GRAYSCALE)
        gr = cv2.imread(str(rp), cv2.IMREAD_GRAYSCALE)
        if gl is None or gr is None:
            continue
        size = gl.shape[::-1]
        okl, cl = cv2.findChessboardCorners(gl, pattern)
        okr, cr = cv2.findChessboardCorners(gr, pattern)
        if okl and okr:
            obj.append(objp)
            il.append(cv2.cornerSubPix(gl, cl, (11, 11), (-1, -1), crit))
            ir.append(cv2.cornerSubPix(gr, cr, (11, 11), (-1, -1), crit))
    if len(obj) < 10:
        print(f"only {len(obj)} usable pairs; need >= 10", file=sys.stderr)
        return 1
    _, K1, D1, _, _ = cv2.calibrateCamera(obj, il, size, None, None)
    _, K2, D2, _, _ = cv2.calibrateCamera(obj, ir, size, None, None)
    rms, K1, D1, K2, D2, R, T, _, _ = cv2.stereoCalibrate(obj, il, ir, K1, D1, K2, D2, size,
                                                          flags=cv2.CALIB_FIX_INTRINSIC, criteria=crit)
    out = ROOT / "calib" / "stereo.npz"
    np.savez(out, K1=K1, D1=D1, K2=K2, D2=D2, R=R, T=T, size=np.array(size))
    print(f"stereo RMS {rms:.3f} px, baseline {np.linalg.norm(T) * 100:.1f} cm -> {out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--left", default="/dev/video0")
    c.add_argument("--right", default="/dev/video2")
    c.add_argument("--width", type=int, default=640)
    c.add_argument("--height", type=int, default=480)
    c.add_argument("--n", type=int, default=25)
    s = sub.add_parser("solve")
    s.add_argument("--cols", type=int, default=9)
    s.add_argument("--rows", type=int, default=6)
    s.add_argument("--square", type=float, default=0.025, help="square size in metres")
    args = ap.parse_args(argv)
    return capture(args) if args.cmd == "capture" else solve(args)


if __name__ == "__main__":
    sys.exit(main())
