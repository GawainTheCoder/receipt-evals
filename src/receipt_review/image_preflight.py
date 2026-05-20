from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps


MIN_ROTATION_CONFIDENCE = 1.0


@dataclass(frozen=True)
class OrientationResult:
    rotate_degrees: int
    confidence: float | None


def prepare_receipt_image(
    image_path: str | Path,
    *,
    output_dir: str | Path = "outputs/preprocessed",
) -> Path:
    path = Path(image_path)
    orientation = detect_orientation(path)

    if not should_rotate(orientation):
        return path

    rotated_path = Path(output_dir) / f"{path.stem}.rotated_{orientation.rotate_degrees}{path.suffix}"
    rotated_path.parent.mkdir(parents=True, exist_ok=True)
    rotate_image(path, rotated_path, orientation.rotate_degrees)
    return rotated_path


def detect_orientation(image_path: Path) -> OrientationResult:
    if not shutil.which("tesseract"):
        return OrientationResult(rotate_degrees=0, confidence=None)

    try:
        result = subprocess.run(
            ["tesseract", str(image_path), "stdout", "--psm", "0"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return OrientationResult(rotate_degrees=0, confidence=None)

    rotate_match = re.search(r"^Rotate:\s*(\d+)\s*$", result.stdout, re.MULTILINE)
    confidence_match = re.search(r"^Orientation confidence:\s*([0-9.]+)\s*$", result.stdout, re.MULTILINE)
    if not rotate_match:
        return OrientationResult(rotate_degrees=0, confidence=None)

    confidence = float(confidence_match.group(1)) if confidence_match else None
    return OrientationResult(rotate_degrees=int(rotate_match.group(1)) % 360, confidence=confidence)


def should_rotate(orientation: OrientationResult) -> bool:
    if orientation.rotate_degrees != 180:
        return False
    if orientation.confidence is None:
        return False
    return orientation.confidence >= MIN_ROTATION_CONFIDENCE


def rotate_image(source_path: Path, destination_path: Path, rotate_degrees: int) -> None:
    with Image.open(source_path) as image:
        normalized_image = ImageOps.exif_transpose(image)
        normalized_image.rotate(rotate_degrees, expand=True).save(destination_path, quality=95)
