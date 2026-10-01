# -*- coding: utf-8 -*-
"""2단계 스마트 정리 테스트 (TDD: 구현 전 작성) — 동일보도 묶기·분류·selected 필터."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import newsdb  # noqa: E402


VERIFIED_METADATA = {
    "body_status": "본문 검증 완료",
    "publication_verification_basis": "본문에서 확인한 구체적 기관·사실 근거",
    "publication_verified_at": "2026-08-20T00:00:00+00:00",
}


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
            ("[인터뷰] 도성훈 인천시교육감 \"읽걷쓰 AI로 학생성공시대 완성\"", "교육감"),
            ("도성훈 인천교육감, 국회서 무고성 아동학대 신고 관련 법 개정 촉구", "교육감"),
            ("인천시의회, 교육감 공약 이행 미흡 지적", "비판·점검"),
            ("인천교육청, 주민직선 5기 공약 실천계획 발표", "정책·현안"),
            ("인천신트리도서관, 여름방학 어린이 체험프로그램 참가자 모집", "행사·모집"),
            ("월드비전·우리금융·인천교육청, 결식 우려 초등생 조식 지원 협약", "사업·성과"),
            ("안민석 경기도교육감 \"폰OFF, RAS ON\" 청담고서 확인한 자율의 힘", "타 시도 동향"),
        ]
        for title, expected in cases:
            self.assertEqual(newsdb.classify_type(title), expected, msg=title)

    def test_core_issue_type_order_puts_superintendent_first_and_criticism_last(self):
        core_order = [t for t in newsdb.TYPE_ORDER if t != "타 시도 동향"]
        self.assertEqual(core_order[0], "교육감")
        self.assertEqual(core_order[-1], "비판·점검")

    def test_edu_fields_multi_tag(self):
        fields = newsdb.classify_fields("인천광역시교육청학생교육원, 학생자치 역량 키우는 리더십 캠프 운영")
        self.assertIn("체험교육·학생자치", fields)
        fields = newsdb.classify_fields("인천교육청, 특수교육대상학생 위한 AI 직업교육 운영")
        self.assertIn("특수교육", fields)
        self.assertIn("AI·디지털교육", fields)
        self.assertIn("진로·직업교육", fields)
        self.assertEqual(newsdb.classify_fields("완전히 무관한 제목"), ["기타"])
        student_fields = newsdb.classify_fields("갑룡초 학생, 전국 발명대회 입상")
        self.assertIn("학교·학생활동", student_fields)


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
        newsdb.ingest_articles(
            self.conn, "2026-07-15", "인천교육", arts,
            source_kind="briefing-md", raw_text="r"
        )

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

    def test_build_groups_prefers_explicit_briefing_item_as_representative(self):
        tmp = tempfile.TemporaryDirectory()
        conn = newsdb.open_db(str(Path(tmp.name) / "briefing-priority.db"))
        briefing = [{
            "title": "인천교육청, 읽걷쓰AI 실천공동체 워크숍 운영…교사 300명 참여",
            "publisher": "인천in",
            "original_url": "https://briefing.example/article",
        }]
        candidates = [{
            "title": "인천광역시교육청, 읽걷쓰AI 교사 실천공동체 워크숍 개최",
            "publisher": "후보매체",
            "original_url": "https://candidate.example/article",
            "published_at": "2026-07-15 08:00",
            "relevance_hint": "likely_relevant",
            "publication_eligible": True,
            **VERIFIED_METADATA,
        }]
        newsdb.ingest_articles(
            conn, "2026-07-15", "인천교육", briefing,
            source_kind="briefing-md", raw_text="briefing"
        )
        newsdb.ingest_articles(
            conn, "2026-07-15", "인천교육", candidates,
            source_kind="collector-json", raw_text="candidates"
        )

        newsdb.build_groups(conn, "2026-07-15")
        groups = newsdb.list_groups(conn, "2026-07-15")
        representative = next(
            member for member in groups[0]["members"]
            if member["article_id"] == groups[0]["representative_article_id"]
        )
        self.assertEqual(
            representative["original_url"],
            "https://briefing.example/article",
        )
        conn.close()
        tmp.cleanup()

    def test_build_groups_keeps_distinct_briefing_events_in_separate_groups(self):
        tmp = tempfile.TemporaryDirectory()
        conn = newsdb.open_db(str(Path(tmp.name) / "briefing-boundary.db"))
        briefing = [
            {
                "title": "인천 신트리도서관, 하반기 평생학습프로그램 9개 강좌 운영",
                "publisher": "매일일보",
                "original_url": "https://briefing.example/sintri",
            },
            {
                "title": "인천 계양도서관, 유아부터 어르신까지 하반기 평생학습 프로그램 운영",
                "publisher": "한국강사신문",
                "original_url": "https://briefing.example/gyeyang",
            },
        ]
        candidates = [
            {
                "title": "인천광역시교육청신트리도서관, 2026년 하반기 평생학습프로그램 운영",
                "publisher": "후보매체1",
                "original_url": "https://candidate.example/sintri",
                "published_at": "2026-07-15 08:00",
                "relevance_hint": "likely_relevant",
                "publication_eligible": True,
                **VERIFIED_METADATA,
            },
            {
                "title": "인천 계양도서관, 유아·어르신 하반기 평생학습 프로그램 운영",
                "publisher": "후보매체2",
                "original_url": "https://candidate.example/gyeyang",
                "published_at": "2026-07-15 08:10",
                "relevance_hint": "likely_relevant",
                "publication_eligible": True,
                **VERIFIED_METADATA,
            },
        ]
        newsdb.ingest_articles(
            conn, "2026-07-15", "인천교육", briefing,
            source_kind="briefing-md", raw_text="briefing"
        )
        newsdb.ingest_articles(
            conn, "2026-07-15", "인천교육", candidates,
            source_kind="collector-json", raw_text="candidates"
        )

        newsdb.build_groups(conn, "2026-07-15")
        groups = newsdb.list_groups(conn, "2026-07-15")
        representative_urls = {
            next(
                member["original_url"] for member in group["members"]
                if member["article_id"] == group["representative_article_id"]
            )
            for group in groups
        }
        self.assertEqual(
            representative_urls,
            {
                "https://briefing.example/sintri",
                "https://briefing.example/gyeyang",
            },
        )
        conn.close()
        tmp.cleanup()

    def test_build_groups_uses_current_date_selection_source_for_reused_url(self):
        tmp = tempfile.TemporaryDirectory()
        conn = newsdb.open_db(str(Path(tmp.name) / "cross-date-source.db"))
        recurring = {
            "title": "인천교육청, 읽걷쓰 AI 교사 공동체 워크숍 개최",
            "publisher": "과거브리핑매체",
            "original_url": "https://example.com/recurring",
            "published_at": "2026-08-11 08:00",
        }
        current_briefing = {
            "title": "인천교육청, 읽걷쓰 AI 교사 공동체 워크숍 운영",
            "publisher": "현재브리핑매체",
            "original_url": "https://example.com/current-briefing",
            "published_at": "2026-08-12 10:00",
        }
        current_candidate = dict(
            recurring,
            published_at="2026-08-12 07:00",
            relevance_hint="likely_relevant",
            publication_eligible=True,
            **VERIFIED_METADATA,
        )
        newsdb.ingest_articles(
            conn, "2026-08-11", "인천교육", [recurring],
            source_kind="briefing-md", raw_text="previous briefing"
        )
        newsdb.ingest_articles(
            conn, "2026-08-12", "인천교육", [current_briefing],
            source_kind="briefing-md", raw_text="current briefing"
        )
        newsdb.ingest_articles(
            conn, "2026-08-12", "인천교육", [current_candidate],
            source_kind="collector-json", raw_text="current candidates"
        )

        self.assertEqual(newsdb.build_groups(conn, "2026-08-12"), 1)
        group = newsdb.list_groups(conn, "2026-08-12")[0]
        representative = next(
            member for member in group["members"]
            if member["article_id"] == group["representative_article_id"]
        )
        self.assertEqual(
            representative["original_url"],
            current_briefing["original_url"],
        )
        conn.close()
        tmp.cleanup()

    def test_build_groups_is_idempotent(self):
        newsdb.build_groups(self.conn, "2026-07-15")
        newsdb.build_groups(self.conn, "2026-07-15")  # 재실행 시 중복 그룹 금지
        n = self.conn.execute("SELECT COUNT(*) FROM article_groups").fetchone()[0]
        self.assertEqual(n, 1)


class TestSelectedFilter(unittest.TestCase):
    def test_candidate_first_then_briefing_preserves_metadata_and_marks_selected(self):
        tmp = tempfile.TemporaryDirectory()
        conn = newsdb.open_db(str(Path(tmp.name) / "t.db"))
        sel = [{"title": "선별 기사", "publisher": "매체", "original_url": "https://a.kr/s1"}]
        pool = [{"title": "선별 기사", "publisher": "매체", "original_url": "https://a.kr/s1",
                 "relevance_hint": "likely_relevant", "location_hits": ["인천"],
                 "published_at": "2026-07-16 09:00", "engine": "google-news-rss",
                 "queries": "Q1:인천교육청"},
                {"title": "풀 전용 기사", "publisher": "매체", "original_url": "https://a.kr/p1",
                 "relevance_hint": "likely_irrelevant"}]
        newsdb.ingest_articles(conn, "2026-07-16", "인천교육", pool, source_kind="collector-json", raw_text="r")
        newsdb.ingest_articles(conn, "2026-07-16", "인천교육", sel, source_kind="briefing-md", raw_text="r")
        rows = newsdb.search_articles(conn, selected=True)
        self.assertEqual([r["title"] for r in rows], ["선별 기사"])
        self.assertEqual(rows[0]["relevance_hint"], "likely_relevant")
        self.assertEqual(json.loads(rows[0]["location_hits"]), ["인천"])
        self.assertEqual(rows[0]["published_at"], "2026-07-16 09:00")
        self.assertEqual(rows[0]["engine"], "google-news-rss")
        conn.close()
        tmp.cleanup()

    def test_briefing_first_then_collector_overlap_preserves_explicit_selection(self):
        tmp = tempfile.TemporaryDirectory()
        conn = newsdb.open_db(str(Path(tmp.name) / "briefing-first.db"))
        shared_url = "https://a.kr/shared"
        briefing = [{
            "title": "읽걷쓰 AI 실천공동체 워크숍 운영",
            "publisher": "브리핑매체",
            "original_url": shared_url,
        }]
        candidates = [{
            "title": "읽걷쓰 AI 실천공동체 워크숍 운영",
            "publisher": "후보매체",
            "original_url": shared_url,
            "relevance_hint": "likely_relevant",
        }]

        newsdb.ingest_articles(
            conn, "2026-08-12", "인천교육", briefing,
            source_kind="briefing-md", raw_text="briefing"
        )
        newsdb.ingest_articles(
            conn, "2026-08-12", "인천교육", candidates,
            source_kind="collector-json", raw_text="candidates"
        )

        selected = newsdb.search_articles(
            conn, date="2026-08-12", selected=True
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["source_kind"], "briefing-md")
        digest = newsdb.build_digest_data(conn, "2026-08-12")
        incheon_titles = {
            article["title"]
            for _, articles in digest["all_by_type"]
            for article in articles
        }
        self.assertEqual(incheon_titles, {briefing[0]["title"]})
        self.assertEqual(digest["other_articles"], [])
        conn.close()
        tmp.cleanup()

    def test_collector_publishes_only_explicitly_verified_candidates(self):
        tmp = tempfile.TemporaryDirectory()
        conn = newsdb.open_db(str(Path(tmp.name) / "triage.db"))
        pool = [
            {"title": "인천교육청 정책 점검", "publisher": "매체",
             "original_url": "https://a.kr/relevant", "relevance_hint": "likely_relevant",
             "publication_eligible": True, **VERIFIED_METADATA},
            {"title": "경남교육청 수업 사례", "publisher": "매체",
             "original_url": "https://a.kr/review", "relevance_hint": "needs_review"},
            {"title": "구청 폭염 냉장고 운영", "publisher": "매체",
             "original_url": "https://a.kr/irrelevant", "relevance_hint": "likely_irrelevant"},
            {"title": "판정 없는 후보", "publisher": "매체",
             "original_url": "https://a.kr/missing"},
            {"title": "미지 판정 후보", "publisher": "매체",
             "original_url": "https://a.kr/unknown", "relevance_hint": "future_status"},
        ]
        newsdb.ingest_articles(
            conn, "2026-08-03", "인천교육", pool,
            source_kind="collector-json", raw_text="pool"
        )
        rows = newsdb.search_articles(conn, date="2026-08-03", selected=True)
        self.assertEqual(
            {r["title"] for r in rows},
            {"인천교육청 정책 점검"},
        )
        self.assertEqual(len(newsdb.search_articles(conn, date="2026-08-03")), 5)
        conn.close()
        tmp.cleanup()

    def test_open_db_backfills_existing_publishable_collector_selection(self):
        tmp = tempfile.TemporaryDirectory()
        db_path = Path(tmp.name) / "migration.db"
        conn = newsdb.open_db(str(db_path))
        newsdb.ingest_articles(
            conn,
            "2026-08-03",
            "인천교육",
            [{
                "title": "기존 후보 기사",
                "publisher": "매체",
                "original_url": "https://a.kr/legacy-candidate",
                "relevance_hint": "likely_relevant",
                "publication_eligible": True,
                **VERIFIED_METADATA,
            }],
            source_kind="collector-json",
            raw_text="legacy candidates",
        )
        conn.execute("DROP TABLE article_selections")
        conn.execute("UPDATE articles SET selected_for_digest = 0")
        conn.commit()
        conn.close()

        migrated = newsdb.open_db(str(db_path))
        selected = newsdb.search_articles(
            migrated, date="2026-08-03", selected=True
        )
        self.assertEqual([row["title"] for row in selected], ["기존 후보 기사"])
        self.assertEqual(selected[0]["source_kind"], "collector-json")
        migrated.close()
        tmp.cleanup()

    def test_open_db_quarantines_legacy_collector_selection_without_verification(self):
        tmp = tempfile.TemporaryDirectory()
        db_path = Path(tmp.name) / "legacy-unverified.db"
        raw = sqlite3.connect(str(db_path))
        raw.executescript(
            """
            CREATE TABLE source_batches (
                batch_id INTEGER PRIMARY KEY,
                batch_date TEXT NOT NULL,
                list_type TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                raw_text TEXT,
                article_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE articles (
                article_id INTEGER PRIMARY KEY,
                batch_id INTEGER,
                batch_date TEXT NOT NULL,
                list_type TEXT NOT NULL,
                title TEXT NOT NULL,
                publisher TEXT DEFAULT '',
                publisher_domain TEXT DEFAULT '',
                original_url TEXT NOT NULL,
                clean_url TEXT NOT NULL UNIQUE,
                published_at TEXT DEFAULT '',
                body_status TEXT DEFAULT '본문 미수집',
                engine TEXT DEFAULT '',
                queries TEXT DEFAULT '',
                relevance_hint TEXT DEFAULT '',
                relevance_reasons TEXT DEFAULT '[]',
                location_hits TEXT DEFAULT '[]',
                education_subject_hits TEXT DEFAULT '[]',
                student_story_hits TEXT DEFAULT '[]',
                negative_context_hits TEXT DEFAULT '[]',
                input_order INTEGER DEFAULT 0,
                collected_at TEXT NOT NULL
            );
            CREATE TABLE article_selections (
                article_id INTEGER NOT NULL,
                batch_date TEXT NOT NULL,
                list_type TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                input_order INTEGER NOT NULL DEFAULT 0,
                selected_at TEXT NOT NULL,
                PRIMARY KEY(article_id, batch_date, list_type)
            );
            CREATE TABLE article_groups (
                group_id INTEGER PRIMARY KEY,
                group_title TEXT NOT NULL,
                summary TEXT DEFAULT '',
                category TEXT DEFAULT '',
                article_type TEXT DEFAULT '',
                priority INTEGER DEFAULT 0,
                representative_article_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE article_group_items (
                group_id INTEGER NOT NULL,
                article_id INTEGER NOT NULL,
                similarity_type TEXT DEFAULT '',
                similarity_score REAL DEFAULT 0,
                UNIQUE(group_id, article_id)
            );
            """
        )
        raw.execute(
            "INSERT INTO source_batches VALUES (1, '2026-08-03', '인천교육', 'collector-json', 'legacy', 1, '2026-08-03 05:00:00')"
        )
        raw.execute(
            "INSERT INTO articles(article_id, batch_id, batch_date, list_type, title, original_url, clean_url, collected_at) "
            "VALUES (1, 1, '2026-08-03', '인천교육', '기존 후보', 'https://a.kr/legacy', 'https://a.kr/legacy', '2026-08-03 05:00:00')"
        )
        raw.execute(
            "INSERT INTO article_selections VALUES (1, '2026-08-03', '인천교육', 'collector-json', 1, '2026-08-03 05:00:00')"
        )
        raw.commit()
        raw.close()

        migrated = newsdb.open_db(str(db_path))
        self.assertEqual(
            migrated.execute(
                "SELECT COUNT(*) FROM article_selections WHERE source_kind='collector-json'"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            migrated.execute(
                "SELECT COUNT(*) FROM legacy_candidate_selections"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            migrated.execute("SELECT COUNT(*) FROM source_batches").fetchone()[0],
            1,
        )
        migrated.close()
        tmp.cleanup()

    def test_reopen_does_not_resurrect_removed_briefing_selection(self):
        tmp = tempfile.TemporaryDirectory()
        db_path = Path(tmp.name) / "briefing-reopen.db"
        first = [
            {"title": "제거 기사", "publisher": "매체",
             "original_url": "https://a.kr/briefing-removed"},
            {"title": "유지 기사", "publisher": "매체",
             "original_url": "https://a.kr/briefing-kept"},
        ]
        revised = [first[1]]

        conn = newsdb.open_db(str(db_path))
        newsdb.ingest_articles(
            conn, "2026-08-12", "인천교육", first,
            source_kind="briefing-md", raw_text="first briefing"
        )
        conn.close()
        conn = newsdb.open_db(str(db_path))
        newsdb.ingest_articles(
            conn, "2026-08-12", "인천교육", revised,
            source_kind="briefing-md", raw_text="revised briefing"
        )
        conn.close()

        reopened = newsdb.open_db(str(db_path))
        selected = newsdb.search_articles(
            reopened, date="2026-08-12", selected=True
        )
        self.assertEqual([row["title"] for row in selected], ["유지 기사"])
        reopened.close()
        tmp.cleanup()

    def test_reopen_does_not_resurrect_removed_collector_selection(self):
        tmp = tempfile.TemporaryDirectory()
        db_path = Path(tmp.name) / "collector-reopen.db"
        first = [
            {"title": "제거 후보", "publisher": "매체",
             "original_url": "https://a.kr/candidate-removed",
             "relevance_hint": "likely_relevant",
             "publication_eligible": True, **VERIFIED_METADATA},
            {"title": "유지 후보", "publisher": "매체",
             "original_url": "https://a.kr/candidate-kept",
             "relevance_hint": "needs_review",
             "publication_eligible": True, **VERIFIED_METADATA},
        ]
        revised = [first[1]]

        conn = newsdb.open_db(str(db_path))
        newsdb.ingest_articles(
            conn, "2026-08-12", "인천교육", first,
            source_kind="collector-json", raw_text="first candidates"
        )
        conn.close()
        conn = newsdb.open_db(str(db_path))
        newsdb.ingest_articles(
            conn, "2026-08-12", "인천교육", revised,
            source_kind="collector-json", raw_text="revised candidates"
        )
        conn.close()

        reopened = newsdb.open_db(str(db_path))
        selected = newsdb.search_articles(
            reopened, date="2026-08-12", selected=True
        )
        self.assertEqual([row["title"] for row in selected], ["유지 후보"])
        reopened.close()
        tmp.cleanup()

    def test_same_day_multi_list_selection_is_published_once_without_ghost_group(self):
        tmp = tempfile.TemporaryDirectory()
        conn = newsdb.open_db(str(Path(tmp.name) / "multi-list.db"))
        article = {
            "title": "공통 교육정책 설명회 개최",
            "publisher": "매체",
            "original_url": "https://a.kr/shared-list",
        }
        newsdb.ingest_articles(
            conn, "2026-08-12", "전국·교육부", [article],
            source_kind="paste", raw_text="national section"
        )
        newsdb.ingest_articles(
            conn, "2026-08-12", "인천교육", [article],
            source_kind="paste", raw_text="incheon section"
        )

        selected = newsdb.search_articles(
            conn, date="2026-08-12", selected=True
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["selection_list_type"], "인천교육")
        self.assertEqual(newsdb.build_groups(conn, "2026-08-12"), 0)
        digest = newsdb.build_digest_data(conn, "2026-08-12")
        published = [
            row
            for _, rows in digest["all_by_type"]
            for row in rows
        ] + digest["other_articles"]
        self.assertEqual(digest["meta"]["total"], 1)
        self.assertEqual([row["title"] for row in published], [article["title"]])
        self.assertEqual(digest["other_articles"], [])
        conn.close()
        tmp.cleanup()

    def test_latest_briefing_replaces_previous_selection_for_same_date(self):
        tmp = tempfile.TemporaryDirectory()
        conn = newsdb.open_db(str(Path(tmp.name) / "replace.db"))
        first = [
            {"title": "첫 선별 기사", "publisher": "매체", "original_url": "https://a.kr/first"},
            {"title": "유지 기사", "publisher": "매체", "original_url": "https://a.kr/keep"},
        ]
        revised = [
            {"title": "유지 기사", "publisher": "매체", "original_url": "https://a.kr/keep"},
        ]
        newsdb.ingest_articles(
            conn, "2026-07-16", "인천교육", first,
            source_kind="briefing-md", raw_text="first"
        )
        newsdb.ingest_articles(
            conn, "2026-07-16", "인천교육", revised,
            source_kind="briefing-md", raw_text="revised"
        )
        rows = newsdb.search_articles(conn, date="2026-07-16", selected=True)
        self.assertEqual([r["title"] for r in rows], ["유지 기사"])
        self.assertEqual(
            len(newsdb.search_articles(conn, date="2026-07-16")), 2
        )
        conn.close()
        tmp.cleanup()

    def test_same_url_can_be_selected_again_on_a_later_date(self):
        tmp = tempfile.TemporaryDirectory()
        conn = newsdb.open_db(str(Path(tmp.name) / "repeat-date.db"))
        repeated = {
            "title": "인천 학교 텃밭 교육 사례",
            "publisher": "매체",
            "original_url": "https://a.kr/repeated",
        }
        newsdb.ingest_articles(
            conn, "2026-07-31", "인천교육", [repeated],
            source_kind="briefing-md", raw_text="first date"
        )
        newsdb.ingest_articles(
            conn, "2026-08-03", "인천교육", [repeated],
            source_kind="briefing-md", raw_text="later date"
        )

        old_rows = newsdb.search_articles(conn, date="2026-07-31", selected=True)
        new_rows = newsdb.search_articles(conn, date="2026-08-03", selected=True)
        self.assertEqual([r["title"] for r in old_rows], [repeated["title"]])
        self.assertEqual([r["title"] for r in new_rows], [repeated["title"]])
        self.assertEqual(new_rows[0]["selection_batch_date"], "2026-08-03")
        self.assertEqual(newsdb.build_digest_data(conn, "2026-08-03")["meta"]["total"], 1)
        history = newsdb.search_articles(conn, selected=True)
        self.assertEqual(
            [row["selection_batch_date"] for row in history],
            ["2026-08-03", "2026-07-31"],
        )
        conn.close()
        tmp.cleanup()

    def test_same_url_candidate_publication_is_date_scoped(self):
        tmp = tempfile.TemporaryDirectory()
        conn = newsdb.open_db(str(Path(tmp.name) / "candidate-date-scope.db"))
        first = {
            "title": "인천교육청 1차 검증 기사",
            "publisher": "매체",
            "original_url": "https://a.kr/repeated-candidate",
            "relevance_hint": "likely_relevant",
            "publication_eligible": True,
            **VERIFIED_METADATA,
        }
        second = {
            **first,
            "title": "인천교육청 2차 미검증 기사",
            "relevance_hint": "likely_irrelevant",
            "publication_eligible": False,
            "body_status": "본문 미검증",
            "publication_verification_basis": "",
            "publication_verified_at": "",
        }
        newsdb.ingest_articles(
            conn, "2026-08-01", "인천교육", [first],
            source_kind="collector-json", raw_text="first",
        )
        newsdb.ingest_articles(
            conn, "2026-08-02", "인천교육", [second],
            source_kind="collector-json", raw_text="second",
        )
        self.assertEqual(
            newsdb.build_digest_data(conn, "2026-08-01")["meta"]["total"], 1
        )
        self.assertEqual(
            newsdb.build_digest_data(conn, "2026-08-02")["meta"]["total"], 0
        )
        conn.close()
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=1)
