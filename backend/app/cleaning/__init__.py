"""C0 cleaning gate: detect data-quality issues after profiling and apply the fixes the user approves."""

from .cleaner import DataCleaner
from .detector import IssueDetector
from .notes import data_notes

__all__ = ["DataCleaner", "IssueDetector", "data_notes"]
