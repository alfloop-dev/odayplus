# ODP-OC-R4-008 Visual Parity

## Compared Surface

- Design package: `r4-20260707-package-6`
- Screen label: `Network 低效重配`
- Design setup: switch to `展店經理`, open `展店與店網`, click `低效重配`
- Product setup: `/operator?ws=network`, click Network tab `低效重配`, API reset to initial rebalance candidate

## Evidence

- Manifest: `docs/evidence/completion/ODP-OC-R4-008/screenshot-manifest.json`
- Desktop product: `docs/evidence/completion/ODP-OC-R4-008/screenshots/product-rebalance-desktop.png`
- Desktop design: `docs/evidence/completion/ODP-OC-R4-008/screenshots/design-rebalance-desktop.png`
- Constrained product: `docs/evidence/completion/ODP-OC-R4-008/screenshots/product-rebalance-constrained.png`
- Constrained design: `docs/evidence/completion/ODP-OC-R4-008/screenshots/design-rebalance-constrained.png`

## Assessment

- Core two-column rebalance workflow is present: store queue, selected store detail, five-step progression, trend bars, primary AVM CTA.
- Product intentionally differs from package 6 static mock by binding to one API seed row and removing mock AVM/NetPlan values until service endpoints produce them.
- Constrained product capture includes the Operator global sticky header overlay because the crop target is below sticky chrome; the rebalance detail itself does not show internal text overlap.
- No unresolved visual blocker for the changed `Network 低效重配` surface.
