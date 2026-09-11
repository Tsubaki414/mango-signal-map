import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.importer import _extract_url, normalize_platform, _platform_and_handle
from backend.models import Creator, SocialAccount
from backend.serializers import tab_for_creator


class TestNormalizePlatform(unittest.TestCase):
    def test_x_variants_collapse(self):
        self.assertEqual(normalize_platform("X"), "X")
        self.assertEqual(normalize_platform("X(Twitter)"), "X")
        self.assertEqual(normalize_platform("Twitter"), "X")

    def test_not_specified_becomes_unknown(self):
        self.assertEqual(normalize_platform("未在邮件中注明"), "Unknown")
        self.assertEqual(normalize_platform(None), "Unknown")
        self.assertEqual(normalize_platform(""), "Unknown")

    def test_long_multi_platform_description_collapses(self):
        result = normalize_platform("多平台(IG+TikTok+YouTube Shorts同步发布)")
        self.assertEqual(result, "Multi-platform")

    def test_short_label_passthrough(self):
        # Anything short and not recognized is kept as-is rather than
        # silently discarded -- still standardized-ish, just not in our list.
        self.assertEqual(normalize_platform("Podcast"), "Podcast")


class TestPlatformAndHandle(unittest.TestCase):
    def test_domain_wins_over_messy_hint(self):
        platform, handle = _platform_and_handle("https://instagram.com/someone", "多平台(IG/TikTok/YouTube/Facebook)")
        self.assertEqual(platform, "Instagram")
        self.assertEqual(handle, "someone")

    def test_no_url_falls_back_to_normalized_hint(self):
        platform, handle = _platform_and_handle(None, "X(Twitter)")
        self.assertEqual(platform, "X")
        self.assertIsNone(handle)


class TestExtractUrl(unittest.TestCase):
    def test_label_prefix_and_trailing_note_stripped(self):
        # Regression (real data, creator "Niklas Volland"): the profile_url
        # cell was "TikTok: https://www.tiktok.com/@niklas_volland（另有IG，
        # 邮件自述266K，见报价栏）" -- a label plus URL plus a glued-on note
        # with no separating space. Feeding the whole string to urlparse()
        # produced garbage (platform="Multi-platform", handle="https:").
        raw = "TikTok: https://www.tiktok.com/@niklas_volland（另有IG，邮件自述266K，见报价栏）"
        url = _extract_url(raw)
        self.assertEqual(url, "https://www.tiktok.com/@niklas_volland")
        platform, handle = _platform_and_handle(url, "多平台(IG+TikTok+YouTube Shorts同步发布)")
        self.assertEqual(platform, "TikTok")
        self.assertEqual(handle, "niklas_volland")

    def test_clean_url_passthrough(self):
        self.assertEqual(_extract_url("https://x.com/someone"), "https://x.com/someone")

    def test_no_url_returns_none(self):
        self.assertIsNone(_extract_url("no link provided"))
        self.assertIsNone(_extract_url(None))

    def test_schemeless_known_domain_gets_https_prefix(self):
        # Regression (real data, "x.com/heyDhavall" with no "https://"):
        # without a scheme, urlparse() treats the whole string as a path,
        # so path_parts[0] becomes "x.com" itself instead of the handle.
        url = _extract_url("x.com/heyDhavall")
        self.assertEqual(url, "https://x.com/heyDhavall")
        platform, handle = _platform_and_handle(url, "X")
        self.assertEqual(platform, "X")
        self.assertEqual(handle, "heyDhavall")

    def test_schemeless_unknown_domain_not_matched(self):
        # Only known social domains get the scheme-prepend treatment, so
        # unrelated text is never misread as a URL.
        self.assertIsNone(_extract_url("some random text with a slash/here"))


class TestContainerSegmentHandles(unittest.TestCase):
    def test_youtube_channel_id_url(self):
        # Regression (real data): youtube.com/channel/UC.../ was extracting
        # the literal word "channel" as the handle instead of the id, and
        # collided every /channel/ URL onto the same wrong "handle".
        platform, handle = _platform_and_handle("https://www.youtube.com/channel/UCp1mMtCnO_11BC4dbGv4BSA")
        self.assertEqual(platform, "YouTube")
        self.assertEqual(handle, "UCp1mMtCnO_11BC4dbGv4BSA")

    def test_youtube_custom_url(self):
        platform, handle = _platform_and_handle("https://www.youtube.com/c/AllAboutAI")
        self.assertEqual(platform, "YouTube")
        self.assertEqual(handle, "AllAboutAI")

    def test_linkedin_in_url(self):
        platform, handle = _platform_and_handle("https://www.linkedin.com/in/jan-mraz/")
        self.assertEqual(platform, "LinkedIn")
        self.assertEqual(handle, "jan-mraz")

    def test_normal_handle_url_unaffected(self):
        platform, handle = _platform_and_handle("https://www.youtube.com/@AICashTom0")
        self.assertEqual(platform, "YouTube")
        self.assertEqual(handle, "AICashTom0")


class TestTabForCreator(unittest.TestCase):
    def _creator(self, creator_class):
        c = Creator(display_name="Test", creator_class=creator_class)
        return c

    def test_strategic_classes(self):
        for cls in ("Top KOL", "Community Leader", "KOL"):
            self.assertEqual(tab_for_creator(self._creator(cls)), "strategic")

    def test_media_is_a_distinct_directory_tab(self):
        self.assertEqual(tab_for_creator(self._creator("Media / Community Account")), "media")

    def test_distribution_classes(self):
        for cls in ("KOC", "Marketing Account"):
            self.assertEqual(tab_for_creator(self._creator(cls)), "distribution")

    def test_unknown_goes_to_needs_review_not_strategic(self):
        self.assertEqual(tab_for_creator(self._creator("Unknown")), "needs_review")


if __name__ == "__main__":
    unittest.main()
