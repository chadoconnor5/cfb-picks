"""Injury / availability report for one game, gathered by Claude with web search."""
import os
import re

import anthropic

MODEL = os.getenv("PICKS_CLAUDE_MODEL", "claude-opus-5-5")
MAX_CONTINUATIONS = 4

SYSTEM = """You research college football injury and availability news for one game.
Search for the most recent official availability reports (conferences such as the SEC, Big Ten,
Big 12 and ACC publish them before conference games) and credible beat-reporter news.

Write a concise report:
- One section per team, headed by the team name.
- One bullet per notable player: name, position, status (out / doubtful / questionable / probable /
  returning), and the date of the report or news.
- Prioritize QBs, skill players, offensive and defensive linemen, and anyone who changes how the
  team plays (for example, a pass-heavy team losing its QB or top receivers).
- Ignore anything older than about 10 days unless it is a season-ending injury.
- If you can't find reliable news for a team, say "No reliable injury news found." Never guess.
End with one line: "Style impact:" and a sentence on whether any absence changes how either team
is likely to play."""


def _sources(content):
    seen, out = set(), []
    for b in content:
        for c in getattr(b, "citations", None) or []:
            url = getattr(c, "url", None)
            if url and url not in seen:
                seen.add(url)
                out.append({"url": url, "title": getattr(c, "title", None) or url})
    return out


def fetch_report(game, cutoff=None):
    client = anthropic.Anthropic()
    kickoff = game.get("start_date") or "this week"
    user = (f"Game: {game['away_team']} at {game['home_team']}, season {game['season']} week {game['week']}, "
            f"kickoff {kickoff}. Find the latest injury and availability news for both teams.")
    if cutoff:
        user += (f" CUTOFF: {cutoff.strftime('%Y-%m-%d %H:%M UTC')}. Use only reports published on or before the "
                 "cutoff; ignore anything later (including game-day availability reports) and give each item's date.")
    messages = [{"role": "user", "content": user}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]

    for _ in range(MAX_CONTINUATIONS + 1):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium"},
            system=SYSTEM,
            tools=tools,
            messages=messages,
        )
        if response.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": response.content})
    else:
        raise RuntimeError("Injury search didn't finish; try again.")

    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined the injury search.")
    # Keep only the write-up after the last search (drops "Let me search..." preambles).
    blocks = list(response.content)
    last_search = max((i for i, b in enumerate(blocks) if b.type.endswith("tool_result")), default=-1)
    final = [b for b in blocks[last_search + 1:] if b.type == "text"]
    text = "".join(b.text for b in final).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        raise RuntimeError("Injury search returned no report.")
    return {"report": text, "sources": _sources(final)}
