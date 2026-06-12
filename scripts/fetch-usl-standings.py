#!/usr/bin/env python3
"""
Fetch USL Championship standings from the Opta widget data feed.

This script updates:

- data/usl/current-standings.json
- data/usl/standings-history.csv

Source page:
https://www.uslchampionship.com/league-standings

Discovered data feed:
Opta f3 competition standings feed for competition 807, season 2026.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests


SOURCE_URL = "https://www.uslchampionship.com/league-standings"

OPTA_STANDINGS_URL = (
    "https://omo.akamai.opta.net/auth/competition.php"
    "?feed_type=f3"
    "&competition=807"
    "&season_id=2026"
    "&user=OW2017"
    "&psw=dXWg5gVZ"
    "&sps=widgets"
    "&jsoncallback=f3_807_2026"
)

OPTA_TEAM_TRANSLATION_URL = (
    "https://secure.widget.cloud.opta.net/translations_v2/default/"
    "TN_default_1_en_US_1_2026_807.json"
)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "usl"
CURRENT_JSON_PATH = DATA_DIR / "current-standings.json"
HISTORY_CSV_PATH = DATA_DIR / "standings-history.csv"
DEBUG_OPTA_STANDINGS_PATH = DATA_DIR / "debug-opta-standings.json"
DEBUG_TEAM_TRANSLATIONS_PATH = DATA_DIR / "debug-team-translations.json"

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
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int
    opta_team_id: str


def unwrap_jsonp(text: str) -> dict:
    """
    Convert JSONP like callback({...}) into a Python dictionary.
    """
    text = text.strip()

    match = re.match(r"^[A-Za-z0-9_]+\((.*)\)\s*;?\s*$", text, flags=re.S)

    if not match:
        raise RuntimeError("Response did not look like JSONP.")

    return json.loads(match.group(1))


def fetch_text(url: str) -> str:
    """
    Download a text response with a browser-like user agent.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; NagleAnalyticsBot/1.0; "
            "+https://nagle-analytics.github.io/)"
        ),
        "Accept": "application/javascript, application/json, text/plain, */*",
        "Referer": SOURCE_URL,
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    text = response.text or ""

    if not text.strip():
        raise RuntimeError(f"Empty response from {url}")

    return text


def parse_team_translations(text: str) -> Dict[str, dict]:
    """
    Parse the Opta team translation file.

    Example translation chunk:
    5621|Tampa Bay Rowdies||TBR
    """
    payload = unwrap_jsonp(text)

    raw = payload.get("d", "")
    teams: Dict[str, dict] = {}

    for item in raw.split("¦"):
        item = item.strip()

        if not item:
            continue

        parts = item.split("|")

        if len(parts) < 2:
            continue

        team_id = parts[0].strip()
        team_name = parts[1].strip()
        abbr = parts[3].strip() if len(parts) > 3 and parts[3].strip() else ""

        if team_id and team_name:
            teams[team_id] = {
                "name": team_name,
                "abbr": abbr or make_abbr(team_name),
            }

    return teams


def make_abbr(team_name: str) -> str:
    """
    Create a fallback abbreviation from a team name.
    """
    words = [word for word in re.split(r"\s+", team_name) if word]
    letters = "".join(word[0] for word in words if word[0].isalpha()).upper()
    return letters[:4] if letters else team_name[:4].upper()


def ensure_list(value):
    """
    Force Opta values to list form because some feeds use an object for one item
    and a list for multiple items.
    """
    if value is None:
        return []

    if isinstance(value, list):
        return value

    return [value]


def to_int(value: Optional[str], default: int = 0) -> int:
    """
    Convert Opta numeric string fields to integers.
    """
    if value is None:
        return default

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_conference_name(group_id: str, group_name: str) -> str:
    """
    Convert Opta group labels into the public conference names.
    """
    value = f"{group_id} {group_name}".lower()

    if "east" in value:
        return "Eastern Conference"

    if "west" in value:
        return "Western Conference"

    return group_name or group_id or "Unknown Conference"


def parse_standings(
    standings_payload: dict,
    team_lookup: Dict[str, dict],
) -> Dict[str, List[StandingRow]]:
    """
    Parse Opta f3 standings payload into Eastern and Western conference rows.
    """
    soccer_document = standings_payload["SoccerFeed"]["SoccerDocument"]
    competition = soccer_document["Competition"]

    team_standings = ensure_list(competition.get("TeamStandings"))

    output: Dict[str, List[StandingRow]] = {
        "Eastern Conference": [],
        "Western Conference": [],
    }

    for group in team_standings:
        round_info = group.get("Round", {})
        name_info = round_info.get("Name", {})

        group_name = name_info.get("@value", "")
        group_id = name_info.get("@attributes", {}).get("id", "")

        conference = normalize_conference_name(group_id, group_name)

        team_records = ensure_list(group.get("TeamRecord"))

        rows: List[StandingRow] = []

        for record in team_records:
            team_ref = record.get("@attributes", {}).get("TeamRef", "")
            opta_team_id = team_ref.replace("t", "")

            standing = record.get("Standing", {})

            team_info = team_lookup.get(opta_team_id, {})
            team_name = team_info.get("name", f"Team {opta_team_id}")
            abbr = team_info.get("abbr", make_abbr(team_name))

            goals_for = to_int(standing.get("For"))
            goals_against = to_int(standing.get("Against"))

            rows.append(
                StandingRow(
                    rank=to_int(standing.get("Position")),
                    team=team_name,
                    abbr=abbr,
                    played=to_int(standing.get("Played")),
                    wins=to_int(standing.get("Won")),
                    losses=to_int(standing.get("Lost")),
                    ties=to_int(standing.get("Drawn")),
                    goals_for=goals_for,
                    goals_against=goals_against,
                    goal_difference=goals_for - goals_against,
                    points=to_int(standing.get("Points")),
                    opta_team_id=opta_team_id,
                )
            )

        rows.sort(key=lambda row: row.rank)

        if conference in output:
            output[conference] = rows
        else:
            output[conference] = rows

    return output


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
    """
    Write latest standings JSON.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "last_updated": fetched_at,
        "snapshot_date": snapshot_date,
        "source_page": SOURCE_URL,
        "source_feed": OPTA_STANDINGS_URL,
        "team_translation_feed": OPTA_TEAM_TRANSLATION_URL,
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
    """
    Read current history CSV rows.
    """
    if not HISTORY_CSV_PATH.exists():
        return []

    with HISTORY_CSV_PATH.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader)


def write_history_csv(rows: List[dict]) -> None:
    """
    Write history CSV rows.
    """
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
                    "goals_for": row.goals_for,
                    "goals_against": row.goals_against,
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
    Warn if team counts differ from expected conference sizes.
    """
    for conference, expected_count in EXPECTED_CONFERENCES.items():
        actual_count = len(standings.get(conference, []))

        if actual_count != expected_count:
            print(
                f"[WARN] {conference}: expected {expected_count}, found {actual_count}."
            )


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    snapshot_date = datetime.now(timezone.utc).date().isoformat()

    print(f"[INFO] Fetching Opta team translations: {OPTA_TEAM_TRANSLATION_URL}")
    team_translation_text = fetch_text(OPTA_TEAM_TRANSLATION_URL)
    team_lookup = parse_team_translations(team_translation_text)

    DEBUG_TEAM_TRANSLATIONS_PATH.write_text(
        json.dumps(team_lookup, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"[INFO] Parsed {len(team_lookup)} team translations.")

    print(f"[INFO] Fetching Opta standings feed: {OPTA_STANDINGS_URL}")
    standings_text = fetch_text(OPTA_STANDINGS_URL)
    standings_payload = unwrap_jsonp(standings_text)

    DEBUG_OPTA_STANDINGS_PATH.write_text(
        json.dumps(standings_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    standings = parse_standings(
        standings_payload=standings_payload,
        team_lookup=team_lookup,
    )

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

        if rows:
            leader = rows[0]
            print(
                f"[INFO] {conference} leader: "
                f"{leader.team} — {leader.points} pts"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
