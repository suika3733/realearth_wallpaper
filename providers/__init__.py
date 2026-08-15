"""卫星数据源提供器 - 支持多颗地球静止卫星 + SDO 太阳观测 + 风云四号"""
from .geostationary import (
    GEOSTATIONARY_SATELLITES,
    SATELLITE_SIZES,
    fetch_satellite_image,
)
from .sdo import SDO_BANDS, fetch_sdo_image
from .fy4 import fetch_fy4_image, get_fy4_capture_time, test_fy4_connectivity
from .noaa_goes import fetch_noaa_goes_image, get_noaa_goes_capture_time, test_noaa_goes_connectivity

__all__ = [
    "GEOSTATIONARY_SATELLITES",
    "SATELLITE_SIZES",
    "SDO_BANDS",
    "fetch_satellite_image",
    "fetch_sdo_image",
    "fetch_fy4_image",
    "get_fy4_capture_time",
    "test_fy4_connectivity",
    "fetch_noaa_goes_image",
    "get_noaa_goes_capture_time",
    "test_noaa_goes_connectivity",
]
