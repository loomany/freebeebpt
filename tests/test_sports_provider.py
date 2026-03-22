import unittest
from unittest.mock import AsyncMock

from services.sports_provider import SportsProvider


class SportsProviderFixtureSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_find_fixture_uses_date_window_then_home_team_fallback(self):
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
        self.assertEqual(first_call.kwargs, {"from": "2026-03-27", "to": "2026-03-29"})
        second_call = provider._get.await_args_list[1]
        self.assertEqual(second_call.args, ("/fixtures",))
        self.assertEqual(second_call.kwargs, {"team": 1, "next": 10})

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


class SportsProviderDebugFixtureLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_debug_fixture_lookup_reports_wrong_comparison(self):
        provider = SportsProvider(api_key="test")
        provider._get = AsyncMock(
            side_effect=[
                {"response": [{"team": {"id": 1, "name": "Team 1"}}]},
                {"response": [{"team": {"id": 2, "name": "Team 2"}}]},
                {
                    "response": [
                        {
                            "fixture": {"id": 555, "date": "2026-03-28T20:00:00+00:00"},
                            "teams": {
                                "home": {"id": 1, "name": "Team 1"},
                                "away": {"id": 3, "name": "Team 3"},
                            },
                        }
                    ]
                },
            ]
        )

        debug = await provider.debug_fixture_lookup("Team 1", "Team 2", "2026-03-28T20:00:00")

        self.assertEqual(debug["selected_team_ids"], {"team1_id": 1, "team2_id": 2})
        self.assertEqual(debug["fixtures_request"], {"from": "2026-03-27", "to": "2026-03-29"})
        self.assertEqual(debug["fixtures_count"], 1)
        self.assertEqual(debug["first_10_fixtures"][0]["fixture.id"], 555)
        self.assertEqual(debug["found_match"], "NO")
        self.assertEqual(debug["reason"], "wrong comparison")
        self.assertIn("comparing fixture 555", debug["comparisons"][0])

    async def test_debug_fixture_lookup_reports_wrong_team_id(self):
        provider = SportsProvider(api_key="test")
        provider._get = AsyncMock(
            side_effect=[
                {"response": []},
                {"response": [{"team": {"id": 2, "name": "Team 2"}}]},
                {"response": []},
            ]
        )

        debug = await provider.debug_fixture_lookup("Unknown", "Team 2", "2026-03-28T20:00:00")

        self.assertEqual(debug["selected_team_ids"], {"team1_id": None, "team2_id": 2})
        self.assertEqual(debug["reason"], "wrong team_id")



class SportsProviderRequestedCaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_debug_fixture_lookup_for_stockport_county_vs_afc_wimbledon(self):
        provider = SportsProvider(api_key="test")
        provider._get = AsyncMock(
            side_effect=[
                {
                    "response": [
                        {"team": {"id": 101, "name": "Stockport"}},
                        {"team": {"id": 202, "name": "Stockport County"}},
                    ]
                },
                {
                    "response": [
                        {"team": {"id": 303, "name": "Wimbledon"}},
                        {"team": {"id": 404, "name": "AFC Wimbledon"}},
                    ]
                },
                {
                    "response": [
                        {
                            "fixture": {"id": 555, "date": "2026-03-28T15:00:00+00:00"},
                            "teams": {
                                "home": {"id": 202, "name": "Stockport County"},
                                "away": {"id": 404, "name": "AFC Wimbledon"},
                            },
                        }
                    ]
                },
            ]
        )

        debug = await provider.debug_fixture_lookup("Stockport County", "AFC Wimbledon", "2026-03-28")

        self.assertEqual(debug["selected_team_ids"], {"team1_id": 202, "team2_id": 404})
        self.assertEqual(debug["found_match"], "YES")
        self.assertEqual(debug["reason"], "match found")

class SportsProviderTeamSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_team_id_uses_nested_team_structure_and_best_name_match(self):
        provider = SportsProvider(api_key="test")
        provider._get = AsyncMock(
            return_value={
                "response": [
                    {"team": {"id": 999, "name": "Wimbledon FC"}},
                    {"team": {"id": 2, "name": "AFC Wimbledon"}},
                ]
            }
        )

        team_id = await provider.resolve_team_id("AFC Wimbledon")

        self.assertEqual(team_id, 2)

    async def test_search_team_returns_selected_response_item(self):
        provider = SportsProvider(api_key="test")
        provider._get = AsyncMock(
            return_value={
                "response": [
                    {"team": {"id": 11, "name": "Stockport"}},
                    {"team": {"id": 22, "name": "Stockport County"}, "venue": {"id": 5, "name": "Edgeley Park"}},
                ]
            }
        )

        team_data = await provider.search_team("Stockport County")

        self.assertEqual((team_data or {}).get("team", {}).get("id"), 22)
        self.assertEqual((team_data or {}).get("team", {}).get("name"), "Stockport County")
