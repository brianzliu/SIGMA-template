#!/usr/bin/env python3
"""Run a suite of experiments and score the outputs in one invocation.

Subprocess-calls ``scripts/run_experiment.py`` then
``scripts/eval/batch_evaluate.py`` once per agent. Prints the exact
commands it runs so either half can be rerun standalone.

Typical use:

    uv run python scripts/run_and_eval.py \\
        --run experiment_run1 \\
        --agents claude_code_repo3_plugin \\
        --include ExampleEDPWellbore TutorialDeadOilEgg \\
        --workers 2

Per-agent output:

    data/eval/<agent>/<run>/<task>/        ← experiment workspaces (agent XML + logs)
    data/eval/results/<run>/<agent>/
        <task>_eval.json                   ← per-task score detail
        _summary.json                      ← aggregate across tasks
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_EXPERIMENT = REPO_ROOT / "scripts" / "run_experiment.py"
BATCH_EVALUATE = REPO_ROOT / "scripts" / "eval" / "batch_evaluate.py"
DATA_DIR = REPO_ROOT / "data"

DEFAULT_AGENTS = ["claude_code_repo3_plugin"]
DEFAULT_EXPERIMENTS_DIR = DATA_DIR / "eval" / "experiments"
DEFAULT_GROUND_TRUTH_DIR = DATA_DIR / "eval" / "experiments_gt"
DEFAULT_RESULTS_ROOT = DATA_DIR / "eval"
DEFAULT_EVAL_RESULTS_ROOT = DATA_DIR / "eval" / "results"


def _print_cmd(cmd: list[str]) -> None:
    print("  $", " ".join(str(c) for c in cmd), flush=True)


def _run(cmd: list[str]) -> int:
    _print_cmd(cmd)
    return subprocess.run(cmd).returncode


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--run", "-r", required=True, metavar="RUN_NAME",
                   help="Run name; results land at <agent_dir>/<run>/<task>/.")
    p.add_argument("--agents", "-a", nargs="+", default=DEFAULT_AGENTS,
                   help=f"Agents to run (default: {DEFAULT_AGENTS}).")
    p.add_argument("--include", nargs="+", metavar="TASK",
                   help="Only run these tasks.")
    p.add_argument("--exclude", nargs="+", metavar="TASK",
                   help="Skip these tasks.")
    p.add_argument("--experiments-dir", "-d", type=Path,
                   default=DEFAULT_EXPERIMENTS_DIR,
                   help=f"Task input dir (default: {DEFAULT_EXPERIMENTS_DIR}).")
    p.add_argument("--ground-truth-dir", "-g", type=Path,
                   default=DEFAULT_GROUND_TRUTH_DIR,
                   help=f"Ground-truth dir (default: {DEFAULT_GROUND_TRUTH_DIR}).")
    p.add_argument("--results-root-dir", type=Path, default=DEFAULT_RESULTS_ROOT,
                   help=f"Agent workspaces root (default: {DEFAULT_RESULTS_ROOT}).")
    p.add_argument("--eval-results-dir", type=Path, default=DEFAULT_EVAL_RESULTS_ROOT,
                   help=f"Per-agent <run>/<agent>/*.json score output "
                        f"(default: {DEFAULT_EVAL_RESULTS_ROOT}).")
    p.add_argument("--workers", "-w", type=int, default=1)
    p.add_argument("--timeout", "-t", type=int, default=1200)
    p.add_argument("--dry-run", action="store_true",
                   help="Print docker commands without executing (run phase only).")
    p.add_argument("--legacy", action="store_true",
                   help="Use lxml_xml_eval (weighted dimensions) instead of XMLTreeSim.")
    p.add_argument("--skip-run", action="store_true",
                   help="Skip the experiment run and only score existing outputs.")
    p.add_argument("--skip-eval", action="store_true",
                   help="Only run experiments; don't score afterwards.")
    p.add_argument("--continue-on-run-failure", action="store_true",
                   help="Still score outputs even if run_experiment returns nonzero.")
    args = p.parse_args()

    # ---------------- Run phase ----------------
    run_rc = 0
    if not args.skip_run:
        run_cmd = [
            sys.executable, str(RUN_EXPERIMENT),
            "--run", args.run,
            "--agents", *args.agents,
            "--experiments-dir", str(args.experiments_dir),
            "--ground-truth-dir", str(args.ground_truth_dir),
            "--results-root-dir", str(args.results_root_dir),
            "--workers", str(args.workers),
            "--timeout", str(args.timeout),
        ]
        if args.include:
            run_cmd += ["--include", *args.include]
        if args.exclude:
            run_cmd += ["--exclude", *args.exclude]
        if args.dry_run:
            run_cmd += ["--dry-run"]

        print(f"\n=== Running experiments ({len(args.agents)} agent(s)) ===")
        run_rc = _run(run_cmd)
        if run_rc != 0 and not args.continue_on_run_failure:
            print(f"\nrun_experiment.py exited with code {run_rc}; skipping evaluation.",
                  file=sys.stderr)
            print("Re-run with --continue-on-run-failure to score partial outputs, "
                  "or --skip-run + --run <name> to rescore later.", file=sys.stderr)
            return run_rc

    if args.skip_eval or args.dry_run:
        return run_rc

    # ---------------- Eval phase ----------------
    print(f"\n=== Scoring outputs ({len(args.agents)} agent(s)) ===")
    eval_rcs: list[int] = []
    for agent in args.agents:
        agent_run_dir = args.results_root_dir / agent / args.run
        if not agent_run_dir.exists():
            print(f"  [skip] {agent}: {agent_run_dir} does not exist", file=sys.stderr)
            eval_rcs.append(1)
            continue

        out_dir = args.eval_results_dir / args.run / agent
        eval_cmd = [
            sys.executable, str(BATCH_EVALUATE),
            "--experiments-dir", str(agent_run_dir),
            "--ground-truth-dir", str(args.ground_truth_dir),
            "--results-dir", str(out_dir),
            "--output", str(out_dir / "_summary.json"),
        ]
        if args.include:
            eval_cmd += ["--experiments", *args.include]
        if args.legacy:
            eval_cmd += ["--legacy"]

        print(f"\n--- {agent} ---")
        eval_rcs.append(_run(eval_cmd))

    worst = max([run_rc, *eval_rcs]) if eval_rcs else run_rc
    return worst


if __name__ == "__main__":
    sys.exit(main())
