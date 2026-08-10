"""Jira Cloud REST helpers (create issue + browse URL)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

JIRA_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True, slots=True)
class JiraIssueResult:
    key: str
    url: str
    created: bool


def jira_browse_url(site_url: str, issue_key: str) -> str:
    base = site_url.rstrip("/")
    return f"{base}/browse/{issue_key}"


def create_jira_issue(
    *,
    site_url: str,
    email: str,
    api_token: str,
    project_key: str,
    issue_type: str,
    summary: str,
    description: str,
) -> JiraIssueResult | None:
    """Create a Jira Cloud issue via REST API v3. Returns None on failure."""
    base = site_url.rstrip("/")
    url = f"{base}/rest/api/3/issue"
    # ADF document for description (Jira Cloud API v3)
    body = {
        "fields": {
            "project": {"key": project_key},
            "summary": summary[:255],
            "issuetype": {"name": issue_type},
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": description[:4000],
                            }
                        ],
                    }
                ],
            },
        }
    }
    try:
        with httpx.Client(timeout=JIRA_TIMEOUT_SECONDS) as client:
            response = client.post(
                url,
                json=body,
                auth=(email, api_token),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
        if response.status_code >= 400:
            logger.warning(
                "jira_create_issue_failed",
                status_code=response.status_code,
                body=response.text[:300],
            )
            return None
        payload = response.json()
        key = str(payload.get("key") or "").strip()
        if not key:
            logger.warning("jira_create_issue_missing_key")
            return None
        return JiraIssueResult(key=key, url=jira_browse_url(base, key), created=True)
    except Exception as exc:  # noqa: BLE001 - never break callers
        logger.warning("jira_create_issue_error", error=str(exc))
        return None


def add_jira_comment(
    *,
    site_url: str,
    email: str,
    api_token: str,
    issue_key: str,
    body_text: str,
) -> bool:
    base = site_url.rstrip("/")
    url = f"{base}/rest/api/3/issue/{issue_key}/comment"
    payload = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": body_text[:4000]}],
                }
            ],
        }
    }
    try:
        with httpx.Client(timeout=JIRA_TIMEOUT_SECONDS) as client:
            response = client.post(
                url,
                json=payload,
                auth=(email, api_token),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
        if response.status_code >= 400:
            logger.warning(
                "jira_add_comment_failed",
                status_code=response.status_code,
                issue_key=issue_key,
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("jira_add_comment_error", error=str(exc), issue_key=issue_key)
        return False
