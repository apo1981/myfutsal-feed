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
from fake_pages import JSON_TYPE
from support import SEASON_ID, TempDirTestCase, output_of, run_script, season_with

NEW_UPDATED_AT = "2026-10-02T10:00:00Z"
OLD_UPDATED_AT = "2026-10-01T10:00:00Z"
HTML_TYPE = "text/html; charset=utf-8"
PRIVACY = support.PRIVACY_PAGE.encode("utf-8")
OLD_PRIVACY = PRIVACY.replace(b"GitHub", b"GitHub (antes)")
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
        self.pages.publish("/privacy.html", PRIVACY, content_type=HTML_TYPE)
        self.pages.start()
        self.addCleanup(self.pages.stop)

    def smoke(self, extra: Optional[List[str]] = None, host: str = "127.0.0.1"):
        args = [os.path.join(support.SCRIPTS, "smoke-check"), "--base", self.pages.base_url(host), "--public", self.public]
        return run_script(args + (extra if extra is not None else FAST))

    def in_process(self, **kwargs):
        with mock.patch.dict(os.environ, support.tool_env(), clear=True):
            return smoke_check.run_smoke(self.pages.base_url(), SEASON_ID, NEW_UPDATED_AT, PRIVACY, log=lambda _line: None, **kwargs)


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

    def test_us008b_b4_privacy_page_missing_is_a_404_failure(self) -> None:
        self.pages.versions.pop("/myfutsal-feed/privacy.html")
        self.assert_fails_with(self.smoke(), "privacy.html respondio 404")

    def test_us008b_b4_privacy_page_content_type_must_be_html(self) -> None:
        self.pages.publish("/privacy.html", PRIVACY, content_type=JSON_TYPE)
        self.assert_fails_with(self.smoke(), "no es text/html")

    def test_us008b_b4_privacy_page_must_mention_ip(self) -> None:
        self.pages.publish("/privacy.html", PRIVACY.replace(b"IP", b"direccion"), content_type=HTML_TYPE)
        self.assert_fails_with(self.smoke(), "no menciona 'IP'")

    def test_us008b_b4_privacy_page_must_mention_github(self) -> None:
        self.pages.publish("/privacy.html", PRIVACY.replace(b"GitHub", b"Alojamiento"), content_type=HTML_TYPE)
        self.assert_fails_with(self.smoke(), "no menciona 'GitHub'")

    def test_us008b_b4_privacy_page_differing_from_local_fails_after_the_deadline(self) -> None:
        self.pages.publish("/privacy.html", OLD_PRIVACY, content_type=HTML_TYPE)
        self.assert_fails_with(self.smoke(), "politica distinta de la local")


class Retries(SmokeTestCase):
    def test_us008b_b4_old_privacy_page_then_new_retries_and_passes(self) -> None:
        self.pages.publish("/privacy.html", OLD_PRIVACY, OLD_PRIVACY, PRIVACY, content_type=HTML_TYPE)
        result = self.smoke()
        self.assertEqual(result.returncode, 0, output_of(result))
        self.assertIn("politica distinta de la local", result.stdout)
        self.assertIn("(politica servida)", result.stdout)

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


SEASON_PATH = "/v1/seasons/%s.json" % SEASON_ID
INDEX_PATH = "/v1/index.json"


class TransientCdnErrors(SmokeTestCase):
    """US-040: la CDN falla de forma transitoria; los tests usan reloj y espera falsos, sin plazo real."""

    def run_fake_clock(self):
        now = [0.0]
        waits = []  # type: List[float]

        def sleep(seconds: float) -> None:
            waits.append(seconds)
            now[0] += seconds

        return self.in_process(interval=60, timeout=720, clock=lambda: now[0], sleep=sleep), waits

    def failure_with_fake_clock(self) -> str:
        with self.assertRaises(smoke_check.SmokeFailure) as caught:
            self.run_fake_clock()
        return str(caught.exception)

    def test_us040_c1_503_on_first_request_is_retried_and_passes(self) -> None:
        self.pages.fault(SEASON_PATH, "503")
        elapsed, waits = self.run_fake_clock()
        self.assertEqual(waits, [60])
        self.assertEqual(elapsed, 60)

    def test_us040_c1_503_through_the_script_exits_zero(self) -> None:
        self.pages.fault(SEASON_PATH, "503")
        result = self.smoke()
        self.assertEqual(result.returncode, 0, output_of(result))
        self.assertNotIn("FALLO", result.stderr)

    def test_us040_c2_dropped_connection_is_retried_and_passes(self) -> None:
        self.pages.fault(INDEX_PATH, "drop")
        _elapsed, waits = self.run_fake_clock()
        self.assertEqual(waits, [60])

    def test_us040_c2_dropped_connection_through_the_script_exits_zero(self) -> None:
        self.pages.fault(SEASON_PATH, "drop")
        result = self.smoke()
        self.assertEqual(result.returncode, 0, output_of(result))

    def test_us040_c2_connection_refused_is_transient_and_reported(self) -> None:
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=ConnectionRefusedError("rechazada")):
            with self.assertRaises(smoke_check.TransientFailure):
                smoke_check.fetch("http://127.0.0.1:1/x")

    def test_us040_c2_request_timeout_is_transient(self) -> None:
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=TimeoutError("timed out")):
            with self.assertRaises(smoke_check.TransientFailure):
                smoke_check.fetch("http://127.0.0.1:1/x")

    def test_us040_c3_conditional_200_with_same_etag_is_retried_and_passes(self) -> None:
        self.pages.fault(INDEX_PATH, "cond-same")
        _elapsed, waits = self.run_fake_clock()
        self.assertEqual(waits, [60])

    def test_us040_c4_conditional_200_with_different_etag_persistently_fails_with_that_reason(self) -> None:
        self.pages.fault(INDEX_PATH, "cond-other", forever=True)
        message = self.failure_with_fake_clock()
        self.assertIn("respondio 200 y se esperaba 304", message)
        self.assertIn("distinto", message)

    def test_us040_c5_404_fails_at_the_first_attempt_without_waiting(self) -> None:
        self.pages.versions.pop("/myfutsal-feed" + SEASON_PATH)
        sleeps = []  # type: List[float]
        with self.assertRaises(smoke_check.SmokeFailure) as caught:
            self.in_process(interval=60, timeout=720, sleep=sleeps.append)
        self.assertNotIsInstance(caught.exception, smoke_check.TransientFailure)
        self.assertIn("404", str(caught.exception))
        self.assertIn(SEASON_PATH, str(caught.exception))
        self.assertEqual(sleeps, [])

    def test_us040_c5_other_4xx_on_conditional_request_is_immediate(self) -> None:
        with mock.patch("smoke_check.fetch", return_value=(403, {}, b"")):
            with self.assertRaises(smoke_check.SmokeFailure) as caught:
                smoke_check.check_revalidation("http://x/v1/index.json", '"a"')
        self.assertNotIsInstance(caught.exception, smoke_check.TransientFailure)

    def test_us040_c6_persistent_503_fails_with_the_last_reason(self) -> None:
        self.pages.fault(SEASON_PATH, "503", forever=True)
        message = self.failure_with_fake_clock()
        self.assertIn("503 en", message)
        self.assertIn(SEASON_PATH, message)

    def test_us040_c6_persistent_dropped_connection_fails_with_the_last_reason(self) -> None:
        self.pages.fault(INDEX_PATH, "drop", forever=True)
        self.assertIn("no se pudo pedir", self.failure_with_fake_clock())

    def test_us040_c6_persistent_conditional_200_with_same_etag_fails_with_the_last_reason(self) -> None:
        self.pages.fault(INDEX_PATH, "cond-same", forever=True)
        message = self.failure_with_fake_clock()
        self.assertIn("If-None-Match", message)
        self.assertIn("respondio 200", message)

    def test_us040_c6_persistent_failure_makes_the_script_exit_nonzero(self) -> None:
        self.pages.fault(SEASON_PATH, "503", forever=True)
        result = self.smoke(["--interval", "0.05", "--timeout", "0.3"])
        self.assertNotEqual(result.returncode, 0, output_of(result))
        self.assertIn("503 en", result.stderr)


if __name__ == "__main__":
    unittest.main()
