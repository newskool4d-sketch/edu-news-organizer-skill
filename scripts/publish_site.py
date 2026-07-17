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
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import newsdb  # noqa: E402

if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DATE_RE = re.compile(r"^(20\d{2}-\d{2}-\d{2})\.html$")


def esc(s) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def _fmt(date_iso: str) -> str:
    try:
        d = datetime.strptime(date_iso, "%Y-%m-%d")
        wd = ["월", "화", "수", "목", "금", "토", "일"][d.weekday()]
        return f"{d.year}. {d.month}. {d.day}. ({wd})"
    except Exception:
        return date_iso


def render_archive_index(dates: list) -> str:
    """날짜 목록(ISO) → 아카이브 인덱스 HTML. 최신순 정렬, 자기완결."""
    dates = sorted(set(dates), reverse=True)
    items = "\n".join(
        f'    <li><a href="archive/{esc(d)}.html"><span class="d">{esc(d)}</span>'
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
    <a class="home" href="index.html">← 최신 브리핑</a>
    <h1>지난 호 보기</h1>
    <p class="sub">인천교육 언론보도 브리핑 아카이브 (비공식 · 개인 정리용)</p>
    <ul>
{items}
    </ul>
  </div>
</body>
</html>'''


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
        conn = newsdb.open_db(db_path)
        html = newsdb.render_digest_html(newsdb.build_digest_data(conn, date_iso), public=True)
        conn.close()
        (site_dir / "archive" / f"{date_iso}.html").write_text(html, encoding="utf-8", newline="\n")

    dates = sorted(set(existing_dates(site_dir)), reverse=True)
    if dates:
        latest = dates[0]
        latest_html = (site_dir / "archive" / f"{latest}.html").read_text(encoding="utf-8")
        (site_dir / "index.html").write_text(latest_html, encoding="utf-8", newline="\n")
    (site_dir / "archive" / "index.html").write_text(render_archive_index(dates), encoding="utf-8", newline="\n")
    return dates


def git_push(site_dir: Path, date_iso: str):
    for args in (["git", "add", "-A"],
                 ["git", "commit", "-m", f"다이제스트 갱신: {date_iso or datetime.now():%Y-%m-%d}"],
                 ["git", "push"]):
        r = subprocess.run(args, cwd=str(site_dir), capture_output=True, text=True)
        if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
            print(f"[git] {' '.join(args)} → {r.stderr.strip() or r.stdout.strip()}", file=sys.stderr)


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
