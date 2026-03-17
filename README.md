# Downtown Kinston Now — Backend API

FastAPI backend with Stripe subscription checkout for business tiers.

## What It Does

- **Stripe Checkout** — Creates subscription sessions for Boost ($29), Featured ($79), Premium ($149)
- **Webhook Handler** — Activates/deactivates tiers when Stripe events fire
- **Business CRUD** — Create, read, update business listings
- **Community Posts** — Ask Kinston, yard sales, buy/sell, lost pets, services, events, news
- **Admin Endpoints** — Pin posts, delete posts, view revenue stats

## API Endpoints

### Stripe
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/checkout` | Create Stripe Checkout session |
| GET | `/api/checkout/success?session_id=` | Verify completed checkout |
| POST | `/api/webhook/stripe` | Stripe webhook receiver |

### Businesses
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/businesses` | List all (filter: category, search, featured_only) |
| GET | `/api/businesses/{id}` | Get single business |
| POST | `/api/businesses` | Create new listing |
| PATCH | `/api/businesses/{id}` | Update listing |

### Community
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/posts` | List posts (filter: type, search) |
| POST | `/api/posts` | Create post |
| POST | `/api/posts/{id}/like` | Like a post |

### Admin
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/admin/posts/{id}/pin` | Toggle pin |
| DELETE | `/api/admin/posts/{id}` | Delete post |
| GET | `/api/admin/stats` | Revenue + counts |

## Local Dev

```bash
cd dkn-backend
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your Stripe test keys
python main.py
# → http://localhost:8000
# → http://localhost:8000/docs (Swagger UI)
```

## Deploy to Render

1. Push to GitHub
2. Render → New Web Service → connect repo
3. Build: `pip install -r requirements.txt`
4. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Add env vars in Render dashboard:
   - `STRIPE_SECRET_KEY` — from Stripe Dashboard → Developers → API Keys
   - `STRIPE_WEBHOOK_SECRET` — from Stripe Dashboard → Developers → Webhooks
   - `FRONTEND_URL` — your frontend domain

## Stripe Webhook Setup

1. Stripe Dashboard → Developers → Webhooks → Add endpoint
2. URL: `https://your-render-url.onrender.com/api/webhook/stripe`
3. Events to listen for:
   - `checkout.session.completed`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_failed`
4. Copy the signing secret → set as `STRIPE_WEBHOOK_SECRET` in Render

## Checkout Flow

```
Frontend pricing page
  → User clicks "Go Featured" ($79/mo)
  → POST /api/checkout {tier: "featured", business_email, business_name}
  → Backend creates Stripe Checkout Session
  → User redirected to Stripe-hosted checkout
  → User pays
  → Stripe fires webhook → checkout.session.completed
  → Backend creates business with "featured" tier
  → User redirected to /success
```

## Production Notes

- Current storage is in-memory (resets on restart). Add SQLAlchemy + Render Postgres for persistence.
- Add JWT auth for business owner dashboard.
- Add rate limiting for community posts.
- Add image upload via S3 or Cloudinary for yard sales, lost pets, etc.
