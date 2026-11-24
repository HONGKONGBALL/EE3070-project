"""python -m cane [--config config/config.toml] [--sim]"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

from .app import CaneApp
from .config import load_config
from .nav.providers import map_client
from .voice.pi_places import PiPlaceSearch
from .web.pairing import LAN_NETWORKS, PHONE_PORT, PhonePair

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
    ap.add_argument("--phone-pair", type=Path, help="Pi pairing directory for iPhone location and K2 recording")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    cfg_path = Path(args.config)
    cfg = load_config(cfg_path if cfg_path.exists() else None)
    if not cfg_path.exists():
        print(f"config {cfg_path} not found, using defaults", file=sys.stderr)

    if args.phone_pair is not None:
        try:
            pair = PhonePair.load(args.phone_pair)
        except (OSError, ValueError):
            ap.error("prepare the phone pairing with tools/setup_pi_phone.py")
        if not any(pair.address in network for network in LAN_NETWORKS):
            ap.error("phone pairing must use a private IPv4 address")
        if args.no_web or not cfg.web.enabled:
            ap.error("phone pairing requires the web service")
        cert, key = args.phone_pair / "server.crt", args.phone_pair / "server.key"
        if not cert.is_file() or not key.is_file():
            ap.error("phone pairing requires a server certificate and key")
        cfg.web.phone_host = str(pair.address)
        cfg.web.phone_port = PHONE_PORT
        cfg.web.certfile, cfg.web.keyfile = str(cert), str(key)
        cfg.secrets.phone_token = pair.token
        cfg.voice.manual_stop = True
        cfg.voice.candidate_selection = True
        cfg.voice.places_source = "pi"
        cfg.voice.backend = "vosk"

    Path(cfg.general.log_dir).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(Path(cfg.general.log_dir) / "cane.log")],
    )
    log.info("starting (sim=%s) %r", args.sim, cfg.secrets)

    amap = map_client(cfg)
    pi_places = PiPlaceSearch(amap) if args.phone_pair is not None else None
    app = CaneApp(cfg, sim=args.sim, amap=amap if cfg.nav.enabled else None, pi_places=pi_places)
    _attach_imu(app)
    _attach_vision(app)
    if cfg.web.enabled and not args.no_web:
        try:
            from .web.app import serve

            serve(app, cfg.web, cfg.secrets.phone_token, origin_control=app if pi_places else None,
                  paired_phone=pi_places is not None)
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
