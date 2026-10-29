"""一键启用联网搜索通道：安装嵌入式 SearXNG（免 Docker、免 key）。

用法（仓库根目录）：
    python scripts/enable_search.py            # 安装 + 冒烟测试 + 写入通道配置
    python scripts/enable_search.py --start    # 安装后立即前台试运行（Ctrl+C 停止）
    python scripts/enable_search.py --remove   # 彻底移除（删除 .searchenv 与配置）

产物：
    .searchenv/                     独立 venv（卸载即删目录）
    data/search-channel.json        通道配置（run.py 据此随主服务启停子进程）
"""
from __future__ import annotations

import argparse
import json
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SEARCH_ENV = BASE_DIR / ".searchenv"
CACHE_DIR = BASE_DIR / "data" / "cache"
SHIM_DIR = BASE_DIR / "scripts" / "searxng-embedded" / "shim"
CHANNEL_CONFIG = BASE_DIR / "data" / "search-channel.json"
SEARXNG_TARBALL = "https://github.com/searxng/searxng/archive/refs/heads/master.tar.gz"
PORT = 8889


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("  $", " ".join(str(c) for c in cmd[:6]), "..." if len(cmd) > 6 else "")
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)


def _venv_python() -> Path:
    return SEARCH_ENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def install() -> bool:
    if _venv_python().exists():
        print("[1/6] 独立 venv 已存在，跳过创建")
    else:
        print("[1/6] 创建独立 venv（.searchenv/）...")
        r = _run([sys.executable, "-m", "venv", str(SEARCH_ENV)])
        if r.returncode != 0:
            print("    venv 创建失败：", r.stderr[-400:]); return False

    py = _venv_python()
    print("[2/6] 下载 SearXNG 源码包（缓存于 data/cache）...")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tarball = CACHE_DIR / "searxng-master.tar.gz"
    if not tarball.exists():
        try:
            urllib.request.urlretrieve(SEARXNG_TARBALL, tarball)
        except Exception as exc:
            print("    下载失败：", exc, "（可手动下载后放入 data/cache/）"); return False
    else:
        print("    使用缓存包")

    print("[3/6] 解包（跳过符号链接，Windows 兼容）...")
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(tarball) as tf:
            for m in tf.getmembers():
                if m.issym() or m.islnk():
                    continue
                tf.extract(m, tmp)
        src = Path(tmp) / "searxng-master"
        r = _run([py, "-m", "pip", "install", "-q", "-r", src / "requirements.txt"])
        if r.returncode != 0:
            print("    依赖安装失败：", r.stderr[-500:]); return False
        # setup.py 导入 searx 需先有依赖（鸡生蛋），故先装 requirements 再装包本体
        r = _run([py, "-m", "pip", "install", "-q", "--no-deps", str(src)])
        if r.returncode != 0:
            print("    包安装失败：", r.stderr[-500:]); return False

    print("[4/6] 注入兼容垫片（Python 3.10 需要 3 个；3.11+ 仅 pwd）...")
    settings = BASE_DIR / "data" / "searxng_settings.yml"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        f'use_default_settings: true\n'
        f'server:\n'
        f'  secret_key: "{secrets.token_urlsafe(32)}"\n'
        f'  bind_address: "127.0.0.1"\n'
        f'  port: {PORT}\n'
        f'  limiter: false\n'
        f'  image_proxy: true\n'
        f'search:\n'
        f'  formats:\n'
        f'    - html\n'
        f'    - json\n', encoding="utf-8")

    print("[5/6] 写入通道配置（run.py 将随主服务自动启停搜索子进程）...")
    CHANNEL_CONFIG.write_text(json.dumps({
        "venv_python": str(_venv_python()),
        "settings": str(settings),
        "shim": str(SHIM_DIR),
        "port": PORT,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[6/6] 冒烟测试（启动 12 秒探测 JSON API）...")
    ok = smoke_test(quiet_success=False)
    if not ok:
        print("    冒烟未通过：run.py 仍会尝试托管；也可用 --start 前台排查")
    return True


def smoke_test(quiet_success: bool = True) -> bool:
    import os
    cfg = json.loads(CHANNEL_CONFIG.read_text(encoding="utf-8"))
    env = {**os.environ,
           "SEARXNG_SETTINGS_PATH": cfg["settings"],
           "PYTHONPATH": str(Path(cfg["shim"]).parent / "shim")}
    proc = subprocess.Popen(
        [cfg["venv_python"], "-c", "from searx.webapp import run; run()"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(9)
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{cfg['port']}/search?q=ping&format=json", timeout=10) as resp:
                ok = resp.status == 200
        except Exception as exc:
            ok = False
            print("    探测失败：", exc)
        if ok and not quiet_success:
            print("    ✓ JSON API 正常（127.0.0.1:%d）" % cfg["port"])
        return ok
    finally:
        proc.terminate()


def remove() -> None:
    print("移除嵌入式搜索环境 ...")
    shutil.rmtree(SEARCH_ENV, ignore_errors=True)
    CHANNEL_CONFIG.unlink(missing_ok=True)
    print("已彻底移除（.searchenv/ 与 data/search-channel.json）")


def main() -> None:
    parser = argparse.ArgumentParser(description="JobMap 联网搜索通道（嵌入式 SearXNG）")
    parser.add_argument("--start", action="store_true", help="安装后前台试运行")
    parser.add_argument("--remove", action="store_true", help="彻底移除")
    args = parser.parse_args()
    if args.remove:
        remove(); return
    ok = install()
    if ok and args.start:
        cfg = json.loads(CHANNEL_CONFIG.read_text(encoding="utf-8"))
        import os
        env = {**os.environ, "SEARXNG_SETTINGS_PATH": cfg["settings"],
               "PYTHONPATH": str(Path(cfg["shim"]).parent / "shim")}
        print("前台运行 SearXNG（Ctrl+C 停止）: http://127.0.0.1:%d" % cfg["port"])
        try:
            subprocess.run([cfg["venv_python"], "-c", "from searx.webapp import run; run()"], env=env)
        except KeyboardInterrupt:
            pass
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
