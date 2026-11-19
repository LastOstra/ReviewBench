#!/usr/bin/env python3
"""Local (no-Docker) ReviewBench runner for pr-agent.

For each benchmark PR it prepares a git checkout like scripts/try-agent.sh does,
runs `pr-agent --diff-file ... review`, and converts the review JSON into the
ReviewBench judging input format (see docs/JUDGING_INPUT.md).

Usage:
  python3 scripts/local_run.py --pr 0
  python3 scripts/local_run.py --set test --out ./findings
  python3 scripts/local_run.py --pr-agent /path/to/pr-agent --limit 3
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "corpus" / "test" / "test.json"
GIT_ENV = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"}
# Used only for probing commit existence: lazy fetching must stay off there,
# but checkout needs it to pull the blobs the filtered fetch skipped.
GIT_PROBE_ENV = {**GIT_ENV, "GIT_NO_LAZY_FETCH": "1"}


def run(cmd, cwd=None, env=None, timeout=None, check=True):
    result = subprocess.run(
        cmd, cwd=cwd, env=env or GIT_ENV, timeout=timeout,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n{result.stdout[-4000:]}"
        )
    return result


def has_commits(repo_dir, shas):
    for sha in shas:
        result = run(["git", "-C", str(repo_dir), "cat-file", "-e", f"{sha}^{{commit}}"],
                     env=GIT_PROBE_ENV, check=False)
        if result.returncode != 0:
            return False
    return True


def prepare_repo(repo_dir, nwo, base, head, mirror_org):
    origin_url = f"git@github.com:{nwo}.git"
    if not (repo_dir / ".git").exists():
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "-q", "--no-checkout", origin_url, str(repo_dir)])

    run(["git", "-C", str(repo_dir), "remote", "set-url", "origin", origin_url], check=False)
    run(["git", "-C", str(repo_dir), "remote", "remove", "upstream"], check=False)
    run(["git", "-C", str(repo_dir), "remote", "add", "upstream",
         f"git@github.com:{nwo}.git"], check=False)

    if not has_commits(repo_dir, [base, head]):
        run(["git", "-C", str(repo_dir), "fetch", "-q", "origin", base, head], check=False)
    if not has_commits(repo_dir, [base, head]):
        print("   mirror missing commits, falling back to upstream", flush=True)
        run(["git", "-C", str(repo_dir), "fetch", "-q", "upstream", base, head], check=False)
    if not has_commits(repo_dir, [base, head]):
        raise RuntimeError(f"could not fetch base={base} head={head}")

    run(["git", "-C", str(repo_dir), "checkout", "-q", "--force", "--detach", head])
    run(["git", "-C", str(repo_dir), "clean", "-qfdx"])


def seed_repo(repo_dir, seed_dir, nwo, head):
    """Mirror the checkout into the layout `eval.ts` expects under --repo-dir.

    `--shared` links objects through alternates, so this is near-instant and
    costs no extra disk space.
    """
    owner, name = nwo.split("/", 1)
    dst = seed_dir / f"{owner}__{name}" / head[:8]
    if (dst / ".git").exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "-q", "--shared", "--no-checkout", str(repo_dir), str(dst)])
    run(["git", "-C", str(dst), "checkout", "-q", "--force", "--detach", head])


def to_findings(review_json, entry, producer):
    data = json.loads(Path(review_json).read_text(encoding="utf-8"))
    review = data.get("review", data)
    issues = review.get("key_issues_to_review") or []

    findings = []
    for issue in issues:
        path = (issue.get("relevant_file") or "").strip()
        if path.startswith("./"):
            path = path[2:]
        path = path.lstrip("/")
        start = issue.get("start_line")
        end = issue.get("end_line", start)
        header = (issue.get("issue_header") or "").strip()
        content = (issue.get("issue_content") or "").strip()
        message = f"**{header}**\n\n{content}" if header else content
        if not path or not isinstance(start, int) or not isinstance(end, int) or start < 1 or not message:
            continue
        findings.append({
            "producer": producer,
            "file": path,
            "start_line": start,
            "end_line": max(start, end),
            "message": message,
        })

    return {
        "pr": {
            "repo": entry["repo"],
            "pr_number": int(entry["pr_number"]),
            "base": entry["base"],
            "head": entry["head"],
        },
        "agent": producer,
        "findings": findings,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", dest="set_name", default="test", choices=["test", "full"])
    parser.add_argument("--manifest")
    parser.add_argument("--pr", type=int, action="append", help="run only these PR indices")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--work", default=str(ROOT / ".local-run"))
    parser.add_argument("--out", default=str(ROOT / "findings"))
    parser.add_argument("--pr-agent", default="pr-agent", help="pr-agent executable")
    parser.add_argument("--producer", default="pr-agent")
    parser.add_argument("--mirror-org", default="review-bench")
    parser.add_argument("--timeout", type=int, default=1800, help="seconds per PR for pr-agent")
    parser.add_argument("--seed-dir", default=str(ROOT / ".reviewbench-repos"),
                        help="mirror checkouts here for `npm run judge -- --repo-dir ...`")
    parser.add_argument("--no-seed", action="store_true", help="skip populating --seed-dir")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    manifest_path = Path(args.manifest) if args.manifest else (
        ROOT / "corpus" / ("test" if args.set_name == "test" else "") / (
            "test.json" if args.set_name == "test" else "manifest.json"))
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))

    indices = args.pr if args.pr else list(range(len(entries)))
    if args.limit:
        indices = indices[:args.limit]

    work = Path(args.work)
    out_dir = Path(args.out)
    (work / "repos").mkdir(parents=True, exist_ok=True)
    (work / "pr").mkdir(parents=True, exist_ok=True)
    (work / "review").mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    passed = failed = 0
    for idx in indices:
        entry = entries[idx]
        nwo = entry["nwo"]
        pr_number = entry["pr_number"]
        base, head = entry["base"], entry["head"]
        key = f"{nwo.replace('/', '_')}_{pr_number}-{head[:8]}"

        print(f"== [{idx}] {nwo}#{pr_number} ({key})", flush=True)

        repo_dir = work / "repos" / nwo.replace("/", "_")
        pr_dir = work / "pr" / key
        pr_dir.mkdir(parents=True, exist_ok=True)
        diff_path = pr_dir / "diff.patch"
        review_json = work / "review" / f"{key}.json"
        have_findings = args.skip_existing and (out_dir / f"{key}.json").exists()
        if have_findings and args.no_seed:
            print("   skip: findings already exist", flush=True)
            continue

        try:
            cache_existed = (repo_dir / ".git").exists()
            try:
                prepare_repo(repo_dir, nwo, base, head, args.mirror_org)
            except Exception:
                if cache_existed:
                    raise
                # A cache left broken by an interrupted run is cheaper to rebuild than to repair.
                shutil.rmtree(repo_dir, ignore_errors=True)
                prepare_repo(repo_dir, nwo, base, head, args.mirror_org)
            if not args.no_seed:
                try:
                    seed_repo(repo_dir, Path(args.seed_dir), nwo, head)
                except Exception as exc:
                    print(f"   warn: seed failed: {exc}", flush=True)
            if have_findings:
                print("   skip: findings already exist (repo cache seeded)", flush=True)
                continue
            with open(diff_path, "w", encoding="utf-8") as fh:
                fh.write(run(["git", "-C", str(repo_dir), "diff", f"{base}...{head}"]).stdout)
            if not diff_path.read_text(encoding="utf-8").strip():
                raise RuntimeError("empty diff")

            started = time.time()
            result = subprocess.run(
                [args.pr_agent, "--diff-file", str(diff_path),
                 "--json-output", str(review_json), "review"],
                cwd=str(repo_dir), timeout=args.timeout,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            elapsed_ms = int((time.time() - started) * 1000)
            if result.returncode != 0:
                raise RuntimeError(f"pr-agent failed ({result.returncode})\n{result.stdout[-4000:]}")
            if not review_json.exists():
                raise RuntimeError("pr-agent produced no --json-output file")

            payload = to_findings(review_json, entry, args.producer)
            payload["usage"] = {"time_in_ms": elapsed_ms}
            (out_dir / f"{key}.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"   ok: {len(payload['findings'])} finding(s), {elapsed_ms / 1000:.1f}s", flush=True)
            passed += 1
        except Exception as exc:
            print(f"   FAIL: {exc}", flush=True)
            failed += 1

    print(f"passed {passed}, failed {failed}; findings in {out_dir}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())