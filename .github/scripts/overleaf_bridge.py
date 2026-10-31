"""One-way, revision-checked import from the specified Overleaf project."""

import json
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


PROJECT = "69e740f8b52c29cb561f584f"
REPOSITORY = "plfm-iis/CQ-automata"
REMOTE = f"https://git@git.overleaf.com/{PROJECT}"
STATE = ".github/overleaf-state.json"
METADATA_FILES = {"AGENTS.md", "PAPER_CONTEXT.md", "CLAIMS.md"}
SHA = re.compile(r"[0-9a-f]{40}\Z")


class Refused(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def git(repo, *args, check=True):
    result = subprocess.run(
        ["git", *args], cwd=repo, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if check and result.returncode:
        raise Refused(f"git {args[0]} failed: {result.stderr.decode(errors='replace').strip()}")
    return result


def output(repo, *args):
    return git(repo, *args).stdout.decode("utf-8").strip()


def paper_path(path):
    return path.split("/")[0] not in {".github", ".ai"} and path not in METADATA_FILES


def manifest(repo, ref="HEAD"):
    result = {}
    for entry in git(repo, "ls-tree", "-rz", ref).stdout.split(b"\0"):
        if not entry:
            continue
        attributes, encoded_path = entry.split(b"\t", 1)
        mode, kind, oid = attributes.decode("ascii").split()
        path = encoded_path.decode("utf-8")
        if not paper_path(path):
            continue
        require(not path.startswith("/") and all(p not in {"", ".", "..", ".git"} for p in path.split("/")), "Unsafe paper path.")
        require(kind == "blob" and mode == "100644", f"Unsupported paper file mode at {path}: {mode}. Only regular non-executable files are supported.")
        result[path] = {"oid": oid, "mode": mode}
    return result


def read_json(repo, ref, path):
    if git(repo, "cat-file", "-e", f"{ref}:{path}", check=False).returncode:
        return None
    try:
        value = json.loads(git(repo, "show", f"{ref}:{path}").stdout)
    except (ValueError, UnicodeDecodeError) as error:
        raise Refused(f"Invalid JSON in {path}.") from error
    require(isinstance(value, dict) and value.get("schema") == 1, f"Unsupported schema in {path}.")
    return value


def remote_head(repo, remote="origin", ref="refs/heads/main"):
    lines = output(repo, "ls-remote", remote, ref).splitlines()
    require(len(lines) == 1, "Remote ref did not resolve uniquely.")
    return lines[0].split()[0]


def clean(repo):
    require(not output(repo, "status", "--porcelain", "--untracked-files=all"), "Working tree is not clean; no files were changed.")


def ancestor(repo, older, newer):
    return git(repo, "merge-base", "--is-ancestor", older, newer, check=False).returncode == 0


def replace_paper(source, destination, source_manifest):
    """Copy only tracked paper blobs; preserve all GitHub bridge metadata."""
    old = manifest(destination)
    for path in sorted(old, key=len, reverse=True):
        if path not in source_manifest:
            target = destination / path
            target.unlink()
            parent = target.parent
            while parent != destination:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
    for path, item in source_manifest.items():
        if old.get(path) == item:
            continue
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(git(source, "cat-file", "blob", item["oid"]).stdout)
        target.chmod(0o644)


def commit(repo, message, allowed_paths):
    # The source snapshot may legitimately track files matched by .gitignore.
    git(repo, "add", "-A", "-f")
    staged = set(git(repo, "diff", "--cached", "--name-only", "-z").stdout.decode("utf-8").split("\0")) - {""}
    require(staged <= allowed_paths, "Unexpected files were staged; refusing to commit.")
    if not staged:
        return False
    git(repo, "-c", "user.name=paper-sync-bot", "-c", "user.email=paper-sync-bot@users.noreply.github.com", "commit", "-m", message)
    return True


class Bridge:
    def __init__(self, repo, remote=REMOTE):
        self.repo = Path(repo).resolve()
        self.remote = remote
        self.start = output(self.repo, "rev-parse", "HEAD")

    def check_github(self):
        require(remote_head(self.repo) == self.start, "GitHub main changed; refresh and review again.")

    def state(self):
        state = read_json(self.repo, self.start, STATE)
        if state is not None:
            require(state.get("project_id") == PROJECT, "State belongs to another Overleaf project.")
            require(isinstance(state.get("overleaf_revision"), str) and SHA.fullmatch(state["overleaf_revision"]), "Invalid Overleaf revision in state.")
            require(isinstance(state.get("paper_manifest"), dict), "Missing paper manifest in state.")
        return state

    def save_state(self, revision, files):
        target = self.repo / STATE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"schema": 1, "repository": REPOSITORY, "project_id": PROJECT, "overleaf_revision": revision, "paper_manifest": files, "github_source_commit": self.start, "fetched_at": datetime.now(timezone.utc).isoformat(), "run_url": os.environ.get("BRIDGE_RUN_URL")}, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def run(self):
        clean(self.repo)
        self.check_github()
        print(f"Checking inbound refresh at GitHub {self.start}, Overleaf project {PROJECT}.")
        with tempfile.TemporaryDirectory(prefix="overleaf-bridge-") as directory:
            overleaf = Path(directory) / "overleaf"
            git(self.repo, "clone", "--quiet", "--no-checkout", self.remote, str(overleaf))
            revision = output(overleaf, "rev-parse", "HEAD")
            ol_files = manifest(overleaf)
            gh_files = manifest(self.repo)
            state = self.state()
            self.refresh(overleaf, revision, ol_files, gh_files, state)

    def refresh(self, overleaf, revision, ol_files, gh_files, state):
        if state is None:
            require(gh_files == ol_files, "Initial GitHub/Overleaf paper snapshots differ. No paper was written; compare and approve the differences first.")
        else:
            require(gh_files == state["paper_manifest"], "GitHub has paper edits not confirmed on Overleaf. Refresh would overwrite them; refusing.")
            require(ancestor(overleaf, state["overleaf_revision"], revision), "Overleaf baseline is not an ancestor of the current project; refusing replacement.")
            require(manifest(overleaf, state["overleaf_revision"]) == state["paper_manifest"], "Stored baseline does not match Overleaf history.")
        require("main.tex" in ol_files and "macros.tex" in ol_files and "literature.bib" in ol_files, "Required manuscript files are missing; refusing import.")
        deleted = set(gh_files) - set(ol_files)
        require(len(deleted) <= max(1, len(gh_files) // 4), "Large deletion detected; compare and review before importing.")
        require(remote_head(overleaf, ref="HEAD") == revision, "Overleaf changed during refresh; run refresh again.")
        self.check_github()
        replace_paper(overleaf, self.repo, ol_files)
        self.save_state(revision, ol_files)
        changed = commit(self.repo, "[overleaf-sync] Refresh paper and record Overleaf baseline", set(gh_files) | set(ol_files) | {STATE})
        require(manifest(self.repo) == ol_files, "Git staging altered the imported paper snapshot; no GitHub push.")
        if changed:
            self.check_github()
            require(remote_head(overleaf, ref="HEAD") == revision, "Overleaf changed before refresh completed; run refresh again.")
            git(self.repo, "push", "origin", "HEAD:refs/heads/main")
        require(remote_head(self.repo) == output(self.repo, "rev-parse", "HEAD"), "GitHub changed after refresh; check the latest state again.")
        require(remote_head(overleaf, ref="HEAD") == revision, "Overleaf changed after refresh; run refresh again before substantive work.")
        final_sha = output(self.repo, "rev-parse", "HEAD")
        summary = f"Refresh confirmed Overleaf {revision}; GitHub {final_sha}; {len(ol_files)} paper files.\n"
        print(summary)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
                stream.write(summary)


def main():
    require(len(sys.argv) == 2 and sys.argv[1] == "refresh", "Usage: overleaf_bridge.py refresh")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "Wrong GitHub repository.")
        require(os.environ.get("GITHUB_REF") == "refs/heads/main", "Bridge runs only on main.")
        require(output(Path.cwd(), "rev-parse", "HEAD") == os.environ.get("GITHUB_SHA"), "Checkout does not match the triggering commit.")
    require(bool(os.environ.get("OVERLEAF_GIT_TOKEN")), "OVERLEAF_GIT_TOKEN is unavailable to this repository.")
    with tempfile.TemporaryDirectory(prefix="overleaf-askpass-") as directory:
        askpass = Path(directory) / "askpass.sh"
        askpass.write_text('#!/bin/sh\ncase "$1" in\n  *Username*) printf "%s\\n" git ;;\n  *Password*) printf "%s\\n" "$OVERLEAF_GIT_TOKEN" ;;\n  *) exit 1 ;;\nesac\n', encoding="utf-8")
        askpass.chmod(0o700)
        os.environ["GIT_ASKPASS"] = str(askpass)
        os.environ["GIT_TERMINAL_PROMPT"] = "0"
        Bridge(Path.cwd()).run()


if __name__ == "__main__":
    try:
        main()
    except Refused as error:
        print(f"Bridge stopped: {error}", file=sys.stderr)
        sys.exit(1)
