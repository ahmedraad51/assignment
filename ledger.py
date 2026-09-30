#!/usr/bin/env python3
"""Dual-currency receivables ledger.

The books are kept in IQD. Invoices are in USD. Customers pay in USD or IQD.
Exchange rates (IQD per 1 USD) change daily.

Usage:
    python ledger.py input.json > output.json

See README.md for the accounting rules.
"""

from __future__ import annotations

import bisect
import json
import sys
from dataclasses import dataclass
from datetime import date as Date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------

CASH = {"USD": "Cash - USD", "IQD": "Cash - IQD"}
REVENUE = "Sales Revenue"
FX_GAIN = "Realized FX Gain"
FX_LOSS = "Realized FX Loss"


def receivable(customer: str) -> str:
    """One receivable account per customer (a customer sub-ledger)."""
    return f"Accounts Receivable - {customer}"


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------

ONE_DINAR = Decimal("1")
ONE_CENT = Decimal("0.01")
ZERO = Decimal("0")


def to_iqd(value: Decimal) -> Decimal:
    """Round to whole dinars, half up."""
    return value.quantize(ONE_DINAR, rounding=ROUND_HALF_UP)


def to_usd(value: Decimal) -> Decimal:
    """Round to cents, half up."""
    return value.quantize(ONE_CENT, rounding=ROUND_HALF_UP)


class LedgerError(Exception):
    """Bad input. The program stops and says why."""


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass
class Invoice:
    id: str
    date: str
    customer: str
    usd: Decimal
    iqd: Decimal = ZERO  # value at the invoice-day rate
    usd_open: Decimal = ZERO  # still unpaid
    iqd_open: Decimal = ZERO  # still unpaid, at the invoice-day rate


@dataclass
class Payment:
    id: str
    date: str
    customer: str
    currency: str
    amount: Decimal
    usd: Decimal = ZERO  # value in USD at the payment-day rate
    iqd: Decimal = ZERO  # value in IQD at the payment-day rate
    usd_open: Decimal = ZERO  # not used yet = customer credit
    iqd_open: Decimal = ZERO
    reversed: bool = False


@dataclass
class Allocation:
    """One part of a payment used to pay one part of an invoice."""

    payment: Payment
    invoice: Invoice
    usd: Decimal
    invoice_iqd: Decimal  # IQD removed from the invoice (invoice-day rate)
    payment_iqd: Decimal  # IQD removed from the payment (payment-day rate)

    @property
    def fx(self) -> Decimal:
        """Realized FX difference. Positive = loss, negative = gain."""
        return self.invoice_iqd - self.payment_iqd


class Entry:
    """A journal entry. Lines on the same account are combined."""

    def __init__(self, date: str, ref: str) -> None:
        self.date = date
        self.ref = ref
        self.net: dict[str, Decimal] = {}  # account -> debit minus credit

    def debit(self, account: str, amount: Decimal) -> None:
        self.net[account] = self.net.get(account, ZERO) + amount

    def credit(self, account: str, amount: Decimal) -> None:
        self.debit(account, -amount)

    def is_empty(self) -> bool:
        return all(value == 0 for value in self.net.values())

    def to_json(self) -> dict:
        debits = [
            {"account": account, "debit": int(value), "credit": 0}
            for account, value in self.net.items()
            if value > 0
        ]
        credits = [
            {"account": account, "debit": 0, "credit": int(-value)}
            for account, value in self.net.items()
            if value < 0
        ]
        return {"date": self.date, "ref": self.ref, "lines": debits + credits}


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------


class Ledger:
    def __init__(self, rates: dict[str, Decimal]) -> None:
        self._rates = rates
        self._rate_days = sorted(rates)
        self.invoices: list[Invoice] = []  # in posting order = oldest first
        self.payments: dict[str, Payment] = {}  # in posting order
        self.allocations: list[Allocation] = []
        self.entries: list[Entry] = []
        self.customers: set[str] = set()

    # ---- rates -----------------------------------------------------------

    def rate_on(self, day: str) -> Decimal:
        """Rate for a day. If the day has no rate, use the latest earlier one."""
        i = bisect.bisect_right(self._rate_days, day)
        if i == 0:
            raise LedgerError(f"no exchange rate on or before {day}")
        return self._rates[self._rate_days[i - 1]]

    # ---- events ----------------------------------------------------------

    def add_invoice(self, inv: Invoice) -> None:
        inv.iqd = to_iqd(inv.usd * self.rate_on(inv.date))
        inv.usd_open, inv.iqd_open = inv.usd, inv.iqd
        self.invoices.append(inv)
        self.customers.add(inv.customer)

        entry = Entry(inv.date, inv.id)
        entry.debit(receivable(inv.customer), inv.iqd)
        entry.credit(REVENUE, inv.iqd)
        self._post(entry)

        # The customer may already hold credit from an earlier overpayment.
        self._apply_credits(inv.customer, inv.date)

    def add_payment(self, pay: Payment) -> None:
        rate = self.rate_on(pay.date)
        if pay.currency == "USD":
            pay.usd = pay.amount
            pay.iqd = to_iqd(pay.amount * rate)
        else:  # IQD: find how many dollars it pays, at the payment-day rate
            pay.iqd = pay.amount
            pay.usd = to_usd(pay.amount / rate)
        pay.usd_open, pay.iqd_open = pay.usd, pay.iqd
        self.payments[pay.id] = pay
        self.customers.add(pay.customer)

        entry = Entry(pay.date, pay.id)
        entry.debit(CASH[pay.currency], pay.iqd)
        entry.credit(receivable(pay.customer), pay.iqd)
        self._apply_credits(pay.customer, pay.date, entry)
        self._post(entry)

    def reverse_payment(self, payment_id: str, day: str) -> None:
        pay = self.payments.get(payment_id)
        if pay is None:
            raise LedgerError(f"reversal of unknown payment {payment_id}")
        if pay.reversed:
            raise LedgerError(f"payment {payment_id} is reversed more than once")

        # Undo the payment at its ORIGINAL amounts, not at today's rate.
        entry = Entry(day, f"REV-{pay.id}")
        entry.debit(receivable(pay.customer), pay.iqd)
        entry.credit(CASH[pay.currency], pay.iqd)

        # Reopen every invoice it paid (also later credit applications)
        # and cancel the FX it created.
        for alloc in [a for a in self.allocations if a.payment is pay]:
            alloc.invoice.usd_open += alloc.usd
            alloc.invoice.iqd_open += alloc.invoice_iqd
            self._book_fx(entry, pay.customer, alloc.fx, reverse=True)
            self.allocations.remove(alloc)

        pay.usd_open = pay.iqd_open = ZERO
        pay.reversed = True
        self._post(entry)

        # Other credit this customer holds can now pay the reopened invoices.
        self._apply_credits(pay.customer, day)

    # ---- matching --------------------------------------------------------

    def _apply_credits(self, customer: str, day: str, entry: Entry | None = None) -> None:
        """Use unused payments to pay open invoices, oldest first on both sides.

        If `entry` is given (a payment), FX lines go into it. Otherwise each
        match gets its own entry, posted only if it has an FX difference.
        """
        while True:
            inv = next(
                (i for i in self.invoices if i.customer == customer and i.usd_open > 0),
                None,
            )
            pay = next(
                (p for p in self.payments.values() if p.customer == customer and p.usd_open > 0),
                None,
            )
            if inv is None or pay is None:
                return
            target = entry if entry is not None else Entry(day, f"APPLY-{pay.id}-{inv.id}")
            self._allocate(pay, inv, target)
            if entry is None:
                self._post(target)

    def _allocate(self, pay: Payment, inv: Invoice, entry: Entry) -> None:
        usd = min(pay.usd_open, inv.usd_open)
        alloc = Allocation(
            payment=pay,
            invoice=inv,
            usd=usd,
            invoice_iqd=self._take(inv, usd),
            payment_iqd=self._take(pay, usd),
        )
        self.allocations.append(alloc)
        self._book_fx(entry, inv.customer, alloc.fx)

    @staticmethod
    def _take(item: Invoice | Payment, usd: Decimal) -> Decimal:
        """Remove `usd` from an open item and return the matching IQD.

        Partial: a proportional share. Full: everything left, so no stray
        dinars stay behind because of rounding.
        """
        if usd == item.usd_open:
            iqd = item.iqd_open
        else:
            iqd = to_iqd(item.iqd_open * usd / item.usd_open)
        item.usd_open -= usd
        item.iqd_open -= iqd
        return iqd

    @staticmethod
    def _book_fx(entry: Entry, customer: str, fx: Decimal, reverse: bool = False) -> None:
        """Clear the IQD difference from the receivable into an FX account.

        A reversal uses the SAME account as the original (a reversed loss
        is a credit to FX Loss, not a gain).
        """
        if fx == 0:
            return
        account = FX_LOSS if fx > 0 else FX_GAIN
        amount = -fx if reverse else fx
        entry.debit(account, amount)
        entry.credit(receivable(customer), amount)

    # ---- output ----------------------------------------------------------

    def _post(self, entry: Entry) -> None:
        if entry.is_empty():
            return
        if sum(entry.net.values(), ZERO) != 0:
            raise RuntimeError(f"entry {entry.ref} does not balance")
        self.entries.append(entry)

    def balance(self, customer: str) -> Decimal:
        account = receivable(customer)
        return sum((e.net.get(account, ZERO) for e in self.entries), ZERO)

    def check(self) -> None:
        """The receivable in the journal must equal open invoices minus unused credit."""
        for customer in self.customers:
            owed = sum(
                (i.iqd_open for i in self.invoices if i.customer == customer), ZERO
            )
            credit = sum(
                (p.iqd_open for p in self.payments.values() if p.customer == customer), ZERO
            )
            if self.balance(customer) != owed - credit:
                raise RuntimeError(f"receivable for {customer} does not match open items")

    def report(self) -> dict:
        self.check()
        return {
            "entries": [e.to_json() for e in self.entries],
            "open_invoices": [
                {"id": i.id, "usd_open": _number(i.usd_open), "iqd_open": int(i.iqd_open)}
                for i in self.invoices
                if i.usd_open > 0
            ],
            "balances": [
                {"customer": c, "iqd": int(self.balance(c))} for c in sorted(self.customers)
            ],
        }


def _number(value: Decimal) -> int | float:
    """Whole numbers as int (300), others as float (12.5)."""
    return int(value) if value == value.to_integral_value() else float(value)


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------


def _items(data: dict, key: str) -> list:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise LedgerError(f'"{key}" must be a list')
    for item in value:
        if not isinstance(item, dict):
            raise LedgerError(f'every item in "{key}" must be an object')
    return value


def _text(record: dict, key: str, what: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LedgerError(f'{what}: missing or empty "{key}"')
    return value


def _day(value, what: str) -> str:
    try:
        ok = isinstance(value, str) and Date.fromisoformat(value).isoformat() == value
    except ValueError:
        ok = False
    if not ok:
        raise LedgerError(f"{what}: bad date {value!r}, expected YYYY-MM-DD")
    return value


def _amount(value, what: str, step: Decimal | None) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise LedgerError(f"{what}: amount must be a number")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise LedgerError(f"{what}: amount must be a number") from None
    if not number.is_finite() or number <= 0:
        raise LedgerError(f"{what}: amount must be positive")
    if step is not None and number != number.quantize(step):
        raise LedgerError(f"{what}: too many decimal places")
    return number


def _parse(data) -> tuple[dict, list[Invoice], list[Payment], list[dict]]:
    if not isinstance(data, dict):
        raise LedgerError("input must be a JSON object")

    raw_rates = data.get("rates")
    if not isinstance(raw_rates, dict) or not raw_rates:
        raise LedgerError('"rates" must be a non-empty object')
    rates = {_day(d, "rates"): _amount(r, f"rate on {d}", None) for d, r in raw_rates.items()}

    invoices = []
    for x in _items(data, "invoices"):
        what = f"invoice {x.get('id', '?')}"
        invoices.append(
            Invoice(
                id=_text(x, "id", what),
                date=_day(x.get("date"), what),
                customer=_text(x, "customer", what),
                usd=_amount(x.get("usd"), what, ONE_CENT),
            )
        )

    payments = []
    for x in _items(data, "payments"):
        what = f"payment {x.get('id', '?')}"
        currency = x.get("currency")
        if currency not in CASH:
            raise LedgerError(f'{what}: currency must be "USD" or "IQD"')
        step = ONE_CENT if currency == "USD" else ONE_DINAR
        payments.append(
            Payment(
                id=_text(x, "id", what),
                date=_day(x.get("date"), what),
                customer=_text(x, "customer", what),
                currency=currency,
                amount=_amount(x.get("amount"), what, step),
            )
        )

    ids = [i.id for i in invoices] + [p.id for p in payments]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise LedgerError(f"duplicate ids: {', '.join(duplicates)}")

    by_id = {p.id: p for p in payments}
    reversals, seen = [], set()
    for x in _items(data, "reversals"):
        pid = _text(x, "payment_id", "reversal")
        day = _day(x.get("date"), f"reversal of {pid}")
        if pid not in by_id:
            raise LedgerError(f"reversal of unknown payment {pid}")
        if pid in seen:
            raise LedgerError(f"payment {pid} is reversed more than once")
        if day < by_id[pid].date:
            raise LedgerError(f"reversal of {pid} is dated before the payment")
        seen.add(pid)
        reversals.append({"payment_id": pid, "date": day})

    return rates, invoices, payments, reversals


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------


def run(data) -> dict:
    """Process the input (a dict loaded from JSON) and return the output dict."""
    rates, invoices, payments, reversals = _parse(data)
    ledger = Ledger(rates)

    # Oldest first. On the same day: invoices, then payments, then reversals.
    # Ties keep the input order.
    events = []
    for n, inv in enumerate(invoices):
        events.append((inv.date, 0, n, lambda inv=inv: ledger.add_invoice(inv)))
    for n, pay in enumerate(payments):
        events.append((pay.date, 1, n, lambda pay=pay: ledger.add_payment(pay)))
    for n, rev in enumerate(reversals):
        events.append(
            (rev["date"], 2, n, lambda rev=rev: ledger.reverse_payment(rev["payment_id"], rev["date"]))
        )
    events.sort(key=lambda event: event[:3])
    for *_, action in events:
        action()

    return ledger.report()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python ledger.py input.json > output.json", file=sys.stderr)
        return 2
    try:
        with open(args[0], encoding="utf-8") as f:
            data = json.load(f, parse_float=Decimal)
        result = run(data)
    except (OSError, json.JSONDecodeError, LedgerError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    # UTF-8 output, so Arabic customer names also work on Windows.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
