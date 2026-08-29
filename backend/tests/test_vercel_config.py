"""Static checks for the Vercel multi-service boundary.

The frontend only makes same-origin ``/v1`` requests. Vercel Services therefore
has to route that prefix to the FastAPI app before its catch-all routes the SPA
to Vite. A service root alone is not a Python entrypoint, which is why this
small check exists: Vercel otherwise discovers the omission only after a push.
"""

import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_vercel_config_exposes_the_fastapi_entrypoint_and_api_route() -> None:
    """The deployed service uses the app the local entrypoint names, at ``/v1``."""
    config = json.loads((REPOSITORY_ROOT / "vercel.json").read_text(encoding="utf-8"))

    assert config["services"]["backend"] == {
        "root": "backend",
        "framework": "fastapi",
        "entrypoint": "app.main:app",
    }
    assert config["rewrites"] == [
        {"source": "/v1/(.*)", "destination": {"service": "backend"}},
        {"source": "/(.*)", "destination": {"service": "frontend"}},
    ]
