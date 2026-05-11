# Problem Statement: Git Work Analytics

## What I'm Trying to Do

Given a Git repository (local or remote), I want to understand **how much time and
effort I actually invested** in that project — not just a commit count, but a real
picture of my working patterns: when I worked, for how long, and how intensely.

## Why Commits Alone Are Not Enough

Git commits are a poor proxy for actual work because:
- I may spend hours writing code before making a single commit.
- I may make micro-commits many times a day, or batch them at the end of a session.
- Merges, squashes, and rebases distort the timeline further.

A richer picture needs to be inferred from the *pattern* of commit timestamps, the
files changed, and the magnitude of each change.

## What I Want to Measure

1. **Work sessions** — contiguous periods of activity, inferred by clustering commits
   that fall within a short time window (e.g. < 2 hours apart).
2. **Session duration** — how long each session lasted (start estimate = first
   commit – 15 min buffer; end = last commit + 5 min buffer; minimum 30 min).
3. **Total investment** — sum of all inferred session durations.
4. **Work patterns** — which times of day and days of week I tend to work.
5. **File-level activity** — which files I touched most often.
6. **Change velocity** — lines added/deleted per session over time.

## Data Sources

| Source | What it gives us |
|--------|-----------------|
| `git log --format="%ai ..."` | Commit timestamps (author date with timezone) |
| `git log --numstat` | Lines added/deleted + file list per commit |
| File system `mtime` | Last-modified time of working-tree files (local repos only) |

The primary source is **git log** since it works for both local and remote repos (after
cloning). File system `mtime` is a secondary signal for uncommitted in-progress work.

## Inputs

- A **local git repo path** (e.g. `/home/user/my-project`) — richest data, includes
  any uncommitted work signal from `mtime`.
- A **remote GitHub URL** (e.g. `https://github.com/user/repo`) — the script clones
  it to a temp directory, analyzes, then cleans up.

Optional filter: `--author` to focus on a specific contributor.

## Desired Output

An interactive, self-contained **HTML report** (`work_report.html`) containing:

1. **Summary card row** — total hours, sessions, avg session, commits, files, span.
2. **Work sessions timeline** — Gantt-style view of every session.
3. **Daily hours bar chart** — effort per calendar day.
4. **Hour-of-day distribution** — when during the day work happens.
5. **Day-of-week distribution** — which weekdays are most productive.
6. **Session duration histogram** — how long sessions typically last.
7. **Cumulative effort curve** — running total of hours invested.
8. **Top modified files** — most frequently changed files.
9. **Detailed sessions table** — per-session breakdown (date, time range, duration,
   commits, files, lines changed).

## Sample Repository for Testing

`https://github.com/ksprashu/gcx-vector-protocol`

## Tech Stack Decision

| Layer | Choice | Reason |
|-------|--------|--------|
| Language | Python 3.10+ | Universal, rich stdlib for subprocess/datetime |
| Git data | `subprocess` + raw `git` commands | More reliable than GitPython for complex queries |
| Data processing | `pandas` | Groupby, resampling, date arithmetic |
| Visualization | `plotly` | Interactive HTML output, no server needed |
| Packaging | `requirements.txt` | Minimal — only two runtime deps |

## Session Inference Algorithm

```
1. Parse all commits, sort by author timestamp ascending.
2. Group into sessions: if gap between consecutive commits > SESSION_GAP (default 2h),
   start a new session.
3. For each session:
   - start  = first_commit_time - 15 min   (pre-commit work buffer)
   - end    = last_commit_time  + 5 min    (post-commit buffer)
   - if (end - start) < 30 min  → set end = start + 30 min
   - if (end - start) > 4 hours → cap at 4 hours  (prevents inflated sessions)
4. Sum session durations for total estimated effort.
```
