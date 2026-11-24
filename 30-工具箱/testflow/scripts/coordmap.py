#!/usr/bin/env python3
"""testflow 坐标换算器。

真机测试最容易翻车的地方。三条纪律:
  1. 用例里只存归一化比例(0..1),禁止硬编码像素
  2. 必须先探测设备逻辑分辨率与画面实际矩形
  3. 优先锚点相对定位,而非绝对坐标

这个工具负责第 2 条里的换算,以及把归一化坐标映射到:
  - 输入 API 坐标(adb tap 的物理像素 / iOS 触摸 API 的 points)
  - 投屏窗口像素(computer-use 点击用)

纯标准库,零依赖。PNG 解码是手写的(探测黑边 + 判定轮裁剪区域都要用),
支持 8/16bit、灰度、灰度+alpha、RGB、RGBA、调色板(1-8bit,含 tRNS),
不支持交错且明确报错。编码侧只写 8bit RGB/RGBA。
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------- 机型档案

@dataclass(frozen=True)
class DeviceProfile:
    name: str
    pixel_w: int
    pixel_h: int
    dpi: int
    scale: float          # iOS: pixels per point (2 或 3);Android: 1.0
    tap_space: str        # "pixels" 或 "points"

    @property
    def logical_w(self) -> float:
        """输入 API 使用的坐标空间宽度。"""
        return self.pixel_w / self.scale if self.tap_space == "points" else float(self.pixel_w)

    @property
    def logical_h(self) -> float:
        return self.pixel_h / self.scale if self.tap_space == "points" else float(self.pixel_h)

    @property
    def aspect(self) -> float:
        return self.pixel_w / self.pixel_h


PROFILES: dict[str, DeviceProfile] = {
    # Android:tap 坐标与截图为同一物理像素空间
    "android-mid":    DeviceProfile("android-mid", 1080, 2400, 480, 1.0, "pixels"),
    "android-low":    DeviceProfile("android-low", 720, 1280, 320, 1.0, "pixels"),
    "android-hd":     DeviceProfile("android-hd", 1440, 3120, 560, 1.0, "pixels"),
    "android-tablet": DeviceProfile("android-tablet", 1600, 2560, 320, 1.0, "pixels"),
    # iOS:触摸 API 用 points,截图是 pixels,差 scaleFactor 倍
    "iphone-14":      DeviceProfile("iphone-14", 1170, 2532, 460, 3.0, "points"),
    "iphone-15-pro":  DeviceProfile("iphone-15-pro", 1179, 2556, 460, 3.0, "points"),
    "iphone-se":      DeviceProfile("iphone-se", 750, 1334, 326, 2.0, "points"),
    "ipad-10":        DeviceProfile("ipad-10", 1640, 2360, 264, 2.0, "points"),
}


# ---------------------------------------------------------------- 映射核心

@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    def as_list(self) -> list[float]:
        return [round(self.x, 2), round(self.y, 2), round(self.w, 2), round(self.h, 2)]


def clamp01(v: float) -> float:
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def norm_to_tap(norm: tuple[float, float], prof: DeviceProfile) -> tuple[float, float]:
    """归一化比例 -> 输入 API 坐标(adb 物理像素 / iOS points)。"""
    nx, ny = clamp01(norm[0]), clamp01(norm[1])
    return round(nx * prof.logical_w, 2), round(ny * prof.logical_h, 2)


def norm_to_device_pixels(norm: tuple[float, float], prof: DeviceProfile) -> tuple[int, int]:
    """归一化比例 -> 设备物理像素(截图坐标系)。"""
    nx, ny = clamp01(norm[0]), clamp01(norm[1])
    return int(round(nx * prof.pixel_w)), int(round(ny * prof.pixel_h))


def norm_to_window(norm: tuple[float, float], content: Rect) -> tuple[int, int]:
    """归一化比例 -> 投屏窗口/截图光栅内的像素(computer-use 点击用)。

    content 是设备画面在窗口内的实际矩形,已扣除缩放与黑边。
    """
    nx, ny = clamp01(norm[0]), clamp01(norm[1])
    return int(round(content.x + nx * content.w)), int(round(content.y + ny * content.h))


def window_to_norm(px: float, py: float, content: Rect) -> tuple[float, float]:
    """反向:窗口像素 -> 归一化比例(把观察到的点击位置写回用例)。"""
    if content.w <= 0 or content.h <= 0:
        raise ValueError("content rect 宽高必须为正")
    return ((px - content.x) / content.w, (py - content.y) / content.h)


def fit_content_rect(capture_w: int, capture_h: int, prof: DeviceProfile,
                     mode: str = "contain") -> Rect:
    """由截图/窗口尺寸与设备宽高比推算设备画面的实际矩形(处理缩放与黑边)。

    contain: 完整显示,留黑边(投屏默认)
    cover:   铺满,裁掉一部分
    stretch: 拉伸(会变形,仅用于已知被拉伸的情况)
    """
    if capture_w <= 0 or capture_h <= 0:
        raise ValueError("capture 尺寸必须为正")
    if mode == "stretch":
        return Rect(0.0, 0.0, float(capture_w), float(capture_h))

    dev_aspect = prof.aspect
    cap_aspect = capture_w / capture_h

    if mode == "contain":
        if cap_aspect > dev_aspect:
            h = float(capture_h)
            w = h * dev_aspect
        else:
            w = float(capture_w)
            h = w / dev_aspect
    elif mode == "cover":
        if cap_aspect > dev_aspect:
            w = float(capture_w)
            h = w / dev_aspect
        else:
            h = float(capture_h)
            w = h * dev_aspect
    else:
        raise ValueError(f"未知 fit mode: {mode}")

    return Rect((capture_w - w) / 2.0, (capture_h - h) / 2.0, w, h)


def anchor_to_target(anchor_norm: tuple[float, float],
                     offset_norm: tuple[float, float]) -> tuple[float, float]:
    """锚点相对定位:定位到 landmark 后按相对偏移点击。

    偏移量用归一化单位(与坐标同一空间),跨机型一致。
    """
    return (clamp01(anchor_norm[0] + offset_norm[0]),
            clamp01(anchor_norm[1] + offset_norm[1]))


# ---------------------------------------------------------------- PNG 黑边探测

class PngUnsupported(Exception):
    pass


CHANNELS_BY_CTYPE = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
CTYPE_NAMES = {0: "grayscale", 2: "RGB", 3: "palette", 4: "grayscale+alpha", 6: "RGBA"}


def parse_png_header(data: bytes) -> dict:
    """只读 IHDR,不解码像素。尺寸探测走这条路(大文件也是微秒级)。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise PngUnsupported(
            "不是 PNG 文件(首 8 字节不是 PNG 签名)。若是 JPEG/HEIC,"
            "请先转成 PNG,例如 `sips -s format png in.jpg --out out.png`"
        )
    if len(data) < 29:
        raise PngUnsupported("PNG 被截断,读不到 IHDR")
    w, h, depth, ctype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", data[16:29])
    if ctype not in CHANNELS_BY_CTYPE:
        raise PngUnsupported(f"未知 color type {ctype}")
    return {"w": w, "h": h, "depth": depth, "ctype": ctype,
            "interlace": interlace, "color": CTYPE_NAMES[ctype]}


def probe_size(path: Path) -> tuple[int, int]:
    """只读文件头取尺寸。不解码,不可能是慢的。"""
    with path.open("rb") as fh:
        head = fh.read(29)
    hdr = parse_png_header(head)
    return hdr["w"], hdr["h"]


def _unpack_subbyte(row: bytes, depth: int, count: int) -> list[int]:
    """把 1/2/4 位深的打包行展开成索引序列。"""
    per = 8 // depth
    mask = (1 << depth) - 1
    out: list[int] = []
    for byte in row:
        for shift in range(per - 1, -1, -1):
            out.append((byte >> (shift * depth)) & mask)
            if len(out) == count:
                return out
    return out


def decode_png_bytes(data: bytes) -> tuple[int, int, int, bytes]:
    """解码 PNG,返回 (w, h, channels, 8bit 字节流),channels 为 2/3/4。

    支持:灰度 1/2/4/8/16、灰度+alpha 8/16、RGB 8/16、RGBA 8/16、
          调色板 1/2/4/8(含 tRNS 透明色)。
    不支持:交错(Adam7)。

    **遇到不支持的情况一律明确抛错,绝不静默降级。**
    静默降级意味着坐标基于一个猜出来的矩形,而使用者只看到一行容易忽略的警告——
    那比直接失败危险得多。
    """
    hdr = parse_png_header(data)
    w, h, depth, ctype, interlace = (hdr["w"], hdr["h"], hdr["depth"],
                                     hdr["ctype"], hdr["interlace"])
    if interlace:
        raise PngUnsupported(
            "不支持交错(Adam7)PNG。先转换格式再试,"
            "例如 `sips -s format png in.png --out out.png`"
        )
    if depth not in (1, 2, 4, 8, 16):
        raise PngUnsupported(f"不支持的位深 {depth}bit")
    if ctype == 3 and depth == 16:
        raise PngUnsupported("调色板 PNG 不支持 16bit")
    if ctype in (2, 6) and depth < 8:
        raise PngUnsupported(f"{CTYPE_NAMES[ctype]} 不支持 {depth}bit")

    pos, idat, plte, trns = 8, bytearray(), None, None
    while pos + 8 <= len(data):
        (ln,) = struct.unpack(">I", data[pos:pos + 4])
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        if typ == b"IDAT":
            idat += chunk
        elif typ == b"PLTE":
            plte = chunk
        elif typ == b"tRNS":
            trns = chunk
        elif typ == b"IEND":
            break
        pos += 12 + ln

    raw_channels = CHANNELS_BY_CTYPE[ctype]
    bits_per_pixel = raw_channels * depth
    bpp = max(1, bits_per_pixel // 8)            # 过滤器参考的字节步长
    stride = (w * bits_per_pixel + 7) // 8
    raw = zlib.decompress(bytes(idat))

    rows: list[bytes] = []
    prev = bytearray(stride)
    p = 0
    for _y in range(h):
        ft = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        rows.append(bytes(line))
        prev = line

    # ---- 调色板
    if ctype == 3:
        if plte is None:
            raise PngUnsupported("调色板 PNG 缺 PLTE chunk")
        palette = [(plte[i], plte[i + 1], plte[i + 2])
                   for i in range(0, len(plte) - 2, 3)]
        alpha = list(trns) if trns else None
        out = bytearray()
        for row in rows:
            for idx in _unpack_subbyte(row, depth, w):
                if idx >= len(palette):
                    raise PngUnsupported(f"调色板索引越界: {idx}")
                r, g, b = palette[idx]
                out += bytes((r, g, b))
                if alpha is not None:
                    out.append(alpha[idx] if idx < len(alpha) else 255)
        return w, h, (4 if alpha is not None else 3), bytes(out)

    # ---- 16bit 降到 8bit(取高字节;截图场景足够)
    if depth == 16:
        out = bytearray()
        step = raw_channels * 2
        for row in rows:
            for i in range(0, step * w, step):
                for c in range(raw_channels):
                    out.append(row[i + c * 2])
        px = bytes(out)
    else:
        px = b"".join(rows)

    # ---- 灰度统一成 RGB
    if ctype == 0:
        out = bytearray()
        if depth == 8:
            for v in px:
                out += bytes((v, v, v))
        else:
            scale = 255 // ((1 << depth) - 1)
            for row in rows:
                for v in _unpack_subbyte(row, depth, w):
                    g = v * scale
                    out += bytes((g, g, g))
        return w, h, 3, bytes(out)

    return w, h, raw_channels, px


def read_png(path: Path) -> tuple[int, int, int, bytes]:
    """从文件读取并解码 PNG。语义同 decode_png_bytes。"""
    return decode_png_bytes(path.read_bytes())


# ---------------------------------------------------------------- PNG 编码与裁剪

def _make_png(w: int, h: int, depth: int, ctype: int, rows: list[bytes],
              plte: bytes | None = None, trns: bytes | None = None,
              interlace: int = 0) -> bytes:
    """最小 PNG 编码器(过滤类型 0)。自测构造样本与裁剪回写共用。"""
    def chunk(typ: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + typ + payload
                + struct.pack(">I", zlib.crc32(typ + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, depth, ctype, 0, 0, interlace)
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
    if plte is not None:
        out += chunk(b"PLTE", plte)
    if trns is not None:
        out += chunk(b"tRNS", trns)
    raw = b"".join(b"\x00" + r for r in rows)      # filter type 0(None)
    out += chunk(b"IDAT", zlib.compress(raw))
    out += chunk(b"IEND", b"")
    return out


def encode_png_8bit(w: int, h: int, ch: int, px: bytes) -> bytes:
    """把 8bit 像素编码成 PNG。ch 只接受 3(RGB) / 4(RGBA)。

    裁剪出来的区域要重新落盘成截图再交给判定模型,走这条路径。
    """
    if ch not in (3, 4):
        raise PngUnsupported(f"encode_png_8bit 只支持 3/4 通道,收到 {ch}")
    if len(px) != w * h * ch:
        raise PngUnsupported(f"像素字节数不符: {w}×{h}×{ch} 应为 {w * h * ch},"
                             f"实际 {len(px)}")
    stride = w * ch
    rows = [px[y * stride:(y + 1) * stride] for y in range(h)]
    return _make_png(w, h, 8, 2 if ch == 3 else 6, rows)


def crop_png_region(path: Path, region) -> tuple[bytes, dict]:
    """按归一化 region [x0,y0,x1,y1] 裁剪截图,返回 (PNG 字节, 几何信息)。

    判定请求里的 region 是归一化坐标,这里换成像素并真的把区域裁出来 ——
    把整屏交给模型会让无关元素造成误判(见 references/4-conversion.md)。

    灰度+alpha 会先展开成 RGBA 再编码,因为判定端点普遍不接受 2 通道 PNG。
    """
    w, h, ch, px = read_png(path)
    x0f, y0f, x1f, y1f = (float(v) for v in region)
    x0 = max(0, min(w - 1, int(round(x0f * w))))
    x1 = max(x0 + 1, min(w, int(round(x1f * w))))
    y0 = max(0, min(h - 1, int(round(y0f * h))))
    y1 = max(y0 + 1, min(h, int(round(y1f * h))))

    stride = w * ch
    out = bytearray()
    for y in range(y0, y1):
        out += px[y * stride + x0 * ch: y * stride + x1 * ch]
    cropped = bytes(out)
    if ch == 2:                                   # 灰度+alpha -> RGBA
        rgba = bytearray()
        for i in range(0, len(cropped), 2):
            g, a = cropped[i], cropped[i + 1]
            rgba += bytes((g, g, g, a))
        ch, cropped = 4, bytes(rgba)

    cw, chh = x1 - x0, y1 - y0
    info = {"full": [w, h], "cropPx": [x0, y0, x1, y1], "cropSize": [cw, chh]}
    return encode_png_8bit(cw, chh, ch, cropped), info


def detect_content_rect(path: Path, dark_threshold: int = 12,
                        min_run_ratio: float = 0.55) -> tuple[int, int, Rect]:
    """扫描截图四边的近黑区域,推断设备画面实际矩形(黑边探测)。

    投屏窗口信噪比可能很高(拍摄内容本身就有黑色区域),所以要求
    黑边在整条边上连续覆盖 min_run_ratio 才认定为黑边。
    """
    w, h, ch, px = read_png(path)

    def is_dark(x: int, y: int) -> bool:
        i = (y * w + x) * ch
        r = px[i]
        g = px[i + 1] if ch >= 3 else r
        b = px[i + 2] if ch >= 3 else r
        return (r + g + b) / 3 <= dark_threshold

    def row_dark_ratio(y: int) -> float:
        return sum(1 for x in range(0, w, max(1, w // 200)) if is_dark(x, y)) / len(
            range(0, w, max(1, w // 200)))

    def col_dark_ratio(x: int) -> float:
        return sum(1 for y in range(0, h, max(1, h // 200)) if is_dark(x, y)) / len(
            range(0, h, max(1, h // 200)))

    top = 0
    while top < h - 1 and row_dark_ratio(top) >= min_run_ratio:
        top += 1
    bottom = h - 1
    while bottom > top and row_dark_ratio(bottom) >= min_run_ratio:
        bottom -= 1
    left = 0
    while left < w - 1 and col_dark_ratio(left) >= min_run_ratio:
        left += 1
    right = w - 1
    while right > left and col_dark_ratio(right) >= min_run_ratio:
        right -= 1

    return w, h, Rect(float(left), float(top), float(right - left + 1), float(bottom - top + 1))


# ---------------------------------------------------------------- 自测

def selftest() -> int:
    fails: list[str] = []

    def check(name: str, got, want, tol: float = 0.51) -> None:
        if isinstance(got, (list, tuple)):
            ok = len(got) == len(want) and all(abs(g - wt) <= tol for g, wt in zip(got, want))
        else:
            ok = abs(got - want) <= tol
        if not ok:
            fails.append(f"{name}: got={got} want={want}")

    a = PROFILES["android-mid"]
    # Android:tap 空间 = 物理像素,1:1
    check("android tap", norm_to_tap((0.5, 0.91), a), (540.0, 2184.0))
    check("android pixels", norm_to_device_pixels((0.5, 0.91), a), (540, 2184))
    check("android logical", a.logical_w, 1080.0)

    i = PROFILES["iphone-14"]
    # iOS:points = pixels / 3。390x844 logical
    check("ios logical", i.logical_w, 390.0)
    check("ios tap (points)", norm_to_tap((0.5, 0.5), i), (195.0, 422.0))
    check("ios pixels", norm_to_device_pixels((0.5, 0.5), i), (585, 1266))
    # 关键:同一个归一化坐标,tap 空间与像素空间差 3 倍
    check("ios 3x ratio", i.pixel_w / i.logical_w, 3.0)

    # 边界裁剪
    check("clamp high", norm_to_tap((1.4, -0.2), a), (1080.0, 0.0))

    # 黑边:设备 1080x2400(0.45) 放进 1200x2400 窗口 -> 左右各 60 黑边
    c = fit_content_rect(1200, 2400, a, "contain")
    check("letterbox w", c.w, 1080.0)
    check("letterbox x", c.x, 60.0)
    check("letterbox h", c.h, 2400.0)
    # 归一化 -> 窗口像素:0.5 应落在窗口正中
    check("window center", norm_to_window((0.5, 0.5), c), (600, 1200))
    check("window left", norm_to_window((0.0, 0.0), c), (60, 0))

    # 反向换算
    check("roundtrip", window_to_norm(600, 1200, c), (0.5, 0.5))

    # cover 模式
    cc = fit_content_rect(1200, 2400, a, "cover")
    check("cover w", cc.w, 1200.0)
    check("cover x", cc.x, 0.0)

    # stretch
    cs = fit_content_rect(1200, 2400, a, "stretch")
    check("stretch", (cs.w, cs.h), (1200.0, 2400.0))

    # 锚点相对定位
    check("anchor", anchor_to_target((0.5, 0.9), (0.0, 0.05)), (0.5, 0.95))

    # ---- PNG 格式覆盖。用内存构造的样本,不依赖外部文件,
    #      这样"解码器支持哪些格式"是被实测的,不是被声称的。
    def dec(png: bytes):
        return decode_png_bytes(png)

    w_, h_, c_, d_ = dec(_make_png(2, 1, 8, 2, [bytes([255, 0, 0, 0, 255, 0])]))
    check("png rgb8 size", [w_, h_, c_], [2, 1, 3])
    check("png rgb8 data", list(d_), [255, 0, 0, 0, 255, 0])

    _, _, c_, d_ = dec(_make_png(1, 1, 8, 6, [bytes([1, 2, 3, 4])]))
    check("png rgba8 ch", c_, 4)
    check("png rgba8 data", list(d_), [1, 2, 3, 4])

    pal = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255])
    _, _, c_, d_ = dec(_make_png(2, 1, 8, 3, [bytes([0, 1])], plte=pal))
    check("png palette8 ch", c_, 3)
    check("png palette8 data", list(d_), [255, 0, 0, 0, 255, 0])

    _, _, c_, d_ = dec(_make_png(2, 1, 8, 3, [bytes([0, 1])], plte=pal,
                                 trns=bytes([128, 255])))
    check("png palette+trns ch", c_, 4)
    check("png palette+trns data", list(d_), [255, 0, 0, 128, 0, 255, 0, 255])

    # 4bit 调色板:一行一个字节装两个索引
    _, _, c_, d_ = dec(_make_png(2, 1, 4, 3, [bytes([0x12])], plte=pal))
    check("png palette4 ch", c_, 3)
    check("png palette4 data", list(d_), [0, 255, 0, 0, 0, 255])

    # 16bit RGB:取高字节降为 8bit
    _, _, c_, d_ = dec(_make_png(1, 1, 16, 2, [bytes([255, 0, 0, 128, 0, 0])]))
    check("png rgb16 ch", c_, 3)
    check("png rgb16 data", list(d_), [255, 0, 0])

    # 灰度 8bit -> 展开成 RGB
    _, _, c_, d_ = dec(_make_png(1, 1, 8, 0, [bytes([128])]))
    check("png gray8 ch", c_, 3)
    check("png gray8 data", list(d_), [128, 128, 128])

    # 灰度 1bit 两个像素 -> 白、黑
    _, _, c_, d_ = dec(_make_png(2, 1, 1, 0, [bytes([0b10000000])]))
    check("png gray1 data", list(d_), [255, 255, 255, 0, 0, 0])

    # 灰度+alpha
    _, _, c_, d_ = dec(_make_png(1, 1, 8, 4, [bytes([7, 9])]))
    check("png gray+alpha ch", c_, 2)
    check("png gray+alpha data", list(d_), [7, 9])

    # 交错必须明确抛错 —— 绝不能静默降级
    try:
        dec(_make_png(1, 1, 8, 2, [bytes([0, 0, 0])], interlace=1))
        fails.append("交错 PNG 应当抛 PngUnsupported,但没有抛")
    except PngUnsupported:
        pass

    # 非 PNG 必须明确抛错
    try:
        parse_png_header(b"\xff\xd8\xff\xe0" + b"\x00" * 40)
        fails.append("非 PNG 数据应当抛 PngUnsupported,但没有抛")
    except PngUnsupported:
        pass

    # 只读头部取尺寸:截断到 29 字节(HIHD 刚好读完)也应能拿到宽高
    hd = parse_png_header(_make_png(2, 1, 8, 2, [bytes([1, 2, 3, 4, 5, 6])])[:29])
    check("probe size header-only", [hd["w"], hd["h"]], [2, 1])

    # ---- 判定轮裁剪:归一化 region -> 像素矩形 -> 重新编码的 PNG 仍可解码。
    #      判定请求里的 region 必须真的作用到像素上,而不是只写进提问文本。
    import tempfile

    row = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])   # 红绿蓝白
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "shot.png"
        p.write_bytes(_make_png(4, 2, 8, 2, [row, row]))

        out, info = crop_png_region(p, (0.5, 0.0, 1.0, 1.0))        # 右半
        check("crop rect", info["cropPx"], [2, 0, 4, 2])
        check("crop size", info["cropSize"], [2, 2])
        cw, chh, cch, cpx = decode_png_bytes(out)
        check("crop out dims", [cw, chh, cch], [2, 2, 3])
        check("crop out data", list(cpx), [0, 0, 255, 255, 255, 255] * 2)

        _, info_full = crop_png_region(p, (0.0, 0.0, 1.0, 1.0))
        check("crop full size", info_full["cropSize"], [4, 2])

        # 归一化越界要夹紧,不能裁出空图或越界读
        _, info_clamp = crop_png_region(p, (-0.5, -0.5, 1.5, 1.5))
        check("crop clamp", info_clamp["cropPx"], [0, 0, 4, 2])

        # 极小 region 至少留 1 像素,不能被 round 成 0 宽
        _, info_min = crop_png_region(p, (0.1, 0.1, 0.11, 0.11))
        check("crop min size", info_min["cropSize"], [1, 1])

        # 灰度+alpha 裁完必须变成 RGBA(判定端点普遍不收 2 通道 PNG)
        p.write_bytes(_make_png(2, 1, 8, 4, [bytes([10, 255, 20, 128])]))
        out2, _ = crop_png_region(p, (0.5, 0.0, 1.0, 1.0))
        _, _, gch, gpx = decode_png_bytes(out2)
        check("crop gray+alpha ch", gch, 4)
        check("crop gray+alpha px", list(gpx), [20, 20, 20, 128])

    # 编码器必须拒绝不合法的输入,不能悄悄写出一张坏图
    try:
        encode_png_8bit(1, 1, 2, bytes([1, 2]))
        fails.append("encode_png_8bit 收到 2 通道应当抛 PngUnsupported")
    except PngUnsupported:
        pass
    try:
        encode_png_8bit(2, 2, 3, bytes([1, 2, 3]))
        fails.append("encode_png_8bit 像素数不符应当抛 PngUnsupported")
    except PngUnsupported:
        pass

    if fails:
        print("自测失败:")
        for f in fails:
            print("  ✗", f)
        return 1
    print(f"自测通过({len(PROFILES)} 个机型档案:映射/黑边/反向/锚点;"
          f"PNG 格式:RGB/RGBA/调色板(1-8bit,含 tRNS)/16bit/灰度/灰度+alpha;"
          f"交错与非 PNG 明确报错;头部取尺寸;"
          f"region 裁剪回写(含灰度+alpha→RGBA/越界夹紧/最小 1 像素/拒绝非法输入))")
    return 0


# ---------------------------------------------------------------- CLI

def parse_pair(s: str, cast=float) -> tuple:
    parts = [p for p in re_split_commas(s) if p != ""]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"需要两个值,形如 0.5,0.91,实为 {s!r}")
    return (cast(parts[0]), cast(parts[1]))


def re_split_commas(s: str) -> list[str]:
    return [p.strip() for p in s.replace("x", ",").replace("，", ",").split(",")]


def parse_rect(s: str) -> Rect:
    parts = re_split_commas(s)
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(f"需要 4 个值 x,y,w,h,实为 {s!r}")
    return Rect(*(float(p) for p in parts))


def build_profile(args) -> DeviceProfile:
    if args.profile:
        if args.profile not in PROFILES:
            raise SystemExit(f"未知机型 {args.profile!r}。用 --list-profiles 查看")
        return PROFILES[args.profile]
    if args.pixels:
        w, h = (int(v) for v in re_split_commas(args.pixels))
        return DeviceProfile("custom", w, h, args.dpi or 0,
                             args.scale or 1.0, args.tap_space)
    raise SystemExit("需要 --profile 或 --pixels")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="testflow 坐标换算:归一化比例 -> 输入 API 坐标 / 投屏窗口像素")
    ap.add_argument("--profile", help="机型档案名")
    ap.add_argument("--pixels", help="自定义设备物理分辨率,形如 1080x2400")
    ap.add_argument("--dpi", type=int)
    ap.add_argument("--scale", type=float, help="iOS: pixels per point(2 或 3)")
    ap.add_argument("--tap-space", choices=["pixels", "points"], default="pixels")
    ap.add_argument("--norm", help="要换算的归一化坐标,形如 0.5,0.91")
    ap.add_argument("--anchor-norm", help="锚点归一化坐标")
    ap.add_argument("--offset", help="锚点相对偏移(归一化单位),形如 0,0.05")
    ap.add_argument("--window-rect", type=parse_rect,
                    help="投屏窗口内设备画面的实际矩形 x,y,w,h")
    ap.add_argument("--capture", type=Path, help="截图文件,用于探测黑边")
    ap.add_argument("--detect-rect", action="store_true",
                    help="从 --capture 探测设备画面矩形(扫描近黑边)")
    ap.add_argument("--fit-mode", choices=["contain", "cover", "stretch"], default="contain",
                    help="无 --window-rect 时,由截图尺寸与设备宽高比推算矩形")
    ap.add_argument("--allow-fit-fallback", action="store_true",
                    help="黑边探测失败时允许退回宽高比推算(默认**不允许**,直接报错)")
    ap.add_argument("--probe", action="store_true",
                    help="只读文件头报告截图格式与尺寸,不解码")
    ap.add_argument("--list-profiles", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()

    if args.list_profiles:
        for name, p in PROFILES.items():
            print(f"{name:16s} {p.pixel_w}x{p.pixel_h} dpi={p.dpi} "
                  f"scale={p.scale} tap={p.tap_space} "
                  f"logical={p.logical_w:g}x{p.logical_h:g}")
        return 0

    if args.selftest:
        return selftest()

    # --probe 与机型无关,必须在 build_profile 之前处理,
    # 否则会被无谓地要求 --profile
    if args.probe:
        if not args.capture:
            print("错误: --probe 需要 --capture", file=sys.stderr)
            return 2
        try:
            with args.capture.open("rb") as fh:
                hdr = parse_png_header(fh.read(64))
        except PngUnsupported as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 2
        print(f"{args.capture}")
        print(f"  {hdr['w']}x{hdr['h']}  depth={hdr['depth']}bit  "
              f"color={hdr['color']}  interlace={hdr['interlace']}")
        return 0

    prof = build_profile(args)

    if not args.norm and not args.anchor_norm:
        ap.print_help()
        return 0

    norm = parse_pair(args.norm) if args.norm else None
    if args.anchor_norm:
        off = parse_pair(args.offset) if args.offset else (0.0, 0.0)
        norm = anchor_to_target(parse_pair(args.anchor_norm), off)

    content: Rect | None = args.window_rect
    detect_info: dict = {}

    if args.capture and content is None:
        if args.detect_rect:
            try:
                cw, ch, content = detect_content_rect(args.capture)
                detect_info = {"capture": str(args.capture), "capture_size": [cw, ch],
                               "method": "black-border scan"}
            except PngUnsupported as exc:
                # 这里**不静默降级**。降级会让坐标基于一个猜出来的矩形,
                # 而使用者只看到一行容易忽略的警告 —— 比直接失败危险得多。
                if not args.allow_fit_fallback:
                    print(f"错误: 黑边探测失败 —— {exc}", file=sys.stderr)
                    print("  不自动降级,因为降级会让坐标基于猜出来的矩形而错误难以发现。"
                          "三个选择:", file=sys.stderr)
                    print("   · 转换格式后重试(如 sips -s format png in.png --out out.png)",
                          file=sys.stderr)
                    print("   · 直接给矩形: --window-rect x,y,w,h", file=sys.stderr)
                    print("   · 显式接受推算: --allow-fit-fallback", file=sys.stderr)
                    return 2
                print(f"警告: 黑边探测失败({exc});"
                      f"因 --allow-fit-fallback 显式退回宽高比推算", file=sys.stderr)

        if content is None:
            try:
                cw, ch = probe_size(args.capture)
            except PngUnsupported as exc:
                print(f"错误: 无法读取截图尺寸 —— {exc}", file=sys.stderr)
                return 2
            content = fit_content_rect(cw, ch, prof, args.fit_mode)
            detect_info = {
                "capture": str(args.capture), "capture_size": [cw, ch],
                "method": f"aspect-ratio fit ({args.fit_mode})"
                          + (" [显式降级:黑边探测失败的兜底]" if args.detect_rect else ""),
            }

    tap_x, tap_y = norm_to_tap(norm, prof)
    px_x, px_y = norm_to_device_pixels(norm, prof)

    out = {
        "profile": prof.name,
        "device_pixels": [prof.pixel_w, prof.pixel_h],
        "tap_space": prof.tap_space,
        "scale": prof.scale,
        "logical_size": [prof.logical_w, prof.logical_h],
        "norm": [round(norm[0], 6), round(norm[1], 6)],
        "tap": [tap_x, tap_y],
        "device_pixel": [px_x, px_y],
    }
    if content is not None:
        out["content_rect"] = content.as_list()
        out["window_pixel"] = list(norm_to_window(norm, content))
        if detect_info:
            out["content_rect_source"] = detect_info

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"机型      : {prof.name}  {prof.pixel_w}x{prof.pixel_h} dpi={prof.dpi} "
          f"scale={prof.scale:g}")
    print(f"输入空间  : {prof.tap_space}  逻辑尺寸 {prof.logical_w:g}x{prof.logical_h:g}")
    print(f"归一化    : ({norm[0]:.4f}, {norm[1]:.4f})")
    print(f"设备像素  : ({px_x}, {px_y})   ← 截图坐标系")
    if prof.tap_space == "points":
        print(f"输入坐标  : ({tap_x:g}, {tap_y:g})   ← points = pixels / {prof.scale:g}")
    else:
        print(f"输入坐标  : ({tap_x:g}, {tap_y:g})   ← adb tap 用物理像素,与截图 1:1")
    if content is not None:
        print(f"画面矩形  : x={content.x:g} y={content.y:g} "
              f"w={content.w:g} h={content.h:g}"
              + (f"   ({detect_info.get('method')})" if detect_info else ""))
        wx, wy = norm_to_window(norm, content)
        print(f"窗口像素  : ({wx}, {wy})   ← computer-use 点击用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
