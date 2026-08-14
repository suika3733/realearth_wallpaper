"""
RealEarth 真实地球 — 新版 Web UI 入口
支持 PyInstaller 打包，Flask 后端 + WebView/Browser 前端
"""
import sys
import os
import threading
import time
import logging
from pathlib import Path

# ---- 路径设置 ----
FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    MEIPASS = Path(sys._MEIPASS)
    BASE_DIR = MEIPASS
else:
    BASE_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(BASE_DIR))

# ---- 导入 ui-redesign/server.py ----
_ui_dir = BASE_DIR / "ui-redesign"
if _ui_dir.exists():
    sys.path.insert(0, str(_ui_dir))

import server as flask_server

# 启动后台调度器（自动刷新 + 自动设壁纸）
from scheduler import start_scheduler, stop_scheduler

from logging.handlers import RotatingFileHandler

# 日志目录
_LOG_DIR = Path.home() / ".nasa_wallpaper" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "app.log"

# 根日志器配置（同时输出到文件和控制台）
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_root_logger.handlers.clear()

_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# 文件处理器（最多保留 5 个文件，每个最大 2MB）
_fh = RotatingFileHandler(str(_LOG_FILE), maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
_fh.setFormatter(_fmt)
_root_logger.addHandler(_fh)

# 控制台处理器
_ch = logging.StreamHandler(sys.stdout)
_ch.setFormatter(_fmt)
_root_logger.addHandler(_ch)

logger = logging.getLogger("launcher")

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 51234
SERVER_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"

# ---- 全局运行时引用（用于彻底关闭所有后台进程） ----
_flask_server = None          # werkzeug make_server 返回的 server 对象
_flask_thread = None          # Flask 服务器线程
_tray_thread = None           # 托盘线程
_shutting_down = False        # 退出锁，避免重复清理


def _is_already_running() -> bool:
    """检测是否已有实例在运行（端口被占用即视为已运行）"""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        result = s.connect_ex((SERVER_HOST, SERVER_PORT))
        return result == 0
    finally:
        s.close()


# ---- 系统托盘 & 窗口状态 ----
_tray_icon = None
_window = None
_window_maximized = False


class WindowAPI:
    """暴露给前端 JS 的 API"""

    def hide(self):
        if _window:
            _window.hide()
        return True

    def show(self):
        if _window:
            _window.show()
            _window.restore()
        return True

    def minimize(self):
        if _window:
            _window.minimize()
        return True

    def toggle_maximize(self):
        global _window_maximized
        if _window:
            if _window_maximized:
                _window.restore()
            else:
                _window.maximize()
        return True

    def quit_app(self):
        # 前端「关闭软件」按钮 → 走完整清理流程，确保无后台进程残留
        shutdown_app()
        return True


def _get_logo_path():
    """查找 logo 图片路径（打包/开发两种模式）"""
    candidates = [
        BASE_DIR / "logo.png",                   # FROZEN: MEIPASS/logo.png
        BASE_DIR / "ui-redesign" / "logo.png",  # 开发: ui-redesign/logo.png
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def _create_tray_image():
    from PIL import Image, ImageDraw

    # 优先使用新 logo
    logo_path = _get_logo_path()
    if logo_path:
        try:
            logo = Image.open(logo_path).convert("RGBA")
            return logo.resize((64, 64), Image.LANCZOS)
        except Exception as e:
            logger.warning(f"Load tray logo failed: {e}")

    # 回退：代码绘制的圆形图标
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    dc = ImageDraw.Draw(img)
    dc.ellipse([4, 4, 60, 60], fill=(77, 171, 154))
    dc.ellipse([14, 14, 50, 50], fill=(26, 90, 138))
    dc.ellipse([20, 16, 36, 28], fill=(255, 255, 255, 60))
    return img


def _on_tray_show(icon, item):
    if _window:
        _window.show()
        _window.restore()


def _on_tray_quit(icon, item):
    # 托盘「退出」→ 走完整清理流程，确保无后台进程残留
    shutdown_app()


def _run_tray():
    global _tray_icon
    import pystray

    menu = pystray.Menu(
        pystray.MenuItem("显示窗口", _on_tray_show),
        pystray.MenuItem("退出", _on_tray_quit),
    )
    _tray_icon = pystray.Icon(
        "RealEarth",
        _create_tray_image(),
        "RealEarth 真实地球",
        menu,
    )
    _tray_icon.run()


def _on_window_maximized():
    global _window_maximized
    _window_maximized = True


def _on_window_restored():
    global _window_maximized
    _window_maximized = False


def _on_window_closing():
    # 正在彻底退出时，允许窗口真正关闭（不再隐藏到托盘）
    if _shutting_down:
        return True
    # 正常情况：阻止关闭，改为隐藏到托盘
    if _window:
        _window.hide()
    return False


def start_flask():
    global _flask_server
    from werkzeug.serving import make_server

    # 用 make_server 持有 server 对象，方便后续 shutdown() 优雅关闭
    _flask_server = make_server(
        SERVER_HOST, SERVER_PORT, flask_server.app,
        threaded=True, processes=1,
    )
    try:
        _flask_server.serve_forever()
    except Exception as e:
        if not _shutting_down:
            logger.warning(f"Flask server stopped: {e}")


def _stop_flask():
    """优雅关闭 Flask HTTP 服务器（阻塞直到线程结束）"""
    global _flask_server, _flask_thread
    if _flask_server is not None:
        try:
            _flask_server.shutdown()
        except Exception as e:
            logger.warning(f"Flask shutdown: {e}")
        _flask_server = None
    # 等待 Flask 线程真正结束（最多 3 秒）
    if _flask_thread and _flask_thread.is_alive():
        _flask_thread.join(timeout=3.0)
    logger.info("Flask server shut down")


def shutdown_app(force_exit: bool = True):
    """完整关闭所有后台进程，确保退出后零残留

    统一关闭顺序：
      1. 停止后台调度器线程并等待其退出
      2. 停止系统托盘图标
      3. 关闭 Flask HTTP 服务器线程
      4. 销毁 pywebview 窗口
      5. 强制退出进程（杀掉任何残留子进程/线程）
    """
    global _shutting_down, _flask_server, _flask_thread, _tray_thread
    if _shutting_down:
        return
    _shutting_down = True
    logger.info("=== Shutting down RealEarth ===")

    # 1. 停止调度器并等待线程退出
    try:
        stop_scheduler()
        import scheduler as _sched_mod
        _st = getattr(_sched_mod, "_scheduler_thread", None)
        if _st and _st.is_alive():
            _st.join(timeout=5.0)
    except Exception as e:
        logger.warning(f"Stop scheduler: {e}")

    # 2. 停止托盘图标
    if _tray_icon:
        try:
            _tray_icon.stop()
        except Exception as e:
            logger.warning(f"Stop tray: {e}")
        _tray_icon = None

    # 3. 关闭 Flask 服务器
    _stop_flask()

    # 4. 销毁 pywebview 窗口
    if _window:
        try:
            _window.destroy()
        except Exception as e:
            logger.warning(f"Destroy window: {e}")
        _window = None

    logger.info("=== RealEarth shut down complete ===")

    # 5. 强制退出：一次性终止所有 daemon 线程与残留子进程，保证零残留
    if force_exit:
        os._exit(0)


def main():
    global _window

    # 单实例保护：已有实例运行时，直接退出，避免端口冲突导致界面错乱
    if _is_already_running():
        logger.warning("检测到已有实例运行，本实例退出")
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "RealEarth 已在运行。\n请检查系统托盘图标（右下角）。",
                "已在运行",
                0x40,  # MB_ICONINFORMATION
            )
        except Exception:
            pass
        return

    # 启动 Flask（后台线程）
    global _flask_thread, _tray_thread
    _flask_thread = threading.Thread(target=start_flask, daemon=True)
    _flask_thread.start()
    time.sleep(1.0)
    logger.info(f"Server ready: {SERVER_URL}")

    # 启动后台调度器（自动刷新 + 自动设为壁纸）
    start_scheduler()
    logger.info("Scheduler started by launcher")

    # 尝试 WebView，失败则打开浏览器
    try:
        import webview

        api = WindowAPI()

        _window = webview.create_window(
            title="RealEarth — 真实地球",
            url=SERVER_URL,
            width=1180,
            height=760,
            min_size=(860, 580),
            resizable=True,
            frameless=True,
            easy_drag=False,
            confirm_close=False,
            js_api=api,
        )

        _window.events.closing += _on_window_closing
        _window.events.maximized += _on_window_maximized
        _window.events.restored += _on_window_restored

        # 启动系统托盘（后台线程）
        _tray_thread = threading.Thread(target=_run_tray, daemon=True)
        _tray_thread.start()

        logger.info("Starting pywebview window (frameless)...")
        webview.start(debug=False)
    except Exception:
        logger.info("pywebview unavailable, opening browser...")
        import webbrowser

        try:
            webbrowser.open(SERVER_URL)
        except Exception:
            logger.info(f"Please open {SERVER_URL} manually")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    # webview.start() 正常返回（窗口被销毁）或浏览器降级被中断
    shutdown_app(force_exit=True)


if __name__ == "__main__":
    main()
