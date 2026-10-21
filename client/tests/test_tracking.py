"""One delivered job = one applications.xlsx row."""

from __future__ import annotations

from datetime import date

import openpyxl
import pytest

from cv_client.tracking import (
    HEADERS,
    STATUS_VALUES,
    NullTracker,
    XlsxTracker,
    build_tracker,
)

from conftest import analysis


@pytest.fixture
def tracker(tmp_path) -> XlsxTracker:
    return XlsxTracker(tmp_path / "applications.xlsx")


def read_row(xlsx_path, row=2):
    sheet = openpyxl.load_workbook(xlsx_path).active
    headers = [str(c.value).strip().lower() for c in sheet[1]]
    return dict(zip(headers, (sheet.cell(row=row, column=c + 1).value
                              for c in range(len(headers)))))


def test_seeds_the_spreadsheet_and_fills_a_row(tracker, tmp_path):
    assert not tracker.xlsx_path.exists()

    tracker.record(tmp_path / "Acme_Corp_Head_of_IT", analysis())

    row = read_row(tracker.xlsx_path)
    assert row["company_name"] == "Acme Corp"
    assert row["job_title"] == "Head of IT"
    assert row["application_date"] == date.today().isoformat()
    assert row["application status"] == STATUS_VALUES[0] == "not applied"
    assert row["notes"] is None
    assert row["weblink"] is None
    assert row["cv_path"] is None


def test_the_cv_path_is_recorded(tracker, tmp_path):
    cv = tmp_path / "job" / "cv_jane_doe.pdf"
    tracker.record(tmp_path / "job", analysis(), cv)
    assert read_row(tracker.xlsx_path)["cv_path"] == str(cv)


def test_skip_application_is_in_the_status_dropdown(tracker, tmp_path):
    assert "skip_application" in STATUS_VALUES
    tracker.record(tmp_path / "job", analysis())
    sheet = openpyxl.load_workbook(tracker.xlsx_path).active
    (dropdown,) = sheet.data_validations.dataValidation
    assert dropdown.formula1.strip('"').split(",") == list(STATUS_VALUES)


def test_the_template_has_every_column(tracker, tmp_path):
    tracker.record(tmp_path / "job", analysis())
    sheet = openpyxl.load_workbook(tracker.xlsx_path).active
    assert tuple(c.value for c in sheet[1]) == HEADERS


def test_the_posting_url_becomes_the_weblink(tracker, tmp_path):
    tracker.record(tmp_path / "job", analysis(posting_url="https://jobs.example/1"))
    assert read_row(tracker.xlsx_path)["weblink"] == "https://jobs.example/1"


def test_highlight_markers_are_stripped_from_the_title(tracker, tmp_path):
    """The **bold** markers are LaTeX instructions, not part of the title."""
    tracker.record(tmp_path / "job", analysis(job_title="Staff **Platform** Engineer"))
    assert read_row(tracker.xlsx_path)["job_title"] == "Staff Platform Engineer"


def test_rows_accumulate(tracker, tmp_path):
    tracker.record(tmp_path / "first", analysis(company_name="Globex"))
    tracker.record(tmp_path / "second", analysis(company_name="Initech"))
    assert read_row(tracker.xlsx_path, row=2)["company_name"] == "Globex"
    assert read_row(tracker.xlsx_path, row=3)["company_name"] == "Initech"


def test_tracking_off_writes_nothing(tmp_path):
    tracker = build_tracker(tmp_path, enabled=False)
    assert isinstance(tracker, NullTracker)
    tracker.record(tmp_path / "job", analysis())
    assert not (tmp_path / "applications.xlsx").exists()


def test_waiting_and_rejected_statuses_are_coloured_deeper_as_they_progress(tracker, tmp_path):
    tracker.record(tmp_path / "job", analysis())
    sheet = openpyxl.load_workbook(tracker.xlsx_path).active
    colours = {}
    for rules in sheet.conditional_formatting._cf_rules.values():
        for rule in rules:
            colours[rule.formula[0].strip('"')] = rule.dxf.fill.bgColor.rgb[-6:]
    ordered = [v for v in STATUS_VALUES if v.startswith(("waiting", "rejected"))]
    assert set(colours) == set(ordered)
    for prefix in ("waiting", "rejected"):
        shades = [colours[v] for v in ordered if v.startswith(prefix)]
        assert shades == sorted(shades, reverse=True) and len(set(shades)) == 3


def test_the_autofilter_grows_with_the_rows(tracker, tmp_path):
    tracker.record(tmp_path / "job", analysis())
    wb = openpyxl.load_workbook(tracker.xlsx_path)
    sheet = wb.active
    assert sheet.auto_filter.ref == "A1:G500"
    for row in range(3, 501):
        sheet.cell(row=row, column=3, value="2026-01-01")
    wb.save(tracker.xlsx_path)
    tracker.record(tmp_path / "job2", analysis())
    assert openpyxl.load_workbook(tracker.xlsx_path).active.auto_filter.ref == "A1:G501"
