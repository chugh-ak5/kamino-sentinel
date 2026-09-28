"""Pytest configuration.

Ensures the project's .env file is never auto-loaded during tests so that
monkeypatched environment variables fully control configuration state.
"""

import os

os.environ.setdefault("KAMINO_SENTINEL_NO_DOTENV", "1")
