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


VERIFIED_METADATA = {
    "body_status": "본문 검증 완료",
    "publication_verification_basis": "본문에서 확인한 구체적 기관·사실 근거",
    "publication_verified_at": "2026-08-20T00:00:00+00:00",
}


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

    def test_latest_page_has_previous_current_next_navigation(self):
        digest = '<html><head><style></style></head><body><main class="wrap"></main></body></html>'
        dates = ["2026-07-21", "2026-07-20", "2026-07-16"]
        html = publish_site.render_issue_page(digest, "2026-07-21", dates, location="root")
        self.assertIn('class="issue-nav"', html)
        self.assertIn('href="archive/2026-07-20.html"', html)
        self.assertIn("◀ 지난 호 (7. 20.)", html)
        self.assertIn("2026. 7. 21. (화)", html)
        self.assertIn("다음 호 예정 · 매일 09:00", html)
        self.assertIn('href="archive/index.html"', html)
        self.assertIn("전체 지난 호", html)

    def test_archive_page_links_to_older_and_newer_issue(self):
        digest = '<html><head><style></style></head><body><main class="wrap"></main></body></html>'
        dates = ["2026-07-21", "2026-07-20", "2026-07-16"]
        html = publish_site.render_issue_page(digest, "2026-07-20", dates, location="archive")
        self.assertIn('href="2026-07-16.html"', html)
        self.assertIn("◀ 지난 호 (7. 16.)", html)
        self.assertIn('href="../index.html"', html)
        self.assertIn("다음 호 (7. 21.) ▶", html)
        self.assertIn('href="index.html"', html)

    def test_oldest_archive_page_disables_previous_issue(self):
        digest = '<html><head><style></style></head><body><main class="wrap"></main></body></html>'
        dates = ["2026-07-21", "2026-07-20", "2026-07-16"]
        html = publish_site.render_issue_page(digest, "2026-07-16", dates, location="archive")
        self.assertIn('<span class="nav-btn nav-prev disabled">◀ 지난 호</span>', html)
        self.assertIn('href="2026-07-20.html"', html)

    def test_issue_navigation_is_idempotent(self):
        digest = '<html><head><style></style></head><body><main class="wrap"></main></body></html>'
        dates = ["2026-07-21", "2026-07-20"]
        once = publish_site.render_issue_page(digest, "2026-07-21", dates, location="root")
        twice = publish_site.render_issue_page(once, "2026-07-21", dates, location="root")
        self.assertEqual(twice.count('class="issue-nav"'), 1)
        self.assertEqual(twice.count("EDU_NEWS_SITE_NAV_START"), 2)  # CSS 1 + HTML 1

    def test_latest_page_requires_expected_digest_structure(self):
        with self.assertRaises(ValueError):
            publish_site.render_issue_page(
                "<html><body></body></html>", "2026-07-21", ["2026-07-21"], location="root"
            )

    def test_build_refreshes_navigation_on_all_archived_pages(self):
        digest = '<html><head><style></style></head><body><main class="wrap"></main></body></html>'
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            archive = site / "archive"
            archive.mkdir()
            for date in ("2026-07-21", "2026-07-20", "2026-07-16"):
                (archive / f"{date}.html").write_text(digest, encoding="utf-8")

            publish_site.build(site)

            latest = (site / "index.html").read_text(encoding="utf-8")
            middle = (archive / "2026-07-20.html").read_text(encoding="utf-8")
            oldest = (archive / "2026-07-16.html").read_text(encoding="utf-8")
            self.assertIn('href="archive/2026-07-20.html"', latest)
            self.assertIn('href="../index.html"', middle)
            self.assertIn('href="2026-07-20.html"', oldest)

    def test_archive_index_escapes(self):
        html = publish_site.render_archive_index(["2026-07-16"])
        self.assertNotIn("<script>alert", html)


class TestPublicationContract(unittest.TestCase):
    @staticmethod
    def _sparse_public_data(section: str) -> dict:
        article = {
            "title": "인천교육 정책 안내",
            "publisher": "예시매체",
            "original_url": "https://example.com/article",
        }
        data = {
            "batch_date": "2026-08-12",
            "meta": {
                "total": 1 if section != "empty" else 0,
                "issue_count": 1 if section == "hero" else 0,
                "interest_count": 0,
                "publisher_count": 1 if section != "empty" else 0,
                "other_count": 1 if section == "other" else 0,
            },
            "hero_issues": [],
            "interest_articles": [],
            "all_by_type": [],
            "other_issues": [],
            "other_articles": [],
        }
        if section == "hero":
            data["hero_issues"] = [{
                "title": article["title"],
                "article_type": "정책·현안",
                "fields": ["교육행정"],
                "member_count": 2,
                "summary": "",
                "representative": article,
                "others": [],
            }]
        elif section == "single":
            data["all_by_type"] = [("정책·현안", [article])]
        elif section == "other":
            data["other_articles"] = [article]
        return data

    def test_locked_contract_records_approved_structure_and_order(self):
        contract = publish_site.load_publication_contract()
        self.assertTrue(contract["locked"])
        self.assertEqual(
            contract["ingestion"]["order"], ["briefing-md", "collector-json"]
        )
        self.assertEqual(
            frozenset(contract["ingestion"]["publish_candidate_statuses"]),
            newsdb.PUBLISHABLE_CANDIDATE_STATUSES,
        )
        self.assertEqual(
            contract["ingestion"]["publish_candidate_requires"],
            [
                "publication_eligible",
                "body_status",
                "publication_verification_basis",
                "publication_verified_at",
            ],
        )
        self.assertEqual(
            contract["layout"]["sections_in_order"],
            ["오늘의 핵심 이슈", "전체 기사", "타 시도·일반 교육 동향"],
        )
        self.assertEqual(
            contract["layout"]["section_presence_policy"],
            "render_when_non_empty",
        )
        self.assertEqual(contract["article_type_order"], newsdb.TYPE_ORDER)
        self.assertEqual(
            contract["ingestion"]["release_required_checks"],
            ["briefing-vs-published-content-parity"],
        )

    def test_validator_accepts_current_approved_public_shape(self):
        contract = publish_site.load_publication_contract()
        sections = "".join(
            f'<section><h2>{name}</h2></section>'
            for name in contract["layout"]["sections_in_order"]
        )
        css = "\n".join(contract["layout"]["required_css_tokens"])
        html = (
            f'<html><head><style>{css}</style></head><body>'
            f'{contract["public_safety"]["required_marker"]}<main>{sections}</main></body></html>'
        )
        publish_site.validate_publication_contract(html, contract)

    def test_validator_accepts_real_sparse_public_renders(self):
        contract = publish_site.load_publication_contract()
        for section in ("empty", "hero", "single", "other"):
            with self.subTest(section=section):
                html = newsdb.render_digest_html(
                    self._sparse_public_data(section), public=True
                )
                publish_site.validate_publication_contract(html, contract)

    def test_public_briefing_singletons_keep_approved_issue_card_shell(self):
        data = self._sparse_public_data("single")
        data["all_by_type"][0][1][0]["source_kind"] = "briefing-md"
        html = newsdb.render_digest_html(data, public=True)
        self.assertIn("오늘의 핵심 이슈", html)
        self.assertIn('class="hero-card"', html)
        self.assertNotIn("전체 기사", html)

    def test_public_briefing_singletons_cap_hero_cards_and_keep_type_sections(self):
        data = self._sparse_public_data("empty")
        items = []
        for i in range(13):
            items.append({
                "article_id": 100 + i,
                "title": f"브리핑 기사 {i:02d}",
                "publisher": "예시매체",
                "original_url": f"https://example.com/b{i}",
                "source_kind": "briefing-md",
                "article_type": "행사·모집",
                # 입력 순서를 거꾸로 주어 정렬이 제목·목록 순서가 아니라 입력 순서를 따르는지 확인
                "selection_input_order": 13 - i,
            })
        data["all_by_type"] = [("행사·모집", items)]
        data["meta"]["total"] = 13
        public = newsdb._public_singleton_issue_fallback(data)
        self.assertEqual(len(public["hero_issues"]), newsdb.PUBLIC_HERO_SINGLETON_LIMIT)
        self.assertEqual(
            [g["representative"]["selection_input_order"] for g in public["hero_issues"]],
            list(range(1, 11)),
        )
        remaining = [a["selection_input_order"] for _, rows in public["all_by_type"] for a in rows]
        self.assertEqual(remaining, [13, 12, 11])
        html = newsdb.render_digest_html(data, public=True)
        self.assertEqual(html.count('class="hero-card"'), 10)
        self.assertIn("전체 기사", html)

    def test_public_real_groups_take_hero_slots_first(self):
        data = self._sparse_public_data("hero")
        single = {
            "article_id": 7, "title": "브리핑 단독", "publisher": "예시매체",
            "original_url": "https://example.com/single", "source_kind": "briefing-md",
            "article_type": "기타", "selection_input_order": 1,
        }
        data["all_by_type"] = [("기타", [single])]
        public = newsdb._public_singleton_issue_fallback(data)
        self.assertEqual([g["member_count"] for g in public["hero_issues"]], [2, 1])
        self.assertEqual(public["all_by_type"], [])

    def test_validator_blocks_missing_or_reordered_sections(self):
        contract = publish_site.load_publication_contract()
        css = "\n".join(contract["layout"]["required_css_tokens"])
        html = (
            f'<html><style>{css}</style>{contract["public_safety"]["required_marker"]}'
            '<main><h2>전체 기사</h2><h2>오늘의 핵심 이슈</h2>'
            '<h2>타 시도·일반 교육 동향</h2></main></html>'
        )
        with self.assertRaisesRegex(ValueError, "섹션 순서"):
            publish_site.validate_publication_contract(html, contract)

    def test_briefing_parity_keeps_explicit_irrelevant_article(self):
        article = {
            "title": "인천시교육감 선거 식사 제공·불법 선거운동 드러나",
            "original_url": "https://www.heraldk.com/article/2026081300230428560",
        }
        publish_site.validate_briefing_published_content_parity(
            [article],
            '<a href="https://www.heraldk.com/article/2026081300230428560">기사</a>',
        )

    def test_briefing_parity_uses_title_fallback_for_grouped_url(self):
        article = {
            "title": "인천교육감 선거 식사 제공·불법 선거운동 드러나",
            "original_url": "https://briefing.example/original",
        }
        publish_site.validate_briefing_published_content_parity(
            [article],
            '<a href="https://other.example/representative">인천교육감 선거 식사 제공 불법 선거운동</a>',
        )

    def test_briefing_parity_accepts_html_escaped_query_url(self):
        article = {
            "title": "AI",
            "original_url": "https://example.com/article?a=1&b=2",
        }
        publish_site.validate_briefing_published_content_parity(
            [article],
            '<a href="https://example.com/article?a=1&amp;b=2">AI</a>',
        )

    def test_briefing_parity_rejects_missing_article(self):
        with self.assertRaisesRegex(ValueError, "브리핑-공개 콘텐츠 정합성"):
            publish_site.validate_briefing_published_content_parity(
                [{"title": "누락된 브리핑 기사", "original_url": "https://missing.example/a"}],
                '<main>다른 기사</main>',
            )

    def test_build_runs_briefing_parity_against_generated_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "news.db"
            site = Path(tmp) / "site"
            conn = newsdb.open_db(str(db_path))
            briefing = [{
                "title": "인천시교육감 선거 식사 제공·불법 선거운동 드러나",
                "publisher": "헤럴드경제 미주판",
                "original_url": "https://www.heraldk.com/article/2026081300230428560",
            }]
            candidates = [{
                "title": "인천교육청, 별도 후보 기사",
                "publisher": "예시매체",
                "original_url": "https://example.com/candidate",
                "relevance_hint": "likely_relevant",
                "publication_eligible": True,
                **VERIFIED_METADATA,
            }]
            newsdb.ingest_articles(
                conn, "2026-08-13", "인천교육", briefing,
                source_kind="briefing-md", raw_text="briefing",
            )
            newsdb.ingest_articles(
                conn, "2026-08-13", "인천교육", candidates,
                source_kind="collector-json", raw_text="collector",
            )
            conn.close()

            with mock.patch(
                "publish_site.validate_briefing_published_content_parity",
                wraps=publish_site.validate_briefing_published_content_parity,
            ) as parity:
                publish_site.build(site, "2026-08-13", str(db_path))

            parity.assert_called_once()
            self.assertEqual(
                parity.call_args.args[0],
                [{
                    "title": briefing[0]["title"],
                    "original_url": briefing[0]["original_url"],
                }],
            )
            self.assertIn(
                briefing[0]["original_url"],
                (site / "archive" / "2026-08-13.html").read_text(encoding="utf-8"),
            )

    def test_build_rejects_missing_duplicate_group_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "news.db"
            site = Path(tmp) / "site"
            conn = newsdb.open_db(str(db_path))
            title = "인천교육청, 기초학력 담당자 역량 강화 연수 개최"
            newsdb.ingest_articles(
                conn, "2026-08-18", "인천교육",
                [{
                    "title": title,
                    "publisher": "브리핑매체",
                    "original_url": "https://example.com/briefing",
                }],
                source_kind="briefing-md", raw_text="briefing",
            )
            newsdb.ingest_articles(
                conn, "2026-08-18", "인천교육",
                [{
                    "title": title,
                    "publisher": "후보매체",
                    "original_url": "https://example.com/candidate",
                    "relevance_hint": "likely_relevant",
                    "publication_eligible": True,
                    **VERIFIED_METADATA,
                }],
                source_kind="collector-json", raw_text="collector",
            )
            conn.close()

            with self.assertRaisesRegex(ValueError, "동일보도 묶음"):
                publish_site.build(site, "2026-08-18", str(db_path))

            conn = newsdb.open_db(str(db_path))
            self.assertEqual(newsdb.build_groups(conn, "2026-08-18"), 1)
            conn.close()
            publish_site.build(site, "2026-08-18", str(db_path))
            html = (site / "archive" / "2026-08-18.html").read_text(encoding="utf-8")
            self.assertIn("오늘의 핵심 이슈", html)

    def test_ingestion_validator_blocks_candidate_allowlist_drift(self):
        contract = publish_site.load_publication_contract()
        contract["ingestion"]["publish_candidate_statuses"] = ["likely_relevant"]
        with self.assertRaisesRegex(ValueError, "allow-list"):
            publish_site.validate_ingestion_contract(None, "2026-08-12", contract)


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

    @mock.patch("publish_site.subprocess.run")
    def test_git_push_decodes_windows_git_output_as_utf8_safely(self, run):
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        publish_site.git_push(Path("site"), "2026-07-21")
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["encoding"], "utf-8")
            self.assertEqual(call.kwargs["errors"], "replace")


if __name__ == "__main__":
    unittest.main(verbosity=1)
