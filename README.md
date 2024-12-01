# github-multi-org-stats
A fast interactive dashboard with leaderboards across all repos of multiple github orgs

## Usage

    export GITHUB_ORGS=myOrg1,myOrg2
    export GH_TOKEN=ghp_...

    python github_contributors_report.py fetch --since 2024-01-01T00:00:00+00:00 --until 2025-01-01T00:00:00+00:00 --top-n-repos 30 --exclude-repos myspam,myspam2

    python github_contributors_report.py parse

    python github_contributors_report.py dashboard

## Features

  - Quick mode (default): uses GitHub's stats APIs as the aggregation input.

  - Slow mode: fetches PRs, comments, etc. Takes a very long time by comparison. Don't use this on large orgs or large repos.

  - Totally unnecessary dark mode.

## Trivia

Created entirely with DeepSeek R1, with the goal of no manual code editing. 21 prompt spins over 28 minutes of wall clock time.

Needed to edit 12 lines of code manually, as instruction following was inconsistent for commands like "output complete working code for the entire file with these changes, including copying parts you didn't change"; adding dark mode and making it apply to tables took as much time as all other features.

## Example screenshots

Shown as style samples. Full interface is much more complete.

<img width="1455" alt="Screenshot 2024-12-01 at 1 35 33 PM" src="https://github.com/user-attachments/assets/d0650f4e-d8aa-4891-978a-0c1e188c7d90">
<img width="1237" alt="Screenshot 2024-12-01 at 1 36 50 PM" src="https://github.com/user-attachments/assets/a1f5e8fb-6659-4237-b971-b277a4d8ec5a">

## Status

This is a throwaway test project, but fully functional and moderately useful. Fork and use in good health.
