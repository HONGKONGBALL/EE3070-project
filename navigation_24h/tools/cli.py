#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pydantic>=2.12,<3", "typer>=0.16,<1", "rich>=14,<15", "pyserial>=3.5,<4"]
# ///
# ─── How to run ───
# python3 navigation_24h/tools/navctl.py --help
# ──────────────────
"""Unified navigation build, verification and evidence command entry."""

from __future__ import annotations

from typing import Annotated, Literal

import acceptance
import builds
import capture
import configure_secrets
import configure_wifi
import environment
import mcp_probe
import packaging_release
import pairing_cli
import progress_store
import provision as device_provision
import typer
import verification
from project import ROOT
from secrets_config import load

app = typer.Typer(
    help="Navigation build, replay and evidence tools",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


@app.callback()
def prepare_command(context: typer.Context) -> None:
    """Prepare the service environment for the selected command."""
    if context.invoked_subcommand != "pair-device":
        load(ROOT / "config/secrets.env")


_ = app.command("pair-device")(pairing_cli.pair_device)


@app.command()
def env() -> None:
    """Inventory toolchains, devices and local configuration."""
    raise typer.Exit(environment.inspect())


@app.command()
def status() -> None:
    """Show persisted task state and current evidence coverage."""
    progress_store.checkpoint()
    print(progress_store.load_progress().model_dump_json(indent=2))
    print(acceptance.write_summary().model_dump_json())


@app.command()
def build(target: Annotated[Literal["host", "esp32", "ios-device"], typer.Option()]) -> None:
    """Invoke the actual build toolchain for a target."""
    raise typer.Exit(builds.build(target))


@app.command("install-ios")
def install_ios() -> None:
    """Install the built application on the configured iPhone."""
    raise typer.Exit(builds.install_ios())


@app.command("flash-esp32")
def flash_esp32() -> None:
    """Flash the configured ESP32 with the built firmware."""
    raise typer.Exit(builds.flash_esp32())


@app.command()
def check(task: Annotated[str, typer.Option()] = "T02") -> None:
    """Check task artifacts, schema, host assertions and project structure."""
    raise typer.Exit(verification.check_task(task))


@app.command()
def regression(suite: Annotated[Literal["host", "all"], typer.Option()] = "host") -> None:
    """Execute host assertions and summarize registered case evidence."""
    raise typer.Exit(verification.regression(suite))


@app.command("test")
def run_cases(
    cases: Annotated[str, typer.Option()],
    mode: Annotated[Literal["host", "device", "mixed"], typer.Option()] = "host",
) -> None:
    """Run registered case assertions or prepare device evidence steps."""
    raise typer.Exit(verification.test_cases(tuple(cases.split(",")), mode))


@app.command("probe-mcp")
def probe_mcp(routes: Annotated[str, typer.Option()] = "config/field_routes.json") -> None:
    """Discover provider protocol and walking schemas, then capture routes."""
    raise typer.Exit(mcp_probe.probe(routes))


@app.command()
def soak(
    minutes: Annotated[int, typer.Option()] = 120,
    schedule: Annotated[str, typer.Option()] = "config/soak_schedule.json",
) -> None:
    """Collect a timed, segmented serial-device session."""
    raise typer.Exit(capture.soak(minutes, schedule))


@app.command()
def field(
    routes: Annotated[str, typer.Option()] = "config/field_routes.json",
    rounds: Annotated[int, typer.Option()] = 2,
) -> None:
    """Prepare or collect the configured field-route rounds."""
    raise typer.Exit(capture.field(routes, rounds))


@app.command()
def package() -> None:
    """Archive project sources, build products and evidence manifest."""
    raise typer.Exit(packaging_release.package())


@app.command()
def provision() -> None:
    """Send local network and provider configuration through USB."""
    raise typer.Exit(device_provision.provision())


@app.command("configure-key")
def configure_key() -> None:
    """Open the native secure input dialog for the Amap key."""
    raise typer.Exit(configure_secrets.main())


@app.command("configure-wifi")
def configure_wifi_network() -> None:
    """Open the native Wi-Fi configuration dialogs."""
    raise typer.Exit(configure_wifi.main())


if __name__ == "__main__":
    app()
