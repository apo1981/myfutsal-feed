"""US-007a, criterio 1 y US-007b1, criterio 7c: estructura, lista permitida, secretos, repositorio independiente,
remoto esperado e identidad."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from typing import Dict, List

import support
import structure
from support import TempDirTestCase, write_json

FEED_NAME = "myfutsal-feed"
NON_PERSONAL_EMAIL = "feed@example.invalid"
OTHER_EMAIL = "otra.persona@example.invalid"
OPERATOR_EMAIL = "12345678+operador@users.noreply.github.com"
OTHER_NOREPLY_EMAIL = "99999999+otro@users.noreply.github.com"
WEB_COMMITTER_EMAIL = "noreply@github.com"
EXPECTED_REMOTE = "https://github.com/operador/myfutsal-feed.git"
SKIPPED = shutil.ignore_patterns(".git", ".venv", "__pycache__")


def git(directory: str, *args: str) -> str:
    result = subprocess.run(["git", "-C", directory] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            universal_newlines=True, check=True)
    return result.stdout


def commit(directory: str, email: str, message: str = "test commit") -> None:
    git(directory, "add", "-A")
    git(directory, "-c", "user.name=Test", "-c", "user.email=" + email, "commit", "-q", "--allow-empty", "-m", message)


class StructureTestCase(TempDirTestCase):
    """Una app temporal con un feed copiado del real, ya con su repositorio y su commit."""

    template = ""  # app + feed ya inicializados y con su commit; cada prueba trabaja sobre una copia

    @classmethod
    def setUpClass(cls) -> None:
        cls.template_root = tempfile.mkdtemp(prefix="feed-structure-template.")
        cls.addClassCleanup(shutil.rmtree, cls.template_root, True)
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}):
            app = os.path.join(cls.template_root, "app")
            feed = os.path.join(app, FEED_NAME)
            os.makedirs(app)
            git(app, "init", "-q", "-b", "main")
            with open(os.path.join(app, ".gitignore"), "w") as handle:
                handle.write("/%s/\n" % FEED_NAME)
            shutil.copytree(support.FEED, feed, ignore=SKIPPED)
            git(feed, "init", "-q", "-b", "main")
            git(feed, "config", "--local", "user.name", "Feed Bot")
            git(feed, "config", "--local", "user.email", NON_PERSONAL_EMAIL)
            commit(feed, NON_PERSONAL_EMAIL)

    def setUp(self) -> None:
        super().setUp()
        self.environ_patch = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        self.saved = {key: os.environ.get(key) for key in self.environ_patch}
        os.environ.update(self.environ_patch)
        self.addCleanup(self.restore_environ)
        self.app = os.path.join(self.tmp, "app")
        self.feed = os.path.join(self.app, FEED_NAME)
        shutil.copytree(os.path.join(self.template_root, "app"), self.app)

    def restore_environ(self) -> None:
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def problems(self) -> Dict[str, List[str]]:
        return {name: found for name, found in structure.structure_problems(self.feed, self.app).items() if found}

    def assert_only(self, check: str, *fragments: str) -> None:
        found = self.problems()
        self.assertEqual(list(found), [check], found)
        for fragment in fragments:
            self.assertTrue(any(fragment in problem for problem in found[check]), "%r no esta en %s" % (fragment, found[check]))

    def put(self, relative: str, content: str = "x") -> None:
        path = os.path.join(self.feed, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(content)


class RequiredFiles(StructureTestCase):
    def test_us007a_c1_complete_structure_passes(self) -> None:
        self.assertEqual(self.problems(), {})

    def test_us007a_c1_each_required_file_is_required(self) -> None:
        for relative in structure.REQUIRED_FILES:
            with self.subTest(relative):
                path = os.path.join(self.feed, relative)
                backup = path + ".bak"
                os.rename(path, backup)
                try:
                    self.assertIn("falta %s" % relative, structure.missing_files(self.feed))
                finally:
                    os.rename(backup, path)

    def test_us007a_c1_season_file_is_required(self) -> None:
        os.remove(os.path.join(self.feed, "public", "v1", "seasons", "2026-27.json"))
        self.assertIn("falta public/v1/seasons/<id>.json para la temporada", structure.missing_files(self.feed))

    def test_us007a_c1_fixture_directories_are_required(self) -> None:
        shutil.rmtree(os.path.join(self.feed, "tests", "fixtures", "contract", "invalid"))
        self.assertIn("falta tests/fixtures/contract/invalid/", structure.missing_files(self.feed))


class Allowlist(StructureTestCase):
    def test_us007a_c1_pdf_in_public_fails_and_is_named(self) -> None:
        self.put("public/v1/calendario.pdf")
        self.assert_only("lista-permitida", "public/v1/calendario.pdf")

    def test_us007a_c1_env_file_in_public_fails_and_is_named(self) -> None:
        self.put("public/.env")
        found = self.problems()
        self.assertTrue(any("public/.env" in p for p in found["lista-permitida"]), found)
        self.assertIn("sin-secretos", found)

    def test_us007a_c1_season_name_must_match_pattern(self) -> None:
        for name in ("2026-7.json", "26-27.json", "temporada.json", "2026-27.json.bak"):
            with self.subTest(name):
                self.put("public/v1/seasons/" + name, "{}")
                self.assertTrue(any(name in p for p in structure.allowlist_problems(os.path.join(self.feed, "public"))))
                os.remove(os.path.join(self.feed, "public", "v1", "seasons", name))

    def test_us007a_c1_other_json_and_nested_files_fail(self) -> None:
        self.put("public/v1/other.json", "{}")
        self.put("public/v1/seasons/sub/2026-27.json", "{}")
        problems = structure.allowlist_problems(os.path.join(self.feed, "public"))
        self.assertEqual(len(problems), 2, problems)

    def test_us007a_c1_second_season_with_valid_name_is_allowed(self) -> None:
        self.put("public/v1/seasons/2027-28.json", "{}")
        self.assertEqual(structure.allowlist_problems(os.path.join(self.feed, "public")), [])

    def test_us007a_c1_missing_public_directory_fails(self) -> None:
        self.assertEqual(structure.allowlist_problems(os.path.join(self.tmp, "nada")), ["public/ no existe"])


class Secrets(StructureTestCase):
    def test_us007a_c1_secret_file_names_fail(self) -> None:
        for name in (".env", ".env.local", "clave.pem", "deploy.key", "id_rsa", "docs/id_ed25519"):
            with self.subTest(name):
                self.put(name)
                self.assertTrue(any(name in p for p in structure.secret_problems(self.feed)), name)
                os.remove(os.path.join(self.feed, name))

    def test_us007a_c1_secret_contents_fail(self) -> None:
        for marker in structure.SECRET_CONTENT_MARKERS:
            with self.subTest(marker):
                self.put("notas.txt", "token " + marker.decode("ascii") + "abcdef")
                self.assertTrue(any("notas.txt" in p for p in structure.secret_problems(self.feed)))
        os.remove(os.path.join(self.feed, "notas.txt"))

    def test_us007a_c1_the_four_documented_markers_are_checked(self) -> None:
        self.assertEqual(sorted(m.decode("ascii") for m in structure.SECRET_CONTENT_MARKERS),
                         sorted(["gh" + "p_", "github" + "_pat_", "-----" + "BEGIN", "AK" + "IA"]))

    def test_us007a_c1_headers_file_fails(self) -> None:
        self.put("public/_headers", "/*\n  X-Robots-Tag: noindex\n")
        self.assertIn("sin-headers", self.problems())
        self.assertIn("public/_headers", self.problems()["sin-headers"][0])

    def test_us007a_c1_real_repository_has_no_secrets(self) -> None:
        self.assertEqual(structure.secret_problems(support.FEED), [])


class NoInventedData(StructureTestCase):
    def test_us007a_c1_real_team_in_public_fails(self) -> None:
        path = os.path.join(self.feed, "public", "v1", "seasons", "2026-27.json")
        season = support.season_with()
        write_json(path, season)
        self.assert_only("sin-datos-inventados", "aaa")

    def test_us007a_c1_match_in_public_fails(self) -> None:
        path = os.path.join(self.feed, "public", "v1", "seasons", "2026-27.json")
        season = support.season_with(lambda s: s.update(teams=[s["teams"][0]]))
        season["teams"][0]["id"] = structure.PLACEHOLDER_ID
        write_json(path, season)
        self.assert_only("sin-datos-inventados", "liga-01")


class IndependentRepository(StructureTestCase):
    def test_us007a_c1_feed_without_its_own_repository_fails(self) -> None:
        shutil.rmtree(os.path.join(self.feed, ".git"))
        self.assertIn("repositorio-independiente", self.problems())

    def test_us007a_c1_gitlink_file_instead_of_directory_fails(self) -> None:
        shutil.move(os.path.join(self.feed, ".git"), os.path.join(self.tmp, "elsewhere.git"))
        with open(os.path.join(self.feed, ".git"), "w") as handle:
            handle.write("gitdir: %s\n" % os.path.join(self.tmp, "elsewhere.git"))
        self.assertIn("repositorio-independiente", self.problems())

    def test_us007a_c1_feed_must_be_ignored_by_the_app(self) -> None:
        with open(os.path.join(self.app, ".gitignore"), "w") as handle:
            handle.write("# nada\n")
        self.assert_only("ignorado-por-la-app", "gitignore")

    def test_us007a_c1_app_tracking_the_feed_fails(self) -> None:
        git(self.app, "add", "-f", FEED_NAME)
        found = self.problems()
        self.assertTrue(any("versiona" in p for p in found["ignorado-por-la-app"]), found)

class Remote(StructureTestCase):
    """US-007b1, 7c: antes sin remoto; despues solo `origin` hacia el repositorio esperado y sin credenciales."""

    def add_remote(self, name: str, url: str) -> None:
        git(self.feed, "remote", "add", name, url)

    def use_operator_identity(self) -> None:
        git(self.feed, "config", "--local", "user.email", OPERATOR_EMAIL)
        git(self.feed, "config", "--local", "user.name", "operador")

    def test_us007b1_c7c_no_remote_is_accepted_before_the_first_push(self) -> None:
        self.assertEqual(git(self.feed, "remote").strip(), "")
        self.assertEqual(structure.remote_problems(self.feed), [])

    def test_us007b1_c7c_the_expected_origin_is_accepted(self) -> None:
        self.add_remote("origin", EXPECTED_REMOTE)
        self.use_operator_identity()
        self.assertEqual(self.problems(), {})

    def test_us007b1_c7c_origin_without_dot_git_suffix_is_accepted(self) -> None:
        self.add_remote("origin", "https://github.com/operador/myfutsal-feed")
        self.assertEqual(structure.remote_problems(self.feed), [])

    def test_us007b1_c7c_a_remote_with_credentials_in_the_url_fails(self) -> None:
        self.add_remote("origin", "https://operador:token@github.com/operador/myfutsal-feed.git")
        self.assertTrue(any("sin credenciales" in p for p in structure.remote_problems(self.feed)))

    def test_us007b1_c7c_a_remote_with_another_name_fails(self) -> None:
        self.add_remote("upstream", EXPECTED_REMOTE)
        self.assertTrue(any("upstream" in p for p in structure.remote_problems(self.feed)))

    def test_us007b1_c7c_origin_pointing_to_another_repository_or_host_fails(self) -> None:
        self.add_remote("origin", EXPECTED_REMOTE)
        for url in ("https://github.com/operador/otro-repo.git", "https://example.com/operador/myfutsal-feed.git",
                    "git@github.com:operador/myfutsal-feed.git"):
            with self.subTest(url):
                git(self.feed, "remote", "set-url", "origin", url)
                self.assertNotEqual(structure.remote_problems(self.feed), [])


class Identity(StructureTestCase):
    def test_us007a_c1_only_configured_address_in_log_passes(self) -> None:
        self.assertEqual(structure.identity_problems(self.feed), [])
        self.assertEqual(set(git(self.feed, "log", "--format=%ae %ce").split()), {NON_PERSONAL_EMAIL})

    def test_us007a_c1_other_author_address_fails_and_is_named(self) -> None:
        commit(self.feed, OTHER_EMAIL)
        self.assert_only("identidad-no-personal", OTHER_EMAIL)

    def test_us007a_c1_other_committer_address_fails(self) -> None:
        git(self.feed, "-c", "user.name=T", "-c", "user.email=%s" % OTHER_EMAIL, "commit", "-q", "--allow-empty",
            "--author=Feed <%s>" % NON_PERSONAL_EMAIL, "-m", "mixed")
        self.assert_only("identidad-no-personal", OTHER_EMAIL)

    def test_us007a_c1_missing_local_identity_fails(self) -> None:
        git(self.feed, "config", "--local", "--unset", "user.email")
        self.assertIn("identidad-no-personal", self.problems())

    def test_us007a_c1_identity_equal_to_global_address_fails(self) -> None:
        global_config = os.path.join(self.tmp, "gitconfig-global")
        with open(global_config, "w") as handle:
            handle.write("[user]\n\temail = %s\n" % NON_PERSONAL_EMAIL)
        os.environ["GIT_CONFIG_GLOBAL"] = global_config
        self.assert_only("identidad-no-personal", "global")

    def test_us007a_c1_repository_without_commits_passes_if_identity_is_set(self) -> None:
        shutil.rmtree(os.path.join(self.feed, ".git"))
        git(self.feed, "init", "-q", "-b", "main")
        git(self.feed, "config", "--local", "user.name", "Feed Bot")
        git(self.feed, "config", "--local", "user.email", NON_PERSONAL_EMAIL)
        self.assertEqual(structure.identity_problems(self.feed), [])


class OperatorIdentity(StructureTestCase):
    """US-007b1, 7c (D-074(2)): lista permitida con la `noreply` del operador, configurada como identidad local."""

    def setUp(self) -> None:
        super().setUp()
        git(self.feed, "remote", "add", "origin", EXPECTED_REMOTE)
        git(self.feed, "config", "--local", "user.email", OPERATOR_EMAIL)
        git(self.feed, "config", "--local", "user.name", "operador")

    def test_us007b1_c7c_history_with_the_three_allowed_addresses_passes(self) -> None:
        commit(self.feed, OPERATOR_EMAIL)
        commit(self.feed, WEB_COMMITTER_EMAIL)
        self.assertEqual(self.problems(), {})

    def test_us007b1_c7c_web_committer_with_operator_author_passes(self) -> None:
        git(self.feed, "-c", "user.name=Web", "-c", "user.email=%s" % WEB_COMMITTER_EMAIL, "commit", "-q", "--allow-empty",
            "--author=Operador <%s>" % OPERATOR_EMAIL, "-m", "web edit")
        self.assertEqual(self.problems(), {})

    def test_us007b1_c7c_another_address_fails_and_is_named(self) -> None:
        for other in (OTHER_EMAIL, OTHER_NOREPLY_EMAIL, "persona@gmail.com"):
            with self.subTest(other):
                commit(self.feed, other)
                self.assert_only("identidad-no-personal", other)
                git(self.feed, "reset", "-q", "--hard", "HEAD~1")

    def test_us007b1_c7c_the_non_personal_address_is_still_allowed_in_the_history(self) -> None:
        # Los commits previos de feed@example.invalid no se reescriben (D-074(2)).
        self.assertIn(NON_PERSONAL_EMAIL, git(self.feed, "log", "--format=%ae").split())
        self.assertEqual(self.problems(), {})

    def test_us007b1_c7c_with_remote_the_local_identity_must_be_noreply(self) -> None:
        git(self.feed, "config", "--local", "user.email", NON_PERSONAL_EMAIL)
        self.assert_only("identidad-no-personal", "noreply")
        git(self.feed, "config", "--local", "user.email", "persona@gmail.com")
        self.assert_only("identidad-no-personal", "noreply")

    def test_us007b1_c7c_without_remote_a_personal_local_address_fails_even_with_no_commits(self) -> None:
        git(self.feed, "remote", "remove", "origin")
        git(self.feed, "config", "--local", "user.email", "persona@gmail.com")
        self.assert_only("identidad-no-personal", "persona@gmail.com")

    def test_us007b1_c7c_missing_global_email_counts_as_different(self) -> None:
        # setUp deja GIT_CONFIG_GLOBAL en /dev/null: no hay email global.
        self.assertEqual(subprocess.run(["git", "config", "--global", "--get", "user.email"], stdout=subprocess.PIPE,
                                        universal_newlines=True).stdout.strip(), "")
        self.assertEqual(self.problems(), {})

    def test_us007b1_c7c_local_identity_equal_to_the_global_one_fails(self) -> None:
        global_config = os.path.join(self.tmp, "gitconfig-global")
        with open(global_config, "w") as handle:
            handle.write("[user]\n\temail = %s\n" % OPERATOR_EMAIL)
        os.environ["GIT_CONFIG_GLOBAL"] = global_config
        self.assert_only("identidad-no-personal", "global")


class RealRepository(unittest.TestCase):
    def real_git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", support.FEED] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def test_us007b1_c7c_every_address_in_the_real_history_is_in_the_allowed_list(self) -> None:
        result = self.real_git("log", "--all", "--format=%ae%n%ce")
        if result.returncode != 0:
            self.skipTest("el repositorio del feed aun no tiene commits")
        configured = self.real_git("config", "--local", "--get", "user.email").stdout.strip()
        allowed = {NON_PERSONAL_EMAIL, WEB_COMMITTER_EMAIL}
        if structure.OPERATOR_NOREPLY_PATTERN.match(configured):
            allowed.add(configured)
        self.assertEqual(set(result.stdout.split()) - allowed, set())

    def test_us007b1_c7c_the_real_repository_passes_the_remote_and_identity_checks(self) -> None:
        self.assertEqual(structure.remote_problems(support.FEED), [])
        self.assertEqual(structure.identity_problems(support.FEED), [])


if __name__ == "__main__":
    unittest.main()
