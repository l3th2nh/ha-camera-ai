/*
 * Camera AI — panel sidebar (native HA, Shadow DOM).
 *   · Xem trực tiếp (ảnh camera làm mới 2s)
 *   · Chế độ thông báo: Mọi lúc / Chỉ ban đêm / Tắt + tạm tắt
 *   · Liên kết: Frigate · Immich · NAS · Cài đặt
 *   · Thư viện khuôn mặt (Frigate) + đồng bộ từ Immich
 *   · Nhật ký cảnh báo kèm ảnh, tên người, đã báo / lý do không báo
 * WebSocket: camera_ai/state, set_mode, snooze, sync_faces, test_notify, clear_log
 */
const STYLE = `
<style>
:host{--bg:#14161c;--panel:#1d2029;--panel-2:#242833;--line:rgba(255,255,255,.08);--line-strong:rgba(255,255,255,.14);
  --text:#f2f4f8;--muted:#9aa4b6;--faint:#6b7385;--accent:#6aa9ff;--ok:#5fd29a;--warn:#ffb24c;--bad:#f0888a;--radius:16px;
  --font:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;--mono:ui-monospace,Menlo,monospace;
  display:block;min-height:100vh;color:var(--text);font-family:var(--font);
  background:radial-gradient(1000px 500px at 80% -10%,rgba(106,169,255,.10),transparent 60%),var(--bg)}
*{box-sizing:border-box}
.wrap{max-width:900px;margin:0 auto;padding:16px 16px 80px}
.top{display:flex;align-items:center;gap:12px;margin-bottom:14px}
.menu{width:42px;height:42px;border-radius:12px;flex:none;background:var(--panel);border:1px solid var(--line);color:var(--muted);
  font-size:20px;display:grid;place-items:center;cursor:pointer}
h1{font-weight:700;font-size:20px;margin:0}
h2{font-size:14px;margin:0 0 10px;color:var(--muted);font-weight:600;letter-spacing:.02em;text-transform:uppercase}
.sub{font-size:12px;color:var(--faint)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:14px;margin:0 0 12px}
.live{position:relative;border-radius:12px;overflow:hidden;background:#000;aspect-ratio:16/9}
.live img{width:100%;height:100%;object-fit:contain;display:block}
.live .tag{position:absolute;left:10px;top:10px;background:rgba(0,0,0,.55);padding:4px 9px;border-radius:8px;font-size:12px}
.camsel{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{padding:9px 13px;border-radius:11px;font-size:13px;cursor:pointer;border:1px solid var(--line-strong);background:var(--panel-2);
  color:var(--text);font-family:inherit}
.chip.on{background:color-mix(in srgb,var(--accent) 22%,var(--panel-2));border-color:var(--accent);font-weight:600}
.status{margin-top:10px;font-size:13px;color:var(--muted);display:flex;gap:14px;flex-wrap:wrap}
.status b.ok{color:var(--ok)} .status b.no{color:var(--warn)}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.btn{display:inline-flex;align-items:center;gap:6px;padding:9px 13px;border-radius:11px;font-size:13px;font-weight:500;cursor:pointer;
  border:1px solid var(--line-strong);background:var(--panel-2);color:var(--text);font-family:inherit;text-decoration:none}
.btn:hover{border-color:var(--accent)} .btn.danger{color:var(--bad)}
.btn[disabled]{opacity:.5;cursor:wait}
.faces{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px}
.face{color:var(--text);text-decoration:none;background:var(--panel-2);border-radius:10px;padding:8px 10px;font-size:13px;display:flex;justify-content:space-between;gap:6px}
.face span{color:var(--faint);font-family:var(--mono)}
.prog{font-family:var(--mono);font-size:12px;color:var(--faint);margin-top:8px;white-space:pre-wrap}
.ev{display:flex;gap:12px;align-items:center;padding:10px 0;border-top:1px solid var(--line);cursor:pointer}
.ev:first-of-type{border-top:0}
.ev img{width:96px;height:64px;object-fit:cover;border-radius:9px;background:var(--panel-2);flex:none}
.ev .t{font-family:var(--mono);font-size:13px;color:var(--muted)}
.ev .d{font-size:14.5px;font-weight:600;margin:2px 0}
.badge{font-size:11.5px;padding:2px 8px;border-radius:7px;background:var(--panel-2);color:var(--faint)}
.badge.ok{color:var(--ok)} .badge.stranger{color:var(--bad)}
.day{margin:14px 0 4px;font-size:12px;color:var(--faint);font-family:var(--mono)}
.empty{color:var(--faint);font-size:13px;padding:8px 0}
.err{color:var(--bad);font-size:12.5px;margin-top:6px}
</style>`;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LBL = { person: "Người", car: "Ô tô", motorcycle: "Xe máy", bicycle: "Xe đạp", dog: "Chó", cat: "Mèo" };
const fmtT = (ts) => new Date(ts * 1000).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const fmtD = (ts) => new Date(ts * 1000).toLocaleDateString("vi-VN", { weekday: "long", day: "2-digit", month: "2-digit" });

class CameraAIPanel extends HTMLElement {
  set hass(h) { this._hass = h; if (this._built && !this._ready) this._init(); if (this._ready) this._renderLive(); }
  set narrow(_n) {} set route(_r) {} set panel(_p) {}

  connectedCallback() {
    if (this._built) return;
    this._built = true;
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = STYLE + `<div class="wrap">
      <div class="top"><div class="menu" id="menu">☰</div><div><h1>Camera AI</h1><div class="sub" id="sub">Đang tải…</div></div></div>
      <div class="card"><div class="live"><img id="live" alt=""><div class="tag" id="camtag"></div></div><div class="camsel" id="camsel"></div></div>
      <div class="card"><h2>Chế độ thông báo</h2><div class="chips" id="modes"></div><div class="status" id="status"></div>
        <div class="row"><button class="btn" data-snz="60">🔕 Tạm tắt 1 giờ</button><button class="btn" data-snz="240">🔕 4 giờ</button>
        <button class="btn" data-snz="0">🔔 Bật lại</button><button class="btn" id="test">🧪 Thông báo thử</button></div>
        <div class="err" id="nerr"></div></div>
      <div class="card"><h2>Liên kết</h2><div class="row" id="links"></div></div>
      <div class="card"><h2>Thư viện khuôn mặt (Frigate)</h2><div class="faces" id="faces"></div>
        <div class="row"><button class="btn" id="sync">🔄 Đồng bộ từ Immich</button></div><div class="prog" id="prog"></div></div>
      <div class="card"><h2 style="display:flex;justify-content:space-between">Nhật ký cảnh báo <a class="btn danger" id="clear" style="padding:3px 9px">Xoá</a></h2><div id="events"></div></div>
    </div>`;
    this.$ = (s) => this.shadowRoot.querySelector(s);
    this.$("#menu").onclick = () => this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }));
    this.shadowRoot.querySelectorAll("[data-snz]").forEach((b) => (b.onclick = () => this._ws("camera_ai/snooze", { minutes: +b.dataset.snz })));
    this.$("#test").onclick = async () => { const r = await this._ws("camera_ai/test_notify"); this.$("#nerr").textContent = r && !r.ok ? "Lỗi: " + (r.err || "") : "Đã gửi ✔"; };
    this.$("#sync").onclick = () => this._ws("camera_ai/sync_faces");
    this.$("#clear").onclick = () => confirm("Xoá toàn bộ nhật ký?") && this._ws("camera_ai/clear_log");
    if (this._hass) this._init();
  }
  disconnectedCallback() { clearInterval(this._poll); clearInterval(this._liveT); this._ready = false; }

  _init() {
    this._ready = true;
    this._cam = this._cam || "san_them";
    this._load();
    this._poll = setInterval(() => this._load(), 5000);
    this._liveT = setInterval(() => this._renderLive(true), 2000);
  }
  async _ws(type, extra = {}) {
    try { const r = await this._hass.callWS({ type, ...extra }); this._load(); return r; } catch (e) { console.error(e); return { ok: false, err: e.message }; }
  }
  async _load() {
    try { this._d = await this._hass.callWS({ type: "camera_ai/state" }); this._render(); }
    catch (e) { this.$("#sub").textContent = "Lỗi tải: " + e.message; }
  }
  _renderLive(bust) {
    const st = this._hass?.states?.[`camera.${this._cam}`];
    const img = this.$("#live");
    if (!st || !img) return;
    const pic = st.attributes.entity_picture;
    if (pic && (bust || !img.src)) img.src = pic + (pic.includes("?") ? "&" : "?") + "t=" + Date.now();
    this.$("#camtag").textContent = "● " + (st.attributes.friendly_name || this._cam);
  }
  _render() {
    const d = this._d; if (!d) return;
    const snz = d.snooze_until ? new Date(d.snooze_until) : null;
    this.$("#sub").textContent = d.allowed_now ? "Đang gửi thông báo" : `Không gửi: ${d.blocked_reason}`;
    this.$("#modes").innerHTML = d.modes.map((m) => `<button class="chip ${m === d.mode ? "on" : ""}" data-m="${esc(m)}">${esc(m)}</button>`).join("");
    this.$("#modes").querySelectorAll("[data-m]").forEach((b) => (b.onclick = () => this._ws("camera_ai/set_mode", { mode: b.dataset.m })));
    this.$("#status").innerHTML = `<span>Hiện tại: <b class="${d.allowed_now ? "ok" : "no"}">${d.allowed_now ? "ĐANG BÁO" : "KHÔNG BÁO"}</b></span>
      <span>🌙 Đêm ${esc(d.night)}: <b>${d.is_night ? "đang là đêm" : "ban ngày"}</b></span>
      ${snz ? `<span>🔕 Tạm tắt tới <b>${snz.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })}</b></span>` : ""}
      <span>Báo khi thấy: ${d.labels.map((l) => LBL[l] || l).join(", ")}</span>`;
    const nl = d.notify_last;
    this.$("#nerr").textContent = nl && !nl.ok ? `Thông báo gần nhất lỗi: ${nl.err || ""}` : (d.notify_service ? "" : "Chưa chọn dịch vụ thông báo (Cài đặt).");
    const L = d.links || {};
    // Điện thoại có app Immich → link immich:// (thẻ <a> thường, iOS chỉ mở app khi là cú chạm trực tiếp)
    const mobile = /iPhone|iPad|iPod|Android|Home Assistant/i.test(navigator.userAgent);
    const useApp = mobile && !!L.immich_app;
    const linkAttrs = (u) => /^(https?:)?\/\//i.test(u) ? `href="${esc(u)}" target="_blank" rel="noopener"` : `href="${esc(u)}"`;
    const personUrl = (id) => !id ? "" : useApp ? `${L.immich_app.split(":")[0]}://people?id=${encodeURIComponent(id)}`
      : L.immich ? `${L.immich}/people/${encodeURIComponent(id)}` : "";
    this._personUrl = personUrl; this._linkAttrs = linkAttrs;
    this.$("#links").innerHTML = [
      [L.frigate, "🎥 Frigate"], [L.frigate && L.frigate + "/review", "🗂️ Xem lại (Frigate)"], [L.frigate && L.frigate + "/faces", "🙂 Khuôn mặt / Train"],
      ...(useApp
        ? [[L.immich_app, "🖼️ Immich (app)"], [L.immich && L.immich + "/people", "🌐 Immich web"]]
        : [[L.immich && L.immich + "/people", "🖼️ Immich – Mọi người"]]),
      [L.nas, "💾 NAS (clip cảnh báo)"], ["/config/integrations/integration/camera_ai", "⚙️ Cài đặt"],
    ].filter((x) => x[0]).map(([u, t]) => `<a class="btn" ${linkAttrs(u)}>${t}</a>`).join("");
    const faces = Object.entries(d.faces || {}).sort((a, b) => a[0].localeCompare(b[0], "vi"));
    const ppl = d.immich_people || {};
    this.$("#faces").innerHTML = faces.length ? faces.map(([n, c]) => {
      const u = personUrl(ppl[n]);
      return u ? `<a class="face" ${linkAttrs(u)}>${esc(n)}<span>${c} ›</span></a>` : `<div class="face">${esc(n)}<span>${c}</span></div>`;
    }).join("") : '<div class="empty">Chưa có khuôn mặt nào.</div>';
    const fs = d.face_sync;
    this.$("#sync").disabled = !!d.sync_running;
    this.$("#sync").textContent = d.sync_running ? "⏳ Đang đồng bộ…" : "🔄 Đồng bộ từ Immich";
    this.$("#prog").textContent = d.sync_running ? (d.sync_progress || []).join("\n")
      : fs ? (fs.ok ? `Lần gần nhất ${new Date(fs.at).toLocaleString("vi-VN")}: ${fs.people} người, thêm ${fs.added} ảnh, lỗi ${fs.failed}` : `Lỗi: ${fs.err}`) : "";
    const cams = [...new Set([this._cam, ...(d.events || []).map((e) => e.camera)])].filter(Boolean);
    this.$("#camsel").innerHTML = cams.length > 1 ? cams.map((c) => `<button class="chip ${c === this._cam ? "on" : ""}" data-c="${esc(c)}">${esc(c)}</button>`).join("") : "";
    this.$("#camsel").querySelectorAll("[data-c]").forEach((b) => (b.onclick = () => { this._cam = b.dataset.c; this.$("#live").src = ""; this._render(); this._renderLive(true); }));
    this._renderEvents(d);
  }
  _renderEvents(d) {
    const evs = d.events || [];
    if (!evs.length) { this.$("#events").innerHTML = '<div class="empty">Chưa có cảnh báo nào kể từ khi cài.</div>'; return; }
    let html = "", lastDay = "";
    for (const e of evs) {
      const day = e.start ? fmtD(e.start) : "";
      if (day !== lastDay) { html += `<div class="day">${esc(day)}</div>`; lastDay = day; }
      const person = (e.labels || []).includes("person");
      const who = person ? (e.names?.length ? "👤 " + e.names.join(", ") : "⚠️ Người lạ") : "";
      const others = (e.labels || []).filter((l) => l !== "person").map((l) => LBL[l] || l).join(", ");
      const desc = [who, others].filter(Boolean).join(" · ") || "Cảnh báo";
      const img = e.event ? `/api/frigate/notifications/${encodeURIComponent(e.event)}/thumbnail.jpg` : "";
      const badge = e.notified ? '<span class="badge ok">đã báo</span>' : `<span class="badge">${esc(e.reason || (e.end ? "không báo" : "đang diễn ra"))}</span>`;
      html += `<div class="ev" data-r="${esc(e.id)}">${img ? `<img loading="lazy" src="${img}" alt="">` : '<img alt="">'}
        <div><div class="t">${e.start ? fmtT(e.start) : ""} · ${esc(e.camera_label || e.camera)}</div>
        <div class="d">${esc(desc)}</div>${badge} ${person && !e.names?.length ? '<span class="badge stranger">chưa nhận ra</span>' : ""}</div></div>`;
    }
    this.$("#events").innerHTML = html;
    const ui = d.links?.frigate;
    this.$("#events").querySelectorAll("[data-r]").forEach((el) => (el.onclick = () => ui && window.open(`${ui}/review?id=${encodeURIComponent(el.dataset.r)}`, "_blank")));
  }
}
if (!customElements.get("camera-ai-panel")) customElements.define("camera-ai-panel", CameraAIPanel);
