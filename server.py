"""
========================================================
  TELEGRAM STUDY PORTAL — Pyrogram + Flask
  No MongoDB — JSON file index, 1GB+ streaming
========================================================
  Env vars set karo (Render/Koyeb):
    API_ID, API_HASH, BOT_TOKEN, CHANNELS
========================================================
"""
import os, json, asyncio, re, time
from pathlib import Path
from flask import Flask, jsonify, Response, send_from_directory, request, stream_with_context
from pyrogram import Client
from pyrogram.errors import FloodWait

# ── CONFIG ──────────────────────────────────────────────────
API_ID    = int(os.environ.get("API_ID", "0"))
API_HASH  = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNELS  = [c.strip() for c in os.environ.get("CHANNELS", "").split(",") if c.strip()]
PORT      = int(os.environ.get("PORT", 5000))
INDEX_FILE = "index.json"

app = Flask(__name__, static_folder="static")

# ── PYROGRAM ─────────────────────────────────────────────────
_client = None
_loop   = None

def get_loop():
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop

def get_client():
    global _client
    if _client and _client.is_connected:
        return _client
    _client = Client(
        "bot_session",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        no_updates=True,
    )
    get_loop().run_until_complete(_client.start())
    return _client

def run(coro):
    return get_loop().run_until_complete(coro)

# ── INDEX ─────────────────────────────────────────────────────
def load_index():
    if Path(INDEX_FILE).exists():
        return json.loads(Path(INDEX_FILE).read_text(encoding="utf-8"))
    return {"channels": [], "last_updated": None}

def save_index(d):
    Path(INDEX_FILE).write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

# ── SCAN ──────────────────────────────────────────────────────
async def scan_channel(client, channel_id, keywords=[]):
    try:
        chat = await client.get_chat(channel_id)
        chat_name, chat_id = chat.title or channel_id, chat.id
    except Exception as e:
        return {"id": channel_id, "name": channel_id, "chat_id": channel_id, "error": str(e), "folders": []}

    folders = {}
    async for msg in client.get_chat_history(chat_id, limit=1000):
        file_obj = file_type = None
        if msg.video:
            file_obj, file_type = msg.video, "video"
        elif msg.document:
            mt = msg.document.mime_type or ""
            if "pdf" in mt:   file_obj, file_type = msg.document, "pdf"
            elif "video" in mt: file_obj, file_type = msg.document, "video"
        if not file_obj: continue

        caption = (msg.caption or msg.text or "").strip()
        folder  = "General"
        hm = re.search(r"#(\w+)", caption)
        if hm:
            folder = hm.group(1)
        else:
            fl = caption.split("\n")[0].strip().replace("*","").replace("_","")
            if fl and len(fl) < 60: folder = fl
        for kw in keywords:
            if kw.lower() in caption.lower():
                folder = kw.capitalize(); break

        if folder not in folders: folders[folder] = []
        seq = len(folders[folder]) + 1
        name = caption.split("\n")[0].strip() or getattr(file_obj,"file_name",None) or f"{file_type.upper()} {seq}"
        sz   = getattr(file_obj,"file_size",0) or 0
        dur  = getattr(file_obj,"duration",None)

        folders[folder].append({
            "seq": seq, "name": name[:120], "type": file_type,
            "size": fmt_size(sz), "size_b": sz,
            "duration": fmt_dur(dur) if dur else None,
            "file_id": file_obj.file_id,
            "msg_id": msg.id, "chat_id": chat_id,
            "date": msg.date.isoformat() if msg.date else None,
        })
        await asyncio.sleep(0.03)

    return {
        "id": channel_id, "name": chat_name, "chat_id": chat_id, "emoji": "📡",
        "folders": [{"name": k, "files": v} for k, v in folders.items() if v]
    }

# ── ROUTES ────────────────────────────────────────────────────
@app.route("/")
def index_page():
    return send_from_directory("static", "index.html")

@app.route("/api/status")
def api_status():
    try:
        c  = get_client()
        me = run(c.get_me())
        idx = load_index()
        total = sum(len(f["files"]) for ch in idx["channels"] for f in ch["folders"])
        return jsonify({"ok": True, "bot": f"@{me.username}", "indexed": total,
                        "last_updated": idx.get("last_updated"), "channels": len(idx["channels"])})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/index")
def api_index():
    return jsonify(load_index())

@app.route("/api/scan", methods=["POST"])
def api_scan():
    body     = request.json or {}
    channels = body.get("channels") or CHANNELS
    keywords = body.get("keywords", [])
    if not channels:
        return jsonify(error="Koi channel nahi"), 400

    client = get_client()
    result = []
    for ch in channels:
        try:
            result.append(run(scan_channel(client, ch, keywords)))
        except FloodWait as e:
            time.sleep(e.value + 2)
            result.append(run(scan_channel(client, ch, keywords)))
        except Exception as e:
            result.append({"id": ch, "name": ch, "emoji": "❌", "error": str(e), "folders": []})

    data = {"channels": result, "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    save_index(data)
    return jsonify(data)

@app.route("/stream/<path:file_id>")
def stream(file_id):
    idx = load_index()
    finfo = next(
        (f for ch in idx.get("channels",[]) for fd in ch.get("folders",[])
         for f in fd.get("files",[]) if f["file_id"] == file_id),
        None
    )
    size = finfo["size_b"] if finfo else 0
    mime = "video/mp4" if (finfo and finfo["type"] == "video") else "application/pdf"

    start, end, status = 0, max(size-1, 0), 200
    hdrs = {"Content-Type": mime, "Accept-Ranges": "bytes", "Cache-Control": "no-cache"}

    rng = request.headers.get("Range","")
    if rng and size:
        m = re.match(r"bytes=(\d+)-(\d*)", rng)
        if m:
            start  = int(m.group(1))
            end    = int(m.group(2)) if m.group(2) else size - 1
            length = end - start + 1
            hdrs["Content-Range"]  = f"bytes {start}-{end}/{size}"
            hdrs["Content-Length"] = str(length)
            status = 206

    def gen():
        client     = get_client()
        chunk_size = 1024 * 1024  # 1MB
        offset     = start // chunk_size
        skip       = start % chunk_size
        first      = True
        async def _inner():
            nonlocal first
            async for chunk in client.stream_media(file_id, offset=offset):
                if first and skip:
                    chunk = chunk[skip:]
                    first = False
                yield chunk
        ag = _inner()
        try:
            while True:
                yield run(ag.__anext__())
        except StopAsyncIteration:
            pass

    return Response(stream_with_context(gen()), status=status, headers=hdrs, direct_passthrough=True)

# ── UTILS ─────────────────────────────────────────────────────
def fmt_size(b):
    if not b: return "?"
    if b > 1_073_741_824: return f"{b/1_073_741_824:.1f} GB"
    if b > 1_048_576:     return f"{b/1_048_576:.0f} MB"
    return f"{b/1024:.0f} KB"

def fmt_dur(s):
    if not s: return None
    h, r = divmod(s, 3600); m, _ = divmod(r, 60)
    return f"{h}h {m}m" if h else f"{m}m"

# ── MAIN ──────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Bot connect ho raha hai...")
    try: get_client(); print("Bot connected!")
    except Exception as e: print(f"Warning: {e}")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
