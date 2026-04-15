from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

AWG_PROTOCOL_VERSION_LEGACY = "legacy"
AWG_PROTOCOL_VERSION_1_5 = "1.5"
AWG_PROTOCOL_VERSION_2_0 = "2.0"

SUPPORTED_AWG_PROTOCOL_VERSIONS = frozenset(
    {
        AWG_PROTOCOL_VERSION_LEGACY,
        AWG_PROTOCOL_VERSION_1_5,
        AWG_PROTOCOL_VERSION_2_0,
    }
)

AWG_FEATURE_SIGNATURE_PACKETS = "signature_packets"
AWG_FEATURE_HEADER_RANGES = "header_ranges"
AWG_FEATURE_COOKIE_PADDING = "cookie_padding"
AWG_FEATURE_TRANSPORT_PADDING = "transport_padding"

AWG_COMPAT_SCALAR_HEADERS_ONLY = "scalar_headers_only"
AWG_COMPAT_LEGACY_EXTENSION_FIELDS = "legacy_extension_fields"
AWG_COMPAT_UNMAPPED_FIELDS = "unmapped_fields_present"

AWG_HEADER_FIELDS = ("h1", "h2", "h3", "h4")
AWG_SIGNATURE_FIELDS = ("i1", "i2", "i3", "i4", "i5")
AWG_LEGACY_EXTENSION_FIELDS = frozenset({"j1", "j2", "j3", "itime"})

_HEADER_RANGE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")
_UINT32_MAX = (1 << 32) - 1
_AWG_PADDING_DOC_LIMITS = {
    "s1": 64,
    "s2": 64,
    "s3": 64,
    "s4": 32,
}


@dataclass(frozen=True)
class AmneziaWgCapabilitySpec:
    protocol_version: str
    supports_signature_packets: bool
    supports_cookie_padding: bool
    supports_transport_padding: bool
    supports_header_ranges: bool
    supports_legacy_extension_fields: bool


@dataclass(frozen=True)
class AmneziaWgResolvedSemantics:
    protocol_version: str
    version_source: str
    feature_flags: tuple[str, ...]
    compatibility_flags: tuple[str, ...]
    warnings: tuple[str, ...]


_CAPABILITY_SPECS: dict[str, AmneziaWgCapabilitySpec] = {
    AWG_PROTOCOL_VERSION_LEGACY: AmneziaWgCapabilitySpec(
        protocol_version=AWG_PROTOCOL_VERSION_LEGACY,
        supports_signature_packets=False,
        supports_cookie_padding=False,
        supports_transport_padding=False,
        supports_header_ranges=False,
        supports_legacy_extension_fields=True,
    ),
    AWG_PROTOCOL_VERSION_1_5: AmneziaWgCapabilitySpec(
        protocol_version=AWG_PROTOCOL_VERSION_1_5,
        supports_signature_packets=True,
        supports_cookie_padding=False,
        supports_transport_padding=False,
        supports_header_ranges=False,
        supports_legacy_extension_fields=True,
    ),
    AWG_PROTOCOL_VERSION_2_0: AmneziaWgCapabilitySpec(
        protocol_version=AWG_PROTOCOL_VERSION_2_0,
        supports_signature_packets=True,
        supports_cookie_padding=True,
        supports_transport_padding=True,
        supports_header_ranges=True,
        supports_legacy_extension_fields=False,
    ),
}


def normalize_awg_protocol_version(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower().replace("_", ".")
    aliases = {
        "1": AWG_PROTOCOL_VERSION_LEGACY,
        "1.0": AWG_PROTOCOL_VERSION_LEGACY,
        "legacy": AWG_PROTOCOL_VERSION_LEGACY,
        "awg-legacy": AWG_PROTOCOL_VERSION_LEGACY,
        "1.5": AWG_PROTOCOL_VERSION_1_5,
        "2": AWG_PROTOCOL_VERSION_2_0,
        "2.0": AWG_PROTOCOL_VERSION_2_0,
    }
    resolved = aliases.get(normalized, normalized)
    if resolved not in SUPPORTED_AWG_PROTOCOL_VERSIONS:
        raise ValueError(
            "Неизвестная версия AmneziaWG. Поддерживаются legacy, 1.5 и 2.0."
        )
    return resolved


def get_awg_capability_spec(protocol_version: str) -> AmneziaWgCapabilitySpec:
    normalized = normalize_awg_protocol_version(protocol_version)
    if normalized is None:
        raise ValueError("Версия AmneziaWG не определена.")
    return _CAPABILITY_SPECS[normalized]


def resolve_awg_semantics(
    *,
    explicit_protocol_version: str | None,
    obfuscation_fields: Mapping[str, Any],
    has_unmapped_fields: bool,
) -> AmneziaWgResolvedSemantics:
    inferred_version = infer_awg_protocol_version(obfuscation_fields)
    protocol_version = normalize_awg_protocol_version(explicit_protocol_version) or inferred_version
    version_source = "explicit" if explicit_protocol_version else "inferred"
    feature_flags = _detect_feature_flags(obfuscation_fields)
    compatibility_flags = _detect_compatibility_flags(
        protocol_version=protocol_version,
        obfuscation_fields=obfuscation_fields,
        has_unmapped_fields=has_unmapped_fields,
    )
    warnings = _build_warnings(
        protocol_version=protocol_version,
        obfuscation_fields=obfuscation_fields,
        has_unmapped_fields=has_unmapped_fields,
    )
    validate_awg_obfuscation_fields(
        obfuscation_fields,
        protocol_version=protocol_version,
        explicit_protocol_version=explicit_protocol_version is not None,
    )
    return AmneziaWgResolvedSemantics(
        protocol_version=protocol_version,
        version_source=version_source,
        feature_flags=feature_flags,
        compatibility_flags=compatibility_flags,
        warnings=warnings,
    )


def infer_awg_protocol_version(obfuscation_fields: Mapping[str, Any]) -> str:
    normalized = _normalize_obfuscation_fields(obfuscation_fields)
    if normalized.get("s3") is not None or normalized.get("s4") is not None:
        return AWG_PROTOCOL_VERSION_2_0
    if any(is_header_range_value(normalized.get(field_name)) for field_name in AWG_HEADER_FIELDS):
        return AWG_PROTOCOL_VERSION_2_0
    if any(normalized.get(field_name) is not None for field_name in AWG_SIGNATURE_FIELDS):
        return AWG_PROTOCOL_VERSION_1_5
    if any(normalized.get(field_name) is not None for field_name in AWG_LEGACY_EXTENSION_FIELDS):
        return AWG_PROTOCOL_VERSION_1_5
    return AWG_PROTOCOL_VERSION_LEGACY


def validate_awg_obfuscation_fields(
    obfuscation_fields: Mapping[str, Any],
    *,
    protocol_version: str,
    explicit_protocol_version: bool = False,
) -> None:
    normalized = _normalize_obfuscation_fields(obfuscation_fields)
    capability_spec = get_awg_capability_spec(protocol_version)

    _validate_padding_value("S1", normalized.get("s1"))
    _validate_padding_value("S2", normalized.get("s2"))
    _validate_padding_value("S3", normalized.get("s3"))
    _validate_padding_value("S4", normalized.get("s4"))

    header_ranges: list[tuple[str, tuple[int, int]]] = []
    for field_name in AWG_HEADER_FIELDS:
        raw_value = normalized.get(field_name)
        if raw_value is None:
            continue
        header_range = parse_awg_header_value(raw_value)
        header_ranges.append((field_name.upper(), header_range))
        if header_range[0] != header_range[1] and not capability_spec.supports_header_ranges:
            raise ValueError(
                f"Параметр {field_name.upper()} в AmneziaWG {capability_spec.protocol_version} "
                "должен быть одиночным uint32-значением без диапазона."
            )

    _validate_non_overlapping_header_ranges(header_ranges)

    if normalized.get("s3") is not None and not capability_spec.supports_cookie_padding:
        raise ValueError("Параметр S3 поддерживается только в AmneziaWG 2.0.")
    if normalized.get("s4") is not None and not capability_spec.supports_transport_padding:
        raise ValueError("Параметр S4 поддерживается только в AmneziaWG 2.0.")

    if (
        any(normalized.get(field_name) is not None for field_name in AWG_SIGNATURE_FIELDS)
        and not capability_spec.supports_signature_packets
    ):
        raise ValueError(
            f"Параметры I1-I5 не поддерживаются в AmneziaWG {capability_spec.protocol_version}."
        )

    legacy_fields = [
        field_name.upper()
        for field_name in AWG_LEGACY_EXTENSION_FIELDS
        if normalized.get(field_name) is not None
    ]
    if legacy_fields and not capability_spec.supports_legacy_extension_fields:
        raise ValueError(
            "Параметры "
            + ", ".join(sorted(legacy_fields))
            + " несовместимы с AmneziaWG 2.0."
        )

    if explicit_protocol_version:
        inferred_version = infer_awg_protocol_version(normalized)
        if protocol_version == AWG_PROTOCOL_VERSION_1_5 and inferred_version == AWG_PROTOCOL_VERSION_2_0:
            raise ValueError(
                "Профиль явно помечен как AmneziaWG 1.5, но содержит параметры, доступные только в 2.0."
            )
        if protocol_version == AWG_PROTOCOL_VERSION_LEGACY and inferred_version != AWG_PROTOCOL_VERSION_LEGACY:
            raise ValueError(
                "Профиль явно помечен как legacy AmneziaWG, но содержит поля более новых версий."
            )


def parse_awg_header_value(value: Any) -> tuple[int, int]:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("Параметры H1-H4 в AWG-конфиге не могут быть пустыми.")
    range_match = _HEADER_RANGE_RE.fullmatch(normalized)
    if range_match is not None:
        start = int(range_match.group(1))
        end = int(range_match.group(2))
        if start > end:
            raise ValueError("Диапазон H1-H4 в AWG-конфиге должен быть задан как min-max.")
        _validate_uint32("H1-H4", start)
        _validate_uint32("H1-H4", end)
        return start, end
    try:
        scalar = int(normalized)
    except ValueError as exc:
        raise ValueError(
            "Параметры H1-H4 в AWG-конфиге должны содержать uint32-значение или диапазон min-max."
        ) from exc
    _validate_uint32("H1-H4", scalar)
    return scalar, scalar


def is_header_range_value(value: Any) -> bool:
    normalized = str(value or "").strip()
    return bool(normalized) and _HEADER_RANGE_RE.fullmatch(normalized) is not None


def requires_runtime_not_implemented_error(
    protocol_version: str,
    obfuscation_fields: Mapping[str, Any],
) -> str | None:
    normalized = _normalize_obfuscation_fields(obfuscation_fields)
    legacy_fields = [
        field_name.upper()
        for field_name in AWG_LEGACY_EXTENSION_FIELDS
        if normalized.get(field_name) is not None
    ]
    if legacy_fields:
        return (
            "Текущий Windows runtime builder не умеет материализовать параметры "
            + ", ".join(sorted(legacy_fields))
            + f" для AmneziaWG {protocol_version}. Уберите эти поля или используйте backend, "
            "который явно поддерживает legacy-расширения."
        )
    return None


def _detect_feature_flags(obfuscation_fields: Mapping[str, Any]) -> tuple[str, ...]:
    normalized = _normalize_obfuscation_fields(obfuscation_fields)
    feature_flags: list[str] = []
    if any(normalized.get(field_name) is not None for field_name in AWG_SIGNATURE_FIELDS):
        feature_flags.append(AWG_FEATURE_SIGNATURE_PACKETS)
    if any(is_header_range_value(normalized.get(field_name)) for field_name in AWG_HEADER_FIELDS):
        feature_flags.append(AWG_FEATURE_HEADER_RANGES)
    if normalized.get("s3") is not None:
        feature_flags.append(AWG_FEATURE_COOKIE_PADDING)
    if normalized.get("s4") is not None:
        feature_flags.append(AWG_FEATURE_TRANSPORT_PADDING)
    return tuple(feature_flags)


def _detect_compatibility_flags(
    *,
    protocol_version: str,
    obfuscation_fields: Mapping[str, Any],
    has_unmapped_fields: bool,
) -> tuple[str, ...]:
    normalized = _normalize_obfuscation_fields(obfuscation_fields)
    compatibility_flags: list[str] = []
    if protocol_version != AWG_PROTOCOL_VERSION_2_0:
        compatibility_flags.append(AWG_COMPAT_SCALAR_HEADERS_ONLY)
    if any(normalized.get(field_name) is not None for field_name in AWG_LEGACY_EXTENSION_FIELDS):
        compatibility_flags.append(AWG_COMPAT_LEGACY_EXTENSION_FIELDS)
    if has_unmapped_fields:
        compatibility_flags.append(AWG_COMPAT_UNMAPPED_FIELDS)
    return tuple(compatibility_flags)


def _build_warnings(
    *,
    protocol_version: str,
    obfuscation_fields: Mapping[str, Any],
    has_unmapped_fields: bool,
) -> tuple[str, ...]:
    normalized = _normalize_obfuscation_fields(obfuscation_fields)
    warnings: list[str] = []
    legacy_fields = [
        field_name.upper()
        for field_name in AWG_LEGACY_EXTENSION_FIELDS
        if normalized.get(field_name) is not None
    ]
    if legacy_fields:
        warnings.append(
            "Обнаружены legacy-параметры "
            + ", ".join(sorted(legacy_fields))
            + f" для AmneziaWG {protocol_version}."
        )
    if has_unmapped_fields:
        warnings.append(
            "Часть AWG-полей сохранена как unmapped и не попадает в runtime-конфиг без явной поддержки backend."
        )
    oversized_padding_fields = _get_oversized_padding_fields(normalized)
    if oversized_padding_fields:
        warnings.append(
            "Параметры "
            + ", ".join(oversized_padding_fields)
            + " выходят за документированные диапазоны AmneziaWG; профиль сохранен без нормализации."
        )
    return tuple(warnings)


def _normalize_obfuscation_fields(obfuscation_fields: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in obfuscation_fields.items():
        field_name = str(key or "").strip().lower()
        if not field_name:
            continue
        if value is None:
            normalized[field_name] = None
            continue
        if isinstance(value, str):
            stripped = value.strip()
            normalized[field_name] = stripped or None
            continue
        normalized[field_name] = value
    return normalized


def _validate_padding_value(field_name: str, value: Any) -> None:
    if value is None:
        return
    try:
        numeric_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Поле {field_name} в AWG-конфиге должно быть целым числом."
        ) from exc
    if numeric_value < 0:
        raise ValueError(
            f"Поле {field_name} в AWG-конфиге не может быть отрицательным."
        )


def _get_oversized_padding_fields(normalized_fields: Mapping[str, Any]) -> list[str]:
    oversized: list[str] = []
    for field_name, documented_limit in _AWG_PADDING_DOC_LIMITS.items():
        value = normalized_fields.get(field_name)
        if value is None:
            continue
        if int(value) > documented_limit:
            oversized.append(field_name.upper())
    return oversized


def _validate_non_overlapping_header_ranges(
    header_ranges: list[tuple[str, tuple[int, int]]],
) -> None:
    for index, (field_name, current_range) in enumerate(header_ranges):
        for other_field_name, other_range in header_ranges[index + 1 :]:
            if _ranges_overlap(current_range, other_range):
                raise ValueError(
                    f"Диапазоны {field_name} и {other_field_name} в AWG-конфиге не должны пересекаться."
                )


def _ranges_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] <= right[1] and right[0] <= left[1]


def _validate_uint32(field_name: str, value: int) -> None:
    if not 0 <= value <= _UINT32_MAX:
        raise ValueError(
            f"Поле {field_name} в AWG-конфиге должно быть в диапазоне 0..{_UINT32_MAX}."
        )
