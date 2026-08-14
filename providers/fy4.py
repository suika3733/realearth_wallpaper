"""中国风云四号（FY-4B）卫星真彩色全圆盘图像获取

数据来源: 国家卫星气象中心 (NSMC)
URL: https://img.nsmc.org.cn/CLOUDIMAGE/FY4B/AGRI/GCLR/FY4B_DISK_GCLR.JPG

FY-4B 是风云四号系列最新业务星（2021 年发射，定点 140°E），
提供每 15 分钟更新的全圆盘真彩色合成图像，分辨率 10992×11912。
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

# FY-4B 真彩色全圆盘图像 URL
FY4B_GCLR_URL = "https://img.nsmc.org.cn/CLOUDIMAGE/FY4B/AGRI/GCLR/FY4B_DISK_GCLR.JPG"

# 缓存有效期（秒）。NSMC 每 15 分钟更新，设为 10 分钟 TTL
FY4_CACHE_TTL = 600

FY4_CACHE_DIR = IMAGE_CACHE_DIR / "fy4"


def _cache_path() -> Path:
    """FY-4 缓存文件路径（固定文件名，配合 TTL 判断）"""
    return FY4_CACHE_DIR / "fy4b_disk_gclr.jpg"


def get_fy4_capture_time(timeout: int = 15) -> datetime | None:
    """获取 FY-4B 最新影像的实际拍摄/更新时间（北京时间）

    通过 HEAD 请求读取响应头 Last-Modified（UTC），换算为 UTC+8。

    Returns:
        datetime（北京时间）或 None
    """
    try:
        resp = requests.head(FY4B_GCLR_URL, timeout=timeout, verify=False)
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
        logger.warning(f"Get FY-4 capture time failed: {e}")
        return None


def fetch_fy4_image(
    target_size: int = 1080,
    force: bool = False,
) -> str | None:
    """获取 FY-4B 真彩色全圆盘图像并缩放到目标尺寸

    Args:
        target_size: 目标短边尺寸（像素），长边按比例缩放
        force: 强制重新下载，忽略缓存有效期

    Returns:
        图像文件路径，失败返回 None
    """
    FY4_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _cache_path()

    # 缓存命中判断：文件存在且在有效期内
    if not force and cache_path.exists():
        age = _time.time() - cache_path.stat().st_mtime
        if age < FY4_CACHE_TTL:
            logger.info(f"Using cached FY-4 (age={age:.0f}s): {cache_path}")
            return str(cache_path)
        logger.info(f"FY-4 cache expired (age={age:.0f}s), re-downloading")

    logger.info(f"Fetching FY-4B: {FY4B_GCLR_URL}")

    try:
        # 下载原图（约 9MB）
        resp = requests.get(FY4B_GCLR_URL, timeout=60, verify=False)
        resp.raise_for_status()

        img = Image.open(BytesIO(resp.content))
        logger.info(f"FY-4B original: {img.width}x{img.height} mode={img.mode}")

        # 缩放到目标尺寸（保持比例，短边=target_size）
        # 原图 10992×11912，短边是 10992
        ratio = target_size / min(img.width, img.height)
        new_w = int(img.width * ratio)
        new_h = int(img.height * ratio)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        logger.info(f"FY-4B resized: {new_w}x{new_h}")

        # 保存为 JPEG
        img.save(str(cache_path), "JPEG", quality=94)
        logger.info(f"FY-4B saved: {cache_path}")
        return str(cache_path)

    except Exception as e:
        logger.error(f"FY-4B download failed: {e}")
        # 如果缓存存在（即使过期），回退使用旧缓存
        if cache_path.exists():
            logger.warning(f"FY-4B fetch failed, falling back to stale cache: {cache_path}")
            return str(cache_path)
        return None


def test_fy4_connectivity(timeout: int = 10) -> dict:
    """测试 NSMC 风云四号服务器连通性

    Returns:
        {"ok": bool, "latency_ms": int, "status_code": int, "capture_time": str|None}
    """
    try:
        t0 = _time.time()
        resp = requests.head(FY4B_GCLR_URL, timeout=timeout, verify=False)
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
        }
    except Exception as e:
        return {
            "ok": False,
            "latency_ms": None,
            "status_code": 0,
            "capture_time": None,
            "error": str(e),
        }
