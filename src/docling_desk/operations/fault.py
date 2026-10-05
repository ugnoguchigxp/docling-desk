"""Compatibility wrapper. New checks should import faults."""

from docling_desk.operations.faults import Fault as QualityFault
from docling_desk.operations.faults import checkpoint

__all__ = ["QualityFault", "checkpoint"]
