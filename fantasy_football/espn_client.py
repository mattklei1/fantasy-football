"""Thin, reusable wrapper around espn_api.football.League.

Centralizes ESPN authentication and caches League objects per season so we
never re-fetch the same season's league object more than once per process.
All ingestion and metric code should go through this client rather than
constructing espn_api.football.League directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from espn_api.football import League

from .config import ESPNCredentials, load_espn_credentials


@dataclass
class LeagueSummary:
    league_id: int
    season: int
    league_name: str
    current_week: int
    team_count: int
    team_names: list[str]
    previous_seasons: list[int]


class ESPNClient:
    """Reusable client for pulling data from a private ESPN fantasy league."""

    def __init__(self, credentials: Optional[ESPNCredentials] = None):
        self.credentials = credentials or load_espn_credentials()
        self._league_cache: dict[int, League] = {}

    def get_league(self, season: Optional[int] = None) -> League:
        """Return the (cached) League object for a given season."""
        season = season or self.credentials.current_season
        if season not in self._league_cache:
            self._league_cache[season] = League(
                league_id=self.credentials.league_id,
                year=season,
                espn_s2=self.credentials.espn_s2,
                swid=self.credentials.swid,
            )
        return self._league_cache[season]

    def available_seasons(self) -> list[int]:
        """All seasons ESPN reports as available, current season first,
        followed by prior seasons newest-to-oldest."""
        league = self.get_league(self.credentials.current_season)
        return [self.credentials.current_season] + list(league.previousSeasons)

    def summary(self, season: Optional[int] = None) -> LeagueSummary:
        league = self.get_league(season)
        return LeagueSummary(
            league_id=self.credentials.league_id,
            season=league.year,
            league_name=league.settings.name,
            current_week=league.current_week,
            team_count=len(league.teams),
            team_names=[team.team_name for team in league.teams],
            previous_seasons=list(league.previousSeasons),
        )
