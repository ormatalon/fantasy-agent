import pytest

from src.interface.digest import _is_actionable
from src.interface.notify import NotifyError, notify


@pytest.mark.parametrize("title", [
    "Ladd McConkey - Not practicing Wednesday",      # 'practic' stem, not 'practice'
    "Myles Garrett (knee) without timetable to return",
    "Aaron Jones Sr. Now the Clear RB1 After Teammate's Injury",
    "Player X ruled out for Sunday",
    "Player Y listed as questionable",
    "Backup RB to see increased workload",
])
def test_decision_relevant_news_is_flagged(title):
    assert _is_actionable(title)


@pytest.mark.parametrize("title", [
    "Travis Kelce solid in win Monday",
    "Isaiah Likely hauls in two touchdowns in win Sunday",
    "Travis Kelce a Victim in Multi-Million Dollar Scam",
    "Jack Campbell - Tallies seven tackles vs. New Orleans",
])
def test_routine_recaps_are_not_flagged(title):
    # Every starter has news every week; a watch list containing the whole
    # lineup is not a watch list.
    assert not _is_actionable(title)


def test_missing_credentials_raise_an_actionable_error(monkeypatch):
    import config

    monkeypatch.setattr(config, "GMAIL_ADDRESS", "")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "")

    with pytest.raises(NotifyError, match="GMAIL_ADDRESS"):
        notify("subject", "body")


def test_a_non_app_password_is_rejected_before_contacting_gmail(monkeypatch):
    import config

    monkeypatch.setattr(config, "GMAIL_ADDRESS", "someone@example.com")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "shortpw")

    with pytest.raises(NotifyError, match="16-character"):
        notify("subject", "body")


def test_spaces_in_an_app_password_are_tolerated(monkeypatch):
    # Google displays app passwords as four groups of four, so they get
    # pasted with spaces more often than not.
    import config

    from src.interface import notify as notify_module

    monkeypatch.setattr(config, "GMAIL_ADDRESS", "someone@example.com")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")

    sent = {}

    class FakeSMTP:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, address, password):
            sent["password"] = password

        def send_message(self, message):
            sent["subject"] = message["Subject"]

    monkeypatch.setattr(notify_module.smtplib, "SMTP_SSL", FakeSMTP)

    notify("hello", "body")

    assert sent["password"] == "abcdefghijklmnop"
    assert sent["subject"] == "hello"
