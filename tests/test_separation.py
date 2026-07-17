# -*- coding: utf-8 -*-
"""인천 vs 타시도·일반교육 분리 테스트 (TDD: 구현 전 작성)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import newsdb  # noqa: E402


class TestIsIncheon(unittest.TestCase):
    def test_incheon_markers(self):
        self.assertTrue(newsdb.is_incheon_title("인천시교육청, 조식 지원 확대"))
        self.assertTrue(newsdb.is_incheon_title("도성훈 교육감, 교육활동 보호 간담회"))
        self.assertTrue(newsdb.is_incheon_title("홍천군, 부평구 어린이 도시문화체험 참가자 모집"))
        self.assertTrue(newsdb.is_incheon_title("옹진군 학생 대상 AI 진로체험 운영"))

    def test_non_incheon(self):
        self.assertFalse(newsdb.is_incheon_title("제주 수학여행단 8만7000명 돌파"))
        self.assertFalse(newsdb.is_incheon_title("광주중앙도서관, 시민문화강좌 수강생 모집"))
        self.assertFalse(newsdb.is_incheon_title("서이초 3주기…교원 3단체 \"아동학대 법 개정하라\""))
        self.assertFalse(newsdb.is_incheon_title("[오늘의 금융지주] KB금융·우리금융·BNK금융"))


class TestDigestSeparation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = newsdb.open_db(str(Path(self.tmp.name) / "t.db"))
        arts = [
            # 인천 동일보도 묶음 (2건)
            {"title": "인천광역시교육청학생교육원, 학생자치 리더십 캠프 운영",
             "publisher": "웹이코노미", "original_url": "https://a.kr/1", "published_at": "2026-07-16 10:00"},
            {"title": "학생교육원, 학생자치 리더십 캠프 운영 등",
             "publisher": "사회적경제뉴스", "original_url": "https://a.kr/2", "published_at": "2026-07-16 09:00"},
            # 타시도 동일보도 묶음 (2건)
            {"title": "제주 안심수학여행 서비스, 전국 8만여명 이용",
             "publisher": "제주일보", "original_url": "https://a.kr/3", "published_at": "2026-07-16 11:00"},
            {"title": "제주 안심수학여행 서비스 호응…상반기 535개 학교 이용",
             "publisher": "한라일보", "original_url": "https://a.kr/4", "published_at": "2026-07-16 11:30"},
            # 인천 단독
            {"title": "인천교육청, 조식 지원 확대", "publisher": "경인일보",
             "original_url": "https://a.kr/5", "published_at": "2026-07-16 08:00"},
            # 일반교육(전국) 단독
            {"title": "교육부, 늘봄학교 전국 확대 발표", "publisher": "연합뉴스",
             "original_url": "https://a.kr/6", "published_at": "2026-07-16 08:30"},
        ]
        newsdb.ingest_articles(self.conn, "2026-07-16", "인천교육", arts, source_kind="briefing-md", raw_text="r")
        newsdb.build_groups(self.conn, "2026-07-16")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_hero_issues_only_incheon(self):
        data = newsdb.build_digest_data(self.conn, "2026-07-16")
        hero_titles = [g["title"] for g in data["hero_issues"]]
        self.assertTrue(any("리더십" in t for t in hero_titles))
        self.assertFalse(any("제주" in t for t in hero_titles))
        # 타시도 묶음은 other_issues로
        other_titles = [g["title"] for g in data["other_issues"]]
        self.assertTrue(any("제주" in t for t in other_titles))

    def test_singles_split(self):
        data = newsdb.build_digest_data(self.conn, "2026-07-16")
        incheon_singles = [r["title"] for _, items in data["all_by_type"] for r in items]
        self.assertIn("인천교육청, 조식 지원 확대", incheon_singles)
        self.assertNotIn("교육부, 늘봄학교 전국 확대 발표", incheon_singles)
        other_singles = [r["title"] for r in data["other_articles"]]
        self.assertIn("교육부, 늘봄학교 전국 확대 발표", other_singles)

    def test_html_has_separate_section_and_slogan(self):
        data = newsdb.build_digest_data(self.conn, "2026-07-16")
        html = newsdb.render_digest_html(data)
        self.assertIn("타 시도·일반 교육", html)
        self.assertIn("학생성공시대를 여는 인천교육", html)  # 슬로건 이미지 alt

    def test_md_has_separate_section(self):
        md = newsdb.digest_md(self.conn, "2026-07-16")
        self.assertIn("타 시도·일반 교육", md)
        # 타시도 기사가 인천 섹션(전체 기사)에 없어야
        all_section = md.split("타 시도·일반 교육")[0]
        self.assertNotIn("늘봄학교 전국 확대", all_section)


if __name__ == "__main__":
    unittest.main(verbosity=1)
