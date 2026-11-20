# Camera AI — Frigate cho Home Assistant (thông báo · khuôn mặt Immich · nhật ký)

Tích hợp HACS quản lý phần **Frigate** trong Home Assistant:

- 🔔 **Thông báo kèm ảnh** khi Frigate có cảnh báo (người, ô tô, xe máy…), ghi **tên người** nếu nhận ra mặt
  (`👤 Mẹ Tâm — 21:05`), người lạ thì báo ưu tiên cao (`⚠️ Người lạ — 02:14`). Nhận ra mặt muộn → thông báo được
  **thay bằng tên đúng** (cùng `tag`).
- 🎚️ **Chế độ thông báo** (entity `select.camera_ai_che_do_thong_bao` + panel):
  **Mọi lúc · Chỉ ban đêm · Tắt**, kèm **tạm tắt 1h/4h** (cả nút ngay trong
  thông báo) và thời gian chờ giữa 2 lần báo.
- 🙂 **Đồng bộ khuôn mặt từ Immich**: lấy ảnh mặt rõ nhất của các người **có tên** trong Immich → nạp vào thư viện
  face recognition của Frigate (nút / dịch vụ `camera_ai.sync_faces`).
- 🎬 **Tab "Xem lại"**: danh sách lượt ghi (cảnh báo / phát hiện) từ Frigate, bấm là **phát ngay trong HA** — clip được
  chuẩn hoá cho iPhone (âm thanh AAC 44.1kHz, faststart) và đệm trên HA (~1.5GB).
- 🗂️ **Panel "Camera AI"** trên sidebar: xem trực tiếp, chế độ, nhật ký cảnh báo kèm ảnh (đã báo / lý do không báo),
  thư viện khuôn mặt, liên kết nhanh tới Frigate · Immich · NAS.

- 🚪 **Cửa cuốn đóng/mở bằng camera** (không cần cảm biến): mỗi cửa là 1 vùng tứ giác trên ảnh camera. Tích hợp
  so cấu trúc ảnh với **kho mẫu "cửa đóng"** tự cập nhật theo ánh sáng (ngày/đêm hồng ngoại) → `binary_sensor`
  (garage_door) + `sensor` % mở + sự kiện `camera_ai_door` cho automation + thông báo (tùy chọn). Panel vẽ viền cửa
  lên ảnh trực tiếp và có nút *Đặt lại mẫu* (bấm khi cửa đang đóng).

## Yêu cầu
- Frigate ≥ 0.14 (đã thử 0.18) có MQTT, bật `face_recognition` nếu muốn gắn tên.
- Tích hợp **Frigate** (HACS `blakeblackshear/frigate-hass-integration`) — dùng proxy ảnh thông báo của nó.
- MQTT trong HA, app **Home Assistant Companion** để nhận thông báo.
- (Tùy chọn) Immich + API key (quyền đọc người & ảnh).

## Cài đặt
1. HACS → ⋮ → *Custom repositories* → `https://github.com/l3th2nh/ha-camera-ai` (Integration) → Download.
2. Khởi động lại HA → *Cài đặt → Thiết bị & dịch vụ → Thêm tích hợp → Camera AI*.
3. Nhập Frigate API (`http://<frigate>:5000`), link giao diện Frigate, dịch vụ notify, Immich URL + API key.
4. *Cấu hình* (Options): loại đối tượng cần báo, giờ đêm, thời gian chờ, tên hiển thị camera (`san_them=Sân - Thềm`), số ảnh mặt mỗi người.

## Dịch vụ
| Dịch vụ | Mô tả |
|---|---|
| `camera_ai.set_mode` | Đặt chế độ thông báo |
| `camera_ai.snooze` | Tạm tắt N phút (`0` = bật lại) |
| `camera_ai.sync_faces` | Đồng bộ mặt từ Immich (`people`, `per_person` tùy chọn) |
| `camera_ai.test_notify` | Gửi thông báo thử |

## Ghi chú
- Ảnh trong Immich là ảnh điện thoại (chính diện, đủ sáng) — camera an ninh nhìn từ trên xuống / hồng ngoại ban đêm
  khác nhiều. Nên bổ sung vài ảnh mặt từ chính camera qua tab **Faces → Train** của Frigate.
- Không lưu mật khẩu trong repo; API key Immich nằm trong config entry của HA.

## Cửa cuốn bằng camera — cấu hình
*Cấu hình* → ô **Cửa cuốn**, mỗi dòng 1 cửa:
```
Tên | x1,y1,x2,y2,x3,y3,x4,y4 | dải hàng | cảm biến (tùy chọn)
Cửa chính | 805,32,805,238,930,350,992,42 | 0-1 | binary_sensor.zigbee_door_contact_cua
Cửa phụ | 1142,100,1015,465,1200,640,1280,62 | 0-0.6 |
```
- Toạ độ trên khung **1280×720**, thứ tự góc: trên-trái, dưới-trái, dưới-phải, trên-phải của cánh cửa.
- *Dải hàng* `0-0.6` = chỉ dùng 60% phía trên (khi chân cửa hay bị xe che).
- Sự kiện cho automation: `camera_ai_door` với `door`, `open` (true/false), `frac`.
