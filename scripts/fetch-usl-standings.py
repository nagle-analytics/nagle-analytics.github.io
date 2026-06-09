#!/usr/bin/env python3
"""
Fetch USL Championship standings and update local data files.

This script reads the official USL Championship standings page, extracts the
Eastern and Western Conference standings, and writes:

- data/usl/current-standings.json
- data/usl/standings-history.csv

The history CSV is what we will use later for the animated standings movement chart.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from io import StringIO
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests


SOURCE_URL = "https://www.uslchampionship.com/league-standings"

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "usl"
CURRENT_JSON_PATH = DATA_DIR / "current-standings.json"
HISTORY_CSV_PATH = DATA_DIR / "standings-history.csv"

EXPECTED_CONFERENCES = {
    "Eastern Conference": 13,
    "Western Conference": 12,
}

HISTORY_FIELDS = [
    "snapshot_date",
    "week",
    "conference",
    "rank",
    "team",
    "abbr",
    "played",
    "wins",
    "losses",
    "ties",
    "goals_for",
    "goals_against",
    "goal_difference",
    "points",
    "source",
]


@dataclass
class StandingRow:
    rank: int
    team: str
    abbr: str
    played: int
    wins: int
    losses: int
    ties: int
    goals_for: Optional[int]
    goals_against: Optional[int]
    goal_difference: int
    points: int


def clean_text(value: object) -> str:
    """Convert a table cell to clean text."""
    if value is None:
        return ""

    text = str(value)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def to_int(value: object) -> Optional[int]:
    """Convert a value to int when possible."""
    text = clean_text(value)

    if text == "" or text.lower() in {"nan", "none", "-"}:
        return None

    match = re.search(r"-?\d+", text)
    if not match:
        return None

    return int(match.group(0))


def normalize_column_name(name: object) -> str:
    """Standardize possible standings column names."""
    text = clean_text(name).lower()
    text = text.replace(".", "")
    text = text.replace(" ", "_")

    aliases = {
        "pos": "rank",
        "position": "rank",
        "#": "rank",
        "club": "team",
        "team": "team",
        "teams": "team",
        "p": "played",
        "pl": "played",
        "played": "played",
        "mp": "played",
        "gp": "played",
        "w": "wins",
        "win": "wins",
        "wins": "wins",
        "l": "losses",
        "loss": "losses",
        "losses": "losses",
        "d": "ties",
        "draw": "ties",
        "draws": "ties",
        "t": "ties",
        "tie": "ties",
        "ties": "ties",
        "gf": "goals_for",
        "goals_for": "goals_for",
        "ga": "goals_against",
        "goals_against": "goals_against",
        "gd": "goal_difference",
        "goal_difference": "goal_difference",
        "pts": "points",
        "points": "points",
    }

    return aliases.get(text, text)


def flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten multi-index columns if pandas reads a complex HTML table."""
    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            "_".join([clean_text(part) for part in col if clean_text(part)])
            for col in df.columns
        ]

    df.columns = [normalize_column_name(col) for col in df.columns]
    return df


def looks_like_standings_table(df: pd.DataFrame) -> bool:
    """Check whether a table appears to be a standings table."""
    columns = set(df.columns)

    required_any_team = {"team"}
    required_stats = {"rank", "played", "wins", "losses", "ties", "goal_difference", "points"}

    return bool(required_any_team.issubset(columns) and len(required_stats.intersection(columns)) >= 5)


def clean_team_name(value: object) -> Tuple[str, str]:
    """
    Return team name and abbreviation.

    Some standings tables show full names, others show abbreviations.
    If only one value exists, both team and abbr are set to that value for now.
    """
    text = clean_text(value)

    text = re.sub(r"^[xzey]-\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        return "", ""

    parts = text.split()

    if len(text) <= 4 and text.isupper():
        return text, text

    possible_abbr = parts[0]
    if len(possible_abbr) <= 4 and possible_abbr.isupper() and len(parts) > 1:
        team = " ".join(parts[1:])
        return team, possible_abbr

    abbreviation = "".join(word[0] for word in parts if word and word[0].isalpha()).upper()
    abbreviation = abbreviation[:4] if abbreviation else text[:4].upper()

    return text, abbreviation


def parse_standings_table(df: pd.DataFrame) -> List[StandingRow]:
    """Parse one normalized standings table into StandingRow objects."""
    rows: List[StandingRow] = []

    df = flatten_columns(df)

    for _, row in df.iterrows():
        rank = to_int(row.get("rank"))
        team, abbr = clean_team_name(row.get("team"))

        played = to_int(row.get("played"))
        wins = to_int(row.get("wins"))
        losses = to_int(row.get("losses"))
        ties = to_int(row.get("ties"))
        goals_for = to_int(row.get("goals_for"))
        goals_against = to_int(row.get("goals_against"))
        goal_difference = to_int(row.get("goal_difference"))
        points = to_int(row.get("points"))

        if rank is None or not team:
            continue

        if played is None or wins is None or losses is None or ties is None:
            continue

        if goal_difference is None or points is None:
            continue

        rows.append(
            StandingRow(
                rank=rank,
                team=team,
                abbr=abbr,
                played=played,
                wins=wins,
                losses=losses,
                ties=ties,
                goals_for=goals_for,
                goals_against=goals_against,
                goal_difference=goal_difference,
                points=points,
            )
        )

    rows.sort(key=lambda item: item.rank)
    return rows


def fetch_html(url: str) -> str:
    """Download page HTML and verify that the response is usable."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; NagleAnalyticsBot/1.0; "
            "+https://nagle-analytics.github.io/)"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    html = response.text or ""

    if not html.strip():
      raise RuntimeError("Received an empty response from the USL standings page.")

    if "<html" not in html.lower():
      raise RuntimeError(
          "The USL standings page response did not look like HTML. "
          f"Content-Type: {response.headers.get('content-type', 'unknown')}"
      )

    print(f"[INFO] Downloaded {len(html):,} characters from USL standings page.")
    print(f"[INFO] Content-Type: {response.headers.get('content-type', 'unknown')}")

    return html


def read_standings_tables(url: str) -> Dict[str, List[StandingRow]]:
    """
    Read standings tables from the official page.

    Many sports websites render standings dynamically. pandas.read_html works
    when the table is present in the HTML. If the site changes, this function
    may need to be updated to use the site's hidden data endpoint.
    """
    html = fetch_html(url)

try:
    tables = pd.read_html(StringIO(html))
except ValueError:
    tables = []
except Exception as exc:
    html_preview = html[:500].replace("\n", " ")
    raise RuntimeError(
        "pandas.read_html failed while parsing the USL standings page. "
        f"Original error: {exc}. "
        f"HTML preview: {html_preview}"
    ) from exc

    normalized_tables = []
    for table in tables:
        table = flatten_columns(table)
        if looks_like_standings_table(table):
            normalized_tables.append(table)

    if len(normalized_tables) < 2:
        raise RuntimeError(
            "Could not find two standings tables on the official page. "
            "The standings may be rendered dynamically. Next step would be "
            "to inspect the page's network requests for the official JSON endpoint."
        )

    eastern_rows = parse_standings_table(normalized_tables[0])
    western_rows = parse_standings_table(normalized_tables[1])

    if not eastern_rows or not western_rows:
        raise RuntimeError("Found standings tables, but could not parse team rows.")

    return {
        "Eastern Conference": eastern_rows,
        "Western Conference": western_rows,
    }


def get_next_week_label(snapshot_date: str) -> str:
    """
    Determine a week label for the history CSV.

    If the snapshot date already exists, reuse its week label.
    Otherwise, assign the next Week N based on existing unique snapshot dates.
    """
    if not HISTORY_CSV_PATH.exists():
        return "Week 1"

    with HISTORY_CSV_PATH.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)

    for row in rows:
        if row.get("snapshot_date") == snapshot_date and row.get("week"):
            return row["week"]

    existing_dates = sorted(
        {
            row.get("snapshot_date", "")
            for row in rows
            if row.get("snapshot_date")
        }
    )

    return f"Week {len(existing_dates) + 1}"


def write_current_json(
    standings: Dict[str, List[StandingRow]],
    fetched_at: str,
    snapshot_date: str,
) -> None:
    """Write the latest standings JSON file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "last_updated": fetched_at,
        "snapshot_date": snapshot_date,
        "source": SOURCE_URL,
        "status": "updated",
        "expected_conferences": EXPECTED_CONFERENCES,
        "conferences": {
            conference: [asdict(row) for row in rows]
            for conference, rows in standings.items()
        },
    }

    with CURRENT_JSON_PATH.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
        file.write("\n")


def read_existing_history() -> List[dict]:
    """Read current history CSV rows."""
    if not HISTORY_CSV_PATH.exists():
        return []

    with HISTORY_CSV_PATH.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader)


def write_history_csv(rows: List[dict]) -> None:
    """Write history CSV rows."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with HISTORY_CSV_PATH.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=HISTORY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def update_history_csv(
    standings: Dict[str, List[StandingRow]],
    snapshot_date: str,
    week_label: str,
) -> None:
    """
    Add or replace rows for the current snapshot date.

    This prevents duplicate rows if you manually run the script twice on the same day.
    """
    existing_rows = read_existing_history()

    existing_rows = [
        row for row in existing_rows
        if row.get("snapshot_date") != snapshot_date
    ]

    new_rows: List[dict] = []

    for conference, rows in standings.items():
        for row in rows:
            new_rows.append(
                {
                    "snapshot_date": snapshot_date,
                    "week": week_label,
                    "conference": conference,
                    "rank": row.rank,
                    "team": row.team,
                    "abbr": row.abbr,
                    "played": row.played,
                    "wins": row.wins,
                    "losses": row.losses,
                    "ties": row.ties,
                    "goals_for": "" if row.goals_for is None else row.goals_for,
                    "goals_against": "" if row.goals_against is None else row.goals_against,
                    "goal_difference": row.goal_difference,
                    "points": row.points,
                    "source": SOURCE_URL,
                }
            )

    all_rows = existing_rows + new_rows
    all_rows.sort(
        key=lambda row: (
            row.get("snapshot_date", ""),
            row.get("conference", ""),
            int(row.get("rank", 999) or 999),
        )
    )

    write_history_csv(all_rows)


def validate_counts(standings: Dict[str, List[StandingRow]]) -> None:
    """
    Print a warning if the conference row counts do not match the expected setup.

    This does not stop the script because league structures can change, but it
    gives us a useful message in the GitHub Actions log.
    """
    for conference, expected_count in EXPECTED_CONFERENCES.items():
        actual_count = len(standings.get(conference, []))

        if actual_count != expected_count:
            print(
                f"[WARN] {conference}: expected {expected_count} teams, "
                f"found {actual_count} teams.",
                file=sys.stderr,
            )


def main() -> int:
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    snapshot_date = datetime.now(timezone.utc).date().isoformat()

    print(f"[INFO] Fetching USL standings from: {SOURCE_URL}")
    standings = read_standings_tables(SOURCE_URL)

    validate_counts(standings)

    week_label = get_next_week_label(snapshot_date)

    write_current_json(
        standings=standings,
        fetched_at=fetched_at,
        snapshot_date=snapshot_date,
    )

    update_history_csv(
        standings=standings,
        snapshot_date=snapshot_date,
        week_label=week_label,
    )

    print(f"[INFO] Updated: {CURRENT_JSON_PATH}")
    print(f"[INFO] Updated: {HISTORY_CSV_PATH}")
    print(f"[INFO] Snapshot date: {snapshot_date}")
    print(f"[INFO] Week label: {week_label}")

    for conference, rows in standings.items():
        print(f"[INFO] {conference}: {len(rows)} teams")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
