"""Tests for ledger.py.

Run from the project folder:
    python -m unittest discover -s tests -v
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ledger import LedgerError, run  # noqa: E402

AR = "Accounts Receivable - Ali Trading"
CASH_USD = "Cash - USD"
CASH_IQD = "Cash - IQD"
REVENUE = "Sales Revenue"
LOSS = "Realized FX Loss"
GAIN = "Realized FX Gain"

RATES = {
    "2026-09-01": 1480,
    "2026-09-10": 1500,
    "2026-09-20": 1470,
    "2026-09-25": 1490,
    "2026-09-28": 1465,
}


# ---- helpers ---------------------------------------------------------------


def book(invoices=(), payments=(), reversals=(), rates=None):
    return run(
        {
            "rates": rates or RATES,
            "invoices": list(invoices),
            "payments": list(payments),
            "reversals": list(reversals),
        }
    )


def inv(id, date, usd, customer="Ali Trading"):
    return {"id": id, "date": date, "customer": customer, "usd": usd}


def pay(id, date, amount, currency="USD", customer="Ali Trading"):
    return {"id": id, "date": date, "customer": customer, "currency": currency, "amount": amount}


def rev(payment_id, date):
    return {"payment_id": payment_id, "date": date}


def lines(result, ref):
    """An entry as {account: debit minus credit}, easy to compare."""
    found = [e for e in result["entries"] if e["ref"] == ref]
    assert len(found) == 1, f"expected one entry {ref}, found {len(found)}"
    return {line["account"]: line["debit"] - line["credit"] for line in found[0]["lines"]}


def refs(result):
    return [e["ref"] for e in result["entries"]]


def balance(result, customer="Ali Trading"):
    return next(b["iqd"] for b in result["balances"] if b["customer"] == customer)


# ---- the sample input from the brief ---------------------------------------

EXPECTED_ENTRIES = [
    {"date": "2026-09-01", "ref": "INV1", "lines": [
        {"account": AR, "debit": 1480000, "credit": 0},
        {"account": REVENUE, "debit": 0, "credit": 1480000},
    ]},
    {"date": "2026-09-10", "ref": "INV2", "lines": [
        {"account": AR, "debit": 750000, "credit": 0},
        {"account": REVENUE, "debit": 0, "credit": 750000},
    ]},
    {"date": "2026-09-20", "ref": "PAY1", "lines": [
        {"account": CASH_USD, "debit": 1764000, "credit": 0},
        {"account": LOSS, "debit": 16000, "credit": 0},
        {"account": AR, "debit": 0, "credit": 1780000},
    ]},
    {"date": "2026-09-25", "ref": "PAY2", "lines": [
        {"account": CASH_IQD, "debit": 447000, "credit": 0},
        {"account": LOSS, "debit": 3000, "credit": 0},
        {"account": AR, "debit": 0, "credit": 450000},
    ]},
    {"date": "2026-09-28", "ref": "REV-PAY2", "lines": [
        {"account": AR, "debit": 450000, "credit": 0},
        {"account": CASH_IQD, "debit": 0, "credit": 447000},
        {"account": LOSS, "debit": 0, "credit": 3000},
    ]},
]


class SampleInputTest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "input.json").read_text(encoding="utf-8"))
        self.result = run(self.data)

    def test_entries(self):
        self.assertEqual(self.result["entries"], EXPECTED_ENTRIES)

    def test_open_invoices(self):
        self.assertEqual(
            self.result["open_invoices"], [{"id": "INV2", "usd_open": 300, "iqd_open": 450000}]
        )

    def test_balances(self):
        self.assertEqual(self.result["balances"], [{"customer": "Ali Trading", "iqd": 450000}])

    def test_output_has_exactly_three_keys(self):
        self.assertEqual(set(self.result), {"entries", "open_invoices", "balances"})

    def test_command_line(self):
        done = subprocess.run(
            [sys.executable, str(ROOT / "ledger.py"), str(ROOT / "input.json")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        self.assertEqual(json.loads(done.stdout), self.result)

    def test_output_json_file_is_up_to_date(self):
        saved = json.loads((ROOT / "output.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, self.result)


# ---- accounting rules ------------------------------------------------------


class FxTest(unittest.TestCase):
    def test_gain_when_rate_goes_up(self):
        # Booked at 1480 = 148,000. Paid at 1500 = 150,000. Gain 2,000.
        result = book([inv("I1", "2026-09-01", 100)], [pay("P1", "2026-09-10", 100)])
        self.assertEqual(lines(result, "P1"), {CASH_USD: 150000, AR: -148000, GAIN: -2000})
        self.assertEqual(balance(result), 0)

    def test_loss_when_rate_goes_down(self):
        # Booked at 1500 = 150,000. Paid at 1470 = 147,000. Loss 3,000.
        result = book([inv("I1", "2026-09-10", 100)], [pay("P1", "2026-09-20", 100)])
        self.assertEqual(lines(result, "P1"), {CASH_USD: 147000, LOSS: 3000, AR: -150000})
        self.assertEqual(balance(result), 0)

    def test_no_fx_when_rate_is_the_same(self):
        result = book([inv("I1", "2026-09-10", 100)], [pay("P1", "2026-09-10", 100)])
        self.assertEqual(lines(result, "P1"), {CASH_USD: 150000, AR: -150000})

    def test_iqd_payment_uses_the_payment_day_rate(self):
        # 447,000 / 1490 = 300 USD, so the invoice is fully paid.
        # (At the invoice rate 1500 it would only be 298 USD.)
        result = book([inv("I1", "2026-09-10", 300)], [pay("P1", "2026-09-25", 447000, "IQD")])
        self.assertEqual(result["open_invoices"], [])
        self.assertEqual(lines(result, "P1"), {CASH_IQD: 447000, LOSS: 3000, AR: -450000})


class AllocationTest(unittest.TestCase):
    def test_partial_payment_keeps_a_proportional_iqd_amount(self):
        # 500 USD at 1500 = 750,000. Pay 200 USD -> 300 USD / 450,000 left.
        result = book([inv("I1", "2026-09-10", 500)], [pay("P1", "2026-09-10", 200)])
        self.assertEqual(result["open_invoices"], [{"id": "I1", "usd_open": 300, "iqd_open": 450000}])
        self.assertEqual(balance(result), 450000)

    def test_payment_pays_the_oldest_invoice_first(self):
        result = book(
            [inv("I2", "2026-09-10", 100), inv("I1", "2026-09-01", 100)],
            [pay("P1", "2026-09-20", 150)],
        )
        # I1 is older (even though it comes second in the input).
        self.assertEqual(result["open_invoices"], [{"id": "I2", "usd_open": 50, "iqd_open": 75000}])

    def test_invoice_and_payment_on_the_same_day(self):
        result = book([inv("I1", "2026-09-20", 100)], [pay("P1", "2026-09-20", 100)])
        self.assertEqual(result["open_invoices"], [])
        self.assertEqual(balance(result), 0)

    def test_customers_are_kept_apart(self):
        result = book(
            [inv("I1", "2026-09-01", 100, "Ali Trading"), inv("I2", "2026-09-01", 100, "Noor Co")],
            [pay("P1", "2026-09-01", 100, customer="Noor Co")],
        )
        self.assertEqual([i["id"] for i in result["open_invoices"]], ["I1"])
        self.assertEqual(
            result["balances"],
            [{"customer": "Ali Trading", "iqd": 148000}, {"customer": "Noor Co", "iqd": 0}],
        )


class CreditTest(unittest.TestCase):
    def test_overpayment_becomes_customer_credit(self):
        # Pay 150 USD at 1500 on a 100 USD invoice -> 50 USD credit = 75,000.
        result = book([inv("I1", "2026-09-01", 100)], [pay("P1", "2026-09-10", 150)])
        self.assertEqual(result["open_invoices"], [])
        self.assertEqual(balance(result), -75000)

    def test_payment_without_invoices_is_all_credit(self):
        result = book(payments=[pay("P1", "2026-09-10", 100, customer="New Co")])
        self.assertEqual(result["balances"], [{"customer": "New Co", "iqd": -150000}])

    def test_credit_pays_a_later_invoice_with_fx(self):
        # 50 USD credit at 1470 = 73,500. New invoice 50 USD at 1490 = 74,500.
        result = book(
            [inv("I1", "2026-09-01", 100), inv("I2", "2026-09-25", 50)],
            [pay("P1", "2026-09-20", 150)],
        )
        self.assertEqual(lines(result, "APPLY-P1-I2"), {LOSS: 1000, AR: -1000})
        self.assertEqual(result["open_invoices"], [])
        self.assertEqual(balance(result), 0)


class ReversalTest(unittest.TestCase):
    def test_reversal_mirrors_the_payment_exactly(self):
        result = book(
            [inv("I1", "2026-09-10", 300)],
            [pay("P1", "2026-09-25", 447000, "IQD")],
            [rev("P1", "2026-09-28")],
        )
        original = lines(result, "P1")
        reversal = lines(result, "REV-P1")
        self.assertEqual(reversal, {account: -value for account, value in original.items()})
        # Not re-valued at the reversal-day rate (1465).
        self.assertEqual(result["open_invoices"], [{"id": "I1", "usd_open": 300, "iqd_open": 450000}])

    def test_reversal_also_undoes_later_credit_use(self):
        # P1 pays I1 and leaves credit, the credit pays I2, then P1 bounces.
        result = book(
            [inv("I1", "2026-09-01", 100), inv("I2", "2026-09-25", 50)],
            [pay("P1", "2026-09-20", 150)],
            [rev("P1", "2026-09-28")],
        )
        self.assertEqual(
            result["open_invoices"],
            [
                {"id": "I1", "usd_open": 100, "iqd_open": 148000},
                {"id": "I2", "usd_open": 50, "iqd_open": 74500},
            ],
        )
        # FX from both uses is cancelled: 1,000 (on I1) + 1,000 (on I2).
        self.assertEqual(lines(result, "REV-P1"), {AR: 222500, CASH_USD: -220500, LOSS: -2000})
        self.assertEqual(balance(result), 148000 + 74500)

    def test_reversal_lets_other_credit_pay_the_reopened_invoice(self):
        result = book(
            [inv("I1", "2026-09-01", 100)],
            [pay("P1", "2026-09-10", 100), pay("P2", "2026-09-20", 100)],
            [rev("P1", "2026-09-25")],
        )
        # P2 (147,000) now pays I1 (148,000): loss 1,000.
        self.assertEqual(refs(result), ["I1", "P1", "P2", "REV-P1", "APPLY-P2-I1"])
        self.assertEqual(lines(result, "REV-P1"), {AR: 148000, GAIN: 2000, CASH_USD: -150000})
        self.assertEqual(lines(result, "APPLY-P2-I1"), {LOSS: 1000, AR: -1000})
        self.assertEqual(result["open_invoices"], [])
        self.assertEqual(balance(result), 0)


class RateTest(unittest.TestCase):
    def test_day_without_a_rate_uses_the_latest_earlier_rate(self):
        # 2026-09-15 has no rate -> use 2026-09-10 (1500).
        result = book([inv("I1", "2026-09-01", 100)], [pay("P1", "2026-09-15", 100)])
        self.assertEqual(lines(result, "P1"), {CASH_USD: 150000, AR: -148000, GAIN: -2000})

    def test_day_before_the_first_rate_is_an_error(self):
        with self.assertRaises(LedgerError):
            book([inv("I1", "2026-08-31", 100)])

    def test_half_dinars_round_up(self):
        # 2.50 x 1481 = 3,702.5 -> 3,703 (Python's round() would give 3,702)
        result = book([inv("I1", "2026-09-01", 2.5)], rates={"2026-09-01": 1481})
        self.assertEqual(lines(result, "I1"), {AR: 3703, REVENUE: -3703})

    def test_decimal_rates_work(self):
        # 33.33 x 1480.5 = 49,345.065 -> 49,345
        result = book([inv("I1", "2026-09-01", 33.33)], rates={"2026-09-01": 1480.5})
        self.assertEqual(lines(result, "I1"), {AR: 49345, REVENUE: -49345})


class EveryEntryBalancesTest(unittest.TestCase):
    def test_debits_equal_credits(self):
        result = book(
            [inv("I1", "2026-09-01", 1000), inv("I2", "2026-09-10", 333.33), inv("I3", "2026-09-25", 77.77)],
            [
                pay("P1", "2026-09-10", 500),
                pay("P2", "2026-09-15", 1000003, "IQD"),
                pay("P3", "2026-09-20", 10),
            ],
            [rev("P1", "2026-09-28")],
        )
        for entry in result["entries"]:
            debit = sum(line["debit"] for line in entry["lines"])
            credit = sum(line["credit"] for line in entry["lines"])
            self.assertEqual(debit, credit, entry["ref"])


# ---- bad input -------------------------------------------------------------


class BadInputTest(unittest.TestCase):
    def assertFails(self, **kwargs):
        with self.assertRaises(LedgerError):
            book(**kwargs)

    def test_reversal_of_unknown_payment(self):
        self.assertFails(reversals=[rev("NOPE", "2026-09-10")])

    def test_payment_reversed_twice(self):
        self.assertFails(
            payments=[pay("P1", "2026-09-10", 100)],
            reversals=[rev("P1", "2026-09-20"), rev("P1", "2026-09-25")],
        )

    def test_reversal_before_the_payment(self):
        self.assertFails(payments=[pay("P1", "2026-09-20", 100)], reversals=[rev("P1", "2026-09-10")])

    def test_unknown_currency(self):
        self.assertFails(payments=[pay("P1", "2026-09-10", 100, "EUR")])

    def test_negative_amount(self):
        self.assertFails(invoices=[inv("I1", "2026-09-10", -5)])

    def test_iqd_with_fractions(self):
        self.assertFails(payments=[pay("P1", "2026-09-10", 100.5, "IQD")])

    def test_duplicate_ids(self):
        self.assertFails(invoices=[inv("X", "2026-09-10", 1)], payments=[pay("X", "2026-09-10", 1)])

    def test_bad_date(self):
        self.assertFails(invoices=[inv("I1", "10/09/2026", 100)])

    def test_bad_input_on_the_command_line(self):
        done = subprocess.run(
            [sys.executable, str(ROOT / "ledger.py"), str(ROOT / "missing.json")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn("error:", done.stderr)


if __name__ == "__main__":
    unittest.main()
