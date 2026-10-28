from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "installer"))
from engine import (InstallCancelled, InstallError, PatchManifest, install,
                    valid_patch_filename)  # noqa: E402


MOCK_XDELTA = r'''
import pathlib, shutil, sys, time
mode = sys.argv[1]
source, patch, target = map(pathlib.Path, sys.argv[2:5])
if mode == "success":
    shutil.copyfile(patch, target)
elif mode == "fail":
    target.write_bytes(b"partial")
    print("mock failure", file=sys.stderr)
    raise SystemExit(2)
elif mode == "slow":
    target.write_bytes(b"partial")
    time.sleep(30)
'''


class InstallerProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.package = self.root / "patch package with 空格"
        self.package.mkdir()
        self.runner_file = self.root / "mock_runner.py"
        self.runner_file.write_text(MOCK_XDELTA, encoding="utf-8")
        self.exe = self.package / "xdelta3.exe"
        self.exe.write_bytes(b"mock executable")
        self.patch_type = "FULL"
        self.source_version = "TEST original"
        self.target_version = "TEST Chinese"
        self.source_bytes = b"input image bytes\x00" * 16
        self.target_bytes = b"translated image bytes\xff" * 16
        self.patch_bytes = self.target_bytes
        self.patch_name = "ACE3_CN_v1.0.0_full.xdelta"
        self.patch = self.package / self.patch_name
        self.source = self.root / "输入 ISO.iso"
        self.output = self.root / "输出 ISO.iso"
        self.patch.write_bytes(self.patch_bytes)
        self.source.write_bytes(self.source_bytes)
        self.sidecar = self.patch.with_suffix(".json")
        self._write_manifest()

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _sha(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _manifest_data(self) -> dict:
        return {
            "schema_version": 1,
            "patch_type": self.patch_type,
            "source": {"version": self.source_version,
                       "iso_size_bytes": len(self.source_bytes),
                       "iso_sha256": self._sha(self.source_bytes)},
            "target": {"version": self.target_version,
                       "iso_size_bytes": len(self.target_bytes),
                       "iso_sha256": self._sha(self.target_bytes)},
            "patch": {"filename": self.patch_name,
                      "size_bytes": len(self.patch_bytes),
                      "sha256": self._sha(self.patch_bytes),
                      "format": "xdelta3",
                      "format_version": "3.1.0 encoder"},
        }

    def _write_manifest(self, data: dict | None = None) -> None:
        self.sidecar.write_text(json.dumps(data or self._manifest_data(), ensure_ascii=False), encoding="utf-8")

    def _runner(self, mode: str = "success") -> list[str]:
        return [sys.executable, str(self.runner_file), mode]

    def _install(self, mode: str = "success", patch: Path | None = None):
        return install(patch or self.patch, self.source, self.output, runner=self._runner(mode))

    def test_full_patch_success_verifies_all_three_files_and_preserves_source(self) -> None:
        before = self._sha(self.source.read_bytes())
        result = self._install()
        self.assertEqual(result, self.output)
        self.assertEqual(self._sha(result.read_bytes()), self._sha(self.target_bytes))
        self.assertEqual(self._sha(self.source.read_bytes()), before)
        self.assertFalse(self.output.with_name(self.output.name + ".part").exists())
        self.assertEqual(PatchManifest.load_for_patch(self.patch).patch_type, "FULL")

    def test_update_patch_success_uses_exact_source_fingerprint(self) -> None:
        self.patch_type = "UPDATE"
        self.source_version = "TEST old Chinese"
        self.target_version = "TEST new Chinese"
        self._write_manifest()
        self._install()
        self.assertEqual(self.output.read_bytes(), self.target_bytes)
        self.assertEqual(PatchManifest.load_for_patch(self.patch).patch_type, "UPDATE")

    def test_approved_example_names_match_schema_and_runtime_pattern(self) -> None:
        schema = json.loads((ROOT / "installer" / "release_manifest.schema.json").read_text(encoding="utf-8"))
        pattern = schema["$defs"]["patch"]["properties"]["filename"]["pattern"]
        compiled = re.compile(pattern, re.ASCII)
        for name in ("ACE3_CN_v1.0.0_full.xdelta", "ACE3_CN_v1.0.0_to_v1.0.1_update.xdelta"):
            self.assertTrue(compiled.fullmatch(name), name)
            self.assertTrue(valid_patch_filename(name), name)
        for name in ("../evil.xdelta", "..\\evil.xdelta", "C:evil.xdelta", "has space.xdelta",
                     "含中文.xdelta", "bad/name.xdelta", "bad\\name.xdelta", "x.xdelta.exe", "-bad.xdelta", "evil.xdelta\n"):
            self.assertFalse(compiled.fullmatch(name), name)
            self.assertFalse(valid_patch_filename(name), name)

    def test_both_published_examples_validate_against_schema(self) -> None:
        schema = json.loads((ROOT / "installer" / "release_manifest.schema.json").read_text(encoding="utf-8"))

        def validate(value, rule, root_schema, path="$", errors=None):
            errors = errors if errors is not None else []
            if "$ref" in rule:
                target = root_schema
                for part in rule["$ref"].removeprefix("#/").split("/"):
                    target = target[part]
                validate(value, target, root_schema, path, errors)
                return errors
            if "type" in rule:
                kind = rule["type"]
                type_ok = {"object": isinstance(value, dict), "string": isinstance(value, str),
                           "integer": type(value) is int}.get(kind, True)
                if not type_ok:
                    errors.append(f"{path}: expected {kind}")
                    return errors
            if "const" in rule and value != rule["const"]:
                errors.append(f"{path}: expected const {rule['const']!r}")
            if "enum" in rule and value not in rule["enum"]:
                errors.append(f"{path}: not in enum")
            if isinstance(value, dict):
                missing = set(rule.get("required", [])) - set(value)
                if missing:
                    errors.append(f"{path}: missing {sorted(missing)}")
                properties = rule.get("properties", {})
                extras = set(value) - set(properties)
                if rule.get("additionalProperties") is False and extras:
                    errors.append(f"{path}: additional properties {sorted(extras)}")
                for key, child in value.items():
                    if key in properties:
                        validate(child, properties[key], root_schema, f"{path}.{key}", errors)
            if isinstance(value, str):
                if len(value) < rule.get("minLength", 0) or len(value) > rule.get("maxLength", float("inf")):
                    errors.append(f"{path}: invalid string length")
                if "pattern" in rule and re.search(rule["pattern"], value, re.ASCII) is None:
                    errors.append(f"{path}: pattern mismatch")
            if type(value) is int and (value < rule.get("minimum", float("-inf"))
                                      or value > rule.get("maximum", float("inf"))):
                errors.append(f"{path}: outside numeric bounds")
            return errors

        for example in (ROOT / "installer" / "examples").glob("*.json"):
            data = json.loads(example.read_text(encoding="utf-8"))
            self.assertEqual(validate(data, schema, schema), [], example.name)
            self.assertEqual(data["patch"]["format"], "xdelta3")
            self.assertIn("encoder", data["patch"]["format_version"])

    def test_wrong_source_hash_rejected(self) -> None:
        self.source.write_bytes(b"unknown source")
        with self.assertRaisesRegex(InstallError, "输入 ISO 不匹配"):
            self._install()
        self.assertFalse(self.output.exists())

    def test_source_hash_mismatch_with_same_size_rejected_before_runner(self) -> None:
        self.source.write_bytes(b"X" * len(self.source_bytes))
        with self.assertRaisesRegex(InstallError, "SHA-256"):
            self._install()

    def test_patch_hash_mismatch_rejected(self) -> None:
        self.patch.write_bytes(b"tampered")
        with self.assertRaisesRegex(InstallError, "补丁文件大小"):
            self._install()

    def test_patch_hash_mismatch_with_same_size_rejected(self) -> None:
        self.patch.write_bytes(b"X" * len(self.patch_bytes))
        with self.assertRaisesRegex(InstallError, "补丁 SHA-256"):
            self._install()

    def test_target_hash_mismatch_never_committed(self) -> None:
        data = self._manifest_data()
        data["target"]["iso_sha256"] = "0" * 64
        self._write_manifest(data)
        with self.assertRaisesRegex(InstallError, "目标 ISO SHA-256"):
            self._install()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_name(self.output.name + ".part").exists())

    def test_existing_output_is_never_overwritten(self) -> None:
        self.output.write_bytes(b"user data")
        with self.assertRaisesRegex(InstallError, "输出文件已存在"):
            self._install()
        self.assertEqual(self.output.read_bytes(), b"user data")

    def test_existing_part_is_preserved(self) -> None:
        part = self.output.with_name(self.output.name + ".part")
        part.write_bytes(b"user temp")
        with self.assertRaisesRegex(InstallError, "临时文件已存在"):
            self._install()
        self.assertEqual(part.read_bytes(), b"user temp")

    def test_xdelta_failure_cleans_partial_file(self) -> None:
        with self.assertRaisesRegex(InstallError, "还原失败"):
            self._install("fail")
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_name(self.output.name + ".part").exists())

    def test_cancel_terminates_runner_and_cleans_partial_file(self) -> None:
        cancel = threading.Event()
        timer = threading.Timer(0.3, cancel.set)
        timer.start()
        try:
            with self.assertRaises(InstallCancelled):
                install(self.patch, self.source, self.output, cancel=cancel, runner=self._runner("slow"))
        finally:
            timer.cancel()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_name(self.output.name + ".part").exists())

    def test_unknown_fields_and_versions_are_rejected(self) -> None:
        data = self._manifest_data()
        data["surprise"] = True
        self._write_manifest(data)
        with self.assertRaisesRegex(InstallError, "字段不符合"):
            self._install()
        data = self._manifest_data()
        data["schema_version"] = 2
        self._write_manifest(data)
        with self.assertRaisesRegex(InstallError, "仅支持 schema_version 1"):
            self._install()

    def test_patch_filename_mismatch_is_rejected(self) -> None:
        data = self._manifest_data()
        data["patch"]["filename"] = "different.xdelta"
        self._write_manifest(data)
        with self.assertRaisesRegex(InstallError, "文件名与元数据"):
            self._install()

    def test_unfrozen_or_invalid_filename_metadata_is_rejected(self) -> None:
        data = self._manifest_data()
        data["patch"]["filename"] = "../patch.xdelta"
        self._write_manifest(data)
        with self.assertRaisesRegex(InstallError, "安全 ASCII"):
            PatchManifest.load_for_patch(self.patch)

    def test_missing_sidecar_and_patch_are_rejected(self) -> None:
        self.sidecar.unlink()
        with self.assertRaisesRegex(InstallError, "无法读取补丁元数据"):
            self._install()
        self._write_manifest()
        self.patch.unlink()
        with self.assertRaises(InstallError):
            install(self.patch, self.source, self.output, runner=self._runner())

    def test_input_and_output_cannot_be_same_path(self) -> None:
        with self.assertRaises(InstallError):
            install(self.patch, self.source, self.source, runner=self._runner())


if __name__ == "__main__":
    unittest.main(verbosity=2)
