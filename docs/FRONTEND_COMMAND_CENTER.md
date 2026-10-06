# NU-ARB Frontend Command Center

The production frontend is the operator surface for the native VenueAdapter execution architecture.

## UI contract
- $3 starter capital for live mode.
- Realized profit compounds the next allocation.
- Realized live loss halts new engagements.
- No averaging down / martingale.
- Live eligibility is fail-closed and evidence based.
- The UI does not infer live eligibility from a venue catalog, installed SDK, or adapter declaration.
- Live cross-exchange execution requires two or more independently eligible venues.
- Opportunity display remains an estimate; positive displayed PnL is not a guarantee of realized profit.

## Main surfaces
1. Market scanner and depth-aware cross-venue opportunity table.
2. Capital policy and live guard summary.
3. Account authentication.
4. Credentialed exchange verification and evidence.
5. Multi-venue engine selection.
6. Paper/live engine control.
7. Verified capital sources.
8. Durable execution journal.

## Verification
PowerShell UI smoke:
.\scripts\upgrade-all.ps1 -Stage ui -RunServerSmoke

Complete core + frontend progression:
.\scripts\upgrade-all.ps1 -Stage all

Live remains an explicit operator action and should only be armed after fresh credentialed venue certification and live preflight.
