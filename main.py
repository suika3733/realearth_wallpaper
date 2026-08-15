"""Live Earth Wallpaper - 多数据源卫星壁纸软件"""
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import threading
import logging
from datetime import datetime, timedelta
from pathlib import Path
from PIL import Image, ImageTk
import pystray

from config import (
    load_config, save_config, load_metadata, save_metadata,
    IMAGE_CACHE_DIR, DEFAULT_API_KEY, CATEGORIES, ALL_CATEGORY,
    WALLPAPER_STYLES,
)
from nasa_api import fetch_apod_range, download_image, ApodImage
from categorizer import categorize_image, get_category_name, get_all_category_keys
from wallpaper import set_wallpaper, watermark_image
from scheduler import start_scheduler, stop_scheduler, is_scheduler_running, check_and_update
from providers import GEOSTATIONARY_SATELLITES, SDO_BANDS, fetch_satellite_image, fetch_sdo_image, fetch_fy4_image, get_fy4_capture_time
from autostart import is_autostart_enabled, set_autostart

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ========== 颜色主题 ==========
BG_MAIN = "#0f0f1a"
BG_CARD = "#1a1a2e"
BG_INPUT = "#16213e"
FG_TEXT = "#e0e0e0"
FG_DIM = "#a0a0b0"
ACCENT = "#e94560"
ACCENT_HOVER = "#ff6b81"
ACCENT2 = "#0f3460"
BORDER = "#2a2a40"
GREEN = "#4ecca3"
YELLOW = "#f9d423"
BLUE = "#2d8cf0"
BLUE_HOVER = "#4aa3f7"
EARTH_ACCENT = "#00b4d8"
SDO_ACCENT = "#ff8c00"
SAT_ACCENT = "#00b4d8"

FONT_FAMILY = ("Microsoft YaHei", "微软雅黑", "PingFang SC", "Arial")
FONT_TITLE = (FONT_FAMILY[0], 14, "bold")
FONT_BODY = (FONT_FAMILY[0], 11)
FONT_SMALL = (FONT_FAMILY[0], 9)
FONT_BIG = (FONT_FAMILY[0], 16, "bold")


# ========== 圆角按钮 ==========
class ModernButton(tk.Canvas):
    def __init__(self, parent, text, command=None, width=100, height=32,
                 bg=ACCENT, fg="white", hover_bg=ACCENT_HOVER, font=FONT_BODY, **kw):
        super().__init__(parent, width=width, height=height, bg=BG_CARD,
                         highlightthickness=0, cursor="hand2", **kw)
        self._text = text
        self._command = command
        self._bg = bg
        self._fg = fg
        self._hover_bg = hover_bg
        self._font = font
        self._radius = 6
        self._draw(self._bg)
        self.bind("<Enter>", lambda e: self._draw(self._hover_bg))
        self.bind("<Leave>", lambda e: self._draw(self._bg))
        self.bind("<Button-1>", self._on_click)

    def _draw(self, color):
        self.delete("all")
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        self._round_rect(0, 0, w, h, self._radius, fill=color, outline="")
        self.create_text(w // 2, h // 2, text=self._text, fill=self._fg, font=self._font)

    def _on_click(self, event):
        if self._command:
            self._command()

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
               x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.create_polygon(pts, smooth=True, **kw)

    def set_text(self, text):
        self._text = text
        self._draw(self._bg)


# ========== 主应用 ==========
class NASAApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Live Earth Wallpaper - 卫星壁纸")
        self.root.geometry("1300x850")
        self.root.configure(bg=BG_MAIN)
        self.root.minsize(1100, 700)

        self.config = load_config()
        self.metadata = load_metadata()
        self.data_source = self.config.get("data_source", "apod")
        self.sat_image_path = None
        self.sat_auto_refresh = self.config.get("satellite_auto_refresh", True)
        self.sat_refresh_interval = self.config.get("satellite_refresh_interval", 10)
        self._sat_timer_id = None
        self._sat_next_refresh = None

        self.sdo_image_path = None
        self.sdo_auto_refresh = self.config.get("sdo_auto_refresh", True)
        self.sdo_refresh_interval = self.config.get("sdo_refresh_interval", 60)
        self._sdo_timer_id = None
        self._sdo_next_refresh = None

        self.fy4_image_path = None
        self.fy4_auto_refresh = self.config.get("fy4_auto_refresh", True)
        self.fy4_refresh_interval = self.config.get("fy4_refresh_interval", 15)
        self._fy4_timer_id = None
        self._fy4_next_refresh = None

        # satellite panel vars
        self.selected_satellite = tk.StringVar(value=self.config.get("satellite_id", "himawari"))
        self.selected_color = tk.StringVar(value=self.config.get("satellite_color", "natural_color"))
        self.selected_sdo_band = tk.StringVar(value=self.config.get("sdo_band", "0304"))

        # APOD 数据
        self.images_by_cat = {key: [] for key in get_all_category_keys()}
        self.current_cat = self.config.get("selected_category", ALL_CATEGORY)
        self.current_idx = 0
        self.current_image = None
        self.photo_ref = None

        # 拦截关闭按钮
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 系统托盘
        self.tray_icon = None

        self._build_ui()
        self._rebuild_category_data()
        self._refresh_ui()
        self._switch_panel(self.data_source)

        start_scheduler()
        self._check_auto_startup()

    # ========== UI 构建 ==========
    def _build_ui(self):
        # ---- 顶部标题栏 ----
        header = tk.Frame(self.root, bg=BG_CARD, height=48)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        tk.Label(header, text="Live Earth Wallpaper", bg=BG_CARD, fg="white",
                 font=(FONT_FAMILY[0], 16, "bold")).pack(side="left", padx=18, pady=8)

        # 数据源切换标签
        ds_frame = tk.Frame(header, bg=BG_CARD)
        ds_frame.pack(side="left", padx=(20, 0))

        self.btn_apod = ModernButton(ds_frame, text="🔭 天文图片",
                                     command=lambda: self._switch_panel("apod"),
                                     width=90, height=30, bg=ACCENT2, hover_bg="#1a4a7a",
                                     font=(FONT_FAMILY[0], 9))
        self.btn_apod.pack(side="left", padx=2)

        self.btn_sat = ModernButton(ds_frame, text="🛰 卫星影像",
                                    command=lambda: self._switch_panel("satellite"),
                                    width=90, height=30, bg="#1a3a4a",
                                    hover_bg="#0f3460", font=(FONT_FAMILY[0], 9))
        self.btn_sat.pack(side="left", padx=2)

        self.btn_sdo = ModernButton(ds_frame, text="☀ 太阳观测",
                                    command=lambda: self._switch_panel("sdo"),
                                    width=90, height=30, bg="#1a3a4a",
                                    hover_bg="#0f3460", font=(FONT_FAMILY[0], 9))
        self.btn_sdo.pack(side="left", padx=2)

        self.btn_fy4 = ModernButton(ds_frame, text="🌏 风云四号",
                                    command=lambda: self._switch_panel("fy4"),
                                    width=90, height=30, bg="#1a3a4a",
                                    hover_bg="#0f3460", font=(FONT_FAMILY[0], 9))
        self.btn_fy4.pack(side="left", padx=2)

        # 右上角按钮
        menu_frame = tk.Frame(header, bg=BG_CARD)
        menu_frame.pack(side="right", padx=8)

        ModernButton(menu_frame, text="📖 说明", command=self._show_help,
                     width=60, height=28, bg=ACCENT2, hover_bg="#1a4a7a",
                     font=(FONT_FAMILY[0], 9)).pack(side="left", padx=3)
        ModernButton(menu_frame, text="⚙ 设置", command=self._show_settings,
                     width=60, height=28, bg=ACCENT2, hover_bg="#1a4a7a",
                     font=(FONT_FAMILY[0], 9)).pack(side="left", padx=3)

        # ---- 主体区域 ----
        body = tk.Frame(self.root, bg=BG_MAIN)
        body.pack(fill="both", expand=True, padx=15, pady=10)

        # === APOD 面板 ===
        self.panel_apod = tk.Frame(body, bg=BG_MAIN)

        # 左侧分类列表
        left = tk.Frame(self.panel_apod, bg=BG_MAIN, width=150)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)

        tk.Label(left, text="📂 图片分类", bg=BG_MAIN, fg=FG_TEXT,
                 font=FONT_TITLE).pack(anchor="w", pady=(0, 8))

        cat_frame = tk.Frame(left, bg=BG_INPUT, highlightbackground=BORDER, highlightthickness=1)
        cat_frame.pack(fill="both", expand=True)

        cols = ("category", "count")
        self.cat_tree = ttk.Treeview(cat_frame, columns=cols, show="headings", height=20)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=BG_INPUT, foreground=FG_TEXT,
                        fieldbackground=BG_INPUT, rowheight=28, font=FONT_BODY)
        style.configure("Treeview.Heading", background=BG_CARD, foreground=FG_TEXT,
                        font=(FONT_FAMILY[0], 10, "bold"))
        style.map("Treeview", background=[("selected", ACCENT2)],
                  foreground=[("selected", "white")])
        style.configure("Vertical.TScrollbar", background=BG_CARD,
                        troughcolor=BG_MAIN, arrowcolor=FG_DIM)

        self.cat_tree.heading("category", text="分类")
        self.cat_tree.heading("count", text="数量")
        self.cat_tree.column("category", width=140, anchor="w")
        self.cat_tree.column("count", width=50, anchor="center")
        self.cat_tree.pack(side="left", fill="both", expand=True)

        cat_scroll = ttk.Scrollbar(cat_frame, orient="vertical", command=self.cat_tree.yview)
        cat_scroll.pack(side="right", fill="y")
        self.cat_tree.configure(yscrollcommand=cat_scroll.set)
        self.cat_tree.bind("<<TreeviewSelect>>", self._on_cat_select)

        # 右侧预览区
        right = tk.Frame(self.panel_apod, bg=BG_MAIN)
        right.pack(side="left", fill="both", expand=True)

        preview = tk.Frame(right, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1)
        preview.pack(fill="both", expand=True, pady=(0, 10))

        self.apod_preview = tk.Label(preview, bg=BG_CARD, text="📷 暂无图片", fg=FG_DIM,
                                     font=(FONT_FAMILY[0], 14))
        self.apod_preview.pack(fill="both", expand=True)

        self.apod_info = tk.Label(preview, bg=BG_CARD, fg=FG_DIM, font=FONT_SMALL,
                                  justify="left", wraplength=700)
        self.apod_info.place(relx=0.02, rely=0.97, anchor="sw")

        # 底部控制栏
        ctrl = tk.Frame(right, bg=BG_MAIN, height=45)
        ctrl.pack(fill="x", side="bottom")
        ctrl.pack_propagate(False)

        nav = tk.Frame(ctrl, bg=BG_MAIN)
        nav.pack(side="left")
        self.btn_prev = ModernButton(nav, text="◀", command=self._prev_image,
                                     width=40, height=32, bg=ACCENT2, hover_bg="#1a4a7a")
        self.btn_prev.pack(side="left", padx=2)
        self.page_label = tk.Label(nav, text="0 / 0", bg=BG_MAIN, fg=FG_TEXT,
                                   font=FONT_BODY, width=10)
        self.page_label.pack(side="left", padx=10)
        self.btn_next = ModernButton(nav, text="▶", command=self._next_image,
                                     width=40, height=32, bg=ACCENT2, hover_bg="#1a4a7a")
        self.btn_next.pack(side="left", padx=2)

        self.btn_fetch = ModernButton(ctrl, text="📥 获取历史", command=self._fetch_history,
                                      width=100, height=32, bg=BLUE, hover_bg=BLUE_HOVER)
        self.btn_fetch.pack(side="left", padx=(30, 2))

        rc = tk.Frame(ctrl, bg=BG_MAIN)
        rc.pack(side="right")
        self.btn_update = ModernButton(rc, text="🔄 更新", command=self._update_now,
                                       width=80, height=32, bg=GREEN, hover_bg="#6ee7c5",
                                       fg="#0f0f1a")
        self.btn_update.pack(side="left", padx=2)
        self.btn_wallpaper = ModernButton(rc, text="🖼 设为壁纸", command=self._set_wallpaper,
                                          width=100, height=32, bg=ACCENT, hover_bg=ACCENT_HOVER)
        self.btn_wallpaper.pack(side="left", padx=2)

        # === 卫星影像面板 ===
        self.panel_sat = tk.Frame(body, bg=BG_MAIN)

        sat_left = tk.Frame(self.panel_sat, bg=BG_MAIN, width=170)
        sat_left.pack(side="left", fill="y", padx=(0, 10))
        sat_left.pack_propagate(False)

        tk.Label(sat_left, text="🛰 卫星影像", bg=BG_MAIN, fg=FG_TEXT,
                 font=FONT_TITLE).pack(anchor="w", pady=(0, 8))

        # 卫星选择
        tk.Label(sat_left, text="卫星:", bg=BG_MAIN, fg=FG_DIM, font=FONT_SMALL).pack(anchor="w")
        sat_list = list(GEOSTATIONARY_SATELLITES.keys())
        sat_names = [GEOSTATIONARY_SATELLITES[s]["name"] for s in sat_list]
        self.sat_combo = ttk.Combobox(sat_left, textvariable=self.selected_satellite,
                                      values=sat_list, state="readonly",
                                      font=(FONT_FAMILY[0], 8), width=22)
        self.sat_combo.pack(fill="x", pady=(0, 8))
        # Map display names
        self._sat_display = dict(zip(sat_list, sat_names))

        # 颜色模式
        tk.Label(sat_left, text="颜色:", bg=BG_MAIN, fg=FG_DIM, font=FONT_SMALL).pack(anchor="w")
        color_frame = tk.Frame(sat_left, bg=BG_MAIN)
        color_frame.pack(fill="x", pady=(0, 8))
        for val, label in [("natural_color", "自然色"), ("geocolor", "地球色")]:
            tk.Radiobutton(color_frame, text=label, variable=self.selected_color, value=val,
                           bg=BG_MAIN, fg=FG_TEXT, selectcolor=BG_INPUT,
                           activebackground=BG_MAIN, activeforeground=FG_TEXT,
                           font=FONT_SMALL).pack(anchor="w")

        tk.Label(sat_left, text="分辨率:", bg=BG_MAIN, fg=FG_DIM, font=FONT_SMALL).pack(anchor="w")
        self.sat_size_var = tk.StringVar(value="1080")
        sz_frame = tk.Frame(sat_left, bg=BG_MAIN)
        sz_frame.pack(fill="x", pady=(0, 8))
        for val, lab in [("688", "标准"), ("1100", "高清"), ("2200", "超清")]:
            tk.Radiobutton(sz_frame, text=lab, variable=self.sat_size_var, value=val,
                           bg=BG_MAIN, fg=FG_TEXT, selectcolor=BG_INPUT,
                           activebackground=BG_MAIN, activeforeground=FG_TEXT,
                           font=FONT_SMALL).pack(anchor="w")

        info_card = tk.Frame(sat_left, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1)
        info_card.pack(fill="both", expand=True)
        self.sat_info_label = tk.Label(info_card, text="", bg=BG_CARD,
                                       fg=FG_DIM, font=FONT_SMALL, justify="left",
                                       padx=8, pady=8, wraplength=140)
        self.sat_info_label.pack(fill="both", expand=True)

        # 卫星预览区
        sat_right = tk.Frame(self.panel_sat, bg=BG_MAIN)
        sat_right.pack(side="left", fill="both", expand=True)

        sat_preview = tk.Frame(sat_right, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1)
        sat_preview.pack(fill="both", expand=True, pady=(0, 10))

        self.sat_preview = tk.Label(sat_preview, bg=BG_CARD,
                                    text="🛰\n选择卫星后点击获取最新影像",
                                    fg=FG_DIM, font=(FONT_FAMILY[0], 14))
        self.sat_preview.pack(fill="both", expand=True)

        self.sat_status = tk.Label(sat_preview, bg=BG_CARD, fg=FG_DIM, font=FONT_SMALL)
        self.sat_status.place(relx=0.02, rely=0.97, anchor="sw")

        # 底部控制
        sat_ctrl = tk.Frame(sat_right, bg=BG_MAIN, height=45)
        sat_ctrl.pack(fill="x", side="bottom")
        sat_ctrl.pack_propagate(False)

        self.btn_sat_fetch = ModernButton(sat_ctrl, text="📡 获取最新影像",
                                          command=self._fetch_satellite,
                                          width=120, height=32, bg=SAT_ACCENT, hover_bg="#00d4f4")
        self.btn_sat_fetch.pack(side="left", padx=(20, 2))

        self.btn_sat_auto = ModernButton(sat_ctrl,
                                         text="🔄 自动刷新: 开" if self.sat_auto_refresh else "🔄 自动刷新: 关",
                                         command=self._toggle_sat_auto_refresh,
                                         width=120, height=32,
                                         bg=GREEN if self.sat_auto_refresh else ACCENT2,
                                         hover_bg="#6ee7c5" if self.sat_auto_refresh else "#1a4a7a")
        self.btn_sat_auto.pack(side="left", padx=5)

        self.sat_countdown = tk.Label(sat_ctrl, text="", bg=BG_MAIN, fg=FG_DIM, font=(FONT_FAMILY[0], 9))
        self.sat_countdown.pack(side="left", padx=8)

        right_sc = tk.Frame(sat_ctrl, bg=BG_MAIN)
        right_sc.pack(side="right")
        self.btn_sat_wp = ModernButton(right_sc, text="🖼 设为壁纸",
                                       command=self._set_sat_wallpaper,
                                       width=100, height=32, bg=ACCENT, hover_bg=ACCENT_HOVER)
        self.btn_sat_wp.pack(side="left", padx=2)

        # === 太阳观测面板 ===
        self.panel_sdo = tk.Frame(body, bg=BG_MAIN)

        sdo_left = tk.Frame(self.panel_sdo, bg=BG_MAIN, width=170)
        sdo_left.pack(side="left", fill="y", padx=(0, 10))
        sdo_left.pack_propagate(False)

        tk.Label(sdo_left, text="☀ 太阳观测", bg=BG_MAIN, fg=FG_TEXT,
                 font=FONT_TITLE).pack(anchor="w", pady=(0, 8))

        tk.Label(sdo_left, text="数据来源\n━━━━━━━━━━━━\nNASA SDO\n太阳动力学天文台\n\n波段:", bg=BG_MAIN,
                 fg=FG_DIM, font=FONT_SMALL, justify="left").pack(anchor="w", pady=(0, 4))

        for key, info in SDO_BANDS.items():
            tk.Radiobutton(sdo_left, text=f"{info['name'][:18]}",
                           variable=self.selected_sdo_band, value=key,
                           bg=BG_MAIN, fg=FG_TEXT, selectcolor=BG_INPUT,
                           activebackground=BG_MAIN, activeforeground=FG_TEXT,
                           font=FONT_SMALL).pack(anchor="w")

        sdo_info_card = tk.Frame(sdo_left, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1)
        sdo_info_card.pack(fill="both", expand=True, pady=(8, 0))
        tk.Label(sdo_info_card, text="拍摄频率\n约每 15-60 分钟\n自动刷新每 60 分钟",
                 bg=BG_CARD, fg=FG_DIM, font=FONT_SMALL, justify="left", padx=8, pady=8).pack(fill="both")

        # SDO 预览区
        sdo_right = tk.Frame(self.panel_sdo, bg=BG_MAIN)
        sdo_right.pack(side="left", fill="both", expand=True)

        sdo_preview = tk.Frame(sdo_right, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1)
        sdo_preview.pack(fill="both", expand=True, pady=(0, 10))

        self.sdo_preview = tk.Label(sdo_preview, bg=BG_CARD,
                                    text="☀\n选择波段后点击获取最新太阳图像",
                                    fg=FG_DIM, font=(FONT_FAMILY[0], 14))
        self.sdo_preview.pack(fill="both", expand=True)

        self.sdo_status = tk.Label(sdo_preview, bg=BG_CARD, fg=FG_DIM, font=FONT_SMALL)
        self.sdo_status.place(relx=0.02, rely=0.97, anchor="sw")

        # SDO 底部控制
        sdo_ctrl = tk.Frame(sdo_right, bg=BG_MAIN, height=45)
        sdo_ctrl.pack(fill="x", side="bottom")
        sdo_ctrl.pack_propagate(False)

        self.btn_sdo_fetch = ModernButton(sdo_ctrl, text="📡 获取最新太阳图",
                                          command=self._fetch_sdo,
                                          width=120, height=32, bg=SDO_ACCENT, hover_bg="#ffaa33")
        self.btn_sdo_fetch.pack(side="left", padx=(20, 2))

        self.btn_sdo_auto = ModernButton(sdo_ctrl,
                                         text="🔄 自动刷新: 开" if self.sdo_auto_refresh else "🔄 自动刷新: 关",
                                         command=self._toggle_sdo_auto_refresh,
                                         width=120, height=32,
                                         bg=GREEN if self.sdo_auto_refresh else ACCENT2,
                                         hover_bg="#6ee7c5" if self.sdo_auto_refresh else "#1a4a7a")
        self.btn_sdo_auto.pack(side="left", padx=5)

        self.sdo_countdown = tk.Label(sdo_ctrl, text="", bg=BG_MAIN, fg=FG_DIM, font=(FONT_FAMILY[0], 9))
        self.sdo_countdown.pack(side="left", padx=8)

        right_sdo = tk.Frame(sdo_ctrl, bg=BG_MAIN)
        right_sdo.pack(side="right")
        self.btn_sdo_wp = ModernButton(right_sdo, text="🖼 设为壁纸",
                                       command=self._set_sdo_wallpaper,
                                       width=100, height=32, bg=ACCENT, hover_bg=ACCENT_HOVER)
        self.btn_sdo_wp.pack(side="left", padx=2)

        # === 风云四号面板 ===
        self.panel_fy4 = tk.Frame(body, bg=BG_MAIN)

        fy4_left = tk.Frame(self.panel_fy4, bg=BG_MAIN, width=170)
        fy4_left.pack(side="left", fill="y", padx=(0, 10))
        fy4_left.pack_propagate(False)

        tk.Label(fy4_left, text="🌏 风云四号", bg=BG_MAIN, fg=FG_TEXT,
                 font=FONT_TITLE).pack(anchor="w", pady=(0, 8))

        tk.Label(fy4_left, text="数据来源\n━━━━━━━━━━━━\n国家卫星气象中心\nNSMC\n\n分辨率:", bg=BG_MAIN,
                 fg=FG_DIM, font=FONT_SMALL, justify="left").pack(anchor="w", pady=(0, 4))

        self.fy4_size_var = tk.StringVar(value=str(self.config.get("fy4_size", 1080)))
        fy4_sz_frame = tk.Frame(fy4_left, bg=BG_MAIN)
        fy4_sz_frame.pack(fill="x", pady=(0, 8))
        for val, lab in [("1080", "1080p"), ("2048", "2K"), ("3840", "4K")]:
            tk.Radiobutton(fy4_sz_frame, text=lab, variable=self.fy4_size_var, value=val,
                           bg=BG_MAIN, fg=FG_TEXT, selectcolor=BG_INPUT,
                           activebackground=BG_MAIN, activeforeground=FG_TEXT,
                           font=FONT_SMALL).pack(anchor="w")

        fy4_info_card = tk.Frame(fy4_left, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1)
        fy4_info_card.pack(fill="both", expand=True, pady=(8, 0))
        tk.Label(fy4_info_card, text="更新频率\n约每 15 分钟\n自动刷新每 15 分钟",
                 bg=BG_CARD, fg=FG_DIM, font=FONT_SMALL, justify="left", padx=8, pady=8).pack(fill="both")

        # FY-4 预览区
        fy4_right = tk.Frame(self.panel_fy4, bg=BG_MAIN)
        fy4_right.pack(side="left", fill="both", expand=True)

        fy4_preview = tk.Frame(fy4_right, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1)
        fy4_preview.pack(fill="both", expand=True, pady=(0, 10))

        self.fy4_preview = tk.Label(fy4_preview, bg=BG_CARD,
                                    text="🌏\n点击获取风云四号最新影像",
                                    fg=FG_DIM, font=(FONT_FAMILY[0], 14))
        self.fy4_preview.pack(fill="both", expand=True)

        self.fy4_status = tk.Label(fy4_preview, bg=BG_CARD, fg=FG_DIM, font=FONT_SMALL)
        self.fy4_status.place(relx=0.02, rely=0.97, anchor="sw")

        # FY-4 底部控制
        fy4_ctrl = tk.Frame(fy4_right, bg=BG_MAIN, height=45)
        fy4_ctrl.pack(fill="x", side="bottom")
        fy4_ctrl.pack_propagate(False)

        self.btn_fy4_fetch = ModernButton(fy4_ctrl, text="📡 获取最新影像",
                                          command=self._fetch_fy4,
                                          width=120, height=32, bg=SAT_ACCENT, hover_bg="#00d4f4")
        self.btn_fy4_fetch.pack(side="left", padx=(20, 2))

        self.btn_fy4_auto = ModernButton(fy4_ctrl,
                                         text="🔄 自动刷新: 开" if self.fy4_auto_refresh else "🔄 自动刷新: 关",
                                         command=self._toggle_fy4_auto_refresh,
                                         width=120, height=32,
                                         bg=GREEN if self.fy4_auto_refresh else ACCENT2,
                                         hover_bg="#6ee7c5" if self.fy4_auto_refresh else "#1a4a7a")
        self.btn_fy4_auto.pack(side="left", padx=5)

        self.fy4_countdown = tk.Label(fy4_ctrl, text="", bg=BG_MAIN, fg=FG_DIM, font=(FONT_FAMILY[0], 9))
        self.fy4_countdown.pack(side="left", padx=8)

        right_fy4 = tk.Frame(fy4_ctrl, bg=BG_MAIN)
        right_fy4.pack(side="right")
        self.btn_fy4_wp = ModernButton(right_fy4, text="🖼 设为壁纸",
                                       command=self._set_fy4_wallpaper,
                                       width=100, height=32, bg=ACCENT, hover_bg=ACCENT_HOVER)
        self.btn_fy4_wp.pack(side="left", padx=2)

        # ---- 状态栏 ----
        self.status_bar = tk.Label(self.root, text="就绪", bg=BG_CARD, fg=FG_DIM,
                                   font=FONT_SMALL, anchor="w", padx=15)
        self.status_bar.pack(fill="x", side="bottom", ipady=4)

    # ========== 面板切换 ==========
    def _switch_panel(self, source: str):
        self.data_source = source
        self.config["data_source"] = source
        save_config(self.config)

        # 隐藏所有面板
        for p in [self.panel_apod, self.panel_sat, self.panel_sdo, self.panel_fy4]:
            p.pack_forget()

        # 停止所有计时器
        self._stop_sat_refresh_timer()
        self._stop_sdo_refresh_timer()
        self._stop_fy4_refresh_timer()

        # 重置按钮样式
        for btn in [self.btn_apod, self.btn_sat, self.btn_sdo, self.btn_fy4]:
            btn._bg = "#1a3a4a"
            btn._hover_bg = "#0f3460"
            btn._draw("#1a3a4a")

        if source == "apod":
            self.panel_apod.pack(fill="both", expand=True)
            self.btn_apod._bg = ACCENT2
            self.btn_apod._hover_bg = "#1a4a7a"
            self.btn_apod._draw(ACCENT2)
            self._update_status("天文图片模式 | NASA APOD 每日精选")
        elif source == "satellite":
            self.panel_sat.pack(fill="both", expand=True)
            self.btn_sat._bg = SAT_ACCENT
            self.btn_sat._hover_bg = "#00d4f4"
            self.btn_sat._draw(SAT_ACCENT)
            self._update_sat_info()
            sat = self.config.get("satellite_id", "himawari")
            self._update_status(f"卫星影像模式 | {GEOSTATIONARY_SATELLITES.get(sat, {}).get('name', sat)}")
            self._start_sat_refresh_timer()
        elif source == "sdo":
            self.panel_sdo.pack(fill="both", expand=True)
            self.btn_sdo._bg = SDO_ACCENT
            self.btn_sdo._hover_bg = "#ffaa33"
            self.btn_sdo._draw(SDO_ACCENT)
            band = self.config.get("sdo_band", "0304")
            self._update_status(f"太阳观测模式 | {SDO_BANDS.get(band, {}).get('name', band)}")
            self._start_sdo_refresh_timer()
        elif source == "fy4":
            self.panel_fy4.pack(fill="both", expand=True)
            self.btn_fy4._bg = SAT_ACCENT
            self.btn_fy4._hover_bg = "#00d4f4"
            self.btn_fy4._draw(SAT_ACCENT)
            self._update_status("风云四号模式 | FY-4B 真彩色全圆盘 (NSMC)")
            self._start_fy4_refresh_timer()

    # ========== APOD 数据操作 ==========
    def _rebuild_category_data(self):
        self.images_by_cat = {key: [] for key in get_all_category_keys()}
        images = self.metadata.get("images", {})
        for date_str, data in images.items():
            img = ApodImage.from_dict(data)
            cat = categorize_image(img)
            self.images_by_cat[cat].append(img)
            self.images_by_cat[ALL_CATEGORY].append(img)
        for key in self.images_by_cat:
            self.images_by_cat[key].sort(key=lambda x: x.date, reverse=True)

    def _refresh_ui(self):
        self.cat_tree.delete(*self.cat_tree.get_children())
        cat_counts = {key: len(self.images_by_cat.get(key, [])) for key in get_all_category_keys()}
        for key in get_all_category_keys():
            name = get_category_name(key)
            count = cat_counts.get(key, 0)
            item = self.cat_tree.insert("", "end", values=(name, count))
            if key == self.current_cat:
                self.cat_tree.selection_set(item)
                self.cat_tree.see(item)
        self._show_current_image()

    def _show_current_image(self):
        images = self.images_by_cat.get(self.current_cat, [])
        total = len(images)
        if total == 0:
            self.apod_preview.config(text="📷 暂无图片\n\n点击「获取历史」拉取 NASA 图片", fg=FG_DIM)
            self.apod_info.config(text="")
            self.page_label.config(text="0 / 0")
            self.current_image = None
            self.photo_ref = None
            return

        self.current_idx = max(0, min(self.current_idx, total - 1))
        img = images[self.current_idx]
        self.current_image = img
        self.page_label.config(text=f"{self.current_idx + 1} / {total}")

        cache_path = IMAGE_CACHE_DIR / f"{img.date}.jpg"
        if not cache_path.exists() and img.hdurl:
            cache_path = IMAGE_CACHE_DIR / f"{img.date}_hd.jpg"

        if cache_path.exists():
            self._load_image(str(cache_path), self.apod_preview, self.apod_info)
        else:
            self.apod_preview.config(text=f"⬇ 正在下载...\n{img.title}", fg=FG_DIM)
            self.apod_info.config(text=f"{img.date} | {img.title}")
            threading.Thread(target=self._download_and_show, args=(img,), daemon=True).start()

    def _load_image(self, path: str, label: tk.Label, info_label: tk.Label = None):
        try:
            pil_img = Image.open(path)
            self.root.update_idletasks()
            pw = label.winfo_width() or 900
            ph = label.winfo_height() or 600
            iw, ih = pil_img.size
            ratio = min(pw / iw, ph / ih, 1.0)
            nw, nh = int(iw * ratio), int(ih * ratio)
            pil_img = pil_img.resize((nw, nh), Image.LANCZOS)
            self.photo_ref = ImageTk.PhotoImage(pil_img)
            label.config(image=self.photo_ref, text="")
            if info_label and self.current_image:
                img = self.current_image
                info = f"📅 {img.date}    📛 {img.title}"
                if img.copyright:
                    info += f"    © {img.copyright}"
                info_label.config(text=info)
        except Exception as e:
            logger.error(f"Load image error: {e}")
            label.config(text="❌ 图片加载失败", fg=ACCENT)

    # ====== 通用图片加载 ======
    def _load_preview(self, path: str, label: tk.Label, status_label: tk.Label = None,
                      source_text: str = "", extra: str = ""):
        """通用预览图加载"""
        try:
            pil_img = Image.open(path)
            self.root.update_idletasks()
            pw = label.winfo_width() or 900
            ph = label.winfo_height() or 600
            iw, ih = pil_img.size
            ratio = min(pw / iw, ph / ih, 1.0)
            nw, nh = int(iw * ratio), int(ih * ratio)
            pil_img = pil_img.resize((nw, nh), Image.LANCZOS)
            self._prev_photo = ImageTk.PhotoImage(pil_img)
            label.config(image=self._prev_photo, text="")
            if status_label:
                now = datetime.now()
                status_label.config(
                    text=f"{source_text} | {now.strftime('%Y-%m-%d %H:%M')} | "
                         f"{iw}x{ih} {extra}")
        except Exception as e:
            logger.error(f"Load preview error: {e}")
            label.config(text="❌ 加载失败", fg=ACCENT)

    def _download_and_show(self, img: ApodImage):
        path = download_image(img, hd=self.config.get("hd", True))
        if path:
            self.root.after(0, lambda: self._load_image(path, self.apod_preview, self.apod_info))
        else:
            self.root.after(0, lambda: self.apod_preview.config(
                text="❌ 下载失败，请检查网络", fg=ACCENT))

    # ========== APOD 事件 ==========
    def _on_cat_select(self, event):
        sel = self.cat_tree.selection()
        if not sel:
            return
        idx = self.cat_tree.index(sel[0])
        keys = get_all_category_keys()
        if idx < len(keys):
            self.current_cat = keys[idx]
            self.current_idx = 0
            self._show_current_image()
            self._update_status(f"已切换：{get_category_name(self.current_cat)}")

    def _prev_image(self):
        if self.current_idx > 0:
            self.current_idx -= 1
            self._show_current_image()

    def _next_image(self):
        images = self.images_by_cat.get(self.current_cat, [])
        if self.current_idx < len(images) - 1:
            self.current_idx += 1
            self._show_current_image()

    def _set_wallpaper(self):
        if not self.current_image:
            messagebox.showwarning("提示", "请先选择一张图片")
            return
        cache_path = IMAGE_CACHE_DIR / f"{self.current_image.date}.jpg"
        if not cache_path.exists() and self.current_image.hdurl:
            cache_path = IMAGE_CACHE_DIR / f"{self.current_image.date}_hd.jpg"
        if cache_path.exists():
            style = self.config.get("wallpaper_style", "fill")
            # 水印标注
            wp_path = watermark_image(
                str(cache_path),
                left_text="来源: NASA 每日天文图片 (APOD)",
                right_text=f"拍摄: {self.current_image.date} | {self.current_image.title}",
                output_key=f"apod_{self.current_image.date}",
            )
            if set_wallpaper(wp_path, self.current_image.date.replace("-", ""), style=style):
                self._update_status(f"壁纸已更换：{self.current_image.title}", color=GREEN)
            else:
                messagebox.showerror("错误", "壁纸设置失败")
        else:
            messagebox.showwarning("提示", "图片尚未下载完成")

    def _fetch_history(self):
        days = simpledialog.askinteger("获取历史图片", "获取最近多少天的 APOD 图片？",
                                       initialvalue=10, minvalue=1, maxvalue=365)
        if not days:
            return
        self._update_status(f"⏳ 正在获取最近 {days} 天的图片...")
        threading.Thread(target=self._do_fetch, args=(days,), daemon=True).start()

    def _do_fetch(self, days: int):
        try:
            end = datetime.now()
            start = end - timedelta(days=days)
            images = fetch_apod_range(
                start_date=start.strftime("%Y-%m-%d"),
                end_date=end.strftime("%Y-%m-%d"),
                api_key=self.config.get("api_key"),
            )
            metadata = load_metadata()
            for img in images:
                metadata["images"][img.date] = img.to_dict()
            save_metadata(metadata)
            self.metadata = metadata
            self.root.after(0, self._on_fetch_done, len(images))
        except Exception as e:
            logger.error(f"Fetch error: {e}")
            self.root.after(0, lambda: self._update_status(f"❌ 获取失败: {e}", color=ACCENT))

    def _on_fetch_done(self, count: int):
        self._rebuild_category_data()
        self._refresh_ui()
        self._update_status(f"已获取 {count} 张图片", color=GREEN)

    def _update_now(self):
        self._update_status("⏳ 正在检查更新...")
        threading.Thread(target=self._do_update, daemon=True).start()

    def _do_update(self):
        try:
            result = check_and_update()
            self.root.after(0, self._rebuild_category_data)
            self.root.after(0, self._refresh_ui)
            self.root.after(0, lambda: self._update_status(
                "壁纸已更新" if result else "今日暂无匹配图片",
                color=GREEN if result else FG_DIM))
        except Exception as e:
            self.root.after(0, lambda: self._update_status(f"❌ 更新失败: {e}", color=ACCENT))

    # ========== 卫星影像事件 ==========
    def _update_sat_info(self):
        """更新卫星信息卡片"""
        sat = self.selected_satellite.get()
        info = GEOSTATIONARY_SATELLITES.get(sat, {})
        src = info.get("source")
        if src == "noaa":
            src_name = "NOAA STAR/NESDIS"
        elif src == "fy4":
            src_name = "国家卫星气象中心 NSMC"
        else:
            src_name = "CIRA RAMMB-Slider"
        self.sat_info_label.config(
            text=f"卫星: {info.get('name', sat)}\n\n"
                 f"数据源\n━━━━━━━━━━\n{src_name}\n\n"
                 f"区域\n━━━━━━━━━━\n{info.get('region', '-')}\n\n"
                 f"更新频率\n━━━━━━━━━━\n约每 10 分钟\n\n"
                 f"颜色模式\n━━━━━━━━━━\n自然色/地球色"
        )

    def _fetch_satellite(self):
        sat = self.selected_satellite.get()
        color = self.selected_color.get()
        size = int(self.sat_size_var.get())
        name = GEOSTATIONARY_SATELLITES.get(sat, {}).get("name", sat)
        self.config["satellite_id"] = sat
        self.config["satellite_color"] = color
        save_config(self.config)
        self._update_status(f"⏳ 正在获取 {name} 卫星影像...", color=YELLOW)
        self.sat_preview.config(text="⏳\n正在获取卫星影像...", fg=FG_DIM)
        self._update_sat_info()
        threading.Thread(target=self._do_fetch_sat, args=(sat, color, size), daemon=True).start()

    def _do_fetch_sat(self, sat: str, color: str, size: int):
        try:
            path = fetch_satellite_image(satellite=sat, color=color, target_size=size)
            if path:
                self.sat_image_path = path
                self.root.after(0, lambda: self._load_preview(path, self.sat_preview, self.sat_status,
                    f"🛰 {GEOSTATIONARY_SATELLITES.get(sat, {}).get('name', sat)}"))
                self.root.after(0, lambda: self._update_status(
                    f"卫星影像已更新 | {GEOSTATIONARY_SATELLITES.get(sat, {}).get('name', sat)}",
                    color=GREEN))
            else:
                self.root.after(0, lambda: self.sat_preview.config(
                    text="❌ 获取失败\n数据暂时不可用，请稍后重试", fg=ACCENT))
                self.root.after(0, lambda: self._update_status("❌ 获取失败", color=ACCENT))
        except Exception as e:
            logger.error(f"Sat fetch error: {e}")
            self.root.after(0, lambda: self.sat_preview.config(text=f"❌ {str(e)[:60]}", fg=ACCENT))
            self.root.after(0, lambda: self._update_status(f"❌ {e}", color=ACCENT))

    def _set_sat_wallpaper(self):
        if not self.sat_image_path or not Path(self.sat_image_path).exists():
            messagebox.showwarning("提示", "请先获取卫星影像")
            return
        sat = self.config.get("satellite_id", "himawari")
        name = GEOSTATIONARY_SATELLITES.get(sat, {}).get("name", sat)
        style = self.config.get("wallpaper_style", "fill")
        now = datetime.now()
        wp_path = watermark_image(
            self.sat_image_path,
            left_text=f"来源: {name}",
            right_text=f"拍摄时间: {now.strftime('%Y-%m-%d %H:%M')} (UTC+8)",
            output_key=f"sat_{sat}",
        )
        if set_wallpaper(wp_path, f"sat_{sat}", style=style):
            self._update_status(f"壁纸已设置 | {name} | 后台持续自动更新", color=GREEN)
        else:
            messagebox.showerror("错误", "壁纸设置失败")

    # ========== SDO 太阳事件 ==========
    def _fetch_sdo(self):
        band = self.selected_sdo_band.get()
        name = SDO_BANDS.get(band, {}).get("name", band)
        self.config["sdo_band"] = band
        save_config(self.config)
        self._update_status(f"⏳ 正在获取 {name} 太阳图像...", color=YELLOW)
        self.sdo_preview.config(text="⏳\n正在获取太阳图像...", fg=FG_DIM)
        threading.Thread(target=self._do_fetch_sdo, args=(band,), daemon=True).start()

    def _do_fetch_sdo(self, band: str):
        try:
            path = fetch_sdo_image(band=band)
            if path:
                self.sdo_image_path = path
                name = SDO_BANDS.get(band, {}).get("name", band)
                self.root.after(0, lambda: self._load_preview(path, self.sdo_preview,
                    self.sdo_status, f"☀ {name}", "NASA SDO"))
                self.root.after(0, lambda: self._update_status(
                    f"太阳图像已更新 | {name}", color=GREEN))
            else:
                self.root.after(0, lambda: self.sdo_preview.config(
                    text="❌ 获取失败\nNASA SDO 数据暂时不可用", fg=ACCENT))
                self.root.after(0, lambda: self._update_status("❌ 获取失败", color=ACCENT))
        except Exception as e:
            logger.error(f"SDO fetch error: {e}")
            self.root.after(0, lambda: self.sdo_preview.config(text=f"❌ {str(e)[:60]}", fg=ACCENT))
            self.root.after(0, lambda: self._update_status(f"❌ {e}", color=ACCENT))

    def _set_sdo_wallpaper(self):
        if not self.sdo_image_path or not Path(self.sdo_image_path).exists():
            messagebox.showwarning("提示", "请先获取太阳图像")
            return
        band = self.config.get("sdo_band", "0304")
        name = SDO_BANDS.get(band, {}).get("name", band)
        style = self.config.get("wallpaper_style", "fill")
        now = datetime.now()
        wp_path = watermark_image(
            self.sdo_image_path,
            left_text=f"来源: NASA SDO 太阳观测",
            right_text=f"波段: {name} | {now.strftime('%Y-%m-%d %H:%M')}",
            output_key=f"sdo_{band}",
        )
        if set_wallpaper(wp_path, f"sdo_{band}", style=style):
            self._update_status(f"壁纸已设置 | {name}", color=GREEN)
        else:
            messagebox.showerror("错误", "壁纸设置失败")

    # ========== 卫星自动刷新 ==========
    def _toggle_sat_auto_refresh(self):
        self.sat_auto_refresh = not self.sat_auto_refresh
        self.config["satellite_auto_refresh"] = self.sat_auto_refresh
        save_config(self.config)
        if self.sat_auto_refresh:
            self.btn_sat_auto._bg = GREEN
            self.btn_sat_auto._hover_bg = "#6ee7c5"
            self.btn_sat_auto._text = "🔄 自动刷新: 开"
            self.btn_sat_auto._draw(GREEN)
            self._start_sat_refresh_timer()
            self._update_status("卫星自动刷新已开启", color=GREEN)
        else:
            self.btn_sat_auto._bg = ACCENT2
            self.btn_sat_auto._hover_bg = "#1a4a7a"
            self.btn_sat_auto._text = "🔄 自动刷新: 关"
            self.btn_sat_auto._draw(ACCENT2)
            self._stop_sat_refresh_timer()
            self._update_status("卫星自动刷新已关闭")

    def _start_sat_refresh_timer(self):
        if not self.sat_auto_refresh:
            return
        if self._sat_timer_id:
            self.root.after_cancel(self._sat_timer_id)
        self._sat_next_refresh = datetime.now() + timedelta(minutes=self.sat_refresh_interval)
        self._update_sat_countdown()
        self._sat_timer_id = self.root.after(1000, self._sat_tick)

    def _stop_sat_refresh_timer(self):
        if self._sat_timer_id:
            self.root.after_cancel(self._sat_timer_id)
            self._sat_timer_id = None
        self._sat_next_refresh = None
        self.sat_countdown.config(text="")

    def _sat_tick(self):
        if not self.sat_auto_refresh:
            return
        now = datetime.now()
        if self._sat_next_refresh and (self._sat_next_refresh - now).total_seconds() <= 0:
            self._sat_next_refresh = now + timedelta(minutes=self.sat_refresh_interval)
            self.sat_countdown.config(text="⏳ 刷新中...")
            sat = self.config.get("satellite_id", "himawari")
            color = self.config.get("satellite_color", "natural_color")
            size = self.config.get("satellite_size", 1080)
            threading.Thread(target=self._do_sat_auto_refresh, args=(sat, color, size), daemon=True).start()
        else:
            self._update_sat_countdown()
        self._sat_timer_id = self.root.after(1000, self._sat_tick)

    def _update_sat_countdown(self):
        if not self._sat_next_refresh:
            return
        remaining = max(0, (self._sat_next_refresh - datetime.now()).total_seconds())
        m, s = int(remaining // 60), int(remaining % 60)
        self.sat_countdown.config(text=f"⏱ {m:02d}:{s:02d}", fg=FG_DIM if m > 1 else YELLOW)

    def _do_sat_auto_refresh(self, sat: str, color: str, size: int):
        try:
            path = fetch_satellite_image(satellite=sat, color=color, target_size=size)
            if not path:
                return
            old = self.sat_image_path
            self.sat_image_path = path
            self.root.after(0, lambda: self._load_preview(path, self.sat_preview, self.sat_status,
                f"🛰 {GEOSTATIONARY_SATELLITES.get(sat, {}).get('name', sat)}"))
            if self.data_source == "satellite":
                style = self.config.get("wallpaper_style", "fill")
                now = datetime.now()
                name = GEOSTATIONARY_SATELLITES.get(sat, {}).get("name", sat)
                wp_path = watermark_image(path,
                    left_text=f"来源: {name}",
                    right_text=f"拍摄时间: {now.strftime('%Y-%m-%d %H:%M')} (UTC+8)",
                    output_key=f"sat_{sat}")
                set_wallpaper(wp_path, f"sat_{sat}", style=style)
                self.root.after(0, lambda: self._update_status(
                    f"🛰 自动刷新 | {now.strftime('%H:%M')} | 壁纸同步更新", color=GREEN))
            else:
                self.root.after(0, lambda: self._update_status("🛰 卫星影像已自动刷新", color=GREEN))
        except Exception as e:
            logger.error(f"Sat auto-refresh error: {e}")

    # ========== SDO 自动刷新 ==========
    def _toggle_sdo_auto_refresh(self):
        self.sdo_auto_refresh = not self.sdo_auto_refresh
        self.config["sdo_auto_refresh"] = self.sdo_auto_refresh
        save_config(self.config)
        if self.sdo_auto_refresh:
            self.btn_sdo_auto._bg = GREEN
            self.btn_sdo_auto._hover_bg = "#6ee7c5"
            self.btn_sdo_auto._text = "🔄 自动刷新: 开"
            self.btn_sdo_auto._draw(GREEN)
            self._start_sdo_refresh_timer()
            self._update_status("SDO 自动刷新已开启", color=GREEN)
        else:
            self.btn_sdo_auto._bg = ACCENT2
            self.btn_sdo_auto._hover_bg = "#1a4a7a"
            self.btn_sdo_auto._text = "🔄 自动刷新: 关"
            self.btn_sdo_auto._draw(ACCENT2)
            self._stop_sdo_refresh_timer()
            self._update_status("SDO 自动刷新已关闭")

    def _start_sdo_refresh_timer(self):
        if not self.sdo_auto_refresh:
            return
        if self._sdo_timer_id:
            self.root.after_cancel(self._sdo_timer_id)
        self._sdo_next_refresh = datetime.now() + timedelta(minutes=self.sdo_refresh_interval)
        self._update_sdo_countdown()
        self._sdo_timer_id = self.root.after(1000, self._sdo_tick)

    def _stop_sdo_refresh_timer(self):
        if self._sdo_timer_id:
            self.root.after_cancel(self._sdo_timer_id)
            self._sdo_timer_id = None
        self._sdo_next_refresh = None
        self.sdo_countdown.config(text="")

    def _sdo_tick(self):
        if not self.sdo_auto_refresh:
            return
        now = datetime.now()
        remaining = max(0, (self._sdo_next_refresh - now).total_seconds())
        if remaining <= 0:
            self._sdo_next_refresh = now + timedelta(minutes=self.sdo_refresh_interval)
            self.sdo_countdown.config(text="⏳ 刷新中...")
            band = self.config.get("sdo_band", "0304")
            threading.Thread(target=self._do_sdo_auto_refresh, args=(band,), daemon=True).start()
        else:
            self._update_sdo_countdown()
        self._sdo_timer_id = self.root.after(1000, self._sdo_tick)

    def _update_sdo_countdown(self):
        if not self._sdo_next_refresh:
            return
        remaining = max(0, (self._sdo_next_refresh - datetime.now()).total_seconds())
        m, s = int(remaining // 60), int(remaining % 60)
        self.sdo_countdown.config(text=f"⏱ {m:02d}:{s:02d}", fg=FG_DIM if m > 1 else YELLOW)

    def _do_sdo_auto_refresh(self, band: str):
        try:
            path = fetch_sdo_image(band=band)
            if not path:
                return
            self.sdo_image_path = path
            name = SDO_BANDS.get(band, {}).get("name", band)
            self.root.after(0, lambda: self._load_preview(path, self.sdo_preview,
                self.sdo_status, f"☀ {name}", "NASA SDO"))
            if self.data_source == "sdo":
                style = self.config.get("wallpaper_style", "fill")
                now = datetime.now()
                wp_path = watermark_image(path,
                    left_text="来源: NASA SDO 太阳观测",
                    right_text=f"波段: {name} | {now.strftime('%Y-%m-%d %H:%M')}",
                    output_key=f"sdo_{band}")
                set_wallpaper(wp_path, f"sdo_{band}", style=style)
                self.root.after(0, lambda: self._update_status(
                    f"☀ SDO 自动刷新 | {now.strftime('%H:%M')} | 壁纸同步更新", color=GREEN))
            else:
                self.root.after(0, lambda: self._update_status("☀ SDO 已自动刷新", color=GREEN))
        except Exception as e:
            logger.error(f"SDO auto-refresh error: {e}")

    # ========== 风云四号事件 ==========
    def _fetch_fy4(self):
        size = int(self.fy4_size_var.get())
        self.config["fy4_size"] = size
        save_config(self.config)
        self._update_status(f"⏳ 正在获取风云四号影像...", color=YELLOW)
        self.fy4_preview.config(text="⏳\n正在获取风云四号影像...", fg=FG_DIM)
        threading.Thread(target=self._do_fetch_fy4, args=(size,), daemon=True).start()

    def _do_fetch_fy4(self, size: int):
        try:
            path = fetch_fy4_image(target_size=size)
            if path:
                self.fy4_image_path = path
                self.root.after(0, lambda: self._load_preview(path, self.fy4_preview, self.fy4_status,
                    f"🌏 风云四号 FY-4B"))
                self.root.after(0, lambda: self._update_status(
                    f"风云四号影像已更新 | FY-4B 真彩色", color=GREEN))
            else:
                self.root.after(0, lambda: self.fy4_preview.config(
                    text="❌ 获取失败\n数据暂时不可用，请稍后重试", fg=ACCENT))
                self.root.after(0, lambda: self._update_status("❌ 获取失败", color=ACCENT))
        except Exception as e:
            logger.error(f"FY-4 fetch error: {e}")
            self.root.after(0, lambda: self.fy4_preview.config(text=f"❌ {str(e)[:60]}", fg=ACCENT))
            self.root.after(0, lambda: self._update_status(f"❌ {e}", color=ACCENT))

    def _set_fy4_wallpaper(self):
        if not self.fy4_image_path or not Path(self.fy4_image_path).exists():
            messagebox.showwarning("提示", "请先获取风云四号影像")
            return
        style = self.config.get("wallpaper_style", "fill")
        now = datetime.now()
        capture_time = None
        try:
            capture_time = get_fy4_capture_time()
        except Exception as e:
            logger.warning(f"Get FY-4 capture time for wallpaper: {e}")
        wp_path = watermark_image(
            self.fy4_image_path,
            left_text="来源: 风云四号 FY-4B (NSMC)",
            right_text=f"更新时间: {capture_time.strftime('%Y-%m-%d %H:%M') if capture_time else now.strftime('%Y-%m-%d %H:%M')} (UTC+8)",
            output_key=f"fy4",
        )
        if set_wallpaper(wp_path, f"fy4", style=style):
            self._update_status(f"壁纸已设置 | 风云四号 FY-4B | 后台持续自动更新", color=GREEN)
        else:
            messagebox.showerror("错误", "壁纸设置失败")

    # ========== 风云四号自动刷新 ==========
    def _toggle_fy4_auto_refresh(self):
        self.fy4_auto_refresh = not self.fy4_auto_refresh
        self.config["fy4_auto_refresh"] = self.fy4_auto_refresh
        save_config(self.config)
        if self.fy4_auto_refresh:
            self.btn_fy4_auto._bg = GREEN
            self.btn_fy4_auto._hover_bg = "#6ee7c5"
            self.btn_fy4_auto._text = "🔄 自动刷新: 开"
            self.btn_fy4_auto._draw(GREEN)
            self._start_fy4_refresh_timer()
            self._update_status("风云四号自动刷新已开启", color=GREEN)
        else:
            self.btn_fy4_auto._bg = ACCENT2
            self.btn_fy4_auto._hover_bg = "#1a4a7a"
            self.btn_fy4_auto._text = "🔄 自动刷新: 关"
            self.btn_fy4_auto._draw(ACCENT2)
            self._stop_fy4_refresh_timer()
            self._update_status("风云四号自动刷新已关闭")

    def _start_fy4_refresh_timer(self):
        if not self.fy4_auto_refresh:
            return
        if self._fy4_timer_id:
            self.root.after_cancel(self._fy4_timer_id)
        self._fy4_next_refresh = datetime.now() + timedelta(minutes=self.fy4_refresh_interval)
        self._update_fy4_countdown()
        self._fy4_timer_id = self.root.after(1000, self._fy4_tick)

    def _stop_fy4_refresh_timer(self):
        if self._fy4_timer_id:
            self.root.after_cancel(self._fy4_timer_id)
            self._fy4_timer_id = None
        self._fy4_next_refresh = None
        self.fy4_countdown.config(text="")

    def _fy4_tick(self):
        if not self.fy4_auto_refresh:
            return
        now = datetime.now()
        remaining = max(0, (self._fy4_next_refresh - now).total_seconds())
        if remaining <= 0:
            self._fy4_next_refresh = now + timedelta(minutes=self.fy4_refresh_interval)
            self.fy4_countdown.config(text="⏳ 刷新中...")
            size = self.config.get("fy4_size", 1080)
            threading.Thread(target=self._do_fy4_auto_refresh, args=(size,), daemon=True).start()
        else:
            self._update_fy4_countdown()
        self._fy4_timer_id = self.root.after(1000, self._fy4_tick)

    def _update_fy4_countdown(self):
        if not self._fy4_next_refresh:
            return
        remaining = max(0, (self._fy4_next_refresh - datetime.now()).total_seconds())
        m, s = int(remaining // 60), int(remaining % 60)
        self.fy4_countdown.config(text=f"⏱ {m:02d}:{s:02d}", fg=FG_DIM if m > 1 else YELLOW)

    def _do_fy4_auto_refresh(self, size: int):
        try:
            path = fetch_fy4_image(target_size=size)
            if not path:
                return
            self.fy4_image_path = path
            self.root.after(0, lambda: self._load_preview(path, self.fy4_preview,
                self.fy4_status, f"🌏 风云四号 FY-4B"))
            if self.data_source == "fy4":
                style = self.config.get("wallpaper_style", "fill")
                now = datetime.now()
                capture_time = None
                try:
                    capture_time = get_fy4_capture_time()
                except Exception as e:
                    logger.warning(f"Get FY-4 capture time: {e}")
                wp_path = watermark_image(path,
                    left_text="来源: 风云四号 FY-4B (NSMC)",
                    right_text=f"更新时间: {capture_time.strftime('%Y-%m-%d %H:%M') if capture_time else now.strftime('%Y-%m-%d %H:%M')} (UTC+8)",
                    output_key=f"fy4")
                set_wallpaper(wp_path, f"fy4", style=style)
                self.root.after(0, lambda: self._update_status(
                    f"🌏 风云四号自动刷新 | {now.strftime('%H:%M')} | 壁纸同步更新", color=GREEN))
            else:
                self.root.after(0, lambda: self._update_status("🌏 风云四号影像已自动刷新", color=GREEN))
        except Exception as e:
            logger.error(f"FY-4 auto-refresh error: {e}")

    def _show_settings(self):
        win = tk.Toplevel(self.root)
        win.title("设置")
        win.geometry("420x480")
        win.configure(bg=BG_MAIN)
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        tk.Label(win, text="⚙ 设置", bg=BG_MAIN, fg="white",
                 font=FONT_TITLE).pack(pady=12)

        # NASA API Key
        f1 = tk.Frame(win, bg=BG_MAIN)
        f1.pack(fill="x", padx=30, pady=4)
        tk.Label(f1, text="NASA API Key:", bg=BG_MAIN, fg=FG_TEXT, font=FONT_BODY).pack(anchor="w")
        api_entry = tk.Entry(f1, bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT,
                             font=FONT_BODY, relief="flat")
        api_entry.pack(fill="x", pady=2, ipady=3)
        api_entry.insert(0, self.config.get("api_key", DEFAULT_API_KEY))

        # 壁纸样式
        f_ws = tk.Frame(win, bg=BG_MAIN)
        f_ws.pack(fill="x", padx=30, pady=4)
        tk.Label(f_ws, text="壁纸样式:", bg=BG_MAIN, fg=FG_TEXT, font=FONT_BODY).pack(anchor="w")
        ws_frame = tk.Frame(f_ws, bg=BG_MAIN)
        ws_frame.pack(fill="x", pady=2)
        wp_style = tk.StringVar(value=self.config.get("wallpaper_style", "fill"))
        styles_cn = [("居中", "center"), ("平铺", "tile"), ("拉伸", "stretch"),
                     ("适应", "fit"), ("填充(推荐)", "fill")]
        for cn, val in styles_cn:
            tk.Radiobutton(ws_frame, text=cn, variable=wp_style, value=val,
                           bg=BG_MAIN, fg=FG_TEXT, selectcolor=BG_INPUT,
                           activebackground=BG_MAIN, activeforeground=FG_TEXT,
                           font=FONT_SMALL).pack(side="left", padx=5)

        # 自动更新
        f2 = tk.Frame(win, bg=BG_MAIN)
        f2.pack(fill="x", padx=30, pady=4)
        auto_var = tk.BooleanVar(value=self.config.get("auto_update", True))
        tk.Checkbutton(f2, text="启用自动更新壁纸", variable=auto_var,
                       bg=BG_MAIN, fg=FG_TEXT, selectcolor=BG_INPUT,
                       activebackground=BG_MAIN, activeforeground=FG_TEXT,
                       font=FONT_BODY).pack(anchor="w")

        # HD
        f4 = tk.Frame(win, bg=BG_MAIN)
        f4.pack(fill="x", padx=30, pady=4)
        hd_var = tk.BooleanVar(value=self.config.get("hd", True))
        tk.Checkbutton(f4, text="优先下载高清图片 (NASA APOD)", variable=hd_var,
                       bg=BG_MAIN, fg=FG_TEXT, selectcolor=BG_INPUT,
                       activebackground=BG_MAIN, activeforeground=FG_TEXT,
                       font=FONT_BODY).pack(anchor="w")

        # 开机自启动
        f5 = tk.Frame(win, bg=BG_MAIN)
        f5.pack(fill="x", padx=30, pady=4)
        autostart_var = tk.BooleanVar(value=is_autostart_enabled())
        tk.Checkbutton(f5, text="开机自启动", variable=autostart_var,
                       bg=BG_MAIN, fg=FG_TEXT, selectcolor=BG_INPUT,
                       activebackground=BG_MAIN, activeforeground=FG_TEXT,
                       font=FONT_BODY).pack(anchor="w")
        tk.Label(f5, text="开机时自动启动软件并在后台运行",
                 bg=BG_MAIN, fg=FG_DIM, font=FONT_SMALL).pack(anchor="w", padx=22)

        def save():
            self.config["api_key"] = api_entry.get().strip() or DEFAULT_API_KEY
            self.config["wallpaper_style"] = wp_style.get()
            self.config["auto_update"] = auto_var.get()
            self.config["hd"] = hd_var.get()

            # 开机自启动
            want_autostart = autostart_var.get()
            if want_autostart != is_autostart_enabled():
                if set_autostart(want_autostart):
                    self.config["autostart"] = want_autostart
                    self._update_status(
                        "开机自启动已开启" if want_autostart else "开机自启动已关闭",
                        color=GREEN)
                else:
                    messagebox.showerror("错误", "开机自启动设置失败，请检查权限")
            else:
                self.config["autostart"] = want_autostart

            save_config(self.config)
            win.destroy()
            self._update_status("设置已保存", color=GREEN)

        ModernButton(win, text="💾 保存", command=save,
                     width=100, height=34, bg=ACCENT).pack(pady=15)

    # ========== 使用说明 ==========
    def _show_help(self):
        win = tk.Toplevel(self.root)
        win.title("使用说明")
        win.geometry("540x520")
        win.configure(bg=BG_MAIN)
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        tk.Label(win, text="📖 使用说明", bg=BG_MAIN, fg="white",
                 font=FONT_TITLE).pack(pady=12)

        help_text = """Live Earth Wallpaper - 卫星壁纸

【三大数据源模式】
点击顶部的标签切换数据源：

🔭 天文图片 — NASA APOD 每日精选
• 首次启动自动获取近 10 天图片
• 左侧选择分类浏览：星云、星系、行星等 10 个类别
• 点击图片设为壁纸，支持导航浏览
• 每天在设定时间自动更新

🛰 卫星影像 — 多卫星实时影像
• 支持 6 颗地球静止卫星：
  GOES-19/18/16 (美洲)、Himawari-8 (亚太)、
  GK2A (韩国)、风云四号 FY-4B (中国)
• 颜色模式：自然色 / 地球色 (含夜景)
• 分辨率：标准 / 高清 / 超清
• 数据源：CIRA RAMMB-Slider / NOAA / NSMC
• 开启自动刷新，每 10 分钟自动更新

☀ 太阳观测 — NASA SDO 太阳图像
• 多个观测波段：
  304 Å (色球层)、171 Å (日冕)、
  连续光球 (太阳黑子)、带磁场线叠加
• 数据来源：NASA 太阳动力学天文台
• 约每 15-60 分钟更新一张

【壁纸样式】
在「设置」中选择：居中 / 平铺 / 拉伸 / 适应 / 填充

【NASA API Key】
默认使用 DEMO_KEY（每小时限流 30 次）。
建议访问 https://api.nasa.gov/ 申请免费 Key。

【数据存储】
配置和缓存在 %USERPROFILE%\\.nasa_wallpaper\\ 目录

【关闭与后台运行】
• 点击右上角 X 可选择「最小化到任务栏」或「退出」
• 最小化后后台持续更新壁纸
• 壁纸右上角标注来源和拍摄时间
"""

        text = tk.Text(win, bg=BG_INPUT, fg=FG_TEXT, font=FONT_BODY,
                       relief="flat", wrap="word", padx=15, pady=10, height=22, width=55)
        text.pack(fill="both", expand=True, padx=20, pady=5)
        text.insert("1.0", help_text)
        text.config(state="disabled")

        ModernButton(win, text="知道了", command=win.destroy,
                     width=80, height=32, bg=ACCENT2, hover_bg="#1a4a7a").pack(pady=8)

    # ========== 状态/自动启动 ==========
    def _update_status(self, text: str, color: str = FG_DIM):
        self.status_bar.config(text=text, fg=color)

    def _auto_fetch_on_startup(self):
        self._update_status("⏳ 首次启动，正在自动获取 NASA 图片...", color=YELLOW)
        self.apod_preview.config(text="⏳ 首次启动\n\n正在从 NASA 获取卫星图片...\n请稍候", fg=FG_DIM)
        self.apod_info.config(text="数据来源: NASA APOD API | 首次运行自动拉取近 10 天图片")
        threading.Thread(target=self._do_auto_fetch, daemon=True).start()

    def _do_auto_fetch(self):
        try:
            end = datetime.now()
            start = end - timedelta(days=10)
            api_key = self.config.get("api_key") or DEFAULT_API_KEY
            self.root.after(0, lambda: self._update_status("⏳ 正在连接 NASA API...", color=YELLOW))
            images = fetch_apod_range(
                start_date=start.strftime("%Y-%m-%d"),
                end_date=end.strftime("%Y-%m-%d"),
                api_key=api_key,
            )
            if images:
                metadata = load_metadata()
                for img in images:
                    metadata["images"][img.date] = img.to_dict()
                save_metadata(metadata)
                self.metadata = metadata
                self.root.after(0, lambda: self._on_auto_fetch_done(len(images)))
            else:
                self.root.after(0, lambda: self._update_status(
                    "⚠ NASA API 暂时不可用，请稍后手动获取", color=ACCENT))
                self.root.after(0, lambda: self.apod_preview.config(
                    text="⚠ 首次拉取失败\n\nNASA API 暂时不可用\n请稍后点击「获取历史」重试", fg=YELLOW))
        except Exception as e:
            logger.error(f"Auto-fetch error: {e}")
            self.root.after(0, lambda: self._update_status(f"⚠ 自动获取失败: {e}", color=ACCENT))

    def _on_auto_fetch_done(self, count: int):
        self._rebuild_category_data()
        self._refresh_ui()
        self._update_status(f"已自动获取 {count} 张 NASA 图片，选一个分类设为壁纸吧", color=GREEN)

    def _check_auto_startup(self):
        if not self.metadata.get("images"):
            self._auto_fetch_on_startup()
        else:
            self._update_status("就绪")

    # ========== 关闭行为 ==========
    # ========== 关闭 / 托盘 ==========
    def _on_close(self):
        """点击 X 按钮时弹出选择对话框"""
        dialog = tk.Toplevel(self.root)
        dialog.title("退出选项")
        dialog.geometry("380x200")
        dialog.configure(bg=BG_MAIN)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        # 居中显示
        dialog.update_idletasks()
        dw, dh = dialog.winfo_width(), dialog.winfo_height()
        rw, rh = self.root.winfo_width(), self.root.winfo_height()
        rx, ry = self.root.winfo_x(), self.root.winfo_y()
        dialog.geometry(f"+{rx + (rw - dw) // 2}+{ry + (rh - dh) // 2}")

        tk.Label(dialog, text="Live Earth Wallpaper", bg=BG_MAIN, fg="white",
                 font=FONT_TITLE).pack(pady=(18, 4))
        tk.Label(dialog, text="请选择关闭方式", bg=BG_MAIN, fg=FG_DIM,
                 font=FONT_BODY).pack(pady=(0, 12))

        btn_frame = tk.Frame(dialog, bg=BG_MAIN)
        btn_frame.pack(pady=4)

        def do_minimize():
            dialog.destroy()
            self._minimize_to_tray()

        def do_quit():
            dialog.destroy()
            self._quit_app()

        ModernButton(btn_frame, text="— 最小化到状态栏 —",
                     command=do_minimize,
                     width=150, height=38, bg=BLUE, hover_bg=BLUE_HOVER,
                     font=(FONT_FAMILY[0], 10)).pack(side="left", padx=8)

        ModernButton(btn_frame, text="✕ 退出程序",
                     command=do_quit,
                     width=120, height=38, bg=ACCENT, hover_bg=ACCENT_HOVER,
                     font=(FONT_FAMILY[0], 10)).pack(side="left", padx=8)

        tk.Label(dialog, text="最小化后后台持续更新 | 在状态栏右键恢复窗口",
                 bg=BG_MAIN, fg=FG_DIM, font=FONT_SMALL).pack(pady=(8, 0))

    def _minimize_to_tray(self):
        """最小化到系统托盘"""
        if self.tray_icon is not None:
            self.root.withdraw()
            return

        # 创建托盘图标（简单的 64x64 地球图标）
        icon_img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        from PIL import ImageDraw
        draw = ImageDraw.Draw(icon_img)
        # 画一个简单的圆形地球
        draw.ellipse([4, 4, 60, 60], fill="#2d8cf0", outline="#1a6fd4", width=2)
        # 画一个简化的大陆轮廓
        draw.ellipse([14, 12, 40, 36], fill="#4ecca3")
        draw.ellipse([22, 42, 50, 58], fill="#4ecca3")

        menu = pystray.Menu(
            pystray.MenuItem("显示窗口", self._restore_from_tray, default=True),
            pystray.MenuItem("退出程序", self._quit_from_tray),
        )

        self.tray_icon = pystray.Icon(
            "LivingEarthWallpaper",
            icon_img,
            "Live Earth Wallpaper",
            menu,
        )

        self.root.withdraw()
        self._update_status("已最小化到状态栏，后台持续更新壁纸", color=GREEN)

        # 在后台线程运行托盘图标
        import threading
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _restore_from_tray(self):
        """从状态栏恢复窗口"""
        self.root.after(0, self._do_restore)

    def _do_restore(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._update_status("窗口已恢复", color=GREEN)

    def _quit_from_tray(self):
        """从状态栏退出"""
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        self._quit_app()

    def _quit_app(self):
        """完全退出程序"""
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        stop_scheduler()
        self.root.destroy()


def main():
    root = tk.Tk()
    NASAApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
