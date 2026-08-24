"""Value decoding and page utilities for Access databases."""

import logging
import math
import os
import struct
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta

type ParsedValue = bool | bytes | float | int | str | None
type PageMap = dict[int, bytes]
type PropertyMap = Mapping[str, ParsedValue]

LOGGER = logging.getLogger("access_parser.utils")


TYPE_BOOLEAN = 1
TYPE_INT8 = 2
TYPE_INT16 = 3
TYPE_INT32 = 4
TYPE_MONEY = 5
TYPE_FLOAT32 = 6
TYPE_FLOAT64 = 7
TYPE_DATETIME = 8
TYPE_BINARY = 9
TYPE_TEXT = 10
TYPE_OLE = 11
TYPE_MEMO = 12
TYPE_GUID = 15
TYPE_96_bit_17_BYTES = 16
TYPE_COMPLEX = 18

TABLE_PAGE_MAGIC = b"\x02\x01"
DATA_PAGE_MAGIC = b"\x01\x01"


ACCESS_EPOCH = datetime(1899, 12, 30)  # noqa: DTZ001 - Access stores timezone-naive dates.

PERCENT_DEFAULT = "0.00%"
EURO_DEFAULT = "€0.00"
DOLLAR_DEFAULT = "$0.00"
GENERAL_NUMBER_DEFAULT = "0"
FIXED_AND_STANDARD_DEFAULT = "0.00"
SCIENTIFIC_DEFAULT = "0.00E+00"

FORMAT_PERCENT = "Percent"
FORMAT_DOLLAR = "$"
FORMAT_EURO = "€"
FORMAT_GENERAL_NUMBER = "General Number"
FORMAT_FIXED = "Fixed"
FORMAT_STANDARD = "Standard"
FORMAT_SCIENTIFIC = "Scientific"

FORMAT_TO_DEFAULT_VALUE = {
    FORMAT_DOLLAR: DOLLAR_DEFAULT,
    FORMAT_STANDARD: FIXED_AND_STANDARD_DEFAULT,
    FORMAT_FIXED: FIXED_AND_STANDARD_DEFAULT,
    FORMAT_PERCENT: PERCENT_DEFAULT,
    FORMAT_EURO: EURO_DEFAULT,
    FORMAT_GENERAL_NUMBER: GENERAL_NUMBER_DEFAULT,
    FORMAT_SCIENTIFIC: SCIENTIFIC_DEFAULT,
}

TYPE_TO_STRUCT_FORMAT = {
    TYPE_INT8: "b",
    TYPE_INT16: "h",
    TYPE_INT32: "i",
    TYPE_COMPLEX: "i",
    TYPE_FLOAT32: "f",
    TYPE_FLOAT64: "d",
}


# https://stackoverflow.com/questions/45560782
def mdb_date_to_readable(double_time: int) -> str:
    """Convert the bit representation of an Access date to readable text."""
    try:
        dtime_bytes = struct.pack("Q", double_time)

        dtime_double = struct.unpack("<d", dtime_bytes)[0]
        dtime_frac, dtime_whole = math.modf(dtime_double)
        dtime = ACCESS_EPOCH + timedelta(days=dtime_whole) + timedelta(days=dtime_frac)
        if dtime == ACCESS_EPOCH:
            return "(Empty Date)"
        return str(dtime)
    except OverflowError:
        return "(Invalid Date)"
    except struct.error:
        return "(Invalid Date)"


def numeric_to_string(bytes_num: bytes, scale: int = 6) -> str:
    """Decode Access's 17-byte numeric representation."""
    neg, num1, num2, num3, num4 = struct.unpack("<BIIII", bytes_num)
    full_number = (num1 << 96) + (num2 << 64) + (num3 << 32) + num4
    full_number = str(full_number)
    # If scale is 6 149804168 will be 149.804168 - 6 from the end.
    # If scale is bigger than the number ignore the scale(1498 will remain 1498)
    if len(full_number) > scale:
        dot_len = len(full_number) - scale
        full_number = full_number[:dot_len] + "." + full_number[dot_len:]
    numeric_string = "-" if neg else ""
    numeric_string += full_number
    return numeric_string


def get_decoded_text(bytes_data: bytes) -> str:
    """Decode Jet 3 text as UTF-8 or Latin-1."""
    try:
        return bytes_data.decode("utf-8")
    except UnicodeDecodeError:
        return bytes_data.decode("latin1")


def parse_money_type(parsed: int, prop_format: str) -> str:
    """Parse and format a money value according to the specified format.

    Args:
        parsed (int): The numerical value to be parsed.
        prop_format (str): The format string specifying the desired format.

    Returns:
        str: The parsed and formatted money value.

    """
    parsed_string = str(parsed)
    if prop_format == FORMAT_PERCENT:
        special_format = "{:.2f}%"
        dot_location = -2
    elif prop_format.startswith(FORMAT_DOLLAR):
        special_format = "${:,.2f}"
        dot_location = -4
    elif prop_format.startswith(FORMAT_EURO):
        special_format = "€{:,.2f}"
        dot_location = -4
    elif prop_format == FORMAT_GENERAL_NUMBER:
        special_format = "{:,.1f}"
        dot_location = -4
    elif prop_format == FORMAT_SCIENTIFIC:
        special_format = "{:.2e}"
        dot_location = -4
    elif prop_format in [FORMAT_FIXED, FORMAT_STANDARD]:
        dot_location = -4
        special_format = "{:,.2f}"
    else:
        LOGGER.warning(f"parse_money_type - unsupported format: {prop_format} value {parsed} may be wrong")
        return parsed_string

    money_value = parsed_string[:dot_location] + "." + parsed_string[dot_location:]
    return special_format.format(float(money_value))


def _parse_money(buffer: bytes, props: PropertyMap | None) -> int | str:
    parsed = struct.unpack_from("q", buffer)[0]
    if not props or not isinstance(prop_format := props.get("Format"), str):
        return parsed
    if parsed:
        return parse_money_type(parsed, prop_format)

    default_value = next(
        (value for name, value in FORMAT_TO_DEFAULT_VALUE.items() if prop_format.startswith(name)), None
    )
    if default_value is None:
        LOGGER.warning(f"parse_type got unknown format while parsing money field {prop_format}")
        return parsed
    return default_value


def _parse_text(buffer: bytes, version: int) -> str:
    if version <= 3:
        parsed = get_decoded_text(buffer)
    elif buffer.startswith((b"\xfe\xff", b"\xff\xfe")):
        parsed = get_decoded_text(buffer[2:])
    else:
        parsed = buffer.decode("utf-16", errors="ignore")

    if "\x00" in parsed:
        LOGGER.debug(f"Parsed string contains NUL (0x00) characters: {parsed}")
        return parsed.replace("\x00", "")
    return parsed


def parse_type(
    data_type: int,
    buffer: bytes,
    length: int | None = None,
    version: int = 3,
    props: PropertyMap | None = None,
) -> ParsedValue:
    """Decode a field according to its Access type identifier."""
    if struct_format := TYPE_TO_STRUCT_FORMAT.get(data_type):
        return struct.unpack_from(struct_format, buffer)[0]
    if data_type == TYPE_MONEY:
        return _parse_money(buffer, props)
    if data_type == TYPE_DATETIME:
        double_datetime = struct.unpack_from("q", buffer)[0]
        return mdb_date_to_readable(double_datetime)
    if data_type == TYPE_BINARY:
        return buffer[:length]
    if data_type == TYPE_OLE:
        return buffer
    if data_type == TYPE_GUID:
        return str(uuid.UUID(buffer[:16].hex()))
    if data_type == TYPE_96_bit_17_BYTES:
        return buffer[:17]
    if data_type == TYPE_TEXT:
        return _parse_text(buffer, version)

    LOGGER.debug(f"parse_type - unsupported data type: {data_type}")
    return ""


def categorize_pages(db_data: bytes, page_size: int) -> tuple[PageMap, PageMap, PageMap]:
    """Split database bytes into table-definition, data, and complete page maps."""
    if len(db_data) % page_size:
        LOGGER.warning(f"DB is not full or PAGE_SIZE is wrong. page size: {page_size} DB length {len(db_data)}")
    pages = {i: db_data[i : i + page_size] for i in range(0, len(db_data), page_size)}
    data_pages: PageMap = {}
    table_defs: PageMap = {}
    for page, value in pages.items():
        if value.startswith(DATA_PAGE_MAGIC):
            data_pages[page] = value
        elif value.startswith(TABLE_PAGE_MAGIC):
            table_defs[page] = value
    return table_defs, data_pages, pages


def read_db_file(path: str | os.PathLike[str]) -> bytes:
    """Read a database file from disk."""
    if not os.path.isfile(path):
        LOGGER.error(f"File {path} not found")
        raise FileNotFoundError(f"File {path} not found")
    with open(path, "rb") as f:
        return f.read()
