"""python -m cane.mac.host: run the complete host on a Mac."""

import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from cane.app import CaneApp
from cane.app_types import JsonValue
from cane.audio import ConsoleAudio
from cane.config import Destination, load_config
from cane.nav.demo import DEMO_DESTINATION, DemoRouteClient
from cane.pico_link import PicoLinkBase
from cane.web.app import create_app

from .audio import MacAudio
from .controller import MacController
from .devices import DeviceSelectionError, choose_serial_port, serial_candidates
from .scenarios import ScenarioPicoLink, SimImu
from .serial import MacSerialPicoLink

log = logging.getLogger(__name__)


class LocalVoiceLink(PicoLinkBase):
    """Pick up the destination button when its Pico appears on USB."""

    def __init__(self, port: str, baud: int) -> None:
        super().__init__()
        self.requested, self.baud = port, baud
        self.receiver: MacSerialPicoLink | None = None
        self.scan_at = 0.0

    @property
    def port(self) -> str | None:
        receiver = self.receiver
        return receiver.port if receiver else None

    @property
    def connected(self) -> bool:
        receiver = self.receiver
        return receiver is not None and receiver.connected

    @property
    def protocol_name(self) -> str:
        receiver = self.receiver
        return receiver.protocol_name if receiver else "idle"

    def refresh(self, now: float) -> None:
        if now < self.scan_at:
            return
        self.scan_at = now + 2
        ports = serial_candidates(pico_only=True)
        if self.connected and self.port in ports:
            return
        self.close()
        selected = self.requested if self.requested in ports else None
        if self.requested == "auto" and len(ports) == 1:
            selected = ports[0]
        if selected is not None:
            self.receiver = MacSerialPicoLink(
                selected, self.baud, max_retries=0, destination_button_only=True,
            )
            self.inbox = self.receiver.inbox
            self.receiver.start()

    def close(self) -> None:
        if self.receiver is not None:
            self.receiver.close()
            self.receiver = None

    def send(self, msg: dict[str, JsonValue]) -> None:
        pass


def main(
    sim: bool = typer.Option(False, help="显式使用模拟传感器与合成路线，不连接Pico"),
    port: str = typer.Option("auto", help="真实模式Pico串口，auto仅接受唯一USB设备"),
    config: Annotated[Path, typer.Option()] = Path("config/mac.example.toml"),
    http_port: int = typer.Option(8765, min=1024, max=65535),
    quiet: bool = typer.Option(False, help="使用文字输出，关闭扬声器播放"),
    camera: bool = typer.Option(False, help="显式启用Mac摄像头目标识别"),
    camera_index: int = typer.Option(0, min=0),
    phone: bool = typer.Option(False, help="启用带令牌认证的手机HTTPS定位服务，需证书和CANE_PHONE_TOKEN"),
    voice_bench: bool = typer.Option(False, help="使用 Mac 麦克风、地点候选和 Pico 目的地键"),
    places_env: Annotated[Path | None, typer.Option(help="高德地点查询的本地密钥文件")] = None,
    apple_places: Annotated[Path | None, typer.Option(help="Apple 地图地点搜索程序")] = None,
) -> None:
    """Mac作为整机主机；硬件连接失败会报错，绝不自动进入模拟。"""
    cfg = load_config(config if config.is_file() else None)
    if voice_bench and (sim or camera):
        raise typer.BadParameter("--voice-bench 是单独的语音联调模式")
    if apple_places is not None:
        if not voice_bench or places_env is not None:
            raise typer.BadParameter("Apple 地图地点搜索请使用 --voice-bench")
        if not apple_places.is_file() or not os.access(apple_places, os.X_OK):
            raise typer.BadParameter("请选择已编译的 Apple 地图地点搜索程序")
        cfg.voice.places_python = ""
        cfg.voice.places_script = str(apple_places.resolve())
        cfg.voice.places_source = "apple"
    if places_env is not None:
        navigation = Path(__file__).resolve().parents[4] / "navigation_24h"
        cfg.voice.places_python = str(navigation / ".venv/bin/python")
        cfg.voice.places_script = str(navigation / "tools/poi_search.py")
        if not all(path.is_file() for path in (places_env, Path(cfg.voice.places_python), Path(cfg.voice.places_script))):
            raise typer.BadParameter("请核对高德密钥文件和 navigation_24h 的 Python 环境")
        cfg.voice.candidate_selection = True
        os.environ["CANE_AMAP_SECRETS_FILE"] = str(places_env.resolve())
    cfg.web.diag_port = http_port
    Path(cfg.general.log_dir).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    simulated_link = None
    voice_link = None
    if voice_bench:
        cfg.nav.enabled = False
        cfg.voice.candidate_selection = True
        cfg.voice.wake_enabled = False
        voice_link = LocalVoiceLink(port, cfg.pico.baud)
        link = voice_link
        routes, imu = None, None
    elif sim:
        simulated_link = ScenarioPicoLink()
        cfg.nav.coord_mode = "none"
        cfg.nav.destinations = [Destination("demo", "演示终点", ["演示目的地", "demo destination"], *DEMO_DESTINATION)]
        link = simulated_link
        routes = DemoRouteClient()
        imu = SimImu(simulated_link)
    else:
        try:
            cfg.pico.port = choose_serial_port(port)
        except DeviceSelectionError as error:
            typer.echo(str(error), err=True)
            raise typer.Exit(2) from error
        link, routes, imu = MacSerialPicoLink(cfg.pico.port, cfg.pico.baud), None, None
    audio = ConsoleAudio(cfg.general.language) if quiet else MacAudio(cfg.audio.dir, cfg.general.language)
    app = CaneApp(cfg, sim=sim, pico=link, audio=audio, amap=routes, imu=imu, destination_only=voice_bench)
    ctrl = MacController(app, simulated_link)
    if camera:
        from .vision import MacVisionFeed

        ctrl.camera = MacVisionFeed(app, camera_index)
        ctrl.camera.start()
    web = create_app(
        ctrl, cfg.web.diag_allow, cfg.secrets.phone_token,
        origin_control=ctrl if voice_bench and cfg.voice.places_source == "apple" else None,
    )
    phone_server = None
    phone_thread = None
    if phone:
        if (not cfg.secrets.phone_token or not Path(cfg.web.certfile).is_file()
                or not Path(cfg.web.keyfile).is_file()):
            raise typer.BadParameter("--phone 需要 CANE_PHONE_TOKEN 及本地TLS证书；见 MAC_README.md")
        phone_web = create_app(ctrl, [], cfg.secrets.phone_token)
        phone_server = uvicorn.Server(uvicorn.Config(
            phone_web, host=cfg.web.phone_host, port=cfg.web.phone_port, log_level="warning",
            proxy_headers=False,
            ssl_certfile=cfg.web.certfile, ssl_keyfile=cfg.web.keyfile,
        ))
        phone_thread = threading.Thread(target=phone_server.run, daemon=True, name="phone-https")
        phone_thread.start()
    server = uvicorn.Server(uvicorn.Config(web, host="127.0.0.1", port=http_port, log_level="warning"))
    stopping = threading.Event()
    runner_error: list[str] = []

    def run_core() -> None:
        try:
            app.start()
            terrain_at = 0.0
            while not stopping.is_set():
                now = time.monotonic()
                if voice_link is not None:
                    voice_link.refresh(now)
                app.step()
                ctrl.refresh_simulated_location(now)
                if sim and now - terrain_at >= 1:
                    app.roughness_tick()
                    terrain_at = now
                stopping.wait(0.05)
        except (OSError, RuntimeError, ValueError) as error:
            log.exception("整机主循环失败")
            runner_error.append(str(error))
            server.should_exit = True
        finally:
            app.stop()

    thread = threading.Thread(target=run_core, daemon=True, name="cane-core")
    thread.start()
    signal.signal(signal.SIGTERM, lambda *_: setattr(server, "should_exit", True))
    mode = "语音目的地联调" if voice_bench else "软件模拟（合成数据）" if sim else "真实硬件模式"
    typer.echo(f"{mode}：http://127.0.0.1:{http_port}/")
    try:
        server.run()
    finally:
        stopping.set()
        thread.join(timeout=3)
        if phone_server is not None:
            phone_server.should_exit = True
        if phone_thread is not None:
            phone_thread.join(timeout=3)
        if ctrl.camera:
            ctrl.camera.close()
        audio.stop()
    if runner_error:
        raise typer.Exit(1)


if __name__ == "__main__":
    typer.run(main)
