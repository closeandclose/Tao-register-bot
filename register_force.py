import asyncio
import math
import time
import traceback
from datetime import timedelta, datetime
from bittensor import Balance
from bittensor_wallet import Wallet
from bittensor.core.async_subtensor import AsyncSubtensor
from bittensor.core.metagraph import AsyncMetagraph
from bittensor.core.config import Config
from dotenv import load_dotenv
import os
from pathlib import Path

load_dotenv()

REGISTER_COST_LIMIT = Balance(float(os.getenv("REGISTER_COST_LIMIT", "1.0")))
WALLET_PWD = os.getenv("WALLET_PASSWORD")
MAX_SLOTS = int(os.getenv("MAX_SLOTS", "6"))  # Subnet 1에서 한 epoch당 등록 가능한 slot 개수
REGISTRATION_TIP = int(os.getenv("REGISTRATION_TIP", "1000000"))  # 등록 시 tip (rao 단위)
ERA_PERIOD = int(os.getenv("ERA_PERIOD", "5"))  # Extrinsic 유효 기간
START_OFFSET = int(os.getenv("START_OFFSET", "1"))  # Epoch 몇 블록 전부터 시작할지 (기본: 2)

def discover_hotkeys(wallet_path, coldkey_name):
    """
    지정된 coldkey에 연결된 모든 hotkey를 자동으로 탐색합니다.
    공개키 파일(.pub, .pub.txt 등)은 제외하고 실제 개인키 파일만 탐색합니다.
    
    Args:
        wallet_path: 지갑 디렉토리 경로
        coldkey_name: coldkey 이름
    
    Returns:
        List[Wallet]: 발견된 모든 지갑 리스트
    """
    expanded_path = Path(os.path.expanduser(wallet_path))
    coldkey_path = expanded_path / coldkey_name / "hotkeys"
    
    if not coldkey_path.exists():
        print(f"Warning: Hotkeys directory not found: {coldkey_path}")
        return []
    
    wallets = []
    seen_addresses = set()  # 중복 방지
    
    for hotkey_file in coldkey_path.iterdir():
        if hotkey_file.is_file():
            hotkey_name = hotkey_file.name
            
            # 공개키 파일 제외 (.pub, .pub.txt, .txt 등)
            if hotkey_name.endswith('.pub') or hotkey_name.endswith('.pub.txt') or hotkey_name.endswith('.txt'):
                print(f"Skipping public key file: {hotkey_name}")
                continue
            
            # 숨김 파일이나 시스템 파일 제외
            if hotkey_name.startswith('.'):
                continue
            
            try:
                wallet = Wallet(name=coldkey_name, hotkey=hotkey_name, path=str(expanded_path))
                hotkey_address = wallet.hotkey.ss58_address
                
                # 중복된 주소 확인 (같은 hotkey를 다른 이름으로 가진 경우)
                if hotkey_address in seen_addresses:
                    print(f"Skipping duplicate hotkey: {hotkey_name} ({hotkey_address})")
                    continue
                
                seen_addresses.add(hotkey_address)
                wallets.append(wallet)
                print(f"✓ Discovered hotkey: {hotkey_name} ({hotkey_address[:10]}...)")
            except Exception as e:
                print(f"✗ Failed to load hotkey {hotkey_name}: {e}")
                continue
    
    print(f"\nTotal valid hotkeys discovered: {len(wallets)}")
    return wallets


async def get_unregistered_hotkeys(subtensor, wallets, netuid):
    """
    미등록된 hotkey들을 찾아 반환합니다.
    
    Args:
        subtensor: AsyncSubtensor 인스턴스
        wallets: 확인할 지갑 리스트
        netuid: 서브넷 ID
    
    Returns:
        List[Wallet]: 미등록된 지갑 리스트
    """
    print(f"\nChecking registration status for {len(wallets)} hotkeys...")
    metagraph = AsyncMetagraph(subtensor=subtensor, netuid=netuid, lite=False)
    await metagraph.sync()
    
    unregistered = []
    registered = []
    
    for wallet in wallets:
        hotkey_ss58 = wallet.hotkey.ss58_address
        if hotkey_ss58 in metagraph.hotkeys:
            registered.append(wallet.hotkey_str)
            print(f"✓ Already registered: {wallet.hotkey_str} ({hotkey_ss58})")
        else:
            unregistered.append(wallet)
            print(f"✗ Not registered: {wallet.hotkey_str} ({hotkey_ss58})")
    
    print(f"\nSummary: {len(registered)} registered, {len(unregistered)} unregistered")
    return unregistered


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


async def prepare_and_submit_extrinsic(subtensor, wallet, netuid, block_id, idx):
    """
    Extrinsic을 준비하고 즉시 제출합니다.
    최적화: 준비 시간을 최소화하여 빠르게 제출
    """
    try:
        start_time = time.time()
        
        # Call 생성
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

        # Coldkey 접근
        signing_keypair = wallet.coldkey
        if signing_keypair is None:
            raise ValueError(f"Coldkey not loaded for wallet {wallet.hotkey_str}")

        # Extrinsic 생성
        extrinsic_data = {
            "call": force_batch_call,
            "keypair": signing_keypair,
            "era": {"period": ERA_PERIOD, "current": block_id - 1},
            "tip": REGISTRATION_TIP,
        }

        extrinsic = await subtensor.substrate.create_signed_extrinsic(**extrinsic_data)
        prep_time = (time.time() - start_time) * 1000
        print(f"{idx} ⚡ Prepared in {prep_time:.1f}ms")
        
        # 즉시 제출
        response = await subtensor.substrate.submit_extrinsic(
            extrinsic,
            wait_for_inclusion=False,
            wait_for_finalization=False,
        )
        
        total_time = (time.time() - start_time) * 1000
        print(f"{idx} ✓ Submitted in {total_time:.1f}ms total: {response}")
        return response
        
    except Exception as e:
        elapsed = (time.time() - start_time) * 1000
        print(f"{idx} ✗ Failed after {elapsed:.1f}ms: {e}")
        traceback.print_exc()
        return None




async def register_miner_epoch(subtensor, wallets_to_register, netuid, next_registration_block):
    """
    단일 epoch에서 지정된 지갑들을 등록합니다.
    개선: 블록 도착 즉시 준비+제출하여 지연 시간 최소화
    
    Args:
        subtensor: AsyncSubtensor 인스턴스
        wallets_to_register: 등록할 지갑 리스트 (최대 MAX_SLOTS개)
        netuid: 서브넷 ID
        next_registration_block: 다음 등록 블록 번호
    """
    registration_complete = asyncio.Event()
    wallets_count = len(wallets_to_register)
    registered_count = 0
    
    start_block = next_registration_block - START_OFFSET
    # 실제로 등록할 수 있는 최대 개수
    actual_registration_count = min(wallets_count, MAX_SLOTS)
    # 마지막 블록 계산
    end_block = start_block + actual_registration_count - 1
    total_blocks = actual_registration_count
    
    print(f"\n{'='*60}")
    print(f"Starting registration for {actual_registration_count} hotkeys")
    print(f"Epoch block: {next_registration_block}")
    print(f"Registration window: {start_block} to {end_block} ({total_blocks} blocks)")
    print(f"Tip: {REGISTRATION_TIP:,} rao ({REGISTRATION_TIP/1e9:.6f} TAO)")
    print(f"{'='*60}\n")
    
    async def on_new_block(block):
        nonlocal registered_count
        block_number = block["header"]["number"]
        print(f"New block received: {block_number} {datetime.now()}", end="\r")
        
        # Epoch START_OFFSET블록 전부터 시작 (더 집중된 전략)
        # 로그 분석 결과: 마지막 2-3개 블록이 성공률이 높음
        start_offset = START_OFFSET
        
        if block_number >= next_registration_block - start_offset:
            idx = block_number - next_registration_block + start_offset
            
            if idx < wallets_count and idx < MAX_SLOTS:
                wallet = wallets_to_register[idx]
                
                # Epoch까지의 거리 표시
                distance = next_registration_block - block_number
                position = f"epoch-{distance}" if distance > 0 else f"epoch" if distance == 0 else f"epoch+{abs(distance)}"
                
                print(f"\n[Block {block_number}] ({position}) 🚀 REGISTERING #{idx}: {wallet.hotkey_str}")
                
                # 블록 도착 즉시 준비+제출
                await prepare_and_submit_extrinsic(
                    subtensor=subtensor,
                    wallet=wallet,
                    netuid=netuid,
                    block_id=block_number,
                    idx=idx
                )
                registered_count += 1
        
        # 모든 slot 처리 완료 확인
        # 마지막 블록 = 시작 블록 + MAX_SLOTS - 1
        last_registration_block = next_registration_block - start_offset + MAX_SLOTS - 1
        
        # 모든 등록 완료 조건:
        # 1. 마지막 블록을 넘어섬
        # 2. 또는 모든 지갑 등록 완료
        if block_number > last_registration_block or registered_count >= min(wallets_count, MAX_SLOTS):
            print(f"\n{'='*60}")
            print(f"Registration epoch completed: {registered_count}/{wallets_count} hotkeys attempted")
            print(f"Last block processed: {block_number}, Target was: {last_registration_block}")
            print(f"{'='*60}\n")
            registration_complete.set()
            return True
    
    await subtensor.substrate.subscribe_block_headers(on_new_block)
    await registration_complete.wait()


async def register_miner(all_wallets, network, netuid):
    """
    메인 등록 루프: 무한 반복하며 매 epoch마다 미등록 hotkey를 자동으로 등록합니다.
    """
    subtensor = AsyncSubtensor(network=network)
    
    while True:  # 무한 루프
        try:
            print(f"\n{'#'*60}")
            print(f"# NEW REGISTRATION CYCLE - {datetime.now()}")
            print(f"{'#'*60}\n")
            
            # 1. 현재 블록 및 epoch 정보 조회
            current_block_number = await subtensor.get_current_block()
            hyperparams = await subtensor.get_subnet_hyperparameters(netuid=netuid)
            last_adjustment_block = await subtensor.substrate.query(
                "SubtensorModule", "LastAdjustmentBlock", [netuid]
            )
            next_registration_block = (
                last_adjustment_block.value + hyperparams.adjustment_interval
            )
            
            blocks_until_next_epoch = next_registration_block - current_block_number
            time_until_next_epoch = blocks_until_next_epoch * 12  # 12초 per block
            
            print(f"Current block: {current_block_number}")
            print(f"Last adjustment block: {last_adjustment_block.value}")
            print(f"Next registration block: {next_registration_block}")
            print(f"Blocks until next epoch: {blocks_until_next_epoch}")
            print(f"Time until next epoch: ~{time_until_next_epoch}s ({time_until_next_epoch/60:.1f} min)")
            
            # 2. 미등록 hotkey 찾기
            unregistered_wallets = await get_unregistered_hotkeys(subtensor, all_wallets, netuid)
            
            if not unregistered_wallets:
                print("\n✓ All hotkeys are already registered!")
                print(f"Waiting until next epoch to check again...")
                # 다음 epoch까지 대기
                await asyncio.sleep(time_until_next_epoch + 30)  # 30초 버퍼
                continue
            
            # 3. 등록할 지갑 선별 (최대 MAX_SLOTS개)
            wallets_to_register = unregistered_wallets[:MAX_SLOTS]
            remaining = len(unregistered_wallets) - len(wallets_to_register)
            
            print(f"\n→ Will register {len(wallets_to_register)} hotkeys in next epoch")
            if remaining > 0:
                print(f"→ {remaining} hotkeys will be registered in future epochs")
            
            for i, wallet in enumerate(wallets_to_register):
                print(f"  [{i}] {wallet.hotkey_str} - {wallet.hotkey.ss58_address}")
            
            # 4. 다음 epoch까지 대기 (여유를 두고 조금 일찍 준비)
            if blocks_until_next_epoch > MAX_SLOTS + 5:
                wait_time = time_until_next_epoch - (MAX_SLOTS + 5) * 12
                print(f"\nWaiting {wait_time}s until registration window...")
                await asyncio.sleep(wait_time)
            
            # 5. 등록 실행
            await register_miner_epoch(
                subtensor=subtensor,
                wallets_to_register=wallets_to_register,
                netuid=netuid,
                next_registration_block=next_registration_block
            )
            
            # 6. 다음 사이클까지 대기
            print(f"\nWaiting before next cycle...")
            await asyncio.sleep(60)  # 1분 대기 후 다시 확인
            
        except Exception as e:
            print(f"\n❌ Error in registration cycle: {e}")
            print("Retrying in 60 seconds...")
            await asyncio.sleep(60)


def main():
    """
    메인 실행 함수: .env에서 설정을 읽고 자동화된 등록 프로세스를 시작합니다.
    """
    # .env에서 설정 읽기
    netuid = int(os.getenv("NETUID", "1"))  # 기본값 1
    wallet_path = os.getenv("WALLET_PATH", "~/.bittensor/wallets")
    coldkey_name = os.getenv("COLD_KEY")
    network = os.getenv("NETWORK", "finney")
    
    if not coldkey_name:
        raise ValueError("COLD_KEY must be set in .env file")
    
    print(f"\n{'='*60}")
    print(f"Bittensor Auto-Registration Bot (Competitive Mode)")
    print(f"{'='*60}")
    print(f"Network: {network}")
    print(f"Netuid: {netuid}")
    print(f"Coldkey: {coldkey_name}")
    print(f"Wallet path: {wallet_path}")
    print(f"\n--- Competition Settings ---")
    print(f"Max slots per epoch: {MAX_SLOTS}")
    print(f"Registration tip: {REGISTRATION_TIP:,} rao ({REGISTRATION_TIP/1e9:.6f} TAO)")
    print(f"Era period: {ERA_PERIOD} blocks")
    print(f"Strategy: PRE-PREPARED EXTRINSICS (Fast Submit)")
    print(f"{'='*60}\n")
    
    # Coldkey에서 모든 hotkey 자동 탐색
    all_wallets = discover_hotkeys(wallet_path, coldkey_name)
    
    if not all_wallets:
        print(f"❌ No hotkeys found for coldkey '{coldkey_name}'")
        print(f"Please check your wallet path: {wallet_path}/{coldkey_name}/hotkeys/")
        return
    
    # 지갑 복호화 (필요한 경우)
    # if WALLET_PWD:
    #     for wallet in all_wallets:
    #         wallet.coldkey_file.decrypt(WALLET_PWD)
    
    # 자동 등록 시작
    print(f"\n🚀 Starting automated registration process...")
    print(f"This bot will run continuously and register unregistered hotkeys every epoch.\n")
    
    try:
        asyncio.run(register_miner(all_wallets, network, netuid))
    except KeyboardInterrupt:
        print("\n\n⏹️  Bot stopped by user")
    except Exception as e:
        print(f"\n\n❌ Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
