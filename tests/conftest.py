import os


# Unit/integration tests do not depend on a local Docker daemon or sandbox image.
# Security-default tests explicitly delete these test-only overrides.
os.environ.setdefault("CURATOR_CONTAINER_SANDBOX_ENABLED", "false")
os.environ.setdefault("CURATOR_CONTAINER_SANDBOX_FALLBACK", "false")
os.environ.setdefault(
    "CURATOR_SANDBOX_WORKER_API_KEY",
    "test-curator-sandbox-worker-key-0000000000000000",
)
