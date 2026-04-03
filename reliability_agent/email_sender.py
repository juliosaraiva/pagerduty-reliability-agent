"""
Email sender.

Sends the rendered HTML report via SMTP. Uses only stdlib modules
(smtplib, email.mime) — no additional dependencies required.
"""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import structlog

logger = structlog.get_logger(__name__)


class EmailSender:
    """Sends HTML reports via SMTP."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        from_address: str,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._from = from_address

    def send(self, html: str, subject: str, to_addresses: list[str]) -> None:
        """Send the HTML report to the specified recipients."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._from
        msg["To"] = ", ".join(to_addresses)
        msg.attach(MIMEText(html, "html"))

        logger.info(
            "sending_email",
            to=to_addresses,
            subject=subject,
            smtp_host=self._host,
        )

        with smtplib.SMTP(self._host, self._port) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(self._user, self._password)
            smtp.sendmail(self._from, to_addresses, msg.as_string())

        logger.info("email_sent", recipients=len(to_addresses))
