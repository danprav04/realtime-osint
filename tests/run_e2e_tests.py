"""
tests/run_e2e_tests.py - E2E Test Suite Runner & Statistics Aggregator

Executes Tiers 1-4 of the Real-time OSINT Gathering Service E2E test suite,
computes per-tier metrics, validates coverage gates, and returns exit code 0 on 100% pass.
"""

import sys
import os
import time
import subprocess
from datetime import datetime, timezone


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


TIERS = [
    {
        "id": "Tier 1",
        "name": "Feature Coverage (5 Plugins, AI, Themes, WS, TG, Auth, Health)",
        "file": "tests/e2e/test_tier1_features.py",
        "min_required": 50,
    },
    {
        "id": "Tier 2",
        "name": "Boundary Values & Edge Cases (Empty RSS, Rate Limits, Farsi/Arabic)",
        "file": "tests/e2e/test_tier2_boundaries.py",
        "min_required": 15,
    },
    {
        "id": "Tier 3",
        "name": "Cross-Feature Interactions (Hot-Reload, Multi-Provider, Throttle)",
        "file": "tests/e2e/test_tier3_cross_feature.py",
        "min_required": 10,
    },
    {
        "id": "Tier 4",
        "name": "Realistic Workloads (Live Stream, News Storm, Sub-60s Alert)",
        "file": "tests/e2e/test_tier4_scenarios.py",
        "min_required": 8,
    },
]


def run_tier(tier_info: dict) -> dict:
    file_path = os.path.join(PROJECT_ROOT, tier_info["file"])
    cmd = [sys.executable, "-m", "pytest", file_path, "-q"]
    start_time = time.time()
    proc = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    duration = time.time() - start_time

    output = proc.stdout + proc.stderr
    exit_code = proc.returncode

    # Parse pytest output summary (e.g. "58 passed in 0.32s")
    passed = 0
    failed = 0
    errors = 0

    for line in output.splitlines():
        line_clean = line.strip()
        if "passed" in line_clean or "failed" in line_clean or "error" in line_clean:
            parts = line_clean.split(",")
            for part in parts:
                p = part.strip()
                if "passed" in p:
                    try:
                        passed = int(p.split()[0])
                    except (ValueError, IndexError):
                        pass
                elif "failed" in p:
                    try:
                        failed = int(p.split()[0])
                    except (ValueError, IndexError):
                        pass
                elif "error" in p:
                    try:
                        errors = int(p.split()[0])
                    except (ValueError, IndexError):
                        pass

    total = passed + failed + errors
    success = (exit_code == 0 and failed == 0 and errors == 0 and total >= tier_info["min_required"])

    return {
        "id": tier_info["id"],
        "name": tier_info["name"],
        "file": tier_info["file"],
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "duration": duration,
        "exit_code": exit_code,
        "success": success,
        "min_required": tier_info["min_required"],
        "raw_output": output
    }


def main():
    print("=" * 80)
    print("  REAL-TIME OSINT GATHERING SERVICE - E2E TEST SUITE RUNNER")
    print(f"  Execution Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Platform: {sys.platform} | Python: {sys.version.split()[0]}")
    print("=" * 80)
    print()

    results = []
    overall_start = time.time()

    for t in TIERS:
        print(f"[*] Executing {t['id']}: {t['name']}...")
        res = run_tier(t)
        results.append(res)
        status_label = "[PASSED]" if res["success"] else "[FAILED]"
        print(f"    -> {status_label} {res['passed']}/{res['total']} passed in {res['duration']:.2f}s")

    total_duration = time.time() - overall_start

    print()
    print("=" * 80)
    print("                         E2E TEST EXECUTION SUMMARY")
    print("=" * 80)
    print(f"{'Tier':<8} | {'Category / Description':<40} | {'Tests':<6} | {'Pass':<5} | {'Fail':<5} | {'Time':<6} | {'Status'}")
    print("-" * 80)

    total_tests = sum(r["total"] for r in results)
    total_passed = sum(r["passed"] for r in results)
    total_failed = sum(r["failed"] + r["errors"] for r in results)
    all_success = all(r["success"] for r in results)

    for r in results:
        status_text = "PASS" if r["success"] else "FAIL"
        short_name = r["name"][:38] + ".." if len(r["name"]) > 40 else r["name"]
        print(f"{r['id']:<8} | {short_name:<40} | {r['total']:<6} | {r['passed']:<5} | {r['failed']:<5} | {r['duration']:>5.2f}s | {status_text}")

    print("-" * 80)
    pass_rate = (total_passed / total_tests * 100) if total_tests > 0 else 0.0
    overall_status = "ALL TIERS PASSED (100%)" if all_success else "FAILURES DETECTED"
    print(f"{'TOTAL':<8} | {'All 4 Tiers Verified':<40} | {total_tests:<6} | {total_passed:<5} | {total_failed:<5} | {total_duration:>5.2f}s | {pass_rate:.1f}%")
    print("=" * 80)
    print(f"  Result: {overall_status}")
    print(f"  Quality Gate: {'SATISFIED (Exit 0)' if all_success else 'VIOLATED (Exit 1)'}")
    print("=" * 80)

    if not all_success:
        print("\n[!] Failure Details:")
        for r in results:
            if not r["success"]:
                print(f"\n--- {r['id']} Output ---")
                print(r["raw_output"])
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
