import os
import time
import html
import shutil
import hashlib
import requests
import feedparser
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any

UA = "Mozilla/5.0 (RSS-Aggregator; +https://github.com)"
MAX_PER_FEED = 30
OUTPUT_DIR = Path("docs")
TEMPLATE_DIR = Path("templates")

SITE_TITLE = os.getenv("SITE_TITLE", "我的聚合订阅")
# SITE_URL 用于生成合并 RSS 的 <link>，请在 Actions 里设为你的 Pages 地址
SITE_URL = os.getenv("SITE_URL", "https://<your-username>.github.io/<repo>")
COMBINED_FEED_PATH = OUTPUT_DIR / "combined.xml"

def read_feeds_list(path: Path) -> List[str]:
    feeds = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        feeds.append(s)
    return feeds

def fetch_feed(url: str) -> feedparser.FeedParserDict:
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=25)
        resp.raise_for_status()
        return feedparser.parse(resp.content)
    except Exception as e:
        print(f"[WARN] Fetch failed: {url} -> {e}")
        return feedparser.parse(b"")

def to_dt(entry: Dict[str, Any]) -> datetime:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        if getattr(entry, key, None):
            return datetime.fromtimestamp(time.mktime(getattr(entry, key))).astimezone(timezone.utc)
        if key in entry and entry[key]:
            return datetime.fromtimestamp(time.mktime(entry[key])).astimezone(timezone.utc)
    return datetime.now(timezone.utc)

def norm_text(s: str) -> str:
    return s.strip() if s else ""

def ensure_dirs():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def render_html(items: List[Dict[str, Any]], feeds_count: int):
    template_path = TEMPLATE_DIR / "index.html.j2"
    css_path = TEMPLATE_DIR / "style.css"
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    if not template_path.exists():
        # 极简内置模板兜底
        html_content = "<html><head><meta charset='utf-8'><title>{}</title></head><body>".format(html.escape(SITE_TITLE))
        html_content += "<h1>{}</h1><p>共 {} 条 · {} 个源</p><ul>".format(html.escape(SITE_TITLE), len(items), feeds_count)
        for it in items:
            dt = it['dt'].astimezone().strftime("%Y-%m-%d %H:%M")
            html_content += "<li><a href='{link}' target='_blank'>{title}</a> <small>{source} · {dt}</small></li>".format(
                link=html.escape(it['link']), title=html.escape(it['title']), source=html.escape(it['source']), dt=dt
            )
        html_content += "</ul></body></html>"
        (OUTPUT_DIR / "index.html").write_text(html_content, encoding="utf-8")
        return

    from jinja2 import Environment, FileSystemLoader, select_autoescape
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"])
    )
    tpl = env.get_template("index.html.j2")
    html_out = tpl.render(
        site_title=SITE_TITLE,
        site_url=SITE_URL,
        total=len(items),
        feeds_count=feeds_count,
        items=items,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    (OUTPUT_DIR / "index.html").write_text(html_out, encoding="utf-8")
    # 复制样式
    if css_path.exists():
        shutil.copy2(css_path, OUTPUT_DIR / "style.css")

def build_combined_rss(items: List[Dict[str, Any]]):
    # 简单手写 RSS 2.0
    def xml_escape(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    now_rfc = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        f"<title>{xml_escape(SITE_TITLE)}</title>",
        f"<link>{xml_escape(SITE_URL)}</link>",
        f"<description>{xml_escape(SITE_TITLE)} - 合并订阅</description>",
        f"<lastBuildDate>{now_rfc}</lastBuildDate>",
    ]
    for it in items:
        pub = it["dt"].strftime("%a, %d %b %Y %H:%M:%S GMT")
        guid = hashlib.sha1(it["link"].encode("utf-8")).hexdigest()
        desc = it.get("summary") or ""
        parts += [
            "<item>",
            f"<title>{xml_escape(it['title'])}</title>",
            f"<link>{xml_escape(it['link'])}</link>",
            f"<guid isPermaLink='false'>{guid}</guid>",
            f"<pubDate>{pub}</pubDate>",
            f"<author>{xml_escape(it['source'])}</author>",
            f"<description><![CDATA[{desc}]]></description>",
            "</item>",
        ]
    parts += ["</channel>", "</rss>"]
    COMBINED_FEED_PATH.write_text("\n".join(parts), encoding="utf-8")

def main():
    ensure_dirs()
    feeds_file = Path("feeds.txt")
    if not feeds_file.exists():
        raise SystemExit("缺少 feeds.txt")

    feed_urls = read_feeds_list(feeds_file)
    all_items: List[Dict[str, Any]] = []
    seen_links = set()

    for url in feed_urls:
        parsed = fetch_feed(url)
        feed_title = norm_text(parsed.feed.title) if parsed.feed and getattr(parsed.feed, "title", "") else url
        entries = parsed.entries[:MAX_PER_FEED]
        for e in entries:
            link = norm_text(getattr(e, "link", "") or e.get("link", ""))
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            title = norm_text(getattr(e, "title", "") or e.get("title", "") or "(无标题)")
            summary = getattr(e, "summary", "") or e.get("summary", "") or ""
            all_items.append({
                "title": title,
                "link": link,
                "summary": summary,
                "source": feed_title,
                "dt": to_dt(e),
            })

    # 统一排序（新→旧）并截断总量（例如保留最近 1000 条）
    all_items.sort(key=lambda x: x["dt"], reverse=True)
    all_items = all_items[:1000]

    render_html(all_items, feeds_count=len(feed_urls))
    build_combined_rss(all_items)
    print(f"OK: items={len(all_items)}, feeds={len(feed_urls)}")
    print(f"- 页面：{OUTPUT_DIR / 'index.html'}")
    print(f"- 合并 RSS：{COMBINED_FEED_PATH}")

if __name__ == "__main__":
    main()
