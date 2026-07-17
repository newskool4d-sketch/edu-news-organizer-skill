# -*- coding: utf-8 -*-
"""edu-news-organizer 데이터 계층 + CLI (Python 표준 라이브러리 전용).

입력 계약: references/data-contract.md
- ingest --json     : daily-news-picker collect_news_rss.py 산출 collected_articles.json
- ingest --briefing : daily-news-picker 일일 브리핑 md (■ 제목 - 매체 / URL 형식)
- ingest --paste    : 본청 배포 뉴스 목록 텍스트 (전국·교육부 / 인천교육 2종 헤더)
"""
import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                   "ref", "source", "fbclid", "wlog_tag3", "outurl", "influxdiv"}

DATE_HEADER_RE = re.compile(
    r"^(20\d{2})\.\s*(\d{1,2})\.\s*(\d{1,2})\.\s*\(.\)\s*(.+?)\s*주요 언론보도 현황입니다")
ARTICLE_LINE_RE = re.compile(r"^■\s+(.+)$")


# ---------- 순수 함수 ----------

def clean_url(url: str) -> str:
    """추적 파라미터만 제거하고 나머지는 보존한다. 원본은 별도 컬럼에 저장."""
    parts = urllib.parse.urlsplit(url)
    if not parts.query:
        return url
    kept = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS]
    query = urllib.parse.urlencode(kept)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def match_interests(title: str, keywords: list) -> list:
    """제목에 포함된 관심 키워드를 등록 순서대로 반환한다."""
    return [k for k in keywords if k in title]


def _header_to_batch(m: re.Match):
    batch_date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    subject = m.group(4)
    if "교육부" in subject or "시·도교육청" in subject or "시도교육청" in subject:
        list_type = "전국·교육부"
    else:
        list_type = "인천교육"
    return batch_date, list_type


def _parse_share_lines(lines: list) -> list:
    """`■ 제목 - 매체` 다음 줄 URL 형식의 기사 목록을 파싱한다."""
    articles = []
    pending = None
    for line in lines:
        line = line.strip()
        m = ARTICLE_LINE_RE.match(line)
        if m:
            body = m.group(1).strip()
            # 매체명은 마지막 ' - ' 뒤 (제목 자체에 ' - '가 올 수 있어 rsplit)
            if " - " in body:
                title, publisher = body.rsplit(" - ", 1)
            else:
                title, publisher = body, ""
            pending = {"title": title.strip(), "publisher": publisher.strip()}
            continue
        if pending and line.startswith("http"):
            pending["original_url"] = line
            articles.append(pending)
            pending = None
    return articles


def parse_briefing_md(text: str):
    """daily-news-picker 일일 브리핑 md → (batch_date, list_type, articles)."""
    batch_date, list_type = None, "인천교육"
    for line in text.splitlines():
        m = DATE_HEADER_RE.match(line.strip())
        if m:
            batch_date, list_type = _header_to_batch(m)
            break
    articles = _parse_share_lines(text.splitlines())
    return batch_date, list_type, articles


def parse_paste(text: str) -> list:
    """본청 배포 목록(복수 섹션 가능) → [(batch_date, list_type, articles), ...]."""
    batches = []
    current_header = None
    current_lines = []
    for line in text.splitlines():
        m = DATE_HEADER_RE.match(line.strip())
        if m:
            if current_header:
                d, t = current_header
                batches.append((d, t, _parse_share_lines(current_lines)))
            current_header = _header_to_batch(m)
            current_lines = []
        else:
            current_lines.append(line)
    if current_header:
        d, t = current_header
        batches.append((d, t, _parse_share_lines(current_lines)))
    return batches


def parse_collected_json(text: str):
    """collect_news_rss.py 산출 JSON → (batch_date, list_type, articles)."""
    payload = json.loads(text)
    batch_date = payload["window_end"].split(" ")[0]
    articles = []
    for a in payload.get("articles", []):
        articles.append({
            "title": a.get("title", ""),
            "publisher": a.get("publisher", ""),
            "publisher_domain": a.get("publisher_domain", ""),
            "original_url": a.get("original_url") or a.get("google_url") or "",
            "published_at": a.get("published_at_kst", ""),
            "engine": a.get("engine", ""),
            "queries": ",".join(a.get("queries", [])),
        })
    return batch_date, "인천교육", articles


# ---------- 저장 계층 ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_batches (
    batch_id INTEGER PRIMARY KEY,
    batch_date TEXT NOT NULL,
    list_type TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    raw_text TEXT,
    article_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS articles (
    article_id INTEGER PRIMARY KEY,
    batch_id INTEGER REFERENCES source_batches(batch_id),
    batch_date TEXT NOT NULL,
    list_type TEXT NOT NULL,
    title TEXT NOT NULL,
    publisher TEXT DEFAULT '',
    publisher_domain TEXT DEFAULT '',
    original_url TEXT NOT NULL,
    clean_url TEXT NOT NULL UNIQUE,
    published_at TEXT DEFAULT '',
    body_status TEXT DEFAULT '본문 미수집',
    engine TEXT DEFAULT '',
    queries TEXT DEFAULT '',
    input_order INTEGER DEFAULT 0,
    collected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_actions (
    action_id INTEGER PRIMARY KEY,
    target_type TEXT NOT NULL DEFAULT 'article',
    target_id INTEGER NOT NULL,
    read_status TEXT NOT NULL DEFAULT 'unread',
    favorite INTEGER NOT NULL DEFAULT 0,
    excluded INTEGER NOT NULL DEFAULT 0,
    memo TEXT DEFAULT '',
    user_category TEXT DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE(target_type, target_id)
);
CREATE TABLE IF NOT EXISTS interests (
    interest_id INTEGER PRIMARY KEY,
    keyword TEXT NOT NULL UNIQUE,
    category TEXT DEFAULT '',
    weight INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
-- 2단계(스마트 정리)용 스키마 예약 — 계획서 Ⅶ장 구조 유지
CREATE TABLE IF NOT EXISTS article_groups (
    group_id INTEGER PRIMARY KEY,
    group_title TEXT NOT NULL,
    summary TEXT DEFAULT '',
    category TEXT DEFAULT '',
    article_type TEXT DEFAULT '',
    priority INTEGER DEFAULT 0,
    representative_article_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS article_group_items (
    group_id INTEGER NOT NULL REFERENCES article_groups(group_id),
    article_id INTEGER NOT NULL REFERENCES articles(article_id),
    similarity_type TEXT DEFAULT '',
    similarity_score REAL DEFAULT 0,
    UNIQUE(group_id, article_id)
);
CREATE INDEX IF NOT EXISTS idx_articles_batch_date ON articles(batch_date);
CREATE INDEX IF NOT EXISTS idx_articles_title ON articles(title);
"""

SEED_INTERESTS = ["학생교육원", "체험교육", "학생안전", "수련활동", "학생자치",
                  "AI교육", "진로체험", "교육활동 보호", "교원정책", "인천교육"]


def default_db_path() -> str:
    env = os.environ.get("EDU_NEWS_DB_PATH")
    if env:
        return env
    return str(Path.home() / "Documents" / "Codex" / "EduNewsOrganizer" / "news.db")


def open_db(db_path: str = None) -> sqlite3.Connection:
    path = Path(db_path or default_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ingest_articles(conn, batch_date, list_type, articles, source_kind, raw_text):
    """기사 목록 저장. 반환: (신규 건수, 중복 건수). 원본은 source_batches에 보존."""
    cur = conn.execute(
        "INSERT INTO source_batches(batch_date, list_type, source_kind, raw_text, article_count, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (batch_date, list_type, source_kind, raw_text, len(articles), _now()))
    batch_id = cur.lastrowid
    new, dup = 0, 0
    for order, a in enumerate(articles, 1):
        url = a.get("original_url", "")
        if not url:
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO articles(batch_id, batch_date, list_type, title, publisher, "
            "publisher_domain, original_url, clean_url, published_at, engine, queries, input_order, collected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (batch_id, batch_date, list_type, a.get("title", ""), a.get("publisher", ""),
             a.get("publisher_domain", "") or urllib.parse.urlsplit(url).netloc,
             url, clean_url(url), a.get("published_at", ""), a.get("engine", ""),
             a.get("queries", ""), order, _now()))
        if cur.rowcount == 1:
            new += 1
        else:
            dup += 1
    conn.commit()
    return new, dup


def add_interest(conn, keyword, category="", weight=1):
    conn.execute("INSERT OR IGNORE INTO interests(keyword, category, weight, created_at) VALUES (?, ?, ?, ?)",
                 (keyword.strip(), category, weight, _now()))
    conn.commit()


def remove_interest(conn, keyword):
    conn.execute("UPDATE interests SET active = 0 WHERE keyword = ?", (keyword.strip(),))
    conn.commit()


def active_keywords(conn) -> list:
    rows = conn.execute("SELECT keyword FROM interests WHERE active = 1 ORDER BY interest_id").fetchall()
    return [r["keyword"] for r in rows]


def mark_article(conn, article_id, read_status=None, favorite=None, excluded=None,
                 memo=None, user_category=None):
    conn.execute(
        "INSERT INTO user_actions(target_type, target_id, updated_at) VALUES ('article', ?, ?) "
        "ON CONFLICT(target_type, target_id) DO NOTHING",
        (article_id, _now()))
    sets, vals = ["updated_at = ?"], [_now()]
    if read_status is not None:
        sets.append("read_status = ?"); vals.append(read_status)
    if favorite is not None:
        sets.append("favorite = ?"); vals.append(1 if favorite else 0)
    if excluded is not None:
        sets.append("excluded = ?"); vals.append(1 if excluded else 0)
    if memo is not None:
        sets.append("memo = ?"); vals.append(memo)
    if user_category is not None:
        sets.append("user_category = ?"); vals.append(user_category)
    vals.extend(["article", article_id])
    conn.execute(f"UPDATE user_actions SET {', '.join(sets)} WHERE target_type = ? AND target_id = ?", vals)
    conn.commit()


def search_articles(conn, q=None, date=None, list_type=None, publisher=None,
                    favorite=None, read_status=None, interest_only=False,
                    include_excluded=False, limit=100):
    """검색 결과를 dict 목록으로 반환. interest_hits는 조회 시점 관심 키워드 대조."""
    sql = ("SELECT a.*, COALESCE(u.read_status, 'unread') AS read_status, "
           "COALESCE(u.favorite, 0) AS favorite, COALESCE(u.excluded, 0) AS excluded, "
           "COALESCE(u.memo, '') AS memo, COALESCE(u.user_category, '') AS user_category "
           "FROM articles a LEFT JOIN user_actions u "
           "ON u.target_type = 'article' AND u.target_id = a.article_id WHERE 1=1")
    vals = []
    if q:
        sql += " AND (a.title LIKE ? OR a.publisher LIKE ?)"
        vals.extend([f"%{q}%", f"%{q}%"])
    if date:
        sql += " AND a.batch_date = ?"; vals.append(date)
    if list_type:
        sql += " AND a.list_type = ?"; vals.append(list_type)
    if publisher:
        sql += " AND a.publisher LIKE ?"; vals.append(f"%{publisher}%")
    if favorite:
        sql += " AND COALESCE(u.favorite, 0) = 1"
    if read_status:
        sql += " AND COALESCE(u.read_status, 'unread') = ?"; vals.append(read_status)
    if not include_excluded:
        sql += " AND COALESCE(u.excluded, 0) = 0"
    sql += " ORDER BY a.batch_date DESC, a.input_order"
    # interest_only는 SQL 밖에서 거르므로 LIMIT을 먼저 걸면 관심 기사가 잘려 나간다 — 필터 후 절단
    if not interest_only:
        sql += " LIMIT ?"
        vals.append(limit)

    keywords = active_keywords(conn)
    rows = []
    for r in conn.execute(sql, vals).fetchall():
        d = dict(r)
        d["interest_hits"] = match_interests(d["title"], keywords)
        rows.append(d)
    if interest_only:
        rows = [r for r in rows if r["interest_hits"]][:limit]
    return rows


def export_md(conn, batch_date=None, rows=None) -> str:
    """공유 문안 호환(■ 제목 - 매체 / URL) md 생성."""
    if rows is None:
        rows = search_articles(conn, date=batch_date, limit=1000)
    header_date = batch_date or (rows[0]["batch_date"] if rows else "")
    lines = [f"# 교육뉴스 정리 ({header_date})", ""]
    by_type = {}
    for r in rows:
        by_type.setdefault(r["list_type"], []).append(r)
    interest_rows = [r for r in rows if r["interest_hits"]]
    if interest_rows:
        lines.append("## 내 관심업무")
        lines.append("")
        for r in interest_rows:
            tags = "·".join(r["interest_hits"])
            lines.append(f"■ {r['title']} - {r['publisher']}  [{tags}]")
            lines.append(r["original_url"])
            if r["memo"]:
                lines.append(f"  메모: {r['memo']}")
            lines.append("")
    for list_type, items in by_type.items():
        lines.append(f"## {list_type}")
        lines.append("")
        for r in items:
            lines.append(f"■ {r['title']} - {r['publisher']}")
            lines.append(r["original_url"])
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def export_csv_rows(rows) -> list:
    header = ["article_id", "batch_date", "list_type", "title", "publisher",
              "original_url", "published_at", "read_status", "favorite", "memo", "interest_hits"]
    out = [header]
    for r in rows:
        out.append([r["article_id"], r["batch_date"], r["list_type"], r["title"], r["publisher"],
                    r["original_url"], r["published_at"], r["read_status"], r["favorite"],
                    r["memo"], "·".join(r["interest_hits"])])
    return out


# ---------- CLI ----------

def _print_rows(rows):
    if not rows:
        print("결과 없음")
        return
    for r in rows:
        flags = []
        if r["favorite"]:
            flags.append("★보관")
        if r["read_status"] != "unread":
            flags.append(r["read_status"])
        if r["interest_hits"]:
            flags.append("관심:" + "·".join(r["interest_hits"]))
        flag_s = (" [" + ", ".join(flags) + "]") if flags else ""
        print(f"#{r['article_id']} ({r['batch_date']}/{r['list_type']}) {r['title']} - {r['publisher']}{flag_s}")
        print(f"   {r['original_url']}")
        if r["memo"]:
            print(f"   메모: {r['memo']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="개인형 교육뉴스 정리 도구")
    parser.add_argument("--db", help="DB 경로 (기본: %%USERPROFILE%%\\Documents\\Codex\\EduNewsOrganizer\\news.db)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="뉴스 목록 저장")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--json", help="collected_articles.json 경로")
    g.add_argument("--briefing", help="일일 브리핑 md 경로")
    g.add_argument("--paste", help="본청 배포 목록 텍스트 파일 경로")

    p = sub.add_parser("search", help="기사 검색")
    p.add_argument("--q")
    p.add_argument("--date")
    p.add_argument("--list-type", dest="list_type")
    p.add_argument("--publisher")
    p.add_argument("--favorite", action="store_true")
    p.add_argument("--read-status", dest="read_status", choices=["unread", "read", "later"])
    p.add_argument("--interest", action="store_true", help="관심 키워드 일치 기사만")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("mark", help="읽음·보관·메모·분류")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--read", action="store_true")
    p.add_argument("--unread", action="store_true")
    p.add_argument("--later", action="store_true")
    p.add_argument("--favorite", action="store_true")
    p.add_argument("--unfavorite", action="store_true")
    p.add_argument("--exclude", action="store_true")
    p.add_argument("--memo")
    p.add_argument("--category")

    p = sub.add_parser("interests", help="관심 키워드 관리")
    p.add_argument("action", choices=["list", "add", "remove", "seed"])
    p.add_argument("keyword", nargs="?")

    p = sub.add_parser("export", help="md/csv 출력")
    p.add_argument("--date")
    p.add_argument("--format", choices=["md", "csv"], default="md")
    p.add_argument("--out", help="저장 경로 (생략 시 표준 출력)")

    sub.add_parser("stats", help="누적 현황")

    args = parser.parse_args(argv)
    conn = open_db(args.db)

    if args.cmd == "ingest":
        if args.json:
            text = Path(args.json).read_text(encoding="utf-8")
            batches = [parse_collected_json(text)]
            kind = "collector-json"
        elif args.briefing:
            text = Path(args.briefing).read_text(encoding="utf-8")
            batches = [parse_briefing_md(text)]
            kind = "briefing-md"
        else:
            text = Path(args.paste).read_text(encoding="utf-8")
            batches = parse_paste(text)
            kind = "paste"
        for batch_date, list_type, articles in batches:
            if not batch_date:
                print("경고: 날짜 헤더를 찾지 못해 이 배치를 건너뜁니다.")
                continue
            new, dup = ingest_articles(conn, batch_date, list_type, articles, kind, text)
            print(f"[{batch_date}/{list_type}] 신규 {new}건, 중복 {dup}건 (입력 {len(articles)}건)")

    elif args.cmd == "search":
        rows = search_articles(conn, q=args.q, date=args.date, list_type=args.list_type,
                               publisher=args.publisher, favorite=args.favorite,
                               read_status=args.read_status, interest_only=args.interest,
                               limit=args.limit)
        _print_rows(rows)

    elif args.cmd == "mark":
        read_status = "read" if args.read else ("unread" if args.unread else ("later" if args.later else None))
        favorite = True if args.favorite else (False if args.unfavorite else None)
        excluded = True if args.exclude else None
        mark_article(conn, args.id, read_status=read_status, favorite=favorite,
                     excluded=excluded, memo=args.memo, user_category=args.category)
        print(f"#{args.id} 반영 완료")

    elif args.cmd == "interests":
        if args.action == "list":
            for k in active_keywords(conn):
                print(f"- {k}")
        elif args.action == "add":
            add_interest(conn, args.keyword)
            print(f"추가: {args.keyword}")
        elif args.action == "remove":
            remove_interest(conn, args.keyword)
            print(f"비활성화: {args.keyword}")
        elif args.action == "seed":
            for k in SEED_INTERESTS:
                add_interest(conn, k)
            print(f"기본 관심 키워드 {len(SEED_INTERESTS)}개 등록")

    elif args.cmd == "export":
        rows = search_articles(conn, date=args.date, limit=1000)
        if args.format == "md":
            content = export_md(conn, batch_date=args.date, rows=rows)
        else:
            buf = []
            for row in export_csv_rows(rows):
                buf.append(",".join('"' + str(c).replace('"', '""') + '"' for c in row))
            content = "\n".join(buf) + "\n"
        if args.out:
            Path(args.out).write_text(content, encoding="utf-8", newline="\n")
            print(f"저장: {args.out} ({len(rows)}건)")
        else:
            print(content)

    elif args.cmd == "stats":
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        dates = conn.execute("SELECT COUNT(DISTINCT batch_date) FROM articles").fetchone()[0]
        favs = conn.execute("SELECT COUNT(*) FROM user_actions WHERE favorite = 1").fetchone()[0]
        memos = conn.execute("SELECT COUNT(*) FROM user_actions WHERE memo != ''").fetchone()[0]
        print(f"기사 {total}건 / 수록일 {dates}일 / 보관 {favs}건 / 메모 {memos}건")
        for r in conn.execute("SELECT batch_date, list_type, COUNT(*) c FROM articles "
                              "GROUP BY batch_date, list_type ORDER BY batch_date DESC LIMIT 10"):
            print(f"  {r['batch_date']} {r['list_type']}: {r['c']}건")

    conn.close()


if __name__ == "__main__":
    main()
