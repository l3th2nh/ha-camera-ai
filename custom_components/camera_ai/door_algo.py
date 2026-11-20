"""Thuật toán nhận diện đóng/mở cửa cuốn — THUẦN numpy/Pillow (không phụ thuộc HA → chạy thử/mô phỏng được).

Mỗi khung:
  1. `rectify`: nắn tứ giác cửa (ảnh 1280x720) thành chữ nhật RW×RH, lấy dải hàng `band`.
  2. `features`: ảnh xám làm mờ nhẹ, chia cho độ sáng trung bình (bù nắng/râm toàn cục).
  3. So với KHO MẪU "đóng" (nhiều mẫu, nhiều điều kiện sáng) — chọn mẫu giống nhất; mỗi hàng lấy TRUNG VỊ
     theo chiều ngang (người đứng che < nửa cửa không ảnh hưởng). Đếm số hàng khác LIÊN TIẾP từ chân cửa lên
     → `frac` ≈ phần cửa đã mở (cửa cuốn kéo từ dưới lên).
  4. Trễ + chống dội (CONFIRM lần đo). Khi đóng & khung đứng yên → làm mới mẫu gần nhất (trung bình trượt)
     và thỉnh thoảng thêm mẫu mới (điều kiện sáng mới). Đổi chế độ ngày↔đêm (IR) khi đang đóng → gieo mẫu mới.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

FRAME_W, FRAME_H = 1280, 720
RW, RH = 48, 96
NIGHT_SAT = 6.0

# Tham số chốt bằng mô phỏng CHÍNH code này trên bản ghi thật, nhịp 4s (2026-11-20):
#   18/11 06:00–11:00 + 19/11 17:30–19:30 → cửa A bắt 7/7 lần mở (đáp án = cảm biến Zigbee), 0 báo nhầm;
#   cửa B bắt 5/5 (gán nhãn tay), 0 báo nhầm. "hp" (cấu trúc) kém nhạy hơn hẳn (A chỉ 2/7) → dùng "int".
PARAMS = {
    "feature": "int",    # "int" = độ sáng chuẩn hoá (đã chọn) | "hp" = cấu trúc (thử nghiệm)
    "row_t": 0.10,       # ngưỡng 1 hàng "khác"
    "open_t": 0.10,      # frac ≥ → MỞ
    "close_t": 0.05,     # frac ≤ → ĐÓNG
    "confirm": 3,        # số lần đo liên tiếp để đổi trạng thái (2s/lần → ~6s)
    "alpha": 0.05,       # tốc độ làm mới mẫu khi đang đóng
    "stable_t": 0.025,   # khung "đứng yên" nếu trung vị |khung - khung trước| < ngưỡng
    "bank_max": 30,      # số mẫu tối đa mỗi chế độ sáng
    "bank_every": 1800,  # giây — tối thiểu giữa 2 lần thêm mẫu mới
}


def frame_mode(im: Image.Image) -> str:
    small = np.asarray(im.resize((160, 90)).convert("RGB")).astype(np.float32)
    return "night" if (small.max(axis=2) - small.min(axis=2)).mean() < NIGHT_SAT else "day"


def rectify(im: Image.Image, quad, band=(0.0, 1.0)) -> np.ndarray:
    """Trả ảnh xám uint8 RH×RW đã nắn (cả cửa); `band` áp dụng ở `features`."""
    if im.size != (FRAME_W, FRAME_H):
        im = im.resize((FRAME_W, FRAME_H))
    return np.asarray(im.transform((RW, RH), Image.QUAD, tuple(quad), Image.BILINEAR).convert("L"))


def features(raw: np.ndarray, band=(0.0, 1.0), feature: str | None = None) -> np.ndarray:
    feature = feature or PARAMS["feature"]
    im = Image.fromarray(raw)
    g = np.asarray(im.filter(ImageFilter.GaussianBlur(1))).astype(np.float32)
    if feature == "hp":
        lo = np.asarray(im.filter(ImageFilter.GaussianBlur(4))).astype(np.float32)
        g = g - lo
        g = g / (float(np.std(g)) + 2.0)
    else:
        g = g / (float(g.mean()) + 1.0)
    h0 = int(RH * band[0])
    h1 = max(h0 + 4, int(RH * band[1]))
    return g[h0:h1]


def frac_from_rows(row: np.ndarray, t: float) -> float:
    hit = row > t
    n = miss = 0
    for v in hit[::-1]:          # từ chân cửa lên, cho phép hở 2 hàng
        if v:
            n += 1 + miss
            miss = 0
        else:
            miss += 1
            if miss > 2:
                break
    return n / len(row)


class DoorTracker:
    """Trạng thái 1 cửa. `update(g, mode, ts)` → (is_open|None, frac)."""

    def __init__(self, params: dict | None = None) -> None:
        self.p = {**PARAMS, **(params or {})}
        self.bank: dict[str, list[np.ndarray]] = {"day": [], "night": []}
        self.is_open: bool | None = None
        self.frac = 0.0
        self.mode: str | None = None
        self.prev: np.ndarray | None = None
        self.pending: bool | None = None
        self.pending_n = 0
        self.last_add = {"day": 0.0, "night": 0.0}
        self.ref_updated: float | None = None

    # ----- lưu / nạp -----
    def dump(self) -> dict:
        return {"bank": {m: [np.round(a, 3).tolist() for a in v] for m, v in self.bank.items()},
                "is_open": self.is_open}

    def load(self, d: dict) -> None:
        for m, lst in (d.get("bank") or {}).items():
            self.bank[m] = [np.array(a, dtype=np.float32) for a in lst if len(a) and len(a[0]) == RW]
        self.is_open = d.get("is_open")

    def seed(self, g: np.ndarray, mode: str, ts: float) -> None:
        """Người dùng xác nhận cửa đang ĐÓNG → mẫu mới cho chế độ sáng hiện tại."""
        self.bank[mode] = [g.copy()] + [b for b in self.bank[mode] if b.shape == g.shape][: self.p["bank_max"] - 1]
        self.last_add[mode] = ts
        self.ref_updated = ts
        self.is_open, self.frac, self.pending, self.pending_n = False, 0.0, None, 0

    # ----- mỗi khung -----
    def update(self, g: np.ndarray, mode: str, ts: float, sensor_closed: bool | None = None) -> tuple[bool | None, float]:
        p = self.p
        switched = self.mode is not None and mode != self.mode
        self.mode = mode
        stable = self.prev is not None and self.prev.shape == g.shape and \
            float(np.median(np.abs(g - self.prev))) < p["stable_t"]
        self.prev = g
        bank = [b for b in self.bank.get(mode, []) if b.shape == g.shape]
        self.bank[mode] = bank

        # Chưa có mẫu cho chế độ này, hoặc vừa đổi ngày↔đêm khi đang đóng → gieo mẫu
        if not bank or (switched and self.is_open is False and not self._matches(g, bank)):
            if sensor_closed is True or (sensor_closed is None and self.is_open is not True):
                self.seed(g, mode, ts)
            return self.is_open, self.frac

        rows = [np.median(np.abs(g - b), axis=1) for b in bank]
        k = int(np.argmin([r.mean() for r in rows]))
        frac = frac_from_rows(rows[k], p["row_t"])
        self.frac = frac
        raw = True if frac >= p["open_t"] else False if frac <= p["close_t"] else self.is_open
        if raw is None:
            raw = False
        if raw == self.is_open:
            self.pending, self.pending_n = None, 0
        elif raw == self.pending:
            self.pending_n += 1
            if self.pending_n >= p["confirm"]:
                self.is_open, self.pending, self.pending_n = raw, None, 0
        else:
            self.pending, self.pending_n = raw, 1
            if p["confirm"] <= 1:
                self.is_open, self.pending, self.pending_n = raw, None, 0

        if self.is_open is False and frac <= p["close_t"] and stable:
            bank[k] = (1 - p["alpha"]) * bank[k] + p["alpha"] * g
            self.ref_updated = ts
            if ts - self.last_add[mode] > p["bank_every"] and rows[k].mean() > p["row_t"] * 0.4:
                bank.append(g.copy())
                self.last_add[mode] = ts
                del bank[: -p["bank_max"]]
        return self.is_open, frac

    def _matches(self, g, bank) -> bool:
        rows = [np.median(np.abs(g - b), axis=1) for b in bank]
        return frac_from_rows(min(rows, key=lambda r: r.mean()), self.p["row_t"]) <= self.p["close_t"]

    @property
    def pct(self) -> int:
        return int(round(min(1.0, self.frac) * 100)) if self.is_open else 0
