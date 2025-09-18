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