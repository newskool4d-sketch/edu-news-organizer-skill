"""Publication boundaries and failure recovery using temporary local fixtures."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import publish_site


def public_html(extra=""):
    contract = publish_site.load_publication_contract()
    css = "\n".join(contract["layout"]["required_css_tokens"])
    return (f'<html><head><meta name="edu-news-mode" content="public"><style>{css}</style></head>'
            f'<body>{contract["public_safety"]["required_marker"]}<main class="wrap">{extra}</main></body></html>')


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def legacy_html(extra=""):
    html = public_html(extra).replace('<meta name="edu-news-mode" content="public">', '')
    mast = '<div class="masthead"><div class="mast-inner"><h1 class="mast-title">브리핑</h1><span class="unofficial">비공식 · 개인 정리용</span></div></div>'
    foot = '<footer class="foot">동일보도는 대표기사 1건으로 묶고 관련 매체를 병기했습니다. 인천광역시교육청의 공식 발행물이 아닙니다.</footer>'
    return html.replace('<main', mast + '<main').replace('</main>', foot + '</main>')


class TestLegacyAdoption(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name)
        (self.site / "archive").mkdir()
        self.archive = self.site / "archive/2026-11-03.html"
        self.archive.write_text(legacy_html(), encoding="utf-8")
        (self.site / "index.html").write_text(legacy_html(), encoding="utf-8")
        (self.site / "archive/index.html").write_text(publish_site.render_archive_index(["2026-11-03"]), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_only_returns_reviewable_manifest(self):
        before = snapshot(self.site)
        candidate = publish_site.adopt_legacy_public(self.site)
        self.assertEqual(candidate["source"], "legacy-public-reviewed")
        self.assertEqual(set(candidate["files"]), set(before))
        self.assertEqual(snapshot(self.site), before)
        with self.assertRaisesRegex(ValueError, "public 생성 표식"):
            publish_site.build(self.site)

    def test_explicit_adoption_changes_only_manifest_then_allows_verified_rebuild(self):
        before = snapshot(self.site)
        candidate = publish_site.adopt_legacy_public(self.site)
        publish_site.adopt_legacy_public(self.site, dry_run=False, expected_files=candidate["files"])
        after = snapshot(self.site)
        self.assertEqual({name: after[name] for name in before}, before)
        self.assertEqual(set(after) - set(before), {publish_site.MANIFEST_NAME})
        self.assertEqual(publish_site.build(self.site), ["2026-11-03"])
        self.assertEqual(publish_site.build(self.site), ["2026-11-03"])

    def test_historical_layout_is_preserved_only_in_explicit_legacy_path(self):
        old_html = legacy_html().replace("grid-template-columns:repeat(4,minmax(0,1fr))",
                                        "grid-template-columns:repeat(3,1fr)")
        self.archive.write_text(old_html, encoding="utf-8")
        (self.site / "index.html").write_text(old_html, encoding="utf-8")
        before = snapshot(self.site)
        with self.assertRaisesRegex(ValueError, "반응형 CSS"):
            publish_site.build(self.site)
        candidate = publish_site.adopt_legacy_public(self.site)
        self.assertEqual(snapshot(self.site), before)
        publish_site.adopt_legacy_public(self.site, dry_run=False, expected_files=candidate["files"])
        self.assertEqual(self.archive.read_text(encoding="utf-8"), old_html)
        publish_site.build(self.site)
        self.assertIn("grid-template-columns:repeat(3,1fr)", self.archive.read_text(encoding="utf-8"))
        # A newly generated public page cannot use the historical-layout exemption.
        new_html = public_html().replace("grid-template-columns:repeat(4,minmax(0,1fr))",
                                         "grid-template-columns:repeat(3,1fr)")
        with self.assertRaisesRegex(ValueError, "반응형 CSS"):
            publish_site.validate_publication_contract(new_html, publish_site.load_publication_contract())

    def test_private_script_event_or_unknown_structure_cannot_be_adopted(self):
        for html in (legacy_html('<p class="int-memo">private</p>'),
                     legacy_html('<script>alert(1)</script>'),
                     legacy_html('<img src="data:image/png;base64,AA" onerror="alert(1)">'),
                     legacy_html('<a href="java\nscript:alert(1)">bad</a>'),
                     legacy_html('<link rel="stylesheet" href="https://example.test/style.css">'),
                     legacy_html('<meta http-equiv="refresh" content="0;url=https://example.test/redirect">'),
                     legacy_html('<img src="data:image/png;base64,AA" srcset="https://example.test/image 1x">'),
                     legacy_html('<svg><title>unknown embedded asset</title></svg>'),
                     public_html().replace('<meta name="edu-news-mode" content="public">', '')):
            with self.subTest(html=html):
                self.archive.write_text(html, encoding="utf-8")
                before = snapshot(self.site)
                with self.assertRaises(ValueError):
                    publish_site.adopt_legacy_public(self.site, dry_run=False)
                self.assertEqual(snapshot(self.site), before)

    def test_changes_after_dry_run_are_not_adopted(self):
        candidate = publish_site.adopt_legacy_public(self.site)
        self.archive.write_text(legacy_html('<p>changed</p>'), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "검토 이후"):
            publish_site.adopt_legacy_public(self.site, dry_run=False, expected_files=candidate["files"])
        self.assertFalse((self.site / publish_site.MANIFEST_NAME).exists())

    def test_changes_after_adoption_fail_integrity_check(self):
        publish_site.adopt_legacy_public(self.site, dry_run=False)
        self.archive.write_text(legacy_html('<p>changed</p>'), encoding="utf-8")
        before = snapshot(self.site)
        with self.assertRaisesRegex(ValueError, "무결성"):
            publish_site.build(self.site)
        self.assertEqual(snapshot(self.site), before)

    def test_root_must_have_same_article_links_as_latest_archive(self):
        (self.site / "index.html").write_text(legacy_html('<a href="https://example.test/extra">extra</a>'), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "기사 링크"):
            publish_site.adopt_legacy_public(self.site)


class TestExactParity(unittest.TestCase):
    def test_url_prefix_is_not_the_same_article(self):
        with self.assertRaisesRegex(ValueError, "정합성"):
            publish_site.validate_briefing_published_content_parity(
                [{"title": "Missing", "original_url": "https://example.test/news/1"}],
                '<a href="https://example.test/news/10">Other</a>')

    def test_scattered_title_words_and_plain_url_do_not_count(self):
        article = {"title": "인천 학부모 안전 교육 정책", "original_url": "https://example.test/missing"}
        for html in ('<p>학부모</p><p>안전</p><p>교육 정책</p>',
                     '<!-- https://example.test/missing -->'):
            with self.subTest(html=html), self.assertRaisesRegex(ValueError, "정합성"):
                publish_site.validate_briefing_published_content_parity([article], html)

    def test_case_sensitive_path_is_not_folded(self):
        with self.assertRaisesRegex(ValueError, "정합성"):
            publish_site.validate_briefing_published_content_parity(
                [{"original_url": "https://example.test/News"}],
                '<a href="https://example.test/news">news</a>')

    def test_title_match_without_trusted_group_relationship_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "정합성"):
            publish_site.validate_briefing_published_content_parity(
                [{"title": "인천 학부모 안전 교육 정책", "original_url": "https://example.test/a"}],
                '<a href="https://example.test/b">인천 학부모 안전 교육 정책</a>')


class TestSafeRebuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "site"
        (self.site / "archive").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self, date, html=None):
        path = self.site / "archive" / f"{date}.html"
        path.write_text(html if html is not None else public_html(), encoding="utf-8")

    def test_rebuild_rejects_private_markup_before_any_write(self):
        for extra in ('<h2>내 관심업무</h2><p>PRIVATE_SAMPLE</p>',
                      '<p class="int-memo">PRIVATE_SAMPLE</p>',
                      '<script>alert(1)</script>',
                      '<meta http-equiv="refresh" content="0;url=https://example.test/redirect">'):
            with self.subTest(extra=extra):
                self.seed("2026-11-03", public_html(extra))
                before = snapshot(self.site)
                with self.assertRaisesRegex(ValueError, "공개 안전"):
                    publish_site.build(self.site)
                self.assertEqual(snapshot(self.site), before)

    def test_unknown_legacy_mode_requires_regeneration(self):
        self.seed("2026-11-03", public_html().replace('<meta name="edu-news-mode" content="public">', ''))
        before = snapshot(self.site)
        with self.assertRaisesRegex(ValueError, "다시 생성"):
            publish_site.build(self.site)
        self.assertEqual(snapshot(self.site), before)

    def test_missing_disclaimer_is_rejected(self):
        self.seed("2026-11-03", public_html().replace("비공식 · 개인 정리용", ""))
        with self.assertRaisesRegex(ValueError, "필수 표기"):
            publish_site.build(self.site)

    def test_empty_archive_cannot_promote_an_unverified_existing_root(self):
        (self.site / "index.html").write_text("PRIVATE_SAMPLE", encoding="utf-8")
        before = snapshot(self.site)
        with self.assertRaisesRegex(ValueError, "보관본 없는"):
            publish_site.build(self.site)
        self.assertEqual(snapshot(self.site), before)

    def test_rebuild_records_public_manifest_and_detects_archive_change(self):
        self.seed("2026-11-03")
        self.assertEqual(publish_site.build(self.site), ["2026-11-03"])
        manifest = json.loads((self.site / publish_site.MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual(manifest["mode"], "public")
        for relative, expected in manifest["files"].items():
            self.assertEqual(hashlib.sha256((self.site / relative).read_bytes()).hexdigest(), expected)
        self.seed("2026-11-03", public_html("<p>modified</p>"))
        before = snapshot(self.site)
        with self.assertRaisesRegex(ValueError, "무결성"):
            publish_site.build(self.site)
        self.assertEqual(snapshot(self.site), before)

    def test_broken_old_archive_does_not_change_newer_file(self):
        self.seed("2026-11-04")
        self.seed("2026-11-03", "<html>broken</html>")
        (self.site / "index.html").write_text("PREVIOUS_ROOT", encoding="utf-8")
        before = snapshot(self.site)
        with self.assertRaises(ValueError):
            publish_site.build(self.site)
        self.assertEqual(snapshot(self.site), before)

    def test_replace_failure_restores_all_preexisting_bytes(self):
        self.seed("2026-11-04")
        self.seed("2026-11-03")
        (self.site / "index.html").write_text("PREVIOUS_ROOT", encoding="utf-8")
        before = snapshot(self.site)
        replace = os.replace
        failed = False

        def fail_root_once(src, dst):
            nonlocal failed
            if Path(dst) == self.site / "index.html" and not failed:
                failed = True
                raise OSError("injected write failure")
            return replace(src, dst)

        with mock.patch("publish_site.os.replace", side_effect=fail_root_once):
            with self.assertRaisesRegex(OSError, "injected"):
                publish_site.build(self.site)
        self.assertTrue(failed)
        self.assertEqual(snapshot(self.site), before)


class TestGitScope(unittest.TestCase):
    def test_unpublished_existing_commits_stop_before_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            publish_site.build(site)
            def run(command, **kwargs):
                outputs = {"rev-parse": str(site), "symbolic-ref": "main", "rev-list": "old-commit\n"}
                stdout = "origin/main" if command[1:3] == ["rev-parse", "--abbrev-ref"] else outputs.get(command[1], "")
                return subprocess.CompletedProcess(command, 0, stdout, "")
            with mock.patch("publish_site.subprocess.run", side_effect=run) as mocked:
                with self.assertRaisesRegex(RuntimeError, "미게시 로컬 커밋"):
                    publish_site.git_push(site, "2026-11-04")
            self.assertFalse(any(c.args[0][1] in {"add", "commit", "push"} for c in mocked.call_args_list))

    def test_nested_site_add_does_not_stage_parent_or_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
            site = repo / "site"
            publish_site.build(site)
            (repo / "private-sample.txt").write_text("fixture", encoding="utf-8")
            (site / "notes.txt").write_text("fixture", encoding="utf-8")
            real_run = subprocess.run
            intercepted = []

            def run(command, **kwargs):
                if command[1:3] == ["rev-parse", "--abbrev-ref"]:
                    return subprocess.CompletedProcess(command, 0, "origin/main\n", "")
                if command[1] == "rev-list":
                    return subprocess.CompletedProcess(command, 0, "", "")
                if command[1] in {"commit", "push"}:
                    intercepted.append(command)
                    return subprocess.CompletedProcess(command, 0, "", "")
                return real_run(command, **kwargs)

            with mock.patch("publish_site.subprocess.run", side_effect=run):
                publish_site.git_push(site, "2026-11-04")
            staged = real_run(["git", "diff", "--cached", "--name-only"], cwd=repo,
                              check=True, capture_output=True, text=True).stdout.splitlines()
            self.assertEqual(set(staged), {"site/archive/index.html", "site/publication-manifest.json"})
            self.assertEqual([c[1] for c in intercepted], ["commit", "push"])

    def test_existing_staged_changes_stop_before_add(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            publish_site.build(site)
            def run(command, **kwargs):
                outputs = {"rev-parse": str(site), "symbolic-ref": "main", "diff": "private.txt\0"}
                stdout = "origin/main" if command[1:3] == ["rev-parse", "--abbrev-ref"] else outputs.get(command[1], "")
                return subprocess.CompletedProcess(command, 0, stdout, "")
            with mock.patch("publish_site.subprocess.run", side_effect=run) as mocked:
                with self.assertRaisesRegex(RuntimeError, "기존 staged"):
                    publish_site.git_push(site, "2026-11-04")
            self.assertFalse(any(c.args[0][1] in {"add", "commit", "push"} for c in mocked.call_args_list))


if __name__ == "__main__":
    unittest.main()
