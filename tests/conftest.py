import pytest
from app import service, ai

@pytest.fixture(autouse=True)
def _fresh_caches():
    service.clear_caches(); ai.reset(); yield; service.clear_caches(); ai.reset()
