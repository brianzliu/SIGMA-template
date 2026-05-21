#!/usr/bin/env python3
"""Harness-less eval: prompt a base LLM directly for GEOS XML, then score.

Instead of running the full Claude Code agent harness, this gives the base
model the task instruction (adapted to emit XML inline instead of writing
files) optionally with a 1-shot ICL example, parses the XML out of the
response, writes it to the same layout that `batch_evaluate.py` consumes,
then invokes the scorer.

Example:

    python scripts/harnessless_eval.py \\
        --run-name harnessless_minimax_1shot_s1 \\
        --model minimax/minimax-m2.7 \\
        --icl-task ExampleProppantTest \\
        --workers 8 \\
        --score

Outputs:
    data/eval/harnessless/<run_name>/
      <task>/
        inputs/<filename>.xml      (scorer-compatible)
        raw_response.txt           (full model output, for debugging)
        metadata.json              (model, prompt tokens, parse status)
      _summary.json                (aggregate timing/cost)

Scoring runs batch_evaluate.py against
    /data/shared/geophysics_agent_data/data/eval/experiments_gt
and writes per-task <name>_eval.json + aggregate JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_MD_PATH = REPO_ROOT / "run" / "AGENTS_old.md"

DEFAULT_SPECS_DIR = Path(
    "/data/shared/geophysics_agent_data/data/eval/experiments_from_mined_specs"
)
DEFAULT_GT_DIR = Path(
    "/data/shared/geophysics_agent_data/data/eval/experiments_gt"
)
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "eval" / "harnessless"

# The 17-task test subset used in PAC-1 (v2 specs on minimax).
# Matches data/eval/claude_code_no_plugin/noplug_mm_v2/ contents.
TEST_TASKS_17 = [
    "AdvancedExampleCasedContactThermoElasticWellbore",
    "AdvancedExampleDeviatedElasticWellbore",
    "AdvancedExampleDruckerPrager",
    "AdvancedExampleExtendedDruckerPrager",
    "AdvancedExampleModifiedCamClay",
    "AdvancedExampleViscoDruckerPrager",
    "buckleyLeverettProblem",
    "ExampleDPWellbore",
    "ExampleEDPWellbore",
    "ExampleIsothermalLeakyWell",
    "ExampleMandel",
    "ExampleThermalLeakyWell",
    "ExampleThermoporoelasticConsolidation",
    "kgdExperimentValidation",
    "pknViscosityDominated",
    "TutorialPoroelasticity",
    "TutorialSneddon",
]

# Held-out from the 46-task pool for ICL use (not in the 36-task test pool).
ICL_HOLDOUT_TASKS = [
    "AdvancedExampleCasedThermoElasticWellbore",
    "AdvancedExamplePureThermalDiffusionWellbore",
    "AdvancedExampleThermoPoroElasticWellbore",
    "AdvancedExampleViscoExtendedDruckerPrager",
    "ExampleIsothermalHystInjection",
    "ExampleMCCWellbore",
    "ExampleProppantTest",
    "ExamplesingleFracCompression",
    "ExampleVerticalPoroElastoPlasticWellbore",
    "TutorialHydraulicFractureWithAdvancedXML",
]

# -----------------------------------------------------------------------
# Prompt construction
# -----------------------------------------------------------------------

HARNESSLESS_MODE_NOTE = """
EVALUATION MODE (HARNESS-LESS):
You have NO filesystem, shell, or tool access in this run. You cannot
read /geos_lib/, run GEOS, or write files. Produce the requested XML
deck(s) directly in your response from the spec alone.

OUTPUT FORMAT (STRICT):
Emit each required XML file inside a `<file>` tag, one tag per file:

    <file path="FILENAME.xml">
    <?xml version="1.0" ?>
    <Problem>
      ...
    </Problem>
    </file>

Rules:
  - Use EXACTLY the filenames listed in the spec's final
    "XML files to create:" line. Do not rename them.
  - No prose, commentary, or markdown fences outside `<file>` tags.
    Any text between `<file>` blocks is ignored by the grader.
  - Emit all required files. Do not abbreviate, elide, or leave
    `<!-- ... -->` placeholders in place of content.
""".strip()


def _strip_evaluation_mode_block(agents_md: str) -> str:
    """Remove the original 'EVALUATION MODE' + file-location rules blocks.

    The AGENTS.md targets the agent harness (write files to inputs/). We
    replace that guidance with the inline-XML protocol above.
    """
    # Drop "EVALUATION MODE:" paragraph.
    agents_md = re.sub(
        r"EVALUATION MODE:\s*\n(?:.*\n)+?\n(?=\n|[A-Z])",
        "",
        agents_md,
    )
    # Drop "CRITICAL FILE LOCATION RULES:" paragraph (file-writing rules).
    agents_md = re.sub(
        r"CRITICAL FILE LOCATION RULES:\s*\n(?:.*\n)+?\n(?=\n|[A-Z])",
        "",
        agents_md,
    )
    # Drop "FILE ACCESS RULES:" paragraph (no /workspace in this mode).
    agents_md = re.sub(
        r"FILE ACCESS RULES:\s*\n(?:.*\n)+?\n(?=\n|[A-Z])",
        "",
        agents_md,
    )
    return agents_md


def load_system_prompt() -> str:
    agents_md = AGENTS_MD_PATH.read_text()
    adapted = _strip_evaluation_mode_block(agents_md)
    return adapted.rstrip() + "\n\n---\n\n" + HARNESSLESS_MODE_NOTE


def parse_required_filenames(instructions: str) -> list[str]:
    """Extract the filenames from the trailing 'XML files to create: a.xml, ...' line."""
    m = re.search(
        r"XML files to create:\s*(.+?)\s*$",
        instructions,
        re.MULTILINE,
    )
    if not m:
        return []
    names = [n.strip() for n in m.group(1).split(",")]
    return [n for n in names if n.endswith(".xml")]


def build_task_user_prompt(instructions: str) -> str:
    return (
        "--- BEGIN SIMULATION SPECIFICATION ---\n"
        f"{instructions.strip()}\n"
        "--- END SIMULATION SPECIFICATION ---"
    )


def build_icl_assistant_message(gt_xml_dir: Path, filenames: list[str]) -> str:
    """Construct the assistant's 'ideal' response for the ICL example."""
    parts: list[str] = []
    for fname in filenames:
        path = gt_xml_dir / fname
        if not path.exists():
            raise FileNotFoundError(f"ICL ground-truth file missing: {path}")
        body = path.read_text().rstrip()
        parts.append(f'<file path="{fname}">\n{body}\n</file>')
    return "\n\n".join(parts)


def load_icl_example(
    icl_task: str,
    specs_dir: Path,
    gt_dir: Path,
) -> tuple[str, str] | None:
    """Returns (user_content, assistant_content) for the 1-shot example, or None."""
    if icl_task is None or icl_task.lower() == "none":
        return None
    spec_path = specs_dir / icl_task / "instructions.txt"
    if not spec_path.exists():
        raise FileNotFoundError(f"ICL spec not found: {spec_path}")
    instructions = spec_path.read_text()
    filenames = parse_required_filenames(instructions)
    if not filenames:
        raise ValueError(f"ICL task {icl_task} has no 'XML files to create:' line")
    gt_xml_dir = gt_dir / icl_task / "inputs"
    if not gt_xml_dir.exists():
        raise FileNotFoundError(f"ICL ground-truth dir missing: {gt_xml_dir}")
    user_msg = build_task_user_prompt(instructions)
    assistant_msg = build_icl_assistant_message(gt_xml_dir, filenames)
    return user_msg, assistant_msg


# -----------------------------------------------------------------------
# Response parsing
# -----------------------------------------------------------------------

FILE_BLOCK_RE = re.compile(
    r'<file\s+path="([^"]+)"\s*>\s*\n?(.*?)\n?\s*</file\s*>',
    re.DOTALL,
)


def parse_file_blocks(response: str) -> dict[str, str]:
    """Return {filename: xml_content} for every <file path="...">...</file> block."""
    blocks: dict[str, str] = {}
    for m in FILE_BLOCK_RE.finditer(response):
        fname = m.group(1).strip()
        # The spec only expects XML files; strip any directory components.
        fname = Path(fname).name
        blocks[fname] = m.group(2).rstrip() + "\n"
    return blocks


# -----------------------------------------------------------------------
# Per-task worker
# -----------------------------------------------------------------------

def run_one_task(
    task_name: str,
    *,
    specs_dir: Path,
    output_dir: Path,
    system_prompt: str,
    icl_messages: list[dict] | None,
    client: OpenAI,
    model: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> dict:
    task_out = output_dir / task_name
    inputs_out = task_out / "inputs"
    inputs_out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    result: dict = {"task": task_name, "model": model}

    spec_path = specs_dir / task_name / "instructions.txt"
    if not spec_path.exists():
        result.update({"status": "error", "error": f"spec missing: {spec_path}"})
        return result
    instructions = spec_path.read_text()
    required = parse_required_filenames(instructions)
    result["required_filenames"] = required

    messages = [{"role": "system", "content": system_prompt}]
    if icl_messages:
        messages.extend(icl_messages)
    messages.append({"role": "user", "content": build_task_user_prompt(instructions)})

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        result.update({
            "status": "api_error",
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_s": round(time.time() - t0, 1),
        })
        return result

    content = (response.choices[0].message.content or "").strip()
    (task_out / "raw_response.txt").write_text(content)

    blocks = parse_file_blocks(content)
    found = sorted(blocks.keys())
    missing = [n for n in required if n not in blocks]
    extra = [n for n in found if n not in required]

    for fname, body in blocks.items():
        # Safe filenames only; strip any traversal.
        safe = Path(fname).name
        if not safe.endswith(".xml"):
            continue
        (inputs_out / safe).write_text(body)

    usage = getattr(response, "usage", None)
    usage_dict = None
    if usage is not None:
        usage_dict = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }

    result.update({
        "status": "ok" if not missing else "missing_files",
        "found_filenames": found,
        "missing_filenames": missing,
        "extra_filenames": extra,
        "usage": usage_dict,
        "generation_id": getattr(response, "id", None),
        "elapsed_s": round(time.time() - t0, 1),
    })

    (task_out / "metadata.json").write_text(json.dumps(result, indent=2))
    return result


# -----------------------------------------------------------------------
# Driver
# -----------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-name", required=True,
                   help="Output subdirectory under data/eval/harnessless/")
    p.add_argument("--model", default="minimax/minimax-m2.7",
                   help="OpenRouter model ID (default: minimax/minimax-m2.7)")
    p.add_argument("--tasks", nargs="+", default=None,
                   help=f"Task names (default: 17-task test subset)")
    p.add_argument("--icl-task", default="ExampleProppantTest",
                   help="Task name to use as 1-shot ICL example, or 'none' for zero-shot "
                        "(default: ExampleProppantTest, held out from the 36-task test pool)")
    p.add_argument("--specs-dir", type=Path, default=DEFAULT_SPECS_DIR,
                   help="Directory containing <task>/instructions.txt for all 46 v2 tasks")
    p.add_argument("--gt-dir", type=Path, default=DEFAULT_GT_DIR,
                   help="Ground-truth dir (also used to load ICL assistant content)")
    p.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--max-tokens", type=int, default=16384,
                   help="Per-response cap. GEOS decks with many includes can be long.")
    p.add_argument("--timeout", type=int, default=600,
                   help="Per-request timeout in seconds")
    p.add_argument("--score", action="store_true",
                   help="After generation, run batch_evaluate.py for TreeSim scores")
    p.add_argument("--results-dir", type=Path, default=None,
                   help="Where to write per-task eval JSON (default: <output>/../results/<run_name>)")
    args = p.parse_args()

    tasks = args.tasks or TEST_TASKS_17
    if args.icl_task and args.icl_task.lower() != "none" and args.icl_task in tasks:
        print(f"ERROR: --icl-task {args.icl_task!r} overlaps with test tasks.",
              file=sys.stderr)
        return 2

    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

    system_prompt = load_system_prompt()
    icl = load_icl_example(args.icl_task, args.specs_dir, args.gt_dir)
    icl_messages: list[dict] | None = None
    if icl is not None:
        user_msg, assistant_msg = icl
        icl_messages = [
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": assistant_msg},
        ]
        print(f"[info] ICL task: {args.icl_task} "
              f"(assistant message ~{len(assistant_msg)} chars)")
    else:
        print("[info] Zero-shot (no ICL)")

    output_dir = args.output_root / args.run_name
    output_dir.mkdir(parents=True, exist_ok=True)

    run_meta = {
        "run_name": args.run_name,
        "model": args.model,
        "icl_task": args.icl_task,
        "tasks": tasks,
        "specs_dir": str(args.specs_dir),
        "gt_dir": str(args.gt_dir),
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "workers": args.workers,
        "started": datetime.now().isoformat(),
    }
    (output_dir / "run_meta.json").write_text(json.dumps(run_meta, indent=2))

    print(f"[info] Launching {len(tasks)} tasks x {args.workers} workers "
          f"against {args.model}")

    start = time.time()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                run_one_task,
                task,
                specs_dir=args.specs_dir,
                output_dir=output_dir,
                system_prompt=system_prompt,
                icl_messages=icl_messages,
                client=client,
                model=args.model,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                timeout=args.timeout,
            ): task
            for task in tasks
        }
        for fut in as_completed(futures):
            task = futures[fut]
            try:
                r = fut.result()
            except Exception as exc:  # noqa: BLE001
                r = {
                    "task": task,
                    "status": "worker_crash",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
            results.append(r)
            status = r.get("status", "?")
            elapsed = r.get("elapsed_s", "?")
            missing = r.get("missing_filenames") or []
            tag = f"missing={missing}" if missing else "OK"
            print(f"  [{status:14s}] {task:60s} {elapsed}s  {tag}")

    total_elapsed = round(time.time() - start, 1)
    summary = {
        "run_name": args.run_name,
        "model": args.model,
        "icl_task": args.icl_task,
        "n_tasks": len(tasks),
        "n_ok": sum(1 for r in results if r.get("status") == "ok"),
        "n_missing": sum(1 for r in results if r.get("status") == "missing_files"),
        "n_api_error": sum(1 for r in results if r.get("status") == "api_error"),
        "n_crash": sum(1 for r in results if r.get("status") == "worker_crash"),
        "total_elapsed_s": total_elapsed,
        "finished": datetime.now().isoformat(),
        "results": sorted(results, key=lambda r: r.get("task", "")),
    }
    (output_dir / "_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\n[info] Generation done in {total_elapsed}s. "
          f"ok={summary['n_ok']} missing={summary['n_missing']} "
          f"api_err={summary['n_api_error']} crash={summary['n_crash']}")

    if args.score:
        results_dir = args.results_dir or (
            args.output_root.parent / "results" / args.run_name
        )
        print(f"[info] Scoring against GT at {args.gt_dir}")
        print(f"[info] Writing per-task eval JSON to {results_dir}")
        import subprocess
        cmd = [
            sys.executable,
            str(REPO_ROOT / "scripts" / "eval" / "batch_evaluate.py"),
            "--experiments-dir", str(output_dir),
            "--ground-truth-dir", str(args.gt_dir),
            "--results-dir", str(results_dir),
            "--experiments", *tasks,
            "--output", str(results_dir / "_aggregate.json"),
        ]
        rc = subprocess.call(cmd)
        if rc != 0:
            print(f"[warn] batch_evaluate exited {rc}")
            return rc

    return 0


if __name__ == "__main__":
    sys.exit(main())
