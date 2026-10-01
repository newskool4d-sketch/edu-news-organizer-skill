# 입력 데이터 계약 (상류: daily-news-picker)

> 2026-07-17 작성, 2026-07-23 학교·학생 관련성 메타데이터 반영. 상류 정본: `~/.codex/skills/daily-news-picker/` (repo: daily-news-picker-skill)
> 이 스킬은 수집을 하지 않는다 — 아래 3종 입력을 받아 저장·정리만 수행한다.

## 1. collected_articles.json (후보 원본 — 먼저 저장)

상류 `scripts/collect_news_rss.py` 산출물. 러너 실행 시 Codex 작업 폴더
(`%TEMP%\DailyNewsPickerCodexWork\current\collected_articles.json`)에도 생성된다.

```json
{
  "window_start": "2026-07-15 05:00",
  "window_end": "2026-07-16 05:00",
  "engine": "google-news-rss",
  "article_count": 312,
  "failures": [{"query": "인천교육청", "label": "Q1", "error": "100건 상한 도달"}],
  "articles": [{
    "title": "...", "publisher": "...", "publisher_domain": "...",
    "google_url": "... | null", "original_url": "...", "url_status": "복원|미복원|원문직접",
    "published_at_kst": "2026-07-15 10:00", "queries": ["Q1:인천교육청"],
    "engine": "google-news-rss | naver-api",
    "relevance_hint": "likely_relevant | likely_irrelevant | needs_review",
    "publication_eligible": false,
    "body_status": "본문 미수집 | 본문 미검증 | 본문 검증 완료",
    "publication_verification_basis": "",
    "publication_verified_at": "",
    "relevance_reasons": ["incheon_location", "education_subject"],
    "location_hits": ["남동구"], "education_subject_hits": ["중학생"],
    "student_story_hits": ["구조"], "negative_context_hits": []
  }]
}
```

매핑: `batch_date` = window_end의 날짜부(보고일) / `list_type` = "인천교육" 고정 /
`original_url` 없으면 `google_url` 폴백. 관련성 힌트와 근거 배열은 기사 테이블에 JSON 문자열로 보존한다.
`engine: naver-api` 및 모든 `collector-json` 항목은 미선별 후보라는 점에 유의하며,
`relevance_hint`는 수집·검토용 triage 값이며 공개 권한이 아니다. 후보는 `publication_eligible`이
true이고 `body_status`가 "본문 검증 완료"이며 `publication_verification_basis`·`publication_verified_at`가
채워진 경우에만 공개한다(2026-10-02 사용자 승인으로 복원). 그 외 후보는 검색·검토용으로만 보존한다.

## 2. 일일 브리핑 md (선별 완료본)

상류 산출 폴더 `Documents\Codex\DailyNewsPicker\인천교육청 언론보도 현황(YYYYMMDD)\*.md`.

- 날짜·구분 헤더: `2026. 7. 15.(수) 인천광역시교육청 주요 언론보도 현황입니다.`
- 기사 형식: `■ 제목 - 매체` 다음 줄에 URL (매체명은 마지막 ` - ` 뒤 — 제목 내 하이픈 허용)
- `## 핵심 요약`·`## 제외 또는 참고`의 불릿은 기사가 아니므로 무시

## 3. 붙여넣기 텍스트 (본청 배포 목록)

```text
2026. 7. 16.(목) 시·도교육청 및 교육부 주요 언론보도 현황입니다.
■ 제목 - 언론사
URL
...
2026. 7. 16.(목) 인천광역시교육청 주요 언론보도 현황입니다.
■ 제목 - 언론사
URL
```

헤더 주제부에 `교육부`/`시·도교육청` 포함 → `전국·교육부`, 그 외 → `인천교육`.
한 파일에 두 섹션이 연달아 와도 각각 별도 배치로 저장한다.

## 공통 규칙

- 후보·선별 분리: 기존 게시 흐름대로 `briefing-md` 또는 본청 `paste`를 먼저 저장하고,
  `collector-json`을 이어서 저장해 후보 원본과 관련성 메타데이터를 보존한다. 본문 검증 표식으로
  승격된 파생 JSON이 있으면 원본 JSON 대신 그 파생본을 인제스트한다.
  날짜별 `article_selections`에는 브리핑 명시 기사와 본문 검증이 완비된
  (`publication_eligible`) 후보만 기록한다. 검증 상태·근거·시각은 선택 관계에도
  복사해 같은 `clean_url`이 다른 날짜에 재등장해도 날짜별 검증 이력을 독립적으로 보존한다.
- 후보 보존: 모든 triage 상태 항목을 삭제하지 않고 내부 검색·검토용 DB에 보존한다.
  공개 묶음·다이제스트에서는 브리핑 명시 기사와 본문 검증 완료 후보만 게시한다.
- 관련성 메타데이터: `relevance_hint`와 근거 배열은 상류 선별을 돕는 정보다. organizer는 이를
  임의로 최종 판정하지 않고 상류 선별 결과를 명시적 상태로 소비한다.
- 표시 순서: `교육감 → 정책·현안 → 인터뷰·기획 → 사업·성과 → 행사·모집 → 기타 → 비판·점검 → 타 시도 동향`을 유지한다.
- 중복 판정: `clean_url`(추적 파라미터 제거 후 URL) UNIQUE. 같은 기사의 모바일/AMP URL 변형은
  현 단계에서 별개로 저장된다 (실측: 브리핑 38건 중 37건 중복 감지, 1건 URL 변형 통과) —
  2단계 동일보도 묶기에서 처리 예정.
- 원본 보존: 입력 전문을 `source_batches.raw_text`에 그대로 저장.
- 구 스키마 마이그레이션: 본문 검증 증거가 없는 기존 `collector-json` 선택 관계는 `open_db` 시
  `legacy_candidate_selections`로 격리하며(사유·날짜·입력 순서 보존), 공개 선택 관계에서 제거한다.
  2026-08-21~10-01 사이 allow-list로 복원됐던 관계도 같은 규칙으로 다시 격리된다.
- DB: `%USERPROFILE%\Documents\Codex\EduNewsOrganizer\news.db`
  (환경변수 `EDU_NEWS_DB_PATH` 또는 `--db` 오버라이드). repo에는 커밋하지 않는다.
