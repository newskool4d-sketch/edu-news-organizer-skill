# -*- coding: utf-8 -*-
"""HTML 다이제스트 테스트 (TDD: 구현 전 작성) — 구조화 데이터 + 렌더링·XSS."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import newsdb  # noqa: E402


class TestDigestData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = newsdb.open_db(str(Path(self.tmp.name) / "t.db"))
        newsdb.add_interest(self.conn, "학생교육원")
        arts = [
            {"title": "인천광역시교육청학생교육원, 학생자치 리더십 캠프 운영",
             "publisher": "웹이코노미", "original_url": "https://a.kr/1", "published_at": "2026-07-16 10:00"},
            {"title": "학생교육원, 학생자치 리더십 캠프 운영 등",
             "publisher": "사회적경제뉴스", "original_url": "https://a.kr/2", "published_at": "2026-07-16 09:00"},
            {"title": "인천시의회 교육위, 직속기관 예산 편성 지적",
             "publisher": "중도일보", "original_url": "https://a.kr/3", "published_at": "2026-07-16 11:00"},
        ]
        newsdb.ingest_articles(self.conn, "2026-07-16", "인천교육", arts, source_kind="briefing-md", raw_text="r")
        newsdb.build_groups(self.conn, "2026-07-16")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_build_digest_data_shape(self):
        data = newsdb.build_digest_data(self.conn, "2026-07-16")
        self.assertEqual(data["batch_date"], "2026-07-16")
        self.assertEqual(data["meta"]["total"], 3)
        self.assertGreaterEqual(data["meta"]["issue_count"], 1)
        self.assertEqual(data["meta"]["interest_count"], 2)  # 리더십 캠프 2건 모두 '학생교육원' 포함
        # 핵심 이슈: 리더십 캠프 묶음(대표=09:00 사회적경제뉴스), 관련 2건
        hero = data["hero_issues"][0]
        self.assertEqual(hero["member_count"], 2)
        self.assertEqual(hero["representative"]["publisher"], "사회적경제뉴스")
        self.assertIn("article_type", hero)
        # 관심 기사에 학생교육원 태그
        self.assertTrue(any("학생교육원" in a["interest_hits"] for a in data["interest_articles"]))

    def test_digest_md_uses_shared_data(self):
        md = newsdb.digest_md(self.conn, "2026-07-16")
        self.assertIn("오늘의 핵심 이슈", md)
        self.assertIn("리더십 캠프", md)


class TestRenderHtml(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = newsdb.open_db(str(Path(self.tmp.name) / "t.db"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_html_is_self_contained_and_has_content(self):
        arts = [{"title": "인천교육청, 조식 지원 확대 발표", "publisher": "경인일보",
                 "original_url": "https://a.kr/x", "published_at": "2026-07-16 10:00"}]
        newsdb.ingest_articles(self.conn, "2026-07-16", "인천교육", arts, source_kind="briefing-md", raw_text="r")
        data = newsdb.build_digest_data(self.conn, "2026-07-16")
        html = newsdb.render_digest_html(data)
        self.assertTrue(html.lstrip().startswith("<!doctype html>"))
        self.assertIn("인천교육청, 조식 지원 확대 발표", html)
        self.assertIn("2026-07-16", html)
        self.assertIn("2026. 7. 16. (목) 05:00 수집 기준", html)
        self.assertIn("<style>", html)
        # 외부 리소스 없음(자기완결)
        self.assertNotIn("http-equiv=\"refresh\"", html)
        self.assertNotIn("<script src=", html)

    def test_html_escapes_malicious_title(self):
        arts = [{"title": "<img src=x onerror=alert(1)> 악성 제목",
                 "publisher": "매체", "original_url": "https://a.kr/x", "published_at": "2026-07-16 10:00"}]
        newsdb.ingest_articles(self.conn, "2026-07-16", "인천교육", arts, source_kind="briefing-md", raw_text="r")
        data = newsdb.build_digest_data(self.conn, "2026-07-16")
        html = newsdb.render_digest_html(data)
        self.assertNotIn("<img src=x onerror=alert(1)>", html)
        self.assertIn("&lt;img", html)

    def test_html_only_links_http_urls(self):
        arts = [{"title": "정상", "publisher": "매체", "original_url": "javascript:alert(1)",
                 "published_at": "2026-07-16 10:00"}]
        newsdb.ingest_articles(self.conn, "2026-07-16", "인천교육", arts, source_kind="briefing-md", raw_text="r")
        data = newsdb.build_digest_data(self.conn, "2026-07-16")
        html = newsdb.render_digest_html(data)
        self.assertNotIn('href="javascript:', html)


if __name__ == "__main__":
    unittest.main(verbosity=1)
