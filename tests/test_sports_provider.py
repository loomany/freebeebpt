import unittest
from unittest.mock import AsyncMock

from services.sports_provider import SportsProvider


class SportsProviderFixtureSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_find_fixture_uses_date_then_home_team_fallback(self):
        provider = SportsProvider(api_key="test")
        provider.search_team = AsyncMock(side_effect=[{"team": {"id": 1}}, {"team": {"id": 2}}])
        provider._get = AsyncMock(
            side_effect=[
                {"response": [{"teams": {"home": {"id": 9}, "away": {"id": 10}}}]},
                {"response": [{"teams": {"home": {"id": 2}, "away": {"id": 1}}}]},
            ]
        )

        fixture = await provider.find_fixture("Team 1", "Team 2", "2026-03-28T19:00:00Z")

        self.assertIsNotNone(fixture)
        self.assertEqual(provider._get.await_count, 2)
        first_call = provider._get.await_args_list[0]
        self.assertEqual(first_call.args, ("/fixtures",))
        self.assertEqual(first_call.kwargs, {"date": "2026-03-28", "include": "league,teams,goals,fixture.status"})
        second_call = provider._get.await_args_list[1]
        self.assertEqual(second_call.args, ("/fixtures",))
        self.assertEqual(second_call.kwargs, {"team": 1, "next": 10, "include": "league,teams,goals,fixture.status"})

    async def test_find_fixture_returns_none_when_not_found(self):
        provider = SportsProvider(api_key="test")
        provider.search_team = AsyncMock(side_effect=[{"team": {"id": 1}}, {"team": {"id": 2}}])
        provider._get = AsyncMock(
            side_effect=[
                {"response": []},
                {"response": [{"teams": {"home": {"id": 1}, "away": {"id": 3}}}]},
            ]
        )

        fixture = await provider.find_fixture("Team 1", "Team 2", "2026-03-28")

        self.assertIsNone(fixture)
        self.assertEqual(provider._get.await_count, 2)


if __name__ == "__main__":
    unittest.main()
