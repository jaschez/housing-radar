"""Sending the composed e-mail through the configured backend."""
import pytest

import notify


@pytest.fixture
def mail(tmp_path):
    p = str(tmp_path / "x_")
    for name, body in (("email_subject.txt", "Subject line"), ("email.html", "<p>hi</p>"), ("email.txt", "hi")):
        (tmp_path / f"x_{name}").write_text(body)
    return p


def test_file_backend_does_not_send(mail):
    assert "not sent" in notify.send(mail, {"notify": {"backend": "file"}})


def test_smtp_backend_builds_a_multipart_message(mail, monkeypatch):
    sent = {}

    class Server:
        def __init__(self, host, port, timeout=None):
            sent["addr"] = (host, port)

        def starttls(self, context=None):
            sent["tls"] = True

        def login(self, user, password):
            sent["login"] = (user, password)

        def send_message(self, msg):
            sent["msg"] = msg

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(notify.smtplib, "SMTP", Server)
    monkeypatch.setenv("SMTP_USER", "me@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    cfg = {"notify": {"backend": "smtp", "to": "me@example.com", "smtp": {"host": "smtp.example.com", "port": 587}}}
    assert notify.send(mail, cfg) == "smtp:smtp.example.com"
    assert sent["tls"] and sent["login"] == ("me@example.com", "app-password")
    assert sent["msg"]["Subject"] == "Subject line" and sent["msg"].is_multipart()


def test_missing_recipient_is_an_error(mail):
    with pytest.raises(RuntimeError):
        notify.send(mail, {"notify": {"backend": "smtp"}})
