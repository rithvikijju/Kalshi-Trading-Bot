"""Slack/Discord webhook alerts. Both are optional; if neither is configured
the call no-ops."""
from __future__ import annotations
import requests
from ..config import KEYS
from ..utils.logging import get_logger

log = get_logger("alerts")


def send(message: str, severity: str = "info") -> None:
    payload = {"text": f"[lab][{severity}] {message}"}
    sent = False
    if KEYS.slack_webhook:
        try:
            requests.post(KEYS.slack_webhook, json=payload, timeout=10)
            sent = True
        except requests.RequestException as e:
            log.warning("slack send failed: %s", e)
    if KEYS.discord_webhook:
        try:
            requests.post(KEYS.discord_webhook,
                          json={"content": payload["text"]}, timeout=10)
            sent = True
        except requests.RequestException as e:
            log.warning("discord send failed: %s", e)
    if not sent:
        log.info("[alert/%s] %s", severity, message)
