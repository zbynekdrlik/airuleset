# Reading a client Discuss message — attachments: incident & history (#1098)

Incident rationale relocated VERBATIM out of the injected `read-with-attachments.md` so the pointer fits under the #745 three-way co-fire budget (`MAX_TOTAL=14000`, #1098) — nothing condensed; this file has NO situational-trigger row (never auto-injected).

Incident that created this: an odoo-erp stream read a client's Discuss reply
(mail.message **1742799**) via the API WITHOUT `attachment_ids`, interpreted
the request from the bare text alone, and shipped odoo-erp #5162 with the
wrong/incomplete interpretation — the client had attached a screenshot
(ir.attachment **13204**) circling the EXACT UI element ("aj tu" = the form's
statusbar) that the text alone did not make clear. Corrected only after the
owner asked about the image (odoo-erp #5214). airuleset #709, 2026-08-25/26.
