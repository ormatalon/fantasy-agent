"""Best lineup for a set of slots: a maximum-weight assignment of players to slots."""

from decisions.league import ACCEPTS

FORBIDDEN = -1e9
KEEP = 0.1  # a player already in a slot stays unless a change gains more than this: no equal-value reshuffles
FILL = 0.001  # a slot with an eligible player is never left empty, even for a 0-point player


def hungarian(weight: list[list[float]]) -> list[int]:
    """Column chosen for each row (rows <= columns) maximising the total weight."""
    n, m = len(weight), len(weight[0])
    u, v, p, way = [0.0] * (n + 1), [0.0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [float("inf")] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], float("inf"), 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = -weight[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    out = [0] * n
    for j in range(1, m + 1):
        if p[j]:
            out[p[j] - 1] = j - 1
    return out


def fill_slots(slots: list[str], current: list[str | None], positions: dict[str, str], value: dict[str, float],
               locked: set[int] = frozenset()) -> list[str | None]:  # fmt: skip
    """The player for each slot (None = empty) with the highest total value.

    positions/value: the players that may move (not locked, not on IR). locked: slot indexes whose current
    player stays; those slots and players are not part of the choice.
    """
    free = [i for i in range(len(slots)) if i not in locked]
    out = [current[i] if i in locked else None for i in range(len(slots))]
    players = list(positions)
    if not free or not players:
        return out
    rows = [
        [value[p] + FILL + (KEEP if current[i] == p else 0.0) if positions[p] in ACCEPTS[slots[i]] else FORBIDDEN
         for p in players] + [0.0] * len(free)  # one "stays empty" column per slot
        for i in free
    ]
    for i, j in zip(free, hungarian(rows)):
        out[i] = players[j] if j < len(players) and rows[free.index(i)][j] > FORBIDDEN / 2 else None
    return out


def best_total(slots: list[str], positions: dict[str, str], value: dict[str, float]) -> float:
    """Total value of the best lineup (no keep or fill bonus): used for roster values and optimal points."""
    lineup = fill_slots(slots, [None] * len(slots), positions, value)
    return sum(value[p] for p in lineup if p)
