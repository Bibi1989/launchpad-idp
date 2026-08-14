"""Minimal HTML pages returned on the loopback callback."""

from __future__ import annotations

SUCCESS_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Authentication complete</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 3rem auto; max-width: 32rem;
           color: #0f172a; line-height: 1.5; }
    h1 { font-size: 1.25rem; margin: 0 0 0.5rem; }
    p { margin: 0; color: #475569; }
  </style>
</head>
<body>
  <h1>Authentication complete</h1>
  <p>You can close this window and return to the application.</p>
</body>
</html>
"""

ERROR_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Authentication failed</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 3rem auto; max-width: 32rem;
           color: #0f172a; line-height: 1.5; }
    h1 { font-size: 1.25rem; margin: 0 0 0.5rem; color: #b91c1c; }
    p { margin: 0; color: #475569; }
  </style>
</head>
<body>
  <h1>Authentication failed</h1>
  <p>{message}</p>
</body>
</html>
"""


def render_error_html(message: str) -> str:
    safe = (
        message.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return ERROR_HTML.format(message=safe or "An unexpected error occurred.")
