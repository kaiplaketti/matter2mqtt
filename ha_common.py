"""Shared Home Assistant MQTT discovery helpers."""

from __future__ import annotations


def build_device_info(
    *,
    identifiers: list[str],
    name: str,
    manufacturer: str,
    model: str,
) -> dict[str, object]:
    """Build the Home Assistant `device` object."""
    return {
        "identifiers": identifiers,
        "name": name,
        "manufacturer": manufacturer,
        "model": model,
    }


def build_availability_fields(
    *,
    availability_topic: str,
    payload_available: str,
    payload_not_available: str,
) -> dict[str, str]:
    """Build common availability fields for HA discovery payloads."""
    return {
        "availability_topic": availability_topic,
        "payload_available": payload_available,
        "payload_not_available": payload_not_available,
    }
