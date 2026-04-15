# GitHub Contribution Summary

A Python script that summarizes your GitHub contribution activity over a given
date range and prints the results as a Markdown table.

## Requirements

- Python 3
- [`gh` CLI](https://cli.github.com/) installed and authenticated (`gh auth login`)

## Usage

```
python github_contributions.py <start> <end>
```

| Argument | Format | Description |
| -------- | ------ | ----------- |
| `start`  | `yyyy-mm-dd` | Start of the date range (inclusive) |
| `end`    | `yyyy-mm-dd` | End of the date range (inclusive) |

### Examples

```bash
# Full year
python github_contributions.py 2025-01-01 2025-12-31

# Single month
python github_contributions.py 2025-06-01 2025-06-30

# Redirect the Markdown table to a file (progress messages still appear in the terminal)
python github_contributions.py 2025-01-01 2025-12-31 > report.md
```

## Output

Progress messages are written to **stderr**; the Markdown table is written to
**stdout**. This means you can redirect the table to a file while still seeing
progress in the terminal.

### Sample output

```
Fetching GitHub contribution data (2025-01-01 → 2025-12-31)…
  Authenticated as: johndoe
  Counting PRs opened…
  Counting commits…
  Counting PRs reviewed…
  Counting comments received on own PRs…
  Counting comments made on others' PRs…

## GitHub Contribution Summary — johndoe (2025-01-01 to 2025-12-31)

| Metric                       | Count |
| ---------------------------- | ----- |
| Pull requests opened         |   103 |
| Commits made                 |   213 |
| Pull requests reviewed       |   114 |
| Comments received on own PRs |   136 |
| Comments made on others' PRs |   132 |
```

## Metrics

| Metric | Description |
| ------ | ----------- |
| Pull requests opened | PRs authored by you with a creation date in the range |
| Commits made | Commits authored by you with an author date in the range |
| Pull requests reviewed | Distinct PRs (not authored by you) updated in the range that have at least one review submission by you |
| Comments received on own PRs | All non-bot comments left by others on your PRs, where the comment itself was posted within the range |
| Comments made on others' PRs | All comments you posted within the range on PRs not authored by you |

All three GitHub comment types are counted for both "comments received" and
"comments made":

- General PR (issue) comments
- Review summary body comments
- Inline review thread (line-level) comments

Bot accounts (`github-actions`, `github-advanced-security`, `dependabot`) are
excluded from comment counts.

## Notes

**Comment date filtering** — Comments are filtered by their actual creation date
falling within the requested date range, not by the PR creation date. This
ensures accuracy for any arbitrary date window, including ranges that don't
align with a calendar year.

**PRs reviewed** — This count uses GitHub's `updated:{start}..{end}` search
filter, since the API does not support filtering by review submission date
directly. This is a good proxy but may occasionally under- or over-count by a
small margin for activity near the date boundaries.

**Pagination** — GraphQL pagination handles arbitrarily large result sets.
Review-thread queries use a page size of 50 (vs 100 elsewhere) to stay within
GitHub's 500k-node-per-query limit.
