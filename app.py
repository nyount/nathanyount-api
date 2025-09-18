import os, time, re, requests, feedparser
from bs4 import BeautifulSoup
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ====== ENV ======
GOODREADS_RSS = os.environ.get("GOODREADS_RSS")  # updates feed (optional)
GOODREADS_READ_RSS = os.environ.get("GOODREADS_READ_RSS")  # "read" shelf feed

# ====== HTTP fetch with browser-ish headers (Goodreads blocks default UA) ======
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

# ====== BASIC HEALTH ======
@app.get("/")
def root():
    return {"status": "ok", "message": "API is alive"}

# ====== DEBUG RAW FEED (works now for you) ======
@app.get("/books/finished/raw")
def books_finished_raw():
    url = os.environ.get("GOODREADS_READ_RSS", "")
    if not url:
        return {"error": "GOODREADS_READ_RSS missing"}, 500
    try:
        xml = _fetch_text(url)
        return {"status": 200, "len": len(xml), "snippet": xml[:2000]}
    except requests.HTTPError as e:
        return {"status": e.response.status_code, "error": str(e)}

# ====== RECENT UPDATES (noisy feed) ======
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

# ====== FINISHED BOOKS (read shelf → table data) ======
LABELS = {
    "author":   ["author_name", "author"],
    "rating":   ["user_rating", "rating"],
    "finished": ["read_at", "date_read", "user_read_at"],
    "review":   ["review", "review_text"],
}

def _grab_label(text, labels):
    for lab in labels:
        m = re.search(rf"{lab}\s*:\s*(.*)", text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""

def _normalize_date(s):
    if not s: return ""
    return s.replace("/", "-").strip()  # Goodreads often YYYY/MM/DD

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
        finished_at = _normalize_date(_grab_label(text, LABELS["finished"]))
        review = _grab_label(text, LABELS["review"])

        # normalize rating
        if rating and rating.isdigit():
            rating = int(rating)
            if rating == 0:
                rating = ""

        # strip " by Author" from title if present
        if " by " in title and author:
            title = title.split(" by ")[0].strip()

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
        # keep only entries with an actual finished date
        items = [it for it in items if it["finished_at"]]
        items.sort(key=lambda it: it["finished_at"], reverse=True)
        _cache["books_read"] = items
        _cache["ts"] = now

    return jsonify({"items": items})

if __name__ == "__main__":
    app.run(debug=True)
