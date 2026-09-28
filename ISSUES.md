## Bugs

1. If GMAIL don't exist it may break.
2. The agent can't handle more then a single league. How to solve it?
3. Assaf tried and for some reason the agent decided it's 2024 and said it can't retrieve 2026. Maybe because he didn't put the gmail?
4. The prompt say it's IDP league. I don't want to bound it.
5. Tool description is to thin. should be more detailed. improve docstrings in file tools.py
6. Add graph.py to create the graph

## Issues
1. When offering waivers it only considers the next week projection.
2. When offering waivers for next week it doesn't consider if the player is available this week. It should look if the players is not held by another participant and that the player has yet to play this week.
3. Seems to struggle with complex tasks. For example, I asked it to find a currently injured player that I can lift from waivers. It should consider the yearly projections, history and injury severity. **It may be due to the simple LLM I use**. 
4. The agent can't get last week results and can't understand my last week opponent to give me a recap.
5. Doesn't consider player status in general. i.e. IR, own by another player etc.
6. It can't get news for players outside of the roster. I need to add an option for this.

## Additions
1. Twitter beatwriters. How to find them? How to screen? maybe by number of followers. You can find a list of bitwriters for each NFL team and their twitter.
2. Reddit channels. Can give you null momentum. whatch out. Assaf doesn't recommend.
3. FantasyPros predictions.
4. The Athletics

## Upgrades
1. Long term memory. It remembers all historical conversations.