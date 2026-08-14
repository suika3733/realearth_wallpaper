"""
RealEarth 真实地球 — Flask REST API Server
为 HTML 前端提供所有后端功能接口
"""
import sys
import os
import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

# 将项目根目录加入 sys.path，以便导入现有模块
import sys
FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    PROJECT_ROOT = Path(sys._MEIPASS)
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
THIS_DIR = Path(__file__).resolve().parent if not FROZEN else PROJECT_ROOT
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS

from config import (
    load_config, save_config, load_metadata, save_metadata,
    IMAGE_CACHE_DIR, CATEGORIES, ALL_CATEGORY, WALLPAPER_STYLES,
    DEFAULT_API_KEY, DEFAULT_CONFIG, ensure_dirs, get_image_cache_path,
)
from nasa_api import fetch_apod_range, fetch_apod, download_image, ApodImage
from categorizer import categorize_image, get_category_name, get_all_category_keys
from wallpaper import set_wallpaper, set_wallpaper_style, watermark_image
from scheduler import (
    start_scheduler, stop_scheduler, is_scheduler_running, get_next_refresh_info,
)
from providers import GEOSTATIONARY_SATELLITES, SDO_BANDS, fetch_satellite_image, fetch_sdo_image
from providers.sdo import test_sdo_connectivity
from autostart import set_autostart, is_autostart_enabled

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[],  # 由 launcher.py 统一配置 handler，此处不加
)
logger = logging.getLogger("server")

# 日志目录（与 launcher.py 保持一致）
_LOG_DIR = Path.home() / ".nasa_wallpaper" / "logs"


def _cache_path_to_url(path) -> str | None:
    """将缓存图片的绝对路径转换为可访问的 URL"""
    if not path:
        return None
    try:
        p = Path(path).resolve()
        rel = p.relative_to(IMAGE_CACHE_DIR.resolve())
        return f"/api/image-cache/{rel.as_posix()}"
    except (ValueError, OSError):
        return None


app = Flask(__name__, static_folder=str(THIS_DIR), static_url_path="")
CORS(app)

# 确保数据目录存在
ensure_dirs()

# ---- 状态跟踪 ----
_task_status = {"running": False, "message": "就绪", "type": "ok"}

# ---- 动态时间水印 ----
import threading


# ================================================================
#  静态文件
# ================================================================

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/api/image-cache/<path:filename>")
def serve_cached_image(filename):
    """提供缓存图片"""
    filepath = IMAGE_CACHE_DIR / filename
    if filepath.exists():
        return send_file(str(filepath))
    return jsonify({"error": "not found"}), 404


# ================================================================
#  状态
# ================================================================

@app.route("/api/status")
def api_status():
    return jsonify({
        "scheduler_running": is_scheduler_running(),
        "task_status": _task_status,
        "scheduler": get_next_refresh_info(),
    })


# ================================================================
#  配置
# ================================================================

@app.route("/api/config")
def api_get_config():
    config = load_config()
    return jsonify(config)


@app.route("/api/config", methods=["POST"])
def api_save_config():
    data = request.get_json(force=True)
    config = load_config()
    # 只更新白名单中的字段
    allowed_keys = [
        "api_key", "selected_category", "auto_update", "update_time",
        "hd", "data_source", "satellite_id", "satellite_color",
        "satellite_size", "satellite_auto_refresh", "satellite_refresh_interval",
        "sdo_band", "sdo_size", "sdo_auto_refresh", "sdo_refresh_interval",
        "wallpaper_style", "autostart",
        "apod_auto_set_wallpaper", "sat_auto_set_wallpaper", "sdo_auto_set_wallpaper",
        "wm_font_size", "wm_font_family", "wm_position", "wm_show_sys_time",
        "wp_scale", "wp_offset_x", "wp_offset_y",
    ]
    for key in allowed_keys:
        if key in data:
            config[key] = data[key]
    save_config(config)

    # 处理开机自启动
    if "autostart" in data:
        want = data["autostart"]
        if want != is_autostart_enabled():
            set_autostart(want)

    return jsonify({"ok": True})


# ================================================================
#  APOD
# ================================================================

@app.route("/api/apod/images")
def api_apod_images():
    """获取已缓存的 APOD 图片列表，支持分类筛选"""
    category = request.args.get("category", "all")
    metadata = load_metadata()
    images = metadata.get("images", {})

    result = []
    for date_str, img_data in images.items():
        cat = img_data.get("category", categorize_image(ApodImage.from_dict(img_data)))
        if category == "all" or cat == category:
            sd_path = get_image_cache_path(date_str, hd=False)
            hd_path = get_image_cache_path(date_str, hd=True)
            if sd_path.exists():
                cached_url = _cache_path_to_url(sd_path)
            elif hd_path.exists():
                cached_url = _cache_path_to_url(hd_path)
            else:
                cached_url = None
            result.append({
                **img_data,
                "category": cat,
                "category_name": get_category_name(cat),
                "cached": sd_path.exists() or hd_path.exists(),
                "preview_url": cached_url,
            })

    # 按日期倒序
    result.sort(key=lambda x: x["date"], reverse=True)
    return jsonify({
        "images": result,
        "total": len(result),
        "category": category,
    })


@app.route("/api/apod/categories")
def api_apod_categories():
    """获取所有分类及其图片数量"""
    metadata = load_metadata()
    images = metadata.get("images", {})
    counts = {ALL_CATEGORY: len(images)}
    for key in get_all_category_keys():
        counts[key] = 0

    for img_data in images.values():
        cat = img_data.get("category", "")
        if cat in counts:
            counts[cat] += 1

    cats = [{"key": ALL_CATEGORY, "name": "全部图片", "count": counts.get(ALL_CATEGORY, 0)}]
    for key_name in CATEGORIES.items():
        key, info = key_name
        cats.append({"key": key, "name": info["name"], "count": counts.get(key, 0)})

    return jsonify({"categories": cats})


@app.route("/api/apod/fetch", methods=["POST"])
def api_apod_fetch():
    """获取近 10 天的 APOD 图片"""
    global _task_status
    _task_status = {"running": True, "message": "正在获取 NASA 图片...", "type": "loading"}

    try:
        config = load_config()
        api_key = config.get("api_key", DEFAULT_API_KEY)
        start_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")

        images = fetch_apod_range(start_date, end_date, api_key=api_key)

        metadata = load_metadata()
        new_count = 0
        for img in images:
            if img.date not in metadata["images"]:
                cat = categorize_image(img)
                data = img.to_dict()
                data["category"] = cat
                data["category_name"] = get_category_name(cat)
                metadata["images"][img.date] = data
                new_count += 1

        save_metadata(metadata)
        _task_status = {
            "running": False,
            "message": f"已获取 {len(images)} 张图片（新增 {new_count} 张）",
            "type": "ok",
        }
        return jsonify({
            "ok": True,
            "total": len(images),
            "new": new_count,
        })
    except Exception as e:
        logger.error(f"APOD fetch failed: {e}")
        _task_status = {"running": False, "message": f"获取失败: {e}", "type": "error"}
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/apod/download", methods=["POST"])
def api_apod_download():
    """下载指定日期的 APOD 图片"""
    data = request.get_json(force=True)
    date_str = data.get("date", "")
    hd = data.get("hd", True)

    metadata = load_metadata()
    img_data = metadata["images"].get(date_str)
    if not img_data:
        return jsonify({"ok": False, "error": "图片未找到"}), 404

    img = ApodImage.from_dict(img_data)
    path = download_image(img, hd=hd)
    if not path:
        return jsonify({"ok": False, "error": "下载失败"}), 500

    return jsonify({"ok": True, "path": path})


# ================================================================
#  卫星影像
# ================================================================

@app.route("/api/satellite/fetch", methods=["POST"])
def api_satellite_fetch():
    """获取卫星影像"""
    global _task_status
    _task_status = {"running": True, "message": "正在获取卫星影像...", "type": "loading"}

    try:
        data = request.get_json(force=True) or {}
        satellite = data.get("satellite_id", "himawari")
        color = data.get("color", "natural_color")
        size = data.get("size", 1080)

        path = fetch_satellite_image(satellite=satellite, color=color, target_size=size)
        if not path:
            _task_status = {"running": False, "message": "卫星影像获取失败", "type": "error"}
            return jsonify({"ok": False, "error": "下载失败"}), 500

        sat_info = GEOSTATIONARY_SATELLITES.get(satellite, {})
        sat_name = sat_info.get("name", satellite)

        _task_status = {
            "running": False,
            "message": f"卫星影像已更新 | {sat_name}",
            "type": "ok",
        }
        return jsonify({
            "ok": True,
            "path": path,
            "url": _cache_path_to_url(path),
            "satellite": satellite,
            "satellite_name": sat_name,
        })
    except Exception as e:
        logger.error(f"Satellite fetch failed: {e}")
        _task_status = {"running": False, "message": f"获取失败: {e}", "type": "error"}
        return jsonify({"ok": False, "error": str(e)}), 500


# ================================================================
#  SDO 太阳观测
# ================================================================

@app.route("/api/sdo/fetch", methods=["POST"])
def api_sdo_fetch():
    """获取 SDO 太阳图像"""
    global _task_status
    _task_status = {"running": True, "message": "正在获取太阳图像...", "type": "loading"}

    try:
        data = request.get_json(force=True) or {}
        band = data.get("band", "0304")

        path = fetch_sdo_image(band=band)
        if not path:
            _task_status = {"running": False, "message": "太阳图像获取失败", "type": "error"}
            return jsonify({"ok": False, "error": "下载失败"}), 500

        band_info = SDO_BANDS.get(band, {})
        band_name = band_info.get("name", band)

        _task_status = {
            "running": False,
            "message": f"太阳图像已更新 | {band_name}",
            "type": "ok",
        }
        return jsonify({
            "ok": True,
            "path": path,
            "url": _cache_path_to_url(path),
            "band": band,
            "band_name": band_name,
        })
    except Exception as e:
        logger.error(f"SDO fetch failed: {e}")
        _task_status = {"running": False, "message": f"获取失败: {e}", "type": "error"}
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/sdo/bands")
def api_sdo_bands():
    """返回所有可用的 SDO 波段"""
    bands = [{"key": k, "name": v["name"], "desc": v["desc"]} for k, v in SDO_BANDS.items()]
    return jsonify({"bands": bands})


@app.route("/api/sdo/test", methods=["POST"])
def api_sdo_test():
    """测试 NASA SDO 服务器连通性"""
    data = request.get_json(force=True) or {}
    band = data.get("band")  # None 则测试全部
    result = test_sdo_connectivity(band=band)
    return jsonify(result)


# ================================================================
#  壁纸设置
# ================================================================

@app.route("/api/wallpaper/set", methods=["POST"])
def api_wallpaper_set():
    """将指定图片设为桌面壁纸"""
    global _task_status
    try:
        data = request.get_json(force=True)
        image_path = data.get("path", "")
        style = data.get("style", "fill")

        if not image_path or not os.path.exists(image_path):
            return jsonify({"ok": False, "error": "图片文件不存在"}), 400

        # 读取用户设置的位置/缩放参数
        _cfg = load_config()
        scale = float(data.get("scale", _cfg.get("wp_scale", 1.0)))
        offset_x = int(data.get("offset_x", _cfg.get("wp_offset_x", 0)))
        offset_y = int(data.get("offset_y", _cfg.get("wp_offset_y", 0)))

        success = set_wallpaper(
            image_path, style=style,
            scale=scale, offset_x=offset_x, offset_y=offset_y,
        )
        if success:
            _task_status = {"running": False, "message": "壁纸已设置", "type": "ok"}
            return jsonify({"ok": True})
        else:
            _task_status = {"running": False, "message": "壁纸设置失败", "type": "error"}
            return jsonify({"ok": False, "error": "设置失败"}), 500
    except Exception as e:
        logger.error(f"Set wallpaper failed: {e}")
        _task_status = {"running": False, "message": f"设置失败: {e}", "type": "error"}
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/wallpaper/watermark-and-set", methods=["POST"])
def api_wallpaper_watermark_set():
    """加水印并设置壁纸"""
    global _task_status
    try:
        data = request.get_json(force=True)
        image_path = data.get("path", "")
        left_text = data.get("left_text", "RealEarth")
        right_text = data.get("right_text", "")
        output_key = data.get("output_key", "wp")
        style = data.get("style", "fill")
        font_size = data.get("font_size")
        font_family = data.get("font_family", "msyh")
        position = data.get("position", "top_right")

        if not image_path or not os.path.exists(image_path):
            return jsonify({"ok": False, "error": "图片文件不存在"}), 400

        # 读取用户设置的位置/缩放参数
        _cfg = load_config()
        scale = float(data.get("scale", _cfg.get("wp_scale", 1.0)))
        offset_x = int(data.get("offset_x", _cfg.get("wp_offset_x", 0)))
        offset_y = int(data.get("offset_y", _cfg.get("wp_offset_y", 0)))

        wp_path = watermark_image(
            image_path, left_text, right_text, output_key,
            font_size=font_size,
            font_family=font_family,
            position=position,
        )
        success = set_wallpaper(
            wp_path, style=style,
            scale=scale, offset_x=offset_x, offset_y=offset_y,
        )
        if success:
            _task_status = {"running": False, "message": "水印壁纸已设置", "type": "ok"}
            return jsonify({"ok": True, "watermarked_path": wp_path})
        else:
            _task_status = {"running": False, "message": "壁纸设置失败", "type": "error"}
            return jsonify({"ok": False, "error": "设置失败"}), 500
    except Exception as e:
        logger.error(f"Watermark+set failed: {e}")
        _task_status = {"running": False, "message": f"设置失败: {e}", "type": "error"}
        return jsonify({"ok": False, "error": str(e)}), 500


# ================================================================
#  调度器
# ================================================================

@app.route("/api/scheduler/start", methods=["POST"])
def api_scheduler_start():
    start_scheduler()
    return jsonify({"ok": True, "running": is_scheduler_running()})


@app.route("/api/scheduler/stop", methods=["POST"])
def api_scheduler_stop():
    stop_scheduler()
    return jsonify({"ok": True, "running": is_scheduler_running()})


@app.route("/api/scheduler/status")
def api_scheduler_status():
    return jsonify({
        "running": is_scheduler_running(),
        "scheduler": get_next_refresh_info(),
    })


# ================================================================
#  日志 & 版本
# ================================================================

@app.route("/api/logs/list")
def api_logs_list():
    """列出所有日志文件"""
    log_dir = _LOG_DIR
    if not log_dir.exists():
        return jsonify({"logs": []})
    files = []
    for f in sorted(log_dir.glob("app.log*"), reverse=True):
        files.append({
            "name": f.name,
            "size": f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })
    return jsonify({"logs": files})


@app.route("/api/logs/read")
def api_logs_read():
    """读取指定日志文件内容（最新 N 行）"""
    filename = request.args.get("file", "app.log")
    lines = int(request.args.get("lines", 200))
    log_path = _LOG_DIR / filename

    # 安全检查：防止路径穿越
    log_path = log_path.resolve()
    log_dir = _LOG_DIR.resolve()
    if not str(log_path).startswith(str(log_dir)):
        return jsonify({"ok": False, "error": "非法文件路径"}), 403

    if not log_path.exists():
        return jsonify({"ok": False, "error": "日志文件不存在"}), 404

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            recent = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return jsonify({
            "ok": True,
            "file": log_path.name,
            "total_lines": len(all_lines),
            "content": "".join(recent),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/version")
def api_version():
    """返回当前版本号"""
    return jsonify({"version": "v3.1.3"})


# ================================================================
#  启动
# ================================================================

def run_server(host="127.0.0.1", port=51234):
    """启动 Flask 服务器"""
    logger.info(f"Starting server on {host}:{port}")
    # 确保后台调度器随服务启动（幂等，launcher 也会调用）
    start_scheduler()
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    run_server()
