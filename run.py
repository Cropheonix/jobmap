"""JobMap 本地启动入口。

用法：
    python run.py                 # 自动挑选可用端口并在默认浏览器打开
    python run.py --port 8600     # 指定端口
    python run.py --no-browser    # 不自动打开浏览器

安全边界：仅监听 127.0.0.1，不开放到局域网。
"""
import argparse
import socket
import threading
import time
import webbrowser


def find_free_port(preferred: int | None = None) -> int:
    if preferred:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", preferred))
                return preferred
            except OSError:
                pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="JobMap 求职情报地图（本地版）")
    parser.add_argument("--port", type=int, default=None, help="指定端口（默认自动挑选）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    parser.add_argument("--no-search", action="store_true", help="不托管嵌入式搜索通道（SearXNG）")
    args = parser.parse_args()

    import uvicorn

    port = find_free_port(args.port)
    url = f"http://127.0.0.1:{port}"
    searxng_proc = None if args.no_search else _start_embedded_search()
    try:
        if not args.no_browser:
            def _open():
                time.sleep(1.5)
                webbrowser.open(url)
            threading.Thread(target=_open, daemon=True).start()

        print(f"JobMap 启动中：{url} （仅监听本机回环地址）")
        if searxng_proc:
            print("联网搜索通道：嵌入式 SearXNG 已随服务托管（http://127.0.0.1:8889）")
        uvicorn.run(
            "backend.main:app",
            host="127.0.0.1",
            port=port,
            log_level="warning",
            reload=args.reload,
        )
    finally:
        if searxng_proc:
            searxng_proc.terminate()
            print("嵌入式搜索通道已停止")


def _start_embedded_search():
    """若 data/search-channel.json 存在，则托管 SearXNG 子进程（随主服务退出回收）。"""
    import json
    import os
    import subprocess
    from pathlib import Path

    cfg_path = Path(__file__).parent / "data" / "search-channel.json"
    if not cfg_path.exists():
        return None
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        python = cfg["venv_python"]
        if not Path(python).exists():
            return None
        env = {**os.environ,
               "SEARXNG_SETTINGS_PATH": cfg["settings"],
               "PYTHONPATH": str(Path(cfg["shim"]).parent / "shim")}
        return subprocess.Popen(
            [python, "-c", "from searx.webapp import run; run()"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        print(f"嵌入式搜索通道启动失败（主功能不受影响）：{exc}")
        return None


if __name__ == "__main__":
    main()
