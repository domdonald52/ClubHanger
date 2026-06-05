"""
Availability service.

Thin re-export of core/availability.py so that callers (views, future API,
future AI layer) import from services.availability_service rather than
directly from the availability module. The underlying implementation is not
duplicated here — we simply delegate.

When the availability logic needs to move or be replaced, only this file and
the underlying module need to change; callers stay stable.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from ..availability import (
    find_free_spans as _find_free_spans,
    find_free_spans_with_instructors as _find_free_spans_with_instructors,
    find_available_slots as _find_available_slots,
    get_date_range,
)


def free_spans_solo(club, date_start: date, date_end: date,
                    aircraft=None, aircraft_type: Optional[str] = None,
                    min_minutes: Optional[int] = None) -> list:
    """
    Aircraft-only free spans (solo / no-instructor search).

    Returns list of dicts: { date, aircraft, spans: [{start, end, minutes}] }
    """
    return _find_free_spans(
        club, date_start, date_end,
        aircraft=aircraft, aircraft_type=aircraft_type, min_minutes=min_minutes,
    )


def free_spans_dual(club, date_start: date, date_end: date,
                    aircraft=None, aircraft_type: Optional[str] = None,
                    instructor=None, min_minutes: Optional[int] = None) -> list:
    """
    Aircraft + instructor free spans (dual search).

    Returns list of dicts: { date, aircraft, instructor_rows: [{instructor, spans}] }
    """
    return _find_free_spans_with_instructors(
        club, date_start, date_end,
        aircraft=aircraft, aircraft_type=aircraft_type,
        instructor=instructor, min_minutes=min_minutes,
    )


def available_slots(club, date_start: date, date_end: date,
                    aircraft=None, aircraft_type: Optional[str] = None,
                    instructor=None, min_minutes: Optional[int] = None) -> list:
    """
    Unified slot search used by the gantt pre-fill and booking creation.
    Delegates to find_available_slots.
    """
    return _find_available_slots(
        club, date_start, date_end,
        aircraft=aircraft, aircraft_type=aircraft_type,
        instructor=instructor, min_minutes=min_minutes,
    )


__all__ = ['free_spans_solo', 'free_spans_dual', 'available_slots', 'get_date_range']
