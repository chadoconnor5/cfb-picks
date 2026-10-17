"""Synthetic CFBD analytics: a spread of teams so percentiles are meaningful."""


def adv(team, pass_rate, pass_ppa, rush_ppa, pass_expl=1.3, def_pass=0.15, def_rush=0.12, plays=700):
    return {
        "season": 2026, "team": team, "conference": "Test",
        "offense": {"plays": plays, "ppa": (pass_ppa * pass_rate + rush_ppa * (1 - pass_rate)), "successRate": 0.44,
                    "explosiveness": 1.2,
                    "passingPlays": {"rate": pass_rate, "ppa": pass_ppa, "successRate": 0.45, "explosiveness": pass_expl},
                    "rushingPlays": {"rate": 1 - pass_rate, "ppa": rush_ppa, "successRate": 0.43, "explosiveness": 1.0}},
        "defense": {"plays": 680, "ppa": (def_pass + def_rush) / 2, "successRate": 0.40, "havoc": {"total": 0.17},
                    "passingPlays": {"rate": 0.5, "ppa": def_pass}, "rushingPlays": {"rate": 0.5, "ppa": def_rush}},
    }


ADVANCED = [
    # Air raid: pass-heavy, explosive, poor run game.
    adv("Air Raid U", 0.62, 0.40, 0.00, pass_expl=1.7),
    # Triple option: run-heavy, efficient on the ground.
    adv("Option Tech", 0.22, 0.10, 0.25, pass_expl=1.1),
    # Elite run defense.
    adv("Wall State", 0.48, 0.15, 0.12, def_rush=-0.10),
] + [adv(f"Filler {i}", 0.40 + i * 0.02, 0.10 + i * 0.01, 0.05 + i * 0.01) for i in range(10)]

SP = [{"team": "Air Raid U", "rating": 12.5, "ranking": 15, "offense": {"rating": 38.0}, "defense": {"rating": 25.5}}]
STATS = [{"team": t["team"], "statName": "games", "statValue": 7} for t in ADVANCED]
