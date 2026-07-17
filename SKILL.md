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
python scripts/newsdb.py export --date 2026-07-16 --format md
python scripts/newsdb.py stats
```

- DB 위치: `%USERPROFILE%\Documents\Codex\EduNewsOrganizer\news.db` (환경변수 `EDU_NEWS_DB_PATH` 또는 `--db`로 변경)
- 원본 목록(raw_text)은 source_batches에 그대로 보존한다 (기본 원칙: 원본 보존)
- 관심도 표시는 저장 시점이 아니라 조회 시점에 interests 테이블과 대조한다 (키워드 변경 즉시 반영)

## Workflow

1. 입력 종류를 판별한다: 상류 JSON > 브리핑 md > 붙여넣기 텍스트 순으로 정밀하다.
2. `ingest` 실행 후 신규/중복 건수를 사용자에게 보고한다. 중복은 clean_url 기준이며 조용히 버리지 않고 건수로 알린다.
3. 조회 요청은 `search` 필터(키워드·날짜·목록구분·매체·읽음·보관·관심)로 답하고, 기사 ID를 함께 보여줘 후속 `mark`가 가능하게 한다.
4. 요약·분류·묶기(2단계 스마트 정리)는 아직 CLI에 없다 — 요청받으면 search 결과를 에이전트가 직접 정리하되, 기사에 없는 사실을 추가하지 않는다.
5. 출력은 `export`(md/csv)를 우선 사용하고, 화면 제시용 재구성은 자유.

## Writing Rules

- 한국어 응답, 행정 브리핑 문체.
- 기사 원문과 요약·해석을 구분해 표시한다.
- 본문 미확보 기사는 제목 기반임을 명시한다.
