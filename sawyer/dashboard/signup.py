"""Sawyer Signup API — turns landing page visitors into paying customers.

Endpoints:
  POST /api/signup           — Create account (Explorer trial), return API key
  POST /api/checkout         — Create Stripe checkout session for paid tier
  GET  /api/checkout/success  — Redirect after successful payment
  GET  /api/checkout/cancel   — Redirect after cancelled payment
  POST /api/stripe/webhook   — Stripe webhook handler
  GET  /api/me               — Get current user info (by API key)
"""

import hashlib
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr

from sawyer.auth.api import SawyerAuth
from sawyer.storage.database import SawyerStorage
from sawyer.token.budget import SubscriptionTier, TokenBalance
from sawyer.token.stripe import SawyerStripe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["signup"])

# ── Request/Response Models ────────────────────────────────────────


class SignupRequest(BaseModel):
    email: EmailStr
    name: str | None = None
    tier: str = "explorer"  # explorer, pro, pioneer, enterprise


class SignupResponse(BaseModel):
    user_id: str
    api_key: str  # Only shown once — client must save this
    tier: str
    token_budget: int
    message: str
    checkout_url: str = ""  # Stripe checkout URL for paid tiers
    session_id: str = ""    # Stripe checkout session ID


class CheckoutRequest(BaseModel):
    email: EmailStr
    tier: str  # pro, pioneer, enterprise
    user_id: str | None = None


class CheckoutResponse(BaseModel):
    checkout_url: str
    session_id: str


class UserInfoResponse(BaseModel):
    user_id: str
    tier: str
    token_budget: int
    tokens_used: int
    tokens_remaining: int
    is_active: bool
    created_at: float


# ── Helpers ────────────────────────────────────────────────────────

_storage: SawyerStorage | None = None
_auth: SawyerAuth | None = None


def _get_storage() -> SawyerStorage:
    global _storage
    if _storage is None:
        from sawyer.config import SawyerConfig

        _storage = SawyerStorage(SawyerConfig().database_url)
    return _storage


def _get_auth() -> SawyerAuth:
    global _auth
    if _auth is None:
        _auth = SawyerAuth(_get_storage())
    return _auth


def _get_user_id_from_email(email: str) -> str:
    """Generate a stable user ID from email."""
    return hashlib.sha256(email.lower().encode()).hexdigest()[:16]


# ── Routes ─────────────────────────────────────────────────────────


@router.post("/signup", response_model=SignupResponse)
async def signup(request: SignupRequest) -> Any:
    """Create a Sawyer account and return an API key.

    Explorer tier is free (14-day trial with unlimited tokens).
    Paid tiers redirect to Stripe checkout.
    """
    auth = _get_auth()
    storage = _get_storage()
    user_id = _get_user_id_from_email(request.email)

    # Check if account already exists
    existing = storage.load_account(user_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Account already exists for {request.email}. "
            "Use /api/checkout to upgrade or contact support.",
        )

    # Validate tier
    try:
        tier = SubscriptionTier(request.tier.lower())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tier: {request.tier}. "
            "Choose from: explorer, pro, pioneer, enterprise",
        ) from None

    # Explorer tier: create account immediately, no payment needed
    if tier == SubscriptionTier.EXPLORER:
        # Create account with token budget
        account = storage.create_account(user_id=user_id, tier=tier)

        # Generate API key
        full_key, api_key_obj = auth.create_key(
            user_id=user_id,
            tier=tier.value,
        )

        return SignupResponse(
            user_id=user_id,
            api_key=full_key,  # Only time the full key is shown
            tier=tier.value,
            token_budget=account.balance.total_available,
            message="Your 14-day free trial is active. "
            "Save your API key — it won't be shown again.",
        )

    # Paid tier: create checkout session
    stripe = SawyerStripe()
    if not stripe.api_key:
        raise HTTPException(
            status_code=503,
            detail="Payment processing not configured. Please try again later.",
        )

    # Create Stripe customer
    customer = stripe.create_customer(
        email=request.email,
        name=request.name,
        user_id=user_id,
    )

    # Create checkout session
    try:
        session = stripe.create_checkout_session(
            stripe_customer_id=customer["id"],
            tier=tier,
            success_url="https://sawyer.infill.systems/api/checkout/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://sawyer.infill.systems/api/checkout/cancel",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    # Return checkout URL so the frontend can redirect
    return SignupResponse(
        user_id=user_id,
        api_key="",
        tier=tier.value,
        token_budget=0,
        message=f"Complete your {tier.value.capitalize()} subscription to get your API key.",
        checkout_url=session.url,
        session_id=session.id,
    )


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(request: CheckoutRequest) -> Any:
    """Create a Stripe checkout session for a paid tier."""
    stripe = SawyerStripe()
    if not stripe.api_key:
        raise HTTPException(
            status_code=503,
            detail="Payment processing not configured.",
        )

    try:
        tier = SubscriptionTier(request.tier.lower())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tier: {request.tier}",
        ) from None

    if tier == SubscriptionTier.EXPLORER:
        raise HTTPException(
            status_code=400,
            detail="Explorer tier is free. Use /api/signup instead.",
        )

    user_id = request.user_id or _get_user_id_from_email(request.email)

    # Create or find Stripe customer
    customer = stripe.create_customer(
        email=request.email,
        user_id=user_id,
    )

    # Create checkout session
    try:
        session = stripe.create_checkout_session(
            stripe_customer_id=customer["id"],
            tier=tier,
            success_url="https://sawyer.infill.systems/api/checkout/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://sawyer.infill.systems/api/checkout/cancel",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return CheckoutResponse(
        checkout_url=session.url,
        session_id=session.id,
    )


@router.get("/checkout/success")
async def checkout_success(session_id: str) -> dict[str, Any]:
    """Handle successful Stripe checkout.

    After payment, Stripe redirects here. We:
    1. Retrieve the subscription details
    2. Create the Sawyer account
    3. Generate an API key
    4. Return the key (in production, send via email)
    """
    stripe = SawyerStripe()
    if not stripe.api_key:
        raise HTTPException(status_code=503, detail="Payment processing not configured.")

    # Retrieve the checkout session
    try:
        import stripe as stripe_lib

        session = stripe_lib.checkout.Session.retrieve(session_id)
    except Exception as e:
        logger.error("Failed to retrieve checkout session %s: %s", session_id, e)
        raise HTTPException(status_code=400, detail="Invalid checkout session.") from None

    if session.payment_status != "paid":
        raise HTTPException(status_code=402, detail="Payment not completed.")

    # Get subscription details
    subscription = stripe.get_subscription(session.subscription)
    if not subscription:
        raise HTTPException(status_code=500, detail="Subscription not found after payment.")

    # Create account and API key
    storage = _get_storage()
    auth = _get_auth()

    # Create account with the paid tier
    account = storage.create_account(
        user_id=subscription.user_id or _get_user_id_from_email(session.customer_email or ""),
        tier=subscription.tier,
    )

    # Generate API key
    full_key, api_key_obj = auth.create_key(
        user_id=account.user_id,
        tier=subscription.tier.value,
    )

    return {
        "user_id": account.user_id,
        "api_key": full_key,
        "tier": subscription.tier.value,
        "token_budget": account.balance.total_available,
        "message": "Subscription active. Save your API key — it won't be shown again.",
    }


@router.get("/checkout/cancel")
async def checkout_cancel() -> dict[str, str]:
    """Handle cancelled checkout."""
    return {
        "status": "cancelled",
        "message": "Checkout was cancelled. Your account was not created.",
    }


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request) -> dict[str, str]:
    """Handle Stripe webhook events.

    Events handled:
    - checkout.session.completed: Activate account after payment
    - customer.subscription.updated: Tier change
    - customer.subscription.deleted: Account cancellation
    - invoice.payment_failed: Payment failure notification
    """
    stripe = SawyerStripe()
    if not stripe.api_key:
        raise HTTPException(status_code=503, detail="Payment processing not configured.")

    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")

    webhook_secret = request.app.state.stripe_webhook_secret if hasattr(request.app, "state") else ""

    try:
        event = stripe.process_webhook(payload, sig_header, webhook_secret)
    except Exception as e:
        logger.error("Webhook signature verification failed: %s", e)
        raise HTTPException(status_code=400, detail="Invalid webhook signature.") from None

    return {"status": "received"}


@router.get("/me", response_model=UserInfoResponse)
async def get_user_info(request: Request) -> Any:
    """Get current user info based on API key."""
    api_key_str = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if not api_key_str:
        raise HTTPException(status_code=401, detail="API key required.")

    auth = _get_auth()
    storage = _get_storage()

    try:
        api_key = auth.validate_key(api_key_str)
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e)) from None

    account = storage.load_account(api_key.user_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")

    return UserInfoResponse(
        user_id=account.user_id,
        tier=account.tier.value,
        token_budget=account.balance.monthly_budget,
        tokens_used=account.total_tokens_used,
        tokens_remaining=account.balance.total_available,
        is_active=account.is_active,
        created_at=account.created_at,
    )