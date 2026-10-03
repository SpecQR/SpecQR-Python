"""Command-line interface: python -m specqr or the installed specqr command."""
from __future__ import annotations
import argparse
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
import json
from pathlib import Path
import sys
from . import SpecQRError, generate, generate_segments


def _json(value):
    if is_dataclass(value):
        return {field.name: _json(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    if isinstance(value, bytes):
        return list(value)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate QR Code Model 2 without runtime dependencies.")
    parser.add_argument("text", nargs="?", help="Unicode text (or use --binary / --segments)")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--binary", type=Path, help="Read raw bytes from a file")
    source.add_argument("--segments", type=Path, help="Read a JSON array of manual segments")
    parser.add_argument("--format", choices=("svg", "png", "matrix", "svg-data-url", "png-data-url"), default="svg")
    parser.add_argument("--output", "-o", type=Path, help="Write output; default is stdout")
    parser.add_argument("--ecc", choices=tuple("LMQH"), default="M")
    parser.add_argument("--version", type=int)
    parser.add_argument("--mask", type=int)
    parser.add_argument("--mode", choices=("auto", "numeric", "alphanumeric", "byte", "kanji"), default="auto")
    parser.add_argument("--eci", type=int)
    parser.add_argument("--gs1", action="store_true")
    parser.add_argument("--scale", type=int, default=8)
    parser.add_argument("--margin", type=int, default=4)
    parser.add_argument("--foreground", default="#000000")
    parser.add_argument("--background", default="#ffffff")
    parser.add_argument("--diagnostics", action="store_true", help="Write diagnostic JSON to stderr")
    args = parser.parse_args(argv)
    if args.text is not None and (args.binary or args.segments):
        parser.error("text cannot be combined with --binary or --segments")
    if args.text is None and not (args.binary or args.segments):
        parser.error("provide text, --binary, or --segments")
    opts = dict(error_correction_level=args.ecc, version=args.version, mask_pattern=args.mask,
                mode=args.mode, eci=args.eci, gs1=args.gs1, scale=args.scale, margin=args.margin,
                foreground=args.foreground, background=args.background)
    try:
        if args.segments:
            if args.segments.stat().st_size > 4_000_000:
                raise ValueError("segment JSON exceeds 4 MB")
            segments = json.loads(args.segments.read_text("utf-8"))
            if isinstance(segments, list):
                for item in segments:
                    if isinstance(item, dict) and isinstance(item.get("bytes"), list):
                        if any(type(v) is not int or not 0 <= v <= 255 for v in item["bytes"]):
                            raise ValueError("segment bytes must be integers from 0 to 255")
                        item["bytes"] = bytes(item["bytes"])
            result = generate_segments(segments, **opts)
        else:
            if args.binary and args.binary.stat().st_size > 1_000_000:
                raise ValueError("binary input exceeds 1 MB")
            result = generate(args.binary.read_bytes() if args.binary else args.text, **opts)
        value = result.render(args.format)
        if args.format == "matrix":
            value = json.dumps(value, separators=(",", ":"))
        raw = value if isinstance(value, bytes) else value.encode("utf-8")
        if args.output:
            args.output.write_bytes(raw)
        else:
            sys.stdout.buffer.write(raw)
            if not isinstance(value, bytes):
                sys.stdout.buffer.write(b"\n")
        if args.diagnostics:
            print(json.dumps(_json(result.diagnostics), ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
        return 0
    except (SpecQRError, OSError, ValueError, UnicodeError) as error:
        print(f"specqr: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
