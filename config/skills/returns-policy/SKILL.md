---
name: returns-policy
description: How a return works at Inkwell Books, step by step, and what each refund reason means.
---
A customer can return a book within 30 days of delivery.

1. Request: the customer asks for a return from their order page, or a member of staff opens one with `return_open`.
2. Label: the back office e-mails a prepaid shipping label; the return shows as «label sent».
3. Receive: when the parcel arrives, staff check the book and mark the return as received.
4. Refund: `order_refund` with one of the reasons «Damaged in transit», «Wrong item sent», «Changed their mind» or
   «Arrived too late». Damaged and wrong items are refunded in full, shipping included; a change of mind is refunded
   without the original shipping.

A return can be closed only once its refund is done. Refunds always ask the user to confirm.
