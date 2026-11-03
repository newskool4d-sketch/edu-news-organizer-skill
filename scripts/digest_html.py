# -*- coding: utf-8 -*-
"""edu-news-organizer 다이제스트 HTML 렌더러.

디자인: 인천광역시교육청 CI 팔레트 기반 프리미엄 에디토리얼 브리핑.
- 브랜드색(심벌마크 실측): 딥블루 #0060B0, 그린 #00A160, 라임 #80C030, 오렌지태양 #F08020, 시안 #17B0D4
- 자기완결(self-contained): 외부 리소스 없음, 로고만 data URI 임베드
- 입력(제목·매체)은 외부 뉴스라 신뢰 불가 → 전부 HTML 이스케이프, http(s) URL만 링크화
"""
import html as _html
import re
from datetime import datetime, timedelta, timezone

# 성격 → 강조색 (CI 팔레트 파생)
TYPE_COLOR = {
    "교육감": "#004A8A",
    "정책·현안": "#0060B0",
    "비판·점검": "#E2620E",
    "인터뷰·기획": "#0E8FB0",
    "사업·성과": "#00A160",
    "행사·모집": "#6FA31E",
    "타 시도 동향": "#5B6B7C",
    "기타": "#8A98A8",
}

# 작은 배지 글자용 색상은 흰색 대비 4.5:1 이상을 확보한다.
# 원래 CI 색상은 카드 테두리·분야 점 등 장식에 유지한다.
TYPE_TEXT_COLOR = {
    "교육감": "#004A8A", "정책·현안": "#0060B0", "비판·점검": "#A84308",
    "인터뷰·기획": "#086A83", "사업·성과": "#007343", "행사·모집": "#486D12",
    "타 시도 동향": "#4D5C6C", "기타": "#526478",
}


def esc(s) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def safe_url(url: str) -> str:
    """http(s)만 허용. 그 외(javascript:, data: 등)는 빈 문자열."""
    u = (url or "").strip()
    if u.lower().startswith("http://") or u.lower().startswith("https://"):
        return u
    return ""


def _fmt_date(batch_date: str) -> str:
    try:
        d = datetime.strptime(batch_date, "%Y-%m-%d")
        wd = ["월", "화", "수", "목", "금", "토", "일"][d.weekday()]
        return f"{d.year}. {d.month}. {d.day}. ({wd})"
    except Exception:
        return batch_date


# ---- 표시용 제목 정리 (2026-10-07 사용자 승인) ----
# 수집기 제목에는 매체 사이트가 붙인 꼬리말이 남는다: " > 뉴스 | 더코리아", " - 머니투데이",
# "…촉구:경찰연합신문", "…:동아경제신문 & daenews.co.kr". DB 원문은 보존하고 화면에서만 뗀다.
# 제목 중간의 하이픈·콜론("인천시교육청-남동구, …", "'이산가족의 날' - 실향민 …")은 건드리지 않도록
# 꼬리 조각이 매체명과 일치·포함되거나, '뉴스'이거나, 매체명 어미·도메인 꼴일 때만 뗀다.
_TAIL_RE = re.compile(r"(?:\s+[-–—|｜>]\s*|\s*[:：|｜>]\s*)([^-–—|｜>:：]{1,40})$")
_OUTLET_SUFFIX_RE = re.compile(
    r"(일보|신문|뉴스|news|투데이|타임스|타임즈|타임|미디어|방송|TV|닷컴|포커스|저널|데일리|매일|신보|위크|매거진|"
    r"통신|헤럴드|포스트|경제|코리아|프레스|press|리뷰|저널리즘|뉴시스|연합)$",
    re.IGNORECASE,
)
_DOMAIN_RE = re.compile(r"[A-Za-z0-9-]+\.(?:co\.kr|com|kr|net|org|io)\b", re.IGNORECASE)


def _is_outlet_tail(segment: str, publisher: str) -> bool:
    seg = (segment or "").strip(" .·")
    if not seg:
        return False
    if seg.lower() in {"뉴스", "news"}:
        return True
    pub = (publisher or "").strip()
    if pub and (seg == pub or seg in pub or pub in seg):
        return True
    if _DOMAIN_RE.search(seg):
        return True
    return bool(_OUTLET_SUFFIX_RE.search(seg))


def _pub(row) -> str:
    try:
        return row["publisher"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def display_title(title: str, publisher: str = "") -> str:
    """화면 표시용 제목: 끝에 붙은 매체 꼬리말을 최대 3회 뗀다. 원문(DB)은 바꾸지 않는다."""
    t = (title or "").strip()
    for _ in range(3):
        m = _TAIL_RE.search(t)
        if not m or not _is_outlet_tail(m.group(1), publisher):
            break
        t = t[:m.start()].rstrip()
    return t or (title or "")


def _chip(text: str, cls: str = "") -> str:
    return f'<span class="chip {cls}">{esc(text)}</span>'


def _link(url: str, label: str) -> str:
    u = safe_url(url)
    if not u:
        return esc(label)
    return f'<a href="{esc(u)}" target="_blank" rel="noopener noreferrer">{esc(label)}</a>'


def _stat(value, label: str) -> str:
    return (f'<div class="stat"><span class="stat-num">{esc(value)}</span>'
            f'<span class="stat-label">{esc(label)}</span></div>')


def _related_articles(articles: list) -> str:
    if not articles:
        return ""
    items = "".join(
        f'<li>{_link(a["original_url"], display_title(a["title"], _pub(a)))}'
        f'<span class="related-pub">{esc(_pub(a))}</span></li>' for a in articles
    )
    return (f'<details class="related"><summary>관련 기사 보기 · {len(articles)}건</summary>'
            f'<ul class="related-list">{items}</ul></details>')


def _hero_card(g: dict) -> str:
    color = TYPE_COLOR.get(g["article_type"], TYPE_COLOR["기타"])
    text_color = TYPE_TEXT_COLOR.get(g["article_type"], TYPE_TEXT_COLOR["기타"])
    rep = g["representative"]
    fields = "".join(_chip(f) for f in g["fields"])
    if g.get("summary_needs_review"):
        summary = '<p class="summary-review">기사 변경 · 요약 재검토 필요</p>'
    else:
        summary = f'<p class="hero-summary">{esc(g["summary"])}</p>' if g.get("summary") else ""
    others = _related_articles(g["others"])
    title_link = _link(rep["original_url"], display_title(g["title"], _pub(rep)))
    return f'''
      <article class="hero-card" style="--accent:{color};--accent-text:{text_color}">
        <header class="hero-head">
          <span class="type-badge" style="background:{text_color}">{esc(g["article_type"])}</span>
          <div class="field-chips">{fields}</div>
        </header>
        <h3 class="hero-title">{title_link}</h3>
        {summary}
        <div class="hero-count">
          <span class="count-pill">관련 보도 {esc(g["member_count"])}건</span>
        </div>
        <div class="hero-rep">
          <span class="rep-label">{'대표 매체' if g['others'] else '매체'}</span>
          <span class="rep-pub">{esc(rep["publisher"])}</span>
        </div>
        {others}
      </article>'''


def _interest_item(r: dict) -> str:
    tags = "".join(f'<span class="int-tag">{esc(k)}</span>' for k in r["interest_hits"])
    memo = f'<p class="int-memo">✎ {esc(r["memo"])}</p>' if r.get("memo") else ""
    fav = '<span class="fav-star" title="보관">★</span>' if r.get("favorite") else ""
    return f'''
      <li class="int-item">
        <div class="int-tags">{tags}{fav}</div>
        <div class="int-body">
          <span class="int-title">{_link(r["original_url"], display_title(r["title"], _pub(r)))}</span>
          <span class="int-pub">{esc(r["publisher"])}</span>
        </div>
        {memo}
      </li>'''


def _all_row(r: dict) -> str:
    return (f'<li class="row"><span class="row-title">{_link(r["original_url"], display_title(r["title"], _pub(r)))}</span>'
            f'<span class="row-pub">{esc(r["publisher"])}</span></li>')


def render(data: dict, logo_uri: str = "", public: bool = False,
           slogan_uri: str = "", title_uri: str = "") -> str:
    """public=True면 개인 데이터(내 관심업무·메모)를 빼고 비공식 표기를 넣은 공개 안전본을 만든다.

    slogan_uri/title_uri: 공식 서체(소통·힘찬)로 사전 렌더한 PNG data URI — 웹폰트 없이 브랜드 서체 반영.
    """
    meta = data["meta"]
    date_disp = _fmt_date(data["batch_date"])
    gen_at = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")

    logo_html = (f'<img class="logo" src="{esc(logo_uri)}" alt="인천광역시교육청" />'
                 if logo_uri else '<div class="logo logo-fallback"></div>')

    stats = (_stat(meta["total"], "선별 기사")
             + _stat(meta["issue_count"], "핵심 이슈")
             + _stat(meta["publisher_count"], "매체"))
    if not public:
        stats = (_stat(meta["total"], "선별 기사")
                 + _stat(meta["issue_count"], "핵심 이슈")
                 + _stat(meta["interest_count"], "내 관심")
                 + _stat(meta["publisher_count"], "매체"))

    unofficial_tag = '<span class="unofficial">비공식 · 개인 정리용</span>' if public else ''

    # 공식 서체 자산: 제목(힘찬)·슬로건(소통) — 없으면 텍스트 폴백
    title_html = (f'<img class="mast-title-img" src="{esc(title_uri)}" alt="인천교육 언론보도 브리핑" />'
                  if title_uri else '<span>인천교육 언론보도 브리핑</span>')
    slogan_html = (f'<img class="slogan-img" src="{esc(slogan_uri)}" alt="학생성공시대를 여는 인천교육" />'
                   if slogan_uri else '')

    hero_html = ""
    if data["hero_issues"]:
        cards = "".join(_hero_card(g) for g in data["hero_issues"])
        hero_html = f'''
    <section class="section">
      <div class="sec-head"><span class="sec-bar"></span><h2>오늘의 핵심 이슈</h2>
        <span class="sec-count">{len(data["hero_issues"])}건</span></div>
      <div class="hero-grid">{cards}</div>
    </section>'''

    interest_html = ""
    if data["interest_articles"] and not public:
        items = "".join(_interest_item(r) for r in data["interest_articles"])
        interest_html = f'''
    <section class="section section-interest">
      <div class="sec-head"><span class="sec-bar orange"></span><h2>내 관심업무</h2>
        <span class="sec-count">{len(data["interest_articles"])}건</span></div>
      <ul class="int-list">{items}</ul>
    </section>'''

    other_html = ""
    other_issues = data.get("other_issues", [])
    other_articles = data.get("other_articles", [])
    if other_issues or other_articles:
        rows = []
        for g in other_issues:
            rep = g["representative"]
            rows.append(
                f'<li class="row"><div class="row-title">{_link(rep["original_url"], display_title(g["title"], _pub(rep)))}'
                f'<span class="row-count">관련 {esc(g["member_count"])}건</span></div>'
                f'<span class="row-pub">{esc(rep["publisher"])}</span>'
                f'{_related_articles(g.get("others", []))}</li>')
        for r in other_articles:
            rows.append(_all_row(r))
        other_count = len(other_issues) + len(other_articles)
        other_html = f'''
    <section class="section section-other">
      <div class="sec-head"><span class="sec-bar slate"></span><h2>타 시도·일반 교육 동향</h2>
        <span class="sec-count slate-count">{other_count}건</span>
        <span class="sec-sub">인천 외 참고 뉴스</span></div>
      <div class="other-panel"><ul class="rows">{"".join(rows)}</ul></div>
    </section>'''

    all_html = ""
    if data["all_by_type"]:
        blocks = []
        for t, items in data["all_by_type"]:
            color = TYPE_COLOR.get(t, TYPE_COLOR["기타"])
            rows = "".join(_all_row(r) for r in items)
            blocks.append(f'''
        <div class="type-block">
          <h3 class="type-head"><span class="type-dot" style="background:{color}"></span>{esc(t)}
            <span class="type-num">{len(items)}</span></h3>
          <ul class="rows">{rows}</ul>
        </div>''')
        all_html = f'''
    <section class="section">
      <div class="sec-head"><span class="sec-bar green"></span><h2>전체 기사 <span class="sec-sub">묶음 외</span></h2></div>
      <div class="all-grid">{"".join(blocks)}</div>
    </section>'''

    return f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="edu-news-mode" content="{'public' if public else 'private'}">
<link rel="icon" href="data:,">
<title>인천교육 언론보도 브리핑 · {esc(date_disp)}</title>
<style>
  :root {{
    color-scheme:light;
    --blue:#0060B0; --blue-deep:#004a8a; --blue-mid:#4782C4; --cyan:#17B0D4;
    --green:#00A160; --lime:#80C030; --orange:#F08020;
    --ink:#12233A; --ink-soft:#33475e; --muted:#526478; --faint:#526478;
    --orange-text:#93430C;
    --paper:#eef2f6; --card:#ffffff; --line:#dde5ec; --line-soft:#eaeff4;
    --flow:linear-gradient(115deg,#00A160 0%,#0060B0 52%,#17B0D4 100%);
  }}
  * {{ box-sizing:border-box; }}
  html {{ -webkit-text-size-adjust:100%; }}
  body {{
    margin:0; background:var(--paper); color:var(--ink);
    font-family:"Pretendard","SUIT","Apple SD Gothic Neo","Malgun Gothic",-apple-system,sans-serif;
    line-height:1.6; letter-spacing:-0.01em;
    -webkit-font-smoothing:antialiased;
  }}
  a {{ color:var(--blue); text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  a:focus-visible, summary:focus-visible {{ outline:3px solid var(--blue-deep); outline-offset:3px;
    border-radius:3px; }}
  .wrap {{ max-width:1320px; margin:0 auto; padding:0 24px 72px; }}

  /* ---- Masthead ---- */
  .masthead {{ position:relative; background:var(--card); border-bottom:1px solid var(--line);
    overflow:hidden; }}
  .flow-band {{ height:7px; background:var(--flow); }}
  .mast-inner {{ max-width:1320px; margin:0 auto; padding:30px 24px 26px;
    display:flex; align-items:center; gap:22px; flex-wrap:wrap; }}
  .logo {{ width:60px; height:60px; object-fit:contain; flex:0 0 auto;
    filter:drop-shadow(0 3px 8px rgba(0,74,138,.16)); }}
  .logo-fallback {{ border-radius:50%; background:var(--flow); }}
  .mast-text {{ flex:1 1 auto; min-width:220px; }}
  .kicker {{ display:flex; flex-wrap:wrap; align-items:center; gap:6px 10px;
    margin:0 0 8px; font-size:12px; font-weight:800; letter-spacing:.08em;
    color:var(--blue); text-transform:uppercase; }}
  .mast-title {{ margin:0; font-size:30px; font-weight:800; letter-spacing:-.02em; line-height:1.12; }}
  .mast-date {{ margin:7px 0 0; font-size:15px; font-weight:600; color:var(--muted); }}
  .stat-strip {{ display:flex; gap:10px; flex:0 0 auto; }}
  .stat {{ display:flex; flex-direction:column; align-items:center; justify-content:center;
    min-width:74px; padding:12px 10px; background:linear-gradient(180deg,#f7fafc,#eef3f8);
    border:1px solid var(--line); border-radius:14px; }}
  .stat-num {{ font-size:24px; font-weight:800; color:var(--blue); line-height:1; letter-spacing:-.02em; }}
  .stat-label {{ margin-top:5px; font-size:12px; font-weight:600; color:var(--muted); }}

  /* ---- Section ---- */
  .section {{ margin-top:40px; }}
  .sec-head {{ display:flex; align-items:center; gap:11px; margin-bottom:18px; }}
  .sec-bar {{ width:5px; height:22px; border-radius:3px; background:var(--blue); }}
  .sec-bar.orange {{ background:var(--orange); }}
  .sec-bar.green {{ background:var(--green); }}
  .sec-head h2 {{ margin:0; font-size:21px; font-weight:800; letter-spacing:-.02em; }}
  .sec-sub {{ font-size:14px; font-weight:600; color:var(--faint); }}
  .sec-count {{ margin-left:2px; font-size:13px; font-weight:700; color:#fff; background:var(--ink);
    padding:2px 10px; border-radius:20px; }}

  /* ---- Hero cards ---- */
  .hero-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px;
    align-items:start; }}
  .hero-card {{ position:relative; min-width:0; background:var(--card); border:1px solid var(--line);
    border-radius:16px; padding:18px 16px 16px;
    box-shadow:0 1px 2px rgba(18,35,58,.04); transition:transform .16s ease, box-shadow .16s ease; }}
  .hero-card::before {{ content:""; position:absolute; inset:0 auto 0 0; width:4px; background:var(--accent); }}
  .hero-card:hover {{ box-shadow:0 8px 22px rgba(18,35,58,.08); }}
  .hero-head {{ display:flex; align-items:center; gap:8px; margin-bottom:12px; flex-wrap:wrap; }}
  .type-badge {{ font-size:12px; font-weight:800; color:#fff; padding:4px 11px; border-radius:20px;
    letter-spacing:-.01em; }}
  .field-chips {{ display:flex; gap:5px; flex-wrap:wrap; }}
  .chip {{ font-size:12px; font-weight:600; color:var(--ink-soft); background:var(--line-soft);
    padding:3px 9px; border-radius:20px; }}
  .hero-title {{ margin:0 0 12px; font-size:18px; font-weight:800; line-height:1.5; letter-spacing:-.02em; }}
  .hero-title a {{ color:var(--ink); }}
  .hero-title a:hover {{ color:var(--blue); }}
  .hero-title, .row-title, .related-list a, .rep-pub {{ overflow-wrap:anywhere; min-width:0; }}
  .hero-summary {{ margin:0 0 14px; font-size:15px; color:var(--ink-soft); line-height:1.65;
    padding-left:12px; border-left:2px solid var(--line); }}
  .hero-count {{ display:flex; align-items:center; gap:10px; margin-bottom:14px; }}
  .count-pill {{ font-size:13px; font-weight:700; color:var(--accent-text); }}
  .hero-rep {{ display:flex; gap:9px; padding-top:13px; border-top:1px dashed var(--line); }}
  .rep-label {{ flex:0 0 auto; font-size:12.5px; font-weight:600; color:var(--muted); }}
  .rep-pub {{ font-size:13px; color:var(--muted); font-weight:600; }}
  .related {{ margin-top:10px; width:100%; min-width:0; }}
  .related summary {{ min-height:44px; padding:10px 4px; cursor:pointer; color:var(--blue);
    font-size:14px; font-weight:700; }}
  .related-list {{ margin:0; padding:0; list-style:none; }}
  .related-list li {{ padding:10px 0; border-top:1px solid var(--line-soft); }}
  .related-list a {{ font-size:15px; line-height:1.6; }}
  .related-pub {{ display:block; margin-top:3px; color:var(--muted); font-size:12.5px; }}
  .summary-review {{ padding:8px 10px; background:#fff4e8; color:var(--orange-text); font-size:13px; }}

  /* ---- Interest ---- */
  .section-interest .int-list {{ list-style:none; margin:0; padding:0;
    background:linear-gradient(180deg,#fff9f2,#fff); border:1px solid #f4dcc4;
    border-radius:16px; overflow:hidden; }}
  .int-item {{ padding:15px 20px; border-bottom:1px solid #f7e8d8; }}
  .int-item:last-child {{ border-bottom:0; }}
  .int-tags {{ display:flex; align-items:center; gap:6px; margin-bottom:6px; flex-wrap:wrap; }}
  .int-tag {{ font-size:12px; font-weight:800; color:#fff; background:var(--orange-text);
    padding:2px 9px; border-radius:20px; }}
  .fav-star {{ color:var(--orange-text); font-size:16px; }}
  .int-body {{ display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }}
  .int-title a {{ font-size:15.5px; font-weight:700; color:var(--ink); }}
  .int-title a:hover {{ color:var(--blue); }}
  .int-pub {{ font-size:12.5px; color:var(--muted); font-weight:600; }}
  .int-memo {{ margin:7px 0 0; font-size:14px; color:var(--orange-text); background:#fff4e8;
    padding:7px 11px; border-radius:9px; }}

  /* ---- All articles ---- */
  .all-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px 18px; }}
  .type-block {{ min-width:0; background:var(--card); border:1px solid var(--line); border-radius:14px; padding:15px 14px; }}
  .type-head {{ display:flex; align-items:center; gap:8px; margin:0 0 10px; font-size:15px; font-weight:800; }}
  .type-dot {{ width:9px; height:9px; border-radius:50%; }}
  .type-num {{ margin-left:auto; font-size:12px; font-weight:800; color:var(--muted);
    background:var(--line-soft); padding:1px 9px; border-radius:20px; }}
  .rows {{ list-style:none; margin:0; padding:0; }}
  .row {{ display:flex; align-items:flex-start; flex-direction:column; gap:3px; padding:12px 0; border-top:1px solid var(--line-soft); }}
  .row:first-child {{ border-top:0; }}
  .row-title {{ flex:1; }}
  .row-title a {{ font-size:15px; font-weight:600; color:var(--ink-soft); line-height:1.6; }}
  .row-title a:hover {{ color:var(--blue); }}
  .row-pub {{ font-size:12.5px; color:var(--muted); font-weight:500; overflow-wrap:anywhere; }}

  /* ---- Footer ---- */
  .foot {{ margin-top:44px; padding-top:20px; border-top:2px solid var(--line);
    font-size:13px; color:var(--muted); }}
  .foot b {{ color:var(--ink-soft); }}
  .foot .flow-dot {{ display:inline-block; width:8px; height:8px; border-radius:50%;
    background:var(--flow); margin-right:6px; vertical-align:middle; }}

  .empty {{ padding:40px; text-align:center; color:var(--faint); font-weight:600; }}
  .unofficial {{ display:inline-block; padding:3px 10px; border-radius:20px;
    background:#fff1e6; color:var(--orange-text); border:1px solid #f3d3b6; font-size:12px;
    font-weight:800; letter-spacing:0; vertical-align:middle; }}

  /* ---- 공식 서체 자산 (힘찬 제목·소통 슬로건) ---- */
  .mast-title-img {{ display:block; height:auto; max-height:38px; width:auto; max-width:100%; }}
  .slogan-img {{ display:block; height:auto; max-height:24px; width:auto; max-width:100%; margin-top:10px; }}

  /* ---- 타 시도·일반 교육 동향 (인천 뉴스와 분리, 차분한 슬레이트 톤) ---- */
  .sec-bar.slate {{ background:#5B6B7C; }}
  .slate-count {{ background:#5B6B7C; }}
  .section-other .other-panel {{ background:#f6f8fa; border:1px solid var(--line);
    border-radius:14px; padding:8px 18px; }}
  .section-other .row-title a {{ color:var(--muted); }}
  .section-other .row-title a:hover {{ color:var(--blue); }}
  .row-count {{ margin-left:8px; font-size:12px; font-weight:700; color:#4D5C6C;
    background:#e8edf2; padding:1px 8px; border-radius:12px; white-space:nowrap; }}
  .disclaimer {{ margin-top:6px; color:var(--faint); }}
  .disclaimer b {{ color:var(--orange-text); }}

  @media (prefers-reduced-motion:reduce) {{
    *, *::before, *::after {{ transition:none !important; scroll-behavior:auto !important; }}
  }}

  /* ---- Responsive ---- */
  @media (max-width:1100px) {{
    .hero-grid, .all-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
  }}
  @media (max-width:720px) {{
    .wrap {{ padding:0 14px 52px; }}
    .mast-inner {{ display:grid; grid-template-columns:50px minmax(0,1fr);
      padding:22px 14px 20px; gap:14px; }}
    .logo {{ grid-column:1; width:50px; height:50px; }}
    .mast-text {{ grid-column:2; min-width:0; }}
    .mast-title {{ font-size:23px; }}
    .mast-title-img {{ max-height:31px; }}
    .slogan-img {{ max-height:20px; }}
    .stat-strip {{ grid-column:1 / -1; display:grid;
      grid-template-columns:repeat(auto-fit,minmax(80px,1fr)); min-width:0;
      width:100%; gap:8px; }}
    .stat {{ min-width:0; padding:10px 7px; }}
    .stat-num {{ font-size:21px; }}
    .section {{ width:100%; min-width:0; margin-top:32px; }}
    .sec-head {{ flex-wrap:wrap; gap:8px; margin-bottom:14px; }}
    .sec-head h2 {{ font-size:19px; }}
    .hero-grid, .all-grid {{ width:100%; min-width:0; grid-template-columns:1fr; }}
    .hero-card, .type-block {{ min-width:0; }}
    .hero-title {{ font-size:18px; }}
    .hero-rep {{ flex-direction:column; gap:4px; }}
    .rep-body, .row-title {{ min-width:0; }}
    .hero-title, .rep-body a, .row-title a {{ overflow-wrap:anywhere; }}
    .row {{ align-items:flex-start; flex-direction:column; gap:2px; }}
    .row-pub {{ font-size:12.5px; }}
    .section-other .other-panel {{ padding:8px 14px; }}
  }}
  /* ---- Print ---- */
  @media print {{
    body {{ background:#fff; }}
    .hero-card, .type-block {{ break-inside:avoid; box-shadow:none; }}
    .hero-card:has(.related) {{ break-inside:auto; }}
    .sec-head, .type-head, .hero-head, .hero-title {{ break-after:avoid; }}
    .related-list li {{ break-inside:avoid; }}
    .hero-card:hover {{ transform:none; }}
    .related::details-content {{ display:block; content-visibility:visible; }}
    .related summary {{ min-height:0; padding:4px 0; }}
    a {{ color:var(--ink); }}
  }}
</style>
</head>
<body>
  <div class="masthead">
    <div class="flow-band"></div>
    <div class="mast-inner">
      {logo_html}
      <div class="mast-text">
        <p class="kicker">Incheon Education · Morning Press Brief {unofficial_tag}</p>
        <h1 class="mast-title">{title_html}</h1>
        <p class="mast-date">브리핑 기준일 · {esc(date_disp)}</p>
        {slogan_html}
      </div>
      <div class="stat-strip">{stats}</div>
    </div>
  </div>
  <main class="wrap">
    {hero_html or ''}
    {interest_html or ''}
    {all_html or ''}
    {other_html or ''}
    {'<div class="empty">확인된 유의미 기사 없음</div>' if not (hero_html or interest_html or all_html or other_html) else ''}
    <footer class="foot">
      <p><span class="flow-dot"></span><b>인천광역시교육청 및 산하기관 언론보도</b>를 현안성·기관 관련성·후속 대응 필요성 기준으로 정리한 개인 브리핑입니다.</p>
      <p>점검일 {esc(data["batch_date"])} · 생성 시각 {esc(gen_at)} (한국시간) · 동일보도는 대표기사와 관련 기사로 묶었습니다. 이슈 요약은 담당자가 검토·작성한 내용입니다. 기사 링크는 새 창으로 열립니다.</p>
      {'<p class="disclaimer">본 페이지는 공개된 뉴스 링크를 개인이 정리한 <b>비공식 자료</b>이며, 인천광역시교육청의 공식 발행물이 아닙니다.</p>' if public else ''}
    </footer>
  </main>
</body>
</html>'''
