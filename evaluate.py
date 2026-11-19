"""Simple reproducible action/tool benchmark, not an end-to-end success claim."""
import argparse
import json
from collections import Counter
from pathlib import Path
from agent_core import Agent, OfflineRouter, Session, jev_router_from_env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["offline", "jev"], default="offline")
    ap.add_argument("--dataset", default="data/smoke_benchmark.jsonl")
    args = ap.parse_args()
    router = OfflineRouter() if args.mode == "offline" else jev_router_from_env()
    agent = Agent(router)
    rows = [json.loads(x) for x in Path(args.dataset).read_text(encoding="utf-8").splitlines() if x.strip()]
    correct = 0
    tcorrect = 0
    tt = 0
    failures = []
    confusion = Counter()
    for row in rows:
        # Every record is a fresh session for single-turn benchmark.
        out = agent.run(row["query"], Session())
        pred = out["route"]["action"] if out["route"] else "system_error"
        # A pre-execution slot validator may convert tool_call to request_info.
        effective = out["status"] if out["status"] in ("request_info", "cannot_answer", "review_needed", "system_error") else pred
        gold = row["gold_action"]
        confusion[(gold, effective)] += 1
        correct += effective == gold
        if gold == "tool_call":
            tt += 1
            tcorrect += effective == gold and out["route"].get("tool") == row.get("gold_tool")
        if effective != gold:
            failures.append((row["query"], gold, effective))
    print(f"samples={len(rows)} action_accuracy={correct/len(rows):.3f} tool_correct={tcorrect}/{tt}")
    print("confusion:")
    for (gold, pred), count in sorted(confusion.items()):
        print(f"  {gold:14s} -> {pred:14s}: {count}")
    if failures:
        print("errors:")
        for item in failures:
            print(" ", item)
    print("注意：这是单轮动作一致率，不是端到端 Task Success Rate。")


if __name__ == "__main__":
    main()
