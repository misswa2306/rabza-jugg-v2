import unittest
import random
from unittest.mock import patch

from game import (
    BASE_SUCCESS_RATE,
    PRISON_DURATION_SECONDS,
    prison_deadline,
    resolve_action,
)


class ResolveActionTests(unittest.TestCase):
    def test_vehicle_success_awards_cash_heat_and_car(self) -> None:
        result = resolve_action("steal_rav4", roll=0.49)
        self.assertEqual(result.cash_delta, 350)
        self.assertEqual(result.heat_delta, 12)
        self.assertEqual(result.gamos, "RAV4")
        self.assertIsNone(result.prison_seconds)

    def test_vehicle_failure_sends_player_to_prison(self) -> None:
        result = resolve_action("steal_lexus", roll=0.7)
        self.assertEqual(result.cash_delta, 0)
        self.assertEqual(result.prison_seconds, PRISON_DURATION_SECONDS)

    def test_jugg_success_threshold_is_70_percent(self) -> None:
        self.assertEqual(resolve_action("steal_rav4", roll=0.599).cash_delta, 350)
        self.assertEqual(resolve_action("steal_rav4", roll=0.699).cash_delta, 350)
        self.assertEqual(
            resolve_action("steal_rav4", roll=0.7).prison_seconds,
            PRISON_DURATION_SECONDS,
        )

    def test_marketplace_success_threshold_is_70_percent(self) -> None:
        self.assertEqual(resolve_action("marketplace", roll=0.699).cash_delta, 250)
        self.assertEqual(
            resolve_action("marketplace", roll=0.7).prison_seconds,
            PRISON_DURATION_SECONDS,
        )

    def test_berri_success_threshold_is_70_percent(self) -> None:
        self.assertEqual(resolve_action("berri", roll=0.699).cash_delta, 120)
        self.assertEqual(
            resolve_action("berri", roll=0.7).prison_seconds,
            PRISON_DURATION_SECONDS,
        )

    def test_random_simulation_of_100_juggs_is_balanced(self) -> None:
        rng = random.Random(2026)
        with patch("game.random.random", side_effect=rng.random):
            results = [resolve_action("bixi") for _ in range(100)]

        successes = sum(result.success for result in results)
        failures = len(results) - successes
        self.assertGreaterEqual(successes, 60)
        self.assertLessEqual(successes, 80)
        self.assertGreater(failures, 0)
        self.assertEqual(BASE_SUCCESS_RATE, 0.70)

    def test_prison_deadline_uses_45_seconds(self) -> None:
        import time

        with patch("game.time.time", return_value=1_700_000_000):
            deadline = prison_deadline()
        self.assertEqual(deadline, 1_700_000_045)
        before = time.time()
        deadline = prison_deadline()
        elapsed = deadline - before
        self.assertGreaterEqual(elapsed, PRISON_DURATION_SECONDS - 0.01)
        self.assertLessEqual(elapsed, PRISON_DURATION_SECONDS + 0.01)

    def test_level_progression_and_level_cap(self) -> None:
        from game import level_for_xp, xp_to_next_level

        self.assertEqual(level_for_xp(99), 1)
        self.assertEqual(level_for_xp(100), 2)
        self.assertEqual(level_for_xp(299), 2)
        self.assertEqual(level_for_xp(300), 3)
        self.assertEqual(level_for_xp(177000), 60)
        self.assertEqual(level_for_xp(999999), 60)
        self.assertEqual(xp_to_next_level(300, 3), 300)
        self.assertEqual(xp_to_next_level(177000, 60), 0)

    def test_action_unlocks_and_obd_gate(self) -> None:
        from game import actions_available

        level_one = {action for action, _ in actions_available(1, False)}
        self.assertEqual(level_one, {"berri", "bixi"})
        level_ten = {action for action, _ in actions_available(10, False)}
        self.assertTrue({"berri", "bixi", "dep", "marketplace"} <= level_ten)
        level_twenty_with_obd = {
            action for action, _ in actions_available(20, True)
        }
        self.assertIn("steal_rav4", level_twenty_with_obd)
        self.assertNotIn("jewelry", level_twenty_with_obd)

    def test_high_tier_equipment_bonus_applies(self) -> None:
        result = resolve_action("port", roll=0.40, success_bonus=0.15)
        self.assertTrue(result.success)
        self.assertEqual(result.xp_gain, 160)

    def test_equipment_bonus_applies_to_classic_juggs(self) -> None:
        without_item = resolve_action("bixi", roll=0.71)
        with_item_bonus = resolve_action("bixi", roll=0.71, success_bonus=0.02)

        self.assertFalse(without_item.success)
        self.assertTrue(with_item_bonus.success)

    def test_equipment_bonus_is_capped_below_guaranteed_success(self) -> None:
        self.assertFalse(
            resolve_action("berri", roll=0.95, success_bonus=0.5).success
        )

    def test_pvp_success_rate_depends_on_attacker_weapon(self) -> None:
        from game import resolve_pvp

        unarmed = resolve_pvp(
            attacker_has_weapon=False,
            victim_has_weapon=False,
            attack_roll=0.50,
            counter_roll=0.99,
            transfer_roll=0.5,
        )
        armed = resolve_pvp(
            attacker_has_weapon=True,
            victim_has_weapon=False,
            attack_roll=0.64,
            counter_roll=0.99,
            transfer_roll=0.5,
        )
        self.assertEqual(unarmed.outcome, "failed")
        self.assertEqual(armed.outcome, "success")
        self.assertEqual(armed.transfer_rate, 0.25)

    def test_victim_weapon_riposte_chance_is_60_percent(self) -> None:
        from game import resolve_pvp

        counter = resolve_pvp(
            attacker_has_weapon=False,
            victim_has_weapon=True,
            attack_roll=0.1,
            counter_roll=0.59,
        )
        no_counter = resolve_pvp(
            attacker_has_weapon=False,
            victim_has_weapon=True,
            attack_roll=0.1,
            counter_roll=0.6,
            transfer_roll=0.9,
        )
        self.assertEqual(counter.outcome, "counter")
        self.assertEqual(counter.prison_seconds, PRISON_DURATION_SECONDS)
        self.assertEqual(counter.fine, 500)
        self.assertEqual(no_counter.outcome, "success")
        self.assertAlmostEqual(no_counter.transfer_rate, 0.33)

    @patch("game.random.random", return_value=0.69)
    def test_default_roll_uses_random_module(self, random_roll) -> None:
        result = resolve_action("steal_rav4")
        random_roll.assert_called_once_with()
        self.assertEqual(result.cash_delta, 350)

    def test_unknown_action_and_invalid_roll_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve_action("unknown", roll=0.2)
        with self.assertRaises(ValueError):
            resolve_action("berri", roll=1)


if __name__ == "__main__":
    unittest.main()
