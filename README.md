# Sukkar Operations: a custom Odoo 18 ERP for a dessert and gifting business

A full operations layer built on Odoo 18 Community for a dessert and gifting shop in Kuwait.
"Sukkar" is a demo brand: this repository is the cleaned, anonymised version of a module running in production.

**Live demo:** https://erp-demo.95-111-252-47.sslip.io (login `demo` / `Sukkar-Demo-2026`, fake data, resets every night)

## What it does
- **Orders and gifting:** delivery date and time slot, gift message, recipient details, gift invoice and delivery sheet in Arabic and English
- **Kitchen workflow:** every order becomes a production ticket that moves through stages on a kitchen board (Piping, Cooling, Packing, Ready)
- **Delivery and dispatch:** 198 Kuwait areas with delivery fees, driver assignment, cash-on-delivery tracking and the driver's end-of-day cash-up
- **Point of sale:** till with delivery orders, KNET and cash, bilingual receipts
- **People:** attendance machines read automatically, overtime under Kuwait labour law (×1.25 / ×1.5 / ×2), payroll with WPS bank and cash split
- **Books:** expense categories, monthly close, profit and loss and balance sheet through free OCA modules (no Enterprise licence)

## Install
Odoo 18 Community plus these OCA repositories on the addons path: `payroll`, `account-financial-tools`, `account-financial-reporting`, `mis-builder`, `reporting-engine`, `bank-statement-import`, `account-reconcile`.

```
odoo -d mydb -i sukkar --without-demo=all
```

Tests: `odoo -d testdb -i sukkar --test-tags sukkar --stop-after-init`

## About
Built by Ahmed S., Shopify and Odoo developer. Fixed-price projects: https://www.upwork.com/freelancers/~01437ca49ef98616c4

Licence: LGPL-3
