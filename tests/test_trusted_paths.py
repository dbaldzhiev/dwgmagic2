from dwgmagic.trusted_folder import merge_trusted_paths


def test_merge_appends_to_existing_value():
    merged = merge_trusted_paths("C:\\dwgmagic;C:\\other", "C:\\Users\\x\\AppData\\Local\\dwgmagic")
    assert merged == "C:\\dwgmagic;C:\\other;C:\\Users\\x\\AppData\\Local\\dwgmagic"


def test_merge_handles_empty_and_none():
    assert merge_trusted_paths("", "C:\\app") == "C:\\app"
    assert merge_trusted_paths(None, "C:\\app") == "C:\\app"


def test_merge_returns_none_when_already_present():
    assert merge_trusted_paths("C:\\app;D:\\x", "C:\\app") is None


def test_merge_is_case_and_slash_insensitive():
    assert merge_trusted_paths("c:\\App\\", "C:\\app") is None
    assert merge_trusted_paths("C:/app", "C:\\app") is None


def test_merge_ignores_blank_entries():
    merged = merge_trusted_paths("C:\\one;;C:\\two;", "C:\\three")
    assert merged == "C:\\one;C:\\two;C:\\three"
