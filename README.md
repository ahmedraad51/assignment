Dual-currency receivables

Books in IQD, invoices in USD, payments in USD or IQD.

Running it

Python 3.8 or newer. Nothing to install.

python ledger.py input.json > output.json

Leave out > output.json if you just want to see the result on screen.

Tests:

python -m unittest discover -s tests -v

If the input is broken (a reversal for a payment that doesn't exist, a date before the first rate, and so on), the program prints an error and stops. It never writes half an output.

One Windows thing: in Windows PowerShell 5, > saves the file as UTF-16. Run the command from cmd instead, or use PowerShell 7.

What's in the folder
ledger.py - the program
tests/test_ledger.py - 34 tests
input.json, output.json - the sample from the brief and its result
AI_NOTES.md - how I used AI
How I handled the accounting

The books are in IQD, so every USD amount has to become IQD at some rate. The whole task is really about which rate to use, and when.

Invoices use the rate of the invoice day. INV1 is 1,000 USD at 1480, so in our books Ali Trading owes 1,480,000 IQD.

Payments use the rate of the payment day, because that's what the money is actually worth when it arrives. PAY1 is 1,200 USD at 1470 = 1,764,000 IQD.

The gap between the two is a realized FX gain or loss. The customer paid every dollar he owed, so I clear the full booked amount from his account and put the difference in an FX account. It's not revenue, and it's not the customer's debt. For PAY1 the booked amount is 1,780,000 (all of INV1 plus 200 USD of INV2), we received 1,764,000, so the loss is 16,000.

IQD payments are converted to USD at the payment-day rate too. PAY2 is 447,000 / 1490 = 300 USD, which closes INV2. With the invoice rate (1500) you'd get 298 USD and INV2 would stay open by 2 dollars, which is wrong.

A reversal undoes the payment with its original numbers. The payment never happened, so there's nothing to convert at the 09-28 rate. I post the exact mirror of the PAY2 entry, the 3,000 loss goes away, and INV2 is open again at 300 USD / 450,000 IQD.

Open invoices stay at their booked IQD value. I didn't revalue them at the latest rate because the brief doesn't give a closing date. (At 1465, INV2 would be worth 439,500, an unrealized loss of 10,500.) The invoice rate isn't stored on its own, but you can get it back from the two amounts: 450,000 / 300 = 1500.

Final result for the sample: INV2 is open with 300 USD / 450,000 IQD, Ali Trading owes 450,000 IQD, and the total FX loss is 16,000.

Assumptions
A rate is IQD per 1 USD.
A day without a rate uses the last rate before it. A date before the first rate is an error.
Events run in date order. On the same day: invoices, then payments, then reversals.
A payment pays the customer's oldest open invoice first.
If a customer pays too much, the extra stays as their credit (negative balance) and pays their next invoice. The FX on that goes into an APPLY-<payment>-<invoice> entry.
A reversal cancels the whole payment, including any of its credit that was already used on a later invoice.
Rounding is half up: whole dinars for IQD, cents for USD. All money is Decimal, never float.
Every customer has their own receivable account, Accounts Receivable - <name>.
In balances, positive means the customer owes us, negative means we're holding their credit.

Accounts used: Accounts Receivable - <customer>, Sales Revenue, Cash - USD, Cash - IQD, Realized FX Gain, Realized FX Loss.