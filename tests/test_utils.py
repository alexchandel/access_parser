import struct
import uuid

import pytest

from access_parser.utils import (
    FORMAT_DOLLAR,
    FORMAT_EURO,
    FORMAT_FIXED,
    FORMAT_GENERAL_NUMBER,
    FORMAT_PERCENT,
    FORMAT_SCIENTIFIC,
    TYPE_BINARY,
    TYPE_COMPLEX,
    TYPE_DATETIME,
    TYPE_FLOAT32,
    TYPE_FLOAT64,
    TYPE_GUID,
    TYPE_INT8,
    TYPE_INT16,
    TYPE_INT32,
    TYPE_MONEY,
    TYPE_OLE,
    TYPE_TEXT,
    TYPE_96_bit_17_BYTES,
    categorize_pages,
    numeric_to_string,
    parse_type,
)


@pytest.mark.parametrize(
    ("data_type", "buffer", "expected"),
    [
        (TYPE_INT8, struct.pack("<b", -8), -8),
        (TYPE_INT16, struct.pack("<h", -1_024), -1_024),
        (TYPE_INT32, struct.pack("<i", 123_456), 123_456),
        (TYPE_COMPLEX, struct.pack("<i", -123_456), -123_456),
        (TYPE_FLOAT32, struct.pack("<f", 1.5), 1.5),
        (TYPE_FLOAT64, struct.pack("<d", -2.25), -2.25),
    ],
)
def test_parse_type_decodes_fixed_width_values(data_type: int, buffer: bytes, expected: float) -> None:
    assert parse_type(data_type, buffer) == expected


@pytest.mark.parametrize(
    ("field_format", "expected"),
    [
        (FORMAT_DOLLAR, "$12.35"),
        (FORMAT_EURO, "€12.35"),
        (FORMAT_PERCENT, "1234.56%"),
        (FORMAT_GENERAL_NUMBER, "12.3"),
        (FORMAT_FIXED, "12.35"),
        (FORMAT_SCIENTIFIC, "1.23e+01"),
    ],
)
def test_parse_type_formats_money(field_format: str, expected: str) -> None:
    assert parse_type(TYPE_MONEY, struct.pack("<q", 123_456), props={"Format": field_format}) == expected


@pytest.mark.parametrize(
    ("field_format", "expected"),
    [
        (FORMAT_DOLLAR, "$0.00"),
        (FORMAT_EURO, "€0.00"),
        (FORMAT_PERCENT, "0.00%"),
        (FORMAT_GENERAL_NUMBER, "0"),
        (FORMAT_FIXED, "0.00"),
        (FORMAT_SCIENTIFIC, "0.00E+00"),
    ],
)
def test_parse_type_uses_money_defaults(field_format: str, expected: str) -> None:
    assert parse_type(TYPE_MONEY, struct.pack("<q", 0), props={"Format": field_format}) == expected


def test_parse_type_leaves_unformatted_money_numeric() -> None:
    assert parse_type(TYPE_MONEY, struct.pack("<q", 123_456)) == 123_456
    assert parse_type(TYPE_MONEY, struct.pack("<q", 0), props={"Format": "Unknown"}) == 0


def test_parse_type_decodes_access_dates() -> None:
    one_and_a_half_days = struct.unpack("<q", struct.pack("<d", 1.5))[0]

    assert parse_type(TYPE_DATETIME, struct.pack("<q", 0)) == "(Empty Date)"
    assert parse_type(TYPE_DATETIME, struct.pack("<q", one_and_a_half_days)) == "1899-12-31 12:00:00"


@pytest.mark.parametrize(
    ("buffer", "version", "expected"),
    [
        (b"caf\xc3\xa9", 3, "café"),
        (b"caf\xe9", 3, "café"),
        (b"a\x00b", 3, "ab"),
        ("café".encode("utf-16"), 4, "café"),
        ("café".encode("utf-16-le"), 4, "café"),
    ],
)
def test_parse_type_decodes_text(buffer: bytes, version: int, expected: str) -> None:
    assert parse_type(TYPE_TEXT, buffer, version=version) == expected


def test_parse_type_decodes_byte_fields_and_guid() -> None:
    guid = uuid.UUID("8bb9d14a-0f9f-c746-a827-0165678e8cbd")
    payload = bytes(range(32))

    assert parse_type(TYPE_BINARY, payload, length=5) == payload[:5]
    assert parse_type(TYPE_OLE, payload) == payload
    assert parse_type(TYPE_96_bit_17_BYTES, payload) == payload[:17]
    assert parse_type(TYPE_GUID, guid.bytes) == str(guid)


@pytest.mark.parametrize(
    ("negative", "scale", "expected"),
    [
        (0, 6, "149.804168"),
        (1, 6, "-149.804168"),
        (0, 10, "149804168"),
    ],
)
def test_numeric_to_string(negative: int, scale: int, expected: str) -> None:
    numeric = struct.pack("<BIIII", negative, 0, 0, 0, 149_804_168)

    assert numeric_to_string(numeric, scale) == expected


def test_categorize_pages() -> None:
    table_page = b"\x02\x01aa"
    data_page = b"\x01\x01bb"
    other_page = b"other"

    table_defs, data_pages, all_pages = categorize_pages(table_page + data_page + other_page, page_size=4)

    assert table_defs == {0: table_page}
    assert data_pages == {4: data_page}
    assert all_pages == {0: table_page, 4: data_page, 8: b"othe", 12: b"r"}
