#!/usr/bin/env python3
"""Validate the checked-in, dependency-free brand asset contract."""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path
from xml.etree import ElementTree


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES = 100_000
MAX_DECOMPRESSED_BYTES = 5_000_000
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
BRAND_COLORS = {"#0F172A", "#F1F5F9", "#0284C7", "#38BDF8", "#B45309", "#FBBF24"}
FORBIDDEN_MARKERS = ("openai", "google", "gemini", "claude", "github")
FORBIDDEN_XML_SYNTAX = ("<!doctype", "<!entity", "<?xml-stylesheet")
FORBIDDEN_VALUE_SYNTAX = ("@import", "url(", "javascript:", "data:", "http://", "https://", "//")
SVG_ALLOWED_ATTRIBUTES = {
    "svg": {"width", "height", "viewBox", "role", "aria-labelledby", "shape-rendering"},
    "title": {"id"},
    "desc": {"id"},
    "path": {"fill", "d"},
}

PNG_CONTRACT = {
    "logo-micro-light-16.png": (16, 16, 6),
    "logo-micro-light-32.png": (32, 32, 6),
    "logo-micro-light-64.png": (64, 64, 6),
    "logo-micro-dark-16.png": (16, 16, 6),
    "logo-micro-dark-32.png": (32, 32, 6),
    "logo-micro-dark-64.png": (64, 64, 6),
    "social-preview-1280x640.png": (1280, 640, 2),
}

SVG_CONTRACT = {
    "logo-light.svg": (
        "1024",
        "1024",
        "0 0 1024 1024",
        {"#0F172A", "#0284C7", "#B45309"},
    ),
    "logo-dark.svg": (
        "1024",
        "1024",
        "0 0 1024 1024",
        {"#F1F5F9", "#38BDF8", "#FBBF24"},
    ),
    "logo-micro-light.svg": (
        "16",
        "16",
        "0 0 16 16",
        {"#0F172A", "#0284C7", "#B45309"},
    ),
    "logo-micro-dark.svg": (
        "16",
        "16",
        "0 0 16 16",
        {"#F1F5F9", "#38BDF8", "#FBBF24"},
    ),
}


def local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def validate_png(path: Path, expected: tuple[int, int, int]) -> None:
    data = path.read_bytes()
    if len(data) > MAX_PNG_BYTES:
        raise ValueError(f"{path.name}: unexpectedly large ({len(data)} bytes)")
    if len(data) < len(PNG_SIGNATURE) or data[:8] != PNG_SIGNATURE:
        raise ValueError(f"{path.name}: not a PNG")

    cursor = len(PNG_SIGNATURE)
    chunk_index = 0
    ihdr = None
    seen_idat = False
    idat_ended = False
    seen_iend = False
    idat_payloads = []
    while cursor < len(data):
        if len(data) - cursor < 12:
            raise ValueError(f"{path.name}: truncated PNG chunk")
        length = struct.unpack(">I", data[cursor : cursor + 4])[0]
        if length > MAX_PNG_BYTES:
            raise ValueError(f"{path.name}: chunk length exceeds asset bound")
        chunk_end = cursor + 12 + length
        if chunk_end > len(data):
            raise ValueError(f"{path.name}: truncated PNG chunk")
        chunk_type = data[cursor + 4 : cursor + 8]
        chunk_data = data[cursor + 8 : cursor + 8 + length]
        if len(chunk_type) != 4 or not all(
            ord("A") <= byte <= ord("Z") or ord("a") <= byte <= ord("z")
            for byte in chunk_type
        ):
            raise ValueError(f"{path.name}: invalid PNG chunk type")
        stored_crc = struct.unpack(">I", data[cursor + 8 + length : chunk_end])[0]
        calculated_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if stored_crc != calculated_crc:
            raise ValueError(f"{path.name}: invalid CRC for {chunk_type.decode('ascii')}")
        if chunk_type == b"tRNS":
            raise ValueError(f"{path.name}: tRNS is forbidden for contract assets")

        if chunk_index == 0 and chunk_type != b"IHDR":
            raise ValueError(f"{path.name}: IHDR is not the first chunk")
        if chunk_type == b"IHDR":
            if ihdr is not None or chunk_index != 0 or length != 13:
                raise ValueError(f"{path.name}: IHDR must be single, first, and length 13")
            ihdr = chunk_data
        elif chunk_type == b"IDAT":
            if idat_ended:
                raise ValueError(f"{path.name}: IDAT chunks are not consecutive")
            seen_idat = True
            idat_payloads.append(chunk_data)
        elif seen_idat and chunk_type != b"IEND":
            idat_ended = True

        if chunk_type == b"IEND":
            if seen_iend or length != 0:
                raise ValueError(f"{path.name}: IEND must be single and length 0")
            if not seen_idat:
                raise ValueError(f"{path.name}: missing IDAT before IEND")
            seen_iend = True
            cursor = chunk_end
            if cursor != len(data):
                raise ValueError(f"{path.name}: trailing bytes after IEND")
            break

        cursor = chunk_end
        chunk_index += 1

    if ihdr is None:
        raise ValueError(f"{path.name}: missing IHDR")
    if not seen_idat:
        raise ValueError(f"{path.name}: missing IDAT")
    if not seen_iend:
        raise ValueError(f"{path.name}: missing IEND")

    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    expected_width, expected_height, expected_color_type = expected
    if (width, height) != (expected_width, expected_height):
        raise ValueError(
            f"{path.name}: expected {expected_width}x{expected_height}, got {width}x{height}"
        )
    if bit_depth != 8 or color_type != expected_color_type:
        raise ValueError(
            f"{path.name}: expected 8-bit color type {expected_color_type}, "
            f"got {bit_depth}-bit type {color_type}"
        )
    if compression != 0 or filtering != 0 or interlace != 0:
        raise ValueError(f"{path.name}: invalid IHDR method fields")

    bytes_per_pixel = {2: 3, 6: 4}[color_type]
    row_size = 1 + width * bytes_per_pixel
    expected_decompressed_size = height * row_size
    if expected_decompressed_size > MAX_DECOMPRESSED_BYTES:
        raise ValueError(f"{path.name}: decompressed image exceeds asset bound")
    decompressor = zlib.decompressobj()
    try:
        scanlines = decompressor.decompress(
            b"".join(idat_payloads), expected_decompressed_size + 1
        )
        if len(scanlines) > expected_decompressed_size or decompressor.unconsumed_tail:
            raise ValueError(f"{path.name}: decompressed image exceeds expected size")
        remaining = expected_decompressed_size + 1 - len(scanlines)
        if remaining:
            scanlines += decompressor.flush(remaining)
    except zlib.error as exc:
        raise ValueError(f"{path.name}: invalid IDAT zlib stream") from exc
    if not decompressor.eof:
        raise ValueError(f"{path.name}: incomplete IDAT zlib stream")
    if decompressor.unused_data or decompressor.unconsumed_tail:
        raise ValueError(f"{path.name}: trailing compressed IDAT data")
    if len(scanlines) != expected_decompressed_size:
        raise ValueError(
            f"{path.name}: expected {expected_decompressed_size} decompressed bytes, "
            f"got {len(scanlines)}"
        )
    for row in range(height):
        filter_type = scanlines[row * row_size]
        if filter_type > 4:
            raise ValueError(f"{path.name}: invalid PNG filter byte in row {row}")


def validate_svg(path: Path, expected: tuple[str, str, str, set[str]]) -> tuple[str, ...]:
    text = path.read_text(encoding="utf-8")
    lowered = text.lower()
    for marker in FORBIDDEN_MARKERS:
        if marker in lowered:
            raise ValueError(f"{path.name}: contains vendor marker {marker!r}")
    for token in FORBIDDEN_XML_SYNTAX:
        if token in lowered:
            raise ValueError(f"{path.name}: contains forbidden XML syntax {token!r}")

    root = ElementTree.fromstring(text)
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        raise ValueError(f"{path.name}: root element is not svg")
    width, height, view_box, expected_fills = expected
    expected_root = {
        "width": width,
        "height": height,
        "viewBox": view_box,
        "role": "img",
        "aria-labelledby": "title desc",
    }
    if width == "16":
        expected_root["shape-rendering"] = "crispEdges"

    paths = 0
    fills = set()
    geometry = []
    for element in root.iter():
        if not isinstance(element.tag, str) or not element.tag.startswith(
            f"{{{SVG_NAMESPACE}}}"
        ):
            raise ValueError(f"{path.name}: element outside the SVG namespace")
        name = local_name(element.tag)
        if name not in SVG_ALLOWED_ATTRIBUTES:
            raise ValueError(f"{path.name}: forbidden {name} element")
        allowed_attributes = SVG_ALLOWED_ATTRIBUTES[name]
        for attribute, value in element.attrib.items():
            if attribute.startswith("{"):
                raise ValueError(f"{path.name}: namespaced attributes are forbidden")
            if attribute.lower().startswith("on"):
                raise ValueError(f"{path.name}: event attributes are forbidden")
            if attribute not in allowed_attributes:
                raise ValueError(f"{path.name}: forbidden {name} attribute {attribute!r}")
            lowered_value = value.lower()
            if any(token in lowered_value for token in FORBIDDEN_VALUE_SYNTAX):
                raise ValueError(f"{path.name}: external or executable attribute value")
        if element.text and any(
            token in element.text.lower() for token in FORBIDDEN_VALUE_SYNTAX
        ):
            raise ValueError(f"{path.name}: external or executable text content")
        if name == "path":
            paths += 1
            fills.add(element.attrib.get("fill", ""))
            if set(element.attrib) != {"fill", "d"} or not element.attrib["d"]:
                raise ValueError(f"{path.name}: path attributes changed")
            geometry.append(element.attrib["d"])
    if root.attrib != expected_root:
        raise ValueError(f"{path.name}: dimensions or root attributes changed")
    child_names = [local_name(child.tag) for child in root]
    if child_names != ["title", "desc", "path", "path", "path"]:
        raise ValueError(f"{path.name}: unexpected SVG element structure")
    if root[0].attrib != {"id": "title"} or root[1].attrib != {"id": "desc"}:
        raise ValueError(f"{path.name}: accessible title/description IDs changed")
    if paths != 3 or fills != expected_fills or not fills.issubset(BRAND_COLORS):
        raise ValueError(f"{path.name}: unexpected geometry or color palette")
    if len(text.encode("utf-8")) > 10_000:
        raise ValueError(f"{path.name}: unexpectedly large")
    return tuple(geometry)


def main() -> int:
    if len(sys.argv) > 2:
        print(f"usage: {Path(sys.argv[0]).name} [BRAND_ASSET_DIR]", file=sys.stderr)
        return 64
    brand_dir = (
        Path(sys.argv[1])
        if len(sys.argv) == 2
        else Path(__file__).resolve().parents[1] / "docs/assets/brand"
    )
    try:
        svg_geometry = {}
        for name, contract in SVG_CONTRACT.items():
            svg_geometry[name] = validate_svg(brand_dir / name, contract)
        for light, dark in (
            ("logo-light.svg", "logo-dark.svg"),
            ("logo-micro-light.svg", "logo-micro-dark.svg"),
        ):
            if svg_geometry[light] != svg_geometry[dark]:
                raise ValueError(f"{light}/{dark}: ordered geometry diverged")
        for name, contract in PNG_CONTRACT.items():
            validate_png(brand_dir / name, contract)
    except (OSError, ElementTree.ParseError, ValueError) as exc:
        print(f"brand asset validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"brand asset validation passed: {len(SVG_CONTRACT)} SVG, {len(PNG_CONTRACT)} PNG")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
