"""Secure booking status lookup.

A booking is returned only when BOTH the PNR and the last four digits of the
registered phone number match the same row. Any mismatch returns the same
generic "not found" result, so the lookup cannot be used to discover which
PNRs exist. Other customers' rows are never returned.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

import config

PNR_PATTERN = re.compile(r"^BS\d{6}$")
PHONE_LAST4_PATTERN = re.compile(r"^\d{4}$")
REQUIRED_COLUMNS = {
    "pnr", "passenger_name", "phone_last4", "route",
    "travel_date", "seat", "status", "amount_inr",
}
NOT_FOUND_MESSAGE = (
    "I couldn't find a booking that matches that PNR and phone number. "
    "Please check both and try again."
)


class BookingDataError(RuntimeError):
    pass


def normalize_pnr(pnr: object) -> str:
    return re.sub(r"\s+", "", str(pnr or "")).upper()


def normalize_last4(phone_last4: object) -> str:
    return re.sub(r"\D", "", str(phone_last4 or ""))


@lru_cache(maxsize=4)
def _load(path_str: str, mtime: float) -> pd.DataFrame:  # mtime busts the cache on edits
    df = pd.read_csv(path_str, dtype=str, keep_default_na=False)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise BookingDataError(f"bookings.csv is missing columns: {sorted(missing)}")
    df["pnr"] = df["pnr"].map(normalize_pnr)
    df["phone_last4"] = df["phone_last4"].map(lambda v: normalize_last4(v).zfill(4))
    return df


def load_bookings(path: Path | str = config.BOOKINGS_CSV) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise BookingDataError(f"Booking data not found at {path}.")
    return _load(str(path), path.stat().st_mtime)


def validate_inputs(pnr: object, phone_last4: object) -> tuple[str, str, str | None]:
    """Return (pnr, last4, error_message_or_None)."""
    p = normalize_pnr(pnr)
    raw_phone = str(phone_last4 or "").strip()
    last4 = normalize_last4(raw_phone)
    if not p:
        return p, last4, "Please share your PNR. It looks like BS followed by 6 digits."
    if not PNR_PATTERN.match(p):
        return p, last4, "That PNR doesn't look right. It should be BS followed by 6 digits, like BS123456."
    if not PHONE_LAST4_PATTERN.match(last4) or len(raw_phone) > 6:
        return p, last4, "Please share only the last 4 digits of your registered phone number."
    return p, last4, None


def get_booking_status(pnr: str, phone_last4: str, path: Path | str = config.BOOKINGS_CSV) -> dict:
    """Look up one booking after verifying PNR + last four phone digits.

    Returns a dict with keys:
      found (bool), message (str), booking (dict | None), error (str | None)
    """
    p, last4, error = validate_inputs(pnr, phone_last4)
    if error:
        return {"found": False, "message": error, "booking": None, "error": "invalid_input"}
    try:
        df = load_bookings(path)
    except BookingDataError as exc:
        return {
            "found": False,
            "message": "Booking lookup is unavailable right now. Please try again later.",
            "booking": None,
            "error": f"data_error: {exc}",
        }
    match = df[(df["pnr"] == p) & (df["phone_last4"] == last4)]
    if len(match) != 1:
        return {"found": False, "message": NOT_FOUND_MESSAGE, "booking": None, "error": "not_found"}
    row = match.iloc[0]
    booking = {
        "pnr": row["pnr"],
        "passenger_name": row["passenger_name"],
        "phone": f"ending in {row['phone_last4']}",  # never the full number
        "route": row["route"],
        "travel_date": row["travel_date"],
        "seat": row["seat"],
        "status": row["status"],
        "amount_inr": row["amount_inr"],
    }
    return {"found": True, "message": "Booking found.", "booking": booking, "error": None}


def format_booking(booking: dict) -> str:
    return (
        f"PNR {booking['pnr']} for {booking['passenger_name']}: {booking['route']} on "
        f"{booking['travel_date']}, seat {booking['seat']}. Status: {booking['status']}. "
        f"Amount paid: ₹{booking['amount_inr']}."
    )
