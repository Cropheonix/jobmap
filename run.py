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
    args = parser.parse_args()

    import uvicorn

    port = find_free_port(args.port)
    url = f"http://127.0.0.1:{port}"

    if not args.no_browser:
        def _open():
            time.sleep(1.5)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    print(f"JobMap 启动中：{url} （仅监听本机回环地址）")
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=port,
        log_level="warning",
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
