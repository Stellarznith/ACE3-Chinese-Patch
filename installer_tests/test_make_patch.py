from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "installer"))
from engine import install  # noqa: E402
from make_patch import PatchBuildError, create_patch  # noqa: E402


def find_xdelta() -> tuple[Path, Path] | None:
    root = ROOT / "installer" / "third_party" / "xdelta3-3.2.2"
    encoder = root / "xdelta3.exe"
    decoder = root / "xdelta3decode.exe"
    if os.name == "nt" and encoder.is_file() and decoder.is_file():
        return encoder, decoder
    return None


class PatchGeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        found = find_xdelta()
        if found is None:
            raise unittest.SkipTest("官方 Xdelta3 3.2.2 Windows 测试程序未提供")
        cls.encoder, cls.decoder = found

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "installer_tests")
        self.root = Path(self.temp.name)
        self.source = self.root / "输入 source.iso"
        self.full_target = self.root / "目标 FULL.iso"
        self.update_source = self.root / "旧版 中文.iso"
        self.update_target = self.root / "目标 UPDATE.iso"
        self.source_bytes = bytes(range(256)) * 32 + b"source trailer"
        self.full_bytes = bytes(range(255, -1, -1)) * 24 + b"full target"
        self.update_source_bytes = self.full_bytes + b"old revision"
        self.update_bytes = self.full_bytes + b"new revision"
        self.source.write_bytes(self.source_bytes)
        self.full_target.write_bytes(self.full_bytes)
        self.update_source.write_bytes(self.update_source_bytes)
        self.update_target.write_bytes(self.update_bytes)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _generate(self, kind: str, source: Path, target: Path, name: str,
                  expected_source: bool = False) -> tuple[Path, Path]:
        patch = self.root / name
        args = (kind, source, target, patch,
                "TEST source", f"TEST target {kind}", self.encoder)
        if expected_source:
            sidecar = create_patch(*args,
                                   expected_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
        else:
            sidecar = create_patch(*args)
        return patch, sidecar

    def _assert_pair(self, kind: str, source: Path, target: Path,
                     name: str, expected_source: bool = False) -> dict:
        patch, sidecar = self._generate(kind, source, target, name, expected_source)
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["patch_type"], kind)
        self.assertEqual(data["source"]["iso_size_bytes"], source.stat().st_size)
        self.assertEqual(data["source"]["iso_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(data["target"]["iso_size_bytes"], target.stat().st_size)
        self.assertEqual(data["target"]["iso_sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertEqual(data["patch"]["filename"], patch.name)
        self.assertEqual(data["patch"]["size_bytes"], patch.stat().st_size)
        self.assertEqual(data["patch"]["sha256"], hashlib.sha256(patch.read_bytes()).hexdigest())
        self.assertEqual(data["patch"]["format"], "xdelta3")
        self.assertEqual(data["patch"]["format_version"], "3.2.2")
        return data

    def _decode_and_compare(self, source: Path, patch: Path, target: Path) -> None:
        restored = self.root / (patch.stem + ".restored.iso")
        result = subprocess.run([str(self.decoder), "-d", "-s", str(source), str(patch), str(restored)],
                                capture_output=True, check=False, shell=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(restored.stat().st_size, target.stat().st_size)
        self.assertEqual(hashlib.sha256(restored.read_bytes()).digest(),
                         hashlib.sha256(target.read_bytes()).digest())
        self.assertEqual(restored.read_bytes(), target.read_bytes())

    def test_real_full_encode_manifest_schema_and_decode_roundtrip(self) -> None:
        before = hashlib.sha256(self.source.read_bytes()).digest()
        self._assert_pair("FULL", self.source, self.full_target, "ACE3_CN_test_full.xdelta", True)
        self._decode_and_compare(self.source, self.root / "ACE3_CN_test_full.xdelta", self.full_target)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), before)

    def test_real_update_encode_manifest_schema_and_decode_roundtrip(self) -> None:
        before = hashlib.sha256(self.update_source.read_bytes()).digest()
        self._assert_pair("UPDATE", self.update_source, self.update_target,
                          "ACE3_CN_test_update.xdelta")
        self._decode_and_compare(self.update_source, self.root / "ACE3_CN_test_update.xdelta",
                                 self.update_target)
        self.assertEqual(hashlib.sha256(self.update_source.read_bytes()).digest(), before)

    def test_generated_full_pair_is_accepted_by_installer_engine(self) -> None:
        patch, _ = self._generate("FULL", self.source, self.full_target,
                                  "ACE3_CN_engine_test_full.xdelta", True)
        output = self.root / "安装器引擎输出.iso"
        install(patch, self.source, output, xdelta_executable=self.encoder)
        self.assertEqual(output.read_bytes(), self.full_target.read_bytes())

    def test_generated_update_pair_is_accepted_by_installer_engine(self) -> None:
        patch, _ = self._generate("UPDATE", self.update_source, self.update_target,
                                  "ACE3_CN_engine_test_update.xdelta")
        output = self.root / "安装器引擎增量输出.iso"
        install(patch, self.update_source, output, xdelta_executable=self.encoder)
        self.assertEqual(output.read_bytes(), self.update_target.read_bytes())

    def test_full_requires_confirmed_source_sha256(self) -> None:
        with self.assertRaisesRegex(PatchBuildError, "必须显式提供"):
            self._generate("FULL", self.source, self.full_target, "no_hash.xdelta")

    def test_full_rejects_wrong_confirmed_source_sha256(self) -> None:
        patch = self.root / "wrong_hash.xdelta"
        with self.assertRaisesRegex(PatchBuildError, "不匹配"):
            create_patch("FULL", self.source, self.full_target, patch,
                         "TEST source", "TEST target", self.encoder,
                         "0" * 64)
        self.assertFalse(patch.exists())

    def test_existing_outputs_are_never_overwritten(self) -> None:
        patch = self.root / "exists.xdelta"
        patch.write_bytes(b"preserve patch")
        with self.assertRaisesRegex(PatchBuildError, "不会覆盖"):
            create_patch("UPDATE", self.update_source, self.update_target, patch,
                         "old", "new", self.encoder)
        self.assertEqual(patch.read_bytes(), b"preserve patch")

    def test_existing_building_sidecar_is_preserved(self) -> None:
        patch = self.root / "preserve_sidecar.xdelta"
        temp_sidecar = self.root / "preserve_sidecar.json.building"
        temp_sidecar.write_text("user-owned temporary data", encoding="utf-8")
        with self.assertRaisesRegex(PatchBuildError, "临时 JSON 已存在"):
            create_patch("UPDATE", self.update_source, self.update_target, patch,
                         "old", "new", self.encoder)
        self.assertEqual(temp_sidecar.read_text(encoding="utf-8"), "user-owned temporary data")
        self.assertFalse(patch.exists())

    def test_invalid_patch_name_is_rejected(self) -> None:
        patch = self.root / "unsafe name.xdelta"
        with self.assertRaisesRegex(PatchBuildError, "安全 ASCII"):
            create_patch("UPDATE", self.update_source, self.update_target, patch,
                         "old", "new", self.encoder)


if __name__ == "__main__":
    unittest.main(verbosity=2)
