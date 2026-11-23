"""Mijoz va identity amallari (CQS — Command/Write qatlami).

**Poyga holati bu modulda ham bor.** Bir mijoz ikki kanaldan bir vaqtda
murojaat qilsa (telefon qiladi va shu payt Telegram'da yozadi), ikki parallel
tranzaksiya bir xil mijoz uchun ikkita `Customer` yozuvi yaratishga urinadi.

Himoya: `UniqueConstraint(channel, external_id)` va `get_or_create`. Ikkinchi
tranzaksiya `IntegrityError` oladi va mavjud yozuvni o'qib oladi — natijada
bitta mijoz bitta yozuvga ega bo'ladi.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.actors import Actor
from apps.audit import services as audit
from apps.audit.models import AuditEventType
from apps.common.constants import ChannelKind
from apps.common.phone import mask_phone, normalize_phone
from apps.customers.models import Customer, CustomerIdentity


@transaction.atomic
def resolve_or_create_by_phone(
    actor: Actor,
    *,
    raw_phone: str,
    display_name: str = "",
) -> tuple[Customer, bool]:
    """Telefon raqami bo'yicha mijozni topadi yoki yaratadi.

    Qo'ng'iroq kelganda birinchi bajariladigan amal. Caller ID operator
    tarmog'idan kelgani uchun `VOICE` identity darhol **tasdiqlangan** holatda
    yaratiladi — raqamni soxtalashtirish SIP darajasida mumkin, ammo mahalliy
    operator trunkida bu amalda uchramaydi.

    Returns:
        `(mijoz, yangi_yaratildimi)`.

    Raises:
        `InvalidPhoneNumberError` — raqam tanib bo'lmaydigan formatda.
    """
    phone = normalize_phone(raw_phone)

    identity = (
        CustomerIdentity.objects.select_related("customer")
        .filter(channel=ChannelKind.VOICE, external_id=phone)
        .first()
    )
    if identity is not None:
        return identity.customer, False

    try:
        customer = Customer.objects.create(full_name=display_name, primary_phone=phone)
    except IntegrityError:
        # Parallel tranzaksiya shu raqamli mijozni allaqachon yaratgan.
        # Bu xatolik emas — kutilgan poyga holati, mavjud yozuvni o'qiymiz.
        customer = Customer.objects.get(primary_phone=phone)
        return customer, False

    CustomerIdentity.objects.create(
        customer=customer,
        channel=ChannelKind.VOICE,
        external_id=phone,
        display_name=display_name,
        is_verified=True,
        verified_at=timezone.now(),
        can_receive_messages=False,  # SMS alohida kanal, bu yerda tasdiqlanmaydi
    )

    audit.record_event(
        actor,
        AuditEventType.IDENTITY_VERIFIED,
        f"Yangi mijoz yaratildi: {mask_phone(phone)}",
        customer_ref=customer.pk,
        payload={"channel": ChannelKind.VOICE, "phone_masked": mask_phone(phone)},
    )
    return customer, True


def find_by_channel(channel: str, external_id: str) -> Customer | None:
    """Kanal identifikatori bo'yicha mijozni topadi.

    O'qish amali, lekin `services` da joylashgan: u `link_identity` bilan
    yonma-yon ishlatiladi va bir xil kanal mantig'iga tayanadi.
    Hech narsa yozmaydi.
    """
    identity = (
        CustomerIdentity.objects.select_related("customer")
        .filter(channel=channel, external_id=external_id)
        .first()
    )
    return identity.customer if identity else None


@transaction.atomic
def link_identity(
    actor: Actor,
    *,
    customer: Customer,
    channel: str,
    external_id: str,
    display_name: str = "",
    is_verified: bool = False,
    can_receive_messages: bool = False,
) -> CustomerIdentity:
    """Mijozga yangi kanal identifikatorini bog'laydi.

    Chaqiriladi:
      * Telegram opt-in: mijoz imzolangan deep-link'ni ochganda
        (`is_verified=True`, `can_receive_messages=True` — bot endi yoza oladi);
      * Instagram: mijoz izoh qoldirganda (`is_verified=False` — akkaunt nomi
        hech narsani isbotlamaydi, shuning uchun shaxsiy ma'lumot berilmaydi).

    Agar identity boshqa mijozga bog'langan bo'lsa — mavjud yozuv qaytariladi
    va **ko'chirilmaydi**: bu identity o'g'irlash urinishi bo'lishi mumkin,
    shuning uchun hodisa xavfsizlik jurnaliga tushadi.
    """
    existing = (
        CustomerIdentity.objects.select_related("customer")
        .filter(channel=channel, external_id=external_id)
        .first()
    )

    if existing is not None:
        if existing.customer_id != customer.pk:
            audit.record_security_violation(
                actor,
                AuditEventType.SUSPICIOUS_INPUT,
                f"Identity boshqa mijozga bog'langan: {channel}",
                customer_ref=customer.pk,
                payload={
                    "channel": channel,
                    "existing_customer_ref": existing.customer_id,
                    "requested_customer_ref": customer.pk,
                },
            )
        return existing

    identity = CustomerIdentity.objects.create(
        customer=customer,
        channel=channel,
        external_id=external_id,
        display_name=display_name,
        is_verified=is_verified,
        verified_at=timezone.now() if is_verified else None,
        can_receive_messages=can_receive_messages,
    )

    audit.record_event(
        actor,
        AuditEventType.IDENTITY_VERIFIED if is_verified else AuditEventType.CONVERSATION_STARTED,
        f"Kanal bog'landi: {channel} ({'tasdiqlangan' if is_verified else 'tasdiqlanmagan'})",
        customer_ref=customer.pk,
        payload={"channel": channel, "is_verified": is_verified},
    )
    return identity


@transaction.atomic
def mark_identity_verified(actor: Actor, *, identity_id: int) -> CustomerIdentity:
    """Identity'ni tasdiqlangan deb belgilaydi (SMS kodi tekshirilgandan keyin).

    Shundan keyin bu kanal bo'yicha shaxsiy ma'lumot (buyurtma tarixi, manzil)
    berilishi mumkin bo'ladi.
    """
    identity = CustomerIdentity.objects.select_for_update().get(pk=identity_id)

    if identity.is_verified:
        return identity

    identity.is_verified = True
    identity.verified_at = timezone.now()
    identity.save(update_fields=["is_verified", "verified_at", "updated_at"])

    audit.record_event(
        actor,
        AuditEventType.IDENTITY_VERIFIED,
        f"Shaxs tasdiqlandi: {identity.channel}",
        customer_ref=identity.customer_id,
        payload={"channel": identity.channel, "identity_id": identity.pk},
    )
    return identity
