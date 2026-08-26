default: test

image := "bifrost-llm-gateway"

# Install dev dependencies, pre-commit hooks, and the playwright chromium browser.
setup:
    uv sync
    uv run pre-commit install
    uv run playwright install chromium

# Build the container image.
build:
    docker build -t {{image}} .

# Build and run the container locally on http://localhost:8080, persisting
# Bifrost's data under ./data. Configure providers + keys in the web UI.
run: build
    mkdir -p ./data ./temp_data
    docker run --rm -p 8080:8080 \
        -e OPENHOST_APP_DATA_DIR=/data/app_data/bifrost-llm-gateway \
        -e OPENHOST_APP_TEMP_DIR=/data/app_temp_data/bifrost-llm-gateway \
        -v $(pwd)/data:/data/app_data/bifrost-llm-gateway \
        -v $(pwd)/temp_data:/data/app_temp_data/bifrost-llm-gateway \
        {{image}}

# Run the test suite (builds the Dockerfile and runs it under podman).
test:
    uv run pytest -x

# Lint and format (same checks as the pre-commit hooks).
check:
    uv run ruff check --fix .
    uv run ruff format .
