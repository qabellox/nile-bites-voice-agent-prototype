"""Experiment commands: validate | run | dist.

validate  Check a trials file against its tasks (references, ground truth,
          audio files, duplicate ids, declared-vs-measured bands).
run       Freeze the configuration, re-validate, execute every trial on both
          architectures, and write JSONL + CSV records. Output goes to a
          dev-labeled run directory; this is engineering validation, NOT
          formal data collection.
dist      Print the verified-percentage distribution per declared band
          (evidence for freezing C0-C4 boundaries; chooses nothing).

Examples:
  python scripts/run_experiment.py validate --trials data/pilot/trials.yaml --tasks data/pilot/tasks.yaml
  python scripts/run_experiment.py dist --trials data/pilot/trials.yaml
  python scripts/run_experiment.py run --trials data/pilot/trials.yaml --tasks data/pilot/tasks.yaml --out runs/smoke-001 --only-trial PILOT-ORD2-C2
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=False)
try:
    # The user-confirmed key file wins over ambient shell values, which may
    # hold an invalid placeholder (observed: 11-char key causing 401s).
    load_dotenv(Path(r"C:\Users\Qebaa\.env"), override=True)
except Exception:
    pass

from nbva.agent.app import build_app
from nbva.config.loader import load_config
from nbva.research import experiment as E
from nbva.research.runner import ExperimentRunner
from nbva.research.taskdefs import load_tasks


def _load(trials_path, tasks_path):
    trials = E.load_trials(trials_path)
    tasks = load_tasks(tasks_path)
    return trials, {t.task_id: t for t in tasks}


def _analyzer_app(args):
    cfg = load_config()
    if args.llm_model:
        cfg.set("llm.provider", "openai_compatible")
        cfg.set("llm.model", args.llm_model)
        cfg.set("llm.base_url", "https://openrouter.ai/api/v1")
        cfg.set("llm.openai_compatible.api_key_env", "OPENAI_API_KEY")
    if args.asr_language is not None:
        cfg.set("asr.language", args.asr_language)
    app = build_app(cfg, attach_player=False)
    return cfg, app


def cmd_validate(args):
    trials, by_id = _load(args.trials, args.tasks)
    cfg, app = _analyzer_app(args)
    res = E.validate_dataset(
        trials, by_id, app.pipeline.conditions,
        measure_fn=lambda s: float(app.pipeline.analyzer.analyze(s).english_pct),
        audio_root=ROOT)
    print(f"trials={res['trial_count']} flagged={res['flagged_count']} clean={res['clean']}")
    for tid, flags in res["flags"].items():
        for flag in flags:
            print(f"  {tid}: {flag}")
    return 0 if res["clean"] else 2


def cmd_dist(args):
    trials, _ = _load(args.trials, args.tasks)
    dist = E.pilot_distribution([t.to_dict() for t in trials])
    print(f"total={dist['total']}")
    for band, row in sorted(dist["per_band"].items()):
        print(f"  {band}: n={row['n']} min={row['min']} max={row['max']} mean={row['mean']}")
    print("(boundaries are frozen by the researcher, never by this tool)")
    return 0


def cmd_run(args):
    trials, by_id = _load(args.trials, args.tasks)
    if args.only_trial:
        trials = [t for t in trials if t.trial_id == args.only_trial]
        if not trials:
            print(f"no trial {args.only_trial}")
            return 2
    cfg, app = _analyzer_app(args)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    freeze = E.freeze_experiment(
        cfg, out, experiment_id=args.experiment_id or out.name,
        db_path=ROOT / "data" / "restaurant" / "nile_bites.json",
        prompt_path=ROOT / "config" / "prompts" / "baseline_system_prompt.md")
    print(f"freeze: {out / 'freeze.json'}")

    res = E.validate_dataset(
        trials, by_id, app.pipeline.conditions,
        measure_fn=lambda s: float(app.pipeline.analyzer.analyze(s).english_pct),
        audio_root=ROOT)
    if not res["clean"]:
        print("dataset validation FAILED:")
        for tid, flags in res["flags"].items():
            for flag in flags:
                print(f"  {tid}: {flag}")
        return 2

    from nbva.research.wer import wer_config_from_config

    runner = ExperimentRunner(
        app=app, analyzer=app.pipeline.analyzer, conditions=app.pipeline.conditions,
        wer_config=wer_config_from_config(cfg), experiment_id=freeze.experiment_id,
        trace_dir=str(out / "stream-traces"))
    records = runner.run_experiment(trials, by_id)
    dicts = [r.to_dict() for r in records]
    jp = E.write_jsonl(dicts, out / "records.jsonl")
    cp = E.records_to_csv(dicts, out / "records.csv")
    print(f"records: {len(dicts)} -> {jp}, {cp}")
    for r in dicts:
        print(f"  {r['architecture']:9s} {r['trial_id']:18s} "
              f"success={r['task_success']} wer={r['wer']} "
              f"latency_s={r['latency_s']} fail={r['failure_category']} "
              f"err={str(r['error'])[:60] if r['error'] else None}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Experiment infrastructure commands.")
    sub = parser.add_subparsers(dest="command", required=True)

    def _common(p):
        p.add_argument("--trials", required=True)
        p.add_argument("--tasks", required=True)
        p.add_argument("--llm-model", default=None)
        p.add_argument("--asr-language", default=None)
        return p

    _common(sub.add_parser("validate"))
    _common(sub.add_parser("dist"))
    run_p = _common(sub.add_parser("run"))
    run_p.add_argument("--out", required=True)
    run_p.add_argument("--only-trial", default=None)
    run_p.add_argument("--experiment-id", default=None)

    args = parser.parse_args(argv)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "dist":
        return cmd_dist(args)
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
