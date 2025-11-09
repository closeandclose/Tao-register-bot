import asyncio
import math
import time
from datetime import timedelta, datetime
from bittensor import Balance
from bittensor_wallet import Wallet
from bittensor.core.async_subtensor import AsyncSubtensor
from bittensor.core.metagraph import AsyncMetagraph
from bittensor.core.config import Config
from dotenv import load_dotenv
import os

load_dotenv()

REGISTER_COST_LIMIT = Balance(float(os.getenv("REGISTER_COST_LIMIT")))
WALLET_PWD = os.getenv("WALLET_PASSWORD")

async def wait_until_timestamp(timestamp):
    while datetime.now().timestamp() <= timestamp.timestamp():
        await asyncio.sleep(0.5)


async def register_single_miner(subtensor, wallet, netuid, idx, block_id):
    try:
        print(f"{idx} Start track time: {time.time()}")

        print(
            f"{idx} Registering hotkey {wallet.hotkey.ss58_address} to netuid {netuid} ..."
        )
        print(f"{idx} Current block number: {block_id}")
        # block_hash = await subtensor.substrate.get_block_hash(block_id)
        # current_register_rao = await subtensor.get_hyperparameter(
        #     param_name="Burn", netuid=netuid, block_hash=block_hash
        # )
        # curret_register_cost = (
        #     Balance.from_rao(int(current_register_rao))
        #     if current_register_rao
        #     else Balance(0)
        # )

        # if curret_register_cost > REGISTER_COST_LIMIT:
        #     print(
        #         f"Register costs over the limit {curret_register_cost} > {REGISTER_COST_LIMIT}"
        #     )
        #     return

        call = await subtensor.substrate.compose_call(
            call_module="SubtensorModule",
            call_function="burned_register",
            call_params={
                "netuid": netuid,
                "hotkey": wallet.hotkey.ss58_address,
            },
        )

        force_batch_call = await subtensor.substrate.compose_call(
            call_module="Utility",
            call_function="force_batch",
            call_params={"calls": [call]},
        )

        signing_keypair = getattr(wallet, "coldkey")

        extrinsic_data = {
            "call": force_batch_call,
            "keypair": signing_keypair,
            "era": {"period": 2, "current": block_id - 1},
            "tip": 100_000,
            # "nonce": nonce,  # Uncomment if nonce is needed
        }

        print(f"{idx} Prepare1 track time: {time.time()}")
        extrinsic = await subtensor.substrate.create_signed_extrinsic(**extrinsic_data)
        print(f"{idx} Prepare2 track time: {time.time()}")

        while True:
            current_time = time.time() * 1000
            if ((current_time - 1751585076050) % 12000 < 100):
                print(f"{idx} Send track time: {current_time}")
                response = await subtensor.substrate.submit_extrinsic(
                    extrinsic,
                    wait_for_inclusion=False,
                    wait_for_finalization=False,
                )
                print(f"{idx} End track time: {time.time()}")
                break

    except Exception as e:
        print(
            f"{id} Error registering hotkey {wallet.hotkey.ss58_address} to netuid {netuid}: {e}"
        )


async def register_miner(wallets, network, netuid):
    subtensor = AsyncSubtensor(network="finney")
    metagraph = AsyncMetagraph(subtensor=subtensor, netuid=netuid, lite=False)
    await metagraph.sync()

    current_block_number = await subtensor.get_current_block()
    hyperparams = await subtensor.get_subnet_hyperparameters(netuid=netuid)
    metagraph_info = await subtensor.get_metagraph_info(netuid=netuid)
    registration_block = metagraph_info.network_registered_at
    print(f"Current block number: {current_block_number}")
    print(f"Registration block: {registration_block}")
    last_adjustment_block = await subtensor.substrate.query(
        "SubtensorModule", "LastAdjustmentBlock", [netuid]
    )
    next_registration_block = (
        last_adjustment_block.value + hyperparams.adjustment_interval
    )
    print(f"Last update block: {last_adjustment_block.value}")
    print(f"Next update block: {next_registration_block}")
    current_block_timestamp = await subtensor.get_timestamp(block=current_block_number)
    print(f"Time: {int(current_block_timestamp.timestamp() * 1000)} {int(time.time() * 1000)} {time.time() - current_block_timestamp.timestamp()}")
    next_update_timestamp = current_block_timestamp + timedelta(
        # seconds=12
        seconds=12
        * (
            last_adjustment_block.value
            + hyperparams.adjustment_interval
            - current_block_number
        )
    )
    print(f"Current timestamp: {datetime.now()}")
    print(f"Current block timestamp: {current_block_timestamp}")
    print(f"Next update timestamp: {next_update_timestamp}")

    async def on_new_block(block):
        block_number = block["header"]["number"]
        print(f"New block received: {block_number} {datetime.now()}", end="\r")
        if block_number >= next_registration_block - 2:
            idx = block_number - next_registration_block + 2
            await register_single_miner(
                subtensor=subtensor,
                wallet=wallets[idx],
                netuid=netuid,
                idx=idx,
                block_id=block_number,
            )
            if idx == 2:
                return True
        # await asyncio.sleep(10)
        # if (block_number >= next_registration_block - 1) and (
        #     block_number < next_registration_block + len(wallets)
        # ):
        #     print(f"New block {block_number} received, registering miners...")
        #     idx = block_number - next_registration_block + 1
        #     await register_single_miner(
        #         subtensor=subtensor,
        #         wallet=wallets[idx % len(wallets)],
        #         netuid=netuid,
        #         idx=idx,
        #         block_id=block_number
        #     )
        #     if idx == len(wallets):
        #         for wallet in wallets:
        #             wallet.coldkey_file.encrypt(WALLET_PWD)
        #         return True

    await subtensor.substrate.subscribe_block_headers(on_new_block)


def main():
    netuid = int(os.getenv("NETUID"))
    print(f"Registering miners for netuid: {netuid}")

    WALLET_PATH = "~/.bittensor/wallets"
    COLD_KEY = os.getenv("COLD_KEY")

    wallets = [
        Wallet(name="dr-main-1", hotkey="hot-1", path=WALLET_PATH),
        Wallet(name="dr-main-2", hotkey="hot-1", path=WALLET_PATH),
        Wallet(name="dr-main-3", hotkey="hot-1", path=WALLET_PATH)
    ]
    # for wallet in wallets:
    #     wallet.coldkey_file.decrypt(WALLET_PWD)  # password
    asyncio.run(register_miner(wallets, "finney", netuid))


if __name__ == "__main__":
    main()
