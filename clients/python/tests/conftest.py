from __future__ import annotations

from collections.abc import Iterator

import pytest
import respx

from tests.support import BASE


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        yield r
