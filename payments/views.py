from django.shortcuts import render
import razorpay
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import Payment
import json
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
import hmac
import hashlib

client = razorpay.Client(
    auth=(
        settings.RAZORPAY_KEY_ID,
        settings.RAZORPAY_KEY_SECRET
    )
)


@csrf_exempt
def create_order(request):

    if request.method != "POST":
        return JsonResponse(
            {"error": "POST method required"},
            status=405
        )

    try:
        amount = 10000  # ₹100 in paise

        order_data = {
            "amount": amount,
            "currency": "INR",
            "receipt": "receipt_demo_001"
        }

        razorpay_order = client.order.create(
            data=order_data
        )

        payment = Payment.objects.create(
            amount=amount,
            currency="INR",
            razorpay_order_id=razorpay_order["id"]
        )

        return JsonResponse({
            "success": True,
            "order_id": razorpay_order["id"],
            "amount": amount,
            "currency": "INR",
            "key": settings.RAZORPAY_KEY_ID
        })

    except Exception as e:
        return JsonResponse({
            "success": False,
            "error": str(e)
        }, status=500)




def checkout(request):
    return render(request, 'payments/checkout.html')



def payment_success(request):
    return render(
        request,
        'payments/payment_success.html'
    )




@csrf_exempt
@require_POST
def verify_payment(request):
    print("\n========== VERIFY PAYMENT STARTED ==========")

    try:
        data = json.loads(request.body)

        order_id = data.get("razorpay_order_id")
        payment_id = data.get("razorpay_payment_id")
        signature = data.get("razorpay_signature")

        print("Order ID received:", bool(order_id))
        print("Payment ID received:", bool(payment_id))
        print("Signature received:", bool(signature))

        if not all([order_id, payment_id, signature]):
            return JsonResponse({
                "success": False,
                "error": "Missing payment details"
            }, status=400)

        payment = Payment.objects.get(
            razorpay_order_id=order_id
        )

        client.utility.verify_payment_signature({
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature
        })

        payment.razorpay_payment_id = payment_id
        payment.status = "PAID"
        payment.save()

        print("PAYMENT VERIFIED SUCCESSFULLY")

        return JsonResponse({
            "success": True,
            "message": "Payment verified successfully"
        })

    except Payment.DoesNotExist:
        print("ERROR: Order not found")
        return JsonResponse({
            "success": False,
            "error": "Order not found"
        }, status=404)

    except razorpay.errors.SignatureVerificationError:
        print("ERROR: Invalid payment signature")
        return JsonResponse({
            "success": False,
            "error": "Invalid payment signature"
        }, status=400)

    except Exception as e:
        print("VERIFICATION ERROR:", repr(e))
        return JsonResponse({
            "success": False,
            "error": "Payment verification failed"
        }, status=400)





@csrf_exempt
@require_POST
def razorpay_webhook(request):
    webhook_secret = settings.RAZORPAY_WEBHOOK_SECRET

    if not webhook_secret:
        return JsonResponse(
            {"error": "Webhook secret not configured"},
            status=500
        )

    # Verify signature using the original request body
    webhook_signature = request.headers.get(
        "X-Razorpay-Signature", ""
    )

    expected_signature = hmac.new(
        webhook_secret.encode("utf-8"),
        request.body,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(
        expected_signature, webhook_signature
    ):
        return JsonResponse(
            {"error": "Invalid webhook signature"},
            status=400
        )

    try:
        data = json.loads(request.body)
        event = data.get("event")

        if event not in ["payment.captured", "payment.failed"]:
            return JsonResponse({"status": "ignored"})

        payment_entity = (
            data.get("payload", {})
                .get("payment", {})
                .get("entity", {})
        )

        order_id = payment_entity.get("order_id")
        payment_id = payment_entity.get("id")

        if not order_id or not payment_id:
            return JsonResponse(
                {"error": "Missing payment details"},
                status=400
            )

        payment = Payment.objects.get(
            razorpay_order_id=order_id
        )

        # Check amount and currency before marking as paid
        if (
            payment_entity.get("amount") != payment.amount
            or payment_entity.get("currency") != payment.currency
        ):
            return JsonResponse(
                {"error": "Payment amount or currency mismatch"},
                status=400
            )

        if event == "payment.captured":
            payment.razorpay_payment_id = payment_id
            payment.status = "PAID"
            payment.save()

        elif event == "payment.failed":
            # Never downgrade an already paid payment
            if payment.status != "PAID":
                payment.razorpay_payment_id = payment_id
                payment.status = "FAILED"
                payment.save()

        return JsonResponse({"status": "success"})

    except Payment.DoesNotExist:
        return JsonResponse(
            {"error": "Payment order not found"},
            status=404
        )

    except (ValueError, TypeError):
        return JsonResponse(
            {"error": "Invalid webhook payload"},
            status=400
        )
