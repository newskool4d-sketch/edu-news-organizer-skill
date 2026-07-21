# -*- coding: utf-8 -*-
"""공개(웹) 모드 테스트 (TDD): 개인 데이터 미노출 + 비공식 표기 + 아카이브 인덱스."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import newsdb  # noqa: E402
import publish_site  # noqa: E402


class TestPublicMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = newsdb.open_db(str(Path(self.tmp.name) / "t.db"))
        newsdb.add_interest(self.conn, "학생교육원")
        arts = [{"title": "인천광역시교육청학생교육원, 리더십 캠프 운영",
                 "publisher": "웹이코노미", "original_url": "https://a.kr/1", "published_at": "2026-07-16 10:00"},
                {"title": "인천교육청, 조식 지원 확대", "publisher": "경인일보",
                 "original_url": "https://a.kr/2", "published_at": "2026-07-16 09:00"}]
        newsdb.ingest_articles(self.conn, "2026-07-16", "인천교육", arts, source_kind="briefing-md", raw_text="r")
        # 개인 메모 부여 (공개 모드에서 새어나가면 안 됨)
        aid = self.conn.execute("SELECT article_id FROM articles WHERE title LIKE '%리더십%'").fetchone()[0]
        newsdb.mark_article(self.conn, aid, memo="2027 캠프 계획 참고 - 대외비 메모")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_public_excludes_personal_data(self):
        data = newsdb.build_digest_data(self.conn, "2026-07-16")
        html = newsdb.render_digest_html(data, public=True)
        # 개인 메모·관심업무 섹션이 공개본에 없어야 함
        self.assertNotIn("대외비 메모", html)
        self.assertNotIn("내 관심업무", html)
        # 뉴스 기사(비개인)는 남아야 함
        self.assertIn("조식 지원 확대", html)

    def test_public_has_unofficial_marking(self):
        data = newsdb.build_digest_data(self.conn, "2026-07-16")
        html = newsdb.render_digest_html(data, public=True)
        self.assertIn("비공식", html)

    def test_private_still_includes_personal(self):
        data = newsdb.build_digest_data(self.conn, "2026-07-16")
        html = newsdb.render_digest_html(data, public=False)
        self.assertIn("내 관심업무", html)
        self.assertIn("대외비 메모", html)


class TestArchiveIndex(unittest.TestCase):
    def test_archive_index_lists_dates_desc(self):
        html = publish_site.render_archive_index(["2026-07-14", "2026-07-16", "2026-07-15"])
        self.assertTrue(html.lstrip().startswith("<!doctype html>"))
        # 최신순 정렬
        i16 = html.find("2026-07-16")
        i15 = html.find("2026-07-15")
        i14 = html.find("2026-07-14")
        self.assertTrue(i16 < i15 < i14)
        # archive/index.html 기준으로 날짜 파일과 최신 루트에 연결
        self.assertIn('href="2026-07-16.html"', html)
        self.assertNotIn('href="archive/2026-07-16.html"', html)
        self.assertIn('href="../index.html"', html)

    def test_latest_page_has_archive_navigation(self):
        digest = '<html><head><style></style></head><body><main class="wrap"></main></body></html>'
        html = publish_site.render_latest_page(digest)
        self.assertIn('class="site-nav"', html)
        self.assertIn('href="archive/index.html"', html)
        self.assertIn("지난 호 보기", html)

    def test_latest_page_requires_expected_digest_structure(self):
        with self.assertRaises(ValueError):
            publish_site.render_latest_page("<html><body></body></html>")

    def test_archive_index_escapes(self):
        html = publish_site.render_archive_index(["2026-07-16"])
        self.assertNotIn("<script>alert", html)


class TestGitPush(unittest.TestCase):
    @mock.patch("publish_site.subprocess.run")
    def test_git_push_uses_iso_date_in_commit_message(self, run):
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        publish_site.git_push(Path("site"), "2026-07-21")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[1], ["git", "commit", "-m", "다이제스트 갱신: 2026-07-21"])
        self.assertEqual(commands[2], ["git", "push"])

    @mock.patch("publish_site.subprocess.run")
    def test_git_push_surfaces_command_failure(self, run):
        run.return_value = mock.Mock(returncode=1, stdout="", stderr="authentication failed")
        with self.assertRaisesRegex(RuntimeError, "authentication failed"):
            publish_site.git_push(Path("site"), "2026-07-21")


if __name__ == "__main__":
    unittest.main(verbosity=1)
