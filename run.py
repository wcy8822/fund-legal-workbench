"""Run with `python run.py`; bind only to loopback, never public interfaces."""
import os
import uvicorn
from app.main import create_app

if __name__ == "__main__":
    app = create_app()
    print("私募基金投资法务工作台 · 仅供虚构数据本地演示")
    print("打开 http://127.0.0.1:8765 · Ctrl+C 停止")
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("FWB_PORT", "8765")), access_log=False)
