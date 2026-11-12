import unittest
import random
from unittest.mock import call, patch

from game import (
    BASE_SUCCESS_RATE,
    JUGG_TIERS,
    PRISON_DURATION_SECONDS,
    prison_deadline,
    resolve_action,
    resolve_jugg,
)


class ResolveActionTests(unittest.TestCase):
    def test_jugg_tiers_have_fixed_rewards_and_bounded_cooldowns(self) -> None:
        self.assertEqual(
            [
                (tier.tier_id, tier.success_reward, tier.cooldown_seconds)
                for tier in JUGG_TIERS
            ],
            [
                ("bloc", 1200, 15),
                ("centre_ville", 4500, 25),
                ("international", 15000, 30),
                ("elite", 25000, 35),
            ],
        )
        self.assertEqual(
            [tier.min_level for tier in JUGG_TIERS],
            [1, 3, 7, 15],
        )

    def test_jugg_success_and_failure_resolve_to_game_only_outcomes(self) -> None:
        success = resolve_jugg("bloc", success_roll=0.1)
        fine = resolve_jugg("centre_ville", success_roll=0.9, setback_roll=0.1)
        prison = resolve_jugg("international", success_roll=0.9, setback_roll=0.9)

        self.assertTrue(success.success)
        self.assertEqual(success.cash_delta, 1200)
        self.assertEqual(success.prison_seconds, 0)
        self.assertIn("arcade", success.message)
        self.assertEqual(fine.cash_delta, -700)
        self.assertEqual(fine.prison_seconds, 0)
        self.assertEqual(prison.cash_delta, 0)
        self.assertEqual(prison.prison_seconds, 20)
        with self.assertRaises(ValueError):
            resolve_jugg("unknown", success_roll=0.1)

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
        with patch("game.time.time", return_value=1_700_000_000):
            self.assertEqual(prison_deadline(90), 1_700_000_090)
        with self.assertRaises(ValueError):
            prison_deadline(0)
        before = time.time()
        deadline = prison_deadline()
        elapsed = deadline - before
        self.assertGreaterEqual(elapsed, PRISON_DURATION_SECONDS - 0.01)
        self.assertLessEqual(elapsed, PRISON_DURATION_SECONDS + 0.01)

    def test_level_progression_and_level_cap(self) -> None:
        from game import level_for_xp, xp_to_next_level, xp_with_bonus

        self.assertEqual(level_for_xp(99), 1)
        self.assertEqual(level_for_xp(100), 2)
        self.assertEqual(level_for_xp(299), 2)
        self.assertEqual(level_for_xp(300), 3)
        self.assertEqual(level_for_xp(177000), 60)
        self.assertEqual(level_for_xp(999999), 60)
        self.assertEqual(xp_to_next_level(300, 3), 300)
        self.assertEqual(xp_to_next_level(177000, 60), 0)
        self.assertEqual(xp_with_bonus(30, 0), 30)
        self.assertEqual(xp_with_bonus(30, 20), 36)
        self.assertEqual(xp_with_bonus(1, 10), 1)

    def test_all_cash_reward_catalogues_assign_positive_xp(self) -> None:
        from game import ACTION_SPECS, HUSTLES, JUGG_TIERS
        from bot import MISSIONS

        self.assertTrue(
            all(
                action.reward <= 0 or action.xp > 0
                for action in ACTION_SPECS.values()
            )
        )
        self.assertTrue(
            all(
                hustle.success_reward <= 0 or hustle.xp > 0 for hustle in HUSTLES
            )
        )
        self.assertTrue(
            all(
                tier.success_reward <= 0 or tier.xp > 0 for tier in JUGG_TIERS
            )
        )
        self.assertTrue(
            all(
                mission.reward <= 0 or mission.xp > 0 for mission in MISSIONS
            )
        )

    def test_action_unlocks_and_obd_gate(self) -> None:
        from game import actions_available

        level_one = {action for action, _ in actions_available(1, False)}
        self.assertEqual(level_one, {"berri", "bixi"})
        level_ten = {action for action, _ in actions_available(10, False)}
        self.assertTrue({"berri", "bixi", "dep", "marketplace"} <= level_ten)
        level_five = {action for action, _ in actions_available(5, False)}
        self.assertIn("dep", level_five)
        self.assertNotIn("port", level_five)
        level_seven = {action for action, _ in actions_available(7, False)}
        self.assertIn("port", level_seven)
        self.assertNotIn("jewelry", level_seven)
        level_ten_with_obd = {action for action, _ in actions_available(10, True)}
        self.assertIn("steal_rav4", level_ten_with_obd)
        level_fifteen = {action for action, _ in actions_available(15, True)}
        self.assertTrue({"steal_lexus", "jewelry", "port"} <= level_fifteen)
        level_twenty_with_obd = {
            action for action, _ in actions_available(20, True)
        }
        self.assertIn("steal_rav4", level_twenty_with_obd)
        self.assertIn("jewelry", level_twenty_with_obd)

    def test_missions_follow_the_progression_milestones(self) -> None:
        from bot import MISSIONS

        levels = {mission.mission_id: mission.min_level for mission in MISSIONS}
        self.assertEqual(
            {
                "civic": levels["civic"],
                "audi": levels["audi"],
                "entrepot": levels["entrepot"],
                "depot": levels["depot"],
                "port": levels["port"],
                "blindee": levels["blindee"],
                "crypto": levels["crypto"],
                "bijouterie": levels["bijouterie"],
                "banque_centrale": levels["banque_centrale"],
            },
            {
                "civic": 1,
                "audi": 3,
                "entrepot": 5,
                "depot": 5,
                "port": 7,
                "blindee": 10,
                "crypto": 10,
                "bijouterie": 15,
                "banque_centrale": 20,
            },
        )

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

    def test_pvp_default_rolls_use_integer_random_draws(self) -> None:
        from game import resolve_pvp

        with patch("game.random.randint", side_effect=[0, 9999, 0]) as draw:
            result = resolve_pvp(False, False)

        self.assertEqual(result.outcome, "success")
        self.assertEqual(
            draw.call_args_list,
            [call(0, 9_999)] * 3,
        )

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

    def test_stronger_weapons_improve_robbery_and_target_defense(self) -> None:
        from game import resolve_pvp

        weaker_weapon = resolve_pvp(
            attacker_has_weapon=True,
            victim_has_weapon=False,
            attacker_weapon_power=1,
            victim_weapon_power=0,
            attack_roll=0.70,
            counter_roll=0.99,
            transfer_roll=0.99,
        )
        stronger_weapon = resolve_pvp(
            attacker_has_weapon=True,
            victim_has_weapon=False,
            attacker_weapon_power=3,
            victim_weapon_power=0,
            attack_roll=0.70,
            counter_roll=0.99,
            transfer_roll=0.99,
        )
        victim_armed = resolve_pvp(
            attacker_has_weapon=True,
            victim_has_weapon=True,
            attacker_weapon_power=1,
            victim_weapon_power=3,
            attack_roll=0.1,
            counter_roll=0.70,
            transfer_roll=0.5,
        )
        self.assertEqual(weaker_weapon.outcome, "failed")
        self.assertEqual(stronger_weapon.outcome, "success")
        self.assertGreater(stronger_weapon.transfer_rate, 0.35)
        self.assertEqual(victim_armed.outcome, "counter")

    def test_shootout_is_power_weighted_and_reports_police_intervention(self) -> None:
        from game import resolve_shootout

        attacker_wins = resolve_shootout(
            3, 1, outcome_roll=0.70, police_roll=0.99
        )
        target_wins_with_police = resolve_shootout(
            1, 3, outcome_roll=0.40, police_roll=0.1
        )
        self.assertEqual(attacker_wins.winner, "attacker")
        self.assertEqual(attacker_wins.xp_gain, 175)
        self.assertFalse(attacker_wins.police_intervention)
        self.assertEqual(target_wins_with_police.winner, "victim")
        self.assertTrue(target_wins_with_police.police_intervention)
        self.assertEqual(target_wins_with_police.xp_gain, 175)

    def test_virtual_protection_adds_defense_to_pvp_without_a_weapon(self) -> None:
        from game import resolve_pvp

        defended = resolve_pvp(
            attacker_has_weapon=False,
            victim_has_weapon=False,
            attack_roll=0.1,
            counter_roll=0.34,
            victim_has_armor=True,
        )
        not_defended = resolve_pvp(
            attacker_has_weapon=False,
            victim_has_weapon=False,
            attack_roll=0.1,
            counter_roll=0.35,
            victim_has_armor=True,
        )
        self.assertEqual(defended.outcome, "counter")
        self.assertEqual(not_defended.outcome, "success")

    def test_ballistic_protection_reduces_robbery_chance_and_keeps_ten_percent_floor(
        self,
    ) -> None:
        from game import resolve_pvp

        for protection, attack_roll in ((0.10, 0.45), (0.20, 0.35), (0.35, 0.20)):
            with self.subTest(protection=protection):
                result = resolve_pvp(
                    attacker_has_weapon=False,
                    victim_has_weapon=False,
                    attack_roll=attack_roll,
                    counter_roll=0.99,
                    transfer_roll=0.5,
                    victim_protection=protection,
                    victim_protection_item_id="shield_tactique",
                    victim_protection_name="Bouclier",
                )
                self.assertEqual(result.outcome, "failed")
                self.assertTrue(result.protection_blocked)
                self.assertEqual(result.protection_item_id, "shield_tactique")
                self.assertEqual(result.protection_name, "Bouclier")

        floor_success = resolve_pvp(
            attacker_has_weapon=False,
            victim_has_weapon=False,
            attack_roll=0.09,
            counter_roll=0.99,
            victim_protection=0.90,
        )
        floor_failure = resolve_pvp(
            attacker_has_weapon=False,
            victim_has_weapon=False,
            attack_roll=0.10,
            counter_roll=0.99,
            victim_protection=0.90,
        )
        self.assertEqual(floor_success.outcome, "success")
        self.assertEqual(floor_failure.outcome, "failed")
        self.assertTrue(floor_failure.protection_blocked)

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
