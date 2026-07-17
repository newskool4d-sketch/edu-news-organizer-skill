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


# ---------- 2단계: 동일보도 묶기·분류 ----------

GROUP_THRESHOLD = 0.60  # 정규화 제목 SequenceMatcher 비율 (실데이터 보정값)


def normalize_title(title: str) -> str:
    """묶음 판정용 제목 정규화: 영숫자·한글만 남긴다."""
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", title).lower()


def title_similarity(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


# 기사 성격 규칙 — 위에서부터 첫 일치 적용 (계획서 Ⅳ-4장 7유형)
OTHER_REGIONS = ["서울", "부산", "대구", "광주", "대전", "울산", "세종", "경기", "강원",
                 "충북", "충남", "전북", "전남", "경북", "경남", "제주"]
TYPE_RULES = [
    ("인터뷰·기획", ["인터뷰", "[기획", "특집", "대담", "기고"]),
    ("비판·점검", ["질타", "지적", "논란", "반발", "촉구", "감사", "의혹", "수사", "사고",
                "부실", "비판", "규탄", "삭감", "미흡", "험로", "민원"]),
    ("정책·현안", ["공약", "법 개정", "법안", "조례", "정책", "제도", "개정", "대책",
                "실천계획", "기본계획", "방안", "개편"]),
    ("행사·모집", ["모집", "개최", "캠프", "연수", "특강", "설명회", "공모전", "행사",
                "축제", "체험", "교실", "성료", "워크숍"]),
    ("사업·성과", ["협약", "수상", "선정", "성과", "지원", "확대", "구축", "개교", "준공",
                "출원", "수주", "운영"]),
]


def classify_type(title: str) -> str:
    if "인천" not in title and ("교육청" in title or "교육감" in title):
        if any(r in title for r in OTHER_REGIONS):
            return "타 시도 동향"
    for type_name, keywords in TYPE_RULES:
        if any(k in title for k in keywords):
            return type_name
    return "기타"


# 교육 분야 규칙 — 복수 태그 (계획서 Ⅳ-5장 14분야)
FIELD_RULES = [
    ("교권·교원정책", ["교권", "교원", "교사", "아동학대", "교육활동 보호", "교장", "교감"]),
    ("학생안전·생활교육", ["안전", "학교폭력", "생활교육", "재해", "폭염", "호우", "흡연", "중독"]),
    ("진로·직업교육", ["진로", "직업", "취업", "도제", "직업계고"]),
    ("AI·디지털교육", ["AI", "인공지능", "디지털", "코딩", "에듀테크", "플랫폼", "미디어"]),
    ("교육과정", ["교육과정", "수업", "학력", "성취기준", "논·서술형", "평가"]),
    ("늘봄·돌봄", ["늘봄", "돌봄", "방과후"]),
    ("특수교육", ["특수", "장애"]),
    ("다문화·국제교육", ["다문화", "국제", "재외동포", "이주배경", "글로벌", "외국어", "고려인"]),
    ("체험교육·학생자치", ["체험", "캠프", "수련", "학생자치", "리더십", "학생회", "수학여행"]),
    ("예산·시설·감사", ["예산", "시설", "감사", "신설", "청사", "개축", "결산", "추경", "이전"]),
    ("도서관·평생교육", ["도서관", "평생교육", "평생학습", "독서"]),
    ("교육행정", ["인사", "조직", "채용", "임용", "적극행정", "개청"]),
    ("교육복지", ["급식", "복지", "조식", "장학", "결식"]),
]


def classify_fields(title: str) -> list:
    fields = [name for name, keywords in FIELD_RULES if any(k in title for k in keywords)]
    return fields or ["기타"]


def build_groups(conn, batch_date: str) -> int:
    """같은 날짜 기사 중 유사 제목을 묶어 article_groups에 저장. 재실행 시 해당 날짜 그룹 재생성.

    반환: 생성된 그룹(2건 이상 묶음) 수. 단독 기사는 그룹을 만들지 않는다.
    """
    # 멱등성: 이 날짜 기사가 속한 기존 그룹 제거 후 재생성
    old = [r[0] for r in conn.execute(
        "SELECT DISTINCT i.group_id FROM article_group_items i "
        "JOIN articles a ON a.article_id = i.article_id WHERE a.batch_date = ?",
        (batch_date,)).fetchall()]
    if old:
        marks = ",".join("?" * len(old))
        conn.execute(f"DELETE FROM article_group_items WHERE group_id IN ({marks})", old)
        conn.execute(f"DELETE FROM article_groups WHERE group_id IN ({marks})", old)

    rows = conn.execute(
        "SELECT article_id, title, published_at, input_order FROM articles "
        "WHERE batch_date = ? ORDER BY article_id", (batch_date,)).fetchall()

    # union-find로 유사 쌍 병합
    parent = {r["article_id"]: r["article_id"] for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    norm = {r["article_id"]: normalize_title(r["title"]) for r in rows}
    import difflib
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            ratio = difflib.SequenceMatcher(None, norm[a["article_id"]], norm[b["article_id"]]).ratio()
            if ratio >= GROUP_THRESHOLD:
                parent[find(a["article_id"])] = find(b["article_id"])

    clusters = {}
    for r in rows:
        clusters.setdefault(find(r["article_id"]), []).append(r)

    made = 0
    for members in clusters.values():
        if len(members) < 2:
            continue
        # 대표기사: 발행시각 빠른 기사 (없으면 입력 순서)
        rep = min(members, key=lambda r: (r["published_at"] or "9999", r["input_order"]))
        cur = conn.execute(
            "INSERT INTO article_groups(group_title, summary, category, article_type, priority, "
            "representative_article_id, created_at, updated_at) VALUES (?, '', ?, ?, 0, ?, ?, ?)",
            (rep["title"], ",".join(classify_fields(rep["title"])), classify_type(rep["title"]),
             rep["article_id"], _now(), _now()))
        gid = cur.lastrowid
        for m in members:
            conn.execute(
                "INSERT OR IGNORE INTO article_group_items(group_id, article_id, similarity_type, similarity_score) "
                "VALUES (?, ?, 'title', ?)",
                (gid, m["article_id"],
                 title_similarity(rep["title"], m["title"])))
        made += 1
    conn.commit()
    return made


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
    # 마이그레이션: 2단계 분류 컬럼 (기존 DB 호환)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(articles)")}
    if "article_type" not in cols:
        conn.execute("ALTER TABLE articles ADD COLUMN article_type TEXT DEFAULT ''")
    if "edu_fields" not in cols:
        conn.execute("ALTER TABLE articles ADD COLUMN edu_fields TEXT DEFAULT ''")
    conn.commit()
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
        title = a.get("title", "")
        cur = conn.execute(
            "INSERT OR IGNORE INTO articles(batch_id, batch_date, list_type, title, publisher, "
            "publisher_domain, original_url, clean_url, published_at, engine, queries, input_order, "
            "collected_at, article_type, edu_fields) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (batch_id, batch_date, list_type, title, a.get("publisher", ""),
             a.get("publisher_domain", "") or urllib.parse.urlsplit(url).netloc,
             url, clean_url(url), a.get("published_at", ""), a.get("engine", ""),
             a.get("queries", ""), order, _now(),
             classify_type(title), ",".join(classify_fields(title))))
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
                    include_excluded=False, selected=False, article_type=None,
                    edu_field=None, limit=100):
    """검색 결과를 dict 목록으로 반환. interest_hits는 조회 시점 관심 키워드 대조."""
    sql = ("SELECT a.*, COALESCE(u.read_status, 'unread') AS read_status, "
           "COALESCE(u.favorite, 0) AS favorite, COALESCE(u.excluded, 0) AS excluded, "
           "COALESCE(u.memo, '') AS memo, COALESCE(u.user_category, '') AS user_category "
           "FROM articles a LEFT JOIN user_actions u "
           "ON u.target_type = 'article' AND u.target_id = a.article_id WHERE 1=1")
    vals = []
    if selected:
        # 선별 기사 = 브리핑 md 배치로 처음 저장된 기사 (풀 전용 후보 제외)
        sql += (" AND a.batch_id IN (SELECT batch_id FROM source_batches "
                "WHERE source_kind = 'briefing-md')")
    if article_type:
        sql += " AND a.article_type = ?"; vals.append(article_type)
    if edu_field:
        sql += " AND a.edu_fields LIKE ?"; vals.append(f"%{edu_field}%")
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


TYPE_ORDER = ["정책·현안", "비판·점검", "인터뷰·기획", "사업·성과", "타 시도 동향", "행사·모집", "기타"]


def list_groups(conn, batch_date: str) -> list:
    """날짜별 그룹 목록 (대표기사·구성원 포함), 성격 우선순위 정렬."""
    groups = []
    for g in conn.execute(
            "SELECT g.*, COUNT(i.article_id) AS member_count FROM article_groups g "
            "JOIN article_group_items i ON i.group_id = g.group_id "
            "JOIN articles a ON a.article_id = g.representative_article_id "
            "WHERE a.batch_date = ? GROUP BY g.group_id", (batch_date,)).fetchall():
        d = dict(g)
        d["members"] = [dict(r) for r in conn.execute(
            "SELECT a.article_id, a.title, "
            "COALESCE(NULLIF(a.publisher, ''), a.publisher_domain) AS publisher, "
            "a.original_url FROM article_group_items i "
            "JOIN articles a ON a.article_id = i.article_id WHERE i.group_id = ? "
            "ORDER BY a.published_at", (g["group_id"],)).fetchall()]
        groups.append(d)
    groups.sort(key=lambda g: (TYPE_ORDER.index(g["article_type"]) if g["article_type"] in TYPE_ORDER else 99,
                               -g["member_count"]))
    return groups


def set_group_summary(conn, group_id: int, summary: str):
    conn.execute("UPDATE article_groups SET summary = ?, updated_at = ? WHERE group_id = ?",
                 (summary, _now(), group_id))
    conn.commit()


def build_digest_data(conn, batch_date: str) -> dict:
    """다이제스트 공유 데이터 구조. md·html 렌더러가 모두 이 데이터를 소비한다."""
    rows = search_articles(conn, date=batch_date, limit=2000)
    groups = list_groups(conn, batch_date)
    grouped_ids = {m["article_id"] for g in groups for m in g["members"]}

    hero_issues = []
    for g in groups:
        rep = next((m for m in g["members"] if m["article_id"] == g["representative_article_id"]),
                   g["members"][0])
        others = [m for m in g["members"] if m["article_id"] != rep["article_id"]]
        hero_issues.append({
            "group_id": g["group_id"],
            "title": g["group_title"],
            "article_type": g["article_type"] or "기타",
            "fields": [f for f in (g["category"] or "").split(",") if f],
            "member_count": g["member_count"],
            "summary": g["summary"],
            "representative": rep,
            "others": others,
        })

    interest_articles = [r for r in rows if r["interest_hits"]]

    singles = [r for r in rows if r["article_id"] not in grouped_ids]
    by_type = {}
    for r in singles:
        by_type.setdefault(r["article_type"] or "기타", []).append(r)
    all_by_type = [(t, by_type[t]) for t in TYPE_ORDER if t in by_type]

    publishers = {r["publisher"] for r in rows if r["publisher"]}
    return {
        "batch_date": batch_date,
        "meta": {
            "total": len(rows),
            "issue_count": len(hero_issues),
            "interest_count": len(interest_articles),
            "publisher_count": len(publishers),
        },
        "hero_issues": hero_issues,
        "interest_articles": interest_articles,
        "all_by_type": all_by_type,
    }


def digest_md(conn, batch_date: str) -> str:
    """계획서 10.2 형식의 일일 정리 md: 핵심 이슈(묶음) → 내 관심업무 → 전체 기사."""
    data = build_digest_data(conn, batch_date)
    lines = [f"# {batch_date} 교육뉴스 정리", ""]

    if data["hero_issues"]:
        lines += ["## 오늘의 핵심 이슈", ""]
        for g in data["hero_issues"]:
            rep = g["representative"]
            lines.append(f"### {g['title']}")
            lines.append("")
            lines.append(f"- 성격: {g['article_type']} / 분야: {', '.join(g['fields'])}")
            lines.append(f"- 관련 보도: {g['member_count']}건")
            if g["summary"]:
                lines.append(f"- 요약: {g['summary']}")
            lines.append(f"- 대표기사: {rep['title']} - {rep['publisher']}")
            lines.append(f"  {rep['original_url']}")
            if g["others"]:
                lines.append("- 관련 기사: " + " / ".join(m["publisher"] for m in g["others"]))
            lines.append("")

    if data["interest_articles"]:
        lines += ["## 내 관심업무", ""]
        for r in data["interest_articles"]:
            lines.append(f"■ {r['title']} - {r['publisher']}  [{'·'.join(r['interest_hits'])}]")
            lines.append(r["original_url"])
            if r["memo"]:
                lines.append(f"  메모: {r['memo']}")
            lines.append("")

    lines += ["## 전체 기사 (묶음 외)", ""]
    for t, items in data["all_by_type"]:
        lines.append(f"### {t}")
        lines.append("")
        for r in items:
            lines.append(f"■ {r['title']} - {r['publisher']}")
            lines.append(r["original_url"])
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_digest_html(data: dict) -> str:
    """프리미엄 HTML 다이제스트 렌더. 인천교육청 CI 팔레트·로고 임베드. digest_html 모듈 위임."""
    import digest_html
    return digest_html.render(data, load_logo_datauri())


def load_logo_datauri() -> str:
    p = Path(__file__).parent / "incheon_logo.txt"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""


def classify_backfill(conn) -> int:
    """분류 누락 기사(기존 DB) 재분류."""
    n = 0
    for r in conn.execute("SELECT article_id, title FROM articles "
                          "WHERE article_type = '' OR article_type IS NULL").fetchall():
        conn.execute("UPDATE articles SET article_type = ?, edu_fields = ? WHERE article_id = ?",
                     (classify_type(r["title"]), ",".join(classify_fields(r["title"])), r["article_id"]))
        n += 1
    conn.commit()
    return n


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

    p = sub.add_parser("group", help="동일보도 묶음 생성/재생성")
    p.add_argument("--date", required=True)

    p = sub.add_parser("groups", help="묶음 목록·요약 관리")
    p.add_argument("action", choices=["list", "summary"])
    p.add_argument("--date")
    p.add_argument("--id", type=int)
    p.add_argument("--text")

    p = sub.add_parser("digest", help="일일 정리 (핵심 이슈→관심→전체)")
    p.add_argument("--date", required=True)
    p.add_argument("--format", choices=["md", "html"], default="md")
    p.add_argument("--out")

    sub.add_parser("classify", help="분류 누락 기사 재분류 (기존 DB 백필)")

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

    elif args.cmd == "group":
        made = build_groups(conn, args.date)
        print(f"{args.date}: 묶음 {made}개 생성")

    elif args.cmd == "groups":
        if args.action == "list":
            if not args.date:
                parser.error("groups list에는 --date가 필요합니다")
            for g in list_groups(conn, args.date):
                summary = f" | {g['summary']}" if g["summary"] else ""
                print(f"[G{g['group_id']}] ({g['article_type']}/{g['category']}) "
                      f"{g['group_title']} — 관련 {g['member_count']}건{summary}")
                for m in g["members"]:
                    print(f"    #{m['article_id']} {m['publisher']}: {m['title']}")
        else:
            if not (args.id and args.text is not None):
                parser.error("groups summary에는 --id와 --text가 필요합니다")
            set_group_summary(conn, args.id, args.text)
            print(f"G{args.id} 요약 저장")

    elif args.cmd == "digest":
        if args.format == "html":
            content = render_digest_html(build_digest_data(conn, args.date))
        else:
            content = digest_md(conn, args.date)
        if args.out:
            Path(args.out).write_text(content, encoding="utf-8", newline="\n")
            print(f"저장: {args.out}")
        else:
            print(content)

    elif args.cmd == "classify":
        n = classify_backfill(conn)
        print(f"재분류 {n}건")

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
