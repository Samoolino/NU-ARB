# Manual reconciliation runbook

A `RECONCILIATION_REQUIRED` execution is a trading halt, not a retry signal. Do not delete the SQLite journal or edit execution states directly.

## Required operator procedure

1. Stop the bot and confirm no second process can submit orders against the same account/journal.
2. Open the execution snapshot from the ledger (`get_execution(execution_id)`) and record every leg's client order ID, exchange order ID, requested size, fill size, status, and stored response.
3. Query the venue's open, closed, and canceled orders plus user trade history for each client/order ID. Treat timeouts and missing history as unresolved, not as proof that no order was accepted.
4. Compare exchange free/locked balances and trade fills against the journal. Identify residual assets and fees, including fees charged in a third token. Obtain a verified market valuation for any third-token fee before reporting realized PnL.
5. If necessary, manually hedge or transfer residual inventory under the operator's separate risk approval. Record fills, fees, balances, and transaction/order IDs in an incident ticket.
6. Only after the venue state and residual inventory have been reconciled, call `resolve_manual(..., operator=..., evidence=..., confirm=True)`. The evidence field should reference the incident ticket and summarize what was checked.
7. Restart in paper mode first and confirm the unresolved list is empty. Never use manual resolution to bypass missing evidence or to declare an execution profitable.

`resolve_manual` is an auditable human acknowledgement only. It does not query an exchange, verify the supplied evidence, move assets, or establish that a hedge succeeded. Keep the SQLite journal and incident evidence for audit.

## Example

Use the same SQLite journal path configured for the halted executor:

```python
from arbx.order_reconciliation import OrderLedger

ledger = OrderLedger("tri_orders.sqlite3")
for execution in ledger.unresolved():
    print(ledger.get_execution(execution["execution_id"]))
# After the independent reconciliation steps above:
ledger.resolve_manual(
    execution_id,
    operator="operator-id",
    evidence="Incident INC-123: venue orders, trade fills, balances, fees, and residual inventory reconciled.",
    confirm=True,
)
ledger.close()
```

Do not automate this acknowledgement as part of the trading loop.
