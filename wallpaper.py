"""Windows 壁纸设置 - 支持壁纸样式控制和图片水印"""
import ctypes
import logging
import time
import uuid
import shutil
import winreg
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# 水印缓存目录
_WATERMARK_DIR = Path.home() / ".nasa_wallpaper" / "watermarked"
_WATERMARK_DIR.mkdir(parents=True, exist_ok=True)

# 水印字体查找（按优先级）
_WATERMARK_FONTS = [
    "C:/Windows/Fonts/msyh.ttc",     # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",   # 黑体
    "C:/Windows/Fonts/arial.ttf",    # Arial
    "Microsoft YaHei",
    "SimHei",
    "Arial",
]

# 可选字体（用户可编辑）
_FONT_FAMILIES = {
    "msyh": "C:/Windows/Fonts/msyh.ttc",
    "simhei": "C:/Windows/Fonts/simhei.ttf",
    "simsun": "C:/Windows/Fonts/simsun.ttc",
    "arial": "C:/Windows/Fonts/arial.ttf",
}

# 水印位置：四角
_POSITIONS = ("top_right", "top_left", "bottom_right", "bottom_left")


def _get_watermark_font(size: int) -> ImageFont.FreeTypeFont:
    """获取可用的中文字体"""
    for name in _WATERMARK_FONTS:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _get_font(family: str, size: int) -> ImageFont.FreeTypeFont:
    """按字体名获取字体，失败则回退到默认字体"""
    path = _FONT_FAMILIES.get(family)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            pass
    return _get_watermark_font(size)


def watermark_image(
    image_path: str,
    left_text: str,
    right_text: str | None = None,
    output_key: str | None = None,
    font_size: int | None = None,
    font_family: str = "msyh",
    position: str = "top_right",
    show_sys_time: bool = False,
) -> str:
    """在图片角落添加半透明水印标注（角标风格），保存到缓存目录

    Args:
        image_path: 原始图片路径
        left_text: 第一行文字（来源信息，如 "NASA 每日天文图片"）
        right_text: 第二行文字（时间信息，如 "2026-08-11 | M31 仙女座星系"）
        output_key: 输出文件名键（不含扩展名）
        font_size: 字体大小（像素），None 则按图片尺寸自适应
        font_family: 字体（msyh/simhei/simsun/arial）
        position: 位置（top_right/top_left/bottom_right/bottom_left）
        show_sys_time: 是否追加显示当前系统时间（默认 False，不显示系统时间）

    Returns:
        带水印的图片路径
    """
    # 使用带时间戳的唯一文件名，避免覆盖已存在文件触发沙箱 safe-delete 拦截
    ts = int(time.time())
    uid = uuid.uuid4().hex[:6]
    output_path = _WATERMARK_DIR / f"{output_key or Path(image_path).stem}_{ts}_{uid}.jpg"

    try:
        img = Image.open(image_path).convert("RGBA")
        iw, ih = img.size

        # 字体大小：用户指定或自适应
        if font_size and font_size > 0:
            base_size = int(font_size)
        else:
            base_size = max(14, min(28, int(min(iw, ih) * 0.022)))
        font_main = _get_font(font_family, base_size)
        font_sub = _get_font(font_family, max(10, base_size - 4))

        # 文字尺寸测量
        draw_tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        lines = [(left_text, font_main)]
        if right_text:
            lines.append((right_text, font_sub))

        text_w = 0
        text_h = 0
        for text, font in lines:
            bbox = draw_tmp.textbbox((0, 0), text, font=font)
            text_w = max(text_w, bbox[2] - bbox[0])
            text_h += (bbox[3] - bbox[1]) + 3  # 行间距 3px

        # 角标尺寸和位置
        padding_x = int(iw * 0.025)
        padding_y = int(ih * 0.015)
        badge_w = text_w + padding_x * 2
        badge_h = text_h + padding_y * 2

        if position == "top_left":
            badge_x = padding_x
            badge_y = padding_y
        elif position == "bottom_right":
            badge_x = iw - badge_w - padding_x
            badge_y = ih - badge_h - padding_y
        elif position == "bottom_left":
            badge_x = padding_x
            badge_y = ih - badge_h - padding_y
        else:  # top_right（默认）
            badge_x = iw - badge_w - padding_x
            badge_y = padding_y

        # 创建半透明覆盖层
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # 圆角矩形背景：半透明深色
        radius = 10
        bg_alpha = 140
        draw.rounded_rectangle(
            [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
            radius=radius,
            fill=(8, 12, 22, bg_alpha),
        )

        # 细边框
        draw.rounded_rectangle(
            [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
            radius=radius,
            outline=(100, 160, 220, 80),
            width=1,
        )

        # 逐行绘制文字
        text_x = badge_x + padding_x
        text_y = badge_y + padding_y
        for idx, (text, font) in enumerate(lines):
            draw.text(
                (text_x, text_y),
                text,
                fill=(220, 230, 250, 230) if idx == 0 else (140, 165, 200, 200),
                font=font,
            )
            bbox = draw_tmp.textbbox((0, 0), text, font=font)
            text_y += (bbox[3] - bbox[1]) + 3

        # 合成并保存
        result = Image.alpha_composite(img, overlay).convert("RGB")
        result.save(str(output_path), "JPEG", quality=92)

        logger.info(f"Watermarked: {output_path}")
        return str(output_path)
    except Exception as e:
        logger.error(f"Watermark failed: {e}")
        return image_path  # 降级：返回原图

SPI_SETDESKWALLPAPER = 20
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02

# 壁纸样式注册表值映射
STYLE_REGISTRY = {
    "center":  {"WallpaperStyle": "0", "TileWallpaper": "0"},
    "tile":    {"WallpaperStyle": "0", "TileWallpaper": "1"},
    "stretch": {"WallpaperStyle": "2", "TileWallpaper": "0"},
    "fit":     {"WallpaperStyle": "6", "TileWallpaper": "0"},
    "fill":    {"WallpaperStyle": "10", "TileWallpaper": "0"},
}


def _get_screen_size() -> tuple:
    """获取主显示器分辨率（宽, 高）。失败时返回默认 1920x1080。"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        w = user32.GetSystemMetrics(0)   # SM_CXSCREEN
        h = user32.GetSystemMetrics(1)   # SM_CYSCREEN
        if w > 0 and h > 0:
            return (int(w), int(h))
    except Exception as e:
        logger.warning(f"Get screen size failed: {e}")
    return (1920, 1080)


def prepare_image_with_position(
    image_path: str,
    scale: float = 1.0,
    offset_x: int = 0,
    offset_y: int = 0,
    output_key: str = "pos",
) -> str:
    """按用户设置的缩放比例与偏移量预处理壁纸图片

    将图片缩放到指定比例后，贴到与屏幕分辨率同尺寸的空白画布上，
    通过偏移控制图片在桌面上的位置（配合 style='center' 使用）。

    Args:
        image_path: 原始图片路径
        scale: 缩放比例（0.5~2.0），1.0 表示原图比例
        offset_x: 水平偏移（像素，正值向右）
        offset_y: 垂直偏移（像素，正值向下）
        output_key: 输出文件名键

    Returns:
        预处理后的图片路径；若无需调整则返回原图路径
    """
    try:
        if abs(scale - 1.0) < 0.001 and offset_x == 0 and offset_y == 0:
            # 无任何调整需求，直接返回原图
            return image_path

        screen_w, screen_h = _get_screen_size()
        img = Image.open(image_path).convert("RGB")
        iw, ih = img.size

        # 按比例缩放
        new_w = max(1, int(iw * scale))
        new_h = max(1, int(ih * scale))
        img = img.resize((new_w, new_h), Image.LANCZOS)

        # 画布尺寸取屏幕分辨率（不小于图片）
        canvas_w = max(screen_w, new_w)
        canvas_h = max(screen_h, new_h)

        # 计算贴图位置（默认居中，再叠加偏移）
        pos_x = (canvas_w - new_w) // 2 + offset_x
        pos_y = (canvas_h - new_h) // 2 + offset_y

        canvas = Image.new("RGB", (canvas_w, canvas_h), (0, 0, 0))
        canvas.paste(img, (pos_x, pos_y))

        # 保存（带时间戳唯一文件名，避免覆盖冲突）
        ts = int(time.time())
        uid = uuid.uuid4().hex[:6]
        output_path = _WATERMARK_DIR / f"{output_key}_{ts}_{uid}.jpg"
        canvas.save(str(output_path), "JPEG", quality=92)
        logger.info(
            f"Position prepared: scale={scale} offset=({offset_x},{offset_y}) "
            f"screen={screen_w}x{screen_h} -> {output_path}"
        )
        return str(output_path)
    except Exception as e:
        logger.error(f"Prepare image position failed: {e}")
        return image_path


def set_wallpaper_style(style: str = "fill"):
    """通过注册表设置壁纸样式

    参考 LiveEarth 项目实现，与 set_wallpaper 配合使用。
    样式值：
    - center: 居中
    - tile: 平铺
    - stretch: 拉伸
    - fit: 适应（保持比例缩放到全屏）
    - fill: 填充（保持比例裁剪到全屏）
    """
    reg_values = STYLE_REGISTRY.get(style, STYLE_REGISTRY["fill"])
    try:
        import win32api, win32con
        import win32gui  # noqa: F401

        k = win32api.RegOpenKeyEx(
            win32con.HKEY_CURRENT_USER,
            "Control Panel\\Desktop",
            0, win32con.KEY_SET_VALUE
        )
        win32api.RegSetValueEx(k, "WallpaperStyle", 0, win32con.REG_SZ,
                               reg_values["WallpaperStyle"])
        win32api.RegSetValueEx(k, "TileWallpaper", 0, win32con.REG_SZ,
                               reg_values["TileWallpaper"])
        win32api.RegCloseKey(k)
        logger.info(f"Wallpaper style set: {style}")
    except ImportError:
        # 后备方案：用 ctypes 操作注册表
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Control Panel\Desktop",
                             0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "WallpaperStyle", 0, winreg.REG_SZ,
                          reg_values["WallpaperStyle"])
        winreg.SetValueEx(key, "TileWallpaper", 0, winreg.REG_SZ,
                          reg_values["TileWallpaper"])
        winreg.CloseKey(key)
    except Exception as e:
        logger.warning(f"Set wallpaper style failed (benign): {e}")


def set_wallpaper(
    image_path: str,
    date_str: str = None,
    style: str = "fill",
    scale: float = 1.0,
    offset_x: int = 0,
    offset_y: int = 0,
) -> bool:
    """设置桌面壁纸

    Args:
        image_path: 图片文件路径
        date_str: 日期标识（可选，用于缓存命名）
        style: 壁纸样式: center | tile | stretch | fit | fill
        scale: 图片缩放比例（0.5~2.0），仅在 style='center' 时生效
        offset_x: 水平偏移（像素），仅在 style='center' 时生效
        offset_y: 垂直偏移（像素），仅在 style='center' 时生效

    Returns:
        是否设置成功
    """
    if not image_path:
        return False

    try:
        if date_str:
            from config import get_wallpaper_path
            wp_path = get_wallpaper_path(date_str)
            if wp_path.exists():
                # 避免覆盖触发沙箱 safe-delete
                wp_path = wp_path.parent / f"{wp_path.stem}_{int(time.time())}.jpg"
            shutil.copy2(image_path, wp_path)
            image_path = str(wp_path)

        # 位置 / 缩放预处理：仅当样式为 center 且用户设置了非默认值时生效
        if style == "center" and (abs(scale - 1.0) > 0.001 or offset_x or offset_y):
            image_path = prepare_image_with_position(
                image_path, scale=scale, offset_x=offset_x, offset_y=offset_y,
                output_key="wp_pos",
            )

        # 先设置壁纸样式
        set_wallpaper_style(style)

        # 再设置壁纸图片
        abs_path = str(image_path)
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETDESKWALLPAPER, 0, abs_path,
            SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
        )
        logger.info(f"Wallpaper set (style={style}): {abs_path}")
        return True
    except Exception as e:
        logger.error(f"Set wallpaper failed: {e}")
        return False
