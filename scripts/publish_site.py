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
import hashlib
import html as _html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).parent))
import newsdb  # noqa: E402

if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DATE_RE = re.compile(r"^(20\d{2}-\d{2}-\d{2})\.html$")
CONTRACT_PATH = Path(__file__).resolve().parents[1] / "references" / "publication-contract.json"
MANIFEST_NAME = "publication-manifest.json"
MANIFEST_GENERATOR = "edu-news-organizer/publish_site"
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


def validate_publication_contract(html: str, contract: dict, *, allow_legacy: bool = False) -> None:
    """현재 공개 계약을 검사한다. 명시 이관된 구형 파일은 과거 열 CSS를 보존한다."""
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
    # 구형 공개본의 열 구조는 당시 산출물을 보존한다. 이 예외는 명시 이관 검사 또는
    # 그 결과의 파일별 source+hash를 확인한 build 경로에서만 사용한다.
    if not allow_legacy:
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
    probe = _PublishedHTML()
    probe.feed(html)
    if probe.public_mode != "public" and not (allow_legacy and probe.public_mode is None):
        raise ValueError("공개 안전 위반: public 생성 표식이 없습니다. 새 사이트 폴더에서 원본 DB의 보관 날짜 전체를 다시 생성·검토한 후 교체하세요.")
    if probe.private_markup:
        raise ValueError("공개 안전 위반: 개인 관심업무·메모 요소가 있습니다.")
    if probe.unsafe_markup:
        raise ValueError("공개 안전 위반: 실행 코드·자동 이동·외부 리소스 요소가 있습니다.")


class _PublishedHTML(HTMLParser):
    """문구·CSS가 아닌 실제 링크와 개인용 요소만 읽는다."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = set()
        self.private_markup = False
        self.public_mode = None
        self.classes = set()
        self.tags = set()
        self.hrefs = []
        self.unsafe_markup = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.add(tag)
        self.classes.update(attrs.get("class", "").split())
        if (tag in {"script", "iframe", "object", "embed", "form", "base"}
                or any(name.lower().startswith("on") for name in attrs)
                or tag == "meta" and "http-equiv" in attrs
                or any(name in attrs for name in {"srcset", "ping", "poster", "background", "xlink:href"})):
            self.unsafe_markup = True
        for name in ("href", "src"):
            value = (attrs.get(name) or "").strip()
            try:
                scheme = urlsplit(value).scheme.lower()
            except ValueError:
                scheme = "invalid"
            image_data = tag == "img" and value.lower().startswith(("data:image/png;", "data:image/jpeg;", "data:image/gif;", "data:image/webp;"))
            empty_icon = tag == "link" and value == "data:,"
            if value and (scheme not in {"", "http", "https", "data"}
                          or (scheme == "data" and not (image_data or empty_icon))
                          or name == "src" and not image_data
                          or tag == "link" and not empty_icon):
                self.unsafe_markup = True
        if tag == "meta" and attrs.get("name") == "edu-news-mode":
            self.public_mode = attrs.get("content")
        if tag == "a":
            self.hrefs.append(attrs.get("href", ""))
            url = _normalize_url(attrs.get("href"))
            if url:
                self.urls.add(url)
        if set(attrs.get("class", "").split()) & {"section-interest", "int-list", "int-item", "int-memo"}:
            self.private_markup = True


def _normalize_url(url: str) -> str:
    raw = (url or "").strip()
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return ""
    # Host/scheme are case-insensitive; path and query are not.
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def validate_briefing_published_content_parity(briefing_articles: list, published_html: str,
                                               represented_by: dict = None) -> None:
    """실제 href를 정확히 대조한다. 묶음 대체는 호출자가 검증한 명시 관계만 허용한다."""
    missing = []
    probe = _PublishedHTML()
    probe.feed(published_html or "")
    replacements = {_normalize_url(k): _normalize_url(v) for k, v in (represented_by or {}).items()}
    for article in briefing_articles:
        url = _normalize_url(article.get("original_url") or article.get("url"))
        if url and (url in probe.urls or replacements.get(url) in probe.urls):
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
  :root {{ color-scheme:light; --blue:#0060B0; --ink:#12233A; --muted:#526579; --line:#dde5ec; --paper:#eef2f6;
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
  a:focus-visible {{ outline:3px solid #0060B0; outline-offset:3px; }}
  @media (max-width:480px) {{ li a {{ flex-direction:column; gap:4px; }} }}
  @media (prefers-reduced-motion:reduce) {{ li a {{ transition:none; }} li a:hover {{ transform:none; }} }}
  .d {{ font-weight:800; color:var(--blue); }}
  .w {{ color:var(--muted); font-size:15px; }}
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
  .issue-nav { max-width:1320px; margin:0 auto; padding:14px 22px;
    display:grid; grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);
    align-items:center; gap:12px; border-bottom:1px solid var(--line); }
  .issue-nav .nav-btn { display:inline-flex; align-items:center; width:max-content; max-width:100%;
    min-height:44px; padding:10px 14px; border:1px solid var(--line); border-radius:8px; background:var(--card);
    color:var(--blue); font-size:14px; font-weight:800; line-height:1.35;
    box-shadow:0 1px 2px rgba(18,35,58,.04); }
  .issue-nav a:focus-visible { outline:3px solid var(--blue); outline-offset:3px; }
  .issue-nav .nav-next { justify-self:end; }
  .issue-nav .nav-btn.disabled { color:var(--muted); background:var(--paper); pointer-events:none; }
  .issue-now { text-align:center; font-size:14px; color:var(--ink); line-height:1.4; }
  .issue-now strong { display:block; font-size:14.5px; font-weight:800; font-variant-numeric:tabular-nums; }
  .issue-now a { display:inline-block; margin-top:3px; color:var(--muted); font-size:14px; font-weight:700; min-height:44px; padding:10px 8px; }
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
        next_html = '<span class="nav-btn nav-next disabled">다음 호 준비 중</span>'

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
        if m and f.is_file():
            out.append(m.group(1))
    return out


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _generated_path(site_dir: Path, relative: str) -> Path:
    """생성 파일만 허용하며 심볼릭 링크를 통한 외부 쓰기를 막는다."""
    path = Path(relative)
    if relative not in {"index.html", "archive/index.html", MANIFEST_NAME} and not (
            len(path.parts) == 2 and path.parts[0] == "archive" and DATE_RE.fullmatch(path.name)):
        raise ValueError(f"게시 파일 목록에 허용되지 않은 경로: {relative}")
    target = site_dir / path
    if target.is_symlink() or not target.resolve().is_relative_to(site_dir.resolve()):
        raise ValueError(f"게시 파일 경로가 사이트 밖을 가리킵니다: {relative}")
    return target


def _load_manifest(site_dir: Path) -> dict | None:
    path = _generated_path(site_dir, MANIFEST_NAME)
    if not path.exists():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != 1 or manifest.get("mode") != "public"
            or manifest.get("generator") != MANIFEST_GENERATOR
            or not isinstance(manifest.get("files"), dict)):
        raise ValueError("공개 생성 목록 형식·출처가 올바르지 않습니다.")
    for relative in manifest["files"]:
        _generated_path(site_dir, relative)
    return manifest


def _validate_legacy_public(html: str, contract: dict) -> _PublishedHTML:
    """명시 이관에만 사용하는 구 생성기 구조·공개 경계 검사."""
    validate_publication_contract(html, contract, allow_legacy=True)
    probe = _PublishedHTML()
    probe.feed(html)
    required_classes = {"masthead", "mast-inner", "mast-title", "unofficial", "wrap", "foot"}
    allowed_tags = {"html", "head", "meta", "link", "title", "style", "body", "div", "img",
                    "p", "h1", "h2", "h3", "main", "section", "span", "article", "header",
                    "b", "strong", "em", "small", "br", "footer", "ul", "li", "a", "nav",
                    "details", "summary"}
    if (not required_classes.issubset(probe.classes)
            or not probe.tags.issubset(allowed_tags)
            or not {"h1", "main", "footer"}.issubset(probe.tags)
            or "인천광역시교육청의 공식 발행물이 아닙니다" not in html
            or "동일보도는 대표기사 1건으로 묶고 관련 매체를 병기했습니다" not in html
            or re.search(r"(?i)@import|url\(\s*['\"]?\s*(?:https?:|//)", html)
            or probe.unsafe_markup):
        raise ValueError("구형 공개본의 생성기 구조·비공식 고지·안전 요소를 확인할 수 없습니다.")
    return probe


def adopt_legacy_public(site_dir: Path, *, dry_run: bool = True, expected_files: dict = None) -> dict:
    """HTML을 수정하지 않고 명시 검토된 구형 공개본의 hash 목록만 만든다.

    기본은 dry-run이다. 검토한 후보의 files를 expected_files로 넘기면 검토 후 변경도 차단한다.
    """
    site_dir = Path(site_dir).resolve()
    if (site_dir / MANIFEST_NAME).exists():
        raise ValueError("기존 공개 생성 목록이 있습니다. 이관으로 기존 검증 기록을 덮어쓸 수 없습니다.")
    contract = load_publication_contract()
    dates = sorted(existing_dates(site_dir), reverse=True)
    if not dates:
        raise ValueError("이관할 날짜별 보관본이 없습니다.")
    files, sources, probes = {}, {}, {}
    for date in dates:
        relative = f"archive/{date}.html"
        content = _generated_path(site_dir, relative).read_bytes()
        probes[date] = _validate_legacy_public(content.decode("utf-8"), contract)
        files[relative] = _digest(content)
        sources[relative] = "legacy-public-reviewed"
    root = _generated_path(site_dir, "index.html").read_bytes()
    root_probe = _validate_legacy_public(root.decode("utf-8"), contract)
    if root_probe.urls != probes[dates[0]].urls:
        raise ValueError("루트와 최신 보관본의 기사 링크가 다릅니다. 이관 전에 내용을 확인하세요.")
    files["index.html"] = _digest(root)
    sources["index.html"] = "legacy-public-reviewed"
    archive_index = _generated_path(site_dir, "archive/index.html").read_bytes()
    index_html = archive_index.decode("utf-8")
    probe = _PublishedHTML()
    probe.feed(index_html)
    expected_hrefs = {"../index.html", *(f"{date}.html" for date in dates)}
    if (probe.unsafe_markup or probe.private_markup or set(probe.hrefs) != expected_hrefs
            or "<h1>지난 호 보기</h1>" not in index_html
            or contract["public_safety"]["required_marker"] not in index_html):
        raise ValueError("지난 호 목록의 구조·공개 고지·날짜 링크를 확인할 수 없습니다.")
    files["archive/index.html"] = _digest(archive_index)
    sources["archive/index.html"] = "legacy-public-reviewed"
    if expected_files is not None and files != expected_files:
        raise ValueError("이관 후보 검토 이후 파일이 변경됐습니다. 다시 검토하세요.")
    manifest = {"schema_version": 1, "generator": MANIFEST_GENERATOR, "mode": "public",
                "source": "legacy-public-reviewed", "contract_id": contract["contract_id"],
                "dates": dates, "files": files, "sources": sources}
    if not dry_run:
        _write_site_transaction(site_dir, {
            MANIFEST_NAME: (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")})
    return manifest


def _write_site_transaction(site_dir: Path, outputs: dict) -> None:
    """모든 파일을 먼저 준비하고, 교체 중 오류가 나면 기존 바이트로 복구한다."""
    site_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".edu-news-stage-", dir=site_dir.parent))
    installed = []
    recoverable = True
    try:
        for relative, content in outputs.items():
            target = _generated_path(site_dir, relative)
            prepared = stage / "new" / relative
            prepared.parent.mkdir(parents=True, exist_ok=True)
            prepared.write_bytes(content)
            if target.exists():
                backup = stage / "old" / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
        for relative in outputs:
            target = _generated_path(site_dir, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage / "new" / relative, target)
            installed.append(relative)
    except Exception:
        failures = []
        for relative in reversed(installed):
            backup = stage / "old" / relative
            try:
                target = _generated_path(site_dir, relative)
                if backup.exists():
                    os.replace(backup, target)
                else:
                    target.unlink()
            except Exception as error:
                failures.append(str(error))
        if failures:
            recoverable = False
            raise RuntimeError(f"게시 교체와 복구 실패. 복구용 원본을 보존했습니다: {stage}")
        raise
    finally:
        if recoverable:
            shutil.rmtree(stage)


def build(site_dir: Path, date_iso: str = None, db_path: str = None):
    """전체 공개 파일을 검증·준비한 뒤 교체한다. 실사용 DB는 날짜 생성 때만 연다."""
    site_dir = Path(site_dir).resolve()
    contract = load_publication_contract()
    manifest = _load_manifest(site_dir)
    archives = {}
    archive_sources = {}
    if date_iso:
        datetime.strptime(date_iso, "%Y-%m-%d")
        if not DATE_RE.fullmatch(f"{date_iso}.html"):
            raise ValueError("게시 날짜는 YYYY-MM-DD 형식이어야 합니다.")
    for archived_date in existing_dates(site_dir):
        if archived_date == date_iso:
            continue
        relative = f"archive/{archived_date}.html"
        content = _generated_path(site_dir, relative).read_bytes()
        if manifest is not None and manifest["files"].get(relative) != _digest(content):
            raise ValueError(f"공개 보관본 무결성 확인 실패: {relative}. 원본에서 다시 생성하세요.")
        archived_html = content.decode("utf-8")
        legacy = manifest is not None and manifest.get("sources", {}).get(relative) == "legacy-public-reviewed"
        if legacy:
            _validate_legacy_public(archived_html, contract)
        else:
            validate_publication_contract(archived_html, contract)
        archives[archived_date] = archived_html
        archive_sources[archived_date] = "legacy-public-reviewed" if legacy else "generated-public"
    if date_iso:
        conn = newsdb.open_db(db_path)
        try:
            validate_ingestion_contract(conn, date_iso, contract)
            validate_grouping_state(conn, date_iso)
            html = newsdb.render_digest_html(newsdb.build_digest_data(conn, date_iso), public=True)
            validate_publication_contract(html, contract)
            briefing_articles = [{"title": row["title"], "original_url": row["original_url"]}
                                 for row in newsdb.search_articles(
                                     conn, date=date_iso, selected=True, include_excluded=True, limit=-1)
                                 if row["source_kind"] in {"briefing-md", "paste"}]
            validate_briefing_published_content_parity(briefing_articles, html)
        finally:
            conn.close()
        archives[date_iso] = html
        archive_sources[date_iso] = "generated-public"
    dates = sorted(archives, reverse=True)
    if not dates and (site_dir / "index.html").exists():
        raise ValueError("보관본 없는 기존 index.html은 공개 여부를 확인할 수 없습니다. 원본 날짜를 지정해 다시 생성하세요.")
    outputs = {}
    sources = {}
    if dates:
        for archived_date in dates:
            html = render_issue_page(archives[archived_date], archived_date, dates, location="archive")
            validate_publication_contract(html, contract, allow_legacy=archive_sources[archived_date] == "legacy-public-reviewed")
            outputs[f"archive/{archived_date}.html"] = html.encode("utf-8")
            sources[f"archive/{archived_date}.html"] = archive_sources[archived_date]
        latest = dates[0]
        html = render_issue_page(archives[latest], latest, dates, location="root")
        validate_publication_contract(html, contract, allow_legacy=archive_sources[latest] == "legacy-public-reviewed")
        outputs["index.html"] = html.encode("utf-8")
        sources["index.html"] = archive_sources[latest]
    outputs["archive/index.html"] = render_archive_index(dates).encode("utf-8")
    sources["archive/index.html"] = "generated-public"
    new_manifest = {"schema_version": 1, "generator": MANIFEST_GENERATOR, "mode": "public",
                    "contract_id": contract["contract_id"], "dates": dates,
                    "files": {name: _digest(content) for name, content in outputs.items()}, "sources": sources}
    outputs[MANIFEST_NAME] = (json.dumps(new_manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _write_site_transaction(site_dir, outputs)
    return dates


def _git(site_dir: Path, *args) -> str:
    command = ["git", *args]
    r = subprocess.run(command, cwd=str(site_dir), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        detail = r.stderr.strip() or r.stdout.strip() or f"exit code {r.returncode}"
        raise RuntimeError(f"[git] {' '.join(command)} 실패: {detail}")
    return r.stdout


def git_push(site_dir: Path, date_iso: str):
    """검증된 생성 목록만 커밋한다. 기존 staged 변경과 다른 대상 브랜치는 거부한다."""
    site_dir = Path(site_dir).resolve()
    repo = Path(_git(site_dir, "rev-parse", "--show-toplevel").strip()).resolve()
    if not site_dir.is_relative_to(repo):
        raise RuntimeError("게시 디렉터리가 확인된 Git 저장소 안에 없습니다.")
    branch = _git(site_dir, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
    upstream = _git(site_dir, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}").strip()
    remote, separator, remote_branch = upstream.partition("/")
    if not branch or not separator or branch != remote_branch:
        raise RuntimeError("현재 브랜치와 게시 upstream 브랜치가 일치하지 않습니다.")
    if _git(site_dir, "rev-list", "@{upstream}..HEAD").strip():
        raise RuntimeError("미게시 로컬 커밋이 있습니다. 포함 범위를 별도로 검토한 후 게시하세요.")
    if _git(site_dir, "diff", "--cached", "--name-only", "-z").strip("\0\n"):
        raise RuntimeError("기존 staged 변경이 있습니다. 먼저 별도로 정리한 후 게시하세요.")
    manifest = _load_manifest(site_dir)
    if manifest is None:
        raise ValueError("검증된 공개 생성 목록이 없습니다. 먼저 build를 실행하세요.")
    generated = []
    for relative, expected in manifest["files"].items():
        path = _generated_path(site_dir, relative)
        if not path.is_file() or _digest(path.read_bytes()) != expected:
            raise ValueError(f"공개 생성 파일 무결성 확인 실패: {relative}")
        generated.append(path.relative_to(repo).as_posix())
    generated.append((site_dir / MANIFEST_NAME).relative_to(repo).as_posix())
    generated.sort()
    _git(repo, "add", "--", *generated)
    staged = set(filter(None, _git(repo, "diff", "--cached", "--name-only", "-z").split("\0")))
    if not staged.issubset(set(generated)):
        raise RuntimeError("게시 파일 이외 staged 변경을 발견해 커밋을 중단했습니다.")
    if not staged:
        return
    date_label = date_iso or datetime.now().strftime("%Y-%m-%d")
    _git(repo, "commit", "-m", f"다이제스트 갱신: {date_label}")
    _git(repo, "push", "--", remote, f"HEAD:refs/heads/{remote_branch}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="정적 사이트 배포 빌더")
    ap.add_argument("--site-dir", required=True)
    ap.add_argument("--date", help="구울 날짜 YYYY-MM-DD")
    ap.add_argument("--db")
    ap.add_argument("--rebuild-index", action="store_true", help="기존 아카이브로 index만 재생성")
    ap.add_argument("--adopt-legacy-public", action="store_true", help="구형 공개본 검토 후 HTML 변경 없이 검증 목록만 생성")
    ap.add_argument("--dry-run", action="store_true", help="--adopt-legacy-public의 검증 후보만 출력, 파일 쓰기 없음")
    ap.add_argument("--push", action="store_true", help="빌드 후 git add/commit/push")
    args = ap.parse_args(argv)

    site_dir = Path(args.site_dir)
    if args.adopt_legacy_public:
        if args.date or args.db or args.rebuild_index or args.push:
            ap.error("구형 공개본 이관은 --date/--db/--rebuild-index/--push와 함께 실행할 수 없습니다.")
        manifest = adopt_legacy_public(site_dir, dry_run=args.dry_run)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return
    if args.dry_run:
        ap.error("--dry-run은 --adopt-legacy-public과 함께 사용하세요.")
    date_iso = None if args.rebuild_index else args.date
    dates = build(site_dir, date_iso=date_iso, db_path=args.db)
    print(f"빌드 완료: {site_dir} (아카이브 {len(dates)}일, 최신 {dates[0] if dates else '없음'})")
    if args.push:
        git_push(site_dir, date_iso)


if __name__ == "__main__":
    main()
