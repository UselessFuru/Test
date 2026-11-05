"""Start the optional local SearXNG service. No administrator shell required."""
from pathlib import Path
import json
import secrets
import shutil
import subprocess
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener
from urllib.parse import urlencode
from urllib.error import URLError

ROOT = Path(__file__).resolve().parent / "search"


def main():
    docker = shutil.which("docker")
    if not docker:
        print("Install Docker Desktop for Windows from https://www.docker.com/products/docker-desktop/ first.")
        print("Start Docker Desktop using Linux containers, then run Start-Search.bat again.")
        return 1
    try:
        info = subprocess.run([docker, "info", "--format", "{{.OSType}}"], capture_output=True, text=True, timeout=20)
        if info.returncode or info.stdout.strip() != "linux":
            print("Start Docker Desktop and select Linux containers. Wait until its engine is running, then retry.")
            return 1
        env_file = ROOT / ".env"
        if not env_file.exists():
            env_file.write_text("RAIDEN_SEARCH_SECRET=" + secrets.token_hex(32) + "\n", encoding="utf-8")
        print("Starting local search. The first run downloads SearXNG's software image, not game guides.", flush=True)
        started = subprocess.run([docker, "compose", "up", "-d"], cwd=ROOT)
        if started.returncode:
            print("Search did not start. Check the message above; port 8888 must be available.")
            return 1
        opener = build_opener(ProxyHandler({}))
        for _ in range(12):
            try:
                request = Request("http://127.0.0.1:8888/search", data=urlencode({"q": "SearXNG documentation", "format": "json"}).encode())
                with opener.open(request, timeout=3) as response:
                    data = json.loads(response.read(1024 * 1024))
                if isinstance(data.get("results"), list):
                    print("Search is ready at http://127.0.0.1:8888")
                    print("Keep Docker Desktop running. Start Raiden, then use Check-Connections.bat if needed.")
                    return 0
            except (URLError, OSError, ValueError):
                pass
            time.sleep(1)
        print("Container started, but search is not ready yet. Retry Test search connection in Raiden Settings > Guides.")
        return 1
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("Docker could not be started:", str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
