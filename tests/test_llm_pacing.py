"""Client-side pacing keeps bursts under the gateway's per-minute limit."""
import asyncio

import t2s.llm as llm_mod
from t2s.llm import GatewayLLM


def test_pacing_spaces_requests(monkeypatch):
    clock = {"t": 0.0}
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)
        clock["t"] += s

    monkeypatch.setattr(llm_mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(llm_mod.asyncio, "sleep", fake_sleep)

    g = GatewayLLM.__new__(GatewayLLM)  # skip httpx client creation
    g.rpm, g._sent, g._pace_lock = 3, [], asyncio.Lock()

    async def burst():
        times = []
        for _ in range(7):
            await g._pace()
            times.append(clock["t"])
        return times

    times = asyncio.run(burst())
    assert times[:3] == [0.0, 0.0, 0.0]           # first 3 go straight through
    assert 60 <= times[3] < 61                    # 4th waits for the window to slide
    for i in range(len(times)):                   # never more than 3 in any 60 s window
        assert sum(1 for t in times if times[i] <= t < times[i] + 60) <= 3


def test_pacing_off_by_default():
    g = GatewayLLM.__new__(GatewayLLM)
    g.rpm, g._sent, g._pace_lock = 0, [], asyncio.Lock()
    asyncio.run(g._pace())
    assert g._sent == []
