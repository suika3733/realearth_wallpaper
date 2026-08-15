"""地球静止卫星图像获取 — 基于 CIRA RAMMB-Slider

数据来源: https://rammb-slider.cira.colostate.edu
支持卫星: GOES-16/18, Himawari-8, GK2A, Meteosat-9/0deg
"""

import datetime
import json
import logging
import socket
import ssl
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image

from config import IMAGE_CACHE_DIR

logger = logging.getLogger(__name__)

# 全局 SSL 上下文（兼容部分 Windows 环境）
try:
    _SSL_CTX = ssl.create_default_context()
except Exception:
    _SSL_CTX = ssl._create_unverified_context()

# 网络请求超时（秒）—— CIRA 位于美国，跨洋访问较慢，适当放宽
_TIMEOUT = 25

# ---------------------------------------------------------------------------
# 卫星元数据
# ---------------------------------------------------------------------------
GEOSTATIONARY_SATELLITES = {
    "goes-19":      {"name": "GOES-19 (美洲)",      "size": 678, "region": "americas"},
    "goes-18":      {"name": "GOES-18 (美洲西)",     "size": 678, "region": "americas"},
    "goes-16":      {"name": "GOES-16 (美洲东)",     "size": 678, "region": "americas", "source": "noaa"},
    "himawari":     {"name": "Himawari-8 (亚太)",   "size": 688, "region": "asia_pacific"},
    "gk2a":         {"name": "GK2A (韩国)",          "size": 688, "region": "asia_pacific"},
    "fy4b":         {"name": "风云四号 FY-4B (中国)", "size": 10992, "region": "asia_pacific", "source": "fy4"},
}

SATELLITE_SIZES = {k: v["size"] for k, v in GEOSTATIONARY_SATELLITES.items()}

COLOR_MODES = {
    "natural_color": "自然色",
    "geocolor":      "地球色 (含夜景)",
}

RAMMB_BASE = "https://rammb-slider.cira.colostate.edu"

# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
SATELLITE_CACHE_DIR = IMAGE_CACHE_DIR / "satellite"

# 卫星缓存有效期（秒）。CIRA 影像约每 10 分钟更新，设为 8 分钟 TTL
# 避免在调度间隔内重复请求时永远命中缓存导致「永不更新」
SATELLITE_CACHE_TTL = 480


def _cache_path(satellite: str, color: str, scale: int, time_code: int) -> Path:
    """构建缓存文件路径"""
    cache_key = f"{satellite}_{color}_{scale}_{time_code}"
    return SATELLITE_CACHE_DIR / f"{cache_key}.jpg"


def _get_time_code(satellite: str, color: str) -> tuple[int, str]:
    """获取最新可用时间戳（带超时和重试）"""
    url = f"{RAMMB_BASE}/data/json/{satellite}/full_disk/{color}/latest_times.json"
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_SSL_CTX) as f:
                data = json.load(f)
            latest = data["timestamps_int"][0]
            date = datetime.datetime.strptime(str(latest), "%Y%m%d%H%M%S").strftime("%Y/%m/%d")
            return latest, date
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            last_err = e
            logger.warning(f"Time code fetch attempt {attempt + 1}/3 failed: {e}")
            time.sleep(1.5)
    raise ConnectionError(
        f"无法连接到 CIRA RAMMB-Slider 服务器 ({last_err}). "
        f"请检查网络连接，该服务可能需要代理/VPN 访问。"
    )


def _calc_scale(satellite: str, target_size: int) -> int:
    """计算缩放级别 (0-4)"""
    base = SATELLITE_SIZES[satellite]
    ratio = target_size / base / 1.2
    scale = int(ratio).bit_length()  # log2 取整
    scale = max(0, min(scale, 4))
    return scale


def _build_url(satellite: str, scale: int, color: str) -> str:
    """构建瓦片基础 URL"""
    time_code, date = _get_time_code(satellite, color)
    return f"{RAMMB_BASE}/data/imagery/{date}/{satellite}---full_disk/{color}/{time_code}/0{scale}", time_code


def _download_tile(url: str) -> Image.Image:
    """下载单个瓦片（带超时和重试）"""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    # 连接/读取超时也重试，CIRA 跨洋网络不稳定
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16))

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "image/png,image/*,*/*",
    }

    try:
        resp = session.get(url, headers=headers, timeout=_TIMEOUT, verify=True)
        resp.raise_for_status()
    except requests.exceptions.ConnectTimeout:
        raise ConnectionError(
            "连接 CIRA 服务器超时。请检查网络，该服务位于美国，可能需要代理/VPN。"
        )
    except requests.exceptions.ConnectionError as e:
        raise ConnectionError(
            f"无法连接到 CIRA 服务器: {e}. 请检查网络连接。"
        )

    from io import BytesIO
    return Image.open(BytesIO(resp.content))


def fetch_satellite_image(
    satellite: str = "himawari",
    color: str = "natural_color",
    target_size: int = 1080,
    force: bool = False,
) -> str | None:
    """获取地球静止卫星合成图像

    Args:
        satellite: 卫星标识 (goes-16/goes-18/himawari/gk2a/meteosat-0deg/meteosat-9)
        color: 颜色模式 (natural_color / geocolor)
        target_size: 目标尺寸（像素），自动计算缩放级别
        force: 强制重新下载，忽略缓存

    Returns:
        图像文件路径，失败返回 None
    """
    if satellite not in SATELLITE_SIZES:
        logger.error(f"Unknown satellite: {satellite}")
        return None

    if color not in COLOR_MODES:
        logger.error(f"Unknown color mode: {color}")
        return None

    # NOAA GOES 系列（goes-16 等）：走 NOAA 官方整图数据源，非 CIRA 瓦片
    if GEOSTATIONARY_SATELLITES[satellite].get("source") == "noaa":
        return _fetch_noaa_goes(satellite, target_size, force)

    # 风云四号 FY-4B：走 NSMC 国家卫星气象中心数据源
    if GEOSTATIONARY_SATELLITES[satellite].get("source") == "fy4":
        from .fy4 import fetch_fy4_image
        return fetch_fy4_image(target_size=target_size, force=force)

    try:
        scale = _calc_scale(satellite, target_size)
        # 仅获取最新时间戳，瓦片 URL 在下载时按实际 scale 构建
        time_code, _ = _get_time_code(satellite, color)
    except ConnectionError:
        raise  # 向上传递网络错误，让 GUI 显示友好提示

    SATELLITE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _cache_path(satellite, color, scale, time_code)

    # 缓存命中判断：文件存在且在有效期内
    if not force and cache_path.exists():
        import time as _t
        age = _t.time() - cache_path.stat().st_mtime
        if age < SATELLITE_CACHE_TTL:
            logger.info(f"Using cached satellite (age={age:.0f}s): {cache_path}")
            return str(cache_path)
        logger.info(f"Satellite cache expired (age={age:.0f}s), re-downloading")

    # 瓦片数量: 2^scale x 2^scale
    tiles_n = 2 ** scale
    tilesize = SATELLITE_SIZES[satellite]

    logger.info(f"Fetching {satellite} ({color}), scale={scale}, {tiles_n}x{tiles_n} tiles")

    # 并行下载所有瓦片；若瓦片缺失（跨洋网络不稳定），自动降级到更低 scale 重试，
    # 确保始终能出图。time_code 复用开头获取到的最新时间戳，避免重复请求。
    for attempt_scale in range(scale, -1, -1):
        s_n = 2 ** attempt_scale
        # 基于相同 time_code 构建该 scale 的瓦片基础 URL
        from datetime import datetime as _dt
        _d = _dt.strptime(str(time_code), "%Y%m%d%H%M%S").strftime("%Y/%m/%d")
        s_base = f"{RAMMB_BASE}/data/imagery/{_d}/{satellite}---full_disk/{color}/{time_code}/0{attempt_scale}"
        tile_map: dict[tuple[int, int], Image.Image] = {}

        def _fetch(row: int, col: int):
            url = f"{s_base}/{str(row).zfill(3)}_{str(col).zfill(3)}.png"
            img = _download_tile(url)
            return (row, col), img

        with ThreadPoolExecutor(max_workers=min(s_n * s_n, 16)) as pool:
            futures = {pool.submit(_fetch, r, c): (r, c)
                       for r in range(s_n) for c in range(s_n)}
            for future in as_completed(futures):
                try:
                    pos, img = future.result()
                    tile_map[pos] = img
                except Exception as e:
                    logger.warning(f"Tile download failed: {e}")

        expected = s_n * s_n
        if len(tile_map) >= expected:
            scale = attempt_scale
            tiles_n = s_n
            tilesize = SATELLITE_SIZES[satellite]
            # 缓存路径随实际 scale 更新
            cache_path = _cache_path(satellite, color, scale, time_code)
            break
        logger.warning(f"Tile download incomplete ({len(tile_map)}/{expected}), "
                       f"degrading to scale {attempt_scale - 1}")

    if not tile_map:
        logger.error("All tile downloads failed")
        raise ConnectionError(
            "所有卫星瓦片下载失败。请检查网络连接，"
            "CIRA RAMMB-Slider 服务位于美国，国内访问可能需要代理/VPN。"
        )

    # 拼接瓦片
    full_w = tilesize * tiles_n
    full_h = tilesize * tiles_n
    canvas = Image.new("RGB", (full_w, full_h))

    for (r, c), img in tile_map.items():
        x = c * tilesize
        y = r * tilesize
        canvas.paste(img, (x, y))

    canvas.save(str(cache_path), "JPEG", quality=94)
    logger.info(f"Saved: {cache_path} ({full_w}x{full_h})")
    return str(cache_path)


# ---------------------------------------------------------------------------
# NOAA GOES 整图数据源（非 CIRA 瓦片）
# ---------------------------------------------------------------------------
def _fetch_noaa_goes(satellite: str, target_size: int, force: bool) -> str | None:
    """通过 NOAA 官方数据源获取 GOES 全盘影像

    NOAA 直接提供整张全盘图（约 10MB），与 CIRA 的瓦片拼接不同。
    使用较长的缓存有效期避免频繁下载大图。

    Args:
        satellite: 卫星标识（goes-16 等，映射到 NOAA URL）
        target_size: 目标短边尺寸
        force: 强制重新下载

    Returns:
        图像文件路径，失败返回 None
    """
    from providers.noaa_goes import fetch_noaa_goes_image

    # 卫星标识映射：goes-16 -> goes16
    noaa_id = satellite.replace("-", "")

    try:
        return fetch_noaa_goes_image(satellite=noaa_id, target_size=target_size, force=force)
    except Exception as e:
        logger.error(f"NOAA GOES fetch failed ({satellite}): {e}")
        raise ConnectionError(
            f"NOAA GOES-16 影像获取失败: {e}。"
            f"NOAA 服务器位于美国，国内访问可能需要代理/VPN。"
        )
