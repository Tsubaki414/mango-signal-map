import unittest

from scripts import collect_x_interactions_v4 as interactions


def tweet(author="Source", destination="Dest", *, reply=False, quote=False, mention=True, tweet_id="1"):
    legacy = {
        "id_str": tweet_id,
        "full_text": f"@{destination} hello",
        "created_at": "Mon Aug 10 23:18:17 +0000 2026",
        "entities": {"user_mentions": ([{"screen_name": destination}] if mention else [])},
        "in_reply_to_screen_name": destination if reply else None,
        "is_quote_status": quote,
    }
    result = {
        "rest_id": tweet_id,
        "core": {"user_results": {"result": {"core": {"screen_name": author}}}},
        "legacy": legacy,
    }
    if quote:
        result["quoted_status_result"] = {
            "result": {
                "rest_id": "quoted",
                "core": {"user_results": {"result": {"core": {"screen_name": destination}}}},
                "legacy": {"id_str": "quoted", "full_text": "original"},
            }
        }
    return result


def response(*tweets):
    return {
        "result": {
            "timeline": {
                "instructions": [
                    {
                        "entries": [
                            {"content": {"itemContent": {"tweet_results": {"result": item}}}}
                            for item in tweets
                        ]
                    }
                ]
            }
        }
    }


class XInteractionTests(unittest.TestCase):
    def test_primary_pairs_are_unique_across_companies(self):
        paths = [
            {"company": "A", "graph_reachable": True, "primary_path_labels": ["Solomon (@root)", "Connector (@middle)", "A (@a)"]},
            {"company": "B", "graph_reachable": True, "primary_path_labels": ["Solomon (@root)", "Connector (@middle)", "B (@b)"]},
        ]
        pairs = interactions.primary_adjacent_pairs(paths)
        self.assertEqual(len(pairs), 3)
        shared = next(item for item in pairs if item["right_handle"] == "middle")
        self.assertEqual(shared["companies"], ["A", "B"])

    def test_reply_takes_precedence_over_mention(self):
        item = tweet(reply=True)
        self.assertEqual(interactions.classify_interaction(item, "source", "dest"), "reply")

    def test_quote_requires_exact_quoted_author(self):
        item = tweet(quote=True, mention=False)
        self.assertEqual(interactions.classify_interaction(item, "SOURCE", "DEST"), "quote")
        self.assertIsNone(interactions.classify_interaction(item, "source", "other"))

    def test_wrong_author_search_noise_is_rejected(self):
        item = tweet(author="SomeoneElse")
        self.assertIsNone(interactions.classify_interaction(item, "source", "dest"))

    def test_nested_quote_is_not_emitted_as_a_second_top_level_hit(self):
        payload = response(tweet(quote=True, mention=False))
        rows = interactions.interaction_rows(payload, "source", "dest", "query")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["interaction_type"], "quote")


if __name__ == "__main__":
    unittest.main()
