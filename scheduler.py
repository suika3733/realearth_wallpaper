"""调度模块 - 支持 NASA APOD + 多卫星 + SDO 自动刷新并设为壁纸"""
import threading
import logging
from datetime import datetime, timedelta

from config import load_config, save_config, load_metadata, save_metadata
from nasa_api import fetch_apod, download_image
from categorizer import categorize_image
from wallpaper import set_wallpaper, watermark_image
from providers import GEOSTATIONARY_SATELLITES, fetch_satellite_image, fetch_sdo_image, fetch_fy4_image, get_fy4_capture_time

logger = logging.getLogger(__name__)

_scheduler_thread = None
_stop_event = threading.Event()

# 调度器轮询间隔（秒）。改为短轮询而非长时间阻塞等待，保证：
# 1) 切换数据源（apod/satellite/sdo/fy4）能在 POLL_INTERVAL 秒内即时生效
# 2) 自动刷新/自动设壁纸开关变更能快速响应
# 旧实现用 _stop_event.wait(3600) 等长时间阻塞，导致切换数据源后调度器要等当前等待结束
# 才能读到新配置，表现为「自动刷新/自动设壁纸全部失效」。
POLL_INTERVAL = 15

# 各数据源下次刷新时间（供前端展示倒计时）
_next_refresh = {
    "apod": None,
    "satellite": None,
    "sdo": None,
    "fy4": None,
}


# 各数据源上次刷新时间（datetime，用于精确判定是否到点）
_last_refresh_time = {
    "apod": None,
    "satellite": None,
    "sdo": None,
    "fy4": None,
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

    # 影像实际拍摄时间：CIRA time_code 为 UTC，这里统一换算为北京时间(UTC+8)
    capture_time = None
    try:
        from providers.geostationary import GEOSTATIONARY_SATELLITES as _SATS, _get_time_code
        _src = _SATS.get(sat, {}).get("source")
        # NOAA GOES 系列走 NOAA 的 Last-Modified 时间
        if _src == "noaa":
            from providers.noaa_goes import get_noaa_goes_capture_time
            capture_time = get_noaa_goes_capture_time(sat.replace("-", ""))
        # 风云四号走 NSMC 的 Last-Modified 时间
        elif _src == "fy4":
            from providers.fy4 import get_fy4_capture_time
            capture_time = get_fy4_capture_time()
        # 其余走 CIRA time_code
        else:
            tc, _ = _get_time_code(sat, color)      # 形如 20260814064000 (UTC)
            capture_time = datetime.strptime(str(tc), "%Y%m%d%H%M%S") + timedelta(hours=8)
    except Exception as e:
        logger.warning(f"Get satellite capture time failed: {e}")

    path = fetch_satellite_image(satellite=sat, color=color, target_size=size, force=True)
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
        right_text=f"拍摄时间: {capture_time.strftime('%Y-%m-%d %H:%M') if capture_time else now.strftime('%Y-%m-%d %H:%M')} (UTC+8)",
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
    # 影像实际拍摄/更新时间（北京时间），用于水印展示
    capture_time = None
    try:
        from providers.sdo import get_sdo_capture_time
        capture_time = get_sdo_capture_time(band=band, target_size=config.get("sdo_size", 1024))
    except Exception as e:
        logger.warning(f"Get SDO capture time failed: {e}")

    path = fetch_sdo_image(band=band, force=True)
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
        right_text=f"波段: {name} | {capture_time.strftime('%Y-%m-%d %H:%M') if capture_time else now.strftime('%Y-%m-%d %H:%M')}",
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


def check_and_update_fy4() -> bool:
    """风云四号影像更新"""
    config = load_config()
    style = config.get("wallpaper_style", "fill")
    auto_set = config.get("fy4_auto_set_wallpaper", True)
    size = config.get("fy4_size", 1080)

    logger.info(f"FY-4 update: target_size={size}")

    # 影像实际更新时间（北京时间），用于水印展示
    capture_time = None
    try:
        capture_time = get_fy4_capture_time()
    except Exception as e:
        logger.warning(f"Get FY-4 capture time failed: {e}")

    path = fetch_fy4_image(target_size=size, force=True)
    if not path:
        logger.warning("FY-4 image download failed")
        return False

    now = datetime.now()
    # 仅当开启「自动设为壁纸」时才真正设置壁纸
    if not auto_set:
        logger.info("FY-4 auto-set-wallpaper disabled, skip setting wallpaper")
        return True

    wp_path = watermark_image(path,
        left_text="来源: 风云四号 FY-4B (NSMC)",
        right_text=f"更新时间: {capture_time.strftime('%Y-%m-%d %H:%M') if capture_time else now.strftime('%Y-%m-%d %H:%M')} (UTC+8)",
        output_key=f"fy4")
    if set_wallpaper(
        wp_path, f"fy4", style=style,
        scale=config.get("wp_scale", 1.0),
        offset_x=config.get("wp_offset_x", 0),
        offset_y=config.get("wp_offset_y", 0),
    ):
        config["last_fy4_update"] = now.strftime("%Y-%m-%d %H:%M:%S")
        save_config(config)
        logger.info(f"FY-4 wallpaper updated")
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
        "fy4_auto_refresh": config.get("fy4_auto_refresh", True),
        "sat_auto_set_wallpaper": config.get("sat_auto_set_wallpaper", True),
        "sdo_auto_set_wallpaper": config.get("sdo_auto_set_wallpaper", True),
        "fy4_auto_set_wallpaper": config.get("fy4_auto_set_wallpaper", True),
        "satellite_refresh_interval": config.get("satellite_refresh_interval", 10),
        "sdo_refresh_interval": config.get("sdo_refresh_interval", 60),
        "fy4_refresh_interval": config.get("fy4_refresh_interval", 15),
        "next_refresh": {k: (v.isoformat() if v else None) for k, v in _next_refresh.items()},
        "running": is_scheduler_running(),
    }
    return info


def _interruptible_wait(seconds: float, step: float = 3.0):
    """可中断的等待：以 step 秒为粒度分段等待，避免长时间阻塞导致配置变更无法实时生效。

    旧实现直接调用 _stop_event.wait(3600) 等长时间阻塞，调度器在等待期间不会重新读取
    配置，导致用户切换数据源或开关后，需要等到当前等待结束才能生效（APOD 分支最长可达 1 天）。
    改为分段等待后，每次循环开头都会重新 load_config()，配置变更在 POLL_INTERVAL 秒内生效。
    """
    remaining = seconds
    while remaining > 0 and not _stop_event.is_set():
        s = min(step, remaining)
        _stop_event.wait(s)
        remaining -= s


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
                elif _due("satellite", sat_interval):
                    _last_refresh_time["satellite"] = datetime.now()
                    _next_refresh["satellite"] = _last_refresh_time["satellite"] + timedelta(minutes=sat_interval)
                    logger.info(f"Satellite refresh due, running check (interval={sat_interval}m)")
                    check_and_update_satellite()
                else:
                    base = _last_refresh_time["satellite"] or datetime.now()
                    _next_refresh["satellite"] = base + timedelta(minutes=sat_interval)

            elif data_source == "sdo":
                if not sdo_auto:
                    _next_refresh["sdo"] = None
                elif _due("sdo", sdo_interval):
                    _last_refresh_time["sdo"] = datetime.now()
                    _next_refresh["sdo"] = _last_refresh_time["sdo"] + timedelta(minutes=sdo_interval)
                    logger.info(f"SDO refresh due, running check (interval={sdo_interval}m)")
                    check_and_update_sdo()
                else:
                    base = _last_refresh_time["sdo"] or datetime.now()
                    _next_refresh["sdo"] = base + timedelta(minutes=sdo_interval)

            elif data_source == "fy4":
                fy4_auto = config.get("fy4_auto_refresh", True)
                fy4_interval = config.get("fy4_refresh_interval", 15)
                if not fy4_auto:
                    _next_refresh["fy4"] = None
                elif _due("fy4", fy4_interval):
                    _last_refresh_time["fy4"] = datetime.now()
                    _next_refresh["fy4"] = _last_refresh_time["fy4"] + timedelta(minutes=fy4_interval)
                    logger.info(f"FY-4 refresh due, running check (interval={fy4_interval}m)")
                    check_and_update_fy4()
                else:
                    base = _last_refresh_time["fy4"] or datetime.now()
                    _next_refresh["fy4"] = base + timedelta(minutes=fy4_interval)

            else:
                # NASA APOD：每日在 update_time 之后检查一次
                if not apod_auto:
                    _next_refresh["apod"] = None
                else:
                    today = datetime.now().strftime("%Y-%m-%d")
                    update_time = config.get("update_time", "09:00")
                    now = datetime.now()
                    try:
                        update_dt = datetime.strptime(f"{today} {update_time}", "%Y-%m-%d %H:%M")
                    except ValueError:
                        update_dt = now
                    past_update_time = now >= update_dt
                    already = (config.get("last_update") or "").startswith(today)

                    if already or not past_update_time:
                        # 今天已更新，或还没到今日更新时间 -> 暂不刷新
                        _next_refresh["apod"] = update_dt if not already else (update_dt + timedelta(days=1))
                    else:
                        # 今天未更新且已过更新时间 -> 立即刷新
                        _last_refresh_time["apod"] = now
                        _next_refresh["apod"] = now + timedelta(hours=1)
                        logger.info("APOD daily update due, running check")
                        check_and_update()

            # 关键修复：不再长时间阻塞，改为短轮询，使数据源/开关变更在 POLL_INTERVAL 秒内即时生效
            _interruptible_wait(POLL_INTERVAL)

        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            _interruptible_wait(30)

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
