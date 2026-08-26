import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.importer import run_import
from backend.models import Base, Creator, RateCard, SocialAccount

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
KOL_DATA_DIR = REPO_ROOT / "KOL_data"


@unittest.skipUnless(KOL_DATA_DIR.exists(), "KOL_data source sheets not present")
class TestImporter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(cls.tmpdir.name) / "test.db"
        cls.engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(cls.engine)
        Session = sessionmaker(bind=cls.engine)
        cls.session = Session()
        cls.stats = run_import(KOL_DATA_DIR, cls.session)

    @classmethod
    def tearDownClass(cls):
        cls.session.close()
        cls.tmpdir.cleanup()

    def test_only_quoted_rows_imported(self):
        # Source files have 209 (CSV) + 93 (xlsx) quoted rows = 302 records,
        # merging into slightly fewer distinct (platform, handle) creators.
        self.assertEqual(self.stats["records_processed"], 302)
        self.assertGreater(self.stats["creators_after"], 250)
        self.assertLessEqual(self.stats["creators_after"], 302)

    def test_no_follower_count_absurdly_large(self):
        # Regression: annotated follower cells like "54,591（10天均浏览...)"
        # must not get digit-concatenated into a nonsense value.
        accounts = self.session.query(SocialAccount).all()
        for a in accounts:
            if a.followers is not None:
                self.assertLess(a.followers, 500_000_000, f"{a.handle} has implausible followers={a.followers}")

    def test_every_confident_rate_card_has_usd_amount(self):
        cards = self.session.query(RateCard).filter(RateCard.is_confident.is_(True)).all()
        self.assertGreater(len(cards), 0)
        for rc in cards:
            self.assertIsNotNone(rc.quote_amount_usd, f"confident card {rc.id} missing USD conversion")

    def test_unconfident_rows_keep_raw_text_and_no_amount(self):
        cards = self.session.query(RateCard).filter(RateCard.is_confident.is_(False)).all()
        self.assertGreater(len(cards), 0)
        for rc in cards:
            self.assertIsNone(rc.quote_amount_usd)
            self.assertTrue(rc.raw_quote_text)

    def test_creators_default_to_unknown_class(self):
        creators = self.session.query(Creator).all()
        self.assertTrue(all(c.creator_class == "Unknown" for c in creators))

    def test_reimport_is_idempotent(self):
        before = self.session.query(Creator).count()
        run_import(KOL_DATA_DIR, self.session)
        after = self.session.query(Creator).count()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
