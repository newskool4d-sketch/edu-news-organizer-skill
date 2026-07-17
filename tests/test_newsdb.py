# -*- coding: utf-8 -*-
"""newsdb 핵심 동작 테스트 (TDD: 구현 전 작성, 표준 라이브러리 unittest)."""
import json
import sys
import tempfile
import unittest
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
            }],
        }
        batch_date, list_type, articles = newsdb.parse_collected_json(json.dumps(payload, ensure_ascii=False))
        self.assertEqual(batch_date, "2026-07-16")  # window_end 날짜 = 보고일
        self.assertEqual(list_type, "인천교육")
        self.assertEqual(articles[0]["publisher"], "웹이코노미")
        self.assertEqual(articles[0]["published_at"], "2026-07-15 10:00")
        self.assertEqual(articles[0]["engine"], "google-news-rss")


if __name__ == "__main__":
    unittest.main(verbosity=2)
