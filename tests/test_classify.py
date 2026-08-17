from dwgmagic.classify import classify_dwg_files


def test_classify_views_sheets_and_ignored():
    result = classify_dwg_files(
        [
            "SheetA.dwg",
            "SheetA-View-1.dwg",
            "SheetA-View-2.dwg",
            "SheetB.dwg",
            "Export-rvt-link.dwg",
            "notes.txt",
        ]
    )
    assert result.sheets == ["SheetA.dwg", "SheetB.dwg"]
    assert result.views == ["SheetA-View-1.dwg", "SheetA-View-2.dwg"]
    assert result.ignored == ["Export-rvt-link.dwg", "notes.txt"]
    assert result.orphan_views == []


def test_classify_reports_orphan_views():
    result = classify_dwg_files(["SheetA.dwg", "Mystery-View-9.dwg"])
    assert result.orphan_views == ["Mystery-View-9.dwg"]


def test_sheet_views_lookup_and_structured_sheets():
    result = classify_dwg_files(
        ["SheetA.dwg", "SheetA-View-1.dwg", "SheetA-View-12.dwg", "SheetB.dwg"]
    )
    assert result.sheet_views_lookup == {
        "SheetA": ["SheetA-View-1.dwg", "SheetA-View-12.dwg"],
        "SheetB": [],
    }
    structured = result.structured_sheets
    assert structured[0]["sheetName"] == "SheetA"
    assert [view["viewIndx"] for view in structured[0]["viewsOnSheet"]] == ["1", "12"]
    assert structured[1] == {"sheetName": "SheetB", "viewsOnSheet": []}


def test_summary_mentions_counts():
    result = classify_dwg_files(["SheetA.dwg", "SheetA-View-1.dwg", "x-rvt-y.dwg"])
    summary = result.summary()
    assert "1 sheet(s)" in summary
    assert "1 view(s)" in summary
    assert "1 ignored" in summary
