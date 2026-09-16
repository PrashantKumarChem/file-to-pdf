"""ci/weekly_check.py: the "Weekly check failed" issue tracks the weekly schedule, never twice."""

from ci import weekly_check

PASSING = {"workflow": "Tests", "conclusion": "success", "url": "https://example.invalid/1"}
FAILING = {"workflow": "Visual comparison", "conclusion": "failure", "url": "https://example.invalid/2"}
UNKNOWN = {"workflow": "Windows app", "conclusion": None, "url": None}


def test_everything_passing_and_no_issue_open_is_a_noop():
    action, body = weekly_check.verdict({"statuses": [PASSING], "issue_open": None})
    assert action == "noop"
    assert "passing" in body


def test_a_failure_with_no_issue_open_creates_one():
    action, body = weekly_check.verdict({"statuses": [PASSING, FAILING], "issue_open": None})
    assert action == "create"
    assert "**failure**" in body


def test_a_failure_with_the_issue_already_open_comments_instead():
    action, _ = weekly_check.verdict({"statuses": [FAILING], "issue_open": 7})
    assert action == "comment"


def test_recovering_closes_the_open_issue():
    action, body = weekly_check.verdict({"statuses": [PASSING], "issue_open": 7})
    assert action == "close"
    assert "passing" in body


def test_still_passing_with_nothing_open_stays_a_noop():
    action, _ = weekly_check.verdict({"statuses": [PASSING], "issue_open": None})
    assert action == "noop"


def test_a_workflow_with_no_weekly_run_yet_is_not_treated_as_failing():
    action, body = weekly_check.verdict({"statuses": [PASSING, UNKNOWN], "issue_open": None})
    assert action == "noop"
    assert "no weekly (or dispatched) run yet" in body


def test_an_unknown_status_never_closes_a_real_failure_by_itself():
    action, _ = weekly_check.verdict({"statuses": [FAILING, UNKNOWN], "issue_open": 7})
    assert action == "comment"


def test_missing_statuses_is_a_noop_not_a_crash():
    action, body = weekly_check.verdict({"issue_open": None})
    assert action == "noop"
    assert "each workflow this watches" in body


def test_a_status_with_no_url_still_gets_a_line():
    action, body = weekly_check.verdict({"statuses": [{"workflow": "Tests", "conclusion": "success"}]})
    assert action == "noop"
    assert "Tests: passing." in body


def test_a_skipped_run_is_not_treated_as_failing():
    skipped = {"workflow": "Windows app", "conclusion": "skipped", "url": "https://example.invalid/3"}
    action, body = weekly_check.verdict({"statuses": [PASSING, skipped], "issue_open": None})
    assert action == "noop"
    assert "**skipped**" in body


def test_a_cancelled_run_is_treated_as_failing():
    cancelled = {"workflow": "Windows app", "conclusion": "cancelled", "url": "https://example.invalid/4"}
    action, body = weekly_check.verdict({"statuses": [PASSING, cancelled], "issue_open": None})
    assert action == "create"
    assert "**cancelled**" in body
