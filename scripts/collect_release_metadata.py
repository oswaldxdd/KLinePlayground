import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path


LICENSE_BASENAMES = {
    "license", "license.txt", "license.md", "license.rst", "copying", "copying.txt",
    "notice", "notice.txt", "copyright", "copyright.txt",
}


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "package"


def collect_licenses(destination):
    license_root = destination / "THIRD_PARTY_LICENSES"
    license_root.mkdir(parents=True, exist_ok=True)
    copied = []
    for distribution in sorted(metadata.distributions(), key=lambda dist: (dist.metadata.get("Name") or "").lower()):
        package_name = distribution.metadata.get("Name") or "unknown"
        version = distribution.version
        copied_for_package = 0
        for entry in distribution.files or ():
            name = Path(str(entry)).name.lower()
            if name not in LICENSE_BASENAMES and not name.startswith("license.") and not name.startswith("copying."):
                continue
            source = Path(distribution.locate_file(entry))
            try:
                if not source.is_file() or source.stat().st_size > 2_000_000:
                    continue
                relative = Path(str(entry))
                target_dir = license_root / safe_name(package_name)
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / relative.name
                if target.exists():
                    continue
                shutil.copy2(source, target)
                copied.append(f"{package_name} {version}: THIRD_PARTY_LICENSES/{target.relative_to(license_root).as_posix()}")
                copied_for_package += 1
            except OSError:
                continue
        if not copied_for_package:
            expression = distribution.metadata.get("License-Expression") or ""
            license_text = distribution.metadata.get("License") or ""
            summary = expression or license_text.splitlines()[0] if license_text else expression or "license file not present in installed metadata"
            copied.append(f"{package_name} {version}: {summary}")
    (destination / "THIRD_PARTY_LICENSES.txt").write_text("\n".join(copied) + "\n", encoding="utf-8")
    return len(copied)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-dirty", action="store_true")
    parser.add_argument("--assistant-included", action="store_true")
    args = parser.parse_args()

    package_root = args.package_root.resolve()
    shutil.copy2(Path(__file__).resolve().parents[1] / "LICENSE", package_root / "LICENSE")
    shutil.copy2(Path(__file__).resolve().parents[1] / "requirements-release-win.lock.txt", package_root / "DEPENDENCIES.lock.txt")
    license_entries = collect_licenses(package_root)
    manifest = {
        "product": "KLinePlayground US Market K-Line Trainer",
        "version": args.version,
        "file_version": "3.3.0.1",
        "architecture": "Windows x64",
        "python": "3.12.10 x64",
        "source_commit": args.source_sha,
        "source_tree_dirty": args.source_dirty,
        "assistant_included": args.assistant_included,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "license_files_or_metadata_entries": license_entries,
        "webview": "pywebview / native Microsoft Edge WebView2 runtime",
    }
    (package_root / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
