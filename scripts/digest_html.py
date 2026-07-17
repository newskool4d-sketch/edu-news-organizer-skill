# -*- coding: utf-8 -*-
"""edu-news-organizer 다이제스트 HTML 렌더러.

디자인: 인천광역시교육청 CI 팔레트 기반 프리미엄 에디토리얼 브리핑.
- 브랜드색(심벌마크 실측): 딥블루 #0060B0, 그린 #00A160, 라임 #80C030, 오렌지태양 #F08020, 시안 #17B0D4
- 자기완결(self-contained): 외부 리소스 없음, 로고만 data URI 임베드
- 입력(제목·매체)은 외부 뉴스라 신뢰 불가 → 전부 HTML 이스케이프, http(s) URL만 링크화
"""
import html as _html
from datetime import datetime

# 성격 → 강조색 (CI 팔레트 파생)
TYPE_COLOR = {
    "정책·현안": "#0060B0",
    "비판·점검": "#E2620E",
    "인터뷰·기획": "#0E8FB0",
    "사업·성과": "#00A160",
    "행사·모집": "#6FA31E",
    "타 시도 동향": "#5B6B7C",
    "기타": "#8A98A8",
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


def _hero_card(g: dict) -> str:
    color = TYPE_COLOR.get(g["article_type"], TYPE_COLOR["기타"])
    rep = g["representative"]
    fields = "".join(_chip(f) for f in g["fields"])
    summary = f'<p class="hero-summary">{esc(g["summary"])}</p>' if g.get("summary") else ""
    others = ""
    if g["others"]:
        CAP = 10
        shown = g["others"][:CAP]
        chips = "".join(f'<span class="src-chip">{esc(m["publisher"])}</span>' for m in shown)
        extra = len(g["others"]) - len(shown)
        if extra > 0:
            chips += f'<span class="src-chip src-more">+{extra}</span>'
        others = f'<div class="related"><span class="related-label">관련 매체</span>{chips}</div>'
    rep_link = _link(rep["original_url"], rep["title"])
    return f'''
      <article class="hero-card" style="--accent:{color}">
        <header class="hero-head">
          <span class="type-badge" style="background:{color}">{esc(g["article_type"])}</span>
          <div class="field-chips">{fields}</div>
        </header>
        <h3 class="hero-title">{esc(g["title"])}</h3>
        {summary}
        <div class="hero-count">
          <span class="count-pill" style="--accent:{color}">관련 보도 {esc(g["member_count"])}건</span>
          <div class="count-bar"><span style="width:{min(100, g["member_count"] * 7 + 12)}%"></span></div>
        </div>
        <div class="hero-rep">
          <span class="rep-label">대표기사</span>
          <span class="rep-body">{rep_link}<span class="rep-pub">{esc(rep["publisher"])}</span></span>
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
          <span class="int-title">{_link(r["original_url"], r["title"])}</span>
          <span class="int-pub">{esc(r["publisher"])}</span>
        </div>
        {memo}
      </li>'''


def _all_row(r: dict) -> str:
    return (f'<li class="row"><span class="row-title">{_link(r["original_url"], r["title"])}</span>'
            f'<span class="row-pub">{esc(r["publisher"])}</span></li>')


def render(data: dict, logo_uri: str = "", public: bool = False,
           slogan_uri: str = "", title_uri: str = "") -> str:
    """public=True면 개인 데이터(내 관심업무·메모)를 빼고 비공식 표기를 넣은 공개 안전본을 만든다.

    slogan_uri/title_uri: 공식 서체(소통·힘찬)로 사전 렌더한 PNG data URI — 웹폰트 없이 브랜드 서체 반영.
    """
    meta = data["meta"]
    date_disp = _fmt_date(data["batch_date"])
    gen_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    logo_html = (f'<img class="logo" src="{esc(logo_uri)}" alt="인천광역시교육청" />'
                 if logo_uri else '<div class="logo logo-fallback"></div>')

    stats = (_stat(meta["total"], "수집 기사")
             + _stat(meta["issue_count"], "핵심 이슈")
             + _stat(meta["publisher_count"], "매체"))
    if not public:
        stats = (_stat(meta["total"], "수집 기사")
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
                f'<li class="row"><span class="row-title">{_link(rep["original_url"], g["title"])}'
                f'<span class="row-count">관련 {esc(g["member_count"])}건</span></span>'
                f'<span class="row-pub">{esc(rep["publisher"])}</span></li>')
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
<link rel="icon" href="data:,">
<title>인천교육 언론보도 브리핑 · {esc(date_disp)}</title>
<style>
  :root {{
    color-scheme:light;
    --blue:#0060B0; --blue-deep:#004a8a; --blue-mid:#4782C4; --cyan:#17B0D4;
    --green:#00A160; --lime:#80C030; --orange:#F08020;
    --ink:#12233A; --ink-soft:#33475e; --muted:#6b7c8e; --faint:#93a3b3;
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
  .wrap {{ max-width:1120px; margin:0 auto; padding:0 22px 72px; }}

  /* ---- Masthead ---- */
  .masthead {{ position:relative; background:var(--card); border-bottom:1px solid var(--line);
    overflow:hidden; }}
  .flow-band {{ height:7px; background:var(--flow); }}
  .mast-inner {{ max-width:1120px; margin:0 auto; padding:30px 22px 26px;
    display:flex; align-items:center; gap:22px; flex-wrap:wrap; }}
  .logo {{ width:60px; height:60px; object-fit:contain; flex:0 0 auto;
    filter:drop-shadow(0 3px 8px rgba(0,74,138,.16)); }}
  .logo-fallback {{ border-radius:50%; background:var(--flow); }}
  .mast-text {{ flex:1 1 auto; min-width:220px; }}
  .kicker {{ margin:0 0 4px; font-size:12px; font-weight:800; letter-spacing:.14em;
    color:var(--blue); text-transform:uppercase; }}
  .mast-title {{ margin:0; font-size:30px; font-weight:800; letter-spacing:-.02em; line-height:1.12; }}
  .mast-date {{ margin:7px 0 0; font-size:15px; font-weight:600; color:var(--muted); }}
  .stat-strip {{ display:flex; gap:10px; flex:0 0 auto; }}
  .stat {{ display:flex; flex-direction:column; align-items:center; justify-content:center;
    min-width:74px; padding:12px 10px; background:linear-gradient(180deg,#f7fafc,#eef3f8);
    border:1px solid var(--line); border-radius:14px; }}
  .stat-num {{ font-size:24px; font-weight:800; color:var(--blue); line-height:1; letter-spacing:-.02em; }}
  .stat-label {{ margin-top:5px; font-size:11px; font-weight:600; color:var(--muted); }}

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
  .hero-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:16px;
    align-items:start; }}
  .hero-card {{ position:relative; background:var(--card); border:1px solid var(--line);
    border-radius:16px; padding:20px 20px 18px; overflow:hidden;
    box-shadow:0 1px 2px rgba(18,35,58,.04); transition:transform .16s ease, box-shadow .16s ease; }}
  .hero-card::before {{ content:""; position:absolute; inset:0 auto 0 0; width:4px; background:var(--accent); }}
  .hero-card:hover {{ transform:translateY(-3px); box-shadow:0 12px 30px rgba(18,35,58,.10); }}
  .hero-head {{ display:flex; align-items:center; gap:8px; margin-bottom:12px; flex-wrap:wrap; }}
  .type-badge {{ font-size:12px; font-weight:800; color:#fff; padding:4px 11px; border-radius:20px;
    letter-spacing:-.01em; }}
  .field-chips {{ display:flex; gap:5px; flex-wrap:wrap; }}
  .chip {{ font-size:11px; font-weight:600; color:var(--ink-soft); background:var(--line-soft);
    padding:3px 9px; border-radius:20px; }}
  .hero-title {{ margin:0 0 10px; font-size:19px; font-weight:800; line-height:1.34; letter-spacing:-.02em; }}
  .hero-summary {{ margin:0 0 14px; font-size:14px; color:var(--ink-soft); line-height:1.62;
    padding-left:12px; border-left:2px solid var(--line); }}
  .hero-count {{ display:flex; align-items:center; gap:10px; margin-bottom:14px; }}
  .count-pill {{ font-size:12.5px; font-weight:800; color:var(--accent); white-space:nowrap; }}
  .count-bar {{ flex:1; height:6px; background:var(--line-soft); border-radius:4px; overflow:hidden; }}
  .count-bar span {{ display:block; height:100%; background:var(--flow); border-radius:4px; }}
  .hero-rep {{ display:flex; gap:9px; padding-top:13px; border-top:1px dashed var(--line); }}
  .rep-label {{ flex:0 0 auto; font-size:11px; font-weight:800; color:var(--faint); padding-top:2px; }}
  .rep-body {{ display:flex; flex-direction:column; gap:2px; }}
  .rep-body a {{ font-size:14.5px; font-weight:700; line-height:1.4; }}
  .rep-pub {{ font-size:12px; color:var(--muted); font-weight:600; }}
  .related {{ display:flex; align-items:center; gap:7px; margin-top:12px; flex-wrap:wrap; }}
  .related-label {{ font-size:11px; font-weight:800; color:var(--faint); }}
  .src-chip {{ font-size:11px; color:var(--muted); background:#f2f6f9; border:1px solid var(--line-soft);
    padding:2px 8px; border-radius:6px; }}
  .src-more {{ color:var(--blue); font-weight:700; background:#eaf2fa; border-color:#d5e4f3; }}

  /* ---- Interest ---- */
  .section-interest .int-list {{ list-style:none; margin:0; padding:0;
    background:linear-gradient(180deg,#fff9f2,#fff); border:1px solid #f4dcc4;
    border-radius:16px; overflow:hidden; }}
  .int-item {{ padding:15px 20px; border-bottom:1px solid #f7e8d8; }}
  .int-item:last-child {{ border-bottom:0; }}
  .int-tags {{ display:flex; align-items:center; gap:6px; margin-bottom:6px; flex-wrap:wrap; }}
  .int-tag {{ font-size:11px; font-weight:800; color:#fff; background:var(--orange);
    padding:2px 9px; border-radius:20px; }}
  .fav-star {{ color:#f5a623; font-size:14px; }}
  .int-body {{ display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }}
  .int-title a {{ font-size:15.5px; font-weight:700; color:var(--ink); }}
  .int-title a:hover {{ color:var(--blue); }}
  .int-pub {{ font-size:12.5px; color:var(--muted); font-weight:600; }}
  .int-memo {{ margin:7px 0 0; font-size:13px; color:#b06a1e; background:#fff4e8;
    padding:7px 11px; border-radius:9px; }}

  /* ---- All articles ---- */
  .all-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(330px,1fr)); gap:14px 26px; }}
  .type-block {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px 18px; }}
  .type-head {{ display:flex; align-items:center; gap:8px; margin:0 0 10px; font-size:15px; font-weight:800; }}
  .type-dot {{ width:9px; height:9px; border-radius:50%; }}
  .type-num {{ margin-left:auto; font-size:12px; font-weight:800; color:var(--muted);
    background:var(--line-soft); padding:1px 9px; border-radius:20px; }}
  .rows {{ list-style:none; margin:0; padding:0; }}
  .row {{ display:flex; align-items:baseline; gap:10px; padding:7px 0; border-top:1px solid var(--line-soft); }}
  .row:first-child {{ border-top:0; }}
  .row-title {{ flex:1; }}
  .row-title a {{ font-size:14px; font-weight:600; color:var(--ink-soft); line-height:1.45; }}
  .row-title a:hover {{ color:var(--blue); }}
  .row-pub {{ flex:0 0 auto; font-size:11.5px; color:var(--faint); font-weight:600; }}

  /* ---- Footer ---- */
  .foot {{ margin-top:44px; padding-top:20px; border-top:2px solid var(--line);
    font-size:12.5px; color:var(--muted); }}
  .foot b {{ color:var(--ink-soft); }}
  .foot .flow-dot {{ display:inline-block; width:8px; height:8px; border-radius:50%;
    background:var(--flow); margin-right:6px; vertical-align:middle; }}

  .empty {{ padding:40px; text-align:center; color:var(--faint); font-weight:600; }}
  .unofficial {{ display:inline-block; margin-left:8px; padding:2px 9px; border-radius:20px;
    background:#fff1e6; color:#c05a17; border:1px solid #f3d3b6; font-size:10.5px;
    font-weight:800; letter-spacing:0; vertical-align:middle; }}

  /* ---- 공식 서체 자산 (힘찬 제목·소통 슬로건) ---- */
  .mast-title-img {{ display:block; height:38px; width:auto; max-width:100%; }}
  .slogan-img {{ display:block; height:24px; width:auto; max-width:100%; margin-top:10px; }}

  /* ---- 타 시도·일반 교육 동향 (인천 뉴스와 분리, 차분한 슬레이트 톤) ---- */
  .sec-bar.slate {{ background:#5B6B7C; }}
  .slate-count {{ background:#5B6B7C; }}
  .section-other .other-panel {{ background:#f6f8fa; border:1px solid var(--line);
    border-radius:14px; padding:8px 18px; }}
  .section-other .row-title a {{ color:var(--muted); }}
  .section-other .row-title a:hover {{ color:var(--blue); }}
  .row-count {{ margin-left:8px; font-size:11px; font-weight:700; color:#5B6B7C;
    background:#e8edf2; padding:1px 8px; border-radius:12px; white-space:nowrap; }}
  .disclaimer {{ margin-top:6px; color:var(--faint); }}
  .disclaimer b {{ color:var(--orange); }}

  /* ---- Responsive ---- */
  @media (max-width:720px) {{
    .mast-inner {{ gap:16px; }}
    .mast-title {{ font-size:23px; }}
    .stat-strip {{ width:100%; justify-content:space-between; }}
    .stat {{ flex:1; min-width:0; }}
    .hero-grid, .all-grid {{ grid-template-columns:1fr; }}
  }}
  /* ---- Print ---- */
  @media print {{
    body {{ background:#fff; }}
    .hero-card, .type-block {{ break-inside:avoid; box-shadow:none; }}
    .hero-card:hover {{ transform:none; }}
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
        <p class="mast-date">{esc(date_disp)} 점검 기준</p>
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
    {'<div class="empty">확인된 유의미 기사 없음</div>' if not (data["hero_issues"] or data["interest_articles"] or data["all_by_type"] or other_html) else ''}
    <footer class="foot">
      <p><span class="flow-dot"></span><b>인천광역시교육청 및 산하기관 언론보도</b>를 현안성·기관 관련성·후속 대응 필요성 기준으로 정리한 개인 브리핑입니다.</p>
      <p>점검일 {esc(data["batch_date"])} · 생성 시각 {esc(gen_at)} · 동일보도는 대표기사 1건으로 묶고 관련 매체를 병기했습니다. 이슈 요약은 담당자가 검토·작성한 내용입니다.</p>
      {'<p class="disclaimer">본 페이지는 공개된 뉴스 링크를 개인이 정리한 <b>비공식 자료</b>이며, 인천광역시교육청의 공식 발행물이 아닙니다.</p>' if public else ''}
    </footer>
  </main>
</body>
</html>'''
