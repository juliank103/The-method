"""Estimate how long automatic investing takes to fund a monthly income.

Usage:
    python3 calculator.py [monthly_contribution] [starting_balance]
        [--income 2000] [--return 0.05] [--withdrawal 0.04]

All amounts are in today's dollars, so --return should be the return
after inflation (about 5% is a conservative long-run assumption for a
diversified stock index fund).
"""

import argparse


def months_to_target(start, monthly, annual_return, target, max_months=1200):
    balance = start
    for month in range(max_months + 1):
        if balance >= target:
            return month
        balance = balance * (1 + annual_return / 12) + monthly
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("monthly", type=float, nargs="?", default=500)
    parser.add_argument("start", type=float, nargs="?", default=5000)
    parser.add_argument("--income", type=float, default=2000,
                        help="target monthly income (default 2000)")
    parser.add_argument("--return", dest="annual_return", type=float,
                        default=0.05, help="annual real return (default 0.05)")
    parser.add_argument("--withdrawal", type=float, default=0.04,
                        help="safe annual withdrawal rate (default 0.04)")
    args = parser.parse_args()

    target = args.income * 12 / args.withdrawal
    print(f"Portfolio needed for ${args.income:,.0f}/month: ${target:,.0f}")

    months = months_to_target(args.start, args.monthly, args.annual_return,
                              target)
    if months is None:
        print("Not reached within 100 years at these numbers.")
    else:
        print(f"Investing ${args.monthly:,.0f}/month from ${args.start:,.0f}: "
              f"~{months / 12:.1f} years")


if __name__ == "__main__":
    main()
