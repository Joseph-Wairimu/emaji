import logging
from decimal import Decimal
from dateutil import parser as date_parser

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, BasePermission

from .models import (
    Meter, Customer, UnitPrice,
    SmartMeterReading, PrepaidWallet, ValveCommand,
)
from .permissions import IsAdmin
from .services.fengbo_cloud import FengboCloudService

logger = logging.getLogger(__name__)

# Map self-hosted decoder flow units → m³ multiplier
FLOW_UNIT_TO_M3 = {
    "mL":    Decimal("0.000001"),
    "10mL":  Decimal("0.00001"),
    "100mL": Decimal("0.0001"),
    "L":     Decimal("0.001"),
    "10L":   Decimal("0.01"),
    "100L":  Decimal("0.1"),
    "1000L": Decimal("1"),
}


class SmartMeterKeyPermission(BasePermission):
    """API-key auth for meter_subscriber.py — no JWT needed."""
    def has_permission(self, request, view):
        expected = settings.SMART_METER_API_KEY
        provided = request.headers.get("X-Smart-Meter-Key", "")
        return bool(expected and provided == expected)


def _parse_dt(value):
    if not value:
        return None
    try:
        return date_parser.parse(value)
    except Exception:
        return None


def _to_m3(raw_value, unit: str) -> Decimal | None:
    if raw_value is None:
        return None
    multiplier = FLOW_UNIT_TO_M3.get(unit, Decimal("0.001"))  # default: assume L
    return Decimal(str(raw_value)) * multiplier


def _send_valve_command(meter: Meter, action: str, reason: str, wallet: PrepaidWallet | None = None):
    cmd = ValveCommand.objects.create(meter=meter, action=action, reason=reason, status="pending")

    if settings.FENGBO_API_URL:
        # Try Fengbo Cloud API (for meters registered on Fengbo's cloud platform).
        # If it fails or isn't configured, the command stays 'pending' and the
        # self-hosted TCP decoder will pick it up on the meter's next upload.
        try:
            result = FengboCloudService().control_valve(meter.meter_address, action)
            cmd.status = "sent"
            cmd.fengbo_response = result
            cmd.sent_at = timezone.now()
            if wallet is not None:
                wallet.valve_status = "closed" if action == "close" else "open"
            logger.info("Valve %s sent via Fengbo Cloud API for %s", action, meter.meter_address)
        except Exception as exc:
            logger.warning(
                "Fengbo Cloud API unavailable for %s — command queued for TCP decoder: %s",
                meter.meter_address, exc,
            )
    else:
        logger.info(
            "ValveCommand '%s' queued for meter %s — decoder will send on next upload",
            action, meter.meter_address,
        )

    cmd.save()


def _run_prepaid_logic(meter: Meter, current_flow_m3: Decimal, reported_valve_status: str | None):
    try:
        customer = Customer.objects.get(meter=meter)
        wallet = customer.wallet
    except (Customer.DoesNotExist, PrepaidWallet.DoesNotExist):
        return

    if reported_valve_status:
        wallet.valve_status = reported_valve_status

    # First real reading — set baseline, no deduction yet
    if wallet.last_known_flow_m3 == Decimal("0") and current_flow_m3 > Decimal("0"):
        wallet.last_known_flow_m3 = current_flow_m3
        wallet.save()
        return

    consumed = current_flow_m3 - wallet.last_known_flow_m3
    if consumed <= Decimal("0"):
        wallet.save()
        return

    old_balance = wallet.balance_m3
    wallet.balance_m3 = wallet.balance_m3 - consumed
    wallet.last_known_flow_m3 = current_flow_m3

    # Balance just crossed zero — close valve for arrears
    if wallet.balance_m3 <= Decimal("0") and old_balance > Decimal("0"):
        _send_valve_command(meter, "close", "balance_zero", wallet)

    wallet.save()


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class SmartMeterIngestView(APIView):
    """
    POST /api/smart-meter/ingest/
    Called by meter_subscriber.py on every telemetry reading.
    Authenticated with X-Smart-Meter-Key header (no JWT).
    """
    permission_classes = [SmartMeterKeyPermission]
    authentication_classes = []

    def post(self, request):
        payload = request.data
        meter_address = payload.get("meter_address")
        if not meter_address:
            return Response({"error": "meter_address required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            meter = Meter.objects.get(meter_address=meter_address)
        except Meter.DoesNotExist:
            return Response(
                {"error": f"Meter {meter_address} not registered in E-Maji"},
                status=status.HTTP_404_NOT_FOUND,
            )

        total_flow_raw = payload.get("total_flow")
        total_flow_unit = payload.get("total_flow_unit") or "L"
        total_flow_m3 = _to_m3(total_flow_raw, total_flow_unit)

        reading = SmartMeterReading.objects.create(
            meter=meter,
            meter_address=meter_address,
            reading_time=_parse_dt(payload.get("timestamp")),
            received_at=_parse_dt(payload.get("received_at")) or timezone.now(),
            total_flow_m3=total_flow_m3,
            total_flow_raw=total_flow_raw,
            total_flow_unit=total_flow_unit,
            valve_status=payload.get("valve_status"),
            battery_voltage_v=payload.get("battery_voltage_v"),
            csq=payload.get("csq"),
            no_water_alarm=payload.get("no_water_alarm"),
            low_battery_alarm=payload.get("low_battery_alarm"),
            reverse_alarm=payload.get("reverse_alarm"),
            water_temperature_c=payload.get("water_temperature_c"),
            instantaneous_flow=payload.get("instantaneous_flow"),
            raw_payload=payload,
        )

        if total_flow_m3 is not None:
            _run_prepaid_logic(meter, total_flow_m3, payload.get("valve_status"))

        return Response({"status": "ok", "reading_id": str(reading.id)}, status=status.HTTP_201_CREATED)


class SmartMeterStatusView(APIView):
    """
    GET /api/smart-meter/status/<meter_address>/
    Returns the latest SmartMeterReading plus prepaid wallet if applicable.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, meter_address):
        meter = get_object_or_404(Meter, meter_address=meter_address)
        latest = meter.smart_readings.first()
        if not latest:
            return Response({"error": "No readings available yet"}, status=status.HTTP_404_NOT_FOUND)

        data = {
            "meter_address": meter_address,
            "meter_number": meter.meter_number,
            "meter_type": meter.meter_type,
            "reading_time": latest.reading_time,
            "received_at": latest.received_at,
            "total_flow_m3": str(latest.total_flow_m3) if latest.total_flow_m3 is not None else None,
            "valve_status": latest.valve_status,
            "battery_voltage_v": latest.battery_voltage_v,
            "csq": latest.csq,
            "no_water_alarm": latest.no_water_alarm,
            "low_battery_alarm": latest.low_battery_alarm,
            "reverse_alarm": latest.reverse_alarm,
            "water_temperature_c": latest.water_temperature_c,
            "instantaneous_flow": latest.instantaneous_flow,
            "prepaid": None,
        }

        try:
            customer = Customer.objects.get(meter=meter)
            wallet = customer.wallet
            unit_price = UnitPrice.objects.order_by("-effective_date").first()
            balance_kes = None
            if unit_price and unit_price.unit_price:
                balance_kes = str(
                    (wallet.balance_m3 * unit_price.unit_price).quantize(Decimal("0.01"))
                )
            data["prepaid"] = {
                "customer_id": str(customer.id),
                "balance_m3": str(wallet.balance_m3),
                "balance_kes": balance_kes,
                "valve_status": wallet.valve_status,
                "last_updated": wallet.updated_at,
            }
        except (Customer.DoesNotExist, PrepaidWallet.DoesNotExist):
            pass

        return Response(data)


class PrepaidWalletView(APIView):
    """
    GET /api/prepaid/wallet/<customer_id>/
    Returns wallet balance and status for a customer.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, customer_id):
        customer = get_object_or_404(Customer, id=customer_id)
        try:
            wallet = customer.wallet
        except PrepaidWallet.DoesNotExist:
            return Response(
                {"error": "No prepaid wallet for this customer"},
                status=status.HTTP_404_NOT_FOUND,
            )

        unit_price = UnitPrice.objects.order_by("-effective_date").first()
        balance_kes = None
        if unit_price and unit_price.unit_price:
            balance_kes = str(
                (wallet.balance_m3 * unit_price.unit_price).quantize(Decimal("0.01"))
            )

        return Response({
            "customer_id": str(customer.id),
            "customer_name": f"{customer.first_name} {customer.last_name}",
            "balance_m3": str(wallet.balance_m3),
            "balance_kes": balance_kes,
            "valve_status": wallet.valve_status,
            "last_updated": wallet.updated_at,
            "unit_price_per_m3": str(unit_price.unit_price) if unit_price else None,
        })


class PrepaidTopupView(APIView):
    """
    POST /api/prepaid/topup/
    Body: { "customer_id": "<uuid>", "amount_kes": <number> }
    Adds credit, opens valve if balance was previously exhausted.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        customer_id = request.data.get("customer_id")
        amount_kes_raw = request.data.get("amount_kes")

        if not customer_id or amount_kes_raw is None:
            return Response(
                {"error": "customer_id and amount_kes are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            amount_kes = Decimal(str(amount_kes_raw))
            if amount_kes <= 0:
                raise ValueError
        except (ValueError, Exception):
            return Response(
                {"error": "amount_kes must be a positive number"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        customer = get_object_or_404(Customer, id=customer_id)

        unit_price = UnitPrice.objects.order_by("-effective_date").first()
        if not unit_price or unit_price.unit_price <= 0:
            return Response({"error": "No unit price configured"}, status=status.HTTP_400_BAD_REQUEST)

        m3_added = amount_kes / unit_price.unit_price

        wallet, _ = PrepaidWallet.objects.get_or_create(
            customer=customer,
            defaults={
                "balance_m3": Decimal("0"),
                "last_known_flow_m3": Decimal("0"),
                "valve_status": "unknown",
            },
        )

        was_exhausted = wallet.balance_m3 <= Decimal("0")
        wallet.balance_m3 += m3_added
        wallet.save()

        valve_opened = False
        fengbo_payment_sync = None
        meter = customer.meter

        if meter and meter.meter_address:
            # Re-open valve if balance was exhausted and now positive
            if was_exhausted and wallet.balance_m3 > Decimal("0"):
                _send_valve_command(meter, "open", "topup", wallet)
                wallet.save()
                valve_opened = True

            # Sync payment to Fengbo Cloud (non-fatal if it fails)
            if settings.FENGBO_API_URL:
                try:
                    fengbo_payment_sync = FengboCloudService().record_payment(
                        meter.meter_address, float(amount_kes)
                    )
                except Exception as exc:
                    logger.warning("Fengbo payment sync failed (non-fatal): %s", exc)

        return Response({
            "status": "ok",
            "m3_added": str(m3_added.quantize(Decimal("0.000001"))),
            "new_balance_m3": str(wallet.balance_m3.quantize(Decimal("0.000001"))),
            "new_balance_kes": str(
                (wallet.balance_m3 * unit_price.unit_price).quantize(Decimal("0.01"))
            ),
            "valve_opened": valve_opened,
            "fengbo_sync": fengbo_payment_sync,
        })


class ValveControlView(APIView):
    """
    POST /api/smart-meter/valve/
    Body: { "meter_address": "...", "action": "open"|"close" }
    Admin only — manual override.
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def post(self, request):
        meter_address = request.data.get("meter_address")
        action = request.data.get("action")

        if not meter_address or action not in ("open", "close"):
            return Response(
                {"error": "meter_address and action ('open'|'close') required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        meter = get_object_or_404(Meter, meter_address=meter_address)

        # Update wallet valve_status if prepaid
        wallet = None
        try:
            customer = Customer.objects.get(meter=meter)
            wallet = customer.wallet
        except (Customer.DoesNotExist, PrepaidWallet.DoesNotExist):
            pass

        _send_valve_command(meter, action, "manual", wallet)
        if wallet:
            wallet.save()

        return Response({"status": "ok", "action": action, "meter_address": meter_address})


# ---------------------------------------------------------------------------
# Decoder-facing command queue endpoints (SmartMeterKeyPermission, no JWT)
# ---------------------------------------------------------------------------

class SmartMeterCommandPendingView(APIView):
    """
    GET /api/smart-meter/commands/pending/<meter_address>/
    Called by fengbo_decoder.py on each meter upload.
    Returns the oldest pending valve command or {"command": null}.
    """
    permission_classes = [SmartMeterKeyPermission]
    authentication_classes = []

    def get(self, request, meter_address):
        try:
            meter = Meter.objects.get(meter_address=meter_address)
        except Meter.DoesNotExist:
            return Response({"command": None})

        cmd = meter.valve_commands.filter(status="pending").order_by("created_at").first()
        if not cmd:
            return Response({"command": None})

        return Response({
            "command": {
                "id": str(cmd.id),
                "action": cmd.action,
                "reason": cmd.reason,
            }
        })


class SmartMeterCommandUpdateView(APIView):
    """
    POST /api/smart-meter/commands/<command_id>/update/
    Called by fengbo_decoder.py after sending valve frame.
    Body: { "status": "sent" | "failed" }
    """
    permission_classes = [SmartMeterKeyPermission]
    authentication_classes = []

    def post(self, request, command_id):
        try:
            cmd = ValveCommand.objects.get(id=command_id)
        except ValveCommand.DoesNotExist:
            return Response({"error": "Command not found"}, status=status.HTTP_404_NOT_FOUND)

        new_status = request.data.get("status")
        if new_status not in ("sent", "failed"):
            return Response(
                {"error": "status must be 'sent' or 'failed'"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cmd.status = new_status
        cmd.sent_at = timezone.now()
        cmd.save()
        return Response({"status": "ok"})


class SmartMeterCommandAcknowledgeView(APIView):
    """
    POST /api/smart-meter/commands/acknowledge/<meter_address>/
    Called by fengbo_decoder.py when meter returns function code 0x03
    (parameter setting result), confirming the valve command was executed.
    Marks the most recently sent command as acknowledged and syncs wallet
    valve_status to reflect the physical state.
    """
    permission_classes = [SmartMeterKeyPermission]
    authentication_classes = []

    def post(self, request, meter_address):
        try:
            meter = Meter.objects.get(meter_address=meter_address)
        except Meter.DoesNotExist:
            return Response({"status": "unknown_meter"})

        cmd = meter.valve_commands.filter(status="sent").order_by("-sent_at").first()
        if not cmd:
            return Response({"status": "no_sent_command"})

        cmd.status = "acknowledged"
        cmd.acknowledged_at = timezone.now()
        cmd.save()

        # Sync wallet valve_status to what the meter physically confirmed
        try:
            customer = Customer.objects.get(meter=meter)
            wallet = customer.wallet
            wallet.valve_status = "closed" if cmd.action == "close" else "open"
            wallet.save()
        except (Customer.DoesNotExist, PrepaidWallet.DoesNotExist):
            pass

        logger.info(
            "Valve command '%s' acknowledged by meter %s", cmd.action, meter_address
        )
        return Response({"status": "ok", "acknowledged_action": cmd.action})
