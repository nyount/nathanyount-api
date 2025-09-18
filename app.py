# app.py
import os, time, re, requests, feedparser
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request
from flask_cors import CORS
from dateutil import parser as dtparse  # robust date parsing

app = Flask(__name__)
CORS(app)

# ---- ENV ----
GOODREADS_READ_RSS = os.environ.get("GOODREADS_READ_RSS")

# ---- HTTP (browser-like headers; GR blocks default UA) ----
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

# ---- helpers ----
def _grab_user_rating(desc_text: str, entry) -> int | str:
    """
    Return YOUR rating (0..5) as int, or '' if unrated.
    Never fall back to average rating.
    """
    m = re.search(r"user[\s_-]*rating\s*:\s*([0-5])\b", desc_text, flags=re.IGNORECASE)
    if m:
        return int(m.group(1))
    # sometimes feedparser keeps namespaced keys
    for k, v in entry.items():
        lk = k.lower()
        if "user" in lk and "rating" in lk:
            if isinstance(v, (int, float)) and 0 <= v <= 5:
                return int(v)
            m2 = re.search(r"\b([0-5])\b", str(v))
            if m2:
                return int(m2.group(1))
    return ""  # unrated

def _pick_finished_date(desc_text: str, entry) -> tuple[str, int]:
    """
    Find 'date read' robustly. Return:
      - finished_at (YYYY-MM-DD or '')
      - finished_ts (unix epoch int; 0 if unknown)
    """
    # 1) Look for likely labels in description blob
    for lab in ("read_at", "date_read", "user_read_at"):
        m = re.search(rf"{lab}\s*:\s*(.+)", desc_text, flags=re.IGNORECASE)
        if m and m.group(1).strip():
            raw = m.group(1).strip()
            dt = _to_dt(raw)
            if dt:
                return dt.date().isoformat(), int(dt.timestamp())

    # 2) Check entry keys
    for k in ("user_read_at", "read_at", "date_read", "gr_read_at", "gr_date_read", "user_date_read"):
        v = entry.get(k)
        if v:
            dt = _to_dt(str(v))
            if dt:
                return dt.date().isoformat(), int(dt.timestamp())

    # 3) Fallbacks (approximate “finished” as last update)
    for lab in ("user_date_updated", "date_updated", "user_date_added", "date_added", "pubdate", "published"):
        m = re.search(rf"{lab}\s*:\s*(.+)", desc_text, flags=re.IGNORECASE)
        if m and m.group(1).strip():
            dt = _to_dt(m.group(1).strip())
            if dt:
                return dt.date().isoformat(), int(dt.timestamp())

        v = entry.get(lab)
        if v:
            dt = _to_dt(str(v))
            if dt:
                return dt.date().isoformat(), int(dt.timestamp())

    return "", 0  # unknown

def _to_dt(s: str):
    try:
        return dtparse.parse(s)
    except Exception:
        return None

# tiny cache
_CACHE = {"items": None, "ts": 0, "ttl": 15 * 60}

def _parse_finished(feed_url: str, limit: int = 200):
    feed = _fetch_and_parse(feed_url)
    out = []
    for e in feed.entries[:limit]:
        desc_html = e.get("description", "")
        soup = BeautifulSoup(desc_html, "html.parser")
        text = soup.get_text("\n", strip=True)

        title = (e.get("title") or "").strip()
        link = e.get("link")
        # extract author from description; fall back to stripping " by ..."
        m_auth = re.search(r"(?:author|author_name)\s*:\s*(.+)", text, flags=re.IGNORECASE)
        author = m_auth.group(1).strip() if m_auth else ""
        if not author and " by " in title:
            # many GR titles look like "Title by Author"
            maybe_title, maybe_author = title.split(" by ", 1)
            title, author = maybe_title.strip(), maybe_author.strip()

        rating = _grab_user_rating(text, e)  # YOUR rating only
        finished_at, finished_ts = _pick_finished_date(text, e)

        # clean rating: 0 -> '' (unrated)
        if rating == 0:
            rating = ""

        out.append({
            "title": title,
            "author": author,
            "finished_at": finished_at,   # for display (YYYY-MM-DD)
            "finished_ts": finished_ts,   # for sorting (int)
            "rating": rating,             # 1..5 or ''
            "review": _extract_review(text),
            "link": link,
        })
    return out

def _extract_review(text: str) -> str:
    m = re.search(r"(?:^|\n)review\s*:\s*(.*)", text, flags=re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""

# ---- routes ----
@app.get("/", strict_slashes=False)
def health():
    return {"status": "ok"}

@app.get("/books/finished/raw", strict_slashes=False)
def finished_raw():
    if not GOODREADS_READ_RSS:
        return {"error": "GOODREADS_READ_RSS missing"}, 500
    xml = _fetch_text(GOODREADS_READ_RSS)
    return {"status": 200, "len": len(xml), "snippet": xml[:2000]}

@app.get("/books/finished", strict_slashes=False)
def finished():
    if not GOODREADS_READ_RSS:
        return jsonify({"error": "GOODREADS_READ_RSS not configured"}), 500

    # dev switch: ?nocache=1 to force refresh
    if request.args.get("nocache"):
        _CACHE["ts"] = 0

    now = time.time()
    if _CACHE["items"] and now - _CACHE["ts"] < _CACHE["ttl"]:
        items = _CACHE["items"]
    else:
        items = _parse_finished(GOODREADS_READ_RSS, limit=200)
        # If you only want rows with an actual finished date, uncomment:
        # items = [it for it in items if it["finished_ts"] > 0]
        items.sort(key=lambda it: it.get("finished_ts", 0), reverse=True)  # NEWEST first
        _CACHE["items"] = items
        _CACHE["ts"] = now

    # Optional: trim to top N here (or handle on frontend)
    # items = items[:20]
    return jsonify({"items": items})

if __name__ == "__main__":
    app.run(debug=True)
