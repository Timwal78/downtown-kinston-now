"""
Downtown Kinston Now — FastAPI Backend
Stripe subscription checkout, webhook handler, business + community CRUD.

ENV VARS (set in Render):
  STRIPE_SECRET_KEY     - sk_test_... or sk_live_...
  STRIPE_WEBHOOK_SECRET - whsec_... (from Stripe Dashboard → Webhooks)
  FRONTEND_URL          - https://downtownkinston.com (or localhost for dev)
  DATABASE_URL          - postgresql://... (Render Postgres)
"""

import os
import json
import stripe
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Request, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from enum import Enum

# ─── CONFIG ───────────────────────────────────────────────────────────────────
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "sk_test_PLACEHOLDER")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "whsec_PLACEHOLDER")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

stripe.api_key = STRIPE_SECRET_KEY

app = FastAPI(title="Downtown Kinston Now API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── MODELS ───────────────────────────────────────────────────────────────────

class TierEnum(str, Enum):
    FREE = "free"
    BOOST = "boost"
    FEATURED = "featured"
    PREMIUM = "premium"

TIER_PRICES = {
    TierEnum.FREE: 0,
    TierEnum.BOOST: 2900,       # cents
    TierEnum.FEATURED: 7900,
    TierEnum.PREMIUM: 14900,
}

TIER_NAMES = {
    TierEnum.FREE: "Free Listing",
    TierEnum.BOOST: "Boost — $29/mo",
    TierEnum.FEATURED: "Featured — $79/mo",
    TierEnum.PREMIUM: "Premium — $149/mo",
}

class PostType(str, Enum):
    ASK = "ask"
    YARDSALE = "yardsale"
    FORSALE = "forsale"
    LOSTPET = "lostpet"
    NEEDSERVICE = "needservice"
    OFFERSERVICE = "offerservice"
    EVENT = "event"
    NEWS = "news"

class BusinessCreate(BaseModel):
    name: str
    category: str
    description: str = ""
    address: str = ""
    phone: str = ""
    website: str = ""
    hours: str = ""
    email: str
    tags: List[str] = []

class BusinessUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    hours: Optional[str] = None
    tags: Optional[List[str]] = None

class CommunityPostCreate(BaseModel):
    type: PostType
    title: str
    body: str
    contact: str = ""
    price: str = ""
    author_name: str

class CheckoutRequest(BaseModel):
    tier: TierEnum
    business_email: str
    business_name: str

# ─── IN-MEMORY STORE (replace with PostgreSQL on Render) ─────────────────────
# This is a working prototype store. Swap to SQLAlchemy + Postgres for production.

businesses_db: dict = {}  # id -> business dict
posts_db: dict = {}       # id -> post dict
subscriptions_db: dict = {}  # stripe_customer_id -> {tier, business_id, ...}
_biz_counter = 0
_post_counter = 0

def next_biz_id():
    global _biz_counter
    _biz_counter += 1
    return f"biz_{_biz_counter}"

def next_post_id():
    global _post_counter
    _post_counter += 1
    return f"post_{_post_counter}"

# ─── STRIPE PRODUCT SETUP ────────────────────────────────────────────────────
# Call this once to create Stripe products + prices. Idempotent.

_stripe_prices: dict = {}  # tier -> stripe price_id

async def ensure_stripe_products():
    """Create Stripe products and prices if they don't exist."""
    global _stripe_prices
    if _stripe_prices:
        return

    for tier in [TierEnum.BOOST, TierEnum.FEATURED, TierEnum.PREMIUM]:
        amount = TIER_PRICES[tier]
        name = TIER_NAMES[tier]

        # Search for existing product
        existing = stripe.Product.search(query=f'name:"{name}"', limit=1)
        if existing.data:
            product = existing.data[0]
        else:
            product = stripe.Product.create(name=name, description=f"Downtown Kinston Now — {name}")

        # Search for existing price
        prices = stripe.Price.list(product=product.id, active=True, limit=1)
        if prices.data:
            price = prices.data[0]
        else:
            price = stripe.Price.create(
                product=product.id,
                unit_amount=amount,
                currency="usd",
                recurring={"interval": "month"},
            )

        _stripe_prices[tier] = price.id

# ─── ROUTES: HEALTH ──────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "app": "Downtown Kinston Now API", "version": "1.0.0"}

@app.get("/health")
async def health():
    return {"status": "healthy", "stripe_configured": STRIPE_SECRET_KEY != "sk_test_PLACEHOLDER"}

# ─── ROUTES: STRIPE CHECKOUT ─────────────────────────────────────────────────

@app.post("/api/checkout")
async def create_checkout(req: CheckoutRequest):
    """Create a Stripe Checkout Session for a business subscription."""
    if req.tier == TierEnum.FREE:
        # Free tier — just create the business, no payment needed
        biz_id = next_biz_id()
        businesses_db[biz_id] = {
            "id": biz_id,
            "name": req.business_name,
            "email": req.business_email,
            "tier": TierEnum.FREE,
            "category": "other",
            "description": "",
            "address": "",
            "phone": "",
            "website": "",
            "hours": "",
            "tags": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "stripe_customer_id": None,
            "stripe_subscription_id": None,
            "active": True,
        }
        return {"status": "free", "business_id": biz_id, "redirect": f"{FRONTEND_URL}/dashboard?id={biz_id}"}

    await ensure_stripe_products()
    price_id = _stripe_prices.get(req.tier)
    if not price_id:
        raise HTTPException(status_code=400, detail=f"No Stripe price for tier: {req.tier}")

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            customer_email=req.business_email,
            metadata={
                "business_name": req.business_name,
                "tier": req.tier.value,
            },
            success_url=f"{FRONTEND_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{FRONTEND_URL}/pricing",
        )
        return {"status": "checkout", "checkout_url": session.url, "session_id": session.id}
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/checkout/success")
async def checkout_success(session_id: str):
    """Verify a completed checkout session."""
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        return {
            "status": session.payment_status,
            "customer_email": session.customer_email,
            "tier": session.metadata.get("tier", "unknown"),
            "business_name": session.metadata.get("business_name", ""),
        }
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))

# ─── ROUTES: STRIPE WEBHOOK ──────────────────────────────────────────────────

@app.post("/api/webhook/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events for subscription lifecycle."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        # New subscription activated
        customer_id = data.get("customer")
        email = data.get("customer_email")
        tier = data.get("metadata", {}).get("tier", "boost")
        biz_name = data.get("metadata", {}).get("business_name", "New Business")
        sub_id = data.get("subscription")

        biz_id = next_biz_id()
        businesses_db[biz_id] = {
            "id": biz_id,
            "name": biz_name,
            "email": email,
            "tier": tier,
            "category": "other",
            "description": "",
            "address": "",
            "phone": "",
            "website": "",
            "hours": "",
            "tags": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "stripe_customer_id": customer_id,
            "stripe_subscription_id": sub_id,
            "active": True,
        }
        subscriptions_db[customer_id] = {"tier": tier, "business_id": biz_id, "active": True}
        print(f"[WEBHOOK] New business: {biz_name} ({tier}) — {email}")

    elif event_type == "customer.subscription.updated":
        customer_id = data.get("customer")
        status = data.get("status")
        if customer_id in subscriptions_db:
            subscriptions_db[customer_id]["active"] = status == "active"
            biz_id = subscriptions_db[customer_id]["business_id"]
            if biz_id in businesses_db:
                businesses_db[biz_id]["active"] = status == "active"
        print(f"[WEBHOOK] Subscription updated: {customer_id} → {status}")

    elif event_type == "customer.subscription.deleted":
        customer_id = data.get("customer")
        if customer_id in subscriptions_db:
            subscriptions_db[customer_id]["active"] = False
            subscriptions_db[customer_id]["tier"] = "free"
            biz_id = subscriptions_db[customer_id]["business_id"]
            if biz_id in businesses_db:
                businesses_db[biz_id]["tier"] = "free"
                businesses_db[biz_id]["active"] = True  # Downgrade to free, don't delete
        print(f"[WEBHOOK] Subscription cancelled: {customer_id}")

    elif event_type == "invoice.payment_failed":
        customer_id = data.get("customer")
        print(f"[WEBHOOK] Payment failed: {customer_id}")

    return {"status": "ok"}

# ─── ROUTES: BUSINESSES ──────────────────────────────────────────────────────

@app.get("/api/businesses")
async def list_businesses(
    category: Optional[str] = None,
    search: Optional[str] = None,
    featured_only: bool = False,
):
    """List businesses with optional filters."""
    results = list(businesses_db.values())
    if category:
        results = [b for b in results if b["category"] == category]
    if search:
        q = search.lower()
        results = [b for b in results if q in b["name"].lower() or q in b.get("description", "").lower() or any(q in t.lower() for t in b.get("tags", []))]
    if featured_only:
        results = [b for b in results if b["tier"] in ("featured", "premium")]

    # Sort: premium first, then featured, then boost, then free
    tier_order = {"premium": 0, "featured": 1, "boost": 2, "free": 3}
    results.sort(key=lambda b: tier_order.get(b.get("tier", "free"), 3))

    return {"businesses": results, "total": len(results)}

@app.get("/api/businesses/{biz_id}")
async def get_business(biz_id: str):
    if biz_id not in businesses_db:
        raise HTTPException(status_code=404, detail="Business not found")
    return businesses_db[biz_id]

@app.post("/api/businesses")
async def create_business(biz: BusinessCreate):
    biz_id = next_biz_id()
    businesses_db[biz_id] = {
        "id": biz_id,
        "name": biz.name,
        "email": biz.email,
        "tier": TierEnum.FREE,
        "category": biz.category,
        "description": biz.description,
        "address": biz.address,
        "phone": biz.phone,
        "website": biz.website,
        "hours": biz.hours,
        "tags": biz.tags,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
        "active": True,
    }
    return businesses_db[biz_id]

@app.patch("/api/businesses/{biz_id}")
async def update_business(biz_id: str, update: BusinessUpdate):
    if biz_id not in businesses_db:
        raise HTTPException(status_code=404, detail="Business not found")
    for field, value in update.dict(exclude_none=True).items():
        businesses_db[biz_id][field] = value
    return businesses_db[biz_id]

# ─── ROUTES: COMMUNITY POSTS ─────────────────────────────────────────────────

@app.get("/api/posts")
async def list_posts(
    type: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(default=50, le=100),
):
    """List community posts with optional type filter."""
    results = list(posts_db.values())
    if type and type != "all":
        results = [p for p in results if p["type"] == type]
    if search:
        q = search.lower()
        results = [p for p in results if q in p["title"].lower() or q in p.get("body", "").lower()]

    # Sort: pinned first, then by created_at descending
    results.sort(key=lambda p: (not p.get("pinned", False), p.get("created_at", "")), reverse=False)
    results.sort(key=lambda p: p.get("pinned", False), reverse=True)

    return {"posts": results[:limit], "total": len(results)}

@app.post("/api/posts")
async def create_post(post: CommunityPostCreate):
    post_id = next_post_id()
    posts_db[post_id] = {
        "id": post_id,
        "type": post.type,
        "title": post.title,
        "body": post.body,
        "contact": post.contact,
        "price": post.price,
        "author_name": post.author_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "likes": 0,
        "comments": 0,
        "pinned": False,
        "urgent": post.type == PostType.LOSTPET,
    }
    return posts_db[post_id]

@app.post("/api/posts/{post_id}/like")
async def like_post(post_id: str):
    if post_id not in posts_db:
        raise HTTPException(status_code=404, detail="Post not found")
    posts_db[post_id]["likes"] += 1
    return {"likes": posts_db[post_id]["likes"]}

# ─── ROUTES: ADMIN ────────────────────────────────────────────────────────────
# Basic admin endpoints. Add auth middleware for production.

@app.post("/api/admin/posts/{post_id}/pin")
async def pin_post(post_id: str):
    if post_id not in posts_db:
        raise HTTPException(status_code=404, detail="Post not found")
    posts_db[post_id]["pinned"] = not posts_db[post_id]["pinned"]
    return {"pinned": posts_db[post_id]["pinned"]}

@app.delete("/api/admin/posts/{post_id}")
async def delete_post(post_id: str):
    if post_id not in posts_db:
        raise HTTPException(status_code=404, detail="Post not found")
    del posts_db[post_id]
    return {"deleted": True}

@app.get("/api/admin/stats")
async def admin_stats():
    active_subs = sum(1 for s in subscriptions_db.values() if s.get("active"))
    return {
        "total_businesses": len(businesses_db),
        "active_subscriptions": active_subs,
        "total_posts": len(posts_db),
        "revenue_monthly_estimate": sum(
            TIER_PRICES.get(TierEnum(s["tier"]), 0) / 100
            for s in subscriptions_db.values()
            if s.get("active")
        ),
    }

# ─── STARTUP ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    print("🟢 Downtown Kinston Now API starting...")
    print(f"   Stripe configured: {STRIPE_SECRET_KEY != 'sk_test_PLACEHOLDER'}")
    print(f"   Frontend URL: {FRONTEND_URL}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
