#!/usr/bin/env python3
"""
git_work_analyzer.py

Analyze a git repository's commit history to infer work sessions and generate
an interactive HTML report of development effort.

Usage:
    python git_work_analyzer.py --repo /path/to/local/repo
    python git_work_analyzer.py --repo https://github.com/user/repo
    python git_work_analyzer.py --repo /path/to/repo --author "Jane" --output report.html
    python git_work_analyzer.py --repo /path/to/repo --session-gap 3
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import pandas as pd
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ImportError as exc:
    sys.exit(
        f"Missing dependency: {exc}\n"
        "Install with:  pip install -r requirements.txt"
    )

# ── Session-inference knobs ───────────────────────────────────────────────────
DEFAULT_SESSION_GAP_H = 2       # gap (hours) between commits → new session
PRE_COMMIT_BUFFER_MIN = 15      # minutes before first commit in a session
POST_COMMIT_BUFFER_MIN = 5      # minutes after last commit in a session
MIN_SESSION_MIN = 30            # floor: single-commit sessions get at least this
MAX_SESSION_H = 4               # ceiling: cap any session at this many hours
# ─────────────────────────────────────────────────────────────────────────────

DARK_BG = "#0d1117"
PANEL_BG = "#161b22"
BORDER = "#21262d"
BLUE = "#58a6ff"
GRID = "#30363d"


# ── Git helpers ───────────────────────────────────────────────────────────────

def _git(args: list[str], cwd: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed:\n{result.stderr.strip()}"
        )
    return result.stdout


def resolve_repo(repo_arg: str) -> tuple[str, bool]:
    """Return (repo_path, is_temp). Clones remote URLs to a temp dir."""
    if re.match(r"^(https?://|git@)", repo_arg):
        tmp = tempfile.mkdtemp(prefix="git_work_analyzer_")
        print(f"  Cloning {repo_arg} …")
        subprocess.run(["git", "clone", "--quiet", repo_arg, tmp], check=True)
        return tmp, True
    path = os.path.abspath(repo_arg)
    if not os.path.isdir(path):
        sys.exit(f"Not a directory: {path}")
    return path, False


def repo_name(repo_arg: str, repo_path: str) -> str:
    if re.match(r"^https?://", repo_arg):
        return repo_arg.rstrip("/").split("/")[-1].removesuffix(".git")
    return Path(repo_path).name


# ── Commit parsing ────────────────────────────────────────────────────────────

_SEP = "|||GS|||"   # field separator — unlikely to appear in commit data

def parse_commits(repo_path: str, author_filter: str | None) -> list[dict]:
    """
    Return a list of commit dicts from git log (no merges).

    Each dict has:
        hash, dt (UTC datetime), author, email, subject,
        files (list[str]), additions (int), deletions (int)
    """
    fmt = _SEP.join(["%H", "%ai", "%an", "%ae", "%s"])
    raw = _git(
        ["log", f"--format=tformat:{fmt}", "--no-merges", "--numstat"],
        repo_path,
    )

    commits: list[dict] = []
    current: dict | None = None

    for line in raw.splitlines():
        if _SEP in line:
            if current:
                commits.append(current)
            parts = line.split(_SEP)
            if len(parts) < 5:
                continue
            current = {
                "hash": parts[0].strip(),
                "dt": _parse_dt(parts[1].strip()),
                "author": parts[2].strip(),
                "email": parts[3].strip(),
                "subject": parts[4].strip(),
                "files": [],
                "additions": 0,
                "deletions": 0,
            }
        elif current and line.strip():
            # numstat:  <adds>\t<dels>\t<filename>
            cols = line.split("\t", 2)
            if len(cols) == 3:
                try:
                    current["additions"] += int(cols[0]) if cols[0] != "-" else 0
                    current["deletions"] += int(cols[1]) if cols[1] != "-" else 0
                    current["files"].append(cols[2].strip())
                except ValueError:
                    pass

    if current:
        commits.append(current)

    if author_filter:
        af = author_filter.lower()
        commits = [
            c for c in commits
            if af in c["author"].lower() or af in c["email"].lower()
        ]

    commits.sort(key=lambda c: c["dt"])
    return commits


def _parse_dt(s: str) -> datetime:
    """Parse git ISO-8601 date string and return UTC datetime."""
    return datetime.fromisoformat(s).astimezone(timezone.utc)


# ── Session inference ─────────────────────────────────────────────────────────

def infer_sessions(commits: list[dict], gap_hours: float) -> list[dict]:
    """Group commits into work sessions based on time gaps."""
    if not commits:
        return []

    gap = timedelta(hours=gap_hours)
    sessions: list[dict] = []
    bucket: list[dict] = [commits[0]]

    for c in commits[1:]:
        if c["dt"] - bucket[-1]["dt"] > gap:
            sessions.append(_build_session(bucket))
            bucket = [c]
        else:
            bucket.append(c)

    sessions.append(_build_session(bucket))
    return sessions


def _build_session(commits: list[dict]) -> dict:
    raw_start = commits[0]["dt"] - timedelta(minutes=PRE_COMMIT_BUFFER_MIN)
    raw_end   = commits[-1]["dt"] + timedelta(minutes=POST_COMMIT_BUFFER_MIN)

    duration_min = (raw_end - raw_start).total_seconds() / 60
    duration_min = max(duration_min, MIN_SESSION_MIN)
    duration_min = min(duration_min, MAX_SESSION_H * 60)

    end = raw_start + timedelta(minutes=duration_min)

    all_files: list[str] = []
    additions = deletions = 0
    for c in commits:
        all_files.extend(c["files"])
        additions += c["additions"]
        deletions += c["deletions"]

    return {
        "start": raw_start,
        "end": end,
        "duration_min": duration_min,
        "commits": commits,
        "commit_count": len(commits),
        "files": all_files,
        "unique_files": list(dict.fromkeys(all_files)),  # ordered dedupe
        "additions": additions,
        "deletions": deletions,
        "date": raw_start.date(),
    }


# ── Report generation ─────────────────────────────────────────────────────────

def generate_report(
    commits: list[dict],
    sessions: list[dict],
    name: str,
    output: str,
) -> None:
    # ── summary stats ─────────────────────────────────────────────────────────
    total_h     = sum(s["duration_min"] for s in sessions) / 60
    avg_h       = total_h / len(sessions)
    all_files: Counter[str] = Counter()
    for s in sessions:
        all_files.update(s["files"])
    first_dt = commits[0]["dt"]
    last_dt  = commits[-1]["dt"]
    span_days = (last_dt - first_dt).days + 1
    total_adds = sum(c["additions"] for c in commits)
    total_dels = sum(c["deletions"] for c in commits)

    # ── build DataFrames ──────────────────────────────────────────────────────
    df_s = pd.DataFrame([{
        "start":       s["start"],
        "end":         s["end"],
        "duration_h":  s["duration_min"] / 60,
        "commits":     s["commit_count"],
        "files":       len(s["unique_files"]),
        "additions":   s["additions"],
        "deletions":   s["deletions"],
        "date":        s["date"],
    } for s in sessions])

    dts = [c["dt"] for c in commits]

    # ── subplot layout ────────────────────────────────────────────────────────
    fig = make_subplots(
        rows=4, cols=2,
        subplot_titles=[
            "Work Sessions Timeline",
            "Daily Work Hours",
            "Commits by Hour of Day",
            "Commits by Day of Week",
            "Session Duration Distribution",
            "Cumulative Work Hours",
            "Top Modified Files",
            "Change Velocity (adds & dels per session)",
        ],
        vertical_spacing=0.09,
        horizontal_spacing=0.08,
        row_heights=[0.28, 0.22, 0.22, 0.28],
    )

    # 1 ── Work sessions timeline (Gantt via filled polygons) ──────────────────
    palette = ["#58a6ff", "#3fb950", "#ff7b72", "#d2a8ff", "#ffa657"]
    for idx, s in enumerate(sessions):
        col = palette[idx % len(palette)]
        x0, x1 = s["start"], s["end"]
        y0, y1 = idx, idx + 0.75
        hover = (
            f"Session {idx + 1}<br>"
            f"{x0.strftime('%Y-%m-%d')}  "
            f"{x0.strftime('%H:%M')}–{x1.strftime('%H:%M')} UTC<br>"
            f"Duration: {s['duration_min'] / 60:.1f} h<br>"
            f"Commits: {s['commit_count']}<extra></extra>"
        )
        fig.add_trace(go.Scatter(
            x=[x0, x1, x1, x0, x0],
            y=[y0, y0, y1, y1, y0],
            fill="toself",
            mode="lines",
            line=dict(color=col, width=0.8),
            fillcolor=col,
            opacity=0.75,
            showlegend=False,
            hovertemplate=hover,
        ), row=1, col=1)

    # 2 ── Daily hours bar chart ───────────────────────────────────────────────
    daily = df_s.groupby("date")["duration_h"].sum().reset_index()
    fig.add_trace(go.Bar(
        x=daily["date"].astype(str),
        y=daily["duration_h"].round(2),
        marker_color="#2196F3",
        showlegend=False,
        hovertemplate="%{x}: %{y:.1f} h<extra></extra>",
    ), row=1, col=2)

    # 3 ── Hour-of-day distribution ────────────────────────────────────────────
    hours = [dt.hour for dt in dts]
    h_counts = Counter(hours)
    fig.add_trace(go.Bar(
        x=list(range(24)),
        y=[h_counts.get(h, 0) for h in range(24)],
        marker_color="#4CAF50",
        showlegend=False,
        hovertemplate="%{x}:00 → %{y} commits<extra></extra>",
    ), row=2, col=1)

    # 4 ── Day-of-week distribution ────────────────────────────────────────────
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow = Counter(dt.weekday() for dt in dts)
    fig.add_trace(go.Bar(
        x=dow_names,
        y=[dow.get(d, 0) for d in range(7)],
        marker_color="#FF9800",
        showlegend=False,
        hovertemplate="%{x}: %{y} commits<extra></extra>",
    ), row=2, col=2)

    # 5 ── Session duration histogram ──────────────────────────────────────────
    durations = [s["duration_min"] / 60 for s in sessions]
    fig.add_trace(go.Histogram(
        x=durations,
        nbinsx=min(20, len(sessions)),
        marker_color="#9C27B0",
        showlegend=False,
        hovertemplate="~%{x:.1f} h: %{y} sessions<extra></extra>",
    ), row=3, col=1)

    # 6 ── Cumulative hours ────────────────────────────────────────────────────
    cum, cum_x, cum_y = 0.0, [], []
    for s in sessions:
        cum += s["duration_min"] / 60
        cum_x.append(s["end"])
        cum_y.append(round(cum, 2))
    fig.add_trace(go.Scatter(
        x=cum_x, y=cum_y,
        mode="lines+markers",
        fill="tozeroy",
        line=dict(color="#F44336", width=2),
        fillcolor="rgba(244,67,54,0.12)",
        showlegend=False,
        hovertemplate="%{x|%Y-%m-%d}: %{y:.1f} h total<extra></extra>",
    ), row=3, col=2)

    # 7 ── Top modified files ──────────────────────────────────────────────────
    top_files = all_files.most_common(15)
    if top_files:
        fnames, fcounts = zip(*top_files)
        # truncate long paths from the left
        fnames_short = [f if len(f) <= 40 else "…" + f[-39:] for f in fnames]
        fig.add_trace(go.Bar(
            x=list(fcounts)[::-1],
            y=list(fnames_short)[::-1],
            orientation="h",
            marker_color="#58a6ff",
            showlegend=False,
            hovertemplate="%{y}: %{x} commits<extra></extra>",
        ), row=4, col=1)

    # 8 ── Change velocity ─────────────────────────────────────────────────────
    vel_x = [s["end"] for s in sessions]
    vel_adds = [s["additions"] for s in sessions]
    vel_dels = [s["deletions"] for s in sessions]
    fig.add_trace(go.Bar(
        x=vel_x, y=vel_adds,
        name="Additions", marker_color="#3fb950",
        showlegend=True,
        hovertemplate="%{x|%Y-%m-%d}: +%{y}<extra></extra>",
    ), row=4, col=2)
    fig.add_trace(go.Bar(
        x=vel_x, y=vel_dels,
        name="Deletions", marker_color="#ff7b72",
        showlegend=True,
        hovertemplate="%{x|%Y-%m-%d}: -%{y}<extra></extra>",
    ), row=4, col=2)

    # ── global layout ─────────────────────────────────────────────────────────
    fig.update_layout(
        height=1600,
        paper_bgcolor=DARK_BG,
        plot_bgcolor=PANEL_BG,
        font=dict(color="#c9d1d9", family="'Segoe UI Mono', 'Cascadia Code', monospace", size=12),
        title=dict(
            text=f"<b>Work Analytics — {name}</b>",
            font=dict(size=22, color=BLUE),
            x=0.5,
        ),
        legend=dict(
            bgcolor=PANEL_BG, bordercolor=BORDER, borderwidth=1,
            font=dict(color="#c9d1d9"),
        ),
        barmode="stack",
    )
    for row in range(1, 5):
        for col in range(1, 3):
            fig.update_xaxes(gridcolor=GRID, zerolinecolor=GRID, row=row, col=col)
            fig.update_yaxes(gridcolor=GRID, zerolinecolor=GRID, row=row, col=col)

    plot_html = fig.to_html(full_html=False, include_plotlyjs="cdn")

    # ── sessions table ────────────────────────────────────────────────────────
    table_rows = "\n".join(
        f"<tr>"
        f"<td>{i + 1}</td>"
        f"<td>{s['start'].strftime('%Y-%m-%d')}</td>"
        f"<td>{s['start'].strftime('%H:%M')} – {s['end'].strftime('%H:%M')} UTC</td>"
        f"<td>{s['duration_min'] / 60:.1f} h</td>"
        f"<td>{s['commit_count']}</td>"
        f"<td>{len(s['unique_files'])}</td>"
        f"<td style='color:#3fb950'>+{s['additions']}</td>"
        f"<td style='color:#ff7b72'>-{s['deletions']}</td>"
        f"<td style='color:#8b949e;font-size:11px'>"
        f"{'; '.join(c['subject'][:60] for c in s['commits'][:3])}"
        f"{'…' if len(s['commits']) > 3 else ''}</td>"
        f"</tr>"
        for i, s in enumerate(sessions)
    )

    # ── stat cards ────────────────────────────────────────────────────────────
    def card(label: str, value: str, sub: str = "") -> str:
        return (
            f'<div style="background:{PANEL_BG};border:1px solid {BORDER};'
            f'border-radius:10px;padding:18px 26px;min-width:130px;flex:1">'
            f'<div style="color:#8b949e;font-size:11px;letter-spacing:.05em;'
            f'text-transform:uppercase;margin-bottom:6px">{label}</div>'
            f'<div style="color:{BLUE};font-size:30px;font-weight:700;line-height:1">{value}</div>'
            f'{"<div style=color:#8b949e;font-size:11px;margin-top:4px>" + sub + "</div>" if sub else ""}'
            f"</div>"
        )

    cards_html = (
        card("Total Work Time", f"{total_h:.1f} h", f"across {span_days} calendar days")
        + card("Work Sessions", str(len(sessions)), f"avg {avg_h:.1f} h each")
        + card("Commits", str(len(commits)), f"{total_adds:,}+ / {total_dels:,}-")
        + card("Files Touched", str(len(all_files)), "unique paths")
        + card("First Commit", first_dt.strftime("%Y-%m-%d"))
        + card("Last Commit", last_dt.strftime("%Y-%m-%d"))
    )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Work Analytics — {name}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: {DARK_BG};
      color: #c9d1d9;
      font-family: 'Segoe UI', system-ui, sans-serif;
    }}
    header {{
      padding: 32px 40px 24px;
      border-bottom: 1px solid {BORDER};
    }}
    header h1 {{ color: {BLUE}; font-size: 26px; margin-bottom: 4px; }}
    header p  {{ color: #8b949e; font-size: 13px; }}
    .cards {{
      display: flex; flex-wrap: wrap; gap: 14px;
      padding: 24px 40px;
    }}
    .charts {{ padding: 0 16px 24px; }}
    .section {{ padding: 24px 40px; }}
    .section h2 {{ color: {BLUE}; font-size: 18px; margin-bottom: 16px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th {{
      background: {PANEL_BG}; color: {BLUE};
      padding: 10px 12px; text-align: left;
      border-bottom: 2px solid {BORDER};
      font-weight: 600; letter-spacing: .04em;
    }}
    td {{ padding: 9px 12px; border-bottom: 1px solid {BORDER}; }}
    tr:hover td {{ background: {PANEL_BG}; }}
    footer {{
      text-align: center; color: #484f58; font-size: 11px;
      padding: 24px; border-top: 1px solid {BORDER};
    }}
  </style>
</head>
<body>
<header>
  <h1>Work Analytics — {name}</h1>
  <p>Generated {generated_at} &nbsp;·&nbsp;
     Session gap threshold: {DEFAULT_SESSION_GAP_H} h &nbsp;·&nbsp;
     Session floor: {MIN_SESSION_MIN} min &nbsp;·&nbsp;
     Session cap: {MAX_SESSION_H} h</p>
</header>

<div class="cards">
  {cards_html}
</div>

<div class="charts">
  {plot_html}
</div>

<div class="section">
  <h2>Work Sessions Detail</h2>
  <table>
    <thead>
      <tr>
        <th>#</th><th>Date</th><th>Time (UTC)</th>
        <th>Duration</th><th>Commits</th><th>Files</th>
        <th>Added</th><th>Deleted</th><th>Commit messages</th>
      </tr>
    </thead>
    <tbody>
      {table_rows}
    </tbody>
  </table>
</div>

<footer>git-work-analyzer &nbsp;·&nbsp; All times UTC</footer>
</body>
</html>"""

    Path(output).write_text(html, encoding="utf-8")
    print(f"\n  Report → {os.path.abspath(output)}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Analyze a git repo and produce a work-effort HTML report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--repo",        required=True, metavar="PATH|URL",
                   help="Local repo path or remote git URL")
    p.add_argument("--output",      default="work_report.html", metavar="FILE",
                   help="Output HTML file (default: work_report.html)")
    p.add_argument("--author",      default=None, metavar="NAME",
                   help="Filter by author name/email substring (case-insensitive)")
    p.add_argument("--session-gap", default=DEFAULT_SESSION_GAP_H, type=float,
                   metavar="HOURS",
                   help=f"Hours between commits to start a new session (default {DEFAULT_SESSION_GAP_H})")
    args = p.parse_args()

    print(f"\ngit-work-analyzer")
    print(f"  repo:        {args.repo}")
    print(f"  session gap: {args.session_gap} h")
    if args.author:
        print(f"  author:      {args.author}")

    repo_path, is_temp = resolve_repo(args.repo)
    name = repo_name(args.repo, repo_path)

    try:
        print(f"\n  Parsing commits …")
        commits = parse_commits(repo_path, args.author)

        if not commits:
            print("  No commits found (check --author filter).")
            return

        print(f"  Found {len(commits)} commits")

        sessions = infer_sessions(commits, args.session_gap)
        print(f"  Inferred {len(sessions)} work sessions")

        total_h = sum(s["duration_min"] for s in sessions) / 60
        print(f"  Estimated effort: {total_h:.1f} hours")

        generate_report(commits, sessions, name, args.output)

    finally:
        if is_temp:
            shutil.rmtree(repo_path, ignore_errors=True)
            print("  Cleaned up temp clone.")


if __name__ == "__main__":
    main()
