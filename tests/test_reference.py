import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import newsdb  # noqa: E402


class TestReferenceSection(unittest.TestCase):
    def test_reference_filter(self):
        ok = newsdb.is_general_education_reference
        self.assertTrue(ok({"title": "경북교육청, 우즈벡에 K-직업교육 수출", "article_type": "타 시도 동향"}))
        self.assertTrue(ok({"title": "교육부, 학생 마음건강 자문단 위촉", "article_type": "기타"}))
        self.assertFalse(ok({"title": "인천교육청, 고교학점제 지원 프로그램 배포", "article_type": "사업·성과"}))
        self.assertFalse(ok({"title": "북부교육지원청, 문화예술축제 성료 등", "article_type": "행사·모집"}))
        self.assertFalse(ok({"title": "새마을금고, 유튜브 조회수 1000만회 돌파", "article_type": "기타"}))
        self.assertFalse(ok({"title": "쿠팡 물류센터 학생 아르바이트 전액 보상", "article_type": "기타",
                             "negative_context_hits": '["쿠팡"]'}))
        self.assertFalse(ok({"title": "사라진 섬 학교의 역사…시도·모도분교 교적비 설치", "article_type": "기타"}))
        self.assertTrue(ok({"title": "시도교육감들, 교육재정 개편안 재검토 촉구", "article_type": "정책·현안"}))

    def _pool(self):
        return [
            {"title": "경북교육청, 우즈베키스탄에 K-직업교육 모델 확산 나선다", "publisher": "A",
             "original_url": "https://x.kr/1", "relevance_hint": "needs_review", "published_at": "2026-10-07 01:00"},
            {"title": "경북교육청, 우즈베키스탄에 K-직업교육 모델 확산 나선다", "publisher": "B",
             "original_url": "https://x.kr/2", "relevance_hint": "needs_review", "published_at": "2026-10-07 02:00"},
            {"title": "경북교육청 우즈베키스탄에 K-직업교육 모델 확산 본격화", "publisher": "C",
             "original_url": "https://x.kr/3", "relevance_hint": "needs_review", "published_at": "2026-10-07 03:00"},
            {"title": "경북교육청, 우즈벡에 ‘K-직업교육’ 수출…교육 체계 현지 적용 > 뉴스 | 안동신문", "publisher": "C2",
             "original_url": "https://x.kr/3b", "relevance_hint": "needs_review", "published_at": "2026-10-07 04:00"},
            {"title": "교육부, 학생 마음건강 자문단 위촉", "publisher": "D",
             "original_url": "https://x.kr/4", "relevance_hint": "likely_irrelevant"},
            {"title": "속초시, 학교폭력 예방 등굣길 캠페인", "publisher": "E",
             "original_url": "https://x.kr/5", "relevance_hint": "needs_review"},
            {"title": "인천 서해구, 학생 대상 행사", "publisher": "F",   # 인천 마커 → 참고 목록 제외
             "original_url": "https://x.kr/6", "relevance_hint": "likely_relevant"},
            {"title": "농심, 스낵 신제품 출시", "publisher": "G",         # 교육 주체 없음
             "original_url": "https://x.kr/7", "relevance_hint": "likely_irrelevant"},
        ]

    def test_reference_candidates_group_filter_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = newsdb.open_db(str(Path(tmp) / "ref.db"))
            newsdb.ingest_articles(conn, "2026-10-07", "인천교육", self._pool(),
                                   source_kind="collector-json", raw_text="pool")
            issues, singles = newsdb.build_reference_candidates(conn, "2026-10-07")
            self.assertEqual([(g["member_count"], g["representative"]["publisher"]) for g in issues], [(4, "A")])
            self.assertEqual([s["title"] for s in singles],
                             ["교육부, 학생 마음건강 자문단 위촉", "속초시, 학교폭력 예방 등굣길 캠페인"])
            conn.close()

    def test_reference_limit_and_digest_placement(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = newsdb.open_db(str(Path(tmp) / "ref2.db"))
            newsdb.ingest_articles(conn, "2026-10-07", "인천교육", self._pool(),
                                   source_kind="collector-json", raw_text="pool")
            old = newsdb.REFERENCE_SECTION_LIMIT
            try:
                newsdb.REFERENCE_SECTION_LIMIT = 2
                issues, singles = newsdb.build_reference_candidates(conn, "2026-10-07")
                self.assertEqual(len(issues) + len(singles), 2)
            finally:
                newsdb.REFERENCE_SECTION_LIMIT = old
            data = newsdb.build_digest_data(conn, "2026-10-07")
            self.assertEqual(data["hero_issues"], [])
            self.assertEqual(data["all_by_type"], [])
            self.assertEqual(len(data["other_issues"]), 1)
            self.assertEqual(len(data["other_articles"]), 2)
            self.assertEqual(data["meta"]["other_count"], 6)
            html = newsdb.render_digest_html(data, public=True)
            self.assertIn("타 시도·일반 교육 동향", html)
            self.assertIn("관련 4건", html)
            self.assertNotIn("농심", html)
            self.assertNotIn("서해구", html)
            conn.close()


if __name__ == "__main__":
    unittest.main()
