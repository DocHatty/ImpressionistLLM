"""Automatic layout-aware screenshot preprocessing for vision requests.

This module intentionally avoids diagnostic enhancement. It only preserves the
original screenshot and adds conservative layout crops/zooms that help vision
models inspect multi-panel screenshots.
"""

from __future__ import annotations

import base64
import binascii
import io
import math
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._shared import json, logger

try:  # pragma: no cover - exercised by integration tests when vendored deps exist
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageOps

    IMAGE_STACK_AVAILABLE = True
    IMAGE_STACK_ERROR = ""
except Exception as exc:  # noqa: BLE001 - graceful feature fallback
    cv2 = None
    np = None
    Image = None
    ImageDraw = None
    ImageOps = None
    IMAGE_STACK_AVAILABLE = False
    IMAGE_STACK_ERROR = str(exc)


ABSOLUTE_MAX_OUTPUT_IMAGES = 10
DEFAULT_MAX_OUTPUT_IMAGES = 8
DEFAULT_MAX_REQUEST_BYTES = 9_000_000
DEFAULT_MAX_EDGE = 1600
DEFAULT_MIN_PANEL_AREA_RATIO = 0.045
DEFAULT_CONFIDENCE_THRESHOLD = 0.70
DEFAULT_CROP_PADDING_PERCENT = 4.0
DEFAULT_MAX_DERIVED_PER_ORIGINAL = 4
DEFAULT_DEBUG_RETENTION_HOURS = 48


@dataclass
class CandidateBox:
    x: int
    y: int
    w: int
    h: int
    confidence: float
    kind: str = "panel"

    @property
    def area(self) -> int:
        return max(0, self.w) * max(0, self.h)

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.w, self.y + self.h)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any, default: int, low: int | None = None, high: int | None = None) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    if low is not None:
        parsed = max(low, parsed)
    if high is not None:
        parsed = min(high, parsed)
    return parsed


def _as_float(value: Any, default: float, low: float | None = None, high: float | None = None) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    if low is not None:
        parsed = max(low, parsed)
    if high is not None:
        parsed = min(high, parsed)
    return parsed


def _normalize_config(config: dict[str, Any] | None) -> dict[str, Any]:
    raw = config if isinstance(config, dict) else {}
    max_images = _as_int(raw.get("max_output_images"), DEFAULT_MAX_OUTPUT_IMAGES, 1, ABSOLUTE_MAX_OUTPUT_IMAGES)
    return {
        "enabled": _as_bool(raw.get("enabled"), True),
        "debug": _as_bool(raw.get("debug"), False),
        "save_contact_sheet": _as_bool(raw.get("save_contact_sheet"), True),
        "save_derived_images": _as_bool(raw.get("save_derived_images"), False),
        "generate_zooms": _as_bool(raw.get("generate_zooms"), True),
        "max_output_images": max_images,
        "max_request_bytes": _as_int(raw.get("max_request_bytes"), DEFAULT_MAX_REQUEST_BYTES, 1_000_000, 9_800_000),
        "max_edge": _as_int(raw.get("max_edge"), DEFAULT_MAX_EDGE, 768, 2400),
        "min_panel_area_ratio": _as_float(raw.get("min_panel_area_ratio"), DEFAULT_MIN_PANEL_AREA_RATIO, 0.01, 0.30),
        "panel_confidence_threshold": _as_float(raw.get("panel_confidence_threshold"), DEFAULT_CONFIDENCE_THRESHOLD, 0.25, 0.95),
        "crop_padding_percent": _as_float(raw.get("crop_padding_percent"), DEFAULT_CROP_PADDING_PERCENT, 0.0, 12.0),
        "max_derived_per_original": _as_int(raw.get("max_derived_per_original"), DEFAULT_MAX_DERIVED_PER_ORIGINAL, 0, 8),
        "debug_retention_hours": _as_int(raw.get("debug_retention_hours"), DEFAULT_DEBUG_RETENTION_HOURS, 1, 24 * 14),
        "temp_dir": str(raw.get("temp_dir") or ""),
    }


def _decode_base64_image(base64_text: str):
    if not IMAGE_STACK_AVAILABLE:
        raise RuntimeError("Image stack unavailable: " + IMAGE_STACK_ERROR)
    raw = str(base64_text or "").strip()
    if "," in raw and raw.lower().startswith("data:image"):
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=True)
    except binascii.Error as exc:
        raise ValueError("Invalid base64 image") from exc
    image = Image.open(io.BytesIO(data))
    image.load()
    return image.convert("RGB")


def _encode_image_base64(image, max_edge: int = DEFAULT_MAX_EDGE, fmt: str = "JPEG", quality: int = 90) -> str:
    """Resize (preserving aspect) and base64-encode an image for vision LLM use.

    Default JPEG quality 90 cuts payload ~2.5x vs PNG for screenshot/panel content
    with no perceptible loss to vision models. PNG is still selectable for callers
    that need it.
    """
    img = image.convert("RGB")
    w, h = img.size
    longest = max(w, h)
    if longest > max_edge:
        scale = max_edge / float(longest)
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    fmt_upper = (fmt or "JPEG").upper()
    if fmt_upper == "JPEG":
        img.save(buf, format="JPEG", quality=int(quality), optimize=True, progressive=False)
    elif fmt_upper == "WEBP":
        img.save(buf, format="WEBP", quality=int(quality), method=4)
    else:
        img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# Backwards-compatible alias — some call sites may still reference the old name.
# Kept lossless (PNG) so existing semantics are preserved for any external caller.
def _encode_png_base64(image, max_edge: int = DEFAULT_MAX_EDGE) -> str:
    return _encode_image_base64(image, max_edge=max_edge, fmt="PNG")


def _pad_box(box: tuple[int, int, int, int], image_size: tuple[int, int], pad_percent: float) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    w, h = image_size
    pad_x = round((x2 - x1) * pad_percent / 100.0)
    pad_y = round((y2 - y1) * pad_percent / 100.0)
    return (
        max(0, x1 - pad_x),
        max(0, y1 - pad_y),
        min(w, x2 + pad_x),
        min(h, y2 + pad_y),
    )


def _iou(a: CandidateBox, b: CandidateBox) -> float:
    ax1, ay1, ax2, ay2 = a.box
    bx1, by1, bx2, by2 = b.box
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = a.area + b.area - inter
    return inter / float(union or 1)


def _sort_boxes_reading_order(boxes: list[CandidateBox]) -> list[CandidateBox]:
    if not boxes:
        return []
    avg_h = sum(b.h for b in boxes) / max(1, len(boxes))
    row_tol = max(24, avg_h * 0.35)
    return sorted(boxes, key=lambda b: (round(b.y / row_tol), b.x))


def _nms_candidates(candidates: list[CandidateBox], iou_threshold: float = 0.62) -> list[CandidateBox]:
    ordered = sorted(candidates, key=lambda b: (b.confidence, b.area), reverse=True)
    kept: list[CandidateBox] = []
    for cand in ordered:
        if all(_iou(cand, existing) < iou_threshold for existing in kept):
            kept.append(cand)
    return _sort_boxes_reading_order(kept)


def _detect_panels(image, config: dict[str, Any]) -> list[CandidateBox]:
    if not IMAGE_STACK_AVAILABLE:
        return []

    width, height = image.size
    if width < 120 or height < 120:
        return []

    arr = np.asarray(image.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

    longest = max(width, height)
    scale = 1.0
    if longest > 1400:
        scale = 1400.0 / longest
        gray_work = cv2.resize(gray, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    else:
        gray_work = gray

    wh, ww = gray_work.shape[:2]
    if ww < 120 or wh < 120:
        return []

    non_dark = (gray_work > 14).astype("uint8") * 255
    edges = cv2.Canny(gray_work, 28, 92)
    edge_kernel_size = max(3, int(round(min(ww, wh) * 0.012)))
    close_kernel_size = max(9, int(round(min(ww, wh) * 0.026)))
    edge_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (edge_kernel_size, edge_kernel_size))
    close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (close_kernel_size, close_kernel_size))

    edges = cv2.dilate(edges, edge_kernel, iterations=1)
    mask = cv2.bitwise_or(non_dark, edges)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, edge_kernel, iterations=1)

    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    min_area_ratio = float(config["min_panel_area_ratio"])
    threshold = float(config["panel_confidence_threshold"])
    image_area = float(ww * wh)
    candidates: list[CandidateBox] = []

    for idx in range(1, num_labels):
        x, y, bw, bh, stat_area = [int(v) for v in stats[idx]]
        if bw < 80 or bh < 80:
            continue
        area_ratio = (bw * bh) / image_area
        if area_ratio < min_area_ratio:
            continue
        if bw > ww * 0.97 and bh > wh * 0.97:
            continue

        aspect = bw / float(bh or 1)
        aspect_score = 1.0 if 0.20 <= aspect <= 5.5 else 0.45
        fill_ratio = stat_area / float(bw * bh or 1)
        fill_score = max(0.0, min(1.0, fill_ratio * 2.6))
        area_score = max(0.0, min(1.0, (area_ratio - min_area_ratio) / max(0.001, 0.42 - min_area_ratio)))
        border_score = 1.0
        if x <= 3 or y <= 3 or x + bw >= ww - 3 or y + bh >= wh - 3:
            border_score = 0.72

        confidence = 0.30 + 0.30 * area_score + 0.20 * aspect_score + 0.15 * fill_score + 0.05 * border_score
        if confidence < threshold:
            continue

        ox1 = max(0, int(math.floor(x / scale)))
        oy1 = max(0, int(math.floor(y / scale)))
        ox2 = min(width, int(math.ceil((x + bw) / scale)))
        oy2 = min(height, int(math.ceil((y + bh) / scale)))
        candidates.append(CandidateBox(ox1, oy1, ox2 - ox1, oy2 - oy1, confidence))

    candidates = _nms_candidates(candidates)

    # A single low-coverage component is usually anatomy-only noise, not a panel.
    if len(candidates) == 1:
        ratio = candidates[0].area / float(width * height)
        if ratio < max(0.10, min_area_ratio * 1.8):
            return []

    return candidates


def _center_zoom_box(box: tuple[int, int, int, int], image_size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    if bw < 520 or bh < 420:
        return None
    zoom_w = round(bw * 0.64)
    zoom_h = round(bh * 0.64)
    cx = x1 + bw // 2
    cy = y1 + bh // 2
    img_w, img_h = image_size
    zx1 = max(0, cx - zoom_w // 2)
    zy1 = max(0, cy - zoom_h // 2)
    zx2 = min(img_w, zx1 + zoom_w)
    zy2 = min(img_h, zy1 + zoom_h)
    zx1 = max(0, zx2 - zoom_w)
    zy1 = max(0, zy2 - zoom_h)
    if zx2 - zx1 < 220 or zy2 - zy1 < 220:
        return None
    return (zx1, zy1, zx2, zy2)


def _payload_size(items: list[dict[str, Any]]) -> int:
    return sum(len(str(item.get("base64") or "")) + len(str(item.get("label") or "")) + 128 for item in items)


def _build_image_guide(items: list[dict[str, Any]]) -> str:
    lines = [
        "Image map:",
        "Use the original overview image for orientation and sanity checks. Derived panel/zoom images are crops of the same screenshot for detail inspection.",
    ]
    for i, item in enumerate(items, start=1):
        lines.append(f"{i}. {item.get('label') or 'image'}")
    return "\n".join(lines)


def _safe_temp_root(temp_dir: str) -> Path:
    base = Path(temp_dir).expanduser() if temp_dir else Path(tempfile.gettempdir()) / "ImpressionistLLM"
    return base / "image_preprocess"


def _cleanup_old_debug_dirs(root: Path, retention_hours: int) -> None:
    try:
        if not root.exists():
            return
        cutoff = time.time() - (retention_hours * 3600)
        for child in root.iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
            except Exception:
                continue
    except Exception:
        return


def _make_contact_sheet(items: list[dict[str, Any]], debug_dir: Path) -> Path | None:
    if not IMAGE_STACK_AVAILABLE or not items:
        return None

    thumbs = []
    for idx, item in enumerate(items, start=1):
        try:
            img = _decode_base64_image(str(item.get("base64") or ""))
            img = ImageOps.contain(img, (320, 220), method=Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (340, 280), "white")
            canvas.paste(img, ((340 - img.width) // 2, 8))
            draw = ImageDraw.Draw(canvas)
            label = f"{idx}. {item.get('label') or item.get('kind') or 'image'}"
            draw.text((10, 236), label[:62], fill=(0, 0, 0))
            thumbs.append(canvas)
        except Exception:
            continue

    if not thumbs:
        return None

    cols = min(3, len(thumbs))
    rows = math.ceil(len(thumbs) / cols)
    sheet = Image.new("RGB", (cols * 340, rows * 280), "white")
    for i, thumb in enumerate(thumbs):
        x = (i % cols) * 340
        y = (i // cols) * 280
        sheet.paste(thumb, (x, y))
    path = debug_dir / "contact_sheet.png"
    sheet.save(path, "PNG")
    return path


def _write_debug_artifacts(
    items: list[dict[str, Any]],
    manifest: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, str]:
    if not (config["debug"] and config["save_contact_sheet"]):
        return {}

    root = _safe_temp_root(str(config.get("temp_dir") or ""))
    _cleanup_old_debug_dirs(root, int(config["debug_retention_hours"]))
    debug_dir = root / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    debug_dir.mkdir(parents=True, exist_ok=True)

    contact_sheet = _make_contact_sheet(items, debug_dir)
    manifest_path = debug_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    if config["save_derived_images"]:
        for i, item in enumerate(items, start=1):
            try:
                raw = base64.b64decode(str(item.get("base64") or ""), validate=True)
                (debug_dir / f"image_{i:02d}_{item.get('kind') or 'image'}.png").write_bytes(raw)
            except Exception:
                continue

    debug: dict[str, str] = {"manifest_path": str(manifest_path)}
    if contact_sheet:
        debug["contact_sheet_path"] = str(contact_sheet)
    return debug


def preprocess_images_payload(payload: dict[str, Any]) -> dict[str, Any]:
    config = _normalize_config(payload.get("config") if isinstance(payload, dict) else {})
    raw_images = payload.get("images") if isinstance(payload, dict) else []
    if not isinstance(raw_images, list):
        raw_images = []

    originals: list[dict[str, Any]] = []
    for pos, raw_item in enumerate(raw_images, start=1):
        if isinstance(raw_item, dict):
            b64 = str(raw_item.get("base64") or "").strip()
            source_index = _as_int(raw_item.get("source_index"), pos, 1, 999)
        else:
            b64 = str(raw_item or "").strip()
            source_index = pos
        if not b64:
            continue
        originals.append(
            {
                "label": f"original overview from capture {source_index}",
                "kind": "original",
                "source_index": source_index,
                "base64": b64,
                "priority": 0,
            }
        )

    if not originals:
        return {"ok": False, "error": "No images supplied", "images": [], "image_guide": ""}

    cap = max(len(originals), int(config["max_output_images"]))
    cap = min(ABSOLUTE_MAX_OUTPUT_IMAGES, cap)

    if not config["enabled"] or not IMAGE_STACK_AVAILABLE:
        reason = "" if config["enabled"] else "disabled"
        if config["enabled"] and not IMAGE_STACK_AVAILABLE:
            reason = "image stack unavailable: " + IMAGE_STACK_ERROR
        items = originals[:cap]
        return {
            "ok": True,
            "images": [{k: v for k, v in item.items() if k != "priority"} for item in items],
            "image_guide": _build_image_guide(items),
            "debug": {},
            "warnings": [reason] if reason else [],
        }

    output_items: list[dict[str, Any]] = list(originals)
    manifest: dict[str, Any] = {"items": [], "warnings": [], "config": {k: v for k, v in config.items() if k != "temp_dir"}}

    for original in originals:
        if len(output_items) >= cap:
            break
        source_index = int(original["source_index"])
        try:
            image = _decode_base64_image(str(original["base64"]))
            boxes = _detect_panels(image, config)
        except Exception as exc:  # noqa: BLE001 - fallback is intentional
            logger.warning("Image preprocessing failed for capture %s: %s", source_index, exc)
            manifest["warnings"].append(f"capture {source_index}: {exc}")
            continue

        added_for_source = 0
        for panel_idx, cand in enumerate(boxes, start=1):
            if len(output_items) >= cap or added_for_source >= int(config["max_derived_per_original"]):
                break
            padded = _pad_box(cand.box, image.size, float(config["crop_padding_percent"]))
            crop = image.crop(padded)
            b64 = _encode_image_base64(crop, int(config["max_edge"]), fmt="JPEG", quality=90)
            item = {
                "label": f"panel crop {panel_idx} from capture {source_index} (confidence {cand.confidence:.2f})",
                "kind": "panel",
                "source_index": source_index,
                "base64": b64,
                "box": list(padded),
                "confidence": round(cand.confidence, 3),
                "priority": 10 + panel_idx,
            }
            output_items.append(item)
            added_for_source += 1

            if (
                config["generate_zooms"]
                and len(output_items) < cap
                and added_for_source < int(config["max_derived_per_original"])
            ):
                zoom_box = _center_zoom_box(padded, image.size)
                if zoom_box:
                    zoom_crop = image.crop(zoom_box)
                    zoom_b64 = _encode_image_base64(zoom_crop, int(config["max_edge"]), fmt="JPEG", quality=90)
                    output_items.append(
                        {
                            "label": f"center detail zoom of panel {panel_idx} from capture {source_index}",
                            "kind": "zoom",
                            "source_index": source_index,
                            "base64": zoom_b64,
                            "box": list(zoom_box),
                            "confidence": round(cand.confidence, 3),
                            "priority": 40 + panel_idx,
                        }
                    )
                    added_for_source += 1

    # Enforce payload cap by dropping least important derived images first.
    while _payload_size(output_items) > int(config["max_request_bytes"]):
        removable = [i for i, item in enumerate(output_items) if item.get("kind") != "original"]
        if not removable:
            manifest["warnings"].append("original images exceed configured request budget")
            break
        drop_index = max(removable, key=lambda i: int(output_items[i].get("priority") or 99))
        output_items.pop(drop_index)

    public_items = [
        {k: v for k, v in item.items() if k not in {"priority"}}
        for item in output_items[:cap]
    ]
    manifest["items"] = [
        {k: v for k, v in item.items() if k not in {"base64", "priority"}}
        for item in public_items
    ]
    manifest["output_count"] = len(public_items)
    manifest["estimated_payload_bytes"] = _payload_size(public_items)
    debug = _write_debug_artifacts(public_items, manifest, config)

    return {
        "ok": True,
        "images": public_items,
        "image_guide": _build_image_guide(public_items),
        "debug": debug,
        "warnings": manifest.get("warnings") or [],
    }


class ImagePreprocessMixin:
    """HTTP handler mixin for local screenshot image preprocessing."""

    def preprocess_screenshot_images(self, body: str) -> None:
        data = self._parse_json_body(body)
        if data is None:
            return
        try:
            result = preprocess_images_payload(data)
            self.send_json(result, 200 if result.get("ok") else 400)
        except Exception as exc:  # noqa: BLE001 - local endpoint must not crash server
            logger.exception("Screenshot image preprocessing failed")
            self.send_json({"ok": False, "error": str(exc), "images": [], "image_guide": ""}, 500)
