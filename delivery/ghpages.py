"""
GitHub Pages deployment — commits output/index.html to the gh-pages
branch of the configured repo and pushes it.

When running in GitHub Actions, the peaceiris/actions-gh-pages action
handles the push automatically. This module is for local deployment or
when running on a VPS/Raspberry Pi.

Requires:
- git configured with push access to the remote
- The output directory to contain index.html (written by static_formatter)
"""

import os
import subprocess
from datetime import date

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")


def _run(cmd: list[str], cwd: str | None = None) -> None:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nstderr: {result.stderr}\nstdout: {result.stdout}"
        )


def deploy_page(html: str, config: dict) -> None:
    """
    Deploy index.html to GitHub Pages.

    In GitHub Actions: the workflow handles this via peaceiris/actions-gh-pages.
    Set GITHUB_ACTIONS=true to skip local git operations.
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("  [ghpages] Running in GitHub Actions — deployment handled by workflow step.")
        return

    pages_cfg = config.get("github_pages", {})
    repo = pages_cfg.get("repo", "")
    branch = pages_cfg.get("branch", "gh-pages")

    if not repo:
        print("  [ghpages] github_pages.repo not set in config — skipping deploy.")
        return

    index_path = os.path.join(OUTPUT_DIR, "index.html")
    if not os.path.exists(index_path):
        print(f"  [ghpages] {index_path} not found — skipping deploy.")
        return

    # We use a lightweight approach: clone/pull the gh-pages branch,
    # copy the file, commit, and push.
    deploy_dir = os.path.join(OUTPUT_DIR, "_ghpages_deploy")
    remote_url = f"https://github.com/{repo}.git"

    try:
        if os.path.isdir(os.path.join(deploy_dir, ".git")):
            print("  [ghpages] Pulling existing gh-pages clone...")
            _run(["git", "pull", "--rebase"], cwd=deploy_dir)
        else:
            print(f"  [ghpages] Cloning {remote_url} (branch: {branch})...")
            os.makedirs(deploy_dir, exist_ok=True)
            _run(["git", "clone", "--branch", branch, "--depth", "1", remote_url, deploy_dir])

        import shutil
        shutil.copy2(index_path, os.path.join(deploy_dir, "index.html"))

        _run(["git", "add", "index.html"], cwd=deploy_dir)

        commit_msg = f"Daily Digest update — {date.today().isoformat()}"
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=deploy_dir,
        )
        if result.returncode == 0:
            print("  [ghpages] No changes to deploy.")
            return

        _run(["git", "commit", "-m", commit_msg], cwd=deploy_dir)
        _run(["git", "push"], cwd=deploy_dir)
        print("  [ghpages] Deployed successfully.")

    except Exception as e:
        print(f"  [ghpages] ERROR during deployment: {e}")
        raise
