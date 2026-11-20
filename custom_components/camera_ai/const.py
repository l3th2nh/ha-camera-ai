"""Hằng số cho Camera AI (Frigate + Immich)."""
DOMAIN = "camera_ai"

# --- cấu hình (config entry data/options) ---
CONF_FRIGATE_URL = "frigate_url"          # API nội bộ, vd http://192.168.0.195:5000
CONF_FRIGATE_UI = "frigate_ui"            # giao diện web, vd https://192.168.0.195:8971
CONF_NOTIFY = "notify_service"
CONF_IMMICH_URL = "immich_url"
CONF_IMMICH_KEY = "immich_api_key"
CONF_IMMICH_UI = "immich_ui"              # link mở ảnh, vd https://photos.bkascript.com
CONF_IMMICH_APP = "immich_app"            # link mở APP trên điện thoại (immich://)
CONF_NAS_LINK = "nas_link"                # link thư mục clip trên NAS
CONF_LABELS = "labels"
CONF_NIGHT_START = "night_start"
CONF_NIGHT_END = "night_end"
CONF_COOLDOWN = "cooldown"                # giây giữa 2 thông báo của cùng camera
CONF_CAMERA_NAMES = "camera_names"        # "san_them=Sân - Thềm, cong=Cổng"
CONF_FACES_PER_PERSON = "faces_per_person"
CONF_KNOWN_QUIET = "known_quiet"          # người quen: gửi thông báo im lặng
CONF_DOORS = "doors"                      # cửa cuốn nhận diện bằng hình (mỗi dòng 1 cửa)
CONF_DOOR_CAMERA = "door_camera"          # camera Frigate nhìn thấy các cửa
CONF_DOOR_NOTIFY = "door_notify"          # gửi thông báo khi cửa (camera) đóng/mở

DEFAULT_FRIGATE_URL = "http://192.168.0.195:5000"
DEFAULT_FRIGATE_UI = "https://192.168.0.195:8971"
DEFAULT_IMMICH_URL = "http://192.168.0.237:2283"
DEFAULT_IMMICH_UI = "https://photos.bkascript.com"
DEFAULT_IMMICH_APP = "immich://"   # scheme app Immich iOS/Android (Info.plist CFBundleURLSchemes)
DEFAULT_NAS_LINK = "https://nas.bkascript.com"
DEFAULT_LABELS = ["person"]
ALL_LABELS = ["person", "car", "motorcycle", "bicycle", "dog", "cat"]
LABEL_VI = {
    "person": "Người", "car": "Ô tô", "motorcycle": "Xe máy",
    "bicycle": "Xe đạp", "dog": "Chó", "cat": "Mèo",
}
DEFAULT_NIGHT_START = "18:30:00"
DEFAULT_NIGHT_END = "06:00:00"
DEFAULT_COOLDOWN = 120
DEFAULT_FACES_PER_PERSON = 15
# Tọa độ đo trên khung 1280x720 của camera sân (2026-11-20). Cửa B bị ô tô che chân → chỉ dùng phần trên.
DEFAULT_DOORS = (
    "Cửa cuốn A | 805,32,805,238,930,350,992,42 | 0-1 | binary_sensor.zigbee_door_contact_cua\n"
    "Cửa cuốn B | 1142,100,1015,465,1200,640,1280,62 | 0-0.6 |"
)
DEFAULT_DOOR_CAMERA = "san_them"

# --- chế độ thông báo (select entity) ---
MODE_ALWAYS = "Mọi lúc"
MODE_NIGHT = "Chỉ ban đêm"
MODE_OFF = "Tắt"
MODES = [MODE_ALWAYS, MODE_NIGHT, MODE_OFF]
DEFAULT_MODE = MODE_NIGHT

# --- nút trong thông báo ---
ACTION_SNOOZE = "CAMERA_AI_SNOOZE_60"

MAX_LOG = 300
PANEL_PATH = "camera-ai"
