import importlib
import json
import asyncio
import concurrent.futures
from copy import deepcopy
import logging
from data.mods.apply_mods import apply_mods

import data
from data.helpers import get_standard_battle_sets
import constants
from config import ShowdownConfig
from showdown.engine.evaluate import Scoring
from showdown.battle import Pokemon
from showdown.battle import LastUsedMove
from showdown.battle_modifier import async_update_battle

from showdown.websocket_client import PSWebsocketClient

logger = logging.getLogger(__name__)


def battle_is_finished(battle_tag, msg):
    return (
        msg.startswith(">{}".format(battle_tag)) and
        (constants.WIN_STRING in msg or constants.TIE_STRING in msg) and
        constants.CHAT_STRING not in msg
    )


async def async_pick_move(battle, team_preview=False):
    battle_copy = deepcopy(battle)
    if constants.TEAM_PREVIEW in battle_copy.request_json and battle_copy.request_json[constants.TEAM_PREVIEW]:
        pass
    else:
        battle_copy.user.from_json(battle_copy.request_json)
        battle_copy.user.active.seen = True

    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        best_move = await loop.run_in_executor(
            pool, battle_copy.find_best_move, team_preview
        )
    choice = best_move[0]
    if constants.SWITCH_STRING in choice:
        battle.user.last_used_move = LastUsedMove(battle.user.active.name, "switch {}".format(choice.split()[-1]), battle.turn)
    else:
        battle.user.last_used_move = LastUsedMove(battle.user.active.name, choice.split()[2], battle.turn)
    return best_move


async def handle_team_preview(battle, ps_websocket_client):
    battle_copy = deepcopy(battle)
    battle_copy.user.active = Pokemon.get_dummy()
    battle_copy.opponent.active = Pokemon.get_dummy()

    team_list_indexes = []

    if battle_copy.max_chosen_team_size == 6:
        best_move = await async_pick_move(battle_copy, team_preview=True)
        choice_digit = int(best_move[0].split()[-1])
        team_list_indexes.append(choice_digit)
        for j in range(battle_copy.max_chosen_team_size):
            if choice_digit != j+1:
                team_list_indexes.append(j+1)
    else:
        for i in range(battle_copy.max_chosen_team_size):
            best_move = await async_pick_move(battle_copy, team_preview=True)
            choice_digit = int(best_move[0].split()[-1])
            team_list_indexes.append(choice_digit)
            battle_copy.user.reserve[choice_digit - 1] = Pokemon.get_dummy()

    message = ["/team {}|{}".format("".join(str(x) for x in team_list_indexes), battle.rqid)]

    await ps_websocket_client.send_message(battle.battle_tag, message)


async def get_battle_tag_and_opponent(ps_websocket_client: PSWebsocketClient, msgs):
    msg_index = 0
    while True:
        if msg_index < len(msgs):
            msg = msgs[msg_index]
        else:
            msg = await ps_websocket_client.receive_message()
        split_msg = msg.split('|')
        first_msg = split_msg[0]
        if constants.BATTLE_STRING in first_msg:
            battle_tag = first_msg.replace('>', '').strip()
            user_name = split_msg[-1].replace('☆', '').strip()
            opponent_name = split_msg[4].replace(user_name, '').replace('vs.', '').strip()
            return battle_tag, opponent_name
        msg_index += 1


async def initialize_battle_with_tag(ps_websocket_client: PSWebsocketClient, msgs):
    battle_module = importlib.import_module('showdown.battle_bots.{}.main'.format(ShowdownConfig.battle_bot_module))

    battle_tag, opponent_name = await get_battle_tag_and_opponent(ps_websocket_client, msgs)
    msg_index = 0
    while True:
        if msg_index < len(msgs):
            msg = msgs[msg_index]
        else:
            msg = await ps_websocket_client.receive_message()
        split_msg = msg.split('|')
        if split_msg[1].strip() == constants.REQUEST_STRING and split_msg[2].strip():
            user_json = json.loads(split_msg[2].strip('\''))
            user_id = user_json[constants.SIDE][constants.ID]
            opponent_id = constants.ID_LOOKUP[user_id]
            battle = battle_module.BattleBot(battle_tag)
            battle.opponent.name = opponent_id
            battle.opponent.account_name = opponent_name
            battle.request_json = user_json

            return battle, opponent_id, user_json
        msg_index += 1


async def read_messages_until_first_pokemon_is_seen(msg, ps_websocket_client, battle, opponent_id, user_json):
    # keep reading messages until the opponent's first pokemon is seen
    # this is run when starting non team-preview battles
    split_msg = msg.split(constants.START_STRING)[-1].split('\n')
    for line in split_msg:
        if opponent_id in line and constants.SWITCH_STRING in line:
            battle.start_non_team_preview_battle(user_json, line)

        elif battle.started:
            await async_update_battle(battle, line)

    # first move needs to be picked here
    best_move = await async_pick_move(battle)
    await ps_websocket_client.send_message(battle.battle_tag, best_move)

    return


async def start_battle_internal(battle, opponent_id, pokemon_battle_type, generation, ps_websocket_client, msgs, user_json):
    battle.generation = generation

    msg_index = 0
    while True:
        if msg_index < len(msgs):
            msg = msgs[msg_index]
        else:
            msg = await ps_websocket_client.receive_message()
        if constants.START_TEAM_PREVIEW in msg or constants.START_STRING in msg:
            break
        msg_index += 1

    if constants.START_TEAM_PREVIEW not in msg:
        await read_messages_until_first_pokemon_is_seen(msg, ps_websocket_client, battle, opponent_id, user_json)
    else:
        preview_string_lines = msg.split(constants.START_TEAM_PREVIEW)[-1].split('\n')

        opponent_pokemon = []
        for line in preview_string_lines:
            if not line:
                continue

            split_line = line.split('|')
            if split_line[1] == constants.TEAM_PREVIEW_POKE and split_line[2].strip() == opponent_id:
                opponent_pokemon.append(split_line[3])

        battle.initialize_team_preview(user_json, opponent_pokemon, pokemon_battle_type)
        battle.during_team_preview()

        smogon_usage_data = get_standard_battle_sets(
            pokemon_battle_type,
            pokemon_names=set(p.name for p in battle.opponent.reserve + battle.user.reserve)
        )
        data.pokemon_sets = smogon_usage_data
        for pkmn, values in smogon_usage_data.items():
            data.effectiveness[pkmn] = values["effectiveness"]

        await handle_team_preview(battle, ps_websocket_client)


async def start_battle(ps_websocket_client, msgs, pokemon_battle_type, generation):
    battle, opponent_id, user_json = await initialize_battle_with_tag(ps_websocket_client, msgs)
    battle.generation = pokemon_battle_type[:4]

    if any([bt in pokemon_battle_type for bt in constants.RANDOM_TEAM_FORMATS]):
        Scoring.POKEMON_ALIVE_STATIC = 40  # random battle benefits from a lower static score for an alive pkmn
        battle.battle_type = constants.RANDOM_BATTLE
    else:
        battle.battle_type = constants.STANDARD_BATTLE

    await start_battle_internal(battle, opponent_id, pokemon_battle_type, generation, ps_websocket_client, msgs, user_json)

    await ps_websocket_client.send_message(battle.battle_tag, ["hf"])
    #await ps_websocket_client.send_message(battle.battle_tag, ['/timer on'])

    return battle


async def pokemon_battle(ps_websocket_client, msgs, pokemon_battle_type, generation):
    battle = await start_battle(ps_websocket_client, msgs, pokemon_battle_type, generation)
    while True:
        msg = await ps_websocket_client.receive_message()
        if battle_is_finished(battle.battle_tag, msg):
            if constants.WIN_STRING in msg:
                winner = msg.split(constants.WIN_STRING)[-1].split('\n')[0].strip()
            else:
                winner = None
            logger.debug("Winner: {}".format(winner))
            await ps_websocket_client.send_message(battle.battle_tag, ["gg"])
            await ps_websocket_client.leave_battle(battle.battle_tag, save_replay=ShowdownConfig.save_replay)
            return winner
        else:
            action_required = await async_update_battle(battle, msg)
            if action_required and not battle.wait:
                best_move = await async_pick_move(battle)
                await ps_websocket_client.send_message(battle.battle_tag, best_move)
