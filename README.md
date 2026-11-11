
## Settings (Settings page, one block per app)

- Property: deposit refund days, biggest photo (MB), notice days.
- Billing: grace days, invoice days before rent is due, first month rent (by days or full), water leak warning (%), and the M-Pesa STK Push block (on/off switch, Daraja details, Test connection, callback address). Also reachable from Billing > Settings (administrators).
- Maintenance: ask approval above (KES), days to pay a repair charge.
- Today: none.

Building and lease settings are defaults for new records. Existing buildings keep their own value.

## M-Pesa STK Push (Billing)

- **On** (Billing > Settings > M-Pesa STK Push): the tenant opens an invoice, presses Pay now with M-Pesa, enters the PIN on the phone, and Safaricom's answer approves the payment and makes the receipt by itself. The tenant home page shows Pay with M-Pesa and hides the manual payment details.
- **Off**: nothing changes for tenants except that the manual payment details and "I have paid: send proof" are shown again, and staff approve proofs in Payments to check.
- Manual approval always stays. If STK money comes in but cannot be matched (for example the invoice was paid meanwhile), it becomes a normal proof in Payments to check and the STK payment shows "Paid, needs checking".
- Staff list: Billing > Payments > M-Pesa STK payments. "Ask Safaricom what happened" covers a result that never arrived.
- Safaricom needs a public https address to send the result to. Set it in the settings if the system web address is not public.
- Not done yet: one set of Daraja details for all buildings (no per-building paybill), partial amounts from the tenant, refunds.
