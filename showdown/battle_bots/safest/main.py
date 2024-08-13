from showdown.battle import Battle

from ..helpers import format_decision
from ..helpers import pick_safest_move_using_dynamic_search_depth


class BattleBot(Battle):
    def __init__(self, *args, **kwargs):
        super(BattleBot, self).__init__(*args, **kwargs)

    def find_best_move(self, team_preview=False):
        battles = self.prepare_battles(join_moves_together=True)
        safest_move = pick_safest_move_using_dynamic_search_depth(battles, team_preview)
        return format_decision(self, safest_move)
