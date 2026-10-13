"""Experimental condition handling.

The conceptual definitions are locked by the researcher; the numeric band
boundaries are PROVISIONAL and read from ``config/conditions.yaml``.

The instrument records both the *declared* condition (set by whoever authored the
trial) and the *measured* condition (derived from the observed English lexical
proportion), then flags any disagreement. Silently trusting the declared label
would let a trial sit in the wrong band without anybody noticing, which is the
easiest way to corrupt a dose-response result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CONDITION_IDS = ["C0", "C1", "C2", "C3", "C4"]


@dataclass
class Band:
    id: str
    label: str
    label_ar_eg: str
    min_pct: float
    max_pct: float
    min_inclusive: bool
    max_inclusive: bool
    notes: str = ""
    provisional: bool = True
    soft_max_pct: float | None = None

    def contains(self, pct: float, tolerance: float = 0.0) -> bool:
        lo = self.min_pct - tolerance if self.min_inclusive else self.min_pct + tolerance
        hi = self.max_pct + tolerance if self.max_inclusive else self.max_pct - tolerance
        return lo <= pct <= hi

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "min_pct": self.min_pct,
            "max_pct": self.max_pct,
            "provisional": self.provisional,
        }


@dataclass
class ConditionResult:
    declared: str | None
    measured: str | None
    measured_pct: float
    consistent: bool
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "declared_condition": self.declared,
            "measured_condition": self.measured,
            "measured_english_pct": round(self.measured_pct, 4),
            "consistent": self.consistent,
            "warnings": list(self.warnings),
        }


class ConditionSet:
    def __init__(self, bands: list[Band], assignment_mode: str = "both", tolerance: float = 0.0,
                 provisional: bool = True, note: str = ""):
        self.bands = bands
        self.assignment_mode = assignment_mode
        self.tolerance = tolerance
        self.provisional = provisional
        self.note = note
        self._by_id = {b.id: b for b in bands}

    # -- lookup ----------------------------------------------------------
    def band(self, condition_id: str) -> Band | None:
        return self._by_id.get((condition_id or "").strip().upper())

    def ids(self) -> list[str]:
        return [b.id for b in self.bands]

    def contains(self, condition_id: str, pct: float) -> bool:
        b = self.band(condition_id)
        if b is None:
            return False
        return b.contains(pct, self.tolerance)

    def measure_to_condition(self, pct: float) -> str | None:
        for b in self.bands:
            if b.contains(pct, self.tolerance):
                return b.id
        return None

    # -- the check that protects the dose-response ------------------------
    def evaluate(
        self, measured_pct: float, declared: str | None = None
    ) -> ConditionResult:
        measured = self.measure_to_condition(measured_pct)
        warnings: list[str] = []

        if measured is None:
            warnings.append(
                f"measured proportion {measured_pct:.2f}% falls in no configured band"
            )

        if declared:
            declared_norm = declared.strip().upper()
            if self.band(declared_norm) is None:
                warnings.append(f"declared condition {declared!r} is not a known condition id")
            elif measured is not None and declared_norm != measured:
                warnings.append(
                    f"declared condition {declared_norm} disagrees with measured condition "
                    f"{measured} at {measured_pct:.2f}% English tokens"
                )

        if self.provisional:
            warnings.append("condition band boundaries are PROVISIONAL")

        if measured:
            band = self._by_id[measured]
            if band.soft_max_pct is not None and measured_pct > band.soft_max_pct:
                warnings.append(
                    f"{measured_pct:.2f}% exceeds the soft maximum {band.soft_max_pct}% for "
                    f"{band.id}; review whether the trial still satisfies the conceptual "
                    f"definition of {band.label}"
                )

        consistent = bool(measured is not None and (not declared or declared.strip().upper() == measured))
        return ConditionResult(
            declared=declared,
            measured=measured,
            measured_pct=measured_pct,
            consistent=consistent,
            warnings=warnings,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provisional": self.provisional,
            "provisional_note": self.note,
            "assignment_mode": self.assignment_mode,
            "band_tolerance_pct_points": self.tolerance,
            "bands": [b.to_dict() for b in self.bands],
        }


def load_conditions(config) -> ConditionSet:
    section = config.section("conditions") or {}
    bands: list[Band] = []
    for raw in section.get("bands", []) or []:
        bands.append(
            Band(
                id=str(raw["id"]),
                label=str(raw.get("label", raw["id"])),
                label_ar_eg=str(raw.get("label_ar_eg", "")),
                min_pct=float(raw.get("min_pct", 0.0)),
                max_pct=float(raw.get("max_pct", 100.0)),
                min_inclusive=bool(raw.get("min_inclusive", True)),
                max_inclusive=bool(raw.get("max_inclusive", True)),
                notes=str(raw.get("notes", "")),
                provisional=bool(raw.get("provisional", True)),
                soft_max_pct=(
                    float(raw["soft_max_pct"]) if raw.get("soft_max_pct") is not None else None
                ),
            )
        )
    return ConditionSet(
        bands=bands,
        assignment_mode=str(section.get("assignment_mode", "both")),
        tolerance=float(section.get("band_tolerance_pct_points", 0.0)),
        provisional=bool(section.get("provisional", True)),
        note=str(section.get("provisional_note", "")),
    )
