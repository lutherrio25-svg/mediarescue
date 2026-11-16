"""Structure and severity model for a single damaged media file."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path


class Severity(str, Enum):
    """Damage class, ordered roughly from trivially fixable to unrecoverable."""

    HEALTHY = "healthy"
    CORRUPT_PAYLOAD = "corrupt-payload"
    CORRUPT_INDEX = "corrupt-index"
    MISSING_TRAILER = "missing-trailer"
    FRAGMENTED = "fragmented"
    MISSING_ATOMS = "missing-atoms"
    HEADERLESS = "headerless"
    EMPTY = "empty"
    UNSUPPORTED = "unsupported"

    @property
    def recoverable(self) -> bool:
        return self not in (Severity.EMPTY, Severity.UNSUPPORTED, Severity.HEADERLESS)

    @property
    def needs_reference(self) -> bool:
        """True when an intact sibling file is required to rebuild the index."""
        return self in (Severity.MISSING_ATOMS, Severity.FRAGMENTED)


@dataclass
class Atom:
    type: str
    offset: int
    size: int


@dataclass
class Diagnosis:
    path: str
    size: int
    severity: Severity
    atoms: list[Atom] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    zero_byte_ratio: float = 0.0
    notes: list[str] = field(default_factory=list)
    sha256: str = ""

    @property
    def recoverable(self) -> bool:
        return self.severity.recoverable

    @property
    def needs_reference(self) -> bool:
        return self.severity.needs_reference

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        d["recoverable"] = self.recoverable
        d["needs_reference"] = self.needs_reference
        return d


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()
