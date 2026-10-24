from collections.abc import Iterator

import pytest
from openhost_test_harness import OpenhostStack


@pytest.fixture(scope="session")
def stack() -> Iterator[OpenhostStack]:
    """Build the app's Dockerfile, run it under podman per cloudinabottle.toml, and
    front it with the real Cloud in a Bottle router.

    - stack.url            — through the router; requires owner auth (stack.owner_session)
    - stack.app_url        — direct to the container (control your own headers; eg the health probe)
    - stack.container_name — the app's podman container, for `podman exec`
    """
    with OpenhostStack() as s:
        yield s


@pytest.fixture(scope="session")
def container_name(stack: OpenhostStack) -> str:
    return stack.container_name
