"""Screenshot capture: WebSocket round-trip, PNG validation and storage (M4-03).

The browser renders an orthographic top view with A1/D1/W1 labels and returns a
base64 PNG plus a legend. This module is the trust boundary for that payload:
it validates the base64/PNG, resizes the long edge to at most 1536 px, keeps the
file at or below 1 MB and stores it under ``data/screenshots/``.
"""

from __future__ import annotations

import asyncio
import base64
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

import structlog
from PIL import Image

from app.contracts.ws import CaptureResultPayload
from app.core.config import Settings

logger = structlog.get_logger(__name__)

MAX_LONG_EDGE = 1536
MAX_BYTES = 1_000_000
MAX_INPUT_BYTES = 8_000_000
MAX_PIXELS = 40_000_000
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$")

CaptureResolver = Callable[
    [str, str, dict[str, Any] | None], Awaitable[CaptureResultPayload | None]
]


class CaptureProvider(Protocol):
    """In-process screenshot source used by tests and headless CI."""

    async def capture(
        self,
        scene_id: str,
        turn_id: str,
        legend: dict[str, Any] | None = None,
    ) -> tuple[bytes, dict[str, Any]] | None: ...


@dataclass(frozen=True)
class CaptureImage:
    png: bytes
    legend: dict[str, Any]
    width: int
    height: int
    url: str | None = None


class CaptureService:
    def __init__(
        self,
        settings: Settings,
        *,
        resolver: CaptureResolver | None = None,
        provider: CaptureProvider | None = None,
    ) -> None:
        self._settings = settings
        self._resolver = resolver
        self._provider = provider
        self._root = settings.data_dir / "screenshots"

    def set_provider(self, provider: CaptureProvider | None) -> None:
        self._provider = provider

    async def request(
        self,
        scene_id: str,
        turn_id: str,
        *,
        purpose: str = "review",
        persistent: bool = False,
        legend: dict[str, Any] | None = None,
    ) -> CaptureImage | None:
        """Ask the host for a screenshot; never raises into the turn loop."""
        try:
            if self._provider is not None:
                result = await asyncio.wait_for(
                    self._provider.capture(scene_id, turn_id, legend),
                    timeout=self._settings.capture_timeout_seconds,
                )
                if result is None:
                    return None
                png, returned_legend = result
                chosen = (
                    returned_legend
                    if returned_legend.get("items") or returned_legend.get("openings")
                    else legend
                )
                image = await asyncio.to_thread(self._process, png, chosen or {})
            elif self._resolver is not None:
                payload = await self._resolver(scene_id, turn_id, legend)
                if payload is None:
                    return None
                image = await asyncio.to_thread(
                    self._process_base64,
                    payload.png_base64,
                    payload.legend or legend or {},
                )
            else:
                return None
            if persistent:
                url = await asyncio.to_thread(self._store, image, scene_id, turn_id, purpose)
                image = CaptureImage(
                    png=image.png,
                    legend=image.legend,
                    width=image.width,
                    height=image.height,
                    url=url,
                )
            return image
        except TimeoutError:
            logger.info("capture.timeout", scene_id=scene_id, turn_id=turn_id)
            return None
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - screenshots must never fail a turn
            logger.info(
                "capture.invalid",
                scene_id=scene_id,
                turn_id=turn_id,
                error=str(error),
            )
            return None

    def _process_base64(self, encoded: str, legend: dict[str, Any]) -> CaptureImage:
        if len(encoded) > MAX_INPUT_BYTES:
            raise ValueError("capture payload is too large")
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("capture payload is too large")
        return self._process(raw, legend)

    def _process(self, raw: bytes, legend: dict[str, Any]) -> CaptureImage:
        with Image.open(BytesIO(raw)) as probe:
            if probe.format != "PNG":
                raise ValueError("capture must be a PNG")
            width, height = probe.size
            if width <= 0 or height <= 0:
                raise ValueError("capture has no pixels")
            if width * height > MAX_PIXELS:
                raise ValueError(f"capture is too large ({width}x{height})")
        with Image.open(BytesIO(raw)) as image:
            converted = image.convert("RGBA")
            converted = _resize(converted)
            png, final = _encode_png(converted)
        return CaptureImage(
            png=png,
            legend=legend,
            width=final.width,
            height=final.height,
        )

    def _store(self, image: CaptureImage, scene_id: str, turn_id: str, purpose: str) -> str:
        safe_scene = scene_id if _safe_component(scene_id) else "scene"
        safe_turn = turn_id if _safe_component(turn_id) else "turn"
        safe_purpose = purpose if _safe_component(purpose) else "capture"
        folder = self._root / safe_scene
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{safe_turn}-{safe_purpose}.png"
        path.write_bytes(image.png)
        return f"/api/screenshots/{safe_scene}/{path.name}"

    def path_for(self, scene_id: str, name: str) -> Path | None:
        if not _safe_component(scene_id) or not _safe_component(name):
            return None
        if not name.endswith(".png"):
            return None
        path = (self._root / scene_id / name).resolve()
        root = self._root.resolve()
        if root not in path.parents or not path.is_file():
            return None
        return path

    def cleanup(self, *, now: float | None = None) -> int:
        """Delete screenshots older than the retention window (FR-16)."""
        if not self._root.is_dir():
            return 0
        current = time.time() if now is None else now
        cutoff = current - self._settings.screenshot_retention_days * 86400
        removed = 0
        for folder in self._root.iterdir():
            if not folder.is_dir():
                continue
            for path in folder.glob("*.png"):
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except OSError:  # pragma: no cover - best effort cleanup
                    continue
            try:
                if not any(folder.iterdir()):
                    shutil.rmtree(folder)
            except OSError:  # pragma: no cover
                continue
        return removed


def _safe_component(value: str) -> bool:
    return bool(SAFE_NAME.match(value)) and value not in {".", ".."}


def _resize(image: Image.Image) -> Image.Image:
    longest = max(image.width, image.height)
    if longest <= MAX_LONG_EDGE:
        return image
    scale = MAX_LONG_EDGE / longest
    return image.resize(
        (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
        Image.Resampling.LANCZOS,
    )


def _encode_png(image: Image.Image) -> tuple[bytes, Image.Image]:
    """Encode under 1 MB while keeping a real PNG (never JPEG)."""
    for scale in (1.0, 0.85, 0.7, 0.55, 0.4, 0.3):
        resized = image
        if scale < 1.0:
            resized = image.resize(
                (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
        buffer = BytesIO()
        resized.save(buffer, format="PNG", optimize=True)
        data = buffer.getvalue()
        if len(data) <= MAX_BYTES:
            return data, resized
        # Palette PNGs are much smaller; FASTOCTREE is the RGBA-safe method.
        quantized = resized.quantize(colors=128, method=Image.Quantize.FASTOCTREE)
        buffer = BytesIO()
        quantized.save(buffer, format="PNG", optimize=True)
        data = buffer.getvalue()
        if len(data) <= MAX_BYTES:
            return data, quantized
    raise ValueError("capture could not be encoded below 1 MB")


__all__ = ["CaptureImage", "CaptureProvider", "CaptureService"]
