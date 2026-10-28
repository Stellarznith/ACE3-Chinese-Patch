from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).with_name("release_manifest.schema.json")
CHUNK_SIZE = 4 * 1024 * 1024


class PatchBuildError(Exception):
    pass


def file_identity(path: Path) -> dict[str, int | str]:
    try:
        size = path.stat().st_size
        if not path.is_file() or size < 1:
            raise PatchBuildError(f"输入必须是非空普通文件：{path}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
                digest.update(block)
        return {"size_bytes": size, "sha256": digest.hexdigest()}
    except OSError as exc:
        raise PatchBuildError(f"无法读取文件 {path}：{exc}") from exc


def iso_identity(path: Path, version: str) -> dict[str, int | str]:
    identity = file_identity(path)
    return {"version": version, "iso_size_bytes": identity["size_bytes"],
            "iso_sha256": identity["sha256"]}


def _version(executable: Path) -> str:
    try:
        result = subprocess.run([str(executable), "-V"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                check=False, shell=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PatchBuildError(f"无法查询 Xdelta3 版本：{exc}") from exc
    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode != 0 or not output:
        raise PatchBuildError(f"Xdelta3 版本查询失败（退出代码 {result.returncode}）：{output}")
    match = re.search(r"(?im)^\s*xdelta\s+version\s+([0-9]+(?:\.[0-9]+){1,3})\b", output)
    if not match:
        raise PatchBuildError(f"无法从 Xdelta3 输出识别编码器版本：{output}")
    return match.group(1)


def _validate_manifest(data: dict) -> None:
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        errors = sorted(Draft202012Validator(schema).iter_errors(data),
                        key=lambda error: list(map(str, error.path)))
    except Exception as exc:
        if isinstance(exc, PatchBuildError):
            raise
        raise PatchBuildError(f"无法加载或使用冻结的 v1 Schema：{exc}") from exc
    if errors:
        summary = "; ".join(f"/{'/'.join(map(str, e.path))}: {e.message}" for e in errors)
        raise PatchBuildError(f"生成的 release_manifest.json 不符合冻结 v1 Schema：{summary}")


def create_patch(patch_type: str, source: Path, target: Path, patch: Path,
                 source_version: str, target_version: str, xdelta: Path,
                 expected_source_sha256: str | None = None) -> Path:
    if patch_type not in ("FULL", "UPDATE"):
        raise PatchBuildError("patch_type 必须是 FULL 或 UPDATE。")
    if not source_version.strip() or not target_version.strip():
        raise PatchBuildError("source-version 和 target-version 必须是非空版本标签。")
    source = source.resolve(strict=True)
    target = target.resolve(strict=True)
    xdelta = xdelta.resolve(strict=True)
    if patch.suffix != ".xdelta" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.xdelta", patch.name, re.ASCII) or len(patch.name) > 120:
        raise PatchBuildError("输出补丁名必须符合冻结 v1 的安全 ASCII .xdelta 文件名规则。")
    patch = patch.resolve()
    if source == target or patch in (source, target):
        raise PatchBuildError("source、target、patch 必须是互不相同的文件。")
    sidecar = patch.with_suffix(".json")
    if patch.exists() or sidecar.exists():
        raise PatchBuildError("补丁或同名 JSON 已存在；不会覆盖任何文件。")
    if not xdelta.is_file() or (os.name != "nt" and not os.access(xdelta, os.X_OK)):
        raise PatchBuildError(f"Xdelta3 程序缺失或不可执行：{xdelta}")

    source_id = file_identity(source)
    if expected_source_sha256:
        expected = expected_source_sha256.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected, re.ASCII):
            raise PatchBuildError("expected-source-sha256 必须是 64 位十六进制 SHA-256。")
        if source_id["sha256"] != expected:
            raise PatchBuildError("输入 ISO SHA-256 与显式确认值不匹配。")
    elif patch_type == "FULL":
        raise PatchBuildError("FULL 必须显式提供已确认的原版 --expected-source-sha256。")

    target_before = file_identity(target)
    encoder_version = _version(xdelta)
    patch.parent.mkdir(parents=True, exist_ok=True)
    temp_patch = patch.with_name(patch.name + ".building")
    if temp_patch.exists():
        raise PatchBuildError(f"临时补丁已存在，不会覆盖：{temp_patch}")
    temp_patch_created = False
    temp_sidecar_created = False
    try:
        with temp_patch.open("xb"):
            temp_patch_created = True
        command = [str(xdelta), "-e", "-f", "-S", "none", "-s", str(source), str(target), str(temp_patch)]
        result = subprocess.run(command, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", check=False,
                                shell=False)
        if result.returncode != 0:
            raise PatchBuildError(f"Xdelta3 编码失败（退出代码 {result.returncode}）：{result.stderr.strip()}")
        if not temp_patch.is_file():
            raise PatchBuildError("Xdelta3 未生成预期的临时补丁文件。")
        patch_id = file_identity(temp_patch)
        if file_identity(source) != source_id:
            raise PatchBuildError("Xdelta3 编码期间 source ISO 发生变化。")
        if file_identity(target) != target_before:
            raise PatchBuildError("Xdelta3 编码期间 target ISO 发生变化。")
        manifest = {
            "schema_version": 1,
            "patch_type": patch_type,
            "source": {"version": source_version,
                       "iso_size_bytes": source_id["size_bytes"],
                       "iso_sha256": source_id["sha256"]},
            "target": {"version": target_version,
                       "iso_size_bytes": target_before["size_bytes"],
                       "iso_sha256": target_before["sha256"]},
            "patch": {"filename": patch.name,
                      "size_bytes": patch_id["size_bytes"],
                      "sha256": patch_id["sha256"],
                      "format": "xdelta3",
                      "format_version": encoder_version},
        }
        _validate_manifest(manifest)
        temp_sidecar = sidecar.with_name(sidecar.name + ".building")
        if temp_sidecar.exists():
            raise PatchBuildError(f"临时 JSON 已存在，不会覆盖：{temp_sidecar}")
        with temp_sidecar.open("x", encoding="utf-8", newline="\n") as stream:
            temp_sidecar_created = True
            stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        _validate_manifest(json.loads(temp_sidecar.read_text(encoding="utf-8")))
        patch_published = False
        sidecar_published = False
        try:
            os.link(temp_patch, patch)
            patch_published = True
            os.link(temp_sidecar, sidecar)
            sidecar_published = True
        except OSError:
            if sidecar_published and os.path.samefile(temp_sidecar, sidecar):
                sidecar.unlink(missing_ok=True)
            if patch_published and os.path.samefile(temp_patch, patch):
                patch.unlink(missing_ok=True)
            raise
        return sidecar
    except PatchBuildError:
        raise
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise PatchBuildError(f"生成补丁对失败：{exc}") from exc
    finally:
        if temp_patch_created:
            temp_patch.unlink(missing_ok=True)
        if temp_sidecar_created:
            sidecar.with_name(sidecar.name + ".building").unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按冻结 release_manifest v1 生成 Xdelta3 补丁和同名 JSON。")
    parser.add_argument("--type", choices=("FULL", "UPDATE"), required=True)
    parser.add_argument("--source", type=Path, required=True, help="FULL 的纯净原版 ISO；UPDATE 的旧版中文版 ISO")
    parser.add_argument("--source-version", required=True)
    parser.add_argument("--target", type=Path, required=True, help="已构建的目标 ISO")
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--patch", type=Path, required=True, help="输出 .xdelta 文件路径")
    parser.add_argument("--xdelta", type=Path, required=True, help="经核验的 Xdelta3 编码器可执行文件")
    parser.add_argument("--expected-source-sha256", help="显式核验来源 ISO SHA-256；FULL 必填")
    args = parser.parse_args(argv)
    try:
        sidecar = create_patch(args.type, args.source, args.target, args.patch,
                               args.source_version, args.target_version,
                               args.xdelta, args.expected_source_sha256)
    except (PatchBuildError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print(f"已生成并通过 release_manifest v1 Schema 校验：{sidecar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
