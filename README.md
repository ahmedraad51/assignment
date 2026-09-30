# Dual-currency receivables

A small Python program that keeps a customer ledger where:

- the books are in **IQD**,
- invoices are in **USD**,
- customers pay in **USD or IQD**,
- and the exchange rate changes from day to day.

It reads the invoices, payments and reversals from a JSON file and prints the journal entries, the open invoices and each customer's balance.

## How to run

Python 3.8 or newer. Nothing to install.

```
python ledger.py input.json > output.json
```

Leave out `> output.json` to see the result on screen.

Run the tests:

```
python -m unittest discover -s tests -v
```

If the input is broken (a reversal for a payment that doesn't exist, a date before the first rate, and so on), the program prints an error and stops. It never writes half an output.

> **Windows:** in Windows PowerShell 5, `>` saves the file as UTF-16. Run the command from `cmd` instead, or use PowerShell 7.

## Files

| File | What it is |
|---|---|
| `ledger.py` | The program |
| `tests/test_ledger.py` | 34 tests |
| `input.json` | The sample input from the brief |
| `output.json` | The program's result for the sample |
| `AI_NOTES.md` | How I used AI |

## Input and output

**Input** (`input.json`) has four parts:

- `rates`: IQD per 1 USD, by date
- `invoices`: `id`, `date`, `customer`, `usd`
- `payments`: `id`, `date`, `customer`, `currency` (`USD` or `IQD`), `amount`
- `reversals`: `payment_id`, `date`

**Output** has three parts. All money in it is whole IQD, except `usd_open`.

- `entries`: the journal entries. Each has a `date`, a `ref` and `lines` (account, debit, credit).
- `open_invoices`: invoices that are not fully paid, with what is still open in USD and in IQD.
- `balances`: what each customer owes in IQD.

## How the accounting works

The books are in IQD, so every USD amount has to become IQD at some rate. The whole task is about **which rate to use, and when**.

### 1. Invoices use the invoice-day rate

INV1 is 1,000 USD on 09-01 at 1480, so Ali Trading owes **1,480,000 IQD** in our books.

### 2. Payments use the payment-day rate

That's what the money is actually worth when it arrives. PAY1 is 1,200 USD on 09-20 at 1470 = **1,764,000 IQD**.

### 3. The difference is a realized FX gain or loss

The customer paid every dollar it owed, so the full booked amount is cleared from its account and the difference goes to an FX account. It's not revenue, and it's not the customer's debt.

For PAY1:

| | IQD |
|---|---|
| Booked amount cleared (all of INV1 + 200 USD of INV2) | 1,780,000 |
| Cash received | 1,764,000 |
| **Realized FX loss** | **16,000** |

### 4. IQD payments are converted at the payment-day rate too

PAY2 is 447,000 IQD / 1490 = **300 USD**, which closes INV2.
With the invoice rate (1500) it would be 298 USD, and INV2 would wrongly stay open by 2 dollars.

### 5. A reversal is the exact mirror of the payment

The payment never happened, so there's nothing to convert at the 09-28 rate. The program posts the PAY2 entry backwards with its original numbers: the 3,000 loss goes away, and INV2 is open again at 300 USD / 450,000 IQD.

### 6. Open invoices are not revalued

Open invoices stay at their booked IQD value, because the brief doesn't give a closing date. (At 1465, INV2 would be worth 439,500, an unrealized loss of 10,500.)

The invoice rate isn't stored on its own, but you can get it back from the two amounts: 450,000 / 300 = 1500.

## The sample, step by step

| Date | Event | Rate | In IQD | FX | Still open after |
|---|---|---|---|---|---|
| 09-01 | INV1: 1,000 USD | 1480 | 1,480,000 | | INV1 1,000 USD |
| 09-10 | INV2: 500 USD | 1500 | 750,000 | | INV1 1,000 USD, INV2 500 USD |
| 09-20 | PAY1: 1,200 USD | 1470 | 1,764,000 | loss 16,000 | INV2 300 USD |
| 09-25 | PAY2: 447,000 IQD (= 300 USD) | 1490 | 447,000 | loss 3,000 | nothing |
| 09-28 | PAY2 reversed | not used | -447,000 | loss 3,000 cancelled | INV2 300 USD |

**Final result:**

- INV2 is open: 300 USD / 450,000 IQD
- Ali Trading owes 450,000 IQD
- Total FX loss: 16,000 IQD

The full journal entries are in `output.json`.

## Assumptions

- A rate is IQD per 1 USD.
- A day without a rate uses the last rate before it. A date before the first rate is an error.
- Events run in date order. On the same day: invoices, then payments, then reversals.
- A payment pays the customer's oldest open invoice first.
- If a customer pays too much, the extra stays as their credit (a negative balance) and pays their next invoice. The FX on that goes into an `APPLY-<payment>-<invoice>` entry.
- A reversal cancels the whole payment, including any of its credit that was already used on a later invoice. After that, any other credit the customer has pays the reopened invoices.
- Rounding is half up: whole dinars for IQD, cents for USD. All money is `Decimal`, never `float`.
- In `balances`, positive means the customer owes us, and negative means we're holding their credit.

## Accounts used

- `Accounts Receivable - <customer>` (one per customer)
- `Sales Revenue`
- `Cash - USD`
- `Cash - IQD`
- `Realized FX Gain`
- `Realized FX Loss`
