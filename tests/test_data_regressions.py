# -*- coding: utf-8 -*-
"""Repeated user workflows: interests, reviewed groups, and dated corrections."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import newsdb  # noqa: E402


class TestDataRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = newsdb.open_db(str(Path(self.tmp.name) / "news.db"))
        self.day = "2026-11-03"
        self.next_day = "2026-11-04"

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def article(self, key, title=None, published_at=None):
        return {
            "title": title or "인천 학생교육원 학생자치 리더십 캠프 운영",
            "publisher": "시험매체 " + key,
            "original_url": "https://example.invalid/articles/" + key,
            "published_at": published_at or self.day + " 08:00",
        }

    def ingest(self, articles, day=None, raw="synthetic briefing"):
        return newsdb.ingest_articles(
            self.conn, day or self.day, "인천교육", articles,
            source_kind="briefing-md", raw_text=raw,
        )

    def first_group(self, day=None):
        groups = newsdb.list_groups(self.conn, day or self.day)
        self.assertEqual(len(groups), 1)
        return groups[0]

    def test_removed_interest_can_be_readded_and_search_recovers(self):
        article = self.article("interest")
        self.ingest([article])
        newsdb.add_interest(self.conn, "학생교육원")
        self.assertEqual(len(newsdb.search_articles(self.conn, interest_only=True)), 1)

        newsdb.remove_interest(self.conn, "학생교육원")
        self.assertEqual(newsdb.search_articles(self.conn, interest_only=True), [])
        newsdb.add_interest(self.conn, "학생교육원")
        newsdb.add_interest(self.conn, "학생교육원")

        self.assertEqual(newsdb.active_keywords(self.conn), ["학생교육원"])
        recovered = newsdb.search_articles(self.conn, interest_only=True)
        self.assertEqual([r["original_url"] for r in recovered], [article["original_url"]])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM interests").fetchone()[0], 1,
        )

    def test_regroup_keeps_reviewed_summary_and_manual_group_fields(self):
        self.ingest([self.article("one"), self.article("two")])
        newsdb.build_groups(self.conn, self.day)
        before = self.first_group()
        newsdb.set_group_summary(self.conn, before["group_id"], "담당자가 확인한 캠프 요약")
        self.conn.execute(
            "UPDATE article_groups SET category = ?, priority = ? WHERE group_id = ?",
            ("수동 검토 분류", 7, before["group_id"]),
        )
        self.conn.commit()

        newsdb.build_groups(self.conn, self.day)
        newsdb.build_groups(self.conn, self.day)
        after = self.first_group()

        self.assertEqual(after["group_id"], before["group_id"])
        self.assertEqual(after["summary"], "담당자가 확인한 캠프 요약")
        self.assertEqual(after["category"], "수동 검토 분류")
        self.assertEqual(after["priority"], 7)
        self.assertFalse(after["summary_needs_review"])

    def test_changed_membership_archives_summary_and_requests_review(self):
        first, second, third = [self.article(k) for k in ("one", "two", "three")]
        self.ingest([first, second])
        newsdb.build_groups(self.conn, self.day)
        before = self.first_group()
        original_members = {m["article_id"] for m in before["members"]}
        summary = "두 기사에 대해 검토한 요약"
        newsdb.set_group_summary(self.conn, before["group_id"], summary)

        self.ingest([first, second, third], raw="briefing with another report")
        newsdb.build_groups(self.conn, self.day)
        after = self.first_group()
        self.assertEqual(len(after["members"]), 3)
        self.assertEqual(after["summary"], "")
        self.assertTrue(after["summary_needs_review"])
        history = self.conn.execute(
            "SELECT * FROM group_summary_history WHERE batch_date = ? AND summary = ?",
            (self.day, summary),
        ).fetchall()
        self.assertEqual(len(history), 1)
        self.assertEqual(set(json.loads(history[0]["members_json"])), original_members)
        self.assertTrue(history[0]["reason"])

        # A repeated build must keep the review flag without duplicating history.
        newsdb.build_groups(self.conn, self.day)
        self.assertTrue(self.first_group()["summary_needs_review"])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM group_summary_history WHERE summary = ?", (summary,),
        ).fetchone()[0], 1)
        newsdb.set_group_summary(self.conn, after["group_id"], "새 구성 확인 완료")
        reviewed = self.first_group()
        self.assertEqual(reviewed["summary"], "새 구성 확인 완료")
        self.assertFalse(reviewed["summary_needs_review"])

    def test_corrected_publication_time_reselects_representative_and_auto_title(self):
        first = self.article("one", "인천 학생교육원 학생자치 리더십 캠프 운영",
                             self.day + " 10:00")
        second = self.article("two", "인천 학생교육원 학생자치 리더십 캠프 운영 성료",
                              self.day + " 11:00")
        self.ingest([first, second])
        newsdb.build_groups(self.conn, self.day)
        before = self.first_group()
        newsdb.set_group_summary(self.conn, before["group_id"], "검토 완료한 캠프 요약")
        self.conn.execute(
            "UPDATE article_groups SET category = ?, priority = ? WHERE group_id = ?",
            ("담당자 분류", 5, before["group_id"]),
        )
        self.conn.commit()
        self.assertEqual(before["group_title"], first["title"])

        self.ingest([first, dict(second, published_at=self.day + " 09:00")])
        newsdb.build_groups(self.conn, self.day)
        after = self.first_group()
        representative = next(member for member in after["members"]
                              if member["article_id"] == after["representative_article_id"])

        self.assertEqual(after["group_id"], before["group_id"])
        self.assertEqual(representative["original_url"], second["original_url"])
        self.assertEqual(after["group_title"], second["title"])
        self.assertEqual(after["summary"], "검토 완료한 캠프 요약")
        self.assertEqual(after["category"], "담당자 분류")
        self.assertEqual(after["priority"], 5)
        self.assertFalse(after["summary_needs_review"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM group_summary_history").fetchone()[0], 0)

    def test_representative_change_keeps_manually_edited_group_title(self):
        first = self.article("one", published_at=self.day + " 10:00")
        second = self.article("two", published_at=self.day + " 11:00")
        self.ingest([first, second])
        newsdb.build_groups(self.conn, self.day)
        before = self.first_group()
        self.conn.execute("UPDATE article_groups SET group_title = ? WHERE group_id = ?",
                          ("담당자가 정한 이슈 제목", before["group_id"]))
        self.conn.commit()

        self.ingest([first, dict(second, published_at=self.day + " 09:00")])
        newsdb.build_groups(self.conn, self.day)
        after = self.first_group()

        self.assertNotEqual(after["representative_article_id"], before["representative_article_id"])
        self.assertEqual(after["group_title"], "담당자가 정한 이슈 제목")

    def test_summary_is_not_lost_when_group_dissolves(self):
        first, second = self.article("one"), self.article("two")
        self.ingest([first, second])
        newsdb.build_groups(self.conn, self.day)
        group = self.first_group()
        newsdb.set_group_summary(self.conn, group["group_id"], "해체 전 검토 요약")

        self.ingest([first], raw="single article remains")
        newsdb.build_groups(self.conn, self.day)

        self.assertEqual(newsdb.list_groups(self.conn, self.day), [])
        self.assertEqual(self.conn.execute(
            "SELECT summary FROM group_summary_history WHERE batch_date = ?",
            (self.day,),
        ).fetchone()[0], "해체 전 검토 요약")

    def test_same_members_with_corrected_title_require_summary_review(self):
        first = self.article("one", "인천 학생교육원 학생자치 리더십 캠프 30명 참여")
        second = self.article("two", "인천 학생교육원 학생자치 리더십 캠프 30명 참여")
        self.ingest([first, second])
        newsdb.build_groups(self.conn, self.day)
        before = self.first_group()
        member_ids = {member["article_id"] for member in before["members"]}
        original_summary = "캠프 참가자 30명을 확인한 요약"
        newsdb.set_group_summary(self.conn, before["group_id"], original_summary)

        self.ingest([
            dict(first, title="인천 학생교육원 학생자치 리더십 캠프 300명 참여"),
            dict(second, title="인천 학생교육원 학생자치 리더십 캠프 300명 참여"),
        ], raw="same URLs with corrected participant count")
        newsdb.build_groups(self.conn, self.day)

        after = self.first_group()
        self.assertEqual({member["article_id"] for member in after["members"]}, member_ids)
        self.assertEqual(after["summary"], "")
        self.assertTrue(after["summary_needs_review"])
        history = self.conn.execute(
            "SELECT summary, members_json FROM group_summary_history "
            "WHERE batch_date = ? AND summary = ?",
            (self.day, original_summary),
        ).fetchall()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["summary"], original_summary)
        self.assertEqual(set(json.loads(history[0]["members_json"])), member_ids)
        self.assertNotIn(original_summary, newsdb.digest_md(self.conn, self.day))

    def test_same_source_correction_updates_search_digest_and_keeps_memo(self):
        old = self.article("corrected", "인천 학생교육원 캠프 30명 참여")
        self.ingest([old], raw="original 30 participants")
        article_id = newsdb.search_articles(self.conn, date=self.day)[0]["article_id"]
        newsdb.mark_article(self.conn, article_id, memo="후속 검토 메모", favorite=True)
        corrected = dict(old, title="인천 학생교육원 캠프 300명 참여")

        self.ingest([corrected], raw="corrected 300 participants")

        for selected in (False, True):
            rows = newsdb.search_articles(self.conn, date=self.day, q="300명", selected=selected)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["article_id"], article_id)
            self.assertEqual(rows[0]["title"], corrected["title"])
            self.assertEqual(rows[0]["memo"], "후속 검토 메모")
            self.assertEqual(rows[0]["favorite"], 1)
        self.assertEqual(newsdb.search_articles(self.conn, date=self.day, q="30명"), [])
        self.assertIn(corrected["title"], newsdb.digest_md(self.conn, self.day))
        self.assertNotIn(old["title"], newsdb.digest_md(self.conn, self.day))
        self.assertEqual([row[0] for row in self.conn.execute(
            "SELECT raw_text FROM source_batches ORDER BY batch_id",
        )], ["original 30 participants", "corrected 300 participants"])

    def test_later_selection_does_not_rewrite_previous_day_display(self):
        previous = self.article("recurring", "인천 학생교육원 리더십 캠프 운영")
        current = dict(previous, title="인천교육청 AI 교육 정책 방안 발표",
                       publisher="정정매체", published_at=self.next_day + " 08:40")
        self.ingest([previous], raw="previous day report")
        self.ingest([current], day=self.next_day, raw="current day report")

        for selected in (False, True):
            old_row = newsdb.search_articles(self.conn, date=self.day, selected=selected)[0]
            new_row = newsdb.search_articles(self.conn, date=self.next_day, selected=selected)[0]
            self.assertEqual(old_row["title"], previous["title"])
            self.assertEqual(old_row["publisher"], previous["publisher"])
            self.assertEqual(old_row["published_at"], previous["published_at"])
            self.assertEqual(old_row["article_type"], "행사·모집")
            self.assertEqual(new_row["title"], current["title"])
            self.assertEqual(new_row["published_at"], current["published_at"])
            self.assertEqual(new_row["article_type"], "정책·현안")
        self.assertIn(previous["title"], newsdb.digest_md(self.conn, self.day))
        self.assertNotIn(current["title"], newsdb.digest_md(self.conn, self.day))

    def test_backdated_correction_preserves_newer_selection_and_latest_search(self):
        previous = self.article("recurring", "인천 학생교육원 캠프 30명 참여")
        current = dict(previous, title="인천교육청 AI 교육 정책 방안 발표",
                       published_at=self.next_day + " 09:00")
        corrected_previous = dict(previous, title="인천 학생교육원 캠프 300명 참여")
        self.ingest([previous])
        self.ingest([current], day=self.next_day)

        self.ingest([corrected_previous], raw="backdated correction")

        for selected in (False, True):
            old_row = newsdb.search_articles(self.conn, date=self.day, selected=selected)[0]
            new_row = newsdb.search_articles(self.conn, date=self.next_day, selected=selected)[0]
            self.assertEqual(old_row["title"], corrected_previous["title"])
            self.assertEqual(new_row["title"], current["title"])
        self.assertEqual(newsdb.search_articles(self.conn)[0]["title"], current["title"])

    def test_classify_backfill_repairs_migrated_empty_selection_classification(self):
        article = self.article("legacy", "인천교육청 AI 교육 정책 방안 발표")
        self.ingest([article])
        self.conn.execute("UPDATE articles SET article_type = '', edu_fields = ''")
        self.conn.execute("UPDATE article_selections SET article_type = NULL, edu_fields = NULL")
        self.conn.commit()
        self.conn.close()
        self.conn = newsdb.open_db(str(Path(self.tmp.name) / "news.db"))

        self.assertEqual(newsdb.classify_backfill(self.conn), 1)

        for selected in (False, True):
            row = newsdb.search_articles(self.conn, date=self.day, selected=selected)[0]
            self.assertEqual(row["article_type"], "정책·현안")
            self.assertIn("AI·디지털교육", row["edu_fields"])
        self.assertIn(article["title"], newsdb.digest_md(self.conn, self.day))
        self.assertEqual(newsdb.classify_backfill(self.conn), 0)

    def test_classify_backfill_uses_dated_title_and_preserves_existing_values(self):
        previous = self.article("recurring", "인천 학생교육원 리더십 캠프 운영")
        current = dict(previous, title="인천교육청 AI 교육 정책 방안 발표")
        self.ingest([previous])
        self.ingest([current], day=self.next_day)
        self.conn.execute(
            "UPDATE article_selections SET article_type = '', edu_fields = '' WHERE batch_date = ?",
            (self.day,),
        )
        self.conn.execute(
            "UPDATE article_selections SET article_type = ?, edu_fields = '' WHERE batch_date = ?",
            ("수동 검토 분류", self.next_day),
        )
        self.conn.commit()

        self.assertEqual(newsdb.classify_backfill(self.conn), 1)

        previous_row = newsdb.search_articles(self.conn, date=self.day, selected=True)[0]
        current_row = newsdb.search_articles(self.conn, date=self.next_day, selected=True)[0]
        self.assertEqual(previous_row["article_type"], "행사·모집")
        self.assertIn("체험교육·학생자치", previous_row["edu_fields"])
        self.assertEqual(current_row["article_type"], "수동 검토 분류")
        self.assertIn("AI·디지털교육", current_row["edu_fields"])
        self.assertEqual(newsdb.search_articles(self.conn)[0]["article_type"], "정책·현안")


if __name__ == "__main__":
    unittest.main()
