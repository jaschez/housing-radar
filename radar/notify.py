"""Send a composed e-mail. Backends, chosen with `notify.backend` in the config:

  smtp          Any SMTP server with STARTTLS (or SSL on port 465). Host, port and sender
                in the config; SMTP_USER and SMTP_PASSWORD in the workspace secrets.env.
  claude-gmail  Claude Code with the claude.ai Gmail connector (`claude -p`, Haiku). Useful
                when that connector is the only mail route on the machine.
  file          Do not send; leave the e-mail in out/ (tests, dry runs).

An e-mail is the three files report.py/watch.py write with a common prefix:
<prefix>email_subject.txt, <prefix>email.html and <prefix>email.txt.

Usage: python radar/notify.py <prefix>     -> exit 0 only if the e-mail went out
"""
from __future__ import annotations

import argparse
import re
import shutil
import smtplib
import ssl
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path

import workspace

CLAUDE_GMAIL_PROMPT = """Mechanical task, no questions: send one e-mail with the mcp__claude_ai_Gmail__send_message tool.

- to: ["{to}"]
- subject: the exact content of the file `{subject}` (one line).
- htmlBody: the exact content of the file `{html}`, unchanged and unabridged.

Read both files with Read, send the e-mail exactly once, and reply only with the id of the sent message."""


def send(prefix: str, cfg: dict) -> str:
    """Send the e-mail at `prefix`. Returns an id or a description; raises on failure."""
    conf = cfg.get("notify") or {}
    backend = conf.get("backend", "file")
    to = conf.get("to")
    subject = Path(prefix + "email_subject.txt").read_text(encoding="utf-8").strip()
    html = Path(prefix + "email.html").read_text(encoding="utf-8")
    text = Path(prefix + "email.txt").read_text(encoding="utf-8")
    if backend == "file":
        return f"not sent (backend file): {prefix}email.html"
    if not to:
        raise RuntimeError("notify.to is empty")
    if backend == "smtp":
        return _smtp(conf, to, subject, html, text)
    if backend == "claude-gmail":
        return _claude_gmail(to, prefix)
    raise RuntimeError(f"unknown notify backend: {backend}")


def _smtp(conf, to, subject, html, text) -> str:
    s = conf.get("smtp") or {}
    sec = workspace.secrets()
    host, port = s.get("host"), int(s.get("port", 587))
    user, password = sec.get("SMTP_USER"), sec.get("SMTP_PASSWORD")
    if not host:
        raise RuntimeError("notify.smtp.host is empty")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, s.get("from") or user, to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    ctx = ssl.create_default_context()
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=60)
    else:
        server = smtplib.SMTP(host, port, timeout=60)
        server.starttls(context=ctx)
    with server:
        if user and password:
            server.login(user, password)
        server.send_message(msg)
    return f"smtp:{host}"


def _claude_gmail(to, prefix) -> str:
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("backend claude-gmail needs the `claude` command in PATH")
    prompt = CLAUDE_GMAIL_PROMPT.format(to=to, subject=prefix + "email_subject.txt", html=prefix + "email.html")
    r = subprocess.run([exe, "-p", prompt, "--model", "haiku", "--allowedTools",
                        "Read,mcp__claude_ai_Gmail__send_message", "--permission-mode", "dontAsk",
                        "--output-format", "text"], capture_output=True, text=True, timeout=600,
                       stdin=subprocess.DEVNULL)
    out = (r.stdout or "").strip()
    Path(prefix + "send.log").write_text(out + (r.stderr or ""))
    # Without a message id in the answer, it is not considered sent.
    m = re.search(r"\b[0-9a-f]{12,}\b", out)
    if r.returncode != 0 or not m:
        raise RuntimeError(f"claude-gmail did not confirm the e-mail: {out[-300:]}")
    return m.group(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prefix")
    ap.add_argument("--workspace")
    a = ap.parse_args()
    workspace.use(a.workspace)
    try:
        print(send(a.prefix, workspace.load_config()))
    except Exception as e:  # noqa: BLE001
        print(f"e-mail not sent: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
