from __future__ import annotations

from app.parsers import records_from_json_value


def test_records_from_json_object() -> None:
    records = records_from_json_value({"user_id": "U-101", "name": "Emily Carter"})
    assert len(records) == 1
    assert records[0]["user_id"] == "U-101"


def test_records_from_json_array() -> None:
    records = records_from_json_value(
        [{"user_id": "U-101"}, {"user_id": "U-102"}],
        ("users",),
    )
    assert [record["user_id"] for record in records] == ["U-101", "U-102"]


def test_records_from_wrapped_list() -> None:
    records = records_from_json_value({"users": [{"user_id": "U-101"}]}, ("users",))
    assert records[0]["user_id"] == "U-101"
