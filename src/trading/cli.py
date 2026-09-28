"""Línea de comandos: python -m trading <comando>.

  scan                 escáner cuant + carteras automáticas (Q, RND) + brief para agentes
  decide [--run DIR]   aplica decisiones del agente, riesgo y propuestas reales; genera el informe
  monitor              vigilancia: salidas, órdenes pendientes y alertas
  validate TIPO FICH   valida context.json o decisions.json
  weekly               informe semanal de evaluación
  backtest             backtest histórico de la parte cuant
  status               resumen de carteras
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from trading.paths import RUNS_DIR


def latest_run(require: str = "shortlist.json") -> Path:
    runs = sorted(p.parent for p in RUNS_DIR.glob(f"*/*/*/*/{require}"))
    if not runs:
        raise SystemExit(f"No hay ejecuciones con {require} en {RUNS_DIR}")
    return runs[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trading", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_scan = sub.add_parser("scan")
    p_scan.add_argument("--no-coingecko", action="store_true")
    p_dec = sub.add_parser("decide")
    p_dec.add_argument("--run", type=Path)
    p_dec.add_argument("--force", action="store_true", help="re-ejecutar decide (duplica operaciones)")
    p_dec.add_argument("--no-live", action="store_true", help="no generar propuestas con dinero real en esta ejecución")
    sub.add_parser("monitor")
    p_val = sub.add_parser("validate")
    p_val.add_argument("kind", choices=["context", "decisions"])
    p_val.add_argument("path", type=Path)
    sub.add_parser("weekly")
    p_bt = sub.add_parser("backtest")
    p_bt.add_argument("--from", dest="start", default="2021-01-01")
    p_bt.add_argument("--to", dest="end", default=None)
    p_bt.add_argument("--random-runs", type=int, default=100)
    p_bt.add_argument("--refresh", action="store_true", help="vuelve a descargar el histórico")
    sub.add_parser("status")
    p_rs = sub.add_parser("research", help="evalúa las variantes pre-registradas (research/hypotheses.md)")
    p_rs.add_argument("--from", dest="start", default="2021-01-01")
    p_rs.add_argument("--random-runs", type=int, default=50)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.cmd == "scan":
        from trading.pipeline import scan

        rdir = scan(use_coingecko=not args.no_coingecko)
        market = json.loads((rdir / "market.json").read_text())
        shortlist = json.loads((rdir / "shortlist.json").read_text())
        print(f"RUN_DIR={rdir}")
        print(f"Régimen: {market['regime']['label']} (×{market['regime']['multiplier']}) · elegibles "
              f"{market['n_eligible']} · candidatas {market['n_candidates']}")
        print("Lista corta: " + ", ".join(f"{s['symbol']}({s['score']:.2f})" for s in shortlist))
        print(f"Siguiente paso: agentes → {rdir}/context.json y {rdir}/decisions.json; después `python -m trading decide`")
        return 0

    if args.cmd == "decide":
        from trading.pipeline import decide

        rdir = args.run or latest_run()
        result = decide(rdir, force=args.force, live=False if args.no_live else None)
        print(f"RUN_DIR={rdir}")
        for n in result["notes"]:
            print("nota:", n)
        print((rdir / "summary.txt").read_text())
        return 0

    if args.cmd == "monitor":
        from trading.pipeline import monitor

        rdir, needs_action = monitor()
        print(f"RUN_DIR={rdir}")
        print(f"ACCION_NECESARIA={'si' if needs_action else 'no'}")
        print((rdir / "summary.txt").read_text())
        return 0

    if args.cmd == "validate":
        from trading.ledger import Ledger
        from trading.schemas import Context, Decisions, check_decisions_against

        text = args.path.read_text()
        try:
            if args.kind == "context":
                Context.model_validate_json(text)
            else:
                dec = Decisions.model_validate_json(text)
                shortlist = json.loads((args.path.parent / "shortlist.json").read_text())
                open_ids = [t.trade_id for pf in ("QL", "live") for t in Ledger(pf).open_trades()]
                errors = check_decisions_against(dec, [s["symbol"] for s in shortlist], open_ids)
                if errors:
                    print("INVALIDO:\n- " + "\n- ".join(errors))
                    return 1
        except Exception as exc:
            print(f"INVALIDO: {exc}")
            return 1
        print("OK")
        return 0

    if args.cmd == "weekly":
        from trading.evaluate import weekly_report

        print(weekly_report())
        return 0

    if args.cmd == "backtest":
        from trading.backtest import run_backtest

        out = run_backtest(args.start, args.end, random_runs=args.random_runs, refresh=args.refresh)
        print(f"Informe: {out}")
        return 0

    if args.cmd == "research":
        from trading.research import run_research

        print(f"Informe: {run_research(args.start, random_runs=args.random_runs)}")
        return 0

    if args.cmd == "status":
        from trading.config import load_settings
        from trading.data.bitvavo import Bitvavo
        from trading.ledger import Ledger
        from trading.pipeline import capital_for

        settings = load_settings()
        marks = Bitvavo().tickers()["bid"].dropna().to_dict()
        for pf in ("live", "Q", "QL", "RND"):
            led = Ledger(pf)
            cap = capital_for(pf, settings)
            print(f"{pf:5s} capital {led.equity(cap, marks):9.2f} € · abiertas {len(led.open_trades())} · "
                  f"cerradas {len(led.closed_trades())} · P&L realizado {led.realized_pnl():+.2f} €")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
