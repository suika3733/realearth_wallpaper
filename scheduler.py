"""调度模块 - 支持 NASA APOD + 多卫星 + SDO 自动刷新并设为壁纸"""
import threading
import logging
from datetime import datetime, timedelta

from config import load_config, save_config, load_metadata, save_metadata
from nasa_api import fetch_apod, download_image
from categorizer import categorize_image
from wallpaper import set_wallpaper, watermark_image
from providers import GEOSTATIONARY_SATELLITES, fetch_satellite_image, fetch_sdo_image

logger = logging.getLogger(__name__)

_scheduler_thread = None
_stop_event = threading.Event()

# 各数据源下次刷新时间（供前端展示倒计时）
_next_refresh = {
    "apod": None,
    "satellite": None,
    "sdo": None,
}


# 各数据源上次刷新时间（datetime，用于精确判定是否到点）
_last_refresh_time = {
    "apod": None,
    "satellite": None,
    "sdo": None,
}


def _due(source: str, interval_minutes: int) -> bool:
    """判断某数据源是否到刷新时间"""
    last = _last_refresh_time.get(source)
    if last is None:
        return True
    return (datetime.now() - last).total_seconds() >= interval_minutes * 60


def check_and_update() -> bool:
    """NASA APOD 每日检查更新"""
    config = load_config()
    selected_category = config.get("selected_category", "all")
    auto_set = config.get("apod_auto_set_wallpaper", True)

    logger.info(f"Daily update check, category: {selected_category}")

    image = fetch_apod(api_key=config.get("api_key"))
    if not image:
        logger.warning("Today APOD fetch failed")
        return False

    cat = categorize_image(image)
    logger.info(f"Today APOD: {cat} - {image.title}")

    metadata = load_metadata()
    metadata["images"][image.date] = image.to_dict()
    save_metadata(metadata)

    if selected_category != "all" and cat != selected_category:
        logger.info(f"Category mismatch: {cat} vs {selected_category}, skip")
        download_image(image)
        return False

    path = download_image(image)
    if not path:
        logger.warning("Image download failed")
        return False

    # 仅当开启「自动设为壁纸」时才真正设置壁纸
    if not auto_set:
        logger.info("APOD auto-set-wallpaper disabled, skip setting wallpaper")
        return True

    style = config.get("wallpaper_style", "fill")
    wp_path = watermark_image(
        path,
        left_text="来源: NASA 每日天文图片 (APOD)",
        right_text=f"拍摄: {image.date} | {image.title}",
        output_key=f"apod_{image.date}",
    )
    if set_wallpaper(
        wp_path, image.date.replace("-", ""), style=style,
        scale=config.get("wp_scale", 1.0),
        offset_x=config.get("wp_offset_x", 0),
        offset_y=config.get("wp_offset_y", 0),
    ):
        config["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_config(config)
        logger.info("Wallpaper updated")
        return True

    return False


def check_and_update_satellite() -> bool:
    """卫星影像更新（RAMMB-Slider 多卫星）"""
    config = load_config()
    sat = config.get("satellite_id", "himawari")
    color = config.get("satellite_color", "natural_color")
    size = config.get("satellite_size", 1080)
    name = GEOSTATIONARY_SATELLITES.get(sat, {}).get("name", sat)
    style = config.get("wallpaper_style", "fill")
    auto_set = config.get("sat_auto_set_wallpaper", True)

    logger.info(f"Satellite update: {sat} ({color}, {size}px)")
    path = fetch_satellite_image(satellite=sat, color=color, target_size=size)
    if not path:
        logger.warning("Satellite image download failed")
        return False

    now = datetime.now()
    # 仅当开启「自动设为壁纸」时才真正设置壁纸
    if not auto_set:
        logger.info("Satellite auto-set-wallpaper disabled, skip setting wallpaper")
        return True

    wp_path = watermark_image(path,
        left_text=f"来源: {name}",
        right_text=f"拍摄时间: {now.strftime('%Y-%m-%d %H:%M')} (UTC+8)",
        output_key=f"sat_{sat}")
    if set_wallpaper(
        wp_path, f"sat_{sat}", style=style,
        scale=config.get("wp_scale", 1.0),
        offset_x=config.get("wp_offset_x", 0),
        offset_y=config.get("wp_offset_y", 0),
    ):
        config["last_sat_update"] = now.strftime("%Y-%m-%d %H:%M:%S")
        save_config(config)
        logger.info(f"Satellite wallpaper updated: {name}")
        return True
    return False


def check_and_update_sdo() -> bool:
    """SDO 太阳图像更新"""
    config = load_config()
    band = config.get("sdo_band", "0304")
    style = config.get("wallpaper_style", "fill")
    auto_set = config.get("sdo_auto_set_wallpaper", True)
    name = __import__("providers.sdo", fromlist=["SDO_BANDS"]).SDO_BANDS.get(band, {}).get("name", band)

    logger.info(f"SDO update: {band}")
    path = fetch_sdo_image(band=band)
    if not path:
        logger.warning("SDO image download failed")
        return False

    now = datetime.now()
    # 仅当开启「自动设为壁纸」时才真正设置壁纸
    if not auto_set:
        logger.info("SDO auto-set-wallpaper disabled, skip setting wallpaper")
        return True

    wp_path = watermark_image(path,
        left_text="来源: NASA SDO 太阳观测",
        right_text=f"波段: {name} | {now.strftime('%Y-%m-%d %H:%M')}",
        output_key=f"sdo_{band}")
    if set_wallpaper(
        wp_path, f"sdo_{band}", style=style,
        scale=config.get("wp_scale", 1.0),
        offset_x=config.get("wp_offset_x", 0),
        offset_y=config.get("wp_offset_y", 0),
    ):
        config["last_sdo_update"] = now.strftime("%Y-%m-%d %H:%M:%S")
        save_config(config)
        logger.info(f"SDO wallpaper updated: {name}")
        return True
    return False


def get_next_refresh_info() -> dict:
    """返回各数据源下次刷新时间与当前开关状态，供前端展示倒计时"""
    config = load_config()
    info = {
        "data_source": config.get("data_source", "apod"),
        "auto_update": config.get("auto_update", True),
        "satellite_auto_refresh": config.get("satellite_auto_refresh", True),
        "sdo_auto_refresh": config.get("sdo_auto_refresh", True),
        "sat_auto_set_wallpaper": config.get("sat_auto_set_wallpaper", True),
        "sdo_auto_set_wallpaper": config.get("sdo_auto_set_wallpaper", True),
        "apod_auto_set_wallpaper": config.get("apod_auto_set_wallpaper", True),
        "satellite_refresh_interval": config.get("satellite_refresh_interval", 10),
        "sdo_refresh_interval": config.get("sdo_refresh_interval", 60),
        "next_refresh": {k: (v.isoformat() if v else None) for k, v in _next_refresh.items()},
        "running": is_scheduler_running(),
    }
    return info


def _scheduler_loop():
    global _next_refresh, _last_refresh_time
    logger.info("Scheduler started")

    while not _stop_event.is_set():
        try:
            config = load_config()
            data_source = config.get("data_source", "apod")
            sat_interval = config.get("satellite_refresh_interval", 10)
            sat_auto = config.get("satellite_auto_refresh", True)
            sdo_interval = config.get("sdo_refresh_interval", 60)
            sdo_auto = config.get("sdo_auto_refresh", True)
            apod_auto = config.get("auto_update", True)

            if data_source == "satellite":
                if not sat_auto:
                    _next_refresh["satellite"] = None
                    _stop_event.wait(15)
                    continue
                if _due("satellite", sat_interval):
                    _last_refresh_time["satellite"] = datetime.now()
                    _next_refresh["satellite"] = _last_refresh_time["satellite"] + timedelta(minutes=sat_interval)
                    logger.info(f"Satellite refresh due, running check (interval={sat_interval}m)")
                    check_and_update_satellite()
                _stop_event.wait(15)

            elif data_source == "sdo":
                if not sdo_auto:
                    _next_refresh["sdo"] = None
                    _stop_event.wait(15)
                    continue
                if _due("sdo", sdo_interval):
                    _last_refresh_time["sdo"] = datetime.now()
                    _next_refresh["sdo"] = _last_refresh_time["sdo"] + timedelta(minutes=sdo_interval)
                    logger.info(f"SDO refresh due, running check (interval={sdo_interval}m)")
                    check_and_update_sdo()
                _stop_event.wait(15)

            else:
                # NASA APOD: 每天检查一次
                if not apod_auto:
                    _next_refresh["apod"] = None
                    _stop_event.wait(15)
                    continue

                today = datetime.now().strftime("%Y-%m-%d")
                last_update = config.get("last_update") or ""
                if last_update.startswith(today):
                    update_time = config.get("update_time", "09:00")
                    tomorrow = datetime.now() + timedelta(days=1)
                    try:
                        next_run = datetime.strptime(
                            f"{tomorrow.strftime('%Y-%m-%d')} {update_time}",
                            "%Y-%m-%d %H:%M"
                        )
                        _next_refresh["apod"] = next_run
                        wait_seconds = (next_run - datetime.now()).total_seconds()
                        wait_seconds = max(60, min(wait_seconds, 86400))
                    except ValueError:
                        _next_refresh["apod"] = datetime.now() + timedelta(hours=1)
                        wait_seconds = 3600

                    logger.info(f"Already updated today, wait {wait_seconds:.0f}s")
                    _stop_event.wait(wait_seconds)
                    continue

                _next_refresh["apod"] = datetime.now() + timedelta(hours=1)
                _last_refresh_time["apod"] = datetime.now()
                check_and_update()
                _stop_event.wait(3600)

        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            _stop_event.wait(1800)

    logger.info("Scheduler stopped")


def start_scheduler():
    global _scheduler_thread, _stop_event

    if _scheduler_thread and _scheduler_thread.is_alive():
        logger.info("Scheduler already running")
        return

    _stop_event = threading.Event()
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()
    logger.info("Scheduler thread started")


def stop_scheduler():
    global _stop_event
    if _stop_event:
        _stop_event.set()
    logger.info("Scheduler stop signal sent")


def is_scheduler_running() -> bool:
    return _scheduler_thread is not None and _scheduler_thread.is_alive()
