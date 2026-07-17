# -*- coding: utf-8 -*-
"""2단계 스마트 정리 테스트 (TDD: 구현 전 작성) — 동일보도 묶기·분류·selected 필터."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import newsdb  # noqa: E402


class TestNormalizeAndSimilarity(unittest.TestCase):
    def test_normalize_strips_quotes_punct_spaces(self):
        a = newsdb.normalize_title("인천시교육청 학생교육원, '리더십 캠프' 운영!")
        b = newsdb.normalize_title("인천시교육청학생교육원 리더십 캠프 운영")
        self.assertEqual(a, b)

    def test_same_story_titles_are_similar(self):
        t1 = "인천광역시교육청학생교육원, 학생자치 역량 키우는 리더십 캠프 운영"
        t2 = "학생교육원, 학생자치 역량 키우는 '리더십 캠프' 운영 등"
        self.assertGreaterEqual(newsdb.title_similarity(t1, t2), newsdb.GROUP_THRESHOLD)

    def test_different_story_titles_are_not_similar(self):
        t1 = "인천교육청, 특수교사 마음건강 지원 명상 프로그램 운영"
        t2 = "인천시의회 교육위, 교육청 직속기관 예산 편성 적정성 지적"
        self.assertLess(newsdb.title_similarity(t1, t2), newsdb.GROUP_THRESHOLD)


class TestClassify(unittest.TestCase):
    def test_article_type_rules(self):
        cases = [
            ("인천시의회 교육위, 교육청 직속기관 예산 편성 적정성 지적", "비판·점검"),
            ("[인터뷰] 도성훈 인천시교육감 \"읽걷쓰 AI로 학생성공시대 완성\"", "인터뷰·기획"),
            ("도성훈 인천교육감, 국회서 무고성 아동학대 신고 관련 법 개정 촉구", "비판·점검"),
            ("인천교육청, 주민직선 5기 공약 실천계획 발표", "정책·현안"),
            ("인천신트리도서관, 여름방학 어린이 체험프로그램 참가자 모집", "행사·모집"),
            ("월드비전·우리금융·인천교육청, 결식 우려 초등생 조식 지원 협약", "사업·성과"),
            ("안민석 경기도교육감 \"폰OFF, RAS ON\" 청담고서 확인한 자율의 힘", "타 시도 동향"),
        ]
        for title, expected in cases:
            self.assertEqual(newsdb.classify_type(title), expected, msg=title)

    def test_edu_fields_multi_tag(self):
        fields = newsdb.classify_fields("인천광역시교육청학생교육원, 학생자치 역량 키우는 리더십 캠프 운영")
        self.assertIn("체험교육·학생자치", fields)
        fields = newsdb.classify_fields("인천교육청, 특수교육대상학생 위한 AI 직업교육 운영")
        self.assertIn("특수교육", fields)
        self.assertIn("AI·디지털교육", fields)
        self.assertIn("진로·직업교육", fields)
        self.assertEqual(newsdb.classify_fields("완전히 무관한 제목"), ["기타"])


class TestGrouping(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = newsdb.open_db(str(Path(self.tmp.name) / "t.db"))
        arts = [
            {"title": "인천광역시교육청학생교육원, 학생자치 역량 키우는 리더십 캠프 운영",
             "publisher": "웹이코노미", "original_url": "https://a.kr/1", "published_at": "2026-07-15 10:00"},
            {"title": "학생교육원, 학생자치 역량 키우는 '리더십 캠프' 운영 등",
             "publisher": "사회적경제뉴스", "original_url": "https://a.kr/2", "published_at": "2026-07-15 09:00"},
            {"title": "인천시의회 교육위, 교육청 직속기관 예산 편성 적정성 지적",
             "publisher": "중도일보", "original_url": "https://a.kr/3", "published_at": "2026-07-15 11:00"},
        ]
        newsdb.ingest_articles(self.conn, "2026-07-15", "인천교육", arts, source_kind="test", raw_text="r")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_build_groups_creates_one_group_for_same_story(self):
        made = newsdb.build_groups(self.conn, "2026-07-15")
        self.assertEqual(made, 1)  # 2건 묶음 1개, 단독 기사는 그룹 없음
        rows = self.conn.execute(
            "SELECT g.group_id, g.representative_article_id, COUNT(i.article_id) c "
            "FROM article_groups g JOIN article_group_items i ON i.group_id = g.group_id "
            "GROUP BY g.group_id").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["c"], 2)
        # 대표기사 = 발행시각 빠른 기사 (사회적경제뉴스 09:00)
        rep_title = self.conn.execute("SELECT title FROM articles WHERE article_id = ?",
                                      (rows[0]["representative_article_id"],)).fetchone()[0]
        self.assertIn("사회적", self.conn.execute(
            "SELECT publisher FROM articles WHERE article_id = ?",
            (rows[0]["representative_article_id"],)).fetchone()[0])
        self.assertTrue(rep_title)

    def test_build_groups_is_idempotent(self):
        newsdb.build_groups(self.conn, "2026-07-15")
        newsdb.build_groups(self.conn, "2026-07-15")  # 재실행 시 중복 그룹 금지
        n = self.conn.execute("SELECT COUNT(*) FROM article_groups").fetchone()[0]
        self.assertEqual(n, 1)


class TestSelectedFilter(unittest.TestCase):
    def test_selected_filter_uses_briefing_source(self):
        tmp = tempfile.TemporaryDirectory()
        conn = newsdb.open_db(str(Path(tmp.name) / "t.db"))
        sel = [{"title": "선별 기사", "publisher": "매체", "original_url": "https://a.kr/s1"}]
        pool = [{"title": "선별 기사", "publisher": "매체", "original_url": "https://a.kr/s1"},
                {"title": "풀 전용 기사", "publisher": "매체", "original_url": "https://a.kr/p1"}]
        newsdb.ingest_articles(conn, "2026-07-16", "인천교육", sel, source_kind="briefing-md", raw_text="r")
        newsdb.ingest_articles(conn, "2026-07-16", "인천교육", pool, source_kind="collector-json", raw_text="r")
        rows = newsdb.search_articles(conn, selected=True)
        self.assertEqual([r["title"] for r in rows], ["선별 기사"])
        conn.close()
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=1)
