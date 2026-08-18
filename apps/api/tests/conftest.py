import os
import pytest

# Force default settings for all tests to match expected baseline
os.environ["COST_DISPLAY_CURRENCY"] = "USD"
os.environ["DEFAULT_WORKLOAD_IMAGE"] = "nginx:1.27-alpine"

@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
