#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[2] == "--help":
        print(f"fake help for {sys.argv[1]}")
        return 0

    state_path = Path(os.environ.get("FAKE_OV_STATE", "/tmp/fake-ov.json"))
    state = _load_state(state_path)
    state.setdefault("commands", []).append(sys.argv[1:])
    _save_state(state_path, state)

    if _should_fail_once(state_path, sys.argv[1:]):
        print("temporary failure", file=sys.stderr)
        return 7

    args = sys.argv[1:]
    if args[:1] == ["stat"]:
        return _stat(state_path, args)
    if args[:1] == ["add-resource"]:
        return _add_resource(state_path, args)
    if args[:1] == ["write"]:
        return _write(state_path, args)
    if args[:1] == ["rm"]:
        return _rm(state_path, args)
    if args[:1] == ["set-tags"]:
        return _set_tags(state_path, args)
    if args[:1] == ["find"]:
        return _find(args)
    if args[:1] == ["health"]:
        return _health(args)
    if args[:1] == ["wait"]:
        return _wait(args)

    print(f"unexpected args: {sys.argv}", file=sys.stderr)
    return 2


def _stat(state_path: Path, args: list[str]) -> int:
    uri = args[1]
    state = _load_state(state_path)
    resources = _resources(state)
    resource = resources.get(uri) or _child_resource(resources, uri)
    if resource is None:
        print(json.dumps({"error": "not found", "uri": uri}), file=sys.stderr)
        return 4
    print(json.dumps({"ok": True, "result": {"uri": uri, **resource}}))
    return 0


def _add_resource(state_path: Path, args: list[str]) -> int:
    path = args[1]
    uri = args[args.index("--to") + 1]
    _assert_tail(args, ["--no-progress", "-o", "json"])
    state = _load_state(state_path)
    resources = _resources(state)
    if uri in resources:
        print(json.dumps({"error": "already exists", "uri": uri}), file=sys.stderr)
        return 5
    child_name = Path(path).name
    child_uri = f"{uri.rstrip('/')}/{child_name}"
    resources[uri] = {
        "path": path,
        "isDir": True,
        "writes": 0,
        "removes": 0,
        "tags": {},
        "children": {child_uri: {"path": path, "isDir": False, "writes": 0, "tags": {}}},
    }
    _save_state(state_path, state)
    print(json.dumps({"uri": uri}))
    return 0


def _write(state_path: Path, args: list[str]) -> int:
    uri = args[1]
    path = args[args.index("--from-file") + 1]
    _assert_tail(args, ["-o", "json"])
    if "--no-progress" in args:
        print("Unexpected argument: --no-progress", file=sys.stderr)
        return 2
    state = _load_state(state_path)
    resources = _resources(state)
    resource = resources.get(uri) or _child_resource(resources, uri)
    if resource is None:
        print(json.dumps({"error": "not found", "uri": uri}), file=sys.stderr)
        return 4
    if resource.get("isDir") is True:
        print("write only supports existing files, got directory", file=sys.stderr)
        return 6
    resource["path"] = path
    resource["writes"] = int(resource.get("writes", 0)) + 1
    _save_state(state_path, state)
    print(json.dumps({"path": uri}))
    return 0


def _rm(state_path: Path, args: list[str]) -> int:
    uri = args[1]
    if uri.endswith("/") or "*" in uri:
        print(json.dumps({"error": "recursive deletion refused", "uri": uri}), file=sys.stderr)
        return 9
    recursive = "--recursive" in args
    expected_tail = ["--recursive", "--wait", "--timeout", "5", "-o", "json"] if recursive else ["--wait", "--timeout", "5", "-o", "json"]
    _assert_tail(args, expected_tail)
    state = _load_state(state_path)
    resources = _resources(state)
    if uri not in resources:
        print(json.dumps({"error": "not found", "uri": uri}), file=sys.stderr)
        return 4
    if resources[uri].get("isDir") is True and not recursive:
        print(json.dumps({"error": "Cannot remove directory without --recursive", "uri": uri}), file=sys.stderr)
        return 10
    if resources[uri].get("isDir") is not True and recursive:
        print(json.dumps({"error": "recursive deletion only allowed for directories", "uri": uri}), file=sys.stderr)
        return 11
    del resources[uri]
    state.setdefault("removed", []).append(uri)
    _save_state(state_path, state)
    print(json.dumps({"uri": uri, "removed": True}))
    return 0


def _set_tags(state_path: Path, args: list[str]) -> int:
    uri = args[1]
    tags = args[args.index("--tags") + 1]
    if args[args.index("--mode") + 1] != "replace":
        print("expected replace mode", file=sys.stderr)
        return 2
    state = _load_state(state_path)
    resources = _resources(state)
    if uri not in resources:
        print(json.dumps({"error": "not found", "uri": uri}), file=sys.stderr)
        return 4
    resources[uri]["tags"] = dict(part.split("=", 1) for part in tags.split(","))
    _save_state(state_path, state)
    print(json.dumps({"uri": uri, "tags": resources[uri]["tags"]}))
    return 0


def _find(args: list[str]) -> int:
    query = " ".join(args)
    results = []
    if "DEMO_PROJECT" in query or "Rugby" in query:
        results.append({"uri": "viking://resources/brain/brain_demo_project.md", "tags": {"source_id": "brain:demo_project"}})
    if "Ghostty" in query:
        results.append({"path": "viking://resources/brain/brain_ghostty.txt", "tags": ["source_id=brain:ghostty"]})
    if "OpenViking" in query or "MEMORY" in query:
        results.append({"id": "viking://resources/brain/brain_workflow.md", "tags": [{"key": "source_id", "value": "brain:workflow"}]})
    print(json.dumps({"results": results}))
    return 0


def _health(args: list[str]) -> int:
    if args != ["health", "-o", "json"]:
        print(f"unexpected health args: {args}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "ok"}))
    return 0


def _wait(args: list[str]) -> int:
    if len(args) != 5 or args[0] != "wait" or args[1] != "--timeout" or args[3:] != ["-o", "json"]:
        print(f"unexpected wait args: {args}", file=sys.stderr)
        return 2
    if os.environ.get("FAKE_OV_WAIT_FAIL"):
        print(json.dumps({"error": "wait failed"}), file=sys.stderr)
        return 8
    print(json.dumps({"status": "complete"}))
    return 0


def _assert_tail(args: list[str], expected: list[str]) -> None:
    if args[-len(expected) :] != expected:
        raise SystemExit(f"expected trailing args {expected}, got {args}")


def _should_fail_once(state_path: Path, args: list[str]) -> bool:
    if not os.environ.get("FAKE_OV_FAIL_ONCE") or args[:1] in (["stat"], ["find"]):
        return False
    marker = state_path.with_suffix(".fail-once")
    if marker.exists():
        return False
    marker.write_text("failed", encoding="utf-8")
    return True


def _resources(state: dict[str, Any]) -> dict[str, Any]:
    resources = state.setdefault("resources", {})
    if not isinstance(resources, dict):
        raise SystemExit("invalid fake state")
    return resources


def _child_resource(resources: dict[str, Any], uri: str) -> dict[str, Any] | None:
    for resource in resources.values():
        children = resource.get("children", {})
        if isinstance(children, dict) and uri in children:
            child = children[uri]
            if isinstance(child, dict):
                return child
    return None


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"commands": [], "resources": {}, "removed": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
