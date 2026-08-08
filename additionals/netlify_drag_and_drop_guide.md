# Netlify Manual Drag & Drop Deployment Guide (No GitHub Required)

This guide shows you how to deploy the **AE-03 Directive V2 Platform** directly to Netlify using **Manual Drag & Drop** (without needing GitHub).

---

## 📂 Method 1: Netlify Web Uploader (Easiest & Recommended)

1. Open your browser and go to **[Netlify Drop](https://app.netlify.com/drop)** (or log into [app.netlify.com](https://app.netlify.com)).
2. Drag and drop your **`c:\hack`** folder (the entire workspace root) directly into the upload area on Netlify.
3. Netlify will automatically detect **`netlify.toml`** in the root folder:
   - **Base directory**: `frontend`
   - **Build command**: `npm run build`
   - **Publish directory**: `frontend/.next`
4. Netlify will build your site and give you a live URL (e.g. `https://ae03-orchestrator.netlify.app`)!

---

## ⚡ Method 2: Netlify CLI Manual Deployment (Alternative)

If you prefer using the command line:

1. Install Netlify CLI:
   ```bash
   npm install -g netlify-cli
   ```
2. Run Netlify manual deploy from your terminal:
   ```bash
   cd c:\hack
   netlify deploy --prod
   ```
3. Follow the prompts on screen to authorize your Netlify account.

---

## ⚙️ Setting Up Live API & Backend Connections

In Netlify Site Settings (**Site configuration > Environment variables**):
- Add `NEXT_PUBLIC_API_BASE_URL`: `https://ae03-directive-backend.onrender.com/api/v2`

Your frontend will automatically communicate with your live backend API!
