"""The live engine on Modal: the same FastAPI app the dashboard calls.

    modal deploy deploy/modal_app.py

One container (4 cores, 8 GB), scaled to zero when idle. Run folders live on a
Modal volume, so live runs survive restarts; the copilot's spend ledger lives on
a second volume so its cap holds across containers.
"""
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("torch==2.8.0", "torchvision==0.23.0", index_url="https://download.pytorch.org/whl/cpu")
    .pip_install_from_pyproject(str(ROOT / "pyproject.toml"))
    .env({"PYTHONPATH": "/app/src", "BATO_SPEND_FILE": "/app/state/api_spend.jsonl",
          "GDAL_HTTP_MULTIPLEX": "YES", "GDAL_HTTP_VERSION": "2"})
    .add_local_dir(str(ROOT / "src"), "/app/src", ignore=["**/__pycache__"])
    .add_local_dir(str(ROOT / "scripts"), "/app/scripts", ignore=["**/__pycache__"])
)

runs = modal.Volume.from_name("lifeline-runs", create_if_missing=True)
state = modal.Volume.from_name("lifeline-state", create_if_missing=True)
app = modal.App("lifeline")


@app.function(image=image, cpu=4.0, memory=8192, timeout=3600, max_containers=1, scaledown_window=1200,
              volumes={"/app/runs": runs, "/app/state": state},
              secrets=[modal.Secret.from_name("lifeline-anthropic")])
@modal.concurrent(max_inputs=32)
@modal.asgi_app()
def web():
    import threading
    import time

    from lifeline.api.server import app as api

    def keep_saved():   # live runs write to the volume from worker threads; save them every minute
        while True:
            time.sleep(60)
            try:
                runs.commit()
                state.commit()
            except Exception:
                pass

    threading.Thread(target=keep_saved, daemon=True).start()
    return api
