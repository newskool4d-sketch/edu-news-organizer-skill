import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import digest_html  # noqa: E402


class TestDisplayTitle(unittest.TestCase):
    def test_strips_outlet_tails(self):
        f = digest_html.display_title
        self.assertEqual(f("부평구 청소년수련관, 10월 진로·체험 프로그램 > 뉴스", "더코리아"),
                         "부평구 청소년수련관, 10월 진로·체험 프로그램")
        self.assertEqual(f("\"우즈벡이 먼저 찾았다\"…경북교육청, K-직업교육 수출 > 뉴스 | 대구경북일보", "대구경북일보"),
                         "\"우즈벡이 먼저 찾았다\"…경북교육청, K-직업교육 수출")
        self.assertEqual(f("경인여대 외국인 유학생, 송편 빚으며 추석문화 체험 - 머니투데이", "머니투데이"),
                         "경인여대 외국인 유학생, 송편 빚으며 추석문화 체험")
        self.assertEqual(f("울산교육청, 전 학교 학부모회장 대상 원탁토론회 열어:경찰연합신문", "koreapolicenews.com"),
                         "울산교육청, 전 학교 학부모회장 대상 원탁토론회 열어")
        self.assertEqual(f("…통합 대응체계 마련 및 긴급 구제 촉구:동아경제신문 & daenews.co.kr", "동아경제"),
                         "…통합 대응체계 마련 및 긴급 구제 촉구")

    def test_keeps_meaningful_hyphens_and_colons(self):
        f = digest_html.display_title
        self.assertEqual(f("인천시교육청-남동구, 약산초 안전 통학로 완공", "경기일보"),
                         "인천시교육청-남동구, 약산초 안전 통학로 완공")
        self.assertEqual(f("교동도 망향대에서 열린 '이산가족의 날' - 실향민 1~4세대 한 자리에", "인천일보"),
                         "교동도 망향대에서 열린 '이산가족의 날' - 실향민 1~4세대 한 자리에")
        self.assertEqual(f("인천교육청, 2027년 기초학력 전문교원 확대…'중학교까지 책임교육'", "인천일보"),
                         "인천교육청, 2027년 기초학력 전문교원 확대…'중학교까지 책임교육'")
        self.assertEqual(f("", "x"), "")

    def test_render_uses_display_title_but_keeps_url(self):
        article = {"title": "인천교육 정책 안내 > 뉴스", "publisher": "더코리아",
                   "original_url": "https://example.com/a?x=1"}
        data = {
            "batch_date": "2026-10-07",
            "meta": {"total": 1, "issue_count": 1, "interest_count": 0, "publisher_count": 1, "other_count": 0},
            "hero_issues": [{"title": article["title"], "article_type": "정책·현안", "fields": ["교육행정"],
                             "member_count": 1, "summary": "", "representative": article, "others": []}],
            "interest_articles": [], "all_by_type": [("정책·현안", [article])],
            "other_issues": [], "other_articles": [article],
        }
        html = digest_html.render(data, public=True)
        self.assertNotIn("&gt; 뉴스", html)
        self.assertNotIn("> 뉴스", html)
        self.assertIn("인천교육 정책 안내</", html)
        self.assertIn("https://example.com/a?x=1", html.replace("&amp;", "&"))


if __name__ == "__main__":
    unittest.main()
