---
name: edu-news-organizer
description: Ingest, organize, search, and annotate daily education news (Incheon + national) into a personal SQLite archive with interest tagging, read/favorite/memo actions, and md/csv export.
metadata:
  short-description: 개인형 교육뉴스 정리·검색·보관 도구 (daily-news-picker 하류)
---

# Edu News Organizer

## Overview

daily-news-picker(상류 수집·선별 스킬)가 만드는 산출물 또는 본청 배포 뉴스 목록을 받아
개인 SQLite 아카이브에 축적하고, 관심 키워드 표시·읽음/보관/메모·검색·출력을 제공하는 하류 정리 도구.

- 정본 계획: 개인형 교육뉴스 수집·정리 자동화 도구 구축계획 (2026-07-17 착수, MVP 1단계 = 스킬 + CLI)
- 입력 계약: [references/data-contract.md](./references/data-contract.md)
- 뉴스를 새로 수집하지 않는다 — 수집·선별은 상류 스킬 소관.

## Quick Start

사용자가 다음과 같이 요청하면 이 스킬을 사용:

- "오늘 뉴스 DB에 넣어줘 / 정리해줘" → `ingest`
- "학생교육원 기사 찾아줘 / 보관함 보여줘" → `search`
- "이 기사 보관해줘 / 메모 남겨줘 / 읽음 처리" → `mark`
- "관심 키워드 추가해줘" → `interests`
- "이번 주 기사 md로 뽑아줘" → `export`

## CLI

모든 기능은 `scripts/newsdb.py` 단일 진입점으로 실행한다 (Python 표준 라이브러리만 사용).

```text
python scripts/newsdb.py ingest --json <collected_articles.json>   # 상류 수집기 JSON
python scripts/newsdb.py ingest --briefing <브리핑.md>              # 상류 일일 브리핑 md
python scripts/newsdb.py ingest --paste <목록.txt>                  # 본청 배포 목록 붙여넣기 저장본
python scripts/newsdb.py search --q 학생교육원 --favorite --limit 20
python scripts/newsdb.py mark --id 12 --favorite --memo "2027 캠프 계획 참고"
python scripts/newsdb.py interests list|add|remove|seed
python scripts/newsdb.py group --date 2026-07-16                   # 동일보도 묶기
python scripts/newsdb.py groups list --date 2026-07-16             # 묶음·요약 확인
python scripts/newsdb.py groups summary --id 12 --text "요약문"     # 이슈 요약 저장
python scripts/newsdb.py digest --date 2026-07-16 --format md      # 정리본 md
python scripts/newsdb.py digest --date 2026-07-16 --format html --out 다이제스트.html  # 프리미엄 HTML
python scripts/newsdb.py export --date 2026-07-16 --format md
python scripts/newsdb.py stats
```

HTML 다이제스트는 인천광역시교육청 CI 팔레트 기반 에디토리얼 브리핑(로고 임베드·자기완결)이다.
러너 자동 실행 시 브리핑 폴더에 `교육뉴스 다이제스트.html`로 함께 저장된다.
이슈 요약(`groups summary`)은 담당자가 검토·작성한다 — 기계 요약으로 사실을 지어내지 않는다.

## 웹 배포 (선택)

```bash
python scripts/publish_site.py --site-dir <site폴더> --date 2026-07-16   # 공개 안전본 굽기
python scripts/publish_site.py --site-dir <site폴더> --date 2026-07-16 --push  # git push까지
```

`site/`(index·archive)를 GitHub→Vercel로 자동 배포한다. 절차·환경변수·안전 설계는
[references/deploy-vercel.md](./references/deploy-vercel.md). **웹 배포본은 개인 데이터(내 관심업무·메모)를
제외한 공개 안전본**이며 "비공식·개인 정리용" 표기가 붙는다. 전체 개인 버전은 로컬 전용.

공개 방식의 잠금 정본은 [references/publication-contract.json](./references/publication-contract.json)이다.
입력 순서·후보 게시 경계·데이터가 있는 최대 3단 섹션의 순서·반응형 열·기사 유형 순서가 이 JSON과 다르면
`publish_site.py`가 생성을 중단한다. JSON 또는 템플릿 변경은 사용자 명시 승인 없이는 금지한다.

- DB 위치: `%USERPROFILE%\Documents\Codex\EduNewsOrganizer\news.db` (환경변수 `EDU_NEWS_DB_PATH` 또는 `--db`로 변경)
- 원본 목록(raw_text)은 source_batches에 그대로 보존한다 (기본 원칙: 원본 보존)
- 상류 JSON의 `relevance_hint`·판정 근거·위치/교육주체/학생활동 히트는 기사 단위로 보존한다. 후보 자체는 삭제하지 않는다. 공개 묶음·다이제스트에는 브리핑 명시 기사와 `likely_relevant`·`needs_review` 후보를 사용하고 `likely_irrelevant`만 제외해 기존 동일보도 묶음 구조를 유지한다. 본문 검증 메타데이터는 별도 보존한다.
- 관심도 표시는 저장 시점이 아니라 조회 시점에 interests 테이블과 대조한다 (키워드 변경 즉시 반영)

## Workflow

1. 이전 승인 흐름대로 `briefing-md`를 먼저 넣고 `collector-json` 후보 풀을 이어서 넣는다. 같은 URL이 다른 날짜에 다시 등장해도 날짜별 선택 관계와 본문 검증 메타데이터를 보존한다. 후보 전체는 검색·검토용으로 유지하되, 공개본은 브리핑 명시 기사와 `relevance_hint`가 `likely_relevant` 또는 `needs_review`인 후보를 기존 방식으로 묶어 싣는다.
2. `ingest` 실행 후 신규/중복 건수를 사용자에게 보고한다. 중복은 clean_url 기준이며 조용히 버리지 않고 건수로 알린다.
3. 조회 요청은 `search` 필터(키워드·날짜·목록구분·매체·읽음·보관·관심)로 답하고, 기사 ID를 함께 보여줘 후속 `mark`가 가능하게 한다.
4. `group`으로 선별된 기사끼리만 동일보도를 묶고 제목 기반 유형·교육 분야를 자동 분류한다. 이슈 요약은 담당자가 검토·작성하며 기사에 없는 사실을 추가하지 않는다.
5. 출력은 `export`(md/csv)를 우선 사용하고, 화면 제시용 재구성은 자유.

## Writing Rules

- 한국어 응답, 행정 브리핑 문체.
- 기사 원문과 요약·해석을 구분해 표시한다.
- 본문 미확보 기사는 제목 기반임을 명시한다.
