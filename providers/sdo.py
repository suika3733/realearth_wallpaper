"""NASA SDO 太阳动力学天文台 - 太阳实时图像

数据来源: https://sdo.gsfc.nasa.gov
支持波段: 0171, 0304, HMIIC 等
"""

import bisect
import logging
from pathlib import Path

from PIL import Image
from io import BytesIO
import requests

from config import IMAGE_CACHE_DIR

logger = logging.getLogger(__name__)

SDO_RESOLUTIONS = [512, 1024, 2048, 4096]

SDO_BANDS = {
    "0304":      {"name": "304 Å (色球层)",      "desc": "极紫外 - 太阳色球层与过渡区"},
    "0171":      {"name": "171 Å (日冕)",        "desc": "极紫外 - 太阳日冕"},
    "0211":      {"name": "211 Å (活动区)",      "desc": "极紫外 - 太阳活动区"},
    "0193":      {"name": "193 Å (日冕高温)",    "desc": "极紫外 - 日冕高温"},
    "0094":      {"name": "94 Å (耀斑)",         "desc": "极紫外 - 太阳耀斑"},
    "0131":      {"name": "131 Å (过渡区)",      "desc": "极紫外 - 过渡区"},
    "0335":      {"name": "335 Å (活动区)",      "desc": "极紫外 - 活动区"},
    "1600":      {"name": "1600 Å (上层光球)",   "desc": "紫外 - 上层光球"},
    "1700":      {"name": "1700 Å (光球)",       "desc": "紫外 - 光球"},
    "HMIIC":     {"name": "连续光球 (可见光)",    "desc": "可见光 - 太阳黑子与表面"},
    "HMIB":      {"name": "磁场图 (磁图)",        "desc": "磁场线 - 太阳磁图"},
}

SDO_CACHE_DIR = IMAGE_CACHE_DIR / "sdo"

# SDO 最新影像缓存有效期（秒）。URL 固定指向 latest_xxx.jpg，无时间戳，
# 为避免缓存旧影像导致「永不更新」，默认 10 分钟内重新下载。
SDO_CACHE_TTL = 600


def _pick_resolution(target: int) -> int:
    """选择不小于 target 的最小可用分辨率"""
    return SDO_RESOLUTIONS[bisect.bisect_left(SDO_RESOLUTIONS, target)]


def _cache_path(band: str, resolution: int) -> Path:
    """SDO 缓存文件路径（固定文件名，配合 TTL 判断是否需要刷新）"""
    return SDO_CACHE_DIR / f"sdo_{band}_{resolution}.jpg"


def get_sdo_capture_time(band: str = "0304", target_size: int = 1024):
    """获取 SDO 最新影像的实际拍摄/更新时间（北京时间）

    通过 HEAD 请求读取响应头 Last-Modified（UTC），换算为 UTC+8。

    Returns:
        datetime（北京时间）或 None
    """
    try:
        from datetime import datetime, timedelta
        resolution = _pick_resolution(target_size)
        url = f"https://sdo.gsfc.nasa.gov/assets/img/latest/latest_{resolution}_{band}.jpg"
        resp = requests.head(url, timeout=15)
        if resp.status_code != 200:
            return None
        lm = resp.headers.get("Last-Modified")
        if not lm:
            return None
        # Last-Modified 是 HTTP 时间格式 (GMT/UTC)
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(lm)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=__import__("datetime").timezone.utc)
        return dt + timedelta(hours=8)  # 转北京时间
    except Exception as e:
        logger.warning(f"Get SDO capture time failed: {e}")
        return None


def fetch_sdo_image(
    band: str = "0304",
    target_size: int = 1024,
    force: bool = False,
) -> str | None:
    """获取 NASA SDO 太阳最新图像

    Args:
        band: 波段 (0304 / 0171 / 0304pfss / 0171pfss / HMIIC)
        target_size: 目标尺寸，自动选择 512/1024/2048/4096 中最接近的
        force: 强制重新下载（忽略缓存有效期）

    Returns:
        图像文件路径，失败返回 None
    """
    if band not in SDO_BANDS:
        logger.error(f"Unknown SDO band: {band}")
        return None

    resolution = _pick_resolution(target_size)

    SDO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _cache_path(band, resolution)

    # 缓存命中判断：文件存在且在有效期内
    if not force and cache_path.exists():
        import time as _t
        age = _t.time() - cache_path.stat().st_mtime
        if age < SDO_CACHE_TTL:
            logger.info(f"Using cached SDO (age={age:.0f}s): {cache_path}")
            return str(cache_path)
        logger.info(f"SDO cache expired (age={age:.0f}s), re-downloading")

    url = f"https://sdo.gsfc.nasa.gov/assets/img/latest/latest_{resolution}_{band}.jpg"
    logger.info(f"Fetching SDO: {url}")

    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content))
        img.save(str(cache_path), "JPEG", quality=94)
        logger.info(f"SDO saved: {cache_path} ({img.width}x{img.height})")
        return str(cache_path)
    except Exception as e:
        logger.error(f"SDO download failed: {e}")
        return None


def test_sdo_connectivity(band: str = None, timeout: int = 10) -> dict:
    """测试 NASA SDO 服务器连通性

    Args:
        band: 指定波段测试，None 则测试全部波段
        timeout: 超时秒数

    Returns:
        {"ok": bool, "results": [{band, name, status_code, ok, latency_ms}]}
    """
    if band is not None:
        bands = [band] if band in SDO_BANDS else []
    else:
        bands = list(SDO_BANDS.keys())

    results = []
    for b in bands:
        url = f"https://sdo.gsfc.nasa.gov/assets/img/latest/latest_1024_{b}.jpg"
        import time as _time
        try:
            start = _time.time()
            resp = requests.get(url, timeout=timeout, stream=True)
            latency = int((_time.time() - start) * 1000)
            # 只取状态码，不下载完整图片（HEAD 可能不被支持，用 stream GET + 关闭）
            resp.close()
            ok = resp.status_code == 200
            results.append({
                "band": b,
                "name": SDO_BANDS[b]["name"],
                "status_code": resp.status_code,
                "ok": ok,
                "latency_ms": latency,
            })
        except Exception as e:
            results.append({
                "band": b,
                "name": SDO_BANDS[b]["name"],
                "status_code": 0,
                "ok": False,
                "latency_ms": None,
                "error": str(e),
            })

    return {"ok": all(r["ok"] for r in results), "results": results}
