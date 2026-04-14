from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Server:
    protocol: str
    address: str
    port: int
    uuid: str
    name: str
    raw_uri: str
    extra: dict[str, Any] = field(default_factory=dict)

