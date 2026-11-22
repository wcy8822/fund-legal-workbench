"""Optional Node-based render checks using real isolated API responses, not E2E."""
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix="fwb-render-check-") as temporary:
    fixture = str(Path(temporary) / "fixtures.json")
    subprocess.run([sys.executable, str(root / "scripts/frontend_fixtures.py"), fixture], cwd=root, check=True)
    subprocess.run(["node", "--check", str(root / "app/static/app.js")], cwd=root, check=True)
    subprocess.run(["node", str(root / "scripts/frontend_render_check.cjs"), fixture], cwd=root, check=True)
