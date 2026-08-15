"""NOAA GOES 静止卫星真彩色全圆盘图像获取（官方数据源）

数据来源: NOAA STAR / NESDIS CDN
URL: https://cdn.star.nesdis.noaa.gov/GOES16/ABI/FD/GEOCOLOR/latest.jpg

与 CIRA RAMMB-Slider（瓦片拼接）不同，NOAA 官方直接提供整张全盘图
（GOES ABI 满分辨率约 5424×5424），使用「latest.jpg」即可获取最新一帧。

注意：
- 单张图约 8~10MB，下载较慢（尤其国内访问美国 CDN），因此使用较长的
  缓存有效期（15 分钟），避免频繁下载。
- 所有下载建议在后台线程执行，避免阻塞界面。
"""

import logging
import time as _time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from PIL import Image
from io import BytesIO

from config import IMAGE_CACHE_DIR

logger = logging.getLogger(__name__)

# NOAA GOES 全盘 GeoColor 影像（latest.jpg 为最新一帧）
GOES_URLS = {
    "goes16": "https://cdn.star.nesdis.noaa.gov/GOES16/ABI/FD/GEOCOLOR/latest.jpg",
    "goes18": "https://cdn.star.nesdis.noaa.gov/GOES18/ABI/FD/GEOCOLOR/latest.jpg",
    "goes19": "https://cdn.star.nesdis.noaa.gov/GOES19/ABI/FD/GEOCOLOR/latest.jpg",
}

# 缓存有效期（秒）。NOAA 图较大（约 10MB），设为 15 分钟避免频繁下载
NOAA_GOES_CACHE_TTL = 900

NOAA_GOES_CACHE_DIR = IMAGE_CACHE_DIR / "noaa_goes"

# 单张图下载超时（秒）—— NOAA 跨洋访问可能较慢，放宽
DOWNLOAD_TIMEOUT = 120


def _cache_path(satellite: str) -> Path:
    """NOAA GOES 缓存文件路径（固定文件名，配合 TTL 判断）"""
    return NOAA_GOES_CACHE_DIR / f"{satellite}_fd_geocolor.jpg"


def get_noaa_goes_capture_time(satellite: str = "goes16", timeout: int = 20) -> datetime | None:
    """获取 NOAA GOES 最新影像的拍摄/更新时间（北京时间）

    通过 HEAD 请求读取 Last-Modified（UTC），换算为 UTC+8。

    Returns:
        datetime（北京时间）或 None
    """
    url = GOES_URLS.get(satellite)
    if not url:
        return None
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
        if resp.status_code != 200:
            return None
        lm = resp.headers.get("Last-Modified")
        if not lm:
            return None
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(lm)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt + timedelta(hours=8)  # 转北京时间
    except Exception as e:
        logger.warning(f"Get NOAA GOES capture time failed ({satellite}): {e}")
        return None


def fetch_noaa_goes_image(
    satellite: str = "goes16",
    target_size: int = 1080,
    force: bool = False,
) -> str | None:
    """获取 NOAA GOES 真彩色全圆盘图像并缩放到目标尺寸

    Args:
        satellite: 卫星标识 (goes16 / goes18 / goes19)
        target_size: 目标短边尺寸（像素），长边按比例缩放
        force: 强制重新下载，忽略缓存有效期

    Returns:
        图像文件路径，失败返回 None
    """
    url = GOES_URLS.get(satellite)
    if not url:
        logger.error(f"Unknown NOAA GOES satellite: {satellite}")
        return None

    NOAA_GOES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _cache_path(satellite)

    # 缓存命中判断：文件存在且在有效期内
    if not force and cache_path.exists():
        age = _time.time() - cache_path.stat().st_mtime
        if age < NOAA_GOES_CACHE_TTL:
            logger.info(f"Using cached NOAA GOES ({satellite}, age={age:.0f}s): {cache_path}")
            return str(cache_path)
        logger.info(f"NOAA GOES cache expired ({satellite}, age={age:.0f}s), re-downloading")

    logger.info(f"Fetching NOAA GOES {satellite}: {url}")

    try:
        # 流式下载，避免一次性加载 10MB 到内存
        resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True)
        resp.raise_for_status()

        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=1024 * 256):
            if chunk:
                chunks.append(chunk)
                total += len(chunk)
        data = b"".join(chunks)
        logger.info(f"NOAA GOES {satellite} downloaded: {total / 1024:.0f} KB")

        img = Image.open(BytesIO(data))
        logger.info(f"NOAA GOES {satellite} original: {img.width}x{img.height} mode={img.mode}")

        # 缩放到目标尺寸（保持比例，短边=target_size）
        ratio = target_size / min(img.width, img.height)
        new_w = int(img.width * ratio)
        new_h = int(img.height * ratio)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        logger.info(f"NOAA GOES {satellite} resized: {new_w}x{new_h}")

        # 保存为 JPEG
        img.save(str(cache_path), "JPEG", quality=94)
        logger.info(f"NOAA GOES {satellite} saved: {cache_path}")
        return str(cache_path)

    except Exception as e:
        logger.error(f"NOAA GOES {satellite} download failed: {e}")
        # 如果缓存存在（即使过期），回退使用旧缓存
        if cache_path.exists():
            logger.warning(f"NOAA GOES fetch failed, falling back to stale cache: {cache_path}")
            return str(cache_path)
        return None


def test_noaa_goes_connectivity(satellite: str = "goes16", timeout: int = 15) -> dict:
    """测试 NOAA GOES 服务器连通性

    Returns:
        {"ok": bool, "latency_ms": int, "status_code": int, "capture_time": str|None}
    """
    url = GOES_URLS.get(satellite, GOES_URLS["goes16"])
    try:
        t0 = _time.time()
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
        latency = int((_time.time() - t0) * 1000)
        ok = resp.status_code == 200
        ct = None
        if ok:
            lm = resp.headers.get("Last-Modified")
            if lm:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(lm) + timedelta(hours=8)
                ct = dt.strftime("%Y-%m-%d %H:%M:%S")
        return {
            "ok": ok,
            "latency_ms": latency,
            "status_code": resp.status_code,
            "capture_time": ct,
            "content_length": resp.headers.get("Content-Length"),
        }
    except Exception as e:
        return {
            "ok": False,
            "latency_ms": None,
            "status_code": 0,
            "capture_time": None,
            "error": str(e),
        }
