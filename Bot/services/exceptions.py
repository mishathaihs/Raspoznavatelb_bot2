class OcrError(Exception):
    """Raised when OCR fails or produces unreliable results."""


class SpreadsheetInitError(Exception):
    """Raised when Google Spreadsheet cannot be initialized or accessed."""


class ParseError(Exception):
    """Raised when document parsing fails."""


class DriveUploadError(Exception):
    """Raised when Google Drive upload fails for non-quota reasons."""


class DriveQuotaExceededError(Exception):
    """Raised when Google Drive reports storage quota exceeded."""


class PersistDriveError(Exception):
    """Raised when document persistence fails due to Drive issues."""


__all__ = [
    "OcrError",
    "SpreadsheetInitError",
    "ParseError",
    "DriveUploadError",
    "DriveQuotaExceededError",
    "PersistDriveError",
]
