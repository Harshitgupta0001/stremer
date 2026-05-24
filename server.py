"""
Telegram Study Portal — Pyrogram + Flask
Fix: Dedicated event loop thread for Pyrogram async calls
"""
import os, json, re, time, asyncio, threading
from pathlib import Path
from flask import Flask, jsonify, Response, send_from_directory, request, stream_with_context
from pyrogram import Client
from pyrogram.errors import FloodWait

# ── CONFIG ───────────────────────────────────────────────────
API_ID    = int(os.environ.get("API_ID", "0"))
API_HASH  = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNELS  = [c.strip() for c in os.environ.get("CHANNELS", "").split(",") if c.strip()]
PORT      = int(os.environ.get("PORT", 5000))
INDEX_FILE = Path("index.json")

app = Flask(__name__, static_folder="static")

# ── DEDICATED ASYNC THREAD ───────────────────────────────────
# Flask is sync, Pyrogram is async.
# Solution: run one persistent event loop in a background thread.
# Flask threads call run_coroutine() to submit work to that loop.

_loop: asyncio.AbstractEventLoop = None
_client: Client = None
_ready = threading.Event()   # signals that loop+client are up

def _start_loop():
    global _loop, _client
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)

    async def _init():
        global _client
        _client = Client(
            "bot_session",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            no_updates=True,
        )
        await _client.start()
        _ready.set()          # signal Flask that we're ready
        print("✅ Pyrogram client started!")

    _loop.run_until_complete(_init())
    _loop.run_forever()       # keep loop alive for future coroutines

# Start background thread ONCE at import time
_thread = threading.Thread(target=_start_loop, daemon=True)
_thread.start()

def run(coro):
    """Submit a coroutine to the background loop and block until done."""
    if not _ready.wait(timeout=30):
        raise RuntimeError("Pyrogram client not ready in 30s")
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=120)

# ── INDEX ─────────────────────────────────────────────────────
def load_index():
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return {"channels": [], "last_updated": None}

def save_index(d):
    INDEX_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

# ── SCAN ──────────────────────────────────────────────────────
async def _scan_channel(channel_id: str, keywords: list):
    try:
        chat = await _client.get_chat(channel_id)
        chat_name, chat_id = chat.title or channel_id, chat.id
    except Exception as e:
        return {"id": channel_id, "name": channel_id, "chat_id": channel_id,
                "error": str(e), "folders": []}

    folders: dict = {}
    async for msg in _client.get_chat_history(chat_id, limit=1000):
        file_obj = file_type = None
        if msg.video:
            file_obj, file_type = msg.video, "video"
        elif msg.document:
            mt = msg.document.mime_type or ""
            if "pdf" in mt:    file_obj, file_type = msg.document, "pdf"
            elif "video" in mt: file_obj, file_type = msg.document, "video"
        if not file_obj:
            continue

        caption = (msg.caption or msg.text or "").strip()
        folder  = "General"
        hm = re.search(r"#(\w+)", caption)
        if hm:
            folder = hm.group(1)
        else:
            fl = caption.split("\n")[0].strip().replace("*","").replace("_","")
            if fl and len(fl) < 60:
                folder = fl
        for kw in keywords:
            if kw.lower() in caption.lower():
                folder = kw.capitalize()
                break

        folders.setdefault(folder, [])
        seq  = len(folders[folder]) + 1
        name = caption.split("\n")[0].strip() or getattr(file_obj, "file_name", None) or f"{file_type.upper()} {seq}"
        sz   = getattr(file_obj, "file_size", 0) or 0
        dur  = getattr(file_obj, "duration", None)

        folders[folder].append({
            "seq": seq, "name": name[:120], "type": file_type,
            "size": fmt_size(sz), "size_b": sz,
            "duration": fmt_dur(dur) if dur else None,
            "file_id": file_obj.file_id,
            "msg_id": msg.id, "chat_id": chat_id,
            "date": msg.date.isoformat() if msg.date else None,
        })
        await asyncio.sleep(0.02)

    return {
        "id": channel_id, "name": chat_name, "chat_id": chat_id, "emoji": "📡",
        "folders": [{"name": k, "files": v} for k, v in folders.items() if v],
    }

# ── ROUTES ────────────────────────────────────────────────────
@app.route("/")
def index_page():
    return send_from_directory("static", "index.html")

@app.route("/api/status")
def api_status():
    try:
        me    = run(_client.get_me())
        idx   = load_index()
        total = sum(len(f["files"]) for ch in idx["channels"] for f in ch["folders"])
        return jsonify({
            "ok": True, "bot": f"@{me.username}", "name": me.first_name,
            "indexed": total, "channels": len(idx["channels"]),
            "last_updated": idx.get("last_updated"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/index")
def api_index():
    return jsonify(load_index())

@app.route("/api/scan", methods=["POST"])
def api_scan():
    body     = request.json or {}
    channels = body.get("channels") or CHANNELS
    keywords = [k.strip() for k in body.get("keywords", []) if k.strip()]
    if not channels:
        return jsonify(error="Koi channel nahi diya"), 400

    result = []
    for ch in channels:
        try:
            result.append(run(_scan_channel(ch, keywords)))
        except FloodWait as e:
            time.sleep(e.value + 2)
            result.append(run(_scan_channel(ch, keywords)))
        except Exception as e:
            result.append({"id": ch, "name": ch, "emoji": "❌", "error": str(e), "folders": []})

    data = {
        "channels": result,
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    save_index(data)
    return jsonify(data)

@app.route("/stream/<path:file_id>")
def stream(file_id):
    idx   = load_index()
    finfo = next(
        (f for ch in idx.get("channels", [])
           for fd in ch.get("folders", [])
           for f  in fd.get("files", [])
           if f["file_id"] == file_id),
        None,
    )
    size = (finfo or {}).get("size_b", 0)
    mime = "video/mp4" if finfo and finfo["type"] == "video" else "application/pdf"

    start, end, status = 0, max(size - 1, 0), 200
    hdrs = {"Content-Type": mime, "Accept-Ranges": "bytes", "Cache-Control": "no-cache"}

    rng = request.headers.get("Range", "")
    if rng and size:
        m = re.match(r"bytes=(\d+)-(\d*)", rng)
        if m:
            start  = int(m.group(1))
            end    = int(m.group(2)) if m.group(2) else size - 1
            hdrs["Content-Range"]  = f"bytes {start}-{end}/{size}"
            hdrs["Content-Length"] = str(end - start + 1)
            status = 206

    def gen():
        chunk_size = 1024 * 1024   # 1 MB
        offset     = start // chunk_size
        skip       = start % chunk_size
        first      = True

        async def _inner():
            nonlocal first
            async for chunk in _client.stream_media(file_id, offset=offset):
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

# ── ENTRY POINT ───────────────────────────────────────────────
if __name__ == "__main__":
    print("Waiting for Pyrogram to start...")
    _ready.wait(timeout=30)
    print(f"Server starting on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
