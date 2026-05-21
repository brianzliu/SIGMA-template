"""Dashboard subpackage: live HTML dashboard + JSON snapshot helpers."""

from pathlib import Path

_DASHBOARD_HTML_PATH = Path(__file__).parent / "template.html"


def dashboard_html() -> bytes:
    """Return the dashboard HTML as bytes (loaded from template.html)."""
    return _DASHBOARD_HTML_PATH.read_bytes()


__all__ = ["dashboard_html"]
