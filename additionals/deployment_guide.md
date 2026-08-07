# AE-03 Directive V2 — Production Netlify & Cloud Deployment Guide

This guide provides step-by-step instructions for deploying the **AE-03 Directive V2 Platform** online using **Netlify** (Frontend), **Render / Railway** (Backend API), and **Supabase** (PostgreSQL pgvector Database).

---

## 🌐 Architecture Overview

```
┌─────────────────────────┐       ┌───────────────────────────────┐       ┌────────────────────────────────┐
│   Netlify Next.js UI    │ ────> │   Render FastAPI Backend API  │ ────> │   Supabase pgvector Database   │
│  (https://yourapp.net)  │       │   (https://api.onrender.com)  │       │    (1536-dim HNSW Embeddings)  │
└─────────────────────────┘       └───────────────────────────────┘       └────────────────────────────────┘
```

---

## 🚀 Step 1: Deploy PostgreSQL pgvector Database on Supabase

1. Create a free project on [Supabase](https://supabase.com).
2. Go to **SQL Editor** and run the schema setup script located at `backend/rag/schema.sql`:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   -- Runs table creation for rag_documents and rag_chunks
   ```
3. Copy your Database Connection string:
   - Host: `db.xxxx.supabase.co`
   - User: `postgres`
   - Password: `[YOUR_PASSWORD]`
   - Port: `5432` or `6543`

---

## 🐍 Step 2: Deploy Python FastAPI Backend on Render or Railway

### Option A: Render Deployment (Recommended)
1. Log into [Render](https://render.com) and click **New Web Service**.
2. Connect your GitHub repository: `https://github.com/Zinat-Khan/CodeRush2.0-AllBlueInnovation`.
3. Set the following settings:
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python -m uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
4. Add the Environment Variables:
   - `OPENROUTER_KEY_1`: `your_openrouter_key`
   - `GOOGLE_API_KEY`: `your_google_key`
   - `POSTGRES_HOST`: `db.xxxx.supabase.co`
   - `POSTGRES_USER`: `postgres`
   - `POSTGRES_PASSWORD`: `your_supabase_password`
   - `POSTGRES_DB`: `postgres`
   - `POSTGRES_PORT`: `5432`
5. Click **Create Web Service**. Your backend API will be live at `https://ae03-directive-backend.onrender.com`.

---

## ⚡ Step 3: Deploy Next.js Frontend on Netlify

1. Log into your [Netlify Account](https://app.netlify.com).
2. Click **Add new site** → **Import an existing project**.
3. Select **GitHub** and authorize access to `Zinat-Khan/CodeRush2.0-AllBlueInnovation`.
4. Netlify will automatically detect `netlify.toml`:
   - **Base directory**: `frontend`
   - **Build command**: `npm run build`
   - **Publish directory**: `frontend/.next`
5. Add Environment Variables under **Site configuration > Environment variables**:
   - `NEXT_PUBLIC_API_BASE_URL`: `https://ae03-directive-backend.onrender.com/api/v2`
6. Click **Deploy Site**. Netlify will build and launch your site live!

---

## 🔒 Verification Checklist
- [x] `netlify.toml` configured in root directory.
- [x] `Dockerfile` and `render.yaml` created for cloud backend hosting.
- [x] `backend/rag/schema.sql` ready for Supabase vector indexing.
- [x] Zero changes to core codebase files; all deployment files added modularly.
