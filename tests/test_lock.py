"""US-007b1, criterio 7b: bloqueo de herramientas con hashes e instalacion que falla ante cualquier desvio.

Sin red: la instalacion funcional usa ruedas minimas construidas aqui y `--no-index`. Lo que se prueba es
que las opciones que usa el workflow (`--require-hashes --only-binary=:all:`, sin `--no-deps`) rechazan un
hash distinto y una dependencia transitiva ausente del bloqueo.
"""
from __future__ import annotations

import hashlib
import os
import re
import unittest
import zipfile
from typing import Dict, List, Optional

import support
from support import TempDirTestCase, output_of, run_script

LOCK = os.path.join(support.FEED, "requirements-ci.txt")
WORKFLOW = os.path.join(support.FEED, ".github", "workflows", "publish.yml")
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+?)(?: \\)?$")
HASH = re.compile(r"^    --hash=sha256:[0-9a-f]{64}(?: \\)?$")
INSTALL_LINE = re.compile(r"^\s*\.venv/bin/pip install (.*)$", re.M)
FLAGS_TO_KEEP = ("--require-hashes", "--only-binary=:all:")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def workflow_install_flags() -> List[str]:
    """Opciones de `pip install` del workflow que cambian el comportamiento de la instalacion."""
    match = INSTALL_LINE.search(read(WORKFLOW))
    if not match:
        raise AssertionError("el workflow no instala con .venv/bin/pip install")
    return [token for token in match.group(1).split() if token.startswith("--")]


def tools_python() -> str:
    for candidate in (os.environ.get("FEED_TOOLS_VENV"), os.path.join(support.FEED, ".venv"),
                      os.path.join(support.FEED, "..", ".venv-tools")):
        if candidate and os.path.exists(os.path.join(candidate, "bin", "python")):
            return os.path.join(candidate, "bin", "python")
    raise unittest.SkipTest("falta el venv de herramientas")


class LockFile(unittest.TestCase):
    def blocks(self) -> Dict[str, List[str]]:
        found = {}  # type: Dict[str, List[str]]
        current = None  # type: Optional[str]
        for number, line in enumerate(read(LOCK).splitlines(), start=1):
            if not line or line.startswith("#"):
                continue
            pin = PIN.match(line)
            if pin:
                current = "%s==%s" % (pin.group(1), pin.group(2))
                found[current] = []
            elif HASH.match(line) and current:
                found[current].append(line.strip())
            else:
                self.fail("requirements-ci.txt:%d: linea no admitida: %r" % (number, line))
        return found

    def test_us007b1_c7b_every_pinned_version_has_well_formed_sha256_hashes(self) -> None:
        blocks = self.blocks()
        self.assertIn("check-jsonschema==0.36.2", blocks)
        for pin, hashes in blocks.items():
            with self.subTest(pin):
                self.assertGreaterEqual(len(hashes), 1)
                self.assertEqual(len(hashes), len(set(hashes)), "hashes repetidos")

    def test_us007b1_c7b_packages_with_native_wheels_carry_hashes_for_both_platforms(self) -> None:
        # rpds-py y regress tienen ruedas distintas para macOS/cp39 y manylinux/cp312 (D-078): un solo hash romperia la CI.
        blocks = self.blocks()
        for pin, hashes in blocks.items():
            if pin.startswith(("rpds-py==", "regress==")):
                with self.subTest(pin):
                    self.assertGreater(len(hashes), 2)


class WorkflowInstall(unittest.TestCase):
    def test_us007b1_c7b_workflow_installs_with_require_hashes_and_only_binary(self) -> None:
        flags = workflow_install_flags()
        for expected in FLAGS_TO_KEEP:
            self.assertIn(expected, flags)

    def test_us007b1_c7b_workflow_does_not_skip_dependencies(self) -> None:
        self.assertNotIn("--no-deps", workflow_install_flags())


def build_wheel(directory: str, name: str, requires: Optional[str] = None) -> str:
    """Rueda minima `name-1.0-py3-none-any.whl`. Devuelve el SHA-256 del fichero."""
    dist = "%s-1.0.dist-info" % name
    metadata = "Metadata-Version: 2.1\nName: %s\nVersion: 1.0\n" % name
    if requires:
        metadata += "Requires-Dist: %s\n" % requires
    path = os.path.join(directory, "%s-1.0-py3-none-any.whl" % name)
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr("%s/__init__.py" % name, "")
        wheel.writestr("%s/METADATA" % dist, metadata)
        wheel.writestr("%s/WHEEL" % dist, "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        wheel.writestr("%s/RECORD" % dist, "%s/RECORD,,\n" % dist)
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class InstallBehaviour(TempDirTestCase):
    """La instalacion con las opciones del workflow, sobre ruedas locales."""

    def setUp(self) -> None:
        super().setUp()
        self.wheels = os.path.join(self.tmp, "wheels")
        os.makedirs(self.wheels)
        self.hash_a = build_wheel(self.wheels, "lockdemo_a", requires="lockdemo_b")
        self.hash_b = build_wheel(self.wheels, "lockdemo_b")

    def install(self, lock_text: str, extra: Optional[List[str]] = None):
        lock = os.path.join(self.tmp, "lock.txt")
        with open(lock, "w", encoding="utf-8") as handle:
            handle.write(lock_text)
        target = os.path.join(self.tmp, "target")
        command = [tools_python(), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--no-index",
                   "--find-links", self.wheels, "--target", target, "--no-cache-dir"]
        command += [flag for flag in workflow_install_flags() if flag in FLAGS_TO_KEEP or flag == "--no-deps"]
        command += (extra or []) + ["-r", lock]
        env = support.tool_env({"PIP_CONFIG_FILE": os.devnull})
        return run_script(command, cwd=self.tmp, env=env)

    def lock_text(self, hash_b: Optional[str] = None, with_b: bool = True) -> str:
        text = "lockdemo-a==1.0 \\\n    --hash=sha256:%s\n" % self.hash_a
        if with_b:
            text += "lockdemo-b==1.0 \\\n    --hash=sha256:%s\n" % (hash_b or self.hash_b)
        return text

    def test_us007b1_c7b_complete_lock_with_matching_hashes_installs(self) -> None:
        result = self.install(self.lock_text())
        self.assertEqual(result.returncode, 0, output_of(result))

    def test_us007b1_c7b_a_package_that_does_not_match_its_hash_fails(self) -> None:
        result = self.install(self.lock_text(hash_b="0" * 64))
        self.assertNotEqual(result.returncode, 0, output_of(result))
        self.assertIn("HASH", output_of(result))

    def test_us007b1_c7b_a_transitive_dependency_missing_from_the_lock_fails(self) -> None:
        result = self.install(self.lock_text(with_b=False))
        self.assertNotEqual(result.returncode, 0, output_of(result))

    def test_us007b1_c7b_the_missing_dependency_test_is_sensitive_to_no_deps(self) -> None:
        # Control: con --no-deps la misma instalacion pasaria; por eso el workflow no puede llevarlo.
        result = self.install(self.lock_text(with_b=False), extra=["--no-deps"])
        self.assertEqual(result.returncode, 0, output_of(result))


if __name__ == "__main__":
    unittest.main()
