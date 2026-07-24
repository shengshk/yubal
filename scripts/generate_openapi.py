"""Generate OpenAPI schema by temporarily starting the server."""

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

# Configuration
API_HOST = "127.0.0.1"
API_PORT = 8765  # Use non-standard port to avoid conflicts with dev server
OPENAPI_URL = f"http://{API_HOST}:{API_PORT}/openapi.json"
MAX_WAIT_SECONDS = 10


def wait_for_server(timeout: int = MAX_WAIT_SECONDS) -> bool:
    """Wait for server to be ready."""
    print(f"Waiting for server at {OPENAPI_URL}...", end="", flush=True)

    start = time.time()
    while time.time() - start < timeout:
        try:
            if requests.get(OPENAPI_URL, timeout=1).status_code == 200:
                print(" ready")
                return True
        except requests.exceptions.RequestException:
            pass
        print(".", end="", flush=True)
        time.sleep(0.5)

    print(" failed")
    return False


def main() -> int:
    """Generate OpenAPI schema."""
    print("Starting temporary API server...")

    # Start server with temporary data/config under a temp root
    tmp = Path(tempfile.mkdtemp(prefix="yubal-openapi-"))
    (tmp / "Download").mkdir()
    (tmp / "config").mkdir()
    server_process = subprocess.Popen(
        [
            "uv",
            "run",
            "--package",
            "yubal-api",
            "uvicorn",
            "yubal_api.api.app:app",
            "--host",
            API_HOST,
            "--port",
            str(API_PORT),
            "--log-level",
            "error",
        ],
        env=os.environ
        | {
            "YUBAL_ROOT": str(tmp),
            "YUBAL_DATA": str(tmp / "Download"),
            "YUBAL_CONFIG": str(tmp / "config"),
            "YUBAL_LIBRARY_ROOT": str(tmp),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        if not wait_for_server():
            print(f"Server failed to start within {MAX_WAIT_SECONDS}s")
            return 1

        print("Generating TypeScript types...")
        result = subprocess.run(
            ["bun", "run", "generate-api"],
            cwd="web",
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            print(f"Type generation failed:\n{result.stderr}")
            return 1

        # Strip /api prefix from paths since client.ts uses baseUrl: "/api"
        schema_path = Path("web/src/api/schema.d.ts")
        content = schema_path.read_text()
        content = re.sub(r'"/api/', '"/', content)
        schema_path.write_text(content)

        print("Generated web/src/api/schema.d.ts")
        return 0

    finally:
        print("Shutting down server...")
        server_process.terminate()
        try:
            server_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server_process.kill()
            server_process.wait()


if __name__ == "__main__":
    sys.exit(main())
