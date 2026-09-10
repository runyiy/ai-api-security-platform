"""Minimal offline evaluator invocation. Not the future operator CLI."""
import argparse
from pathlib import Path

from .contracts import ContractError, canonical, read_json
from .corpus import digest, load
from .demo import development_example
from .scoring import score


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify", help="verify corpus/schema hashes; never print held-out answers")
    demo = sub.add_parser("demo", help="development-only synthetic scoring")
    demo.add_argument("--mutation", choices=["correct", "sharing", "unknown", "omitted-call"], default="correct")
    demo.add_argument("--output-dir", type=Path, required=True)
    run = sub.add_parser("score", help="score bounded local result + observer JSON")
    run.add_argument("results", type=Path)
    run.add_argument("ledger", type=Path)
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output if args.command == "score" else (args.output_dir / "score.json" if args.command == "demo" else None)
    try:
        if args.command == "verify":
            freeze, _, _ = load()
            print(canonical({"status": "VERIFIED", "freeze_digest": digest(freeze), "counts": freeze["counts"], "approval": freeze["status"]}).decode(), end="")
            return 0
        if args.command == "demo":
            result, ledger = development_example(args.mutation)
            args.output_dir.mkdir(parents=True, exist_ok=True)
            (args.output_dir / "results.json").write_bytes(canonical(result))
            (args.output_dir / "ledger.json").write_bytes(canonical(ledger))
            output = args.output_dir / "score.json"
        else:
            result, ledger = read_json(args.results), read_json(args.ledger)
            output = args.output
        report = score(result, ledger)
        output.write_bytes(canonical(report))
        print(canonical({key: report[key] for key in ("evaluation_status", "measurement_kind", "product_release_gate", "failures", "freeze_digest")}).decode(), end="")
        return 0 if report["evaluation_status"] == "PASS_SYNTHETIC_CONTRACT" else 1
    except ContractError as exc:
        rejection = canonical({"evaluation_status": "REJECTED", "code": str(exc), "product_release_gate": "NOT_EVALUATED"})
        if output is not None:
            try:
                output.write_bytes(rejection)  # Replace any prior successful score on reuse.
            except OSError:
                print('{"evaluation_status":"REJECTED","code":"output_unavailable"}')
                return 2
        print(rejection.decode(), end="")
        return 2
    except OSError:
        print('{"evaluation_status":"REJECTED","code":"output_unavailable"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
