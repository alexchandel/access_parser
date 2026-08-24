import struct
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from access_parser import AccessParser
from access_parser.access_parser import AccessTable
from access_parser.parsing_primitives import Column, RelativeMetadata
from access_parser.utils import TYPE_BINARY, TYPE_TEXT, ParsedValue

EXPECTED_CATALOG = {
    "MSysObjects": 2,
    "ClarotyTable": 18,
    "f_AF8292619150475ABBCC3E04860C1240_Data": 22,
    "MSysNameMap": 36,
    "MSysNavPaneGroupCategories": 44,
    "MSysNavPaneGroups": 47,
    "MSysNavPaneGroupToObjects": 51,
    "MSysNavPaneObjectIDs": 59,
}

TABLE_DIMENSIONS = [
    ("MSysObjects", 17, 26),
    ("ClarotyTable", 4, 2),
    ("f_AF8292619150475ABBCC3E04860C1240_Data", 8, 1),
    ("MSysNameMap", 5, 1),
    ("MSysNavPaneGroupCategories", 7, 3),
    ("MSysNavPaneGroups", 7, 9),
    ("MSysNavPaneGroupToObjects", 7, 11),
    ("MSysNavPaneObjectIDs", 3, 12),
]


@pytest.fixture(scope="module")
def database() -> AccessParser:
    return AccessParser(Path(__file__).parents[1] / "examples" / "test.mdb")


def test_reads_jet4_catalog(database: AccessParser) -> None:
    assert database.version == 4
    assert database.page_size == 4096
    assert database.catalog == EXPECTED_CATALOG


def test_parses_table_rows(database: AccessParser) -> None:
    assert database.parse_table("ClarotyTable") == {
        "ID": [1, 2],
        "Field1": ["test", "test2"],
        "Field2": ["Claroty", "Claroty"],
        "Field3": ["ICS!", None],
    }


@pytest.mark.parametrize(("table_name", "column_count", "row_count"), TABLE_DIMENSIONS)
def test_parses_all_discovered_tables(
    database: AccessParser,
    table_name: str,
    column_count: int,
    row_count: int,
) -> None:
    table = database.parse_table(table_name)

    assert len(table) == column_count
    assert {len(column) for column in table.values()} == {row_count}


def test_parses_schema_metadata(database: AccessParser) -> None:
    table = database.get_table("ClarotyTable")
    properties = database.extra_props["ClarotyTable"]

    assert table is not None
    assert properties is not None
    assert table.primary_keys == ["ID"]
    assert properties["Field3"]["ColumnOrder"] == 4


def test_parses_guid_and_binary_fields(database: AccessParser) -> None:
    name_map = database.parse_table("MSysNameMap")
    resources = database.parse_table("f_AF8292619150475ABBCC3E04860C1240_Data")
    name_map_data = name_map["NameMap"][0]
    file_data = resources["FileData"][0]

    assert name_map["GUID"] == ["8bb9d14a-0f9f-c746-a827-0165678e8cbd"]
    assert isinstance(name_map_data, bytes)
    assert len(name_map_data) == 342
    assert resources["FileName"] == ["Office Theme.thmx"]
    assert isinstance(file_data, bytes)
    assert file_data.startswith(b"\x01\x00\x00\x00P\x0c\x00\x00x^")
    assert len(file_data) == 2794


def test_prints_database(
    database: AccessParser,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(database, "catalog", {"ClarotyTable": 18})

    database.print_database()

    output = capsys.readouterr().out
    assert "TABLE NAME: ClarotyTable" in output
    assert "test2" in output


def test_rejects_missing_database(tmp_path: Path) -> None:
    missing_database = tmp_path / "missing.mdb"

    with pytest.raises(FileNotFoundError, match=r"missing\.mdb"):
        AccessParser(missing_database)


def test_rejects_invalid_database(tmp_path: Path) -> None:
    invalid_database = tmp_path / "invalid.mdb"
    invalid_database.write_bytes(b"not an Access database")

    with pytest.raises(ValueError, match="Failed to parse DB file header"):
        AccessParser(invalid_database)


def test_rejects_unknown_table(database: AccessParser) -> None:
    with pytest.raises(KeyError, match="Unknown table: MissingTable"):
        database.parse_table("MissingTable")


def test_jet3_jump_table_offsets_are_independent_of_null_fields() -> None:
    record = bytearray(268)
    record[250:260] = b"a" * 10
    record[260:264] = b"b" * 4
    record[264:268] = b"c" * 4
    columns: dict[int, Column] = {
        index: cast(
            "Column",
            SimpleNamespace(type=TYPE_BINARY, column_id=index, col_name_str=f"column_{index}"),
        )
        for index in range(3)
    }
    metadata = cast(
        "RelativeMetadata",
        SimpleNamespace(
            variable_length_field_offsets=[250, 4, 8],
            variable_length_jump_table=[1],
            var_len_count=12,
        ),
    )
    table = AccessTable.__new__(AccessTable)
    table.version = 3
    table.parsed_table = defaultdict[str, list[ParsedValue]](list)

    table._parse_dynamic_length_data(  # pyright: ignore[reportPrivateUsage]
        bytes(record), metadata, columns, [True, False, True]
    )

    assert table.parsed_table == {
        "column_0": [b"a" * 10],
        "column_1": [None],
        "column_2": [b"c" * 4],
    }


def test_parses_single_byte_jet3_lvprop_names() -> None:
    property_name = b"Description"
    column_name = b"Notes"
    value = b"hello"
    name_body = struct.pack("<H", len(property_name)) + property_name
    name_chunk = struct.pack("<IH", 6 + len(name_body), 128) + name_body
    property_data = struct.pack("<HBBHH", 8 + len(value), 0, TYPE_TEXT, 0, len(value)) + value
    value_body = struct.pack("<IH", 6 + len(column_name) + len(property_data), len(column_name))
    value_body += column_name + property_data
    value_chunk = struct.pack("<IH", 6 + len(value_body), 1) + value_body
    parser = AccessParser.__new__(AccessParser)
    parser.version = 3

    assert parser.parse_lvprop(b"KKD\0" + name_chunk + value_chunk) == {
        "Notes": {"Description": "hello"},
    }
