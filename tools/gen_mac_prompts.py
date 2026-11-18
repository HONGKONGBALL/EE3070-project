#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["typer>=0.12"]
# ///
# How to run: .venv/bin/python tools/gen_mac_prompts.py --out audio
"""Pre-generate three-language local WAVs with installed macOS voices."""

from __future__ import annotations

import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import typer
from cane.config import Destination
from cane.nav.demo import DEMO_DESTINATION
from cane.prompts import CATALOG, destination_prompt_id

VOICES: Final = {"cmn": "Tingting", "yue": "Sinji", "en": "Samantha"}


@dataclass(frozen=True, slots=True)
class EmptyClipError(RuntimeError):
    path: Path

    def __str__(self) -> str:
        return f"Empty generated clip: {self.path}"


def generate_one(language: str, clip_id: str, text: str, directory: Path) -> Path:
    """Generate without playing; reject empty or malformed output."""
    target = directory / language / f"{clip_id}.wav"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        return target
    temporary = target.with_suffix(".aiff")
    subprocess.run(["say", "-v", VOICES[language], "-o", str(temporary), text],
                   check=True, capture_output=True, timeout=30)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@22050", str(temporary), str(target)],
                   check=True, capture_output=True, timeout=10)
    temporary.unlink()
    with wave.open(str(target), "rb") as audio:
        if audio.getnframes() == 0:
            raise EmptyClipError(target)
    return target


def main(out: Path = Path("audio")) -> None:
    """Generate all catalog and configured demonstration destination names."""
    jobs = [(language, prompt_id, prompt.text(language), out)
            for language in VOICES for prompt_id, prompt in CATALOG.items()]
    destinations = [Destination("demo", "演示终点", [], *DEMO_DESTINATION),
                    Destination("dest1", "香港城市大学"), Destination("dest2", "九龙塘站")]
    for language in VOICES:
        for destination in destinations:
            text = destination.name
            if language == "en":
                text = {"demo": "Demo destination", "dest1": "City University of Hong Kong",
                        "dest2": "Kowloon Tong station"}[destination.id]
            jobs.append((language, destination_prompt_id(destination.id), text, out))
    with ThreadPoolExecutor(max_workers=3) as pool:
        outputs = list(pool.map(lambda args: generate_one(*args), jobs))
    typer.echo(f"已生成并检查 {len(outputs)} 条本地 WAV：{out.resolve()}")


if __name__ == "__main__":
    typer.run(main)
