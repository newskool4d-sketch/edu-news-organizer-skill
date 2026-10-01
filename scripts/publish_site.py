# -*- coding: utf-8 -*-
"""edu-news-organizer → 정적 사이트 배포기 (Vercel/Netlify/Cloudflare Pages 등).

A안: 생성은 로컬, 산출물만 배포. 매일 공개 안전본 다이제스트를 site/ 구조로 굽고
(개인 데이터 제외·비공식 표기), 선택적으로 git 커밋·푸시하면 Vercel Git 연동이 자동 배포한다.

site/
  index.html            # 최신 날짜 공개 다이제스트
  archive/2026-07-16.html
  archive/index.html     # 날짜별 목록

사용:
  python publish_site.py --date 2026-07-16 --site-dir <경로>
  python publish_site.py --date 2026-07-16 --site-dir <경로> --push   # git add/commit/push
  python publish_site.py --rebuild-index --site-dir <경로>            # 아카이브 인덱스만 재생성
"""
import argparse
import html as _html
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import newsdb  # noqa: E402

if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DATE_RE = re.compile(r"^(20\d{2}-\d{2}-\d{2})\.html$")
CONTRACT_PATH = Path(__file__).resolve().parents[1] / "references" / "publication-contract.json"
NAV_STYLE_RE = re.compile(
    r"\s*/\* EDU_NEWS_SITE_NAV_START \*/.*?/\* EDU_NEWS_SITE_NAV_END \*/\s*",
    re.DOTALL,
)
NAV_HTML_RE = re.compile(
    r"\s*<!-- EDU_NEWS_SITE_NAV_START -->.*?<!-- EDU_NEWS_SITE_NAV_END -->\s*",
    re.DOTALL,
)


def esc(s) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def load_publication_contract(path: Path = CONTRACT_PATH) -> dict:
    """승인된 공개 구조 계약을 읽는다. 누락·잠금 해제 상태면 게시를 중단한다."""
    contract = json.loads(path.read_text(encoding="utf-8"))
    if not contract.get("locked"):
        raise ValueError("공개 구조 계약이 잠금 상태가 아닙니다.")
    return contract


def validate_ingestion_contract(conn, date_iso: str, contract: dict) -> None:
    """해당 날짜의 최신 입력 순서가 briefing → collector인지 확인한다."""
    expected = contract["ingestion"]["order"]
    if expected != ["briefing-md", "collector-json"]:
        raise ValueError(f"지원하지 않는 입력 순서 계약입니다: {expected}")
    publishable_statuses = frozenset(
        contract["ingestion"]["publish_candidate_statuses"]
    )
    if publishable_statuses != newsdb.PUBLISHABLE_CANDIDATE_STATUSES:
        raise ValueError(
            "공개 입력 계약 위반: 후보 게시 상태 allow-list가 구현과 다릅니다."
        )
    if contract["ingestion"].get("publish_candidate_requires") != newsdb.PUBLICATION_REQUIRED_FIELDS:
        raise ValueError(
            "공개 입력 계약 위반: 후보 게시에는 본문 검증 메타데이터가 필요합니다."
        )
    briefing = conn.execute(
        "SELECT MAX(batch_id) FROM source_batches WHERE batch_date = ? "
        "AND source_kind IN ('briefing-md', 'paste')", (date_iso,)
    ).fetchone()[0]
    collector = conn.execute(
        "SELECT MAX(batch_id) FROM source_batches WHERE batch_date = ? "
        "AND source_kind = 'collector-json'", (date_iso,)
    ).fetchone()[0]
    if briefing is None or collector is None or briefing >= collector:
        raise ValueError(
            "공개 입력 순서 위반: briefing-md를 먼저, collector-json을 다음에 넣어야 합니다."
        )


def _group_memberships(conn, date_iso: str) -> tuple:
    groups = {}
    for group_id, article_id in conn.execute(
            "SELECT g.group_id, i.article_id FROM article_groups g "
            "JOIN article_group_items i ON i.group_id = g.group_id "
            "WHERE g.batch_date = ? ORDER BY g.group_id, i.article_id",
            (date_iso,)):
        groups.setdefault(group_id, []).append(article_id)
    return tuple(sorted(tuple(sorted(article_ids)) for article_ids in groups.values()))


def validate_grouping_state(conn, date_iso: str) -> None:
    """현재 DB의 동일보도 묶음이 최신 선별 관계와 일치하는지 확인한다."""
    actual = _group_memberships(conn, date_iso)
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    try:
        conn.backup(scratch)
        newsdb.build_groups(scratch, date_iso)
        expected = _group_memberships(scratch, date_iso)
    finally:
        scratch.close()
    if actual != expected:
        raise ValueError(
            "동일보도 묶음 상태 불일치: "
            f"newsdb.py group --date {date_iso} 실행 후 다시 게시하세요 "
            f"(현재 {len(actual)}개, 예상 {len(expected)}개)."
        )


def validate_publication_contract(html: str, contract: dict) -> None:
    """레이아웃·유형 순서·공개 안전 규칙이 승인 JSON과 같지 않으면 실패한다."""
    main_pos = html.find("<main")
    if main_pos < 0:
        raise ValueError("공개 구조 위반: <main>이 없습니다.")
    body = html[main_pos:]
    layout = contract["layout"]
    if layout.get("section_presence_policy") != "render_when_non_empty":
        raise ValueError("공개 구조 위반: 지원하지 않는 섹션 표시 정책입니다.")
    positions = []
    for section in layout["sections_in_order"]:
        pos = body.find(f"<h2>{section}")
        if pos >= 0:
            positions.append(pos)
    if positions != sorted(positions):
        raise ValueError("공개 구조 위반: 표시된 섹션 순서가 변경됐습니다.")
    for token in layout["required_css_tokens"]:
        if token not in html:
            raise ValueError(f"공개 구조 위반: 반응형 CSS 토큰 누락 - {token}")
    if newsdb.TYPE_ORDER != contract["article_type_order"]:
        raise ValueError("공개 구조 위반: 기사 유형 순서가 승인 계약과 다릅니다.")
    marker = contract["public_safety"]["required_marker"]
    if marker not in html:
        raise ValueError(f"공개 안전 위반: 필수 표기 누락 - {marker}")
    for forbidden in contract["public_safety"]["forbidden_text"]:
        if forbidden in body:
            raise ValueError(f"공개 안전 위반: 금지 문구 노출 - {forbidden}")


def _normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _title_keywords(title: str) -> set:
    tokens = re.findall(r"[가-힣A-Za-z0-9]{2,}", title or "")
    return {token for token in tokens if token not in {"인천", "교육청", "인천시"}}


def validate_briefing_published_content_parity(briefing_articles: list, published_html: str) -> None:
    """브리핑 명시 기사가 공개 HTML에 남았는지 URL 우선·제목 보조로 확인한다.

    동일보도 묶음은 대표 URL이 달라질 수 있으므로 URL이 없을 때 제목 핵심어가
    모두 공개 HTML에 존재하는지 2차 확인한다. 두 방식 모두 실패하면 release를 중단한다.
    """
    missing = []
    # digest_html._link가 href의 '&'를 '&amp;'로 이스케이프하므로 비교 전에
    # HTML entity를 복원한다. 제목 fallback은 URL이 대표 URL로 대체된 그룹에만 사용한다.
    html_lower = _html.unescape(published_html or "").lower()
    for article in briefing_articles:
        url = _normalize_url(article.get("original_url") or article.get("url"))
        if url and url.lower() in html_lower:
            continue
        keywords = _title_keywords(article.get("title", ""))
        matched_keywords = {
            keyword for keyword in keywords if keyword.lower() in html_lower
        }
        if (len(matched_keywords) >= 2
                and len(matched_keywords) / len(keywords) >= 0.6):
            continue
        missing.append(article.get("title") or url or "(제목·URL 없음)")
    if missing:
        raise ValueError(
            "브리핑-공개 콘텐츠 정합성 위반: " + "; ".join(missing[:5])
        )


def _fmt(date_iso: str) -> str:
    try:
        d = datetime.strptime(date_iso, "%Y-%m-%d")
        wd = ["월", "화", "수", "목", "금", "토", "일"][d.weekday()]
        return f"{d.year}. {d.month}. {d.day}. ({wd})"
    except Exception:
        return date_iso


def _fmt_short(date_iso: str) -> str:
    try:
        d = datetime.strptime(date_iso, "%Y-%m-%d")
        return f"{d.month}. {d.day}."
    except Exception:
        return date_iso


def render_archive_index(dates: list) -> str:
    """날짜 목록(ISO) → 아카이브 인덱스 HTML. 최신순 정렬, 자기완결."""
    dates = sorted(set(dates), reverse=True)
    items = "\n".join(
        f'    <li><a href="{esc(d)}.html"><span class="d">{esc(d)}</span>'
        f'<span class="w">{esc(_fmt(d))}</span></a></li>'
        for d in dates)
    return f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>인천교육 언론보도 브리핑 · 지난 호</title>
<style>
  :root {{ color-scheme:light; --blue:#0060B0; --ink:#12233A; --muted:#6b7c8e; --line:#dde5ec; --paper:#eef2f6;
    --flow:linear-gradient(115deg,#00A160,#0060B0 52%,#17B0D4); }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--paper); color:var(--ink);
    font-family:"Pretendard","SUIT","Apple SD Gothic Neo","Malgun Gothic",sans-serif; }}
  .flow-band {{ height:7px; background:var(--flow); }}
  .wrap {{ max-width:720px; margin:0 auto; padding:34px 22px 60px; }}
  h1 {{ font-size:25px; font-weight:800; letter-spacing:-.02em; margin:0 0 4px; }}
  .sub {{ color:var(--muted); font-size:14px; margin:0 0 24px; }}
  .home {{ display:inline-block; margin-bottom:20px; color:var(--blue); font-weight:700; text-decoration:none; }}
  ul {{ list-style:none; margin:0; padding:0; }}
  li a {{ display:flex; align-items:baseline; gap:12px; padding:14px 16px; margin-bottom:8px;
    background:#fff; border:1px solid var(--line); border-radius:12px; text-decoration:none; color:var(--ink);
    transition:transform .12s, box-shadow .12s; }}
  li a:hover {{ transform:translateY(-2px); box-shadow:0 8px 20px rgba(18,35,58,.08); }}
  .d {{ font-weight:800; color:var(--blue); }}
  .w {{ color:var(--muted); font-size:13.5px; }}
</style>
</head>
<body>
  <div class="flow-band"></div>
  <div class="wrap">
    <a class="home" href="../index.html">← 최신 브리핑</a>
    <h1>지난 호 보기</h1>
    <p class="sub">인천교육 언론보도 브리핑 아카이브 (비공식 · 개인 정리용)</p>
    <ul>
{items}
    </ul>
  </div>
</body>
</html>'''


def render_issue_page(digest_html: str, current_date: str, dates: list, location: str) -> str:
    """공개 다이제스트에 지난 호/현재 날짜/다음 호 탐색을 추가한다.

    location은 루트 최신 화면(root) 또는 archive 날짜 화면(archive)이다.
    기존에 삽입된 탐색 블록을 먼저 제거하므로 반복 빌드해도 중복되지 않는다.
    """
    if location not in {"root", "archive"}:
        raise ValueError(f"지원하지 않는 페이지 위치입니다: {location}")

    dates = sorted(set(dates), reverse=True)
    if current_date not in dates:
        raise ValueError(f"현재 날짜가 아카이브에 없습니다: {current_date}")

    digest_html = NAV_STYLE_RE.sub("\n", digest_html)
    digest_html = NAV_HTML_RE.sub("\n", digest_html)
    style_anchor = "</style>"
    main_anchor = '<main class="wrap">'
    if style_anchor not in digest_html or main_anchor not in digest_html:
        raise ValueError("다이제스트 HTML 구조에서 사이트 탐색 링크 삽입 위치를 찾지 못했습니다.")

    idx = dates.index(current_date)
    next_date = dates[idx - 1] if idx > 0 else None       # 더 최신 호
    prev_date = dates[idx + 1] if idx < len(dates) - 1 else None  # 더 과거 호

    if location == "root":
        archive_href = "archive/index.html"
        prev_href = f"archive/{prev_date}.html" if prev_date else None
        next_href = f"archive/{next_date}.html" if next_date else None
    else:
        archive_href = "index.html"
        prev_href = f"{prev_date}.html" if prev_date else None
        # 최신 호로 이동할 때는 중복 보관본이 아니라 canonical 루트로 연결한다.
        next_href = ("../index.html" if next_date == dates[0] else f"{next_date}.html") if next_date else None

    nav_css = '''
  /* EDU_NEWS_SITE_NAV_START */
  .issue-nav { max-width:1120px; margin:0 auto; padding:14px 22px;
    display:grid; grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);
    align-items:center; gap:12px; border-bottom:1px solid var(--line); }
  .issue-nav .nav-btn { display:inline-flex; align-items:center; width:max-content; max-width:100%;
    padding:7px 12px; border:1px solid var(--line); border-radius:8px; background:var(--card);
    color:var(--blue); font-size:13px; font-weight:800; line-height:1.35;
    box-shadow:0 1px 2px rgba(18,35,58,.04); }
  .issue-nav .nav-next { justify-self:end; }
  .issue-nav .nav-btn.disabled { color:var(--muted); opacity:.52; pointer-events:none; }
  .issue-now { text-align:center; font-size:14px; color:var(--ink); line-height:1.4; }
  .issue-now strong { display:block; font-size:14.5px; font-weight:800; font-variant-numeric:tabular-nums; }
  .issue-now a { display:inline-block; margin-top:3px; color:var(--muted); font-size:11.5px; font-weight:700; }
  @media (max-width:720px) {
    .issue-nav { grid-template-columns:1fr 1fr; padding:12px 20px; }
    .issue-now { grid-column:1 / -1; grid-row:1; }
    .issue-nav .nav-prev { grid-column:1; grid-row:2; }
    .issue-nav .nav-next { grid-column:2; grid-row:2; text-align:right; }
  }
  @media print { .issue-nav { display:none; } }
  /* EDU_NEWS_SITE_NAV_END */
'''

    if prev_href:
        prev_html = (f'<a class="nav-btn nav-prev" href="{esc(prev_href)}">'
                     f'◀ 지난 호 ({esc(_fmt_short(prev_date))})</a>')
    else:
        prev_html = '<span class="nav-btn nav-prev disabled">◀ 지난 호</span>'

    if next_href:
        next_html = (f'<a class="nav-btn nav-next" href="{esc(next_href)}">'
                     f'다음 호 ({esc(_fmt_short(next_date))}) ▶</a>')
    else:
        next_html = '<span class="nav-btn nav-next disabled">다음 호 예정 · 매일 09:00</span>'

    nav_html = f'''<!-- EDU_NEWS_SITE_NAV_START -->
  <nav class="issue-nav" aria-label="호별 브리핑 이동">
    {prev_html}
    <span class="issue-now"><strong>{esc(_fmt(current_date))}</strong>
      <a href="{esc(archive_href)}">전체 지난 호</a></span>
    {next_html}
  </nav>
  <!-- EDU_NEWS_SITE_NAV_END -->'''
    with_style = digest_html.replace(style_anchor, nav_css + style_anchor, 1)
    return with_style.replace(main_anchor, nav_html + "\n  " + main_anchor, 1)


def existing_dates(site_dir: Path) -> list:
    arch = site_dir / "archive"
    if not arch.exists():
        return []
    out = []
    for f in arch.iterdir():
        m = DATE_RE.match(f.name)
        if m:
            out.append(m.group(1))
    return out


def build(site_dir: Path, date_iso: str = None, db_path: str = None):
    """date_iso 공개 다이제스트를 site/archive에 굽고 최신본을 index.html로, 아카이브 인덱스를 갱신."""
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "archive").mkdir(exist_ok=True)

    if date_iso:
        contract = load_publication_contract()
        conn = newsdb.open_db(db_path)
        try:
            validate_ingestion_contract(conn, date_iso, contract)
            validate_grouping_state(conn, date_iso)
            html = newsdb.render_digest_html(newsdb.build_digest_data(conn, date_iso), public=True)
            validate_publication_contract(html, contract)
            briefing_articles = [
                dict(row) for row in conn.execute(
                    "SELECT DISTINCT a.title, a.original_url "
                    "FROM articles a JOIN article_selections s ON s.article_id = a.article_id "
                    "WHERE s.batch_date = ? AND s.source_kind IN ('briefing-md', 'paste') "
                    "ORDER BY s.input_order, a.article_id",
                    (date_iso,),
                ).fetchall()
            ]
            validate_briefing_published_content_parity(briefing_articles, html)
        finally:
            conn.close()
        (site_dir / "archive" / f"{date_iso}.html").write_text(html, encoding="utf-8", newline="\n")

    dates = sorted(set(existing_dates(site_dir)), reverse=True)
    if dates:
        # 모든 보관본을 다시 장식해야 새 호 추가 시 과거 페이지의 다음 호 링크도 갱신된다.
        for archived_date in dates:
            archived_path = site_dir / "archive" / f"{archived_date}.html"
            archived_html = archived_path.read_text(encoding="utf-8")
            archived_path.write_text(
                render_issue_page(archived_html, archived_date, dates, location="archive"),
                encoding="utf-8",
                newline="\n",
            )

        latest = dates[0]
        latest_html = (site_dir / "archive" / f"{latest}.html").read_text(encoding="utf-8")
        (site_dir / "index.html").write_text(
            render_issue_page(latest_html, latest, dates, location="root"),
            encoding="utf-8",
            newline="\n",
        )
    (site_dir / "archive" / "index.html").write_text(render_archive_index(dates), encoding="utf-8", newline="\n")
    return dates


def git_push(site_dir: Path, date_iso: str):
    date_label = date_iso or datetime.now().strftime("%Y-%m-%d")
    for args in (["git", "add", "-A"],
                 ["git", "commit", "-m", f"다이제스트 갱신: {date_label}"],
                 ["git", "push"]):
        r = subprocess.run(
            args,
            cwd=str(site_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
            detail = r.stderr.strip() or r.stdout.strip() or f"exit code {r.returncode}"
            raise RuntimeError(f"[git] {' '.join(args)} 실패: {detail}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="정적 사이트 배포 빌더")
    ap.add_argument("--site-dir", required=True)
    ap.add_argument("--date", help="구울 날짜 YYYY-MM-DD")
    ap.add_argument("--db")
    ap.add_argument("--rebuild-index", action="store_true", help="기존 아카이브로 index만 재생성")
    ap.add_argument("--push", action="store_true", help="빌드 후 git add/commit/push")
    args = ap.parse_args(argv)

    site_dir = Path(args.site_dir)
    date_iso = None if args.rebuild_index else args.date
    dates = build(site_dir, date_iso=date_iso, db_path=args.db)
    print(f"빌드 완료: {site_dir} (아카이브 {len(dates)}일, 최신 {dates[0] if dates else '없음'})")
    if args.push:
        git_push(site_dir, date_iso)


if __name__ == "__main__":
    main()
