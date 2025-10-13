# -*- coding: utf-8 -*-
"""
聚合多条 RSS/Atom 源，生成：
- docs/index.html  聚合网页
- docs/combined.xml 合并 RSS（可被任何阅读器订阅）

改进点：
- 更真实的浏览器 UA，减少 403/429
- 请求失败重试 + 更清晰的日志
- 对“解析成功但无 entries”也判为失败以便重试
- 仍保留模板/样式可选机制（无模板时用内置极简页）
"""

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

# -------- 可调参数（可用环境变量覆盖） --------
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = int(os.getenv("FEED_TIMEOUT", "25"))
RETRY = int(os.getenv("FEED_RETRY", "2"))  # 失败重试次数
MAX_PER_FEED = int(os.getenv("MAX_PER_FEED", "30"))
MAX_TOTAL = int(os.getenv("MAX_TOTAL", "1000"))

OUTPUT_DIR = Path("docs")
TEMPLATE_DIR = Path("templates")

SITE_TITLE = os.getenv("SITE_TITLE", "我的聚合订阅")
# 用于 RSS <link>，请在 workflow 里设成你的 Pages 地址
SITE_URL = os.getenv("SITE_URL", "https://<your-username>.github.io/<repo>")
COMBINED_FEED_PATH = OUTPUT_DIR / "combined.xml"


def read_feeds_list(path: Path) -> List[str]:
    if not path.exists():
        raise SystemExit("缺少 feeds.txt")
    feeds = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        feeds.append(s)
    return feeds


def fetch_feed(url: str) -> feedparser.FeedParserDict:
    """请求 + 解析；失败重试；entries 为空也算失败触发重试。"""
    last_err = None
    for i in range(RETRY + 1):
        try:
            resp = requests.get(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Connection": "close",
                },
                timeout=TIMEOUT,
            )
            code = resp.status_code
            if code >= 400:
                raise RuntimeError(f"HTTP {code}")
            parsed = feedparser.parse(resp.content)
            # 某些站会返回 200 但不给 entries
            if not parsed.entries:
                raise RuntimeError("no entries parsed")
            return parsed
        except Exception as e:
            last_err = e
            print(f"[WARN] Fetch failed({i+1}/{RETRY+1}): {url} -> {e}")
            time.sleep(1.2 * (i + 1))
    print(f"[ERROR] Give up: {url} -> {last_err}")
    # 返回空，以便主流程继续
    return feedparser.parse(b"")


def to_dt(entry: Dict[str, Any]) -> datetime:
    """从条目里尽力取时间，统一为 UTC；失败则给当前时间（保证可排序）"""
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        val = getattr(entry, key, None) or entry.get(key)
        if val:
            try:
                return datetime.fromtimestamp(time.mktime(val)).astimezone(timezone.utc)
            except Exception:
                pass
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
        # 极简兜底模板
        html_content = [
            "<!doctype html>",
            "<html lang='zh-CN'>",
            "<head><meta charset='utf-8'/>",
            f"<title>{html.escape(SITE_TITLE)}</title>",
            "<meta name='viewport' content='width=device-width,initial-scale=1'/>",
            "<style>",
            "body{font:14px/1.6 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial;"
            "max-width:860px;margin:0 auto;padding:24px 16px;}",
            "a{color:#3366cc;text-decoration:none}a:hover{text-decoration:underline}",
            "li{border-bottom:1px solid #e5e5e5;padding:12px 0}",
            "small{color:#666}",
            "</style></head><body>",
        ]
        html_content.append(f"<h1>{html.escape(SITE_TITLE)}</h1>")
        html_content.append(
            f"<p>共 {len(items)} 条 · {feeds_count} 个源 · 生成于 "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}</p>"
        )
        html_content.append("<p><a href='combined.xml' target='_blank'>订阅合并 RSS</a></p>")
        html_content.append("<ul>")
        for it in items:
            dt = it["dt"].astimezone().strftime("%Y-%m-%d %H:%M")
            html_content.append(
                f"<li><a href='{html.escape(it['link'])}' target='_blank'>"
                f"{html.escape(it['title'])}</a> "
                f"<small>{html.escape(it['source'])} · {dt}</small></li>"
            )
        html_content.append("</ul></body></html>")
        (OUTPUT_DIR / "index.html").write_text("\n".join(html_content), encoding="utf-8")
        return

    # 使用 Jinja2 模板
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    tpl = env.get_template("index.html.j2")
    html_out = tpl.render(
        site_title=SITE_TITLE,
        site_url=SITE_URL,
        total=len(items),
        feeds_count=feeds_count,
        items=items,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    (OUTPUT_DIR / "index.html").write_text(html_out, encoding="utf-8")
    if css_path.exists():
        shutil.copy2(css_path, OUTPUT_DIR / "style.css")


def build_combined_rss(items: List[Dict[str, Any]]):
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
    feed_urls = read_feeds_list(feeds_file)

    all_items: List[Dict[str, Any]] = []
    seen_links = set()
    fail_count = 0

    for url in feed_urls:
        parsed = fetch_feed(url)
        if not parsed.entries:
            fail_count += 1
            continue

        feed_title = (
            norm_text(getattr(parsed.feed, "title", "") if parsed.feed else "")
            or url
        )
        entries = parsed.entries[:MAX_PER_FEED]

        for e in entries:
            link = norm_text(getattr(e, "link", "") or e.get("link", ""))
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            title = norm_text(getattr(e, "title", "") or e.get("title", "") or "(无标题)")
            summary = getattr(e, "summary", "") or e.get("summary", "") or ""
            all_items.append(
                {
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "source": feed_title,
                    "dt": to_dt(e),
                }
            )

    # 按时间倒序并截断总量
    all_items.sort(key=lambda x: x["dt"], reverse=True)
    all_items = all_items[:MAX_TOTAL]

    render_html(all_items, feeds_count=len(feed_urls))
    build_combined_rss(all_items)

    print(
        f"OK: items={len(all_items)}, feeds={len(feed_urls)}, failed={fail_count}"
    )
    print(f"- 页面：{OUTPUT_DIR / 'index.html'}")
    print(f"- 合并 RSS：{COMBINED_FEED_PATH}")


if __name__ == "__main__":
    main()
