#!/usr/bin/env python3
"""
Fetch and reconstruct USL Championship standings history.

Outputs:
- data/usl/current-standings.json
- data/usl/standings-history.csv

Data sources:
- Opta f3 feed: official current standings
- Opta f1 feed: completed match results used to reconstruct historical standings
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

OPTA_MATCHES_URL = (
    "https://omo.akamai.opta.net/auth/competition.php"
    "?feed_type=f1"
    "&competition=807"
    "&season_id=2026"
    "&user=OW2017"
    "&psw=dXWg5gVZ"
    "&sps=widgets"
    "&jsoncallback=f1_807_2026"
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
DEBUG_OPTA_MATCHES_PATH = DATA_DIR / "debug-opta-matches.json"
DEBUG_TEAM_TRANSLATIONS_PATH = DATA_DIR / "debug-team-translations.json"

EXPECTED_CONFERENCES = {
    "Eastern Conference": 13,
    "Western Conference": 12,
}

HISTORY_FIELDS = [
    "snapshot_date",
    "matchday",
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


@dataclass
class MatchResult:
    matchday: int
    date_utc: str
    home_team_id: str
    away_team_id: str
    home_score: int
    away_score: int


def unwrap_jsonp(text: str) -> dict:
    text = text.strip()
    match = re.match(r"^[A-Za-z0-9_]+\((.*)\)\s*;?\s*$", text, flags=re.S)

    if not match:
        raise RuntimeError("Response did not look like JSONP.")

    return json.loads(match.group(1))


def fetch_text(url: str) -> str:
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


def ensure_list(value):
    if value is None:
        return []

    if isinstance(value, list):
        return value

    return [value]


def to_int(value: Optional[str], default: int = 0) -> int:
    if value is None:
        return default

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def make_abbr(team_name: str) -> str:
    words = [word for word in re.split(r"\s+", team_name) if word]
    letters = "".join(word[0] for word in words if word[0].isalpha()).upper()
    return letters[:4] if letters else team_name[:4].upper()


def parse_team_translations(text: str) -> Dict[str, dict]:
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


def normalize_conference_name(group_id: str, group_name: str) -> str:
    value = f"{group_id} {group_name}".lower()

    if "east" in value:
        return "Eastern Conference"

    if "west" in value:
        return "Western Conference"

    return group_name or group_id or "Unknown Conference"


def parse_current_standings(
    standings_payload: dict,
    team_lookup: Dict[str, dict],
) -> tuple[Dict[str, List[StandingRow]], Dict[str, str]]:
    soccer_document = standings_payload["SoccerFeed"]["SoccerDocument"]
    competition = soccer_document["Competition"]
    team_standings = ensure_list(competition.get("TeamStandings"))

    output: Dict[str, List[StandingRow]] = {
        "Eastern Conference": [],
        "Western Conference": [],
    }

    conference_by_team_id: Dict[str, str] = {}

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

            conference_by_team_id[opta_team_id] = conference

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
        output[conference] = rows

    return output, conference_by_team_id


def parse_matches(matches_payload: dict) -> List[MatchResult]:
    soccer_document = matches_payload["SoccerFeed"]["SoccerDocument"]
    match_data = ensure_list(soccer_document.get("MatchData"))

    matches: List[MatchResult] = []

    completed_periods = {
        "FullTime",
        "FullTime90",
        "FullTimePens",
        "EndOfTheMatch",
    }

    for match in match_data:
        match_info = match.get("MatchInfo", {})
        match_attrs = match_info.get("@attributes", {})

        period = match_attrs.get("Period", "")
        match_type = match_attrs.get("MatchType", "")
        matchday = to_int(match_attrs.get("MatchDay") or match_attrs.get("RoundNumber"))

        if matchday <= 0:
            continue

        if period not in completed_periods:
            continue

        if match_type and match_type.lower() != "regular":
            continue

        date_utc = match_info.get("DateUtc") or match_info.get("Date") or ""

        team_data = ensure_list(match.get("TeamData"))
        home = None
        away = None

        for team in team_data:
            attrs = team.get("@attributes", {})
            side = attrs.get("Side", "")
            team_id = attrs.get("TeamRef", "").replace("t", "")
            score = to_int(attrs.get("Score"))

            if side == "Home":
                home = {
                    "team_id": team_id,
                    "score": score,
                }
            elif side == "Away":
                away = {
                    "team_id": team_id,
                    "score": score,
                }

        if not home or not away:
            continue

        if not home["team_id"] or not away["team_id"]:
            continue

        matches.append(
            MatchResult(
                matchday=matchday,
                date_utc=date_utc,
                home_team_id=home["team_id"],
                away_team_id=away["team_id"],
                home_score=home["score"],
                away_score=away["score"],
            )
        )

    matches.sort(key=lambda match: (match.matchday, match.date_utc))

    return matches


def empty_team_stats() -> dict:
    return {
        "played": 0,
        "wins": 0,
        "losses": 0,
        "ties": 0,
        "goals_for": 0,
        "goals_against": 0,
        "points": 0,
    }


def apply_match_to_stats(stats: Dict[str, dict], match: MatchResult) -> None:
    home = stats[match.home_team_id]
    away = stats[match.away_team_id]

    home["played"] += 1
    away["played"] += 1

    home["goals_for"] += match.home_score
    home["goals_against"] += match.away_score

    away["goals_for"] += match.away_score
    away["goals_against"] += match.home_score

    if match.home_score > match.away_score:
        home["wins"] += 1
        away["losses"] += 1
        home["points"] += 3
    elif match.home_score < match.away_score:
        away["wins"] += 1
        home["losses"] += 1
        away["points"] += 3
    else:
        home["ties"] += 1
        away["ties"] += 1
        home["points"] += 1
        away["points"] += 1


def rank_conference(
    conference: str,
    stats: Dict[str, dict],
    conference_by_team_id: Dict[str, str],
    team_lookup: Dict[str, dict],
) -> List[StandingRow]:
    rows: List[StandingRow] = []

    for team_id, team_conference in conference_by_team_id.items():
        if team_conference != conference:
            continue

        team_stats = stats[team_id]
        team_info = team_lookup.get(team_id, {})
        team_name = team_info.get("name", f"Team {team_id}")
        abbr = team_info.get("abbr", make_abbr(team_name))

        goals_for = team_stats["goals_for"]
        goals_against = team_stats["goals_against"]

        rows.append(
            StandingRow(
                rank=0,
                team=team_name,
                abbr=abbr,
                played=team_stats["played"],
                wins=team_stats["wins"],
                losses=team_stats["losses"],
                ties=team_stats["ties"],
                goals_for=goals_for,
                goals_against=goals_against,
                goal_difference=goals_for - goals_against,
                points=team_stats["points"],
                opta_team_id=team_id,
            )
        )

    # Approximate ranking logic.
    # Official USL tiebreakers are more detailed, but this is a strong first pass:
    # points, goal difference, goals for, wins, then team name.
    rows.sort(
        key=lambda row: (
            -row.points,
            -row.goal_difference,
            -row.goals_for,
            -row.wins,
            row.team,
        )
    )

    for index, row in enumerate(rows, start=1):
        row.rank = index

    return rows


def date_only(value: str) -> str:
    if not value:
        return ""

    return value[:10]


def reconstruct_history_rows(
    matches: List[MatchResult],
    conference_by_team_id: Dict[str, str],
    team_lookup: Dict[str, dict],
) -> List[dict]:
    if not matches:
        raise RuntimeError("No completed matches were available to reconstruct history.")

    stats: Dict[str, dict] = {
        team_id: empty_team_stats()
        for team_id in conference_by_team_id
    }

    max_matchday = max(match.matchday for match in matches)
    matches_by_matchday: Dict[int, List[MatchResult]] = {}

    for match in matches:
        matches_by_matchday.setdefault(match.matchday, []).append(match)

    history_rows: List[dict] = []
    latest_snapshot_date = ""

    for matchday in range(1, max_matchday + 1):
        current_matches = matches_by_matchday.get(matchday, [])

        for match in current_matches:
            if match.home_team_id not in stats or match.away_team_id not in stats:
                continue

            apply_match_to_stats(stats, match)

        matchday_dates = [
            date_only(match.date_utc)
            for match in current_matches
            if date_only(match.date_utc)
        ]

        if matchday_dates:
            latest_snapshot_date = max(matchday_dates)

        snapshot_date = latest_snapshot_date or f"2026-matchday-{matchday:02d}"

        for conference in EXPECTED_CONFERENCES:
            ranked_rows = rank_conference(
                conference=conference,
                stats=stats,
                conference_by_team_id=conference_by_team_id,
                team_lookup=team_lookup,
            )

            for row in ranked_rows:
                history_rows.append(
                    {
                        "snapshot_date": snapshot_date,
                        "matchday": matchday,
                        "week": f"Matchday {matchday}",
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

    return history_rows


def write_current_json(
    standings: Dict[str, List[StandingRow]],
    fetched_at: str,
    snapshot_date: str,
) -> None:
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


def write_history_csv(rows: List[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with HISTORY_CSV_PATH.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=HISTORY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def validate_counts(standings: Dict[str, List[StandingRow]]) -> None:
    for conference, expected_count in EXPECTED_CONFERENCES.items():
        actual_count = len(standings.get(conference, []))

        if actual_count != expected_count:
            print(
                f"[WARN] {conference}: expected {expected_count}, found {actual_count}."
            )


def validate_reconstructed_final_against_current(
    history_rows: List[dict],
    current_standings: Dict[str, List[StandingRow]],
) -> None:
    if not history_rows:
        return

    max_matchday = max(to_int(row.get("matchday")) for row in history_rows)

    final_rows = [
        row for row in history_rows
        if to_int(row.get("matchday")) == max_matchday
    ]

    final_lookup = {
        (row["conference"], row["team"]): row
        for row in final_rows
    }

    mismatch_count = 0

    for conference, rows in current_standings.items():
        for current in rows:
            reconstructed = final_lookup.get((conference, current.team))

            if not reconstructed:
                print(
                    f"[WARN] Missing reconstructed final row for "
                    f"{conference} / {current.team}"
                )
                mismatch_count += 1
                continue

            fields = [
                ("played", current.played),
                ("points", current.points),
                ("goals_for", current.goals_for),
                ("goals_against", current.goals_against),
            ]

            for field, expected_value in fields:
                actual_value = to_int(reconstructed.get(field))

                if actual_value != expected_value:
                    print(
                        f"[WARN] Final reconstructed mismatch for {current.team}: "
                        f"{field} expected {expected_value}, got {actual_value}"
                    )
                    mismatch_count += 1

    if mismatch_count == 0:
        print("[INFO] Final reconstructed stats match official current standings.")
    else:
        print(f"[WARN] Reconstructed final validation found {mismatch_count} mismatch(es).")


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

    print(f"[INFO] Fetching official current standings feed: {OPTA_STANDINGS_URL}")
    standings_text = fetch_text(OPTA_STANDINGS_URL)
    standings_payload = unwrap_jsonp(standings_text)

    DEBUG_OPTA_STANDINGS_PATH.write_text(
        json.dumps(standings_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    current_standings, conference_by_team_id = parse_current_standings(
        standings_payload=standings_payload,
        team_lookup=team_lookup,
    )

    validate_counts(current_standings)

    print(f"[INFO] Fetching completed match results feed: {OPTA_MATCHES_URL}")
    matches_text = fetch_text(OPTA_MATCHES_URL)
    matches_payload = unwrap_jsonp(matches_text)

    DEBUG_OPTA_MATCHES_PATH.write_text(
        json.dumps(matches_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    matches = parse_matches(matches_payload)

    print(f"[INFO] Parsed {len(matches)} completed regular-season matches.")

    if matches:
        print(f"[INFO] Matchdays available: 1 to {max(match.matchday for match in matches)}")

    history_rows = reconstruct_history_rows(
        matches=matches,
        conference_by_team_id=conference_by_team_id,
        team_lookup=team_lookup,
    )

    validate_reconstructed_final_against_current(
        history_rows=history_rows,
        current_standings=current_standings,
    )

    write_current_json(
        standings=current_standings,
        fetched_at=fetched_at,
        snapshot_date=snapshot_date,
    )

    write_history_csv(history_rows)

    print(f"[INFO] Updated: {CURRENT_JSON_PATH}")
    print(f"[INFO] Updated: {HISTORY_CSV_PATH}")
    print(f"[INFO] Reconstructed history rows: {len(history_rows)}")

    for conference, rows in current_standings.items():
        print(f"[INFO] {conference}: {len(rows)} teams")

        if rows:
            leader = rows[0]
            print(
                f"[INFO] Current {conference} leader: "
                f"{leader.team} — {leader.points} pts"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
