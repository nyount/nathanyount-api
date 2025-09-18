import os
import feedparser
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # allow your main site to call this API

GOODREADS_RSS = os.environ.get("GOODREADS_RSS")

@app.get("/")
def root():
    return {"status": "ok", "message": "API is alive"}

@app.get("/books/recent")
def books_recent():
    if not GOODREADS_RSS:
        return jsonify({"error": "GOODREADS_RSS not configured"}), 500
    feed = feedparser.parse(GOODREADS_RSS)
    items = []
    for e in feed.entries[:10]:
        items.append({
            "title": e.get("title"),
            "link": e.get("link"),
            "published": e.get("published"),
            "summary": e.get("summary"),
        })
    return {"count": len(items), "items": items}

if __name__ == "__main__":
    # local dev server
    app.run(debug=True)
    


import os, time, re
import feedparser
from bs4 import BeautifulSoup
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

GOODREADS_READ_RSS = os.environ.get("GOODREADS_READ_RSS")

# --- very small cache so we don't hammer Goodreads on every page load ----
_cache = {"books_read": None, "ts": 0, "ttl": 15 * 60}  # 15 minutes

def _parse_read_shelf(feed_url, limit=50):
    """Return a list of dicts: title, author, finished_at (YYYY-MM-DD),
    rating (0-5, can be ''), review (plain text), link."""
    feed = feedparser.parse(feed_url)
    items = []
    for e in feed.entries[:limit]:
        # Goodreads "list_rss" sticks the structured info inside HTML in description.
        desc_html = e.get("description", "")
        soup = BeautifulSoup(desc_html, "html.parser")

        # Heuristics: try multiple ways to extract fields robustly
        title = (e.get("title") or "").strip()
        link = e.get("link")

        # Author: look for obvious tags/labels inside the HTML blob
        author = ""
        finished_at = ""  # date you finished the book
        rating = ""
        review = ""

        text = soup.get_text("\n", strip=True)

        # Common patterns found in list_rss descriptions:
        # "author_name: John Steinbeck"
        # "user_rating: 5"
        # "read_at: 2025/04/16"
        # "review: ... (may be empty)"
        def grab(label):
            # find 'label: value' until next line
            m = re.search(rf"{label}\s*:\s*(.*)", text, flags=re.IGNORECASE)
            return m.group(1).strip() if m else ""

        author = grab("author_name") or grab("author") or ""
        rating = grab("user_rating") or grab("rating") or ""
        finished_at = grab("read_at") or grab("date_read") or ""

        # normalize rating to int/str if it’s '0' (no rating)
        if rating and rating.isdigit():
            rating = int(rating)
        elif rating == "0":
            rating = ""

        # review can be multi-line; try to capture after 'review:' up to end
        review = ""
        m = re.search(r"review\s*:\s*(.*)", text, flags=re.IGNORECASE | re.DOTALL)
        if m:
            review = m.group(1).strip()

        # Clean title if it contains "by Author" etc.
        if " by " in title and author:
            # Keep only the book name before " by "
            title = title.split(" by ")[0].strip()

        items.append({
            "title": title,
            "author": author,
            "finished_at": finished_at,   # string from Goodreads; often YYYY/MM/DD
            "rating": rating,             # 1-5 or ""
            "review": review,
            "link": link,
        })
    return items

@app.get("/books/finished")
def books_finished():
    if not GOODREADS_READ_RSS:
        return jsonify({"error": "GOODREADS_READ_RSS not configured"}), 500

    now = time.time()
    if _cache["books_read"] and now - _cache["ts"] < _cache["ttl"]:
        return jsonify({"items": _cache["books_read"]})

    items = _parse_read_shelf(GOODREADS_READ_RSS, limit=100)
    # Keep only entries that actually have a finished date (some shelves include “currently-reading” with empty read_at)
    items = [it for it in items if it["finished_at"]]

    # Optional: sort newest first if date looks sortable
    def sort_key(it):
        # Try to normalize YYYY/MM/DD -> YYYY-MM-DD
        d = it["finished_at"].replace("/", "-")
        return d
    items.sort(key=sort_key, reverse=True)

    _cache["books_read"] = items
    _cache["ts"] = now
    return jsonify({"items": items})

import requests
import feedparser

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