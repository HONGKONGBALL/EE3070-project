"""Configuration: TOML file + environment variables for secrets.

Secrets (AMAP_KEY, CANE_PHONE_TOKEN) are only read from the environment and
are never written to logs or exposed by the diagnostics page.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

LANGUAGES = ("cmn", "yue", "en")


@dataclass
class GeneralCfg:
    language: str = "cmn"
    test_mode: bool = False
    log_dir: str = "logs"


@dataclass
class PicoCfg:
    port: str = "/dev/ttyACM0"
    baud: int = 115200
    heartbeat_s: float = 0.5
    timeout_s: float = 1.0


@dataclass
class AudioCfg:
    dir: str = "audio"
    device: str = "default"
    player: str = "aplay"


@dataclass
class ArbiterCfg:
    safety_hold_s: float = 2.0
    repeat_cooldown_s: float = 3.0


@dataclass
class HazardCfg:
    walking_speed_mps: float = 1.0
    sensor_latency_s: float = 0.25
    comprehension_s: float = 0.5
    margin_m: float = 0.3
    stop_distance_m: float = 0.6
    head_warn_m: float = 1.2
    max_valid_m: float = 4.0


@dataclass
class TfminiCfg:
    role: str = "ground"
    mount_height_m: float = 0.85
    tilt_down_deg: float = 25.0
    drop_tolerance_m: float = 0.12
    rise_tolerance_m: float = 0.12
    min_strength: int = 100


@dataclass
class ImuCfg:
    enabled: bool = True
    bus: int = 1
    address: int = 0x68
    rate_hz: int = 200
    window_s: float = 1.0


@dataclass
class VisionCfg:
    enabled: bool = True
    left_device: str = "/dev/video0"
    right_device: str = "/dev/video2"
    calibration: str = "calib/stereo.npz"
    odometry: bool = True
    odometry_max_yaw_deg: float = 15.0


@dataclass
class Destination:
    id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    lng: float = 0.0
    lat: float = 0.0


@dataclass
class NavCfg:
    enabled: bool = True
    coord_mode: str = "amap_api"
    city: str = ""
    announce_m: list[float] = field(default_factory=lambda: [50.0, 20.0, 8.0])
    arrive_m: float = 8.0
    offroute_m: float = 25.0
    read_route_with_tts: bool = False
    destinations: list[Destination] = field(default_factory=list)


@dataclass
class VoiceCfg:
    enabled: bool = True
    device: str = "default"
    record_s: float = 4.0
    backend: str = "keyword"
    vosk_model_dir: str = "models"


@dataclass
class WebCfg:
    enabled: bool = True
    diag_host: str = "127.0.0.1"
    diag_port: int = 8080
    phone_host: str = "0.0.0.0"
    phone_port: int = 8443
    certfile: str = "certs/cane.crt"
    keyfile: str = "certs/cane.key"
    diag_allow: list[str] = field(default_factory=lambda: ["127.0.0.1/32", "::1/128"])


@dataclass
class Secrets:
    """Read from the environment only. repr() never shows values."""

    amap_key: str | None = None
    phone_token: str | None = None

    def __repr__(self) -> str:  # never leak secrets into logs
        return (
            f"Secrets(amap_key={'set' if self.amap_key else 'unset'}, "
            f"phone_token={'set' if self.phone_token else 'unset'})"
        )

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Secrets":
        env = os.environ if env is None else env
        return cls(
            amap_key=env.get("AMAP_KEY") or None,
            phone_token=env.get("CANE_PHONE_TOKEN") or None,
        )


@dataclass
class Config:
    general: GeneralCfg = field(default_factory=GeneralCfg)
    pico: PicoCfg = field(default_factory=PicoCfg)
    audio: AudioCfg = field(default_factory=AudioCfg)
    arbiter: ArbiterCfg = field(default_factory=ArbiterCfg)
    hazard: HazardCfg = field(default_factory=HazardCfg)
    tfmini: TfminiCfg = field(default_factory=TfminiCfg)
    imu: ImuCfg = field(default_factory=ImuCfg)
    vision: VisionCfg = field(default_factory=VisionCfg)
    nav: NavCfg = field(default_factory=NavCfg)
    voice: VoiceCfg = field(default_factory=VoiceCfg)
    web: WebCfg = field(default_factory=WebCfg)
    secrets: Secrets = field(default_factory=Secrets)


def _build(cls: type, data: dict[str, Any]) -> Any:
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(f"unknown keys in [{cls.__name__}]: {sorted(unknown)}")
    kwargs = {}
    for name, value in data.items():
        if cls is NavCfg and name == "destinations":
            value = [Destination(**d) for d in value]
        elif cls is NavCfg and name == "announce_m":
            value = [float(v) for v in value]
        kwargs[name] = value
    return cls(**kwargs)


def load_config(path: str | Path | None = None, env: dict[str, str] | None = None) -> Config:
    data: dict[str, Any] = {}
    if path is not None:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    cfg = Config()
    for f in fields(Config):
        if f.name == "secrets":
            continue
        section = data.pop(f.name, None)
        if section is not None:
            default = getattr(cfg, f.name)
            assert is_dataclass(default)
            setattr(cfg, f.name, _build(type(default), section))
    if data:
        raise ValueError(f"unknown config sections: {sorted(data)}")
    if cfg.general.language not in LANGUAGES:
        raise ValueError(f"general.language must be one of {LANGUAGES}")
    if cfg.tfmini.role not in ("ground", "front"):
        raise ValueError("tfmini.role must be 'ground' or 'front'")
    if cfg.nav.coord_mode not in ("amap_api", "offline_gcj02", "none"):
        raise ValueError("nav.coord_mode must be amap_api, offline_gcj02 or none")
    cfg.secrets = Secrets.from_env(env)
    return cfg
