from pathlib import Path

import pytest

from access_parser import AccessParser

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

    assert table is not None
    assert table.primary_keys == ["ID"]
    assert database.extra_props["ClarotyTable"]["Field3"]["ColumnOrder"] == 4


def test_parses_guid_and_binary_fields(database: AccessParser) -> None:
    name_map = database.parse_table("MSysNameMap")
    resources = database.parse_table("f_AF8292619150475ABBCC3E04860C1240_Data")

    assert name_map["GUID"] == ["8bb9d14a-0f9f-c746-a827-0165678e8cbd"]
    assert len(name_map["NameMap"][0]) == 342
    assert resources["FileName"] == ["Office Theme.thmx"]
    assert resources["FileData"][0].startswith(b"\x01\x00\x00\x00P\x0c\x00\x00x^")
    assert len(resources["FileData"][0]) == 2794


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
