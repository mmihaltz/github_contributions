#!/usr/bin/env python3
"""
Summarize GitHub contribution activity for the authenticated user over a date range.

Usage:
    python github_contributions.py <start> <end>

Arguments:
    start   Start date in yyyy-mm-dd format (inclusive)
    end     End date in yyyy-mm-dd format (inclusive)

Requires the `gh` CLI to be installed and authenticated.

Output metrics:
  - Pull requests opened
  - Commits made
  - Pull requests reviewed
  - Total comments received on own pull requests
  - Total comments made on other users' pull requests

All three GitHub comment types are counted: general PR comments, review
summary comments, and inline review thread comments. Bot accounts
(github-actions, github-advanced-security, dependabot) are excluded from
comment counts.

Comments (both received and made) are filtered by their actual creation date
falling within the date range — not just the PR creation date — so results are
accurate for any arbitrary date window.

Output:
  Progress messages are written to stderr; the Markdown table is written to
  stdout, so the output can be cleanly redirected to a file:
      python github_contributions.py 2025-01-01 2025-12-31 > report.md

Implementation notes:
  - GraphQL pagination handles arbitrarily large result sets. Review-thread
    queries use a page size of 50 (vs 100 elsewhere) to stay within GitHub's
    500k-node-per-query limit.
  - The "PRs reviewed" count is based on GitHub's search filter
    updated:{start}..{end}, since the API does not support filtering by review
    submission date directly. This is a good proxy but may occasionally
    under- or over-count by a small margin for activity near the date boundaries.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime

BOTS = {"github-actions", "github-advanced-security", "dependabot"}


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def run_gh_json(*args, extra_headers=None):
    """Run a gh CLI command and return the parsed JSON output."""
    cmd = ["gh"] + list(args)
    if extra_headers:
        for k, v in extra_headers.items():
            cmd += ["-H", f"{k}: {v}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"gh error: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def gh_rest(path, paginate=False, extra_headers=None):
    """Call a GitHub REST API endpoint via gh."""
    args = ["api", path]
    if paginate:
        args.append("--paginate")
    return run_gh_json(*args, extra_headers=extra_headers)


def gh_graphql(query):
    """Execute a GraphQL query via gh and return the response data dict."""
    result = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"gh graphql error: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    payload = json.loads(result.stdout)
    if "errors" in payload:
        print(f"GraphQL errors: {payload['errors']}", file=sys.stderr)
        sys.exit(1)
    return payload["data"]


def graphql_search_paginate(base_query, build_inner_gql, process_page, page_size=100):
    """
    Paginate through a GraphQL search query.

    build_inner_gql(cursor) -> str   inner body of the search { } block
    process_page(nodes) -> int       count of items of interest in this page
    """
    cursor = None
    total = 0
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        gql = f"""
        {{
          search(query: "{base_query}", type: ISSUE, first: {page_size}{after}) {{
            {build_inner_gql()}
            pageInfo {{ hasNextPage endCursor }}
          }}
        }}
        """
        data = gh_graphql(gql)
        search = data["search"]
        total += process_page(search["nodes"])
        if not search["pageInfo"]["hasNextPage"]:
            break
        cursor = search["pageInfo"]["endCursor"]
    return total


# ---------------------------------------------------------------------------
# Individual metrics
# ---------------------------------------------------------------------------

def get_username():
    return gh_rest("/user")["login"]


def count_prs_opened(username, start, end):
    """Count PRs opened by the user in the date range."""
    data = gh_rest(
        f"search/issues?q=is:pr+author:{username}+created:{start}..{end}&per_page=1"
    )
    return data["total_count"]


def count_commits(username, start, end):
    """Count commits authored by the user in the date range."""
    data = gh_rest(
        f"search/commits?q=author:{username}+author-date:{start}..{end}&per_page=1",
        extra_headers={"Accept": "application/vnd.github.cloak-preview"},
    )
    return data["total_count"]


def count_prs_reviewed(username, start, end):
    """
    Count distinct PRs (not authored by the user) that were updated in the date
    range and have at least one review submission by the user.
    """
    data = gh_rest(
        f"search/issues?q=reviewed-by:{username}+is:pr+-author:{username}"
        f"+updated:{start}..{end}&per_page=1"
    )
    return data["total_count"]


def _in_range(iso_datetime, start, end):
    """Return True if the date part of an ISO datetime string is within [start, end]."""
    date = iso_datetime[:10]
    return start <= date <= end


def _author_login(node):
    return (node.get("author") or {}).get("login", "")


def count_comments_received(username, start, end):
    """
    Count all non-bot comments received on the user's own PRs where the comment
    itself falls within the date range.

    Counts three comment types:
      1. General PR (issue) comments
      2. Review summary body comments (non-empty)
      3. Inline review thread comments
    """
    base_query = f"is:pr author:{username} updated:{start}..{end}"

    # --- Phase 1: general comments + review summaries (100 PRs per page) ---
    def inner_phase1():
        return """
            nodes {
              ... on PullRequest {
                comments(first: 100) {
                  nodes { author { login } createdAt }
                }
                reviews(first: 100) {
                  nodes { author { login } submittedAt body }
                }
              }
            }
        """

    def process_phase1(nodes):
        count = 0
        for node in nodes:
            for c in node["comments"]["nodes"]:
                if (
                    _author_login(c) not in {username} | BOTS
                    and _in_range(c["createdAt"], start, end)
                ):
                    count += 1
            for r in node["reviews"]["nodes"]:
                if (
                    _author_login(r) not in {username} | BOTS
                    and r["body"].strip()
                    and _in_range(r["submittedAt"], start, end)
                ):
                    count += 1
        return count

    phase1_total = graphql_search_paginate(
        base_query, inner_phase1, process_phase1, page_size=100
    )

    # --- Phase 2: inline review thread comments (50 PRs per page to stay under node limit) ---
    def inner_phase2():
        return """
            nodes {
              ... on PullRequest {
                reviewThreads(first: 50) {
                  nodes {
                    comments(first: 50) {
                      nodes { author { login } createdAt }
                    }
                  }
                }
              }
            }
        """

    def process_phase2(nodes):
        count = 0
        for node in nodes:
            for thread in node["reviewThreads"]["nodes"]:
                for c in thread["comments"]["nodes"]:
                    if (
                        _author_login(c) not in {username} | BOTS
                        and _in_range(c["createdAt"], start, end)
                    ):
                        count += 1
        return count

    phase2_total = graphql_search_paginate(
        base_query, inner_phase2, process_phase2, page_size=50
    )

    return phase1_total + phase2_total


def count_comments_made(username, start, end):
    """
    Count all comments made by the user on other users' PRs where the comment
    itself falls within the date range.

    Counts three comment types:
      1. General PR (issue) comments
      2. Review summary body comments (non-empty)
      3. Inline review thread comments
    """
    base_query = (
        f"commenter:{username} is:pr -author:{username} updated:{start}..{end}"
    )

    # --- Phase 1: general comments + review summaries (100 PRs per page) ---
    def inner_phase1():
        return """
            nodes {
              ... on PullRequest {
                comments(first: 100) {
                  nodes { author { login } createdAt }
                }
                reviews(first: 100) {
                  nodes { author { login } submittedAt body }
                }
              }
            }
        """

    def process_phase1(nodes):
        count = 0
        for node in nodes:
            for c in node["comments"]["nodes"]:
                if (
                    _author_login(c) == username
                    and _in_range(c["createdAt"], start, end)
                ):
                    count += 1
            for r in node["reviews"]["nodes"]:
                if (
                    _author_login(r) == username
                    and r["body"].strip()
                    and _in_range(r["submittedAt"], start, end)
                ):
                    count += 1
        return count

    phase1_total = graphql_search_paginate(
        base_query, inner_phase1, process_phase1, page_size=100
    )

    # --- Phase 2: inline review thread comments (50 PRs per page) ---
    def inner_phase2():
        return """
            nodes {
              ... on PullRequest {
                reviewThreads(first: 50) {
                  nodes {
                    comments(first: 50) {
                      nodes { author { login } createdAt }
                    }
                  }
                }
              }
            }
        """

    def process_phase2(nodes):
        count = 0
        for node in nodes:
            for thread in node["reviewThreads"]["nodes"]:
                for c in thread["comments"]["nodes"]:
                    if (
                        _author_login(c) == username
                        and _in_range(c["createdAt"], start, end)
                    ):
                        count += 1
        return count

    phase2_total = graphql_search_paginate(
        base_query, inner_phase2, process_phase2, page_size=50
    )

    return phase1_total + phase2_total


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Summarize GitHub contribution activity for a date range.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("start", help="Start date (yyyy-mm-dd, inclusive)")
    parser.add_argument("end", help="End date (yyyy-mm-dd, inclusive)")
    args = parser.parse_args()

    for label, val in [("start", args.start), ("end", args.end)]:
        try:
            datetime.strptime(val, "%Y-%m-%d")
        except ValueError:
            print(f"Invalid {label} date '{val}': expected yyyy-mm-dd", file=sys.stderr)
            sys.exit(1)

    if args.start > args.end:
        print("start date must not be after end date", file=sys.stderr)
        sys.exit(1)

    start, end = args.start, args.end

    def progress(msg):
        print(f"  {msg}", file=sys.stderr)

    print(f"Fetching GitHub contribution data ({start} → {end})…", file=sys.stderr)

    username = get_username()
    progress(f"Authenticated as: {username}")

    progress("Counting PRs opened…")
    prs_opened = count_prs_opened(username, start, end)

    progress("Counting commits…")
    commits = count_commits(username, start, end)

    progress("Counting PRs reviewed…")
    prs_reviewed = count_prs_reviewed(username, start, end)

    progress("Counting comments received on own PRs…")
    comments_received = count_comments_received(username, start, end)

    progress("Counting comments made on others' PRs…")
    comments_made = count_comments_made(username, start, end)

    print(file=sys.stderr)

    # Markdown table to stdout
    print(f"## GitHub Contribution Summary — {username} ({start} to {end})\n")
    print("| Metric | Count |")
    print("|---|---|")
    print(f"| Pull requests opened | {prs_opened} |")
    print(f"| Commits made | {commits} |")
    print(f"| Pull requests reviewed | {prs_reviewed} |")
    print(f"| Comments received on own PRs | {comments_received} |")
    print(f"| Comments made on others' PRs | {comments_made} |")


if __name__ == "__main__":
    main()
