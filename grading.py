"""Pure grading helpers. Spreads are always stored from the HOME team's perspective
(negative = home favored), matching CollegeFootballData's convention."""


def grade_ats(side, home_spread, home_pts, away_pts):
    """Return 'W', 'L', 'P', or None for an against-the-spread pick ('home'/'away')."""
    if side not in ("home", "away") or home_spread is None or home_pts is None or away_pts is None:
        return None
    margin = home_pts + home_spread - away_pts
    if margin == 0:
        return "P"
    home_covers = margin > 0
    return "W" if home_covers == (side == "home") else "L"


def grade_ou(side, total, home_pts, away_pts):
    """Return 'W', 'L', 'P', or None for an over/under pick ('over'/'under')."""
    if side not in ("over", "under") or total is None or home_pts is None or away_pts is None:
        return None
    points = home_pts + away_pts
    if points == total:
        return "P"
    return "W" if (points > total) == (side == "over") else "L"


def format_spread(team, spread):
    """Format a spread for a team, e.g. ('Ohio State', -7) -> 'Ohio State -7'."""
    if spread is None:
        return f"{team} (no line)"
    if spread == 0:
        return f"{team} PK"
    num = f"{spread:+g}"
    return f"{team} {num}"
