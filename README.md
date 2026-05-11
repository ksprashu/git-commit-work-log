# git-work-analyzer

Measure how much time and effort you've actually put into a project by analyzing
its git history — not just commit counts, but inferred **work sessions** with
estimated durations, patterns, and a rich interactive HTML report.

## Quick start

```bash
pip install -r requirements.txt

# Analyze a local repo
python git_work_analyzer.py --repo /path/to/your/project

# Analyze a GitHub repo (clones it temporarily)
python git_work_analyzer.py --repo https://github.com/user/repo

# Focus on one author, custom output file
python git_work_analyzer.py --repo /path/to/project --author "Your Name" --output mine.html

# Change the session gap (default 2 h)
python git_work_analyzer.py --repo /path/to/project --session-gap 3
```

Open `work_report.html` in any browser — no server needed.

## How it works

Git commits are clustered into **work sessions**: if two consecutive commits
are more than `--session-gap` hours apart, they belong to different sessions.
Each session gets a 15-minute pre-commit buffer and a 5-minute post-commit
buffer. Single-commit sessions are floored at 30 minutes; any session is capped
at 4 hours to avoid inflated estimates from overnight gaps.

## Report contents

| Chart | What it shows |
|-------|---------------|
| Work Sessions Timeline | Gantt view of every session |
| Daily Work Hours | Effort per calendar day |
| Commits by Hour of Day | When during the day you work |
| Commits by Day of Week | Which weekdays are most productive |
| Session Duration Distribution | How long sessions typically last |
| Cumulative Work Hours | Running total of hours invested |
| Top Modified Files | Most frequently changed files |
| Change Velocity | Lines added / deleted per session |

Below the charts: a detailed session table with date, time range, duration,
commit count, files changed, lines added/deleted, and commit messages.

## Dependencies

```
pandas>=2.0.0
plotly>=5.18.0
```

See [`problem_statement.md`](problem_statement.md) for the full design rationale.
