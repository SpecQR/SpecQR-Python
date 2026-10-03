"""Dependency-free QR Code Model 2 generation, planning, GS1 and rendering."""
from .api import Capacity, Options, Plan, QRResult, analyze_segments, estimate, generate, generate_segments, get_capacity
from .segments import Segment
from .render import Pixels, to_png, to_png_data_url, to_svg, to_svg_data_url, to_pixels
from .errors import (SpecQRError, DataTooLongError, InvalidInputError, InvalidVersionError,
                     InvalidModeError, InvalidColorError, InvalidEciError, InvalidGs1Error, InvalidOutputError)
from .gs1 import *
from .gs1 import __all__ as _gs1_exports

__version__ = "0.1.0rc1"
__all__ = ["Capacity", "Options", "Plan", "QRResult", "Segment", "Pixels", "generate", "generate_segments",
           "estimate", "analyze_segments", "get_capacity", "to_png", "to_png_data_url", "to_svg", "to_svg_data_url", "to_pixels",
           "SpecQRError", "DataTooLongError", "InvalidInputError", "InvalidVersionError", "InvalidModeError", "InvalidColorError",
           "InvalidEciError", "InvalidGs1Error", "InvalidOutputError"] + _gs1_exports

from .structured_append import (SAResult, MergeResult, generate_structured_append,
    generate_segments_structured_append, calculate_structured_append_parity,
    calculate_structured_append_segments_parity, merge_structured_append_parts)
__all__ += ["SAResult", "MergeResult", "generate_structured_append", "generate_segments_structured_append",
            "calculate_structured_append_parity", "calculate_structured_append_segments_parity", "merge_structured_append_parts"]
