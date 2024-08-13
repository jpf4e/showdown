import os
import json
import logging

logger = logging.getLogger(__name__)

PWD = os.path.dirname(os.path.abspath(__file__))

all_move_json = None
pokedex = None
random_battle_sets = None


def load_moves_and_pokedex():
    global all_move_json, pokedex
    move_json_location = os.path.join(PWD, 'moves.json')
    with open(move_json_location) as f:
        all_move_json = json.load(f)

    pkmn_json_location = os.path.join(PWD, 'pokedex.json')
    with open(pkmn_json_location, 'r') as f:
        pokedex = json.loads(f.read())


def load_random_battle_set():
    global random_battle_sets
    random_battle_set_location = os.path.join(PWD, 'random_battle_sets.json')
    with open(random_battle_set_location, 'r') as f:
        random_battle_sets = json.load(f)


load_moves_and_pokedex()
load_random_battle_set()

pokemon_sets = random_battle_sets
effectiveness = {}
team_datasets = None
