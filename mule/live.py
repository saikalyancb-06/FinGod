"""Live transaction streaming for MuleScope.

* Hub: pushes events (new transactions, analysis summaries, NEW ALERTS) to every browser
  subscribed to a dataset over a WebSocket. Thread-safe: producers run in threads.
* Simulator: a built-in, mr-mule-compatible feed. It emits realistic everyday payments
  (salaries, UPI to merchants, friends) on an accelerated clock and injects money-mule
  typologies on a schedule or on demand: fan-in/out, pass-through chain, cycle, smurfing,
  structuring. Every batch is appended and re-analysed, so alerts appear in real time.
"""
from __future__ import annotations

import asyncio
import itertools
import random
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

TICK = timedelta(hours=1)  # 1 tick (≈1 s at speed 1×) = 1 simulated hour

PATTERNS = {
    "fan_in_out": "Collect & disperse: many payers → one account → quick cash-outs",
    "pass_through": "Pass-through chain: money hops through 4 accounts within minutes",
    "cycle": "Circular flow: funds go round a ring of accounts",
    "smurfing": "Smurfing: a dozen small payments from different accounts in 72 h",
    "structuring": "Structuring: repeated ₹45–50k transfers to stay under a threshold",
}


class Hub:
    def __init__(self):
        self.subs: dict[int, set] = defaultdict(set)
        self.lock = threading.Lock()
        self.recent: dict[int, deque] = defaultdict(lambda: deque(maxlen=30))

    def add(self, ds_id: int, ws, loop):
        with self.lock:
            self.subs[ds_id].add((ws, loop))

    def remove(self, ds_id: int, ws):
        with self.lock:
            self.subs[ds_id] = {(w, l) for (w, l) in self.subs[ds_id] if w is not ws}

    def broadcast(self, ds_id: int, msg: dict):
        if msg.get("type") in ("batch", "alert", "pattern", "status"):
            self.recent[ds_id].append(msg)
        with self.lock:
            targets = list(self.subs.get(ds_id, ()))
        for ws, loop in targets:
            try:
                asyncio.run_coroutine_threadsafe(ws.send_json(msg), loop)
            except Exception:
                self.remove(ds_id, ws)


hub = Hub()


class Simulator(threading.Thread):
    """Streams synthetic transactions into a live dataset until stopped."""

    def __init__(self, ds_id: int, append, *, speed: float = 1.0, auto_patterns: bool = True, seed: int | None = None,
                 max_rows: int = 40000, n_accounts: int = 450):
        super().__init__(daemon=True)
        self.ds_id, self.append, self.speed = ds_id, append, max(0.25, min(speed, 4.0))
        self.auto = auto_patterns
        self.rng = random.Random(seed or int(time.time()))
        self.stop_evt = threading.Event()
        self.queue: deque = deque()
        self.max_rows = max_rows
        self.sent = 0
        self.ids = itertools.count(1)
        self.clock = datetime.now().replace(second=0, microsecond=0) - timedelta(days=2)
        self.next_auto = 3
        self.baseline_alerts: set = set()
        self.injected: list[dict] = []
        # Normal behaviour comes from the same generator the INR side of the mule model was trained on
        # (ml/generate_mule_data.py): quiet individuals, busy merchants, payroll, utilities and hard
        # negatives such as group collections, salary sweeps and small businesses. 90 days of it form
        # the account history the live window starts from.
        from ml.generate_mule_data import DAYS, START, Gen
        g = Gen(seed=self.rng.randint(1000, 9999), n_accounts=n_accounts)
        g.background()
        shift = pd.Timestamp(self.clock) - (START + pd.Timedelta(days=DAYS))
        self._history = [(ts + shift).to_pydatetime() for ts in (r[1] for r in g.rows)]
        self._history_rows = g.rows
        self._shift = shift
        self.people = [a for a in g.people if g.labels.get(a, (0, ""))[1] == "normal_individual"]
        self.merchants, self.employers = g.merchants, g.employers
        self.utils = sorted({r[3] for r in g.rows if r[5] == "BBPS"})
        friends: dict = defaultdict(set)
        for _, _, a, b, _, ch in g.rows:
            if ch == "UPI" and a in g.labels and b in g.labels and g.labels[b][1].startswith("normal_individual"):
                friends[a].add(b)
        self.friends = {p: sorted(friends.get(p, set())) or self.rng.sample(self.people, 3) for p in self.people}
        self.merch_w = np.array([sum(1 for r in g.rows if r[3] == m) for m in self.merchants], dtype=float) + 1
        self.status = "running"

    # ---------------- helpers ----------------
    def _new_acct(self) -> str:
        return f"AC{self.rng.randint(1000000, 9999999)}"

    def _row(self, ts, src: str, dst: str, amt: float) -> str:
        return f"LIVE{self.ds_id}_{next(self.ids):07d},{src},{dst},{amt:.2f},{ts:%Y-%m-%d %H:%M:%S}"

    def inject(self, pattern: str) -> dict:
        """Queue a typology; it is spread over the next few ticks of simulated time."""
        r, t0 = self.rng, self.clock
        rows: list[tuple[datetime, str]] = []
        involved: list[str] = []
        if pattern == "fan_in_out":
            hub_acct = self._new_acct()
            outs = [self._new_acct() for _ in range(r.randint(3, 4))]
            victims = r.sample(self.people, 11)
            tot = 0.0
            for v in victims:
                a = r.uniform(6000, 25000); tot += a
                ts = t0 + timedelta(minutes=r.uniform(0, 25))
                rows.append((ts, self._row(ts, v, hub_acct, a)))
            for o in outs:
                ts = t0 + timedelta(minutes=r.uniform(32, 50))
                rows.append((ts, self._row(ts, hub_acct, o, tot * 0.9 / len(outs))))
                ts2 = ts + timedelta(minutes=r.uniform(5, 30))
                rows.append((ts2, self._row(ts2, o, self._new_acct(), tot * 0.88 / len(outs))))
            involved = [hub_acct, *outs]
        elif pattern == "pass_through":
            chain = [self._new_acct() for _ in range(4)]
            src, amt, ts = r.choice(self.people), r.uniform(60000, 150000), t0
            rows.append((ts, self._row(ts, src, chain[0], amt)))
            for a, b in zip(chain, chain[1:] + [self._new_acct()]):
                ts += timedelta(minutes=r.uniform(4, 18)); amt *= r.uniform(0.97, 0.995)
                rows.append((ts, self._row(ts, a, b, amt)))
            involved = chain
        elif pattern == "cycle":
            ring = [self._new_acct() for _ in range(4)]
            for rep in range(2):
                ts, amt = t0 + timedelta(hours=rep * 6), r.uniform(80000, 200000)
                for a, b in zip(ring, ring[1:] + ring[:1]):
                    ts += timedelta(hours=r.uniform(0.5, 3)); amt *= 0.98
                    rows.append((ts, self._row(ts, a, b, amt)))
            involved = ring
        elif pattern == "smurfing":
            hub_acct = self._new_acct()
            for s in r.sample(self.people, 13):
                ts = t0 + timedelta(hours=r.uniform(0, 40))
                rows.append((ts, self._row(ts, s, hub_acct, r.uniform(150, 450))))
            involved = [hub_acct]
        elif pattern == "structuring":
            boss, mules = self._new_acct(), [self._new_acct() for _ in range(3)]
            for m in mules:
                for _ in range(2):
                    ts = t0 + timedelta(hours=r.uniform(0, 18))
                    rows.append((ts, self._row(ts, boss, m, r.uniform(45000, 49900))))
            cons = self._new_acct()
            for m in mules:
                ts = t0 + timedelta(hours=r.uniform(19, 23))
                rows.append((ts, self._row(ts, m, cons, r.uniform(45000, 49900))))
            involved = [boss, *mules, cons]
        else:
            raise ValueError(f"Unknown pattern {pattern}")
        self.queue.extend(sorted(rows))
        info = {"pattern": pattern, "label": PATTERNS[pattern], "rows": len(rows), "at": str(t0), "accounts": involved}
        self.injected.append(info)
        hub.broadcast(self.ds_id, {"type": "pattern", **info})
        return info

    def _background(self, until: datetime) -> list[tuple[datetime, str]]:
        """Everyday activity for one tick, at the same per-person rates as the training generator:
        ~18 shop payments and ~3 transfers to friends per 90 days, monthly bills, payday salaries."""
        r, out = self.rng, []
        hrs = (until - self.clock).total_seconds() / 3600
        hour_w = [1, .5, .3, .2, .2, .4, 1, 2, 4, 6, 7, 7, 7, 6, 6, 6, 6, 7, 8, 8, 7, 5, 3, 2]
        busy = hour_w[self.clock.hour] / (sum(hour_w) / 24)
        lam = len(self.people) * hrs / 24 * busy
        mp = self.merch_w / self.merch_w.sum()
        for _ in range(np.random.default_rng(r.randint(0, 2**31)).poisson(lam * 18 / 90)):
            ts = self.clock + timedelta(seconds=r.uniform(0, hrs * 3600))
            m = self.merchants[int(np.searchsorted(np.cumsum(mp), r.random()))]
            out.append((ts, self._row(ts, r.choice(self.people), m, r.lognormvariate(6.2, 0.9))))
        for _ in range(np.random.default_rng(r.randint(0, 2**31)).poisson(lam * 3 / 90)):
            ts = self.clock + timedelta(seconds=r.uniform(0, hrs * 3600))
            p = r.choice(self.people)
            out.append((ts, self._row(ts, p, r.choice(self.friends[p]), r.lognormvariate(7.0, 0.8))))
        for _ in range(np.random.default_rng(r.randint(0, 2**31)).poisson(lam * 3 / 90)):
            ts = self.clock + timedelta(seconds=r.uniform(0, hrs * 3600))
            out.append((ts, self._row(ts, r.choice(self.people), r.choice(self.utils), r.uniform(400, 3000))))
        return out

    def warmup_rows(self) -> list[str]:
        """The 90-day account history before the live window (see __init__)."""
        rows = [((r[1] + self._shift).to_pydatetime(), r) for r in self._history_rows]
        rows.sort(key=lambda x: x[0])
        return [self._row(ts, r[2], r[3], r[4]) for ts, r in rows]

    def run(self):
        hub.broadcast(self.ds_id, {"type": "status", "status": "warming_up", "speed": self.speed})
        try:
            out = self.append(self.ds_id, self.warmup_rows())
            self.sent += out["added"]
            self.baseline_alerts = {a["account"] for a in out.get("new_alerts", [])}
            hub.broadcast(self.ds_id, {"type": "batch", **{**out, "rows": out.get("rows", [])[-15:], "new_alerts": []},
                                       "sim_clock": str(self.clock), "sent": self.sent, "warmup": True})
        except Exception as exc:
            hub.broadcast(self.ds_id, {"type": "error", "message": f"warm-up failed: {str(exc)[:160]}"})
        hub.broadcast(self.ds_id, {"type": "status", "status": "running", "speed": self.speed})
        tick = 0
        while not self.stop_evt.is_set() and self.sent < self.max_rows:
            t_start = time.time()
            until = self.clock + TICK
            if self.auto and tick >= self.next_auto:
                self.inject(self.rng.choice(list(PATTERNS)))
                self.next_auto = tick + self.rng.randint(10, 18)
            batch = self._background(until)
            while self.queue and self.queue[0][0] <= until:
                batch.append(self.queue.popleft())
            self.clock = until
            if batch:
                try:
                    out = self.append(self.ds_id, [r for _, r in sorted(batch)])
                    self.sent += out["added"]
                    hub.broadcast(self.ds_id, {"type": "batch", **out, "sim_clock": str(self.clock), "sent": self.sent})
                    for a in out.get("new_alerts", []):
                        hub.broadcast(self.ds_id, {"type": "alert", "dataset_id": self.ds_id, **a})
                except Exception as exc:  # keep streaming even if one batch fails
                    hub.broadcast(self.ds_id, {"type": "error", "message": str(exc)[:200]})
            tick += 1
            self.stop_evt.wait(max(0.2, 1.0 / self.speed - (time.time() - t_start)))
        self.status = "stopped" if self.stop_evt.is_set() else "finished"
        hub.broadcast(self.ds_id, {"type": "status", "status": self.status, "sent": self.sent})

    def stop(self):
        self.stop_evt.set()


sims: dict[int, Simulator] = {}
