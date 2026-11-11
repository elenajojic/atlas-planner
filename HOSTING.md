# Atlas Planner as a free Claude connector

This turns Atlas Planner into a connector you add once in Claude, the same way Gmail works. After that it
works in any chat on claude.ai, the desktop app and your phone, with your Mac closed.

Everything below is free and needs no credit card:
- **Render** (free web service) runs the connector.
- **Neon** (free Postgres) remembers your profile and the downloaded course data.
- **Claude** lets free accounts add one custom connector (paid plans can add more).

Total setup time is about 15 minutes.

## How login works

Atlas uses UMich login with Duo, and UMich only hands logins to sites it has registered. So connecting uses a
tiny Chrome helper (installed once, about a minute): click **Sign in with Atlas** on the connect page, log in
in the tab that opens, and you're sent back to Claude. The helper only runs on your connector's connect page.
Your Atlas login is kept only inside the encrypted token Claude holds for you, never saved on the server.
(No helper? The connect page also lets you paste your `sessionid` cookie by hand.)

## 1. Put the code on GitHub

Create a new **private** repository on github.com called `atlas-planner`, then in Terminal:

```bash
cd ~/Documents/atlas-planner
git init && git add -A && git commit -m "Atlas Planner"
git branch -M main
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/atlas-planner.git
git push -u origin main
```

(`.venv` is excluded by `.gitignore`, so your local setup isn't uploaded.)

## 2. Make the free database (Neon)

1. Sign up at **neon.com** (GitHub login works). Create a project called `atlas-planner`.
2. On the project dashboard click **Connect**, turn on **Connection pooling**, and copy the connection
   string. It looks like `postgresql://...@ep-....-pooler....neon.tech/neondb?sslmode=require`.

## 3. Deploy the connector (Render)

1. Sign up at **render.com** with GitHub.
2. Click **New > Blueprint**, pick your `atlas-planner` repo. Render reads `render.yaml`.
3. When it asks for `DATABASE_URL`, paste the Neon string from step 2. `SECRET_KEY` is generated for you.
4. Click **Apply**. The first build takes about 5 minutes.
5. When it says **Live**, copy your URL, e.g. `https://atlas-planner-xxxx.onrender.com`. Open it in a
   browser: you should see "Atlas Planner".

## 4. Add it to Claude

1. In Claude go to **Customize > Connectors**, click **Add**, choose **Custom > Web**.
2. Name: `Atlas Planner`. URL: your Render URL with `/mcp` on the end, e.g.
   `https://atlas-planner-xxxx.onrender.com/mcp`.
3. Leave authentication as detected (OAuth). If it asks about the OAuth client, pick
   **Register automatically**.
4. Click **Add**, then **Connect**. On the connect page, do the one time helper setup it shows, then click
   **Sign in with Atlas**.

Done. In any chat, turn on Atlas Planner from the **+** menu > **Connectors** and say
"set up my Atlas planner".

## Good to know

- **Speed.** The download starts the moment you connect (usually 1 to 3 minutes the first time, a few requests
  at a time). Course data is then kept for the whole term and your audit for a day, and refreshes happen in the
  background, so later chats answer right away. Course data is shared, so friends who connect later get it instantly.
- **Free Render sleeps** after 15 minutes without use and takes about a minute to wake. If the first message
  of the day errors or times out, send it again. To keep it awake, add a free check at **cron-job.org**
  that opens `https://YOUR-URL/healthz` every 10 minutes (one always-on service fits in Render's 750 free
  hours a month).
- **Sharing with friends.** Send them your `/mcp` URL. Each person connects with their own Atlas cookie and
  gets their own encrypted profile. Their data is stored in *your* Neon database, so only share with people
  who are fine with that.
- **Atlas data is student records data.** Use it for personal planning. This is a student project, not an
  official University of Michigan service; check UMich IT's responsible use rules before sharing widely.
- **Updating.** Push to GitHub and Render redeploys by itself.
