"""Release-infrastructure regressions: DB, access boundary, and TLS."""

from __future__ import annotations

import os
import json
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.access import build_access_link, install_access_gate
from backend.db import engine
from scripts.backup_db import main as backup_main
from scripts.bootstrap_internal_access import ensure_local_token
from scripts.check_runtime_db import ContractError, validate_database
from scripts.capture_uat_screenshots import _safe_screenshot, _strict_failures
from scripts.add_shortlist_quote_override_column import ensure_shortlist_quote_override_column
from scripts.run_solomon_uat import verified_ssl_context


APP_ROOT = Path(__file__).resolve().parent.parent


class TestDatabaseReleaseContract(unittest.TestCase):
    def test_audited_deploy_seed_satisfies_exact_contract(self):
        counts = validate_database(APP_ROOT / "deploy_seed.db", exact_seed=True)
        self.assertEqual(counts["companies"], 99)
        self.assertEqual(counts["intro_paths"], 640)
        self.assertEqual(counts["company_research_dossiers"], 15)

    def test_missing_database_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ContractError):
                validate_database(Path(temp_dir) / "missing.db", exact_seed=False)

    def test_preflight_accepts_production_like_schema_before_column_migration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate = Path(temp_dir) / "pre_migration.db"
            shutil.copy2(APP_ROOT / "deploy_seed.db", candidate)
            with sqlite3.connect(candidate) as connection:
                connection.execute(
                    "ALTER TABLE intro_paths DROP COLUMN direction_data_unavailable_reason"
                )
            counts = validate_database(
                candidate,
                exact_seed=False,
                preflight=True,
            )
            self.assertEqual(counts["intro_paths"], 640)
            with self.assertRaises(ContractError):
                validate_database(candidate, exact_seed=False)

    def test_quote_override_column_migrates_an_old_volume_idempotently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate = Path(temp_dir) / "old_quote_schema.db"
            shutil.copy2(APP_ROOT / "deploy_seed.db", candidate)
            with sqlite3.connect(candidate) as connection:
                connection.execute(
                    "ALTER TABLE shortlist_items DROP COLUMN quote_usd_override"
                )
            validate_database(candidate, exact_seed=False, preflight=True)
            with self.assertRaises(ContractError):
                validate_database(candidate, exact_seed=False)
            self.assertTrue(ensure_shortlist_quote_override_column(candidate))
            self.assertFalse(ensure_shortlist_quote_override_column(candidate))
            validate_database(candidate, exact_seed=False)

    def test_sqlalchemy_connections_enforce_foreign_keys_and_busy_timeout(self):
        with engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_keys").scalar(), 1)
            self.assertEqual(connection.exec_driver_sql("PRAGMA busy_timeout").scalar(), 10_000)

    def test_backup_cli_honours_injected_database_and_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "mounted.db"
            backup_dir = root / "snapshots"
            with sqlite3.connect(source) as connection:
                connection.execute("CREATE TABLE marker (id INTEGER PRIMARY KEY)")
                connection.execute("INSERT INTO marker VALUES (1)")
            result = backup_main(
                [
                    "test",
                    "--db",
                    str(source),
                    "--backup-dir",
                    str(backup_dir),
                ]
            )
            self.assertEqual(result, 0)
            backups = list(backup_dir.glob("kol_*_test.db"))
            self.assertEqual(len(backups), 1)
            with sqlite3.connect(backups[0]) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM marker").fetchone()[0], 1)

    def test_runtime_image_cannot_skip_seedcheck_stage(self):
        dockerfile = (APP_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY --from=seedcheck", dockerfile)
        self.assertIn("--seed-contract", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertNotRegex(
            dockerfile,
            r"(?m)^\s*VOLUME\b",
            "Railway rejects Docker VOLUME; persistence is an attached /data volume",
        )

    def test_entrypoint_backs_up_between_preflight_and_migrations(self):
        entrypoint = (APP_ROOT / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertLess(
            entrypoint.index("--preflight-contract"),
            entrypoint.index("scripts/backup_db.py"),
        )
        self.assertLess(
            entrypoint.index("scripts/backup_db.py"),
            entrypoint.index("scripts/add_intro_path_direction_columns.py"),
        )
        self.assertLess(
            entrypoint.index("scripts/add_intro_path_direction_columns.py"),
            entrypoint.rindex("--runtime-contract"),
        )
        self.assertLess(
            entrypoint.index("scripts/apply_gap_operators.py"),
            entrypoint.index("scripts/apply_operator_x_handles_snapshot.py"),
        )
        self.assertLess(
            entrypoint.index("scripts/apply_operator_x_handles_snapshot.py"),
            entrypoint.index("scripts/apply_intro_bridges_snapshot.py"),
        )
        self.assertLess(
            entrypoint.index("scripts/apply_top5_sales_packets_v7.py"),
            entrypoint.index("scripts/apply_apify_review_candidates.py"),
        )
        self.assertLess(
            entrypoint.index("scripts/apply_apify_review_candidates.py"),
            entrypoint.index("scripts/apply_bd_refocus_20260901.py"),
        )
        self.assertLess(
            entrypoint.index("scripts/apply_bd_refocus_20260901.py"),
            entrypoint.rindex("--runtime-contract"),
        )

    def test_deploy_review_package_contains_only_candidate_contract_layers(self):
        payload = json.loads(
            (APP_ROOT / "data" / "apify_review_candidates_v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(payload),
            {"schema_version", "generated_at", "operators", "sponsorships", "creators"},
        )
        self.assertGreaterEqual(len(payload["operators"]), 40)
        self.assertTrue(all(row.get("review_status") == "unreviewed" for row in payload["operators"]))
        raw = json.dumps(payload).casefold()
        for forbidden in ("api_token", "apify_api_token", "raw actor", "dataset_id", "run_id", "cache_path"):
            self.assertNotIn(forbidden, raw)

    def test_railway_uses_the_public_non_sensitive_healthcheck(self):
        config = json.loads((APP_ROOT / "railway.json").read_text(encoding="utf-8"))
        self.assertEqual(config["build"]["builder"], "DOCKERFILE")
        self.assertEqual(config["deploy"]["healthcheckPath"], "/healthz")
        self.assertGreaterEqual(config["deploy"]["healthcheckTimeout"], 60)


def _access_test_app() -> FastAPI:
    app = FastAPI()
    install_access_gate(app)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/private")
    def private():
        return {"ok": True}

    @app.post("/private")
    def private_write():
        return {"written": True}

    return app


class TestInternalAccessBoundary(unittest.TestCase):
    def test_bootstrap_stores_secret_without_returning_it_in_file_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            token, created = ensure_local_token(env_file)
            self.assertTrue(created)
            self.assertGreaterEqual(len(token), 48)
            self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)
            self.assertIn("INTERNAL_ACCESS_TOKEN=", env_file.read_text(encoding="utf-8"))
            reused, created_again = ensure_local_token(env_file)
            self.assertFalse(created_again)
            self.assertEqual(reused, token)

    def test_missing_token_keeps_reads_public_and_fails_writes_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with TestClient(_access_test_app(), base_url="https://testserver") as client:
                self.assertEqual(client.get("/private").status_code, 200)
                response = client.post("/private")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(
                    response.json()["detail"],
                    "Internal write access is not configured",
                )
                status = client.get("/internal/access-status").json()
                self.assertFalse(status["write_access"])
                self.assertFalse(status["write_access_configured"])

    def test_enabled_gate_allows_public_reads_and_protects_writes_without_basic_auth(self):
        with patch.dict(os.environ, {"INTERNAL_ACCESS_TOKEN": "test-token"}, clear=True):
            with TestClient(_access_test_app(), base_url="https://testserver") as client:
                response = client.get("/private")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["x-mango-access-mode"], "public-read-only")
                self.assertNotIn("www-authenticate", response.headers)
                self.assertEqual(client.get("/healthz").status_code, 200)
                self.assertEqual(client.post("/private").status_code, 401)
                status = client.get("/internal/access-status").json()
                self.assertEqual(status["mode"], "public_read_only")
                self.assertFalse(status["write_access"])
                authorized = client.post(
                    "/private",
                    headers={"Authorization": "Bearer test-token"},
                )
                self.assertEqual(authorized.status_code, 200)
                self.assertEqual(authorized.headers["cache-control"], "private, no-store")
                self.assertEqual(authorized.headers["referrer-policy"], "no-referrer")
                self.assertEqual(authorized.headers["x-content-type-options"], "nosniff")
                self.assertEqual(authorized.headers["x-frame-options"], "DENY")
                self.assertIn("frame-ancestors 'none'", authorized.headers["content-security-policy"])
                self.assertEqual(
                    client.get(
                        "/private",
                        headers={"X-Internal-Access-Token": "test-token"},
                    ).status_code,
                    200,
                )

    def test_signed_link_sets_hardened_cookie_and_does_not_expose_secret(self):
        token = "do-not-put-this-in-the-link"
        with patch.dict(os.environ, {"INTERNAL_ACCESS_TOKEN": token}, clear=True):
            app = _access_test_app()
            with TestClient(app, base_url="https://testserver") as client:
                link = build_access_link(
                    "https://testserver",
                    token,
                    expires=int(time.time()) + 60,
                    next_path="/private",
                )
                self.assertNotIn(token, link)
                parsed = urlparse(link)
                self.assertEqual(parsed.query, "")
                bootstrap = client.get(parsed.path)
                self.assertEqual(bootstrap.status_code, 200)
                self.assertNotIn(token, bootstrap.text)
                fragment = {key: values[0] for key, values in parse_qs(parsed.fragment).items()}
                response = client.post("/internal/session", json=fragment)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["next"], "/private")
                cookie = response.headers["set-cookie"].lower()
                self.assertIn("httponly", cookie)
                self.assertIn("secure", cookie)
                self.assertIn("samesite=lax", cookie)
                self.assertEqual(client.post("/private").status_code, 200)
                self.assertTrue(client.get("/internal/access-status").json()["write_access"])

    def test_expired_signed_link_is_rejected(self):
        token = "expired-test-token"
        with patch.dict(os.environ, {"INTERNAL_ACCESS_TOKEN": token}, clear=True):
            with TestClient(_access_test_app(), base_url="https://testserver") as client:
                link = build_access_link(
                    "https://testserver",
                    token,
                    expires=int(time.time()) - 60,
                )
                parsed = urlparse(link)
                fragment = {key: values[0] for key, values in parse_qs(parsed.fragment).items()}
                self.assertEqual(client.post("/internal/session", json=fragment).status_code, 401)


class TestVerifiedUatTls(unittest.TestCase):
    def test_uat_uses_certifi_ca_bundle(self):
        with patch("scripts.run_solomon_uat.certifi.where", return_value="/tmp/ca.pem"):
            with patch("scripts.run_solomon_uat.ssl.create_default_context") as create:
                sentinel = object()
                create.return_value = sentinel
                self.assertIs(verified_ssl_context(), sentinel)
                create.assert_called_once_with(cafile="/tmp/ca.pem")


class TestBrowserCaptureHardening(unittest.IsolatedAsyncioTestCase):
    async def test_screenshot_timeout_is_recorded_instead_of_raised(self):
        class FailingPage:
            async def screenshot(self, **_kwargs):
                raise TimeoutError("capture stalled")

        error = await _safe_screenshot(FailingPage(), Path("unused.png"))
        self.assertEqual(error, "TimeoutError: capture stalled")

    def test_strict_mode_rejects_a_recorded_screenshot_error(self):
        report = {
            "api_probes": [],
            "surfaces": [
                {
                    "name": "network-small-screen.png",
                    "current_release_selector_found": True,
                    "navigation_error": None,
                    "screenshot_error": "TimeoutError: capture stalled",
                    "page_close_error": None,
                    "console_errors": [],
                    "application_console_errors": [],
                    "page_errors": [],
                    "request_failures": [],
                    "http_errors": [],
                }
            ],
            "broken_internal_links": [],
            "external_links": [],
        }
        self.assertIn(
            "screenshot: network-small-screen.png",
            _strict_failures(report),
        )

    def test_network_mobile_surface_is_captured_exactly_once(self):
        source = (APP_ROOT / "scripts" / "capture_uat_screenshots.py").read_text(
            encoding="utf-8"
        )
        self.assertEqual(source.count("await shot(network_mobile"), 1)

    def test_visual_scan_does_not_treat_offscreen_lazy_images_as_broken(self):
        source = (APP_ROOT / "scripts" / "capture_uat_screenshots.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("[...document.images].filter(inViewport)", source)
        self.assertIn("r.top < window.innerHeight", source)
        self.assertIn("r.left < window.innerWidth", source)

    def test_campaign_manager_capture_waits_for_loaded_content(self):
        source = (APP_ROOT / "scripts" / "capture_uat_screenshots.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '"#managerModal:not(.hidden) #managerBody .shortlist-toolbar"',
            source,
        )
        self.assertIn("manager_visual_state = await _page_visual_state(page)", source)


if __name__ == "__main__":
    unittest.main()
