"""Stable, typed SpecQR exceptions."""

class SpecQRError(ValueError):
    """Base class for invalid data, options, and capacity failures."""
    code = "SPECQR_ERROR"

class DataTooLongError(SpecQRError):
    code = "DATA_TOO_LONG"

class InvalidInputError(SpecQRError):
    code = "INVALID_INPUT"

class InvalidVersionError(SpecQRError):
    code = "INVALID_VERSION"

class InvalidModeError(SpecQRError):
    code = "INVALID_MODE"

class InvalidColorError(SpecQRError):
    code = "INVALID_COLOR"

class InvalidEciError(SpecQRError):
    code = "INVALID_ECI"

class InvalidGs1Error(SpecQRError):
    code = "INVALID_GS1"

class InvalidOutputError(SpecQRError):
    code = "INVALID_OUTPUT"
