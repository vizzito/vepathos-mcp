"""Smart Import rejects `.bin` (415). ChatGPT often omits an extension — never send `.bin`."""

from __future__ import annotations

import pytest

from vepathos_mcp.tools.import_tools import smart_import_filename


@pytest.mark.parametrize(
    ("name", "mime", "expected"),
    [
        ("orders.xlsx", None, "orders.xlsx"),
        ("a/b/c.csv", None, "c.csv"),
        (None, None, "delivery.txt"),
        ("", None, "delivery.txt"),
        ("attachment.bin", None, "attachment.txt"),
        ("upload.bin", "text/csv", "upload.csv"),
        ("sheet", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "sheet.xlsx"),
        ("data.pdf", None, "data.txt"),
        ("noext", "application/json", "noext.json"),
    ],
)
def test_smart_import_filename(name: str | None, mime: str | None, expected: str) -> None:
    assert smart_import_filename(name, mime) == expected
