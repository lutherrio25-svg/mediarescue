"""mediarescue — diagnose and repair damaged audio/video without touching originals."""
from .diagnose import diagnose
from .report import Diagnosis, Severity

__version__ = "0.5.0"
__all__ = ["diagnose", "Diagnosis", "Severity", "__version__"]
