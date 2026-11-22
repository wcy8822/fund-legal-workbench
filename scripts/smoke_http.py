"""Check the real loopback HTTP server without creating a public deployment."""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="fwb-http-smoke-") as temporary:
        env = os.environ.copy()
        env["FWB_DATA_DIR"], env["FWB_PORT"] = temporary, "18765"
        log = Path(temporary) / "server.log"
        with log.open("w") as output:
            server = subprocess.Popen([sys.executable, "run.py"], cwd=root, env=env, stdout=output, stderr=subprocess.STDOUT)
            try:
                ready = False
                for _ in range(50):
                    if server.poll() is not None:
                        break
                    try:
                        with urllib.request.urlopen("http://127.0.0.1:18765/api/health", timeout=0.5) as response:
                            health = json.load(response)
                        ready = True
                        break
                    except OSError:
                        time.sleep(0.1)
                if not ready:
                    raise RuntimeError("HTTP server not reachable: " + log.read_text())
                with urllib.request.urlopen("http://127.0.0.1:18765/") as response:
                    html = response.read().decode()
                with urllib.request.urlopen("http://127.0.0.1:18765/static/app.js") as response:
                    javascript = response.read()
                request = urllib.request.Request("http://127.0.0.1:18765/api/demo", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(request) as response:
                    seed = json.load(response)
                assert health["status"] == "ok" and "私募基金" in html and javascript and seed["project_id"]
                print(json.dumps({"http_health": "ok", "homepage": True, "javascript_bytes": len(javascript), "seed_created": True, "bind": "127.0.0.1"}))
            finally:
                if server.poll() is None:
                    server.terminate()
                    server.wait(timeout=5)


if __name__ == "__main__":
    main()
