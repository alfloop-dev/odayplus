

## Ejected merge route continuation

The failed mandatory requeue can leave an old local snapshot after a successful CAS and failed post-sync read. The advance stage now reloads canonical status before further routing, or stops the tick when unavailable. Expanded existing race cases cover waiting/ejected routes with success, sync failure, transient reload failure and persistent reload failure. See ejected-followup-verification.json and original red/green receipts (2 failures before; 64 selected checks passing after).
