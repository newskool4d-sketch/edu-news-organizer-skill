# -*- coding: utf-8 -*-
"""newsdb 핵심 동작 테스트 (TDD: 구현 전 작성, 표준 라이브러리 unittest)."""
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import newsdb  # noqa: E402  (RED 단계에서는 존재하지 않아 ImportError)


class TestCleanUrl(unittest.TestCase):
    def test_removes_tracking_params(self):
        self.assertEqual(
            newsdb.clean_url("https://a.kr/n?idxno=1&utm_source=x&ref=naver&wlog_tag3=naver"),
            "https://a.kr/n?idxno=1",
        )

    def test_keeps_content_params_and_plain_urls(self):
        self.assertEqual(newsdb.clean_url("https://a.kr/n?key=20260717"), "https://a.kr/n?key=20260717")
        self.assertEqual(newsdb.clean_url("https://a.kr/news/123"), "https://a.kr/news/123")


class TestParseBriefingMd(unittest.TestCase):
    SAMPLE = """# 인천교육청 언론보도 현황

2026. 7. 15.(수) 인천광역시교육청 주요 언론보도 현황입니다.

## 핵심 요약

- 요약 불릿은 기사가 아니다.

## 주요 언론보도

■ 인천시교육청, 2학기부터 '아침 급식' 10개 초교 추가 - 경인일보
https://www.kyeongin.com/article/1767324

■ 개청 앞둔 영종·검단교육지원청 임시청사 위치는 하반기에 윤곽 - 기호일보
https://www.kihoilbo.co.kr/news/articleView.html?idxno=3028964

## 제외 또는 참고

- 제외 사유 불릿.
"""

    def test_extracts_date_and_articles(self):
        batch_date, list_type, articles = newsdb.parse_briefing_md(self.SAMPLE)
        self.assertEqual(batch_date, "2026-07-15")
        self.assertEqual(list_type, "인천교육")
        self.assertEqual(len(articles), 2)
        self.assertEqual(articles[0]["title"], "인천시교육청, 2학기부터 '아침 급식' 10개 초교 추가")
        self.assertEqual(articles[0]["publisher"], "경인일보")
        self.assertEqual(articles[0]["original_url"], "https://www.kyeongin.com/article/1767324")
        self.assertEqual(articles[1]["publisher"], "기호일보")

    def test_extracts_section_date_format_used_by_daily_runner(self):
        text = """# 인천교육청 언론보도 현황

## 보도일
2026. 7. 24.(금)

## 주요 언론보도

■ 인천북부교육지원청, 교육복지 학생 대상 여름방학 멘토링 - 뉴스타운
https://newstown.co.kr/news/articleView.html?idxno=710131
"""
        batch_date, list_type, articles = newsdb.parse_briefing_md(text)
        self.assertEqual(batch_date, "2026-07-24")
        self.assertEqual(list_type, "인천교육")
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["publisher"], "뉴스타운")

    def test_extracts_report_date_metadata_used_by_current_runner(self):
        text = """# 인천교육청 언론보도 현황

- 보고일: 2026-08-19
- 점검 창: 2026-08-18 05:00 ~ 2026-08-19 05:00 (Asia/Seoul)

## 주요 언론보도

■ 인천교육청, 교육정책 간담회 개최 - 예시매체
https://example.com/report-date
"""
        batch_date, list_type, articles = newsdb.parse_briefing_md(text)
        self.assertEqual(batch_date, "2026-08-19")
        self.assertEqual(list_type, "인천교육")
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["publisher"], "예시매체")

    def test_extracts_report_date_metadata_with_korean_weekday(self):
        text = """# 인천교육청 언론보도 현황

- 보고일: 2026. 8. 21.(금)
- 점검 범위: 2026-08-20 05:00 ~ 2026-08-21 05:00 (Asia/Seoul)

## 주요 언론보도

■ 인천교육청, 교육정책 간담회 개최 - 예시매체
https://example.com/report-date-weekday
"""
        batch_date, list_type, articles = newsdb.parse_briefing_md(text)
        self.assertEqual(batch_date, "2026-08-21")
        self.assertEqual(list_type, "인천교육")
        self.assertEqual(len(articles), 1)

    def test_ingest_cli_fails_when_briefing_date_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            briefing = root / "briefing.md"
            database = root / "news.db"
            briefing.write_text(
                "# 인천교육청 언론보도 현황\n\n"
                "## 주요 언론보도\n\n"
                "■ 날짜 없는 기사 - 예시매체\n"
                "https://example.com/no-date\n",
                encoding="utf-8",
            )

            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    newsdb.main([
                        "--db", str(database),
                        "ingest", "--briefing", str(briefing),
                    ])

            self.assertEqual(raised.exception.code, 2)
            self.assertIn("날짜 헤더", stderr.getvalue())
            conn = newsdb.open_db(str(database))
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM source_batches").fetchone()[0],
                0,
            )
            conn.close()


class TestParsePaste(unittest.TestCase):
    SAMPLE = """2026. 7. 16.(목) 시·도교육청 및 교육부 주요 언론보도 현황입니다.

■ 교육부, 늘봄학교 확대 발표 - 연합뉴스
https://www.yna.co.kr/view/AKR001

2026. 7. 16.(목) 인천광역시교육청 주요 언론보도 현황입니다.

■ 인천교육청, 조식 지원 확대 - 인천일보
https://www.incheonilbo.com/news/articleView.html?idxno=1
"""

    def test_splits_two_list_types(self):
        batches = newsdb.parse_paste(self.SAMPLE)
        self.assertEqual(len(batches), 2)
        (d1, t1, a1), (d2, t2, a2) = batches
        self.assertEqual(d1, "2026-07-16")
        self.assertEqual(t1, "전국·교육부")
        self.assertEqual(a1[0]["publisher"], "연합뉴스")
        self.assertEqual(t2, "인천교육")
        self.assertEqual(a2[0]["title"], "인천교육청, 조식 지원 확대")


class TestMatchInterests(unittest.TestCase):
    def test_returns_matched_keywords_only(self):
        keywords = ["학생교육원", "학생자치", "AI교육"]
        self.assertEqual(
            newsdb.match_interests("인천광역시교육청학생교육원, 학생자치 리더십 캠프", keywords),
            ["학생교육원", "학생자치"],
        )
        self.assertEqual(newsdb.match_interests("무관한 제목", keywords), [])


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        self.conn = newsdb.open_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    ARTS = [
        {"title": "기사 A", "publisher": "경인일보", "original_url": "https://a.kr/1?utm_source=x"},
        {"title": "기사 B", "publisher": "기호일보", "original_url": "https://a.kr/2"},
    ]

    def test_ingest_inserts_and_dedups_by_clean_url(self):
        new1, dup1 = newsdb.ingest_articles(self.conn, "2026-07-16", "인천교육", self.ARTS,
                                            source_kind="test", raw_text="raw")
        self.assertEqual((new1, dup1), (2, 0))
        # 같은 기사(추적 파라미터만 다른 URL) 재인제스트 → 중복 처리
        again = [{"title": "기사 A", "publisher": "경인일보", "original_url": "https://a.kr/1?ref=naver"}]
        new2, dup2 = newsdb.ingest_articles(self.conn, "2026-07-16", "인천교육", again,
                                            source_kind="test", raw_text="raw")
        self.assertEqual((new2, dup2), (0, 1))

    def test_raw_text_preserved_in_source_batches(self):
        newsdb.ingest_articles(self.conn, "2026-07-16", "인천교육", self.ARTS,
                               source_kind="test", raw_text="원본 그대로")
        row = self.conn.execute("SELECT raw_text, article_count FROM source_batches").fetchone()
        self.assertEqual(row[0], "원본 그대로")
        self.assertEqual(row[1], 2)

    def test_mark_and_search_roundtrip(self):
        newsdb.ingest_articles(self.conn, "2026-07-16", "인천교육", self.ARTS,
                               source_kind="test", raw_text="raw")
        aid = self.conn.execute("SELECT article_id FROM articles WHERE title='기사 A'").fetchone()[0]
        newsdb.mark_article(self.conn, aid, favorite=True, memo="계획 참고", read_status="read")

        rows = newsdb.search_articles(self.conn, favorite=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "기사 A")
        self.assertEqual(rows[0]["memo"], "계획 참고")
        self.assertEqual(rows[0]["read_status"], "read")

        rows = newsdb.search_articles(self.conn, q="기호")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["publisher"], "기호일보")

    def test_search_annotates_interest_hits(self):
        newsdb.add_interest(self.conn, "아침 급식")
        arts = [{"title": "인천 아침 급식 확대", "publisher": "경인일보", "original_url": "https://a.kr/9"}]
        newsdb.ingest_articles(self.conn, "2026-07-16", "인천교육", arts, source_kind="test", raw_text="r")
        rows = newsdb.search_articles(self.conn, q="급식")
        self.assertEqual(rows[0]["interest_hits"], ["아침 급식"])

    def test_interest_only_filters_before_limit(self):
        # 관심 일치 기사가 입력 순서상 limit 밖에 있어도 interest_only 검색에 잡혀야 한다
        newsdb.add_interest(self.conn, "학생교육원")
        arts = [{"title": f"무관 기사 {i}", "publisher": "매체", "original_url": f"https://a.kr/x{i}"}
                for i in range(5)]
        arts.append({"title": "학생교육원 리더십 캠프", "publisher": "매체", "original_url": "https://a.kr/hit"})
        newsdb.ingest_articles(self.conn, "2026-07-16", "인천교육", arts, source_kind="test", raw_text="r")
        rows = newsdb.search_articles(self.conn, interest_only=True, limit=3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "학생교육원 리더십 캠프")

    def test_export_md_contains_share_format(self):
        newsdb.ingest_articles(self.conn, "2026-07-16", "인천교육", self.ARTS,
                               source_kind="test", raw_text="raw")
        md = newsdb.export_md(self.conn, batch_date="2026-07-16")
        self.assertIn("■ 기사 A - 경인일보", md)
        self.assertIn("https://a.kr/1", md)
        self.assertIn("2026-07-16", md)


class TestParseCollectedJson(unittest.TestCase):
    def test_maps_collector_fields(self):
        payload = {
            "window_start": "2026-07-15 09:00",
            "window_end": "2026-07-16 09:00",
            "engine": "google-news-rss",
            "article_count": 1,
            "articles": [{
                "title": "기사 C",
                "publisher": "웹이코노미",
                "publisher_domain": "www.webeconomy.co.kr",
                "google_url": "https://news.google.com/rss/articles/xxx",
                "original_url": "https://www.webeconomy.co.kr/news/articleView.html?idxno=1",
                "url_status": "복원",
                "published_at_kst": "2026-07-15 10:00",
                "queries": ["Q1:인천교육청"],
                "engine": "google-news-rss",
                "relevance_hint": "likely_relevant",
                "relevance_reasons": ["incheon_education_office", "education_subject"],
                "location_hits": ["인천"],
                "education_subject_hits": ["학생"],
                "student_story_hits": ["수상"],
                "negative_context_hits": [],
            }],
        }
        batch_date, list_type, articles = newsdb.parse_collected_json(json.dumps(payload, ensure_ascii=False))
        self.assertEqual(batch_date, "2026-07-16")  # window_end 날짜 = 보고일
        self.assertEqual(list_type, "인천교육")
        self.assertEqual(articles[0]["publisher"], "웹이코노미")
        self.assertEqual(articles[0]["published_at"], "2026-07-15 10:00")
        self.assertEqual(articles[0]["engine"], "google-news-rss")
        self.assertEqual(articles[0]["relevance_hint"], "likely_relevant")
        self.assertEqual(articles[0]["location_hits"], ["인천"])


    def test_relevance_metadata_roundtrip(self):
        payload = {
            "window_end": "2026-07-24 05:00",
            "articles": [{
                "title": "남동구 중학생 3명, 시민 구조",
                "publisher": "검증매체",
                "original_url": "https://a.kr/student",
                "relevance_hint": "likely_relevant",
                "publication_eligible": False,
                "relevance_reasons": ["incheon_location", "education_subject"],
                "location_hits": ["남동구"],
                "education_subject_hits": ["중학생"],
                "student_story_hits": ["구조"],
                "negative_context_hits": [],
            }],
        }
        batch_date, list_type, articles = newsdb.parse_collected_json(
            json.dumps(payload, ensure_ascii=False)
        )
        with tempfile.TemporaryDirectory() as tmp:
            conn = newsdb.open_db(str(Path(tmp) / "metadata.db"))
            newsdb.ingest_articles(
                conn, batch_date, list_type, articles,
                source_kind="collector-json", raw_text="raw"
            )
            row = newsdb.search_articles(conn, q="중학생")[0]
            self.assertEqual(row["relevance_hint"], "likely_relevant")
            self.assertEqual(row["publication_eligible"], 0)
            self.assertEqual(json.loads(row["location_hits"]), ["남동구"])
            self.assertEqual(json.loads(row["student_story_hits"]), ["구조"])
            conn.close()


    def test_candidate_publication_uses_aug20_relevance_allowlist(self):
        self.assertTrue(newsdb.is_publishable_for_digest({
            "source_kind": "collector-json",
            "relevance_hint": "likely_relevant",
            "publication_eligible": False,
        }))
        self.assertTrue(newsdb.is_publishable_for_digest({
            "source_kind": "collector-json",
            "relevance_hint": "needs_review",
            "publication_eligible": False,
        }))
        self.assertFalse(newsdb.is_publishable_for_digest({
            "source_kind": "collector-json",
            "relevance_hint": "likely_irrelevant",
            "publication_eligible": True,
            "body_status": "본문 검증 완료",
            "publication_verification_basis": "본문에서 확인한 구체적 기관·사실 근거",
            "publication_verified_at": "2026-08-20T00:00:00+00:00",
        }))
        self.assertTrue(newsdb.is_publishable_for_digest({
            "source_kind": "briefing-md",
            "relevance_hint": "likely_irrelevant",
            "publication_eligible": False,
        }))

    def test_verified_candidate_persists_body_evidence_metadata(self):
        payload = {
            "window_end": "2026-08-20 05:00",
            "articles": [{
                "title": "인천교육청, 학교 안전 협력 확인",
                "publisher": "검증매체",
                "original_url": "https://a.kr/verified",
                "relevance_hint": "needs_review",
                "publication_eligible": True,
                "body_status": "본문 검증 완료",
                "publication_verification_basis": "본문에 인천교육청과 학교 안전 협력이 명시됨",
                "publication_verified_at": "2026-08-20T00:00:00+00:00",
            }],
        }
        batch_date, list_type, articles = newsdb.parse_collected_json(
            json.dumps(payload, ensure_ascii=False)
        )
        with tempfile.TemporaryDirectory() as tmp:
            conn = newsdb.open_db(str(Path(tmp) / "verified.db"))
            newsdb.ingest_articles(
                conn, batch_date, list_type, articles,
                source_kind="collector-json", raw_text="verified"
            )
            row = newsdb.search_articles(conn, date=batch_date, selected=True)[0]
            self.assertEqual(row["publication_eligible"], 1)
            self.assertEqual(row["body_status"], "본문 검증 완료")
            self.assertIn("학교 안전 협력", row["publication_verification_basis"])
            self.assertEqual(
                row["publication_verified_at"], "2026-08-20T00:00:00+00:00"
            )
            conn.close()

    def test_candidate_without_allowlisted_relevance_is_not_selected_or_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = newsdb.open_db(str(Path(tmp) / "unverified.db"))
            newsdb.ingest_articles(
                conn,
                "2026-08-20",
                "인천교육",
                [{
                    "title": "인천교육청 검증 없는 후보",
                    "publisher": "후보매체",
                    "original_url": "https://a.kr/unverified",
                    "publication_eligible": True,
                    "body_status": "본문 미검증",
                    "publication_verification_basis": "",
                    "publication_verified_at": "",
                }],
                source_kind="collector-json",
                raw_text="unverified",
            )
            self.assertEqual(
                newsdb.search_articles(conn, date="2026-08-20", selected=True), []
            )
            self.assertEqual(
                newsdb.build_digest_data(conn, "2026-08-20")["meta"]["total"], 0
            )
            self.assertEqual(
                conn.execute("SELECT publication_eligible FROM articles").fetchone()[0],
                1,
            )
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
