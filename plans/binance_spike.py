"""
Binance Leaderboard API Spike
Проверяем: сколько публичных трейдеров, насколько детальны позиции.
"""
import json
import time
import httpx

BASE = "https://www.binance.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "clienttype": "web",
}


def get_leaderboard(trade_type: str = "PERPETUAL", stat_type: str = "ROI") -> list[dict]:
    url = f"{BASE}/bapi/futures/v3/public/future/leaderboard/getLeaderboard"
    body = {
        "tradeType": trade_type,
        "statisticsType": stat_type,
        "periodType": "WEEKLY",
        "isShared": True,
        "isTrader": False,
    }
    r = httpx.post(url, json=body, headers=HEADERS, timeout=10)
    r.raise_for_status()
    data = r.json()
    return data.get("data", [])


def get_trader_positions(encrypted_uid: str, trade_type: str = "PERPETUAL") -> list[dict]:
    url = f"{BASE}/bapi/futures/v2/public/future/leaderboard/getOtherPosition"
    body = {"encryptedUid": encrypted_uid, "tradeType": trade_type}
    r = httpx.post(url, json=body, headers=HEADERS, timeout=10)
    r.raise_for_status()
    data = r.json()
    return data.get("data", {}).get("otherPositionRetList", [])


def get_trader_performance(encrypted_uid: str, trade_type: str = "PERPETUAL") -> dict:
    url = f"{BASE}/bapi/futures/v1/public/future/leaderboard/getOtherPerformance"
    body = {"encryptedUid": encrypted_uid, "tradeType": trade_type}
    r = httpx.post(url, json=body, headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json().get("data", {})


def main() -> None:
    print("=" * 60)
    print("BINANCE LEADERBOARD SPIKE")
    print("=" * 60)

    # 1. Получить топ-100 трейдеров
    print("\n[1] Fetching leaderboard (PERPETUAL, ROI, WEEKLY)...")
    traders = get_leaderboard()
    print(f"    Total traders returned: {len(traders)}")
    if traders:
        sample = traders[0]
        print(f"    Sample trader keys: {list(sample.keys())}")
        print(f"    Sample: {json.dumps(sample, indent=2)[:300]}")

    # 2. Проверить позиции первых 20 трейдеров
    print("\n[2] Checking positions for first 20 traders...")
    with_positions = 0
    total_positions = 0
    private_count = 0
    position_sample = None

    for i, trader in enumerate(traders[:20]):
        uid = trader.get("encryptedUid", "")
        if not uid:
            continue
        try:
            positions = get_trader_positions(uid)
            if positions:
                with_positions += 1
                total_positions += len(positions)
                if position_sample is None:
                    position_sample = positions[0]
                print(f"    [{i+1:02d}] {trader.get('nickName', 'N/A'):20s} → {len(positions)} positions")
            else:
                private_count += 1
                print(f"    [{i+1:02d}] {trader.get('nickName', 'N/A'):20s} → private/empty")
            time.sleep(0.5)  # be gentle
        except Exception as e:
            print(f"    [{i+1:02d}] Error: {e}")
            time.sleep(1)

    print(f"\n    Summary (out of 20 checked):")
    print(f"    - With public positions: {with_positions}")
    print(f"    - Private/empty:         {private_count}")
    print(f"    - Total positions seen:  {total_positions}")
    print(f"    - Public rate:           {with_positions/20*100:.0f}%")

    # 3. Показать структуру позиции
    if position_sample:
        print(f"\n[3] Position data structure:")
        print(json.dumps(position_sample, indent=2))

    # 4. Проверить performance для первого публичного трейдера
    for trader in traders[:10]:
        uid = trader.get("encryptedUid", "")
        if not uid:
            continue
        try:
            perf = get_trader_performance(uid)
            if perf:
                print(f"\n[4] Performance data structure:")
                print(json.dumps(perf, indent=2)[:500])
                break
            time.sleep(0.3)
        except Exception:
            pass

    print("\n" + "=" * 60)
    print("CONCLUSIONS:")
    print(f"- Public position rate: ~{with_positions/max(1, with_positions+private_count)*100:.0f}%")
    print(f"- Avg positions per trader: {total_positions/max(1, with_positions):.1f}")
    print("- Check position fields above for signal detection viability")
    print("=" * 60)


if __name__ == "__main__":
    main()
