"""
crossborder — Cross-Border Shipment Dashboard & Consolidation Planner.

Unifies TIM shipments (ClickUp) and TMS shipments (Moveware) into one
`Shipment` model for the live dashboard and the consolidation engine.

Spec: project doc `tim-tms-cross-border-dashboard-spec.md`.

Modules
  models    — the unified Shipment record, stages, hubs, unit normalization
  clickup   — ClickUp REST v2 reader (TIM) → Shipment
  web       — login-gated Flask blueprint (/crossborder/...)
"""
