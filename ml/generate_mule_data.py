"""Synthetic multi-account transaction generator (AMLSim-inspired, written from scratch).

Produces clearly-labelled SYNTHETIC data with known injected typologies so the
detection pipeline can be tested in a controlled way. It does not establish
real-world performance.

Background behaviour (label 0): salaries, merchant payments, utility bills,
peer-to-peer transfers, merchants that legitimately receive many payments
(a realistic fan-in false-positive source), and a few legitimately dormant
accounts that re-activate.

Injected typologies (label 1 for participating mule accounts):
  fan_in_out    many victims -> collector -> rapid fan-out to cash-out accounts
  pass_through  chain A -> M1 -> M2 -> M3 -> exit, each hop within minutes
  cycle         funds go around a ring of 3-5 accounts within 72 hours
  structuring   large sum split into 45k-50k transfers to several mules
  dormant       dormant account suddenly receives and forwards a burst

Usage:
  python -m ml.generate_mule_data --seed 1 --accounts 900 --out data/generated/mule_train
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

START = pd.Timestamp("2026-08-01")
DAYS = 90


class Gen:
    def __init__(self, seed: int, n_accounts: int):
        self.rng = np.random.default_rng(seed)
        self.seed = seed
        self.rows: list[tuple] = []
        self.labels: dict[str, tuple[int, str, str]] = {}
        self.n = n_accounts
        self._next = 100000 + seed * 7919 % 50000
        self.tid = 0

    def acct(self) -> str:
        self._next += int(self.rng.integers(1, 40))
        return f"AC{self._next:07d}"

    def tx(self, ts: pd.Timestamp, src: str, dst: str, amt: float, ch: str):
        self.tid += 1
        self.rows.append((f"S{self.seed}T{self.tid:07d}", ts.floor("s"), src, dst, round(float(amt), 2), ch))

    def rand_time(self, day_lo=0, day_hi=DAYS) -> pd.Timestamp:
        d = self.rng.uniform(day_lo, day_hi)
        hour = self.rng.choice(np.arange(24), p=_HOUR_P)
        return (START + pd.Timedelta(days=int(d))).normalize() + pd.Timedelta(hours=int(hour), minutes=float(self.rng.uniform(0, 60)))

    # ---------------- background ----------------
    def background(self):
        rng = self.rng
        n_people = int(self.n * 0.82)
        n_merch = max(8, int(self.n * 0.06))
        n_employers = max(4, int(self.n * 0.02))
        n_utils = 4
        people = [self.acct() for _ in range(n_people)]
        merchants = [self.acct() for _ in range(n_merch)]
        employers = [self.acct() for _ in range(n_employers)]
        utils = [self.acct() for _ in range(n_utils)]
        for a in people:
            self.labels[a] = (0, "normal_individual", "")
        for a in merchants:
            self.labels[a] = (0, "normal_merchant", "")
        for a in employers:
            self.labels[a] = (0, "normal_employer", "")
        for a in utils:
            self.labels[a] = (0, "normal_utility", "")
        self.people = people

        friends = {p: list(rng.choice(people, size=int(rng.integers(2, 7)), replace=False)) for p in people}
        merch_pop = rng.dirichlet(np.ones(n_merch) * 0.6)
        for p in people:
            salary = float(rng.lognormal(10.4, 0.45))  # ~33k median
            emp = employers[int(rng.integers(0, n_employers))]
            payday = int(rng.integers(1, 6))
            for m in range(3):
                ts = START + pd.DateOffset(months=m) + pd.Timedelta(days=payday - 1, hours=10, minutes=int(rng.integers(0, 50)))
                self.tx(ts, emp, p, salary * rng.uniform(0.98, 1.02), "NEFT")
            for _ in range(int(rng.poisson(18))):
                m = merchants[int(rng.choice(n_merch, p=merch_pop))]
                self.tx(self.rand_time(), p, m, rng.lognormal(6.2, 0.9), "UPI")
            for m in range(3):
                ts = START + pd.DateOffset(months=m) + pd.Timedelta(days=int(rng.integers(5, 15)), hours=int(rng.integers(8, 22)))
                self.tx(ts, p, utils[int(rng.integers(0, n_utils))], rng.uniform(400, 3000), "BBPS")
            for _ in range(int(rng.poisson(3))):
                self.tx(self.rand_time(), p, friends[p][int(rng.integers(0, len(friends[p])))], rng.lognormal(7.0, 0.8), "UPI")
            if rng.random() < 0.04:  # occasional large legitimate transfer (rent deposit, fees)
                self.tx(self.rand_time(), p, people[int(rng.integers(0, n_people))], rng.uniform(30000, 90000), "IMPS")
        for m in merchants:
            for k in range(3):
                ts = START + pd.DateOffset(months=k) + pd.Timedelta(days=int(rng.integers(20, 27)), hours=11)
                self.tx(ts, m, employers[int(rng.integers(0, n_employers))], rng.uniform(50000, 400000), "RTGS")
        self.merchants = merchants
        self.employers = employers
        # ---- hard negatives: legitimate behaviour that resembles mule typologies ----
        for _ in range(max(3, self.n // 60)):  # group collection: friends chip in, organiser pays a vendor
            org = people[int(rng.integers(0, n_people))]
            self.labels[org] = (0, "normal_group_collection", "")
            t = self.rand_time(3, DAYS - 2)
            tot = 0.0
            for f in rng.choice(people, size=int(rng.integers(6, 13)), replace=False):
                a = rng.uniform(800, 4000); tot += a
                self.tx(t + pd.Timedelta(minutes=float(rng.uniform(0, 600))), f, org, a, "UPI")
            self.tx(t + pd.Timedelta(minutes=float(rng.uniform(620, 900))), org, merchants[int(rng.integers(0, n_merch))], tot * rng.uniform(0.9, 1.0), "UPI")
        for _ in range(max(3, self.n // 50)):  # salary sweep to own savings account within minutes
            p = people[int(rng.integers(0, n_people))]
            own = self.acct()
            self.labels[p] = (0, "normal_salary_sweep", "")
            self.labels[own] = (0, "normal_own_savings", "")
            for m in range(3):
                ts = START + pd.DateOffset(months=m) + pd.Timedelta(days=1, hours=10)
                amt = rng.uniform(40000, 90000)
                self.tx(ts, employers[int(rng.integers(0, n_employers))], p, amt, "NEFT")
                self.tx(ts + pd.Timedelta(minutes=float(rng.uniform(5, 50))), p, own, amt * rng.uniform(0.8, 0.95), "IMPS")
        for _ in range(max(2, self.n // 120)):  # small business: many customers in, supplier payments in 45-50k chunks
            b = self.acct()
            self.labels[b] = (0, "normal_small_business", "")
            for d in range(0, DAYS, 3):
                for _ in range(int(rng.integers(4, 12))):
                    self.tx(START + pd.Timedelta(days=d, hours=float(rng.uniform(9, 21))), people[int(rng.integers(0, n_people))], b, rng.uniform(200, 6000), "UPI")
            for k in range(int(rng.integers(2, 5))):
                t = self.rand_time(10, DAYS - 2)
                sup = self.acct(); self.labels[sup] = (0, "normal_supplier", "")
                for _ in range(3):
                    self.tx(t + pd.Timedelta(hours=float(rng.uniform(0, 8))), b, sup, rng.uniform(45000, 49900), "IMPS")
        # legitimately dormant-then-active accounts (false-positive source)
        for _ in range(max(2, self.n // 150)):
            a = self.acct()
            self.labels[a] = (0, "normal_reactivated", "")
            day = int(rng.integers(45, 80))
            base = START + pd.Timedelta(days=day, hours=10)
            self.tx(base, employers[0], a, rng.uniform(40000, 80000), "NEFT")
            for k in range(int(rng.integers(8, 12))):
                self.tx(base + pd.Timedelta(hours=float(rng.uniform(1, 40))), a, merchants[int(rng.integers(0, n_merch))], rng.uniform(300, 4000), "UPI")

    # ---------------- typologies ----------------
    def mule_acct(self) -> str:
        """Half of mules are recruited ordinary account holders with full normal history."""
        if self.rng.random() < 0.5:
            for _ in range(20):
                a = self.people[int(self.rng.integers(0, len(self.people)))]
                if self.labels.get(a, (0, "normal_individual"))[1] == "normal_individual":
                    return a
        return self.acct()

    def hold(self) -> pd.Timedelta:
        """Holding time before forwarding: usually minutes, sometimes hours (harder)."""
        if self.rng.random() < 0.65:
            return pd.Timedelta(minutes=float(self.rng.uniform(3, 45)))
        return pd.Timedelta(minutes=float(self.rng.uniform(90, 600)))

    def _mark(self, accts, typ, ring):
        for a in accts:
            self.labels[a] = (1, typ, ring)

    def _ext(self) -> str:
        e = self.acct()
        self.labels.setdefault(e, (0, "external_unknown", ""))
        return e

    def fan_in_out(self, ring: str):
        rng = self.rng
        collector = self.mule_acct()
        cashouts = [self.mule_acct() for _ in range(int(rng.integers(2, 6)))]
        victims = list(rng.choice(self.people, size=int(rng.integers(5, 16)), replace=False))
        t0 = self.rand_time(10, DAYS - 5)
        spread = float(rng.choice([25, 240, 1200]))  # minutes over which victims pay in
        total = 0.0
        for v in victims:
            amt = rng.uniform(3000, 30000)
            total += amt
            self.tx(t0 + pd.Timedelta(minutes=float(rng.uniform(0, spread))), v, collector, amt, "UPI")
        left = total * rng.uniform(0.75, 0.95)
        t1 = t0 + pd.Timedelta(minutes=spread) + self.hold()
        shares = rng.dirichlet(np.ones(len(cashouts)))
        for c, s in zip(cashouts, shares):
            t2 = t1 + pd.Timedelta(minutes=float(rng.uniform(0, 30)))
            self.tx(t2, collector, c, left * s, "IMPS")
            self.tx(t2 + self.hold(), c, self._ext(), left * s * rng.uniform(0.9, 0.99), "IMPS")
        self._mark([collector, *cashouts], "fan_in_out", ring)

    def pass_through(self, ring: str):
        rng = self.rng
        chain = [self.mule_acct() for _ in range(int(rng.integers(2, 5)))]
        src = self.people[int(rng.integers(0, len(self.people)))]
        exit_ = self._ext()
        for rep in range(int(rng.integers(1, 4))):
            t = self.rand_time(5, DAYS - 3)
            amt = rng.uniform(25000, 150000)
            self.tx(t, src, chain[0], amt, "IMPS")
            for a, b in zip(chain, chain[1:] + [exit_]):
                t = t + self.hold()
                amt *= rng.uniform(0.95, 0.995)
                self.tx(t, a, b, amt, "IMPS")
        self._mark(chain, "pass_through", ring)

    def cycle(self, ring: str):
        rng = self.rng
        ringa = [self.mule_acct() for _ in range(int(rng.integers(3, 6)))]
        for rep in range(int(rng.integers(1, 4))):
            t = self.rand_time(5, DAYS - 4)
            amt = rng.uniform(20000, 250000)
            for a, b in zip(ringa, ringa[1:] + ringa[:1]):
                t = t + pd.Timedelta(hours=float(rng.uniform(0.3, 14)))
                amt *= rng.uniform(0.94, 0.995)
                self.tx(t, a, b, amt, "NEFT")
        self._mark(ringa, "cycle", ring)

    def structuring(self, ring: str):
        rng = self.rng
        boss = self.mule_acct()
        mules = [self.mule_acct() for _ in range(int(rng.integers(2, 6)))]
        t = self.rand_time(5, DAYS - 3)
        lo = float(rng.choice([45000, 38000]))  # some rings split lower, below the rule band
        for m in mules:
            for k in range(int(rng.integers(1, 4))):
                self.tx(t + pd.Timedelta(hours=float(rng.uniform(0, 30))), boss, m, rng.uniform(lo, 49900), "IMPS")
        consolidator = self.mule_acct()
        for m in mules:
            self.tx(t + pd.Timedelta(hours=float(rng.uniform(31, 48))), m, consolidator, rng.uniform(lo, 49900), "IMPS")
        self._mark([boss, *mules, consolidator], "structuring", ring)

    def dormant(self, ring: str):
        rng = self.rng
        a = self.acct()
        self.tx(START + pd.Timedelta(days=int(rng.integers(0, 4)), hours=12), self.people[0], a, 500, "UPI")
        t = START + pd.Timedelta(days=int(rng.integers(40, 85)), hours=int(rng.integers(0, 23)))
        senders = rng.choice(self.people, size=int(rng.integers(3, 9)), replace=False)
        tot = 0.0
        for s in senders:
            amt = rng.uniform(8000, 25000)
            tot += amt
            self.tx(t + pd.Timedelta(minutes=float(rng.uniform(0, 600))), s, a, amt, "UPI")
        k_out = int(rng.integers(2, 6))
        for k in range(k_out):
            self.tx(t + pd.Timedelta(minutes=610) + self.hold(), a, self._ext(), tot * 0.95 / k_out, "IMPS")
        self._mark([a], "dormant_activation", ring)

    def run(self, rings_per_type: int):
        self.background()
        r = 0
        for fn in (self.fan_in_out, self.pass_through, self.cycle, self.structuring, self.dormant):
            for _ in range(rings_per_type):
                r += 1
                fn(f"S{self.seed}R{r:03d}")
        tx = pd.DataFrame(self.rows, columns=["txn_id", "timestamp", "from_account", "to_account", "amount", "channel"])
        tx = tx.sort_values("timestamp").reset_index(drop=True)
        accts = sorted(set(tx["from_account"]) | set(tx["to_account"]))
        lab = pd.DataFrame(
            [(a, *self.labels.get(a, (0, "external_unknown", ""))) for a in accts],
            columns=["account", "is_mule", "typology", "ring_id"],
        )
        return tx, lab


_h = np.array([1, 0.5, 0.3, 0.2, 0.2, 0.4, 1, 2, 4, 6, 7, 7, 7, 6, 6, 6, 6, 7, 8, 8, 7, 5, 3, 2], dtype=float)
_HOUR_P = _h / _h.sum()


def generate(seed: int, accounts: int, rings_per_type: int):
    return Gen(seed, accounts).run(rings_per_type)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--accounts", type=int, default=900)
    ap.add_argument("--rings", type=int, default=6, help="rings per typology")
    ap.add_argument("--out", required=True, help="output prefix, e.g. data/generated/mule_train")
    a = ap.parse_args()
    tx, lab = generate(a.seed, a.accounts, a.rings)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tx.to_csv(f"{out}_transactions.csv", index=False)
    lab.to_csv(f"{out}_labels.csv", index=False)
    print(f"SYNTHETIC: {len(tx)} transactions, {len(lab)} accounts, {int(lab.is_mule.sum())} mule accounts -> {out}_*.csv")


if __name__ == "__main__":
    main()
