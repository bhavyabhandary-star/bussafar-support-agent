"""Booking status lookup: verification, safe failures and no data leaks."""
from __future__ import annotations

import pandas as pd
import pytest

import config
from booking.booking_lookup import (
    NOT_FOUND_MESSAGE, format_booking, get_booking_status, load_bookings,
)

ALL = pd.read_csv(config.BOOKINGS_CSV, dtype=str)


def test_supplied_data_has_12_bookings():
    assert len(load_bookings()) == 12


def test_valid_lookup_from_brief():
    r = get_booking_status("BS558954", "2087")
    assert r["found"] is True
    b = r["booking"]
    assert b["status"] == "Completed"
    assert b["route"] == "Bengaluru to Goa"
    assert b["phone"] == "ending in 2087"


@pytest.mark.parametrize("row", ALL.to_dict("records"), ids=list(ALL["pnr"]))
def test_every_supplied_booking_verifies(row):
    r = get_booking_status(row["pnr"], row["phone_last4"])
    assert r["found"] and r["booking"]["pnr"] == row["pnr"]
    assert r["booking"]["status"] == row["status"]


def test_wrong_phone_reveals_nothing():
    r = get_booking_status("BS558954", "0000")
    assert r == {"found": False, "message": NOT_FOUND_MESSAGE, "booking": None, "error": "not_found"}


def test_unknown_pnr_gives_same_message_as_wrong_phone():
    a = get_booking_status("BS000000", "2087")
    b = get_booking_status("BS558954", "0000")
    assert a["message"] == b["message"] and a["booking"] is None


def test_phone_of_another_booking_does_not_match():
    # 1120 belongs to BS553205, not BS558954
    assert get_booking_status("BS558954", "1120")["found"] is False


@pytest.mark.parametrize("pnr,last4", [
    ("", "2087"), ("558954", "2087"), ("BS55895", "2087"), ("BS558954' OR '1'='1", "2087"),
    ("BS558954", ""), ("BS558954", "20"), ("BS558954", "abcd"), ("BS558954", "9876542087"),
])
def test_invalid_input_is_rejected_without_details(pnr, last4):
    r = get_booking_status(pnr, last4)
    assert r["found"] is False and r["booking"] is None and r["error"] == "invalid_input"


def test_input_is_normalised():
    assert get_booking_status(" bs558954 ", " 2087 ")["found"] is True


def test_result_contains_only_the_matching_row():
    r = get_booking_status("BS558954", "2087")
    blob = str(r)
    for other in ALL[ALL["pnr"] != "BS558954"].itertuples():
        assert other.pnr not in blob and other.passenger_name not in blob


def test_missing_data_file_fails_safely(tmp_path):
    r = get_booking_status("BS558954", "2087", tmp_path / "missing.csv")
    assert r["found"] is False and r["booking"] is None
    assert "unavailable" in r["message"]


def test_format_booking_has_no_phone_digits_beyond_last4():
    s = format_booking(get_booking_status("BS558954", "2087")["booking"])
    assert "Completed" in s and "phone" not in s.lower()
