from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

CHUNK_SIZE = 4 * 1024 * 1024
PATCH_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.xdelta$", re.ASCII)
MAX_PATCH_FILENAME = 120
Progress = Callable[[str, int | None, str], None]


class InstallError(Exception):
    pass


class InstallCancelled(InstallError):
    pass


@dataclass(frozen=True)
class ImageIdentity:
    version: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class PatchManifest:
    schema_version: int
    patch_type: str
    source: ImageIdentity
    target: ImageIdentity
    patch_filename: str
    patch_size_bytes: int
    patch_sha256: str
    format: str
    encoder_version: str

    @classmethod
    def load_for_patch(cls, patch_path: Path) -> "PatchManifest":
        sidecar = patch_path.with_suffix(".json")
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise InstallError(f"无法读取补丁元数据 {sidecar.name}：{exc}") from exc
        if not isinstance(data, dict) or set(data) != {"schema_version", "patch_type", "source", "target", "patch"}:
            raise InstallError("补丁元数据字段不符合 release_manifest.json v1。")
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise InstallError("不支持该补丁元数据协议版本；仅支持 schema_version 1。")
        if data["patch_type"] not in ("FULL", "UPDATE"):
            raise InstallError("patch_type 必须为 FULL 或 UPDATE。")

        def image(value: object, field: str) -> ImageIdentity:
            if not isinstance(value, dict) or set(value) != {"version", "iso_size_bytes", "iso_sha256"}:
                raise InstallError(f"{field} 字段不符合 release_manifest.json v1。")
            version, size, digest = value["version"], value["iso_size_bytes"], value["iso_sha256"]
            if not isinstance(version, str) or not version.strip():
                raise InstallError(f"{field}.version 必须是非空字符串。")
            if type(size) is not int or size < 1:
                raise InstallError(f"{field}.iso_size_bytes 必须是正整数。")
            if not _valid_sha256(digest):
                raise InstallError(f"{field}.iso_sha256 必须是 64 位小写 SHA-256。")
            return ImageIdentity(version, size, digest)

        patch = data["patch"]
        if not isinstance(patch, dict) or set(patch) != {"filename", "size_bytes", "sha256", "format", "format_version"}:
            raise InstallError("patch 字段不符合 release_manifest.json v1。")
        filename = patch["filename"]
        if not valid_patch_filename(filename):
            raise InstallError("patch.filename 不符合 v1 安全 ASCII 文件名规则。")
        size, digest = patch["size_bytes"], patch["sha256"]
        if type(size) is not int or size < 1:
            raise InstallError("patch.size_bytes 必须是正整数。")
        if not _valid_sha256(digest):
            raise InstallError("patch.sha256 必须是 64 位小写 SHA-256。")
        if patch["format"] != "xdelta3":
            raise InstallError("v1 仅支持 patch.format 为 xdelta3。")
        encoder_version = patch["format_version"]
        if not isinstance(encoder_version, str) or not encoder_version.strip():
            raise InstallError("patch.format_version 必须记录生成补丁的 Xdelta3 编码器版本。")
        return cls(1, data["patch_type"], image(data["source"], "source"),
                   image(data["target"], "target"), filename, size, digest,
                   patch["format"], encoder_version)


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value, re.ASCII) is not None


def valid_patch_filename(value: object) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= MAX_PATCH_FILENAME
            and PATCH_FILENAME_RE.fullmatch(value) is not None
            and value not in (".", ".."))


def sha256_file(path: Path, cancel: threading.Event | None = None,
                progress: Progress | None = None, stage: str = "正在校验文件……") -> str:
    try:
        size = path.stat().st_size
        if not path.is_file():
            raise InstallError(f"不是普通文件：{path}")
        digest = hashlib.sha256()
        done = 0
        with path.open("rb") as stream:
            while True:
                if cancel and cancel.is_set():
                    raise InstallCancelled("安装已取消")
                block = stream.read(CHUNK_SIZE)
                if not block:
                    break
                digest.update(block)
                done += len(block)
                if progress:
                    progress(stage, done * 100 // size if size else 100, f"{done:,} / {size:,} 字节")
        return digest.hexdigest()
    except InstallError:
        raise
    except OSError as exc:
        raise InstallError(f"无法读取文件：{path}（{exc}）") from exc


def _report(progress: Progress | None, stage: str, percent: int | None, detail: str = "") -> None:
    if progress:
        progress(stage, percent, detail)


def _run_xdelta(executable: Path, patch: Path, source: Path, target: Path,
                cancel: threading.Event, progress: Progress | None,
                runner: Sequence[str] | None = None) -> None:
    command = (list(runner) + [str(source), str(patch), str(target)]) if runner is not None else [
        str(executable), "-d", "-s", str(source), str(patch), str(target)
    ]
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as error_log:
        try:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=error_log, shell=False)
        except OSError as exc:
            raise InstallError(f"无法启动 Xdelta3：{exc}") from exc
        started = time.monotonic()
        while process.poll() is None:
            if cancel.wait(0.2):
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise InstallCancelled("安装已取消")
            _report(progress, "正在应用中文化补丁……", None, f"已用时 {int(time.monotonic() - started)} 秒")
        error_log.seek(0)
        stderr = error_log.read()
        if process.returncode != 0:
            raise InstallError(f"Xdelta3 还原失败（退出代码 {process.returncode}）：{stderr.strip() or '未提供错误详情'}")


def _commit_no_replace(part: Path, destination: Path) -> None:
    try:
        if os.name == "nt":
            os.rename(part, destination)
        else:
            os.link(part, destination)
            part.unlink()
    except FileExistsError as exc:
        raise InstallError(f"输出文件已被创建，不会覆盖：{destination}") from exc
    except OSError as exc:
        raise InstallError(f"无法安全提交输出文件（未覆盖现有文件）：{exc}") from exc


def install(patch_path: Path, source: Path, destination: Path,
            cancel: threading.Event | None = None, progress: Progress | None = None,
            xdelta_executable: Path | None = None,
            runner: Sequence[str] | None = None) -> Path:
    cancel = cancel or threading.Event()
    try:
        patch_path = Path(patch_path).resolve(strict=True)
        source = Path(source).resolve(strict=True)
    except OSError as exc:
        raise InstallError(f"所选输入文件不存在或无法读取：{exc}") from exc
    destination = Path(destination).resolve()
    if not valid_patch_filename(patch_path.name):
        raise InstallError("所选补丁文件名不符合 v1 安全 ASCII 文件名规则。")
    if patch_path.suffix != ".xdelta" or not patch_path.is_file():
        raise InstallError("请选择有效的 .xdelta 补丁文件。")
    if not source.is_file() or not os.access(source, os.R_OK):
        raise InstallError("输入 ISO 不存在、不是普通文件或无法读取。")
    if destination.exists():
        raise InstallError(f"输出文件已存在，不会覆盖：{destination}")
    if os.path.normcase(str(source)) == os.path.normcase(str(destination)):
        raise InstallError("输出位置不能与输入 ISO 相同。")
    manifest = PatchManifest.load_for_patch(patch_path)
    if patch_path.name != manifest.patch_filename:
        raise InstallError("所选补丁文件名与元数据 patch.filename 不一致。")
    executable = Path(xdelta_executable) if xdelta_executable else patch_path.parent / "xdelta3.exe"
    if runner is None and (not executable.is_file() or (os.name != "nt" and not os.access(executable, os.X_OK))):
        raise InstallError(f"Xdelta3 程序缺失或不可执行：{executable}")
    try:
        source_size = source.stat().st_size
        patch_size = patch_path.stat().st_size
    except OSError as exc:
        raise InstallError(f"无法读取输入文件属性：{exc}") from exc
    if source_size != manifest.source.size_bytes:
        raise InstallError(f"输入 ISO 不匹配。元数据来源版本：{manifest.source.version}。")
    if patch_size != manifest.patch_size_bytes:
        raise InstallError("补丁文件大小与元数据不匹配。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    required = manifest.target.size_bytes + 64 * 1024 * 1024
    free = shutil.disk_usage(destination.parent).free
    if free < required:
        raise InstallError(f"目标磁盘空间不足：需要约 {required:,} 字节，可用 {free:,} 字节。")
    if not os.access(destination.parent, os.W_OK):
        raise InstallError("目标目录没有写入权限。")
    part = destination.with_name(destination.name + ".part")
    if part.exists():
        raise InstallError(f"临时文件已存在，为避免删除用户文件已停止：{part}")
    try:
        _report(progress, "正在校验补丁文件……", 0, "")
        patch_hash = sha256_file(patch_path, cancel, progress, "正在校验补丁文件……")
        if patch_hash != manifest.patch_sha256:
            raise InstallError("补丁 SHA-256 与元数据不匹配。")
        _report(progress, f"正在校验输入 ISO（{manifest.source.version}）……", 0, "")
        source_hash = sha256_file(source, cancel, progress, f"正在校验输入 ISO（{manifest.source.version}）……")
        if source_hash != manifest.source.sha256:
            raise InstallError("输入 ISO SHA-256 与补丁支持的来源版本不匹配。")
        if cancel.is_set():
            raise InstallCancelled("安装已取消")
        _report(progress, f"正在生成目标 ISO（{manifest.target.version}）……", None, "")
        _run_xdelta(executable, patch_path, source, part, cancel, progress, runner)
        if not part.is_file() or part.stat().st_size != manifest.target.size_bytes:
            raise InstallError("还原结果大小与元数据不匹配，未提交输出文件。")
        _report(progress, "正在校验目标 ISO……", 0, "")
        target_hash = sha256_file(part, cancel, progress, "正在校验目标 ISO……")
        if target_hash != manifest.target.sha256:
            raise InstallError("目标 ISO SHA-256 与元数据不匹配，未提交输出文件。")
        if source.stat().st_size != source_size or sha256_file(source, cancel) != source_hash:
            raise InstallError("输入 ISO 在校验后内容发生变化，已中止安装。")
        if cancel.is_set():
            raise InstallCancelled("安装已取消")
        _commit_no_replace(part, destination)
        _report(progress, "安装完成。", 100, f"{manifest.target.version}：{destination}")
        return destination
    except Exception:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise
