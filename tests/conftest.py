from collections.abc import Iterator

import pytest
from openhost_test_harness import OpenhostStack

# Pinned so tests that need to look inside the container (`podman exec`) can
# address it, rather than relying on the harness's default naming.
_CONTAINER_NAME = "openhost-test-bifrost-llm-gateway-container"


@pytest.fixture(scope="session")
def container_name() -> str:
    return _CONTAINER_NAME


@pytest.fixture(scope="session")
def stack() -> Iterator[OpenhostStack]:
    """Build the app's Dockerfile, run it under podman per openhost.toml, and
    front it with a mock OpenHost router that injects owner auth.

    OpenhostStack() finds openhost.toml by walking up from the cwd, so no app_dir
    is needed as long as tests run from within the app tree.

    - stack.url     — through the mock router (auth header injected, like a real owner request)
    - stack.app_url — direct to the container (control your own headers; eg the health probe)
    """
    with OpenhostStack(container_name=_CONTAINER_NAME) as s:
        yield s
