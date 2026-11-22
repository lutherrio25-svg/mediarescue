"""mediarescue — diagnose and repair damaged audio/video without touching originals."""
from .diagnose import diagnose
from .report import Diagnosis, Severity

__version__ = "0.9.2"
__all__ = ["Diagnosis", "Severity", "__version__", "diagnose"]
