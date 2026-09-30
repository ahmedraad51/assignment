I used claude to:
**Note:** Due to limited time, I used AI to help with the writing, but everything stated below is fully represents me.

**To understand the brief** because of that the task is mostly accounting, I asked claude to explain the task: what a receivable is, why exchange rates create FX gains and losses, and what a payment reversal is.
**To write the code, tests and read me** claude wrote them. Then I ran the program, went through the output entry by entry, and kept asking about each line until I could explain it myself. For example, why the reversal uses the original amounts and not the 09-28 rate, and why the IQD payment is converted at the payment-day rate.

I also asked why the invoice rate isn't saved anywhere. It turned out it's kept through the two amounts (450,000 / 300 = 1500), so I added that to the README.


## Where it was wrong
First I checked it myself, I went through the operations one by one, and checked each entry against the numbers.I paid the most attention to the FX differences-especially the reversal-, because I knew that's where the AI is most likely to make a mistake. I didn't find anything wrong.

Then I asked an AI agent to review the code. It found three cases I missed:

- A name with an extra space ("Ali Trading ") is treated as a different customer, and no error is shown.
- If a customer pays too much in IQD, the extra is kept in dollars. When it later pays another invoice, the program books an FX gain or loss on dinars, which shouldn't happen.
- A very small IQD payment (like 5 IQD) becomes 0.00 USD, so it never pays anything.

I didn't fix it, because They only happen with inputs that aren't in the assigment, and the sample still gives the right result. But I understood all of them and I know why they need attention.

