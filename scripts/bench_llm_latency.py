"""
Deterministic latency benchmark for the NVIDIA-hosted Nemotron endpoint.

Faithful to the voice pipeline: same AsyncOpenAI streaming client, same model,
same base_url. Measures TTFT (time-to-first-token) — the number that matters for
voice — over many requests, and prints the full distribution + percentiles.

Run:  .venv/bin/python scripts/bench_llm_latency.py [N] [CONCURRENCY]
"""

import asyncio
import datetime
import os
import sys
import time

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

MODEL = os.getenv("NVIDIA_LLM_MODEL", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")
BASE_URL = "https://integrate.api.nvidia.com/v1"
# Set REASONING_OFF=1 to disable thinking (faithful to how the voice bot calls the model).
REASONING_OFF = os.getenv("REASONING_OFF", "0") not in ("0", "", "false", "False")
EXTRA_BODY = {"chat_template_kwargs": {"enable_thinking": False}} if REASONING_OFF else None
PER_REQUEST_TIMEOUT = 60.0  # generous, so we CAPTURE a real stall instead of hiding it
# Results are appended here (one timestamped session block per run). Override with LOG_PATH.
LOG_PATH = os.getenv("LOG_PATH", os.path.join(os.path.dirname(__file__), "..", "logs", "llm_latency_bench.log"))


async def one_request(client: AsyncOpenAI, idx: int) -> dict:
    """Fire one streaming completion; return TTFT and total latency in seconds."""
    t0 = time.perf_counter()
    ttft = None
    tokens = 0
    err = None
    try:
        stream = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "Say hello in one short sentence."}],
            max_tokens=30,
            stream=True,
            extra_body=EXTRA_BODY,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                if ttft is None:
                    ttft = time.perf_counter() - t0
                tokens += 1
    except Exception as e:  # noqa: BLE001 — we want to record any failure mode
        err = f"{type(e).__name__}: {e}"
    total = time.perf_counter() - t0
    return {"idx": idx, "ttft": ttft, "total": total, "tokens": tokens, "err": err}


def pct(values: list[float], p: float) -> float:
    """Simple percentile (nearest-rank) on a sorted copy."""
    if not values:
        return float("nan")
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return s[k]


async def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    key = os.getenv("NVIDIA_API_KEY")
    if not key:
        raise SystemExit("NVIDIA_API_KEY missing from .env")

    client = AsyncOpenAI(api_key=key, base_url=BASE_URL, timeout=PER_REQUEST_TIMEOUT)

    # Open the log file (append) and tee every line to it AND the console.
    os.makedirs(os.path.dirname(os.path.abspath(LOG_PATH)), exist_ok=True)
    logfile = open(LOG_PATH, "a")

    def log(line: str = "") -> None:
        print(line)
        logfile.write(line + "\n")
        logfile.flush()

    log("=" * 64)
    log(f"SESSION {datetime.datetime.now().isoformat(timespec='seconds')}")
    log(f"Endpoint: {BASE_URL}")
    log(f"Model:    {MODEL}")
    log(f"Requests: {n} | concurrency: {concurrency} | per-request timeout: {PER_REQUEST_TIMEOUT}s")
    log("-" * 64)

    results: list[dict] = []
    if concurrency <= 1:
        # Sequential — cleanest signal, one request at a time.
        for i in range(n):
            r = await one_request(client, i)
            results.append(r)
            ttft = f"{r['ttft']:.3f}s" if r["ttft"] is not None else "—"
            line = f"req {i + 1:>3}: TTFT {ttft:>8} | total {r['total']:.3f}s | tok {r['tokens']}"
            if r["err"]:
                line += f" | ERROR {r['err']}"
            log(line)
    else:
        # Fire in waves of `concurrency` to also probe behavior under load.
        for start in range(0, n, concurrency):
            batch = [one_request(client, start + j) for j in range(min(concurrency, n - start))]
            batch_results = await asyncio.gather(*batch)
            for r in sorted(batch_results, key=lambda x: x["idx"]):
                results.append(r)
                ttft = f"{r['ttft']:.3f}s" if r["ttft"] is not None else "—"
                line = f"req {r['idx'] + 1:>3}: TTFT {ttft:>8} | total {r['total']:.3f}s | tok {r['tokens']}"
                if r["err"]:
                    line += f" | ERROR {r['err']}"
                log(line)

    ttfts = [r["ttft"] for r in results if r["ttft"] is not None]
    errors = [r for r in results if r["err"]]
    log("-" * 64)
    if ttfts:
        log(f"TTFT  count={len(ttfts)}  min={min(ttfts):.3f}s  mean={sum(ttfts)/len(ttfts):.3f}s  "
            f"median={pct(ttfts,50):.3f}s  p90={pct(ttfts,90):.3f}s  p95={pct(ttfts,95):.3f}s  "
            f"p99={pct(ttfts,99):.3f}s  max={max(ttfts):.3f}s")
        for thresh in (0.5, 1.0, 4.0, 8.0):
            over = sum(1 for t in ttfts if t > thresh)
            log(f"  TTFT > {thresh:>4}s : {over:>3}/{len(ttfts)}  ({100*over/len(ttfts):.1f}%)")
    log(f"errors/timeouts: {len(errors)}/{len(results)}")
    for r in errors:
        log(f"  req {r['idx']+1}: {r['err']} (after {r['total']:.1f}s)")
    log(f"(results appended to {os.path.abspath(LOG_PATH)})")
    logfile.close()


if __name__ == "__main__":
    asyncio.run(main())
