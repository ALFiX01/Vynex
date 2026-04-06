from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ServerEntry:
    id: str
    name: str
    protocol: str
    host: str
    port: int
    raw_link: str
    source: str = "manual"
    subscription_id: str | None = None
    created_at: str = field(default_factory=utc_now_iso)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        name: str,
        protocol: str,
        host: str,
        port: int,
        raw_link: str,
        extra: dict[str, Any] | None = None,
        source: str = "manual",
        subscription_id: str | None = None,
    ) -> "ServerEntry":
        return cls(
            id=str(uuid4()),
            name=name,
            protocol=protocol,
            host=host,
            port=port,
            raw_link=raw_link,
            source=source,
            subscription_id=subscription_id,
            extra=extra or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ServerEntry":
        return cls(**data)


@dataclass
class SubscriptionEntry:
    id: str
    url: str
    title: str
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    server_ids: list[str] = field(default_factory=list)
    auto_update: bool = True
    last_error: str | None = None
    last_error_at: str | None = None

    @classmethod
    def new(cls, *, url: str, title: str) -> "SubscriptionEntry":
        return cls(id=str(uuid4()), url=url, title=title)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SubscriptionEntry":
        return cls(**data)


@dataclass
class RuntimeState:
    pid: int | None = None
    mode: str | None = None
    server_id: str | None = None
    started_at: str | None = None
    socks_port: int | None = None
    http_port: int | None = None
    system_proxy_enabled: bool = False
    previous_system_proxy: dict[str, Any] | None = None
    routing_profile_id: str | None = None
    routing_profile_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeState":
        return cls(**data)

    @property
    def is_running(self) -> bool:
        return self.pid is not None


@dataclass
class AppSettings:
    active_routing_profile_id: str = "default"
    proxy_socks_port: int = 1080
    proxy_http_port: int = 1081
    set_system_proxy: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppSettings":
        return cls(**data)
