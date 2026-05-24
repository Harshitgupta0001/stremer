# 📚 Telegram Study Portal
### Pyrogram + Flask | No MongoDB | JSON Index | 1GB+ Streaming

## Deploy on Render (Free)

### Step 1 — GitHub par upload karo
1. github.com → New Repository → `study-portal`
2. Sab files upload karo (drag & drop)

### Step 2 — Render par deploy karo
1. render.com → New → Web Service → GitHub repo connect
2. **Environment Variables set karo:**

| Variable | Value |
|----------|-------|
| `API_ID` | my.telegram.org se milega |
| `API_HASH` | my.telegram.org se milega |
| `BOT_TOKEN` | @BotFather se milega |
| `CHANNELS` | `@chan1,@chan2,-1001234567890` |

3. Build: `pip install -r requirements.txt`
4. Start: `gunicorn server:app --bind 0.0.0.0:$PORT --workers 1 --timeout 300`
5. Deploy!

### Step 3 — Site par jaao → Setup tab → Scan Channels
Ek baar scan karo — index.json ban jayega — phir sab files milenge!

## API Credentials Kahan Se Milenge

**API_ID & API_HASH:**
- https://my.telegram.org par jaao
- "API Development Tools" click karo
- App naam kuch bhi rakho
- api_id aur api_hash copy karo

**BOT_TOKEN:**
- Telegram mein @BotFather → /newbot
- Token copy karo
- Bot ko channel ka Admin banao

## Features
- ✅ 1GB+ video streaming (Pyrogram stream_media)
- ✅ PDF inline viewer
- ✅ No MongoDB — JSON file index
- ✅ HTTP Range support (video seek kaam karta hai)
- ✅ Multi-channel, auto folder detection
- ✅ Search, Progress tracker, Download
