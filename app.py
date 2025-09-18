import os, time, re, requests, feedparser
from bs4 import BeautifulSoup
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ---------- ENV ----------
GOODREADS_RSS = os.environ.get("GOODREADS_RSS")                  # updates feed (optional)
GOODREADS_READ_RSS = os.environ.get("GOODREADS_READ_RSS")        # read-shelf feed (required for /books/finished)

# ---------- HTTP (browser-ish headers to avoid 403) ----------
UA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
}
def _fetch_text(url: str) -> str:
    r = requests.get(url, headers=UA_HEADERS, timeout=15)
    r.raise_for_status()
    return r.text

def _fetch_and_parse(url: str):
    xml = _fetch_text(url)
    return feedparser.parse(xml)

# ---------- HEALTH ----------
@app.get("/")
def root():
    return {"status": "ok"}

# ---------- DEBUG RAW (read shelf) ----------
@app.get("/books/finished/raw")
def books_finished_raw():
    if not GOODREADS_READ_RSS:
        return {"error": "GOODREADS_READ_RSS missing"}, 500
    xml = _fetch_text(GOODREADS_READ_RSS)
    return {"status": 200, "len": len(xml), "snippet": xml[:2000]}

# ---------- RECENT (updates feed) ----------
@app.get("/books/recent")
def books_recent():
    if not GOODREADS_RSS:
        return jsonify({"error": "GOODREADS_RSS not configured"}), 500
    feed = _fetch_and_parse(GOODREADS_RSS)
    items = []
    for e in feed.entries[:10]:
        items.append({
            "title": e.get("title"),
            "link": e.get("link"),
            "published": e.get("published"),
            "summary": e.get("summary"),
        })
    return jsonify({"count": len(items), "items": items})

# ---------- FINISHED (read shelf -> normalized rows) ----------
LABELS = {
    "author":   ["author_name", "author"],
    "rating":   ["user_rating"],    #, "rating"
    "finished": ["read_at", "date_read", "user_read_at"],
    "fallback": ["user_date_updated", "date_updated", "user_date_added", "date_added", "pubdate"],
    "review":   ["review", "review_text"],
}

def _grab_label(text, labels):
    for lab in labels:
        m = re.search(rf"{lab}\s*:\s*(.*)", text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""

def _norm_date(s):
    if not s: return ""
    return s.replace("/", "-").strip()

def _pick_finished(text, entry):
    primary = _grab_label(text, LABELS["finished"])
    if primary:
        return _norm_date(primary)
    for k in ["user_read_at", "read_at", "date_read", "gr_read_at", "gr_date_read", "user_date_read"]:
        v = entry.get(k)
        if v:
            return _norm_date(str(v))
    fallback = _grab_label(text, LABELS["fallback"])
    if fallback:
        return _norm_date(fallback)
    for k in ["user_date_updated", "date_updated", "user_date_added", "date_added", "published"]:
        v = entry.get(k)
        if v:
            return _norm_date(str(v))
    return ""

_cache = {"books_read": None, "ts": 0, "ttl": 15*60}

def _parse_read_shelf(feed_url, limit=100):
    feed = _fetch_and_parse(feed_url)
    out = []
    for e in feed.entries[:limit]:
        desc_html = e.get("description", "")
        soup = BeautifulSoup(desc_html, "html.parser")
        text = soup.get_text("\n", strip=True)

        title = (e.get("title") or "").strip()
        link = e.get("link")
        author = _grab_label(text, LABELS["author"])
        rating = _grab_label(text, LABELS["rating"])
        review = _grab_label(text, LABELS["review"])

        if rating and rating.isdigit():
            rating = int(rating)
            if rating == 0:
                rating = ""

        if " by " in title and author:
            title = title.split(" by ")[0].strip()

        finished_at = _pick_finished(text, e)

        out.append({
            "title": title,
            "author": author,
            "finished_at": finished_at,
            "rating": rating,
            "review": review,
            "link": link,
        })
    return out

@app.get("/books/finished")
def books_finished():
    if not GOODREADS_READ_RSS:
        return jsonify({"error": "GOODREADS_READ_RSS not configured"}), 500

    now = time.time()
    if _cache["books_read"] and now - _cache["ts"] < _cache["ttl"]:
        items = _cache["books_read"]
    else:
        items = _parse_read_shelf(GOODREADS_READ_RSS, limit=100)
        # If you only want rows with a date, uncomment the next line later.
        # items = [it for it in items if it["finished_at"]]
        items.sort(key=lambda it: it["finished_at"] or "", reverse=True)
        _cache["books_read"] = items
        _cache["ts"] = now

    return jsonify({"items": items})

if __name__ == "__main__":
    app.run(debug=True)
