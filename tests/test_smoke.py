"""US-007a, criterio 6: smoke check posterior al despliegue contra un servidor de prueba local."""
from __future__ import annotations

import json
import os
import unittest
from typing import List, Optional
from unittest import mock

import support
import smoke_check
from fake_pages import FakePages
from support import SEASON_ID, TempDirTestCase, output_of, run_script, season_with

NEW_UPDATED_AT = "2026-10-02T10:00:00Z"
OLD_UPDATED_AT = "2026-10-01T10:00:00Z"
FAST = ["--interval", "0.05", "--timeout", "2"]


def document(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True).encode("utf-8")


class SmokeTestCase(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.new_season = document(season_with(lambda s: s.update(updatedAt=NEW_UPDATED_AT)))
        self.old_season = document(season_with(lambda s: s.update(updatedAt=OLD_UPDATED_AT)))
        self.index = document(support.BASE_INDEX)
        self.public = support.build_public(self.tmp, season_with(lambda s: s.update(updatedAt=NEW_UPDATED_AT)))
        self.pages = FakePages()
        self.pages.publish("/v1/index.json", self.index)
        self.pages.publish("/v1/seasons/%s.json" % SEASON_ID, self.new_season)
        self.pages.start()
        self.addCleanup(self.pages.stop)

    def smoke(self, extra: Optional[List[str]] = None, host: str = "127.0.0.1"):
        args = [os.path.join(support.SCRIPTS, "smoke-check"), "--base", self.pages.base_url(host), "--public", self.public]
        return run_script(args + (extra if extra is not None else FAST))

    def in_process(self, **kwargs):
        with mock.patch.dict(os.environ, support.tool_env(), clear=True):
            return smoke_check.run_smoke(self.pages.base_url(), SEASON_ID, NEW_UPDATED_AT, log=lambda _line: None, **kwargs)


class Passes(SmokeTestCase):
    def test_us007a_c6_passes_with_etag_304_json_and_matching_updated_at(self) -> None:
        result = self.smoke()
        self.assertEqual(result.returncode, 0, output_of(result))
        self.assertIn("smoke-check correcto", result.stdout)
        conditional = [r for r in self.pages.requests if "if-none-match" in r[2]]
        self.assertEqual(sorted(path for _m, path, _h in conditional),
                         sorted(["/myfutsal-feed/v1/index.json", "/myfutsal-feed/v1/seasons/%s.json" % SEASON_ID]))

    def test_us007a_c6_passes_through_localhost_with_a_path_prefix(self) -> None:
        result = self.smoke(host="localhost")
        self.assertEqual(result.returncode, 0, output_of(result))

    def test_us007a_c6_test_server_listens_only_on_loopback_on_a_free_port(self) -> None:
        self.assertEqual(self.pages.address, "127.0.0.1")
        self.assertGreater(self.pages.port, 0)

    def test_us007a_c6_base_url_with_trailing_slash_is_accepted(self) -> None:
        result = run_script([os.path.join(support.SCRIPTS, "smoke-check"), "--base", self.pages.base_url() + "/",
                             "--public", self.public] + FAST)
        self.assertEqual(result.returncode, 0, output_of(result))


class Fails(SmokeTestCase):
    def assert_fails_with(self, result, fragment: str) -> None:
        self.assertNotEqual(result.returncode, 0, output_of(result))
        self.assertIn("FALLO smoke-check", result.stderr)
        self.assertIn(fragment, result.stderr)

    def test_us007a_c6_missing_etag(self) -> None:
        self.pages.send_etag = False
        self.assert_fails_with(self.smoke(), "ETag")

    def test_us007a_c6_server_does_not_answer_304(self) -> None:
        self.pages.honor_conditional = False
        self.assert_fails_with(self.smoke(), "304")

    def test_us007a_c6_content_type_is_not_json(self) -> None:
        self.pages.content_type = "text/html"
        self.assert_fails_with(self.smoke(), "Content-Type")

    def test_us007a_c6_body_is_not_json(self) -> None:
        self.pages.publish("/v1/seasons/%s.json" % SEASON_ID, b"<html>no es json</html>")
        self.assert_fails_with(self.smoke(), "no es JSON")

    def test_us007a_c6_updated_at_never_matches_within_the_deadline(self) -> None:
        self.pages.publish("/v1/seasons/%s.json" % SEASON_ID, self.old_season)
        result = self.smoke(["--interval", "0.05", "--timeout", "0.3"])
        self.assert_fails_with(result, "updatedAt servido")
        self.assertIn(OLD_UPDATED_AT, result.stderr)

    def test_us007a_c6_missing_document_is_a_404_failure(self) -> None:
        self.pages.versions.pop("/myfutsal-feed/v1/index.json")
        self.assert_fails_with(self.smoke(), "404")

    def test_us007a_c6_redirect_is_not_followed(self) -> None:
        self.pages.redirect_to = "http://127.0.0.1:1/otro"
        self.assert_fails_with(self.smoke(), "301")

    def test_us007a_c6_base_url_must_be_https_unless_loopback(self) -> None:
        with self.assertRaises(smoke_check.SmokeFailure):
            smoke_check.check_base_url("http://example.org/myfutsal-feed")
        self.assertEqual(smoke_check.check_base_url("https://usuario.github.io/myfutsal-feed/"), "https://usuario.github.io/myfutsal-feed")

    def test_us007a_c6_everything_outside_loopback_is_blocked_by_the_proxy(self) -> None:
        result = run_script([os.path.join(support.SCRIPTS, "smoke-check"), "--base", "https://usuario.github.io/myfutsal-feed",
                             "--public", self.public] + FAST)
        self.assert_fails_with(result, "no se pudo pedir")
        self.assertEqual([r for r in self.pages.requests if "github" in r[1]], [])


class Retries(SmokeTestCase):
    def test_us007a_c6_serves_old_then_new_retries_passes_and_prints_elapsed(self) -> None:
        self.pages.publish("/v1/seasons/%s.json" % SEASON_ID, self.old_season, self.old_season, self.new_season)
        result = self.smoke()
        self.assertEqual(result.returncode, 0, output_of(result))
        self.assertRegex(result.stdout, r"servida tras [0-9]+\.[0-9] s")
        self.assertIn("intento 1", result.stdout)

    def test_us007a_c6_default_interval_and_deadline_are_those_of_adr_004(self) -> None:
        self.assertEqual(smoke_check.DEFAULT_INTERVAL_SECONDS, 60)
        self.assertEqual(smoke_check.DEFAULT_TIMEOUT_SECONDS, 12 * 60)

    def test_us007a_c6_retry_timing_with_a_fake_clock(self) -> None:
        self.pages.publish("/v1/seasons/%s.json" % SEASON_ID, self.old_season, self.old_season, self.new_season)
        now = [0.0]
        elapsed = self.in_process(interval=60, timeout=720, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))
        self.assertEqual(elapsed, 120.0)

    def test_us007a_c6_gives_up_after_the_deadline_with_the_expected_number_of_attempts(self) -> None:
        self.pages.publish("/v1/seasons/%s.json" % SEASON_ID, self.old_season)
        now = [0.0]
        with self.assertRaises(smoke_check.SmokeFailure) as caught:
            self.in_process(interval=60, timeout=720, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))
        self.assertIn("720", str(caught.exception))
        season_gets = [r for r in self.pages.requests if r[1].endswith("%s.json" % SEASON_ID)]
        self.assertEqual(len(season_gets), 13)  # t = 0, 60, ..., 720


if __name__ == "__main__":
    unittest.main()
