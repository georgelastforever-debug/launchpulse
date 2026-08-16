# 🍊 LaunchPulse

**Product Launch Command Center** — 90-day cycle tracker with Supabase Auth + Database and a sleek orange UI.

## Features

- **Login / Sign-up** on the front page (Supabase Auth)
- **Dashboard** — live stats, phase progress cards, upcoming launches, playbooks
- **Timeline** — visual month-by-month Gantt of all cycles
- **Product CRUD** + per-phase team notes
- Auto phase calculation (Buy → Review → Push)

## 1. Create a Supabase project

1. Go to [supabase.com](https://supabase.com) → New project
2. Open **SQL Editor** → paste and run the contents of `supabase_schema.sql`
3. Go to **Project Settings → API** and copy:
   - Project URL
   - `anon` `public` key

## 2. Configure the app

```bash
cd launchpulse
cp .env.example .env
```

Edit `.env`:

```
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

## 3. Install & run

```bash
pip3 install -r requirements.txt
python3 app.py
```

Open **http://127.0.0.1:5050**

- First visit → Login page
- Create an account (Sign up) or use an existing Supabase Auth user
- On first successful login the four seed products are inserted automatically

## Seed products (Aug 2026)

| Product        | Launch   | Phase              |
|----------------|----------|--------------------|
| MaxErectPro    | Jul 2026 | Review (Month 2)   |
| TrueVitamin    | Aug 2026 | Buy (Month 1)      |
| MaxRyno        | Sep 2026 | Upcoming           |
| ErectForgepro  | Oct 2026 | Upcoming           |

## Auth notes

- Uses Supabase email/password auth
- If your project has "Confirm email" enabled, users must confirm before the first session is issued
- All app routes (except `/login` and `/signup`) require a logged-in session

## Tech

- Flask + Jinja2
- Supabase (Auth + Postgres)
- Tailwind CSS (CDN)
