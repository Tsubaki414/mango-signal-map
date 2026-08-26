from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import run_pipeline
from .rapid_x import RapidXClient


def main() -> None:
    parser = argparse.ArgumentParser(prog="mangobd")
    sub = parser.add_subparsers(dest="command", required=True)
    pilot = sub.add_parser("build-pilot", help="validate, score and export pilot data")
    pilot.add_argument("--data-dir", type=Path, default=Path("data/pilot"))
    pilot.add_argument("--output-dir", type=Path, default=Path("outputs/pilot"))
    pilot.add_argument("--scoring", type=Path, default=Path("config/scoring.json"))
    user = sub.add_parser("x-user", help="fetch an X profile through Rapid X only")
    user.add_argument("username")
    following = sub.add_parser("x-followings", help="fetch public followings through Rapid X only")
    following.add_argument("user_id")
    following.add_argument("--count", type=int, default=100)
    follower_ids = sub.add_parser("x-follower-ids", help="fetch public follower IDs through Rapid X only")
    follower_ids.add_argument("username")
    follower_ids.add_argument("--count", type=int, default=500)
    users_by_id = sub.add_parser("x-users-by-id", help="resolve comma-separated X rest IDs through Rapid X only")
    users_by_id.add_argument("user_ids")
    search = sub.add_parser("x-search", help="search X through Rapid X only")
    search.add_argument("query")
    search.add_argument("--type", default="Top")
    search.add_argument("--count", type=int, default=20)
    args = parser.parse_args()
    if args.command == "build-pilot":
        print(json.dumps(run_pipeline(args.data_dir, args.output_dir, args.scoring), indent=2))
    elif args.command == "x-user":
        print(json.dumps(RapidXClient().get_user(args.username), ensure_ascii=False, indent=2))
    elif args.command == "x-followings":
        print(json.dumps(RapidXClient().get_followings(args.user_id, args.count), ensure_ascii=False, indent=2))
    elif args.command == "x-follower-ids":
        print(json.dumps(RapidXClient().get_follower_ids(args.username, args.count), ensure_ascii=False, indent=2))
    elif args.command == "x-users-by-id":
        ids = [value.strip() for value in args.user_ids.split(",") if value.strip()]
        print(json.dumps(RapidXClient().get_users_by_ids(ids), ensure_ascii=False, indent=2))
    elif args.command == "x-search":
        print(json.dumps(RapidXClient().search(args.query, args.type, args.count), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
