"""Hằng số cho Camera AI (Frigate + Immich)."""
DOMAIN = "camera_ai"

# --- cấu hình (config entry data/options) ---
CONF_FRIGATE_URL = "frigate_url"          # API nội bộ, vd http://192.168.0.195:5000
CONF_FRIGATE_UI = "frigate_ui"            # giao diện web, vd https://192.168.0.195:8971
CONF_NOTIFY = "notify_service"
CONF_IMMICH_URL = "immich_url"
CONF_IMMICH_KEY = "immich_api_key"
CONF_IMMICH_UI = "immich_ui"              # link mở ảnh, vd https://photos.bkascript.com
CONF_NAS_LINK = "nas_link"                # link thư mục clip trên NAS
CONF_LABELS = "labels"
CONF_PERSONS = "persons"                  # person.* dùng để xét "vắng nhà"
CONF_NIGHT_START = "night_start"
CONF_NIGHT_END = "night_end"
CONF_COOLDOWN = "cooldown"                # giây giữa 2 thông báo của cùng camera
CONF_CAMERA_NAMES = "camera_names"        # "san_them=Sân - Thềm, cong=Cổng"
CONF_FACES_PER_PERSON = "faces_per_person"
CONF_KNOWN_QUIET = "known_quiet"          # người quen: gửi thông báo im lặng

DEFAULT_FRIGATE_URL = "http://192.168.0.195:5000"
DEFAULT_FRIGATE_UI = "https://192.168.0.195:8971"
DEFAULT_IMMICH_URL = "http://192.168.0.237:2283"
DEFAULT_IMMICH_UI = "https://photos.bkascript.com"
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

# --- chế độ thông báo (select entity) ---
MODE_ALWAYS = "Mọi lúc"
MODE_NIGHT = "Chỉ ban đêm"
MODE_AWAY = "Khi vắng nhà"
MODE_NIGHT_OR_AWAY = "Ban đêm hoặc vắng nhà"
MODE_OFF = "Tắt"
MODES = [MODE_ALWAYS, MODE_NIGHT, MODE_AWAY, MODE_NIGHT_OR_AWAY, MODE_OFF]
DEFAULT_MODE = MODE_NIGHT_OR_AWAY

# --- nút trong thông báo ---
ACTION_SNOOZE = "CAMERA_AI_SNOOZE_60"

MAX_LOG = 300
PANEL_PATH = "camera-ai"
