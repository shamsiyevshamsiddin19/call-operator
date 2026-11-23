"""Audit jurnaliga yozish (CQS — Command/Write qatlami).

Bu modul boshqa barcha `services` qatlamlari tomonidan chaqiriladi, shuning uchun
u hech qanday biznes modulga bog'lanmaydi (faqat `accounts.actors` va o'z modeliga).
Teskari yo'nalish — `audit` dan `orders` ga murojaat — **taqiqlanadi**, aks holda
aylanma bog'liqlik paydo bo'ladi.

**Xavfsizlik hodisalari ikki joyga yoziladi:** ma'lumotlar bazasiga (tahlil va
hisobot uchun) va `call_operator.security` logeriga (real vaqtda ogohlantirish
uchun). Baza yiqilsa ham log qoladi, log yo'qolsa ham baza qoladi —
konstitutsiyaning 7-bandi buni talab qiladi.
"""

import logging
from typing import Any

from apps.accounts.actors import Actor
from apps.audit.models import AuditEvent, AuditEventType, AuditSeverity

logger = logging.getLogger("call_operator")
security_logger = logging.getLogger("call_operator.security")


def record_event(
    actor: Actor,
    event_type: str,
    summary: str,
    *,
    severity: str = AuditSeverity.INFO,
    customer_ref: int | None = None,
    order_ref: int | None = None,
    payload: dict[str, Any] | None = None,
    source_ip: str | None = None,
) -> AuditEvent:
    """Audit jurnaliga bitta yozuv qo'shadi.

    Tranzaksiya bilan o'ralmaydi: chaqiruvchi `services` funksiyasi allaqachon
    `@transaction.atomic` ichida ishlaydi, shuning uchun biznes amali rollback
    bo'lsa audit yozuvi ham rollback bo'ladi — bu to'g'ri xatti-harakat,
    chunki bo'lmagan amal haqida yozuv qolmasligi kerak.

    Args:
        actor: amalni bajargan tomon.
        event_type: `AuditEventType` qiymati.
        summary: bir qatorli bayon (odam o'qishi uchun).
        severity: `AuditSeverity`. `SECURITY` bo'lsa xavfsizlik logeriga ham tushadi.
        customer_ref, order_ref: bog'lanish (FK emas — modeldagi izohga qarang).
        payload: qo'shimcha tafsilot. **Shaxsiy ma'lumot to'liq yozilmaydi**:
            telefon raqami `mask_phone()` dan o'tkaziladi, transkript matni
            va to'liq manzil bu yerga tushmaydi.
        source_ip: xavfsizlik hodisalari uchun manba IP.
    """
    event = AuditEvent.objects.create(
        event_type=event_type,
        severity=severity,
        actor_label=actor.label,
        summary=summary,
        payload=payload or {},
        customer_ref=customer_ref,
        order_ref=order_ref,
        conversation_ref=actor.conversation_id,
        trace_id=actor.trace_id,
        source_ip=source_ip,
    )

    log_fields = {
        "event_type": event_type,
        "actor": actor.label,
        "trace_id": actor.trace_id,
        "customer_ref": customer_ref,
        "order_ref": order_ref,
    }

    if severity == AuditSeverity.SECURITY:
        security_logger.warning("%s | %s", event_type, summary, extra=log_fields)
    elif severity == AuditSeverity.WARNING:
        logger.warning("%s | %s", event_type, summary, extra=log_fields)
    else:
        logger.info("%s | %s", event_type, summary, extra=log_fields)

    return event


def record_security_violation(
    actor: Actor,
    event_type: str,
    summary: str,
    *,
    customer_ref: int | None = None,
    payload: dict[str, Any] | None = None,
    source_ip: str | None = None,
) -> AuditEvent:
    """Xavfsizlik buzilishi urinishini qayd etadi.

    `record_event(severity=SECURITY)` ustidagi qulay qobiq (Facade — GoF):
    chaqiruvchi kod har safar `severity` ni yozishni esdan chiqarmasligi uchun.
    Ruxsatsiz kirish, chastota chegarasi, tasdiqlanmagan shaxsning ma'lumot
    so'rashi va zaxiradan ortiq miqdor so'rovlari shu funksiya orqali o'tadi.
    """
    return record_event(
        actor,
        event_type,
        summary,
        severity=AuditSeverity.SECURITY,
        customer_ref=customer_ref,
        payload=payload,
        source_ip=source_ip,
    )


def record_permission_denied(actor: Actor, action: str, *, reason: str = "") -> AuditEvent:
    """Ruxsat berilmagan amal urinishi (STRIDE-E — Elevation of Privilege).

    Konstitutsiya 7-bandi: foydalanuvchiga shunchaki 403 qaytarish yetarli emas,
    urinish `user_id` va vaqt tamg'asi bilan xavfsizlik jurnaliga tushishi shart.
    """
    return record_security_violation(
        actor,
        AuditEventType.PERMISSION_DENIED,
        f"Ruxsat berilmadi: {action}",
        payload={"action": action, "reason": reason, "actor_kind": actor.kind},
    )
