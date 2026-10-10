"""python -m cane [--config config/config.toml] [--sim]"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

from .app import CaneApp
from .config import load_config

log = logging.getLogger("cane")


def _attach_imu(app: CaneApp) -> None:
    cfg = app.cfg.imu
    if not cfg.enabled or app.sim:
        return
    try:
        from .imu import ImuRunner, Mpu6050

        runner = ImuRunner(Mpu6050(cfg.bus, cfg.address, cfg.rate_hz), cfg.rate_hz, cfg.window_s,
                           on_window=app.roughness_tick)
        runner.start()
        app.imu = runner
    except Exception as e:  # missing smbus2, sensor not wired, ...
        log.warning("IMU unavailable (%s); running without motion/roughness", e)


def _attach_vision(app: CaneApp) -> None:
    cfg = app.cfg.vision
    if not cfg.enabled or app.sim:
        return
    try:
        from .vision.odometry import HeadingTracker
        from .vision.runner import VisionRunner
        from .vision.stereo import StereoCalib, StereoDepth

        calib = StereoCalib(cfg.calibration)
        stereo = StereoDepth(calib, cfg.left_device, cfg.right_device)
        heading = HeadingTracker(calib.K1, cfg.odometry_max_yaw_deg) if cfg.odometry else None
        runner = VisionRunner(stereo, heading)
        runner.start()
        app.vision = runner
    except Exception as e:
        log.warning("vision unavailable (%s); stereo scan and odometry disabled", e)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cane", description="EE3070 smart cane")
    ap.add_argument("--config", default="config/config.toml")
    ap.add_argument("--sim", action="store_true", help="simulated Pico, console audio")
    ap.add_argument("--no-web", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    cfg_path = Path(args.config)
    cfg = load_config(cfg_path if cfg_path.exists() else None)
    if not cfg_path.exists():
        print(f"config {cfg_path} not found, using defaults", file=sys.stderr)

    Path(cfg.general.log_dir).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(Path(cfg.general.log_dir) / "cane.log")],
    )
    log.info("starting (sim=%s) %r", args.sim, cfg.secrets)

    app = CaneApp(cfg, sim=args.sim)
    _attach_imu(app)
    _attach_vision(app)
    if cfg.web.enabled and not args.no_web:
        try:
            from .web.app import serve

            serve(app, cfg.web, cfg.secrets.phone_token)
            log.info("diagnostics on http://%s:%d/", cfg.web.diag_host, cfg.web.diag_port)
        except ImportError as e:
            log.warning("web disabled: %s", e)

    signal.signal(signal.SIGTERM, lambda *_: app.stop())
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
