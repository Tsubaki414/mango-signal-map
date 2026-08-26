import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, Creator, SocialAccount
from backend.scrapecreators_enrichment import enrich_instagram_account


class _StubClient:
    """Returns the exact response shape that produced the real bug: most
    posts are photos (likes/comments, no video_view_count at all), a
    handful are videos/reels with a much lower view count."""

    def __init__(self, edges, followers=100000):
        self._edges = edges
        self._followers = followers

    def get_instagram_profile(self, handle, **kwargs):
        return {
            "data": {
                "user": {
                    "full_name": "Test Creator",
                    "biography": "bio",
                    "profile_pic_url_hd": "https://example.com/a.jpg",
                    "is_verified": False,
                    "edge_followed_by": {"count": self._followers},
                    "edge_follow": {"count": 10},
                    "edge_owner_to_timeline_media": {"edges": self._edges},
                }
            }
        }


def _photo_edge(likes, comments):
    return {"node": {"id": "p", "edge_liked_by": {"count": likes}, "edge_media_to_comment": {"count": comments}}}


def _video_edge(views, likes, comments):
    return {
        "node": {
            "id": "v",
            "video_view_count": views,
            "edge_liked_by": {"count": likes},
            "edge_media_to_comment": {"count": comments},
        }
    }


class TestInstagramEngagementRate(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.session = sessionmaker(bind=engine)()
        creator = Creator(display_name="Test")
        self.session.add(creator)
        self.session.flush()
        self.account = SocialAccount(creator_id=creator.id, platform="Instagram", handle="test")
        self.session.add(self.account)
        self.session.flush()

    def test_viral_photo_does_not_blow_out_engagement_past_100_percent(self):
        # Regression (real data, "alamin.8020ai"): a 35,808-like photo post
        # (no view count) mixed with a ~600-view-average video subset
        # produced a 232% "engagement rate". With 100k followers this must
        # land in a plausible single/low-double-digit range instead.
        edges = [
            _photo_edge(35808, 6770),
            _photo_edge(9288, 748),
            _video_edge(138, 4, 0),
            _video_edge(788, 30, 2),
            _video_edge(917, 39, 0),
        ]
        client = _StubClient(edges)
        enrich_instagram_account(self.session, self.account, client)
        self.assertIsNotNone(self.account.engagement_rate)
        self.assertLess(self.account.engagement_rate, 100.0)

    def test_engagement_rate_is_none_without_followers(self):
        client = _StubClient([_video_edge(100, 5, 1)], followers=0)
        enrich_instagram_account(self.session, self.account, client)
        self.assertIsNone(self.account.engagement_rate)


if __name__ == "__main__":
    unittest.main()
