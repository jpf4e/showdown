import asyncio
import json
import logging
import traceback
from datetime import datetime
from copy import deepcopy

import constants
from config import ShowdownConfig, init_logging

from teams import load_team
from showdown.run_battle import pokemon_battle
from showdown.websocket_client import PSWebsocketClient

import data
from data.mods.apply_mods import apply_mods

logger = logging.getLogger(__name__)


def check_dictionaries_are_unmodified(original_pokedex, original_move_json):
    # The bot should not modify the data dictionaries
    # This is a "just-in-case" check to make sure and will stop the bot if it mutates either of them
    if original_move_json != data.all_move_json:
        logger.critical("Move JSON changed!\nDumping modified version to `modified_moves.json`")
        with open("modified_moves.json", 'w') as f:
            json.dump(data.all_move_json, f, indent=4)
        exit(1)
    else:
        logger.debug("Move JSON unmodified!")

    if original_pokedex != data.pokedex:
        logger.critical(
            "Pokedex JSON changed!\nDumping modified version to `modified_pokedex.json`"
        )
        with open("modified_pokedex.json", 'w') as f:
            json.dump(data.pokedex, f, indent=4)
        exit(1)
    else:
        logger.debug("Pokedex JSON unmodified!")


async def showdown():
    ShowdownConfig.configure()
    init_logging(
        ShowdownConfig.log_level,
        ShowdownConfig.log_to_file
    )

    original_pokedex = deepcopy(data.pokedex)
    original_move_json = deepcopy(data.all_move_json)

    ps_websocket_client = await PSWebsocketClient.create(
        ShowdownConfig.username,
        ShowdownConfig.password,
        ShowdownConfig.websocket_uri
    )
    await ps_websocket_client.login()

    battles_run = 0
    wins = 0
    losses = 0
    battle_format = None
    while True:
        if ShowdownConfig.log_to_file:
            ShowdownConfig.log_handler.do_rollover(datetime.now().strftime("%Y-%m-%dT%H:%M:%S.log"))
        team = load_team(ShowdownConfig.team)
        if ShowdownConfig.bot_mode == constants.CHALLENGE_USER:
            await ps_websocket_client.challenge_user(
                ShowdownConfig.user_to_challenge,
                ShowdownConfig.pokemon_mode,
                team
            )
        elif ShowdownConfig.bot_mode == constants.ACCEPT_CHALLENGE:
            battle_format = await ps_websocket_client.accept_challenge(
                team,
                ShowdownConfig.room_name
            )
        elif ShowdownConfig.bot_mode == constants.SEARCH_LADDER:
            await ps_websocket_client.search_for_match(ShowdownConfig.pokemon_mode, team)
        else:
            raise ValueError("Invalid Bot Mode: {}".format(ShowdownConfig.bot_mode))

        msgs = []
        msg = ''
        while constants.GEN_STRING not in msg:
            msg = await ps_websocket_client.receive_message()
            msgs.append(msg)

        generation = int(msg.split(constants.GEN_STRING)[-1].split('\n')[0])
        logger.debug("Battling in gen {}".format(generation))
        apply_mods(generation)

        original_pokedex_after_mods = deepcopy(data.pokedex)
        original_move_json_after_mods = deepcopy(data.all_move_json)

        winner = await pokemon_battle(ps_websocket_client, msgs, battle_format if battle_format is not None else ShowdownConfig.pokemon_mode, generation)
        if winner == ShowdownConfig.username:
            wins += 1
        else:
            losses += 1

        logger.info("W: {}\tL: {}".format(wins, losses))
        check_dictionaries_are_unmodified(original_pokedex_after_mods, original_move_json_after_mods)

        apply_mods(generation, revert=True)
        check_dictionaries_are_unmodified(original_pokedex, original_move_json)

        battles_run += 1
        if 0 < ShowdownConfig.run_count <= battles_run:
            break


if __name__ == "__main__":
    try:
        asyncio.run(showdown())
    except Exception as e:
        logger.error(traceback.format_exc())
        raise
