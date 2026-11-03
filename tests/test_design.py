"""Design regressions: readable badges, article access, truthful metadata."""
import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import digest_html


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.badge_colors = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.links.append(attrs.get("href"))
        if attrs.get("class") == "type-badge":
            self.badge_colors.append(attrs["style"].split(":", 1)[1])


def contrast(a, b):
    def lum(color):
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
        return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))
    lower, upper = sorted((lum(a), lum(b)))
    return (upper + .05) / (lower + .05)


def article(index=0):
    return {"title": f"시험 기사 {index}의 고유 제목", "publisher": f"시험매체 {index}",
            "original_url": f"https://example.invalid/news/{index}"}


def issue(article_type="정책·현안", related=0):
    rep = article()
    return {"title": rep["title"], "article_type": article_type, "fields": ["학교교육"],
            "summary": "", "representative": rep, "others": [article(i) for i in range(1, related + 1)],
            "member_count": related + 1}


def data(groups=None):
    return {"batch_date": "2026-11-04", "meta": {"total": 12, "candidate_total": 25,
            "issue_count": len(groups or []), "publisher_count": 12, "interest_count": 0},
            "hero_issues": groups or [], "interest_articles": [], "all_by_type": [],
            "other_issues": [], "other_articles": []}


class DesignRegressionTests(unittest.TestCase):
    def test_singleton_title_is_one_link_not_repeated(self):
        group = issue()
        html = digest_html.render(data([group]), public=True)
        self.assertEqual(html.count(group["title"]), 1)
        self.assertIn(group["representative"]["original_url"], Elements(html).links)
        self.assertNotIn('class="count-bar"', html)

    def test_every_related_article_has_an_accessible_link_even_over_ten(self):
        group = issue(related=12)
        html = digest_html.render(data([group]), public=True)
        expected = {a["original_url"] for a in [group["representative"], *group["others"]]}
        self.assertEqual(set(Elements(html).links), expected)
        self.assertIn("<summary>관련 기사 보기 · 12건</summary>", html)

    def test_reference_group_also_exposes_related_links(self):
        group = issue(related=2)
        fixture = data()
        fixture["other_issues"] = [group]
        html = digest_html.render(fixture, public=True)
        self.assertEqual(len(Elements(html).links), 3)

    def test_pending_summary_is_not_presented_as_verified_text(self):
        group = issue()
        group.update(summary="outdated summary must not be published", summary_needs_review=True)
        html = digest_html.render(data([group]), public=True)
        self.assertNotIn(group["summary"], html)
        self.assertIn("요약 재검토 필요", html)

    def test_selected_count_and_date_do_not_claim_an_unrecorded_collection_time(self):
        html = digest_html.render(data([issue()]), public=True)
        self.assertIn('12</span><span class="stat-label">선별 기사', html)
        self.assertNotIn("수집 기사", html)
        self.assertNotIn("09:00 수집 기준", html)
        self.assertIn("브리핑 기준일 · 2026. 11. 4.", html)
        self.assertIn("(한국시간)", html)

    def test_all_rendered_badges_and_secondary_text_meet_normal_text_contrast(self):
        html = digest_html.render(data([issue(t) for t in digest_html.TYPE_COLOR]), public=True)
        for color in Elements(html).badge_colors:
            with self.subTest(badge=color):
                self.assertGreaterEqual(contrast(color, "#ffffff"), 4.5)
        for variable in ("muted", "faint", "orange-text"):
            color = re.search(r"--" + variable + r":(#[a-fA-F0-9]{6})", html).group(1)
            for background in ("#ffffff", "#eef2f6", "#fff1e6", "#f6f8fa"):
                with self.subTest(variable=variable, background=background):
                    self.assertGreaterEqual(contrast(color, background), 4.5)

    def test_public_empty_state_ignores_private_only_content(self):
        fixture = data()
        private_article = dict(article(), interest_hits=["개인태그"], memo="private memo", favorite=1)
        fixture["interest_articles"] = [private_article]
        html = digest_html.render(fixture, public=True)
        self.assertIn('name="edu-news-mode" content="public"', html)
        self.assertIn("확인된 유의미 기사 없음", html)
        self.assertNotIn("private memo", html)
        self.assertNotIn("개인태그", html)
        private_html = digest_html.render(fixture, public=False)
        self.assertIn('name="edu-news-mode" content="private"', private_html)
        self.assertIn("private memo", private_html)


if __name__ == "__main__":
    unittest.main()
