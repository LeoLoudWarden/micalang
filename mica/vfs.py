from __future__ import annotations

from .errors import MicaError


def normalize_path(path: object) -> str:
    if not isinstance(path, str):
        raise MicaError("path must be a string", kind="type_error")
    parts: list[str] = []
    for segment in path.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(segment)
    return "/" + "/".join(parts)


class VirtualFileSystem:
    """单个 VM 的私有文件空间：纯内存、纯文本，结构上无法触达宿主文件系统。"""

    def __init__(self, max_files: int = 1000, max_file_bytes: int = 1_048_576) -> None:
        self._files: dict[str, str] = {}
        self._max_files = max_files
        self._max_file_bytes = max_file_bytes

    def _is_dir(self, path: str) -> bool:
        prefix = path.rstrip("/") + "/"
        return any(file.startswith(prefix) for file in self._files)

    def read_file(self, path: object) -> str:
        normalized = normalize_path(path)
        if normalized in self._files:
            return self._files[normalized]
        if self._is_dir(normalized):
            raise MicaError(f"is a directory: {path}", kind="is_directory")
        raise MicaError(f"no such file: {path}", kind="file_not_found")

    def write_file(self, path: object, text: object) -> None:
        normalized = normalize_path(path)
        if not isinstance(text, str):
            raise MicaError("file content must be a string", kind="type_error")
        if normalized == "/" or self._is_dir(normalized):
            raise MicaError(f"is a directory: {path}", kind="is_directory")
        size = len(text.encode("utf-8"))
        if size > self._max_file_bytes:
            raise MicaError(
                "file size limit exceeded", kind="resource_exhausted"
            )
        if normalized not in self._files and len(self._files) >= self._max_files:
            raise MicaError(
                "file count limit exceeded", kind="resource_exhausted"
            )
        self._files[normalized] = text

    def list_dir(self, path: object) -> list[str]:
        normalized = normalize_path(path)
        if normalized in self._files:
            raise MicaError(f"not a directory: {path}", kind="not_directory")
        if normalized != "/" and not self._is_dir(normalized):
            raise MicaError(f"no such directory: {path}", kind="file_not_found")
        prefix = normalized.rstrip("/") + "/"
        names = {
            file[len(prefix):].split("/", 1)[0]
            for file in self._files
            if file.startswith(prefix)
        }
        return sorted(names)

    def path_exists(self, path: object) -> bool:
        normalized = normalize_path(path)
        return (
            normalized == "/"
            or normalized in self._files
            or self._is_dir(normalized)
        )
